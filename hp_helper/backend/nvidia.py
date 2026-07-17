"""Power-aware NVIDIA GPU metrics via NVML (no nvidia-smi).

Spawning ``nvidia-smi`` every sensor poll wakes a runtime-suspended laptop
dGPU (D3cold → D0), costs multi-second process startup, and can hold the
chip at ~15–20 W idle. This module:

1. Reads PCI runtime status from sysfs without touching the GPU.
2. Skips all NVML/smi work while the device is suspended.
3. Uses in-process NVML (libnvidia-ml) when already active — milliseconds,
   no process spawn.
4. Backs off after consecutive idle samples so our own queries do not pin
   the GPU in D0 and block runtime autosuspend.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# NVML return codes we care about
_NVML_SUCCESS = 0
_NVML_TEMPERATURE_GPU = 0

# After this many consecutive idle polls while D0, stop querying so runtime
# PM can re-enter D3. 3 × 1 s sensor period ≈ 3 s of idle confirmation.
# A single NVML open can keep the chip in D0 for ~20 s on fine-grained RTD3
# laptops; we must not query again until the device actually re-suspends.
_IDLE_STREAK_DISARM = 3
# Treat util below this as idle (percent).
_IDLE_UTIL_MAX = 1.0


@dataclass(frozen=True)
class NvidiaMetrics:
    temperature: str
    power: float
    utilization: float | None
    pstate: str | None
    source: str = "nvml"


def _nvidia_pci_devices() -> list[Path]:
    """Return sysfs paths for NVIDIA VGA/3D PCI functions."""
    pci = Path("/sys/bus/pci/devices")
    if not pci.is_dir():
        return []
    out: list[Path] = []
    try:
        entries = list(pci.iterdir())
    except OSError:
        return []
    for dev in entries:
        try:
            vendor = (dev / "vendor").read_text().strip().lower()
        except OSError:
            continue
        if vendor != "0x10de":
            continue
        try:
            class_code = (dev / "class").read_text().strip().lower()
        except OSError:
            continue
        # 0x030000 VGA, 0x030200 3D controller
        if class_code.startswith("0x0300") or class_code.startswith("0x0302"):
            out.append(dev)
    return sorted(out, key=lambda p: p.name)


def _runtime_status(dev: Path) -> str | None:
    """PCI runtime PM status: active / suspended / suspending / resuming / …"""
    try:
        return (dev / "power" / "runtime_status").read_text().strip().lower()
    except OSError:
        return None


def _power_state(dev: Path) -> str | None:
    try:
        return (dev / "power_state").read_text().strip().upper()
    except OSError:
        return None


class NvidiaReader:
    """Cached, power-aware GPU metric reader."""

    def __init__(self) -> None:
        self._pci_devs: list[Path] | None = None
        self._lib: ctypes.CDLL | None = None
        self._handle = ctypes.c_void_p()
        self._inited = False
        self._nvml_unavailable = False
        self._idle_streak = 0
        # When False, refuse NVML until the device re-enters runtime suspend
        # (then becomes active again). Prevents our idle polls from pinning
        # the dGPU in D0 forever.
        self._armed = True

    def _devices(self) -> list[Path]:
        if self._pci_devs is None:
            self._pci_devs = _nvidia_pci_devices()
        return self._pci_devs

    def has_nvidia(self) -> bool:
        return bool(self._devices()) or Path("/proc/driver/nvidia").is_dir()

    def is_runtime_active(self) -> bool:
        """True if any NVIDIA display GPU reports runtime_status=active."""
        devs = self._devices()
        if not devs:
            # Driver present but no PCI match — treat as unknown/active so
            # desktop GPUs without runtime PM still get metrics.
            return Path("/proc/driver/nvidia").is_dir()
        for dev in devs:
            if _runtime_status(dev) == "active":
                return True
        return False

    def is_runtime_suspended(self) -> bool:
        devs = self._devices()
        if not devs:
            return False
        # Suspended only when every dGPU is not active (suspended/suspending).
        for dev in devs:
            st = _runtime_status(dev)
            if st == "active":
                return False
            if st is None:
                return False
        return True

    def _load_lib(self) -> ctypes.CDLL | None:
        if self._nvml_unavailable:
            return None
        if self._lib is not None:
            return self._lib
        for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            try:
                self._lib = ctypes.CDLL(name)
                return self._lib
            except OSError:
                continue
        logger.info("libnvidia-ml not found; GPU metrics unavailable without nvidia-smi")
        self._nvml_unavailable = True
        return None

    def _ensure_nvml(self) -> bool:
        lib = self._load_lib()
        if lib is None:
            return False
        if self._inited:
            return True
        init = getattr(lib, "nvmlInit_v2", None) or getattr(lib, "nvmlInit", None)
        if init is None:
            self._nvml_unavailable = True
            return False
        init.restype = ctypes.c_int
        rc = init()
        if rc != _NVML_SUCCESS:
            logger.debug("nvmlInit failed: %s", rc)
            return False
        get_handle = getattr(
            lib, "nvmlDeviceGetHandleByIndex_v2", None,
        ) or getattr(lib, "nvmlDeviceGetHandleByIndex", None)
        if get_handle is None:
            self._shutdown_nvml()
            return False
        get_handle.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        get_handle.restype = ctypes.c_int
        handle = ctypes.c_void_p()
        rc = get_handle(0, ctypes.byref(handle))
        if rc != _NVML_SUCCESS or not handle:
            self._shutdown_nvml()
            return False
        self._handle = handle
        self._inited = True
        return True

    def _shutdown_nvml(self) -> None:
        if not self._inited:
            return
        lib = self._lib
        if lib is not None:
            shutdown = getattr(lib, "nvmlShutdown", None)
            if shutdown is not None:
                try:
                    shutdown.restype = ctypes.c_int
                    shutdown()
                except Exception:
                    logger.debug("nvmlShutdown failed", exc_info=True)
        self._inited = False
        self._handle = ctypes.c_void_p()

    def _nvml_query(self) -> NvidiaMetrics | None:
        if not self._ensure_nvml():
            return None
        lib = self._lib
        handle = self._handle
        assert lib is not None

        temp_c: int | None = None
        power_w: float | None = None
        util: float | None = None
        pstate: str | None = None

        # Temperature
        temp = ctypes.c_uint()
        fn = lib.nvmlDeviceGetTemperature
        fn.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        if fn(handle, _NVML_TEMPERATURE_GPU, ctypes.byref(temp)) == _NVML_SUCCESS:
            temp_c = int(temp.value)

        # Power (mW)
        mw = ctypes.c_uint()
        fn = lib.nvmlDeviceGetPowerUsage
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        if fn(handle, ctypes.byref(mw)) == _NVML_SUCCESS:
            power_w = mw.value / 1000.0

        # Utilization
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        util_s = _Util()
        fn = lib.nvmlDeviceGetUtilizationRates
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Util)]
        fn.restype = ctypes.c_int
        if fn(handle, ctypes.byref(util_s)) == _NVML_SUCCESS:
            util = float(util_s.gpu)

        # Performance state
        ps = ctypes.c_uint()
        fn = lib.nvmlDeviceGetPerformanceState
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        if fn(handle, ctypes.byref(ps)) == _NVML_SUCCESS:
            pstate = f"P{int(ps.value)}"

        if temp_c is None or power_w is None:
            return None
        return NvidiaMetrics(
            temperature=str(temp_c),
            power=power_w,
            utilization=util,
            pstate=pstate,
            source="nvml",
        )

    @staticmethod
    def _looks_idle(m: NvidiaMetrics) -> bool:
        util = m.utilization if m.utilization is not None else 0.0
        if util > _IDLE_UTIL_MAX:
            return False
        # P0–P2 imply real load even if util sample is momentarily 0.
        if m.pstate in ("P0", "P1", "P2"):
            return False
        return True

    def read(self) -> NvidiaMetrics | None:
        """Return GPU metrics, or None when unavailable / intentionally quiet.

        Never calls nvidia-smi. Never opens NVML while the device is
        runtime-suspended, so idle hybrid laptops stay in D3cold.
        """
        if not self.has_nvidia():
            return None

        # Fast path: device fully suspended — do not touch NVML.
        # Re-arm so the next external wake (game, CUDA, compositor) can
        # be monitored again.
        if self.is_runtime_suspended():
            self._idle_streak = 0
            self._armed = True
            self._shutdown_nvml()
            return None

        if not self.is_runtime_active():
            # Unknown / intermediate states: do not force a wake.
            self._shutdown_nvml()
            return None

        # Active but disarmed after idle: sysfs-only until a full
        # suspend→resume cycle (handled above). Avoids re-opening NVML
        # every second and resetting the RTD3 timer (~20 s on this HW).
        if not self._armed:
            self._shutdown_nvml()
            return None

        metrics = self._nvml_query()
        if metrics is None:
            self._shutdown_nvml()
            return None

        if self._looks_idle(metrics):
            self._idle_streak += 1
            if self._idle_streak >= _IDLE_STREAK_DISARM:
                self._shutdown_nvml()
                self._armed = False
                self._idle_streak = 0
                logger.debug(
                    "NVIDIA idle — disarmed NVML until runtime re-suspends",
                )
        else:
            self._idle_streak = 0

        return metrics

    def close(self) -> None:
        self._shutdown_nvml()
