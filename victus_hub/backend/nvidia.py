"""NVIDIA dGPU metrics — aligned with g-helper-linux.

Layering (same idea as g-helper GetGpuTemp / GetCurrentTemp + nvml-temp)::

  1. sysfs hwmon name=``nvidia``  (temp only, when exported)
  2. in-process NVML              (temp + power + util; like gpu-helper
                                   ``nvml-temp``, without a privileged helper)
  3. ``nvidia-smi``               (last resort, 1.2 s timeout)

Sensor 0 = ``NVML_TEMPERATURE_GPU`` = ``temperature.gpu``.

Does **not**:
  - use amdgpu (iGPU) as the NVIDIA temp
  - idle-disarm / standby backoff
  - wake a runtime-suspended (D3cold) dGPU
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from threading import RLock, Thread
from weakref import WeakSet

from PySide6.QtCore import QSettings

from victus_hub.backend.sysfs_read import find_hwmon_by_name, read_int

_NVML_SUCCESS = 0
NVML_TEMPERATURE_GPU = 0
_SMI_TIMEOUT_S = 1.2  # g-helper SmiTimeoutMs = 1200
DISABLE_NVIDIA_QUERIES_KEY = "sensors/disableNvidiaQueries"
_queries_disabled: bool | None = None
_power_save_active = False
_query_lock = RLock()
_readers: WeakSet = WeakSet()


def nvidia_query_disable_enabled() -> bool:
    """Load once, then use memory only on the sensor hot path."""
    global _queries_disabled
    if _queries_disabled is None:
        _queries_disabled = QSettings("victus-hub", "victus-hub").value(
            DISABLE_NVIDIA_QUERIES_KEY, False, type=bool,
        )
    return _queries_disabled


def nvidia_queries_disabled() -> bool:
    """Suppress queries only when the preference and Power Save are active."""
    return nvidia_query_disable_enabled() and _power_save_active


def set_nvidia_power_profile(profile: int) -> None:
    global _power_save_active
    _power_save_active = profile == 0
    if nvidia_queries_disabled():
        Thread(target=_close_disabled_readers, daemon=True, name="nvidia-close").start()


def set_nvidia_queries_disabled(disabled: bool) -> None:
    """Update immediately; never wait for driver work on the GUI thread."""
    global _queries_disabled
    _queries_disabled = disabled
    QSettings("victus-hub", "victus-hub").setValue(
        DISABLE_NVIDIA_QUERIES_KEY, disabled,
    )
    if nvidia_queries_disabled():
        Thread(target=_close_disabled_readers, daemon=True, name="nvidia-close").start()


def _close_disabled_readers() -> None:
    with _query_lock:
        if nvidia_queries_disabled():
            for reader in list(_readers):
                reader.close()


def _query_allowed(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        # Disabled callers must not wait behind a query already in progress.
        if nvidia_queries_disabled():
            return None
        with _query_lock:
            if nvidia_queries_disabled():
                return None
            return function(*args, **kwargs)
    return guarded


@dataclass(frozen=True)
class NvidiaMetrics:
    temperature: str
    power: float | None
    utilization: float | None
    source: str


# ── Passive sysfs (g-helper IsDgpuSuspended) ─────────────────────────────


def _find_dgpu_runtime_status_path() -> Path | None:
    """PCI runtime_status for the discrete NVIDIA GPU (skip boot_vga iGPU)."""
    pci = Path("/sys/bus/pci/devices")
    if not pci.is_dir():
        return None
    try:
        entries = list(pci.iterdir())
    except OSError:
        return None
    for dev in sorted(entries, key=lambda p: p.name):
        try:
            vendor = (dev / "vendor").read_text().strip().lower()
            cls = (dev / "class").read_text().strip().lower()
        except OSError:
            continue
        if vendor != "0x10de":
            continue
        if not (cls.startswith("0x0300") or cls.startswith("0x0302")):
            continue
        # g-helper skips boot_vga (iGPU); discrete NVIDIA is never boot_vga on hybrid
        if read_int(dev / "boot_vga") == 1:
            continue
        path = dev / "power" / "runtime_status"
        if path.is_file():
            return path
    return None


@_query_allowed
def _hwmon_nvidia_temp_c() -> int | None:
    """Layer 1: nvidia hwmon temp1_input → °C (g-helper GetCurrentTemp method 1)."""
    hwmon = find_hwmon_by_name("nvidia")
    if hwmon is None:
        return None
    value = read_int(hwmon / "temp1_input")
    if value is None or value <= 0:
        return None
    return value // 1000


# ── nvidia-smi (layer 3) ──────────────────────────────────────────────


@_query_allowed
def _smi(query: str) -> str | None:
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        result = subprocess.run(
            [smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_SMI_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = (result.stdout or "").strip().splitlines()
    if not lines:
        return None
    raw = lines[0].strip()
    if not raw or raw.upper() in {"N/A", "[N/A]"}:
        return None
    return raw


_NVIDIA_PROC_GPUS = Path("/proc/driver/nvidia/gpus")


@_query_allowed
def get_gpu_name() -> str | None:
    """GPU product name from the driver's proc file, without NVML.

    ``nvmlInit`` maps libcuda into this process and does not unmap it.
    The Model line is already published by the kernel module.
    """
    root = _NVIDIA_PROC_GPUS
    if not root.is_dir():
        return None
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        info = entry / "information"
        try:
            text = info.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("Model:"):
                name = line.split(":", 1)[1].strip()
                if name:
                    return name
    return None


_GPU_NAME: str | None = None


# ── Reader ────────────────────────────────────────────────────────────


class NvidiaReader:
    def __init__(self) -> None:
        self._rt_status_path: Path | None | bool = False  # False = not scanned yet
        self._lib: ctypes.CDLL | None = None
        self._handle = ctypes.c_void_p()
        self._inited = False
        self._nvml_unavailable = False
        with _query_lock:
            _readers.add(self)

    def has_nvidia(self) -> bool:
        return Path("/sys/module/nvidia").is_dir() or Path("/proc/driver/nvidia").is_dir()

    def is_runtime_suspended(self) -> bool:
        """g-helper IsDgpuSuspended: passive sysfs, only status == suspended."""
        if self._rt_status_path is False:
            self._rt_status_path = _find_dgpu_runtime_status_path()
        if not self._rt_status_path:
            return False
        try:
            return self._rt_status_path.read_text().strip().lower() == "suspended"
        except OSError:
            return False

    # ── NVML (layer 2; same APIs as gpu-helper do_nvml_temp) ───────────

    def _ensure_nvml(self) -> bool:
        if self._nvml_unavailable:
            return False
        if self._inited:
            return True
        if self._lib is None:
            for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
                try:
                    self._lib = ctypes.CDLL(name)
                    break
                except OSError:
                    continue
            else:
                self._nvml_unavailable = True
                return False
        lib = self._lib
        init = getattr(lib, "nvmlInit_v2", None) or getattr(lib, "nvmlInit", None)
        get_handle = getattr(
            lib, "nvmlDeviceGetHandleByIndex_v2", None,
        ) or getattr(lib, "nvmlDeviceGetHandleByIndex", None)
        if init is None or get_handle is None:
            self._nvml_unavailable = True
            return False
        init.restype = ctypes.c_int
        if init() != _NVML_SUCCESS:
            return False
        get_handle.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        get_handle.restype = ctypes.c_int
        handle = ctypes.c_void_p()
        if get_handle(0, ctypes.byref(handle)) != _NVML_SUCCESS or not handle:
            self._shutdown_nvml()
            return False
        self._handle = handle
        self._inited = True
        return True

    def _shutdown_nvml(self) -> None:
        if not self._inited:
            return
        if self._lib is not None:
            shutdown = getattr(self._lib, "nvmlShutdown", None)
            if shutdown is not None:
                try:
                    shutdown.restype = ctypes.c_int
                    shutdown()
                except Exception:
                    pass
        self._inited = False
        self._handle = ctypes.c_void_p()

    @_query_allowed
    def _query_nvml(self) -> NvidiaMetrics | None:
        if not self._ensure_nvml():
            return None
        lib, handle = self._lib, self._handle
        assert lib is not None

        temp_c: int | None = None
        power_w: float | None = None
        util: float | None = None

        temp = ctypes.c_uint()
        fn = lib.nvmlDeviceGetTemperature
        fn.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        if fn(handle, NVML_TEMPERATURE_GPU, ctypes.byref(temp)) == _NVML_SUCCESS:
            temp_c = int(temp.value)

        mw = ctypes.c_uint()
        fn = lib.nvmlDeviceGetPowerUsage
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        if fn(handle, ctypes.byref(mw)) == _NVML_SUCCESS and mw.value <= 200_000:
            power_w = mw.value / 1000.0

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        util_s = _Util()
        fn = lib.nvmlDeviceGetUtilizationRates
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Util)]
        fn.restype = ctypes.c_int
        if fn(handle, ctypes.byref(util_s)) == _NVML_SUCCESS:
            util = float(util_s.gpu)

        if temp_c is None:
            return None
        return NvidiaMetrics(str(temp_c), power_w, util, "nvml")

    def _query_smi(self) -> NvidiaMetrics | None:
        """Layer 3: one multi-field nvidia-smi query."""
        raw = _smi("temperature.gpu,utilization.gpu,power.draw")
        if raw is None:
            return None
        parts = [p.strip() for p in raw.split(",")]
        try:
            temp = float(parts[0])
        except (ValueError, IndexError):
            return None

        def f(i: int) -> float | None:
            if i >= len(parts):
                return None
            s = parts[i]
            if not s or s.upper() in {"N/A", "[N/A]"}:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        return NvidiaMetrics(
            str(int(round(temp))), f(2), f(1), "nvidia-smi",
        )

    @_query_allowed
    def read(self) -> NvidiaMetrics | None:
        """Return metrics, or None if no driver / dGPU is runtime-suspended."""
        if not self.has_nvidia():
            return None

        # g-helper: never wake D3cold for telemetry
        if self.is_runtime_suspended():
            self._shutdown_nvml()
            return None

        # Layer 2 then 3 for full metrics (util/power need NVML or smi)
        metrics = self._query_nvml() or self._query_smi()

        # Layer 1: prefer nvidia hwmon for temperature when present
        # (g-helper GetCurrentTemp checks hwmon first).
        hwmon_t = _hwmon_nvidia_temp_c()
        if metrics is None:
            if hwmon_t is None:
                return None
            return NvidiaMetrics(str(hwmon_t), None, None, "hwmon:nvidia")

        if hwmon_t is not None:
            return NvidiaMetrics(
                str(hwmon_t),
                metrics.power,
                metrics.utilization,
                "hwmon:nvidia",
            )
        return metrics

    def close(self) -> None:
        with _query_lock:
            self._shutdown_nvml()
