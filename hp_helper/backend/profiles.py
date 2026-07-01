"""System power-profile management; ports profiles.rs exactly."""

from pathlib import Path

from hp_helper.backend.types import SensorReading
from hp_helper.backend.util import command_path, run_command, run_profile_command


PROFILE_KEYS = ["power-saver", "balanced", "performance"]

TUNED_POWER_SAVER = ["powersave", "balanced-battery"]
TUNED_BALANCED = ["balanced", "desktop"]
TUNED_PERFORMANCE = [
    "throughput-performance",
    "accelerator-performance",
    "latency-performance",
]


def tuned_adm_path() -> Path | None:
    cmd = command_path("tuned-adm")
    if cmd:
        return cmd
    alt = Path("/usr/sbin/tuned-adm")
    return alt if alt.exists() else None


def tuned_candidates(profile: int) -> list[str]:
    if profile == 0:
        return list(TUNED_POWER_SAVER)
    elif profile == 2:
        return list(TUNED_PERFORMANCE)
    else:
        return list(TUNED_BALANCED)


def available_tuned_profiles(tuned_adm: Path) -> set[str]:
    output = run_command(tuned_adm, ["list"])
    if output is None:
        return set()
    profiles = set()
    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] == "-" and len(parts) > 1:
            profiles.add(parts[1])
    return profiles


def tuned_profile_for(profile: int, available: set[str]) -> str | None:
    for candidate in tuned_candidates(profile):
        if candidate in available:
            return candidate
    return None


def apply_system_profile(profile: int) -> str:
    profile = max(0, min(profile, 2))
    key = PROFILE_KEYS[profile]

    tuned = tuned_adm_path()
    if tuned is not None:
        available = available_tuned_profiles(tuned)
        tp = tuned_profile_for(profile, available)
        if tp is not None:
            run_profile_command(tuned, ["profile", tp])
            return f"Applied tuned profile: {tp}"

    ppctl = command_path("powerprofilesctl")
    if ppctl is not None:
        run_profile_command(ppctl, ["set", key])
        return f"Applied powerprofilesctl profile: {key}"

    label = ["Power Saver", "Balanced", "Performance"][profile]
    raise RuntimeError(f"no backend profile found for {label}")


def current_ui_profile_index() -> int | None:
    tuned = tuned_adm_path()
    if tuned is not None:
        active = run_command(tuned, ["active"])
        if active is not None:
            prefix = "Current active profile:"
            if active.startswith(prefix):
                profile = active[len(prefix):].strip()
                for i in range(len(PROFILE_KEYS)):
                    if profile in tuned_candidates(i):
                        return i

    ppctl = command_path("powerprofilesctl")
    if ppctl is not None:
        active = run_command(ppctl, ["get"])
        if active is not None:
            for i, key in enumerate(PROFILE_KEYS):
                if key == active:
                    return i
    return None


def current_profile_reading() -> SensorReading:
    """The value for the 'profile' field in SensorSnapshot."""
    tuned = tuned_adm_path()
    if tuned is not None:
        output = run_command(tuned, ["active"])
        if output is not None:
            prefix = "Current active profile:"
            if output.startswith(prefix):
                profile = output[len(prefix):].strip()
                if profile:
                    return SensorReading(value=profile, source="tuned-adm active")

    ppctl = command_path("powerprofilesctl")
    if ppctl is None:
        return SensorReading(
            value="Unavailable",
            source="tuned-adm and powerprofilesctl not found",
        )

    output = run_command(ppctl, ["get"])
    if output is None or not output.strip():
        return SensorReading(value="Unavailable", source="powerprofilesctl get")
    return SensorReading(value=output.strip(), source="powerprofilesctl get")
