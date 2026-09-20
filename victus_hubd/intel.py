"""Intel package power limits (RAPL) and core/cache voltage offsets (OC mailbox)."""

import os
import struct
import threading
import time
from pathlib import Path

from victus_hub.backend.cpu import is_intel_cpu

POWERCAP_ROOT = Path("/sys/class/powercap")
MSR_PATH = Path("/dev/cpu/0/msr")
OC_MAILBOX = 0x150
_lock = threading.Lock()


def _require_intel() -> None:
    if not is_intel_cpu():
        raise RuntimeError("Intel CPU controls are unavailable on this processor")


def apply_power_limits(pl1_mw: int, pl2_mw: int, root: Path = POWERCAP_ROOT) -> str:
    """Apply long/short term limits to every Intel package, preserving time windows."""
    _require_intel()
    if not 15_000 <= pl1_mw <= pl2_mw <= 120_000:
        raise RuntimeError("Intel power limits must satisfy 15 W <= PL1 <= PL2 <= 120 W")
    with _lock:
        writes = []
        try:
            for package in sorted(root.glob("intel-rapl:*")):
                # Subdomains (core/uncore/DRAM) must never receive package limits.
                if package.name.count(":") != 1:
                    continue
                if not (package / "name").read_text().strip().startswith("package-"):
                    continue
                constraints = {
                    name.read_text().strip(): name.name.removesuffix("_name")
                    for name in package.glob("constraint_*_name")
                }
                for label, value in (("long_term", pl1_mw), ("short_term", pl2_mw)):
                    prefix = constraints.get(label)
                    if prefix is None:
                        raise RuntimeError(f"{package.name} does not expose {label} power limits")
                    target = package / f"{prefix}_power_limit_uw"
                    # Read every target and validate hardware bounds before writing.
                    int(target.read_text())
                    for bound, below in (("min", True), ("max", False)):
                        path = package / f"{prefix}_{bound}_power_uw"
                        if path.exists():
                            limit = int(path.read_text())
                            if limit > 0 and ((value * 1000 < limit) if below else (value * 1000 > limit)):
                                raise RuntimeError(f"{label} power exceeds {package.name} hardware range")
                    writes.append((target, value * 1000))
                enabled = package / "enabled"
                if enabled.exists() and enabled.read_text().strip() != "1":
                    writes.append((enabled, 1))
            if not writes:
                raise RuntimeError("Intel RAPL package power limits are unavailable")
            for path, value in writes:
                path.write_text(str(value))
                accepted = int(path.read_text())
                # RAPL power units may quantize the requested value (up to 1/8 W).
                tolerance = 125_000 if path.name.endswith("_uw") else 0
                if abs(accepted - value) > tolerance:
                    raise RuntimeError(f"{path.parent.name} rejected the requested power limit")
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Could not apply Intel power limits: {exc}. Some limits may have changed.") from exc
    return "Intel PL1/PL2 power limits applied"


def _mailbox(fd: int, domain: int, offset: int | None = None) -> int:
    command = 0x8000001000000000 | (domain << 40)
    if offset is not None:
        command |= 0x100000000 | ((offset & 0x7FF) << 21)
    if os.pwrite(fd, struct.pack("<Q", command), OC_MAILBOX) != 8:
        raise RuntimeError("Short write to Intel voltage mailbox")
    for _ in range(20):
        data = os.pread(fd, 8, OC_MAILBOX)
        if len(data) != 8:
            raise RuntimeError("Short read from Intel voltage mailbox")
        response = struct.unpack("<Q", data)[0]
        if not response & (1 << 63):
            break
        time.sleep(0.001)
    else:
        raise RuntimeError("Intel voltage mailbox timed out")
    if (response >> 32) & 0xFF:
        raise RuntimeError("Intel undervolting is unsupported or locked by firmware")
    raw = (response >> 21) & 0x7FF
    return raw - 0x800 if raw & 0x400 else raw


def apply_undervolt(core_mv: int, cache_mv: int, path: Path = MSR_PATH) -> str:
    """Write only non-positive offsets; verify firmware accepted both domains."""
    _require_intel()
    if not all(-250 <= value <= 0 for value in (core_mv, cache_mv)):
        raise RuntimeError("Intel undervolt offsets must be between -250 and 0 mV")
    with _lock:
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            raise RuntimeError(
                f"Intel undervolting requires writable MSR access (msr kernel module): {exc}"
            ) from exc
        try:
            # Probe both domains before changing either. 0 = core, 2 = cache.
            for domain in (0, 2):
                _mailbox(fd, domain)
            for domain, mv in ((0, core_mv), (2, cache_mv)):
                encoded = round(mv * 1.024)
                _mailbox(fd, domain, encoded)
                if _mailbox(fd, domain) != encoded:
                    raise RuntimeError(
                        "Intel undervolt was not accepted; firmware may lock voltage control. "
                        "Some offsets may have changed."
                    )
        except OSError as exc:
            raise RuntimeError(f"Could not apply Intel undervolt: {exc}. Some offsets may have changed.") from exc
        finally:
            os.close(fd)
    return f"Intel undervolt applied: core {core_mv} mV, cache {cache_mv} mV"
