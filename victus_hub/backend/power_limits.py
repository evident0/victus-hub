"""Power-limit constants and clamps shared by the UI and daemon."""

from dataclasses import dataclass

POWER_MIN_MW = 15_000
POWER_MAX_MW = 120_000
POWER_STEP_MW = 1_000
DEFAULT_POWER_LIMIT_MW = 25_000
DEFAULT_REAPPLY_SECONDS = 5
REAPPLY_MIN_S = 1
REAPPLY_MAX_S = 120

TCTL_TEMP_MIN_C = 75
TCTL_TEMP_MAX_C = 100
DEFAULT_TCTL_TEMP_C = 95


@dataclass
class PowerLimitSettings:
    stapm_limit: int = DEFAULT_POWER_LIMIT_MW
    fast_limit: int = DEFAULT_POWER_LIMIT_MW
    slow_limit: int = DEFAULT_POWER_LIMIT_MW
    tctl_temp: int = DEFAULT_TCTL_TEMP_C
    reapply_seconds: int = DEFAULT_REAPPLY_SECONDS


def clamp_power_limit(value: int) -> int:
    """Clamp and round to nearest power step."""
    return max(POWER_MIN_MW, min(POWER_MAX_MW,
               round(value / POWER_STEP_MW) * POWER_STEP_MW))


def clamp_tctl_temp(value: int) -> int:
    """Clamp Tctl temperature to the supported range (°C)."""
    return max(TCTL_TEMP_MIN_C, min(TCTL_TEMP_MAX_C, int(value)))


def clamp_reapply_seconds(value: int) -> int:
    """Clamp the ryzenadj reapply interval to 1–120 seconds."""
    return max(REAPPLY_MIN_S, min(REAPPLY_MAX_S, int(value)))
