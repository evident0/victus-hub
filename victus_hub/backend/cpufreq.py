"""Read Linux CPU frequency policies (all frequencies are in kHz)."""

from dataclasses import dataclass
from pathlib import Path

CPUFREQ_ROOT = Path("/sys/devices/system/cpu/cpufreq")


@dataclass(frozen=True)
class FrequencyPolicy:
    path: Path
    hardware_min: int
    hardware_max: int
    minimum: int
    maximum: int


def read_frequency_policies(root: Path = CPUFREQ_ROOT) -> list[FrequencyPolicy]:
    policies = []
    for path in sorted(root.glob("policy[0-9]*")):
        try:
            values = [int((path / name).read_text().strip()) for name in (
                "cpuinfo_min_freq", "cpuinfo_max_freq",
                "scaling_min_freq", "scaling_max_freq",
            )]
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Cannot read CPU frequency policy {path.name}: {exc}") from exc
        hwmin, hwmax, minimum, maximum = values
        # AMD exposes the full boost ceiling separately; cpuinfo_max_freq
        # can shrink when boost is disabled by a power profile.
        try:
            amd_max = int((path / "amd_pstate_max_freq").read_text().strip())
        except (OSError, ValueError):
            amd_max = 0
        hwmax = max(hwmax, amd_max)
        if not (0 < hwmin <= minimum <= maximum <= hwmax):
            raise RuntimeError(f"Invalid CPU frequency limits in {path.name}")
        policies.append(FrequencyPolicy(path, hwmin, hwmax, minimum, maximum))
    if not policies:
        raise RuntimeError("CPU frequency control is unavailable on this system")
    return policies
