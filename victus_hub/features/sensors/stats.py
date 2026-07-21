"""Sensor stats accumulation and row building.

Ported from the stats helpers that lived at the bottom of main_window.py
(originally ported from App.tsx).
"""

from victus_hub.features.sensors.definitions import (
    SENSOR_DEFINITIONS,
    dynamic_sensor_definition,
    SensorDefinition,
)


def format_stat(value: float, unit: str) -> str:
    if value == int(value):
        formatted = str(int(value))
    else:
        formatted = f"{value:.1f}"
    return f"{formatted} {unit}" if unit else formatted


def parse_reading_num(reading) -> float | None:
    try:
        return float(str(reading.value).split()[0])
    except (ValueError, AttributeError):
        return None


def next_stats(snapshot, previous: dict) -> dict:
    """Accumulate min/max/avg per sensor."""
    changed = False
    next_stats = dict(previous)
    defs: list[SensorDefinition] = list(SENSOR_DEFINITIONS)
    for es in snapshot.extra_sensors:
        defs.append(dynamic_sensor_definition(es))

    for d in defs:
        value = d.numeric_value(snapshot)
        if value is None:
            continue
        if not isinstance(value, (int, float)):
            continue
        cur = next_stats.get(d.key)
        if cur:
            next_stats[d.key] = {
                "minimum": min(cur["minimum"], value),
                "maximum": max(cur["maximum"], value),
                "sum": cur["sum"] + value,
                "count": cur["count"] + 1,
            }
        else:
            next_stats[d.key] = {
                "minimum": value,
                "maximum": value,
                "sum": value,
                "count": 1,
            }
        changed = True
    return next_stats if changed else previous


def missing_stats(reading) -> dict:
    val = getattr(reading, "value", str(reading) if reading is not None else "—")
    src = getattr(reading, "source", "")
    return {
        "current": {"value": val, "source": src},
        "minimum": "—",
        "maximum": "—",
        "average": "—",
    }
def build_rows(snapshot, stats_by_key: dict) -> list:
    """Build sensor table rows from snapshot + accumulated stats."""
    defs: list[SensorDefinition] = list(SENSOR_DEFINITIONS)
    for es in snapshot.extra_sensors:
        defs.append(dynamic_sensor_definition(es))

    rows = []
    for d in defs:
        current = d.reading(snapshot)
        stats = stats_by_key.get(d.key)
        if not stats:
            rows.append({
                "definition": d,
                "stats": missing_stats(current),
            })
        else:
            rows.append({
                "definition": d,
                "stats": {
                    "current": {"value": current.value, "source": getattr(current, "source", "")},
                    "minimum": format_stat(stats["minimum"], d.unit),
                    "maximum": format_stat(stats["maximum"], d.unit),
                    "average": format_stat(stats["sum"] / stats["count"], d.unit),
                },
            })
    return rows