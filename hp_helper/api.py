"""Mock API stubs for Phase 1 — returns plausible fake data.

Ports api.ts: all interfaces and invoke commands as Python functions.
"""

import random
from dataclasses import dataclass, field


# ── Data types ──

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
    cpu_temp: SensorReading = field(default_factory=lambda: SensorReading("0°C"))
    cpu_temp_c: float | None = None
    cpu_usage: SensorReading = field(default_factory=lambda: SensorReading("0%"))
    cpu_usage_pct: float | None = None
    cpu_max_temp: SensorReading = field(default_factory=lambda: SensorReading("0°C"))
    gpu_temp: SensorReading = field(default_factory=lambda: SensorReading("0°C"))
    gpu_temp_c: float | None = None
    gpu_usage: SensorReading = field(default_factory=lambda: SensorReading("0%"))
    gpu_usage_pct: float | None = None
    cpu_power: SensorReading = field(default_factory=lambda: SensorReading("0.0 W"))
    gpu_power: SensorReading = field(default_factory=lambda: SensorReading("0.0 W"))
    pwm_mode: SensorReading = field(default_factory=lambda: SensorReading("Auto"))
    pwm_value: SensorReading = field(default_factory=lambda: SensorReading("128"))
    profile: SensorReading = field(default_factory=lambda: SensorReading("Balanced"))
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


# ── Mock state ──

_mock_cpu_temp = 55.0
_mock_gpu_temp = 48.0
_mock_cpu_usage = 35.0
_mock_gpu_usage = 22.0
_mock_cpu_fan = 2100
_mock_gpu_fan = 1800
_mock_nvme_temp = 38.0
_mock_nvme_sensor1 = 34.0
_mock_battery_charge = 85.0
_mock_battery_voltage = 16.8

_mock_fan_config = FanConfig(
    profiles=[
        FanProfileConfig(
            cpu_points=[FanPoint(30, 0), FanPoint(100, 100)],
            gpu_points=[FanPoint(30, 0), FanPoint(90, 100)],
        ),
        FanProfileConfig(
            cpu_points=[FanPoint(30, 0), FanPoint(100, 100)],
            gpu_points=[FanPoint(30, 0), FanPoint(90, 100)],
        ),
        FanProfileConfig(
            cpu_points=[FanPoint(30, 30), FanPoint(100, 100)],
            gpu_points=[FanPoint(30, 30), FanPoint(90, 100)],
        ),
    ],
    custom_enabled=False,
)


def _jitter(value: float, amount: float) -> float:
    return value + random.uniform(-amount, amount)


# ── API functions ──

def get_hardware_title() -> str:
    return "HP Laptop (mock)"


def read_sensors() -> SensorSnapshot:
    global _mock_cpu_temp, _mock_gpu_temp, _mock_cpu_usage, _mock_gpu_usage
    global _mock_cpu_fan, _mock_gpu_fan, _mock_cpu_power, _mock_gpu_power
    global _mock_nvme_temp, _mock_nvme_sensor1, _mock_battery_charge, _mock_battery_voltage

    _mock_cpu_temp = _jitter(55.0, 3.0)
    _mock_gpu_temp = _jitter(48.0, 2.0)
    _mock_cpu_usage = _jitter(35.0, 8.0)
    _mock_gpu_usage = _jitter(22.0, 6.0)
    _mock_cpu_fan = max(0, int(_jitter(2100, 200)))
    _mock_gpu_fan = max(0, int(_jitter(1800, 150)))
    _mock_cpu_power = max(0, _jitter(18.5, 3.0))
    _mock_gpu_power = max(0, _jitter(12.0, 2.0))
    _mock_nvme_temp = _jitter(38.0, 2.0)
    _mock_nvme_sensor1 = _jitter(34.0, 1.5)
    _mock_battery_charge = _jitter(85.0, 2.0)
    _mock_battery_voltage = _jitter(16.8, 0.3)

    return SensorSnapshot(
        cpu_fan=SensorReading(f"{_mock_cpu_fan} RPM"),
        gpu_fan=SensorReading(f"{_mock_gpu_fan} RPM"),
        cpu_temp=SensorReading(f"{_mock_cpu_temp:.1f}\u00B0C"),
        cpu_temp_c=round(_mock_cpu_temp, 1),
        cpu_usage=SensorReading(f"{_mock_cpu_usage:.1f}%"),
        cpu_usage_pct=round(_mock_cpu_usage, 1),
        cpu_max_temp=SensorReading(f"{_mock_cpu_temp + 5:.1f}\u00B0C"),
        gpu_temp=SensorReading(f"{_mock_gpu_temp:.1f}\u00B0C"),
        gpu_temp_c=round(_mock_gpu_temp, 1),
        gpu_usage=SensorReading(f"{_mock_gpu_usage:.1f}%"),
        gpu_usage_pct=round(_mock_gpu_usage, 1),
        cpu_power=SensorReading(f"{_mock_cpu_power:.1f} W"),
        gpu_power=SensorReading(f"{_mock_gpu_power:.1f} W"),
        pwm_mode=SensorReading("Auto"),
        pwm_value=SensorReading("128"),
        profile=SensorReading("Balanced"),
        extra_sensors=[
            ExtraSensor(
                key="extra-nvme-temp", group="Drives",
                name="NVMe Composite Temp", unit="\u00B0C",
                value_min=0, value_max=80,
                numeric_value=round(_mock_nvme_temp, 1),
                reading=SensorReading(f"{_mock_nvme_temp:.1f}\u00B0C", "nvme-pci"),
            ),
            ExtraSensor(
                key="extra-nvme-sensor1", group="Drives",
                name="NVMe Sensor 1", unit="\u00B0C",
                value_min=0, value_max=80,
                numeric_value=round(_mock_nvme_sensor1, 1),
                reading=SensorReading(f"{_mock_nvme_sensor1:.1f}\u00B0C", "nvme-pci"),
            ),
            ExtraSensor(
                key="extra-battery-charge", group="Battery",
                name="Battery Charge", unit="%",
                value_min=0, value_max=100,
                numeric_value=round(_mock_battery_charge, 1),
                reading=SensorReading(f"{_mock_battery_charge:.1f}%", "BAT0"),
            ),
            ExtraSensor(
                key="extra-battery-voltage", group="Battery",
                name="Battery Voltage", unit="V",
                value_min=10, value_max=20,
                numeric_value=round(_mock_battery_voltage, 1),
                reading=SensorReading(f"{_mock_battery_voltage:.2f} V", "BAT0"),
            ),
        ],
    )


def get_current_profile() -> int | None:
    return 1  # Balanced


def set_system_profile(profile: int) -> str:
    return "ok"


def set_fan_auto() -> str:
    return "ok"


def set_fan_pwm(pwm: int) -> str:
    return "ok"


def set_keyboard_color(red: int, green: int, blue: int) -> str:
    return "ok"


def get_fan_config() -> FanConfig:
    return _mock_fan_config


def save_fan_profile(profile: int, cpu_points: list[FanPoint],
                     gpu_points: list[FanPoint]) -> FanConfig:
    if 0 <= profile < len(_mock_fan_config.profiles):
        _mock_fan_config.profiles[profile] = FanProfileConfig(
            cpu_points=list(cpu_points),
            gpu_points=list(gpu_points),
        )
    return _mock_fan_config


def set_custom_fan_enabled(enabled: bool) -> FanConfig:
    _mock_fan_config.custom_enabled = enabled
    return _mock_fan_config


def apply_power_limits(stapm: int, fast: int, slow: int) -> str:
    return "ok"
