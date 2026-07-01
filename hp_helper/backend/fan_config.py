"""Fan-curve storage, normalization, and interpolation.

Ports fan_config.rs exactly.
"""

import json
import os
from pathlib import Path

from hp_helper.backend.types import FanPoint, FanProfileConfig, FanConfig

CONFIG_DIR_NAME = "hp-helper"
CONFIG_FILE_NAME = "config.json"
PROFILE_KEYS = ["power-saver", "balanced", "performance"]


# ── Defaults ──

def default_cpu_points() -> list[FanPoint]:
    return [FanPoint(temp=30, speed=0), FanPoint(temp=100, speed=100)]


def default_gpu_points() -> list[FanPoint]:
    return [FanPoint(temp=30, speed=0), FanPoint(temp=90, speed=100)]


# ── Normalization ──

def normalize_fan_points(points: list[FanPoint], temp_max: int) -> list[FanPoint]:
    """Normalize a fan curve: clamp endpoints, enforce monotonic speed."""
    if len(points) >= 2:
        normalized = list(points)
    elif temp_max == 90:
        normalized = default_gpu_points()
    else:
        normalized = default_cpu_points()

    normalized.sort(key=lambda p: p.temp)

    if normalized:
        normalized[0].temp = 30
        normalized[-1].temp = temp_max

    minimum_speed = 0
    for point in normalized:
        point.speed = max(minimum_speed, min(point.speed, 100))
        minimum_speed = point.speed

    return normalized


def normalize_points(tuple_points: list[tuple[int, int]], temp_max: int) -> list[FanPoint]:
    """Normalize from tuple-of-tuples form (used by stored config)."""
    return normalize_fan_points(
        [FanPoint(temp=t, speed=s) for t, s in tuple_points],
        temp_max,
    )


def tuple_points(points: list[FanPoint]) -> list[tuple[int, int]]:
    """Convert FanPoint list to tuple list for JSON storage."""
    return [(p.temp, p.speed) for p in points]


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

    profiles = []
    for key in PROFILE_KEYS:
        cpu_raw = stored.get("curve_points_by_profile", {}).get(key)
        if cpu_raw:
            cpu_points = normalize_points(cpu_raw, 100)
        else:
            cpu_points = default_cpu_points()

        gpu_raw = stored.get("gpu_curve_points_by_profile", {}).get(key)
        if gpu_raw:
            gpu_points = normalize_points(gpu_raw, 90)
        else:
            gpu_points = default_gpu_points()

        profiles.append(FanProfileConfig(cpu_points=cpu_points, gpu_points=gpu_points))

    return FanConfig(profiles=profiles, custom_enabled=custom_enabled)


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
    save_all(config)
    return config


def save_all(config: FanConfig) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    cpu_map = {}
    gpu_map = {}
    for i, profile in enumerate(config.profiles):
        key = PROFILE_KEYS[i]
        cpu_map[key] = tuple_points(profile.cpu_points)
        gpu_map[key] = tuple_points(profile.gpu_points)

    stored = {
        "custom_tuned_profile": "balanced",
        "custom_curve_enabled": config.custom_enabled,
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
