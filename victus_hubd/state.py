"""Persisted daemon policy so control loops survive UI close and reboot."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from victus_hub.backend.fan_config import config_from_dict, config_to_dict
from victus_hub.backend.types import FanConfig
from victus_hub.features.keyboard.lighting import (
    LightingSettings,
    lighting_from_dict,
    lighting_to_dict,
)
from victus_hub.backend.power_limits import (
    DEFAULT_POWER_LIMIT_MW,
    DEFAULT_REAPPLY_SECONDS,
    DEFAULT_TCTL_TEMP_C,
    clamp_power_limit,
    clamp_reapply_seconds,
    clamp_tctl_temp,
)

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("/var/lib/victus-hubd/state.json")


def state_path() -> Path:
    override = os.environ.get("VICTUS_HUBD_STATE")
    if override:
        return Path(override)
    return DEFAULT_STATE_PATH


@dataclass
class PowerPolicy:
    enabled: bool = False
    stapm_limit: int = DEFAULT_POWER_LIMIT_MW
    fast_limit: int = DEFAULT_POWER_LIMIT_MW
    slow_limit: int = DEFAULT_POWER_LIMIT_MW
    tctl_temp: int = DEFAULT_TCTL_TEMP_C
    reapply_seconds: int = DEFAULT_REAPPLY_SECONDS


@dataclass
class DaemonState:
    fan: FanConfig = field(default_factory=lambda: config_from_dict({}))
    lighting: LightingSettings = field(
        default_factory=lambda: LightingSettings(enabled=False),
    )
    power: PowerPolicy = field(default_factory=PowerPolicy)
    cpu_frequency: tuple[int, int] | None = None
    battery_power_save: bool = False
    hardware_shortcuts: bool = False
    profile_before_battery: int | None = None
    initialized: bool = False
    disable_nvidia_queries: bool = False


def power_from_dict(raw: dict | None) -> PowerPolicy:
    raw = raw or {}
    try:
        return PowerPolicy(
            enabled=bool(raw.get("enabled", False)),
            stapm_limit=clamp_power_limit(int(raw.get("stapm_limit", DEFAULT_POWER_LIMIT_MW))),
            fast_limit=clamp_power_limit(int(raw.get("fast_limit", DEFAULT_POWER_LIMIT_MW))),
            slow_limit=clamp_power_limit(int(raw.get("slow_limit", DEFAULT_POWER_LIMIT_MW))),
            tctl_temp=clamp_tctl_temp(int(raw.get("tctl_temp", DEFAULT_TCTL_TEMP_C))),
            reapply_seconds=clamp_reapply_seconds(
                int(raw.get("reapply_seconds", DEFAULT_REAPPLY_SECONDS)),
            ),
        )
    except (TypeError, ValueError):
        return PowerPolicy()


def power_to_dict(policy: PowerPolicy) -> dict:
    return {
        "enabled": policy.enabled,
        "stapm_limit": policy.stapm_limit,
        "fast_limit": policy.fast_limit,
        "slow_limit": policy.slow_limit,
        "tctl_temp": policy.tctl_temp,
        "reapply_seconds": policy.reapply_seconds,
    }


def _cpu_frequency_from_raw(raw) -> tuple[int, int] | None:
    if not raw:
        return None
    try:
        if isinstance(raw, dict):
            minimum, maximum = int(raw["min"]), int(raw["max"])
        else:
            minimum, maximum = int(raw[0]), int(raw[1])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if 0 < minimum <= maximum:
        return minimum, maximum
    return None


def state_to_dict(state: DaemonState) -> dict:
    payload = {
        "fan": config_to_dict(state.fan),
        "lighting": lighting_to_dict(state.lighting),
        "power": power_to_dict(state.power),
        "battery_power_save": bool(state.battery_power_save),
        "hardware_shortcuts": bool(state.hardware_shortcuts),
        "profile_before_battery": state.profile_before_battery,
        "initialized": bool(state.initialized),
        "disable_nvidia_queries": state.disable_nvidia_queries,
    }
    if state.cpu_frequency is not None:
        payload["cpu_frequency"] = {
            "min": state.cpu_frequency[0],
            "max": state.cpu_frequency[1],
        }
    else:
        payload["cpu_frequency"] = None
    return payload


def state_from_dict(raw: dict | None) -> DaemonState:
    raw = raw if isinstance(raw, dict) else {}
    fan_raw = raw.get("fan")
    if not isinstance(fan_raw, dict):
        fan_raw = {}
    lighting_raw = raw.get("lighting")
    if not isinstance(lighting_raw, dict) or not lighting_raw:
        lighting = LightingSettings(enabled=False)
    else:
        lighting = lighting_from_dict(lighting_raw)
    power_raw = raw.get("power")
    if not isinstance(power_raw, dict):
        power_raw = {}
    before = raw.get("profile_before_battery")
    try:
        before = int(before) if before is not None else None
    except (TypeError, ValueError):
        before = None
    if before is not None:
        before = max(0, min(before, 2))
    if "initialized" in raw:
        initialized = bool(raw["initialized"])
    else:
        initialized = bool(raw)
    return DaemonState(
        fan=config_from_dict(fan_raw),
        lighting=lighting,
        power=power_from_dict(power_raw),
        cpu_frequency=_cpu_frequency_from_raw(raw.get("cpu_frequency")),
        battery_power_save=bool(raw.get("battery_power_save", False)),
        hardware_shortcuts=bool(raw.get("hardware_shortcuts", False)),
        profile_before_battery=before,
        initialized=initialized,
        disable_nvidia_queries=bool(raw.get("disable_nvidia_queries", False)),
    )


def load_state(path: Path | None = None) -> DaemonState:
    path = path or state_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return DaemonState()
    if not isinstance(raw, dict):
        return DaemonState()
    return state_from_dict(raw)


def save_state(state: DaemonState, path: Path | None = None) -> None:
    path = path or state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state_to_dict(state), indent=2) + "\n")
        tmp.replace(path)
    except OSError as error:
        logger.warning("failed to persist daemon state: %s", error)
