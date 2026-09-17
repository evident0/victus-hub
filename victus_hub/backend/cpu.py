"""CPU vendor detection shared by the UI and privileged daemon."""

from pathlib import Path


def cpu_vendor() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "vendor_id":
                return value.strip()
    except OSError:
        pass
    return ""


def is_intel_cpu() -> bool:
    return cpu_vendor() == "GenuineIntel"
