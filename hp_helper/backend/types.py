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
class DiskUsage:
    """One mounted local disk / partition for the Storage card."""
    name: str
    used_gb: float
    total_gb: float
    usage_pct: float
    mount: str = ""


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
    gpu_pstate: str | None = None  # e.g. "P0" from nvidia-smi; None if unavailable
    cpu_power: SensorReading = field(default_factory=lambda: SensorReading("0 W"))
    gpu_power: SensorReading = field(default_factory=lambda: SensorReading("0 W"))
    pwm_mode: SensorReading = field(default_factory=lambda: SensorReading("Automatic"))
    pwm_value: SensorReading = field(default_factory=lambda: SensorReading("0 / 255"))
    ram_usage: SensorReading = field(default_factory=lambda: SensorReading("0 GB"))
    ram_usage_pct: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None
    disks: list[DiskUsage] = field(default_factory=list)
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
    # manual pwm1 / pwm1_enable state survives.
    manual_preset: str | None = None
    # Seconds to wait before ramping fan speed down after target drops.
    # 0 = immediate ramp-down. Default 10s (matches the original Rust impl).
    ramp_down_delay: float = 10.0
    # Fan-control tuning constants
    temp_window: int = 15
    write_min_delta_pct: float = 5.0
    ramp_up_pct: float = 30.0
    ramp_down_pct: float = 15.0
    # When the GPU holds P0 long enough, the override ramps the fan toward
    # p0_min_pct and blocks the curve from dropping below it until the
    # debounce releases the floor after ~25s of non-P0 (fan_control).
    p0_min_pct_enabled: bool = False
    p0_min_pct: int = 80

# Re-export list for api.py convenience
__all__ = [
    "SensorReading",
    "ExtraSensor",
    "SensorSnapshot",
    "FanPoint",
    "FanProfileConfig",
    "FanConfig",
]
