"""Power limits settings — ports power-limits.ts."""

from dataclasses import dataclass

from PySide6.QtCore import QSettings

POWER_MIN_MW = 15_000
POWER_MAX_MW = 55_000
POWER_STEP_MW = 1_000
DEFAULT_POWER_LIMIT_MW = 25_000
DEFAULT_REAPPLY_SECONDS = 5

TCTL_TEMP_MIN_C = 75
TCTL_TEMP_MAX_C = 95
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


def read_power_enabled() -> bool:
    settings = QSettings()
    return settings.value("powerLimits/enabled", False, type=bool)


def write_power_enabled(enabled: bool):
    settings = QSettings()
    settings.setValue("powerLimits/enabled", enabled)


def read_power_limit_settings() -> PowerLimitSettings:
    settings = QSettings()
    return PowerLimitSettings(
        stapm_limit=clamp_power_limit(
            int(settings.value("powerLimits/stapm", DEFAULT_POWER_LIMIT_MW))),
        fast_limit=clamp_power_limit(
            int(settings.value("powerLimits/fast", DEFAULT_POWER_LIMIT_MW))),
        slow_limit=clamp_power_limit(
            int(settings.value("powerLimits/slow", DEFAULT_POWER_LIMIT_MW))),
        tctl_temp=clamp_tctl_temp(
            int(settings.value("powerLimits/tctlTemp", DEFAULT_TCTL_TEMP_C))),
        reapply_seconds=max(1, int(settings.value(
            "powerLimits/reapplySeconds", DEFAULT_REAPPLY_SECONDS))),
    )


def write_power_limit_settings(s: PowerLimitSettings):
    settings = QSettings()
    settings.setValue("powerLimits/stapm", clamp_power_limit(s.stapm_limit))
    settings.setValue("powerLimits/fast", clamp_power_limit(s.fast_limit))
    settings.setValue("powerLimits/slow", clamp_power_limit(s.slow_limit))
    settings.setValue("powerLimits/tctlTemp", clamp_tctl_temp(s.tctl_temp))
    settings.setValue("powerLimits/reapplySeconds", max(1, s.reapply_seconds))
