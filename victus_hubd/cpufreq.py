"""Privileged CPU frequency limit writes."""

import threading
from pathlib import Path

from victus_hub.backend.cpufreq import CPUFREQ_ROOT, read_frequency_policies

_lock = threading.Lock()


def apply_frequency_limits(minimum: int, maximum: int,
                           root: Path = CPUFREQ_ROOT) -> str:
    if not 0 < minimum <= maximum:
        raise RuntimeError("CPU minimum frequency must be positive and no greater than maximum")
    with _lock:
        policies = read_frequency_policies(root)
        # Validate every policy before changing any of them.
        for policy in policies:
            if minimum < policy.hardware_min or maximum > policy.hardware_max:
                raise RuntimeError(f"CPU frequency limits exceed hardware range for {policy.path.name}")
        for policy in policies:
            writes = [("scaling_min_freq", minimum), ("scaling_max_freq", maximum)]
            # Raise the ceiling first when the new floor exceeds the old ceiling.
            if minimum > policy.maximum:
                writes.reverse()
            try:
                for name, value in writes:
                    (policy.path / name).write_text(str(value))
            except OSError as exc:
                raise RuntimeError(
                    f"Could not set {policy.path.name}: {exc}. Some CPU limits may have changed."
                ) from exc
    return f"CPU frequency limits applied to {len(policies)} policies"
