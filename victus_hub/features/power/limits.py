"""Power limits settings — ports power-limits.ts."""

from PySide6.QtCore import QSettings

from victus_hub.backend.cpu import is_intel_cpu
from victus_hub.backend.power_limits import (  # noqa: F401
    DEFAULT_POWER_LIMIT_MW,
    DEFAULT_REAPPLY_SECONDS,
    DEFAULT_TCTL_TEMP_C,
    POWER_MAX_MW,
    POWER_MIN_MW,
    POWER_STEP_MW,
    REAPPLY_MAX_S,
    REAPPLY_MIN_S,
    TCTL_TEMP_MAX_C,
    TCTL_TEMP_MIN_C,
    PowerLimitSettings,
    clamp_power_limit,
    clamp_reapply_seconds,
    clamp_tctl_temp,
)


def read_frequency_limits() -> tuple[int, int] | None:
    value = QSettings().value("cpuFrequency/limits")
    try:
        minimum, maximum = map(int, value)
    except (TypeError, ValueError):
        return None
    return (minimum, maximum) if 0 < minimum <= maximum else None


def write_frequency_limits(minimum: int, maximum: int):
    QSettings().setValue("cpuFrequency/limits", [minimum, maximum])


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
