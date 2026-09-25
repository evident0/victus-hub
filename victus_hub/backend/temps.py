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
import threading
import time
from dataclasses import dataclass
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
# Per-column backoff. A failed power or utilization query must not silence
# the fan curve's temperature fallback.
_smi_temp_retry_at = 0.0
_smi_power_retry_at = 0.0
_smi_util_retry_at = 0.0
_smi_known_missing = False
_nvml_lock = threading.RLock()
_smi_lock = threading.Lock()
_display_gpu_until = 0.0
_DISPLAY_HOLD_S = 3.0


@dataclass(frozen=True)
class NvidiaFields:
    """One shared NVML session's answer. Unrequested fields stay None."""

    temp_c: float | None = None
    power_w: float | None = None
    util_pct: float | None = None
    temp_source: str = ""
    power_source: str = ""
    util_source: str = ""


def hold_display_gpu(active: bool) -> None:
    """Remember that the GUI just asked for a GPU field, or that it stopped."""
    global _display_gpu_until
    with _nvml_lock:
        _display_gpu_until = time.monotonic() + _DISPLAY_HOLD_S if active else 0.0


def display_gpu_held() -> bool:
    """True while a recent GUI request still wants GPU temperature, power, or use."""
    with _nvml_lock:
        return time.monotonic() < _display_gpu_until


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
    """Release the shared NVML session when nothing needs it."""
    with _nvml_lock:
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


def _nvml_read_temp() -> float | None:
    lib = _nvml_lib
    if lib is None:
        return None
    temp = ctypes.c_uint()
    fn = lib.nvmlDeviceGetTemperature
    fn.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    fn.restype = ctypes.c_int
    if fn(_nvml_handle, _NVML_TEMPERATURE_GPU, ctypes.byref(temp)) != _NVML_SUCCESS:
        return None
    return float(temp.value)


def _nvml_power_w() -> float | None:
    if not _nvml_ensure():
        return None
    lib = _nvml_lib
    if lib is None:
        return None
    mw = ctypes.c_uint()
    fn = lib.nvmlDeviceGetPowerUsage
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    fn.restype = ctypes.c_int
    if fn(_nvml_handle, ctypes.byref(mw)) != _NVML_SUCCESS or mw.value > 200_000:
        return None
    return mw.value / 1000.0


def _nvml_util_pct() -> float | None:
    if not _nvml_ensure():
        return None
    lib = _nvml_lib
    if lib is None:
        return None

    class _Util(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

    util = _Util()
    fn = lib.nvmlDeviceGetUtilizationRates
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Util)]
    fn.restype = ctypes.c_int
    if fn(_nvml_handle, ctypes.byref(util)) != _NVML_SUCCESS:
        return None
    return float(util.gpu)


def _smi_backoff(now: float, *, temperature: bool, power: bool, utilization: bool) -> None:
    global _smi_temp_retry_at, _smi_power_retry_at, _smi_util_retry_at
    until = now + _SMI_FAIL_BACKOFF_S
    if temperature:
        _smi_temp_retry_at = until
    if power:
        _smi_power_retry_at = until
    if utilization:
        _smi_util_retry_at = until


def _smi_temp_c() -> float | None:
    temp, _power, _util = _smi_selected(temperature=True, power=False, utilization=False)
    return temp


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


# Column order is the nvidia-smi query order. Temperature stays first so a
# power or utilization miss can be skipped without shifting the temp value.
_SMI_COLUMNS = (
    ("temperature", "temperature.gpu"),
    ("utilization", "utilization.gpu"),
    ("power", "power.draw"),
)


def _smi_due(now: float, requested: dict[str, bool]) -> list[tuple[str, str]]:
    retry_at = {
        "temperature": _smi_temp_retry_at,
        "utilization": _smi_util_retry_at,
        "power": _smi_power_retry_at,
    }
    return [
        (field, column)
        for field, column in _SMI_COLUMNS
        if requested[field] and now >= retry_at[field]
    ]


def _smi_float(parts: list[str], index: int) -> float | None:
    if index >= len(parts):
        return None
    text = parts[index]
    if not text or text.upper() in {"N/A", "[N/A]"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _smi_selected(
    *, temperature: bool, power: bool, utilization: bool,
) -> tuple[float | None, float | None, float | None]:
    """Last-resort nvidia-smi for only the requested columns.

    Each column has its own backoff. A miss on power or utilization does not
    suppress a later temperature read. The subprocess stays outside the lock.
    """
    global _smi_known_missing
    missing = (None, None, None)
    requested = {
        "temperature": temperature,
        "utilization": utilization,
        "power": power,
    }
    if not any(requested.values()):
        return missing
    with _smi_lock:
        if _smi_known_missing:
            return missing
        now = time.monotonic()
        due = _smi_due(now, requested)
        if not due:
            return missing
        smi = shutil.which("nvidia-smi")
        if smi is None:
            _smi_known_missing = True
            return missing
    try:
        query = ",".join(column for _field, column in due)
        result = subprocess.run(
            [smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_SMI_TIMEOUT_S,
        )
        raw = (result.stdout or "").strip().splitlines() if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        raw = []
    parts = [part.strip() for part in raw[0].split(",")] if raw else []
    parsed = {
        field: _smi_float(parts, index) if parts else None
        for index, (field, _column) in enumerate(due)
    }
    failed = {field for field, _column in due if parsed[field] is None}
    with _smi_lock:
        _smi_backoff(
            now,
            temperature="temperature" in failed,
            power="power" in failed,
            utilization="utilization" in failed,
        )
    return parsed.get("temperature"), parsed.get("power"), parsed.get("utilization")


def query_nvidia_fields(
    *,
    temperature: bool = False,
    power: bool = False,
    utilization: bool = False,
    disable_nvidia: bool = False,
) -> NvidiaFields:
    """Read only the requested NVIDIA fields on the fan loop's NVML session.

    Temperature alone uses hwmon or ``nvmlDeviceGetTemperature``. Power and
    utilization are not asked unless the caller set those flags.
    """
    if not (temperature or power or utilization):
        return NvidiaFields()
    with _nvml_lock:
        if not _has_nvidia():
            return NvidiaFields()
        if disable_nvidia or dgpu_runtime_suspended():
            _nvml_shutdown()
            return NvidiaFields()
        temp = _nvidia_hwmon_temp_c() if temperature else None
        temp_source = "hwmon:nvidia" if temp is not None else ""
        if temperature and temp is not None and not power and not utilization:
            return NvidiaFields(temp_c=temp, temp_source=temp_source)
        power_w = None
        util = None
        power_source = ""
        util_source = ""
        if _nvml_ensure():
            if power:
                power_w = _nvml_power_w()
                power_source = "nvml" if power_w is not None else ""
            if utilization:
                util = _nvml_util_pct()
                util_source = "nvml" if util is not None else ""
            if temperature and temp is None:
                temp = _nvml_read_temp()
                temp_source = "nvml" if temp is not None else ""
        missing_temp = temperature and temp is None
        missing_power = power and power_w is None
        missing_util = utilization and util is None
    # nvidia-smi stays outside the NVML lock so a 1.2 s fallback cannot stall
    # the fan tick or close_nvidia.
    if missing_temp or missing_power or missing_util:
        smi_temp, smi_power, smi_util = _smi_selected(
            temperature=missing_temp,
            power=missing_power,
            utilization=missing_util,
        )
        if missing_temp and smi_temp is not None:
            temp = smi_temp
            temp_source = "nvidia-smi"
        if missing_power and smi_power is not None:
            power_w = smi_power
            power_source = "nvidia-smi"
        if missing_util and smi_util is not None:
            util = smi_util
            util_source = "nvidia-smi"
    return NvidiaFields(
        temp_c=temp,
        power_w=power_w,
        util_pct=util,
        temp_source=temp_source,
        power_source=power_source,
        util_source=util_source,
    )


def read_gpu_temp_c(*, disable_nvidia: bool = False) -> float | None:
    """GPU temperature in °C. NVIDIA goes through the shared field query."""
    if _has_nvidia():
        return query_nvidia_fields(temperature=True, disable_nvidia=disable_nvidia).temp_c
    return _amd_or_nouveau_temp_c()


def read_cpu_gpu_temps(*, disable_nvidia: bool = False) -> tuple[float | None, float | None]:
    return read_cpu_temp_c(), read_gpu_temp_c(disable_nvidia=disable_nvidia)
