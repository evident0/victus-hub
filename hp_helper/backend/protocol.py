"""Wire protocol shared by the daemon client and the daemon server.

Ports privileged/protocol.rs exactly.
"""

from hp_helper.backend.rapl import CpuPowerSample


def format_cpu_power_response(sample: CpuPowerSample) -> str:
    """Encode a CpuPowerSample into a wire-protocol line (no trailing newline stripped)."""
    if sample.kind == "watts":
        return f"OK\t{sample.watts:.1f}\t{sample.source}\n"
    elif sample.kind == "sampling":
        return f"SAMPLING\t{sample.source}\n"
    elif sample.kind == "unavailable":
        return f"ERR\t{sample.message}\n"
    else:
        return f"ERR\tunknown sample kind: {sample.kind}\n"


def parse_cpu_power_response(response: str) -> CpuPowerSample:
    """Decode a wire-protocol CPU-power line; raises RuntimeError on error."""
    parts = response.strip().split("\t", 2)
    tag = parts[0] if parts else ""
    if tag == "OK":
        if len(parts) < 2:
            raise RuntimeError("missing wattage")
        try:
            watts = float(parts[1])
        except ValueError:
            raise RuntimeError("invalid wattage")
        source = parts[2] if len(parts) > 2 else "hp-helperd RAPL"
        return CpuPowerSample(kind="watts", watts=watts, source=source)
    elif tag == "SAMPLING":
        source = parts[1] if len(parts) > 1 else "hp-helperd RAPL"
        return CpuPowerSample(kind="sampling", source=source)
    elif tag == "ERR":
        message = parts[1] if len(parts) > 1 else "daemon error"
        raise RuntimeError(message)
    elif tag == "":
        raise RuntimeError("empty response")
    else:
        raise RuntimeError(f"unexpected response: {tag}")


def format_status_response(result: tuple[bool, str]) -> str:
    """Encode a (ok:bool, message:str) status into a wire line.

    Python uses a tuple where Rust used Result<String,String>:
    (True, msg) → "OK\\tmsg\\n"
    (False, msg) → "ERR\\tmsg\\n"
    """
    ok, message = result
    if ok:
        return f"OK\t{message}\n"
    else:
        return f"ERR\t{message}\n"


def parse_status_response(response: str) -> str:
    """Decode a status response; returns message on OK, raises RuntimeError on ERR."""
    line = response.strip()
    parts = line.split("\t", 1)
    tag = parts[0] if parts else ""
    if tag == "OK":
        return parts[1] if len(parts) > 1 else "ok"
    elif tag == "ERR":
        message = parts[1] if len(parts) > 1 else "daemon error"
        raise RuntimeError(message)
    elif tag == "":
        raise RuntimeError("empty response")
    else:
        raise RuntimeError(f"unexpected response: {tag}")
