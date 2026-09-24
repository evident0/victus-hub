"""Hardware controls exposed to the UI by the daemon.

Only advertise controls backed by the currently exposed sysfs interfaces.
This is intentionally independent of model names and persisted user policy.
"""

from dataclasses import dataclass
from pathlib import Path

from victus_hub.backend.sysfs_read import find_hwmon_by_name
from victus_hub.features.gpu.mux import GpuMuxMode, GpuMuxState, read_gpu_mux_state


@dataclass(frozen=True)
class HardwareCapabilities:
    fan_modes: tuple[str, ...] = ("auto",)
    keyboard_lighting: bool = False
    gpu_mux: GpuMuxState | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "HardwareCapabilities":
        modes = raw.get("fan_modes")
        if not isinstance(modes, list) or not all(isinstance(m, str) for m in modes):
            raise ValueError("invalid fan_modes capability")
        if not isinstance(raw.get("keyboard_lighting"), bool):
            raise ValueError("invalid keyboard_lighting capability")
        allowed = ("auto", "smart", "max", "custom")
        mux = raw.get("gpu_mux")
        mux_state = None
        if mux is not None:
            if not isinstance(mux, dict) or not isinstance(mux.get("modes"), list):
                raise ValueError("invalid gpu_mux capability")
            mux_state = GpuMuxState(
                tuple(GpuMuxMode(name=m["name"], index=m["index"], label=m["label"])
                      for m in mux["modes"]),
                mux["current_index"],
            )
        return cls(
            fan_modes=tuple(m for m in allowed if m == "auto" or m in modes),
            keyboard_lighting=raw["keyboard_lighting"],
            gpu_mux=mux_state,
        )

    def to_dict(self) -> dict:
        return {
            "fan_modes": list(self.fan_modes),
            "keyboard_lighting": self.keyboard_lighting,
            "gpu_mux": None if self.gpu_mux is None else {
                "modes": [{"name": m.name, "index": m.index, "label": m.label}
                          for m in self.gpu_mux.modes],
                "current_index": self.gpu_mux.current_index,
            },
        }


def keyboard_lighting_supported() -> bool:
    # The daemon's lighting writer uses these hp-kbd-rgb LED names. Check
    # actual LED controls rather than module presence or zone_count alone.
    leds = Path("/sys/class/leds")
    try:
        return any(
            led.name == "hp::kbd_backlight"
            or led.name.startswith("hp::kbd_backlight_zoned_backlight-")
            for led in leds.iterdir()
            if (led / "multi_intensity").exists() and (led / "brightness").exists()
        )
    except OSError:
        return False


def detect_capabilities() -> HardwareCapabilities:
    hwmon = find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")
    modes = ["auto"]
    if hwmon is not None and (hwmon / "pwm1_enable").exists():
        # Manual duty needs both controls; pwm1_enable alone is EC Auto/Max.
        manual = (hwmon / "pwm1").exists()
        if manual:
            modes.append("smart")
        modes.append("max")
        if manual:
            modes.append("custom")
    return HardwareCapabilities(tuple(modes), keyboard_lighting_supported(), read_gpu_mux_state())
