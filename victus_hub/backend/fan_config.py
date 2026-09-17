"""Fan-curve storage, normalization, and interpolation.

Ports fan_config.rs exactly.
"""

import json
import os
from pathlib import Path

from victus_hub.backend.types import FanPoint, FanProfileConfig, FanConfig

CONFIG_DIR_NAME = "victus-hub"
CONFIG_FILE_NAME = "config.json"
PROFILE_KEYS = ["power-saver", "balanced", "performance"]
CURVE_RESPONSE_SMOOTH = "smooth"
CURVE_RESPONSE_AGGRESSIVE = "aggressive"
CURVE_RESPONSES = (CURVE_RESPONSE_SMOOTH, CURVE_RESPONSE_AGGRESSIVE)

# ── Curve bounds (used by the chart and any page that needs them) ──

TEMP_MIN_C = 30
CPU_TEMP_MAX_C = 100
GPU_TEMP_MAX_C = 90


# ── Defaults ──

def default_cpu_points() -> list[FanPoint]:
    return [FanPoint(temp=TEMP_MIN_C, speed=0), FanPoint(temp=CPU_TEMP_MAX_C, speed=100)]


def default_gpu_points() -> list[FanPoint]:
    return [FanPoint(temp=TEMP_MIN_C, speed=0), FanPoint(temp=GPU_TEMP_MAX_C, speed=100)]


# Smart mode: zero-RPM idle, skip the ~30% stall band, quiet midrange,
# then steep near the thermal wall. GPU comes on a little earlier.
SMART_CPU_CURVE = (
    (30, 0), (58, 0), (60, 32), (70, 42), (80, 62), (90, 85), (100, 100),
)
SMART_GPU_CURVE = (
    (30, 0), (52, 0), (54, 32), (65, 48), (75, 68), (82, 88), (90, 100),
)


def smart_cpu_points() -> list[FanPoint]:
    return normalize_fan_points(
        [FanPoint(temp=t, speed=s) for t, s in SMART_CPU_CURVE],
        CPU_TEMP_MAX_C,
    )


def smart_gpu_points() -> list[FanPoint]:
    return normalize_fan_points(
        [FanPoint(temp=t, speed=s) for t, s in SMART_GPU_CURVE],
        GPU_TEMP_MAX_C,
    )


# ── Normalization ──

def normalize_fan_points(points: list[FanPoint], temp_max: int) -> list[FanPoint]:
    """Normalize a fan curve: clamp endpoints, enforce monotonic speed."""
    if len(points) >= 2:
        normalized = list(points)
    elif temp_max == GPU_TEMP_MAX_C:
        normalized = default_gpu_points()
    else:
        normalized = default_cpu_points()

    normalized.sort(key=lambda p: p.temp)

    if normalized:
        normalized[0].temp = TEMP_MIN_C
        normalized[-1].temp = temp_max

    minimum_speed = 0
    for point in normalized:
        point.speed = max(minimum_speed, min(point.speed, 100))
        minimum_speed = point.speed

    return normalized


# ── Config path ──

def _config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    home = os.environ.get("HOME")
    if home:
        return Path(home) / ".config" / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    return Path(".") / CONFIG_FILE_NAME


# ── Load / Save ──

def load() -> FanConfig:
    try:
        text = _config_path().read_text()
        stored = json.loads(text)
    except (OSError, json.JSONDecodeError):
        stored = {}

    custom_enabled = stored.get("custom_curve_enabled", False) or False
    manual_preset = stored.get("manual_preset") or None
    min_fan_change_pct = max(float(stored.get("min_fan_change_pct", 2.0)), 0.0)
    smart_enabled = bool(stored.get("smart_curve_enabled", False)) and custom_enabled
    curve_response = stored.get("fan_curve_response", CURVE_RESPONSE_SMOOTH)
    if curve_response not in CURVE_RESPONSES:
        curve_response = CURVE_RESPONSE_SMOOTH

    profiles = []
    for key in PROFILE_KEYS:
        cpu_raw = stored.get("curve_points_by_profile", {}).get(key)
        cpu_points = normalize_fan_points(
            [FanPoint(temp=t, speed=s) for t, s in (cpu_raw or [])],
            CPU_TEMP_MAX_C,
        ) if cpu_raw else default_cpu_points()

        gpu_raw = stored.get("gpu_curve_points_by_profile", {}).get(key)
        gpu_points = normalize_fan_points(
            [FanPoint(temp=t, speed=s) for t, s in (gpu_raw or [])],
            GPU_TEMP_MAX_C,
        ) if gpu_raw else default_gpu_points()

        profiles.append(FanProfileConfig(cpu_points=cpu_points, gpu_points=gpu_points))
    return FanConfig(
        profiles=profiles,
        custom_enabled=custom_enabled,
        manual_preset=manual_preset,
        min_fan_change_pct=min_fan_change_pct,
        smart_enabled=smart_enabled,
        curve_response=curve_response,
    )


def save_profile(profile: int, cpu_points: list[FanPoint], gpu_points: list[FanPoint]) -> FanConfig:
    config = load()
    idx = min(profile, len(PROFILE_KEYS) - 1)
    config.profiles[idx] = FanProfileConfig(
        cpu_points=normalize_fan_points(cpu_points, 100),
        gpu_points=normalize_fan_points(gpu_points, 90),
    )
    save_all(config)
    return config


def save_custom_enabled(enabled: bool) -> FanConfig:
    config = load()
    config.custom_enabled = enabled
    if not enabled:
        config.smart_enabled = False
    save_all(config)
    return config


def save_smart_enabled(enabled: bool) -> FanConfig:
    config = load()
    config.smart_enabled = enabled
    if enabled:
        config.custom_enabled = True
        config.manual_preset = None
    save_all(config)
    return config


def save_curve_response(response: str) -> FanConfig:
    """Persist the temperature response used by custom fan curves."""
    config = load()
    config.curve_response = (
        response if response in CURVE_RESPONSES else CURVE_RESPONSE_SMOOTH
    )
    save_all(config)
    return config


def save_manual_preset(preset: str | None) -> FanConfig:
    """Record which preset the user clicked (auto / max) or clear it.

    The fan-control background loop reads this on every tick and backs
    off while a preset is active, so clicking Max does not get
    overridden by an auto-mode cleanup the next time the loop sees
    custom_enabled == False.
    """
    config = load()
    config.manual_preset = preset
    save_all(config)
    return config


def save_all(config: FanConfig) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    cpu_map = {}
    gpu_map = {}
    for i, profile in enumerate(config.profiles):
        key = PROFILE_KEYS[i]
        cpu_map[key] = [[p.temp, p.speed] for p in profile.cpu_points]
        gpu_map[key] = [[p.temp, p.speed] for p in profile.gpu_points]

    stored = {
        "custom_tuned_profile": "balanced",
        "custom_curve_enabled": config.custom_enabled,
        "smart_curve_enabled": config.smart_enabled,
        "fan_curve_response": config.curve_response,
        "manual_preset": config.manual_preset,
        "min_fan_change_pct": config.min_fan_change_pct,
        "curve_points_by_profile": cpu_map,
        "gpu_curve_points_by_profile": gpu_map,
    }
    path.write_text(json.dumps(stored, indent=2))


# ── Interpolation ──

def interpolate_fan(points: list[FanPoint], temp: int) -> int:
    """Linear interpolation over sorted fan curve points.

    Returns 0 for empty input; clamps to first/last speed for out-of-range temps.
    """
    if not points:
        return 0
    if temp <= points[0].temp:
        return points[0].speed
    for i in range(1, len(points)):
        if temp <= points[i].temp:
            dx = points[i].temp - points[i - 1].temp
            if dx == 0:
                return points[i].speed
            t = (temp - points[i - 1].temp) / dx
            return points[i - 1].speed + int(
                t * (points[i].speed - points[i - 1].speed)
            )
    return points[-1].speed
