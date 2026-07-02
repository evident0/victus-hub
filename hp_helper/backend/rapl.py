"""RAPL CPU-power sampler — shared by the unprivileged app and the root daemon.

Ports privileged/rapl.rs exactly.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CpuPowerSample:
    """Tagged union for CPU power samples.

    kind == "watts"       -> watts: float, source: str
    kind == "sampling"    -> source: str
    kind == "unavailable" -> message: str
    """
    kind: str
    watts: float = 0.0
    source: str = ""
    message: str = ""
    reason: str = ""


def _read_energy(path: Path) -> tuple[int | None, str]:
    """Read a RAPL energy file. Returns (value, reason).

    `reason` is "permission_denied" for EACCES/EPERM, empty otherwise.
    """
    try:
        return int(path.read_text().strip()), ""
    except PermissionError:
        return None, "permission_denied"
    except (OSError, ValueError):
        return None, ""


def _rapl_package() -> Path | None:
    powercap = Path("/sys/class/powercap")
    entries = []
    try:
        for entry in sorted(powercap.iterdir()):
            name = entry.name
            if name.startswith("intel-rapl:") and name.count(":") == 1:
                entries.append(entry)
    except OSError:
        return None
    return entries[0] if entries else None



class RaplPowerSampler:
    """Reads CPU power via RAPL energy counters. Must be called periodically."""

    def __init__(self):
        self._sample: tuple[Path, int, int | None, float] | None = None

    def read(self) -> CpuPowerSample:
        package = _rapl_package()
        if package is None:
            return CpuPowerSample(kind="unavailable", message="no RAPL package")

        energy_path = package / "energy_uj"
        energy, reason = _read_energy(energy_path)
        if energy is None:
            return CpuPowerSample(
                kind="unavailable",
                message=str(energy_path),
                reason=reason,
            )

        max_range, _ = _read_energy(package / "max_energy_range_uj")

        now = time.monotonic()
        source = str(energy_path)

        previous = self._sample
        self._sample = (package, energy, max_range, now)

        if previous is None:
            return CpuPowerSample(kind="sampling", source=source)

        old_package, old_energy, old_max_range, old_time = previous
        if old_package != package:
            return CpuPowerSample(kind="sampling", source=source)

        elapsed = now - old_time
        delta = energy - old_energy
        wrap = max_range if max_range is not None else old_max_range
        if delta < 0 and wrap is not None:
            delta += wrap

        if elapsed <= 0.0 or delta < 0:
            return CpuPowerSample(kind="unavailable", message=source)

        watts = delta / 1_000_000.0 / elapsed
        return CpuPowerSample(kind="watts", watts=watts, source=source)
