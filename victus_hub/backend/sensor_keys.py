"""Informational sensors each page is allowed to ask the daemon for.

The GUI sends only the keys the visible page displays. A page that shows CPU
wattage does not ask for GPU wattage or utilization. Keyboard and Settings
display none of these. The fan loop's CPU and GPU temperatures are not in
this set: those stay on the daemon while a custom curve is driving the fans.
"""

from __future__ import annotations

HOME_PAGE_KEYS = frozenset({
    "cpu-temp",
    "cpu-usage",
    "cpu-power",
    "gpu-temp",
    "gpu-usage",
    "gpu-power",
    "cpu-fan",
    "gpu-fan",
    "ram-usage",
})

POWER_PAGE_KEYS = frozenset({
    "cpu-power",
    "cpu-frequency",
})

FANS_PAGE_KEYS = frozenset({
    "cpu-fan",
    "gpu-fan",
})

KEYBOARD_PAGE_KEYS: frozenset[str] = frozenset()

SENSORS_PAGE_KEYS = frozenset({
    "cpu-temp",
    "cpu-usage",
    "cpu-power",
    "gpu-temp",
    "gpu-usage",
    "gpu-power",
    "cpu-fan",
    "gpu-fan",
    "pwm-value",
    "pwm-mode",
    "ram-usage",
    "cpu-frequency",
    "lm-sensors",
})

SETTINGS_PAGE_KEYS: frozenset[str] = frozenset()

# Same order as MainWindow's stacked pages.
PAGE_SENSOR_KEYS = (
    HOME_PAGE_KEYS,
    POWER_PAGE_KEYS,
    FANS_PAGE_KEYS,
    KEYBOARD_PAGE_KEYS,
    SENSORS_PAGE_KEYS,
    SETTINGS_PAGE_KEYS,
)

REQUESTABLE_KEYS = SENSORS_PAGE_KEYS
GPU_QUERY_KEYS = frozenset({"gpu-temp", "gpu-usage", "gpu-power"})


def keys_for_page(index: int) -> frozenset[str]:
    if index < 0 or index >= len(PAGE_SENSOR_KEYS):
        return frozenset()
    return PAGE_SENSOR_KEYS[index]


def request_key_for_graph(sensor_key: str) -> str:
    """Map one graph row onto the daemon key that can supply it."""
    if sensor_key.startswith("cpu-frequency-"):
        return "cpu-frequency"
    if sensor_key.startswith("lm-"):
        return "lm-sensors"
    return sensor_key
