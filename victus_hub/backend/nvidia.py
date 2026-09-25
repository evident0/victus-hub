"""GUI-side NVIDIA name and the query-disable preference.

Temperature, power, and utilization are read in the daemon. This module
must not call ``nvmlInit``: that maps libcuda for the life of the process.
"""

from __future__ import annotations

from functools import wraps
from pathlib import Path
from threading import RLock

from PySide6.QtCore import QSettings

DISABLE_NVIDIA_QUERIES_KEY = "sensors/disableNvidiaQueries"
_queries_disabled: bool | None = None
_power_save_active = False
_query_lock = RLock()


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


def set_nvidia_queries_disabled(disabled: bool) -> None:
    """Update immediately; never wait for driver work on the GUI thread."""
    global _queries_disabled
    _queries_disabled = disabled
    QSettings("victus-hub", "victus-hub").setValue(
        DISABLE_NVIDIA_QUERIES_KEY, disabled,
    )


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
