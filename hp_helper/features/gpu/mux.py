"""Read GPU MUX state from the hp-gpu-mux kernel module sysfs nodes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hp_helper.backend.sysfs_read import read_int, read_text

GPU_MUX_PLATFORM = Path("/sys/devices/platform/hp-gpu-mux")

MODE_BY_NAME: dict[str, int] = {
    "hybrid": 0,
    "discrete": 1,
    "optimus": 2,
    "uma": 3,
}


@dataclass(frozen=True)
class GpuMuxMode:
    name: str
    index: int
    label: str


@dataclass(frozen=True)
class GpuMuxState:
    modes: tuple[GpuMuxMode, ...]
    current_index: int | None


def _mode_label(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


def read_gpu_mux_state() -> GpuMuxState | None:
    """Return supported modes and the current mode index, or None if unavailable."""
    if not GPU_MUX_PLATFORM.is_dir():
        return None

    names_raw = read_text(GPU_MUX_PLATFORM / "gpu_mux_supported_names")
    if not names_raw:
        return None

    modes: list[GpuMuxMode] = []
    for name in names_raw.split():
        index = MODE_BY_NAME.get(name)
        if index is None:
            continue
        modes.append(GpuMuxMode(name=name, index=index, label=_mode_label(name)))

    if not modes:
        return None

    current_index = read_int(GPU_MUX_PLATFORM / "gpu_mux_mode")
    return GpuMuxState(modes=tuple(modes), current_index=current_index)