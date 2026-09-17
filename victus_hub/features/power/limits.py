"""Power limits settings — ports power-limits.ts."""

from dataclasses import dataclass

from PySide6.QtCore import QSettings

from victus_hub.backend.cpu import is_intel_cpu

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


def read_frequency_limits() -> tuple[int, int] | None:
    value = QSettings().value("cpuFrequency/limits")
    try:
        minimum, maximum = map(int, value)
    except (TypeError, ValueError):
        return None
    return (minimum, maximum) if 0 < minimum <= maximum else None


def write_frequency_limits(minimum: int, maximum: int):
    QSettings().setValue("cpuFrequency/limits", [minimum, maximum])


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


def read_power_enabled() -> bool:
    settings = QSettings()
    group = "intelPowerLimits" if is_intel_cpu() else "powerLimits"
    return settings.value(f"{group}/enabled", False, type=bool)


def write_power_enabled(enabled: bool):
    settings = QSettings()
    group = "intelPowerLimits" if is_intel_cpu() else "powerLimits"
    settings.setValue(f"{group}/enabled", enabled)


def read_power_limit_settings() -> PowerLimitSettings:
    settings = QSettings()
    if is_intel_cpu():
        pl1 = clamp_power_limit(int(settings.value("intelPowerLimits/pl1", DEFAULT_POWER_LIMIT_MW)))
        pl2 = max(pl1, clamp_power_limit(int(settings.value("intelPowerLimits/pl2", DEFAULT_POWER_LIMIT_MW))))
        # Keep the existing controller interface: slow = PL1, fast = PL2.
        return PowerLimitSettings(
            slow_limit=pl1,
            fast_limit=pl2,
            reapply_seconds=clamp_reapply_seconds(int(settings.value(
                "intelPowerLimits/reapplySeconds", DEFAULT_REAPPLY_SECONDS))),
        )
    return PowerLimitSettings(
        stapm_limit=clamp_power_limit(
            int(settings.value("powerLimits/stapm", DEFAULT_POWER_LIMIT_MW))),
        fast_limit=clamp_power_limit(
            int(settings.value("powerLimits/fast", DEFAULT_POWER_LIMIT_MW))),
        slow_limit=clamp_power_limit(
            int(settings.value("powerLimits/slow", DEFAULT_POWER_LIMIT_MW))),
        tctl_temp=clamp_tctl_temp(
            int(settings.value("powerLimits/tctlTemp", DEFAULT_TCTL_TEMP_C))),
        reapply_seconds=clamp_reapply_seconds(int(settings.value(
            "powerLimits/reapplySeconds", DEFAULT_REAPPLY_SECONDS))),
    )


def write_power_limit_settings(s: PowerLimitSettings):
    settings = QSettings()
    if is_intel_cpu():
        pl1 = clamp_power_limit(s.slow_limit)
        settings.setValue("intelPowerLimits/pl1", pl1)
        settings.setValue("intelPowerLimits/pl2", max(pl1, clamp_power_limit(s.fast_limit)))
        settings.setValue("intelPowerLimits/reapplySeconds", clamp_reapply_seconds(s.reapply_seconds))
        return
    settings.setValue("powerLimits/stapm", clamp_power_limit(s.stapm_limit))
    settings.setValue("powerLimits/fast", clamp_power_limit(s.fast_limit))
    settings.setValue("powerLimits/slow", clamp_power_limit(s.slow_limit))
    settings.setValue("powerLimits/tctlTemp", clamp_tctl_temp(s.tctl_temp))
    settings.setValue("powerLimits/reapplySeconds", clamp_reapply_seconds(s.reapply_seconds))


def read_intel_undervolt() -> tuple[int, int]:
    settings = QSettings()
    values = []
    for domain in ("core", "cache"):
        try:
            value = int(settings.value(f"intelUndervolt/{domain}", 0))
        except (TypeError, ValueError):
            value = 0
        values.append(max(-250, min(0, value)))
    return values[0], values[1]


def write_intel_undervolt(core_mv: int, cache_mv: int) -> None:
    settings = QSettings()
    settings.setValue("intelUndervolt/core", core_mv)
    settings.setValue("intelUndervolt/cache", cache_mv)
