"""Sensor definitions — metadata for each sensor type.

Ports sensor-definitions.ts: SensorDefinition, SENSOR_DEFINITIONS,
dynamicSensorDefinition, sensorDefinitionForKey.
"""

from dataclasses import dataclass, field
from typing import Callable
import re

@dataclass
class SensorDefinition:
    key: str
    group: str
    name: str
    unit: str
    value_min: float
    value_max: float
    graphable: bool
    reading: Callable = field(repr=False)
    numeric_value: Callable = field(repr=False)

_LEADING_NUMBER_RE = re.compile(r"^-?(\d+\.?\d*|\.\d+)")


def _parse_leading_number(value: str) -> float | None:
    """Extract leading numeric value from a string like JS parseFloat."""
    if not isinstance(value, str):
        return None
    m = _LEADING_NUMBER_RE.match(value.strip())
    if m:
        try:
            return float(m.group())
        except ValueError:
            return None
    return None


def _make_reading(field_name: str):
    return lambda snap: getattr(snap, field_name)


def _make_numeric(field_name: str):
    return lambda snap: getattr(snap, field_name, None)


def _make_numeric_parse(field_name: str):
    return lambda snap: _parse_leading_number(getattr(snap, field_name).value)


SENSOR_DEFINITIONS: list[SensorDefinition] = [
    SensorDefinition(
        key="cpu-temp", group="CPU", name="CPU Temp", unit="\u00B0C",
        value_min=0, value_max=100, graphable=True,
        reading=_make_reading("cpu_temp"),
        numeric_value=_make_numeric("cpu_temp_c"),
    ),
    SensorDefinition(
        key="cpu-usage", group="CPU", name="CPU Usage", unit="%",
        value_min=0, value_max=100, graphable=False,
        reading=_make_reading("cpu_usage"),
        numeric_value=_make_numeric("cpu_usage_pct"),
    ),
    SensorDefinition(
        key="cpu-power", group="CPU", name="CPU Power", unit="W",
        value_min=0, value_max=80, graphable=True,
        reading=_make_reading("cpu_power"),
        numeric_value=_make_numeric_parse("cpu_power"),
    ),
    SensorDefinition(
        key="gpu-temp", group="GPU", name="GPU Temp", unit="\u00B0C",
        value_min=0, value_max=100, graphable=True,
        reading=_make_reading("gpu_temp"),
        numeric_value=_make_numeric("gpu_temp_c"),
    ),
    SensorDefinition(
        key="gpu-usage", group="GPU", name="GPU Usage", unit="%",
        value_min=0, value_max=100, graphable=False,
        reading=_make_reading("gpu_usage"),
        numeric_value=_make_numeric("gpu_usage_pct"),
    ),
    SensorDefinition(
        key="gpu-power", group="GPU", name="GPU Power", unit="W",
        value_min=0, value_max=120, graphable=True,
        reading=_make_reading("gpu_power"),
        numeric_value=_make_numeric_parse("gpu_power"),
    ),
    SensorDefinition(
        key="cpu-fan", group="HP Embedded Controller", name="CPU Fan", unit="RPM",
        value_min=0, value_max=6000, graphable=True,
        reading=_make_reading("cpu_fan"),
        numeric_value=_make_numeric_parse("cpu_fan"),
    ),
    SensorDefinition(
        key="gpu-fan", group="HP Embedded Controller", name="GPU Fan", unit="RPM",
        value_min=0, value_max=6000, graphable=True,
        reading=_make_reading("gpu_fan"),
        numeric_value=_make_numeric_parse("gpu_fan"),
    ),
    SensorDefinition(
        key="pwm-value", group="HP Embedded Controller", name="HP PWM Value", unit="PWM",
        value_min=0, value_max=255, graphable=True,
        reading=_make_reading("pwm_value"),
        numeric_value=_make_numeric_parse("pwm_value"),
    ),
    SensorDefinition(
        key="pwm-mode", group="HP Embedded Controller", name="HP PWM Mode", unit="",
        value_min=0, value_max=2, graphable=False,
        reading=_make_reading("pwm_mode"),
        numeric_value=lambda _snap: None,
    ),
    SensorDefinition(
        key="profile", group="System", name="System Profile", unit="",
        value_min=0, value_max=2, graphable=False,
        reading=_make_reading("profile"),
        numeric_value=lambda _snap: None,
    ),
]


def dynamic_sensor_definition(sensor) -> SensorDefinition:
    """Create a SensorDefinition from an ExtraSensor."""
    return SensorDefinition(
        key=sensor.key,
        group=sensor.group,
        name=sensor.name,
        unit=sensor.unit,
        value_min=sensor.value_min,
        value_max=sensor.value_max,
        graphable=True,
        reading=lambda snap: next(
            (e.reading for e in snap.extra_sensors if e.key == sensor.key),
            sensor.reading,
        ),
        numeric_value=lambda snap: next(
            (e.numeric_value for e in snap.extra_sensors if e.key == sensor.key),
            None,
        ),
    )


def sensor_definition_for_key(key: str, snapshot=None) -> SensorDefinition:
    """Look up a sensor definition by key, falling back to dynamic sensors."""
    for d in SENSOR_DEFINITIONS:
        if d.key == key:
            return d
    if snapshot:
        for s in snapshot.extra_sensors:
            if s.key == key:
                return dynamic_sensor_definition(s)
    return SENSOR_DEFINITIONS[0]
