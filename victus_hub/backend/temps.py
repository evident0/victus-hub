"""CPU/GPU temperatures for daemon fan control.

NVIDIA uses the same layering as the UI sensor path (hwmon → NVML →
nvidia-smi) but never touches GPU sensors while the dGPU is
runtime-suspended. nvidia-smi is a last resort with failure backoff so a
missing binary cannot stall the 1 s fan loop.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import time
from pathlib import Path

from victus_hub.backend.sysfs_read import find_hwmon_by_name, iter_hwmon_dirs, read_int, read_text

_CPU_HWMON_NAMES = frozenset({"coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"})
_GPU_HWMON_NAMES = frozenset({"amdgpu", "nouveau"})
_NVIDIA_HWMON = "nvidia"
_NVML_SUCCESS = 0
_NVML_TEMPERATURE_GPU = 0
_SMI_TIMEOUT_S = 1.2
_SMI_FAIL_BACKOFF_S = 30.0

_rt_status_path: Path | None | bool = False
_nvml_lib: ctypes.CDLL | None = None
_nvml_handle = ctypes.c_void_p()
_nvml_inited = False
_nvml_unavailable = False
_smi_retry_at = 0.0
_smi_known_missing = False


def _has_nvidia() -> bool:
    return Path("/sys/module/nvidia").is_dir() or Path("/proc/driver/nvidia").is_dir()


def _dgpu_runtime_status_path() -> Path | None:
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
        if read_int(dev / "boot_vga") == 1:
            continue
        path = dev / "power" / "runtime_status"
        if path.is_file():
            return path
    return None


def dgpu_runtime_suspended() -> bool:
    """True when the discrete NVIDIA GPU is in runtime suspend (do not wake)."""
    global _rt_status_path
    if _rt_status_path is False:
        _rt_status_path = _dgpu_runtime_status_path()
    if not _rt_status_path:
        return False
    try:
        return _rt_status_path.read_text().strip().lower() == "suspended"
    except OSError:
        return False


def read_cpu_temp_c() -> float | None:
    """Return the highest plausible CPU package/Tctl reading in °C.

    GPU hwmon devices are skipped entirely so a CPU poll cannot resume a
    runtime-suspended dGPU. Temperature inputs are read only after the
    hwmon name (or a label) identifies a CPU sensor.
    """
    best: int | None = None
    for hwmon in iter_hwmon_dirs():
        name = (read_text(hwmon / "name") or "").lower()
        if name == _NVIDIA_HWMON or name in _GPU_HWMON_NAMES:
            continue
        try:
            entries = list(hwmon.iterdir())
        except OSError:
            continue
        preferred = name in _CPU_HWMON_NAMES
        for path in entries:
            fname = path.name
            if not fname.startswith("temp") or not fname.endswith("_input"):
                continue
            if not preferred:
                label = (read_text(path.with_name(fname.replace("_input", "_label"))) or "").lower()
                if "package" not in label and "tctl" not in label:
                    continue
            value = read_int(path)
            if value is None or value <= -100_000:
                continue
            if best is None or value > best:
                best = value
    if best is None:
        return None
    return best / 1000.0


def _nvidia_hwmon_temp_c() -> float | None:
    hwmon = find_hwmon_by_name("nvidia")
    if hwmon is None:
        return None
    value = read_int(hwmon / "temp1_input")
    if value is None or value <= 0:
        return None
    return value / 1000.0


def _nvml_shutdown() -> None:
    global _nvml_inited, _nvml_handle
    if not _nvml_inited:
        return
    if _nvml_lib is not None:
        shutdown = getattr(_nvml_lib, "nvmlShutdown", None)
        if shutdown is not None:
            try:
                shutdown.restype = ctypes.c_int
                shutdown()
            except Exception:
                pass
    _nvml_inited = False
    _nvml_handle = ctypes.c_void_p()


def close_nvidia() -> None:
    """Release the fan thread's NVML session when temperature polling stops."""
    _nvml_shutdown()


def _nvml_ensure() -> bool:
    global _nvml_lib, _nvml_handle, _nvml_inited, _nvml_unavailable
    if _nvml_unavailable:
        return False
    if _nvml_inited:
        return True
    if _nvml_lib is None:
        for libname in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            try:
                _nvml_lib = ctypes.CDLL(libname)
                break
            except OSError:
                continue
        else:
            _nvml_unavailable = True
            return False
    lib = _nvml_lib
    init = getattr(lib, "nvmlInit_v2", None) or getattr(lib, "nvmlInit", None)
    get_handle = getattr(lib, "nvmlDeviceGetHandleByIndex_v2", None) or getattr(
        lib, "nvmlDeviceGetHandleByIndex", None,
    )
    if init is None or get_handle is None:
        _nvml_unavailable = True
        return False
    init.restype = ctypes.c_int
    if init() != _NVML_SUCCESS:
        return False
    get_handle.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    get_handle.restype = ctypes.c_int
    handle = ctypes.c_void_p()
    if get_handle(0, ctypes.byref(handle)) != _NVML_SUCCESS or not handle:
        _nvml_shutdown()
        return False
    _nvml_handle = handle
    _nvml_inited = True
    return True


def _nvml_temp_c() -> float | None:
    if not _nvml_ensure():
        return None
    lib = _nvml_lib
    assert lib is not None
    temp = ctypes.c_uint()
    fn = lib.nvmlDeviceGetTemperature
    fn.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    fn.restype = ctypes.c_int
    if fn(_nvml_handle, _NVML_TEMPERATURE_GPU, ctypes.byref(temp)) != _NVML_SUCCESS:
        return None
    return float(temp.value)


def _smi_temp_c() -> float | None:
    global _smi_retry_at, _smi_known_missing
    if _smi_known_missing:
        return None
    now = time.monotonic()
    if now < _smi_retry_at:
        return None
    smi = shutil.which("nvidia-smi")
    if smi is None:
        _smi_known_missing = True
        return None
    try:
        result = subprocess.run(
            [smi, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_SMI_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        _smi_retry_at = now + _SMI_FAIL_BACKOFF_S
        return None
    if result.returncode != 0:
        _smi_retry_at = now + _SMI_FAIL_BACKOFF_S
        return None
    raw = (result.stdout or "").strip().splitlines()
    if not raw:
        _smi_retry_at = now + _SMI_FAIL_BACKOFF_S
        return None
    text = raw[0].strip()
    if not text or text.upper() in {"N/A", "[N/A]"}:
        _smi_retry_at = now + _SMI_FAIL_BACKOFF_S
        return None
    try:
        return float(text)
    except ValueError:
        _smi_retry_at = now + _SMI_FAIL_BACKOFF_S
        return None


def _amd_or_nouveau_temp_c() -> float | None:
    best: int | None = None
    for hwmon in iter_hwmon_dirs():
        name = (read_text(hwmon / "name") or "").lower()
        if name not in _GPU_HWMON_NAMES:
            continue
        try:
            entries = list(hwmon.iterdir())
        except OSError:
            continue
        for path in entries:
            fname = path.name
            if not fname.startswith("temp") or not fname.endswith("_input"):
                continue
            value = read_int(path)
            if value is None:
                continue
            if best is None or value > best:
                best = value
    if best is None:
        return None
    return best / 1000.0


def read_gpu_temp_c(*, disable_nvidia: bool = False) -> float | None:
    """Return GPU temperature in °C.

    NVIDIA: refuse to touch sensors while the dGPU is runtime-suspended,
    then hwmon → NVML → nvidia-smi. AMD/Nouveau use hwmon only.
    """
    if _has_nvidia():
        if disable_nvidia or dgpu_runtime_suspended():
            _nvml_shutdown()
            return None
        hwmon = _nvidia_hwmon_temp_c()
        if hwmon is not None:
            return hwmon
        nvml = _nvml_temp_c()
        if nvml is not None:
            return nvml
        return _smi_temp_c()
    return _amd_or_nouveau_temp_c()


def read_cpu_gpu_temps(*, disable_nvidia: bool = False) -> tuple[float | None, float | None]:
    return read_cpu_temp_c(), read_gpu_temp_c(disable_nvidia=disable_nvidia)
