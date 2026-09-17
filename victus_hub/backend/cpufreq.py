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
        if not (0 < hwmin <= minimum <= maximum <= hwmax):
            raise RuntimeError(f"Invalid CPU frequency limits in {path.name}")
        policies.append(FrequencyPolicy(path, *values))
    if not policies:
        raise RuntimeError("CPU frequency control is unavailable on this system")
    return policies
