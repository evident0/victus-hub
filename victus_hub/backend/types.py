"""Data types shared across the backend, matching the Rust struct fields."""

from dataclasses import dataclass, field


@dataclass
class SensorReading:
    value: str
    source: str = ""


@dataclass
class ExtraSensor:
    key: str
    group: str
    name: str
    unit: str
    value_min: float
    value_max: float
    numeric_value: float
    reading: SensorReading


@dataclass
class SensorSnapshot:
    cpu_fan: SensorReading = field(default_factory=lambda: SensorReading("0 RPM"))
    gpu_fan: SensorReading = field(default_factory=lambda: SensorReading("0 RPM"))
    cpu_temp: SensorReading = field(default_factory=lambda: SensorReading("0 C"))
    cpu_temp_c: float | None = None
    cpu_usage: SensorReading = field(default_factory=lambda: SensorReading("0 %"))
    cpu_usage_pct: float | None = None
    cpu_max_temp: SensorReading = field(default_factory=lambda: SensorReading("0 C"))
    gpu_temp: SensorReading = field(default_factory=lambda: SensorReading("0 C"))
    gpu_temp_c: float | None = None
    gpu_usage: SensorReading = field(default_factory=lambda: SensorReading("0 %"))
    gpu_usage_pct: float | None = None
    cpu_power: SensorReading = field(default_factory=lambda: SensorReading("0 W"))
    gpu_power: SensorReading = field(default_factory=lambda: SensorReading("0 W"))
    pwm_mode: SensorReading = field(default_factory=lambda: SensorReading("Automatic"))
    pwm_value: SensorReading = field(default_factory=lambda: SensorReading("0 / 255"))
    ram_usage: SensorReading = field(default_factory=lambda: SensorReading("0 GB"))
    ram_usage_pct: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None
    profile: SensorReading = field(default_factory=lambda: SensorReading("balanced"))
    extra_sensors: list[ExtraSensor] = field(default_factory=list)


@dataclass
class FanPoint:
    temp: int
    speed: int


@dataclass
class FanProfileConfig:
    cpu_points: list[FanPoint]
    gpu_points: list[FanPoint]


@dataclass
class FanConfig:
    profiles: list[FanProfileConfig]
    custom_enabled: bool
    # Non-None when the user has clicked a preset button (auto / max).
    # The fan-control loop backs off while a preset is active so the
    # hardware fan mode (max flag or manual pwm) survives.
    manual_preset: str | None = None
    # Ignore non-forced PWM target changes at or below this percentage.
    min_fan_change_pct: float = 2.0
    # Smart uses the built-in curve and faster EWMA. Only applies while
    # custom_enabled is True (the loop is driving PWM).
    smart_enabled: bool = False
    # Temperature response used by custom curves. Smart mode always uses its
    # aggressive response regardless of this setting.
    curve_response: str = "smooth"

# Re-export list for api.py convenience
__all__ = [
    "SensorReading",
    "ExtraSensor",
    "SensorSnapshot",
    "FanPoint",
    "FanProfileConfig",
    "FanConfig",
]
