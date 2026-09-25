"""Wire protocol shared by the daemon client and the daemon server.

Ports privileged/protocol.rs exactly.
"""

import json

from victus_hub.backend.rapl import CpuPowerSample
from victus_hub.backend.types import ExtraSensor, SensorReading, SensorSnapshot


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
        source = parts[2] if len(parts) > 2 else "victus-hubd RAPL"
        return CpuPowerSample(kind="watts", watts=watts, source=source)
    elif tag == "SAMPLING":
        source = parts[1] if len(parts) > 1 else "victus-hubd RAPL"
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


# Profile is filled in by the GUI after the reply. cpu_max_temp is not requested.
_SNAPSHOT_FIELDS = (
    ("cpu_fan", "reading"),
    ("gpu_fan", "reading"),
    ("cpu_temp", "reading"),
    ("cpu_temp_c", "number"),
    ("cpu_usage", "reading"),
    ("cpu_usage_pct", "number"),
    ("gpu_temp", "reading"),
    ("gpu_temp_c", "number"),
    ("gpu_usage", "reading"),
    ("gpu_usage_pct", "number"),
    ("cpu_power", "reading"),
    ("gpu_power", "reading"),
    ("pwm_mode", "reading"),
    ("pwm_value", "reading"),
    ("ram_usage", "reading"),
    ("ram_usage_pct", "number"),
    ("ram_used_gb", "number"),
    ("ram_total_gb", "number"),
)
_EXTRA_FIELDS = ("key", "group", "name", "unit", "value_min", "value_max", "numeric_value")


def _reading_json(reading: SensorReading) -> list[str]:
    return [reading.value, reading.source]


def _reading_from_json(value, default: SensorReading) -> SensorReading:
    if not isinstance(value, list) or len(value) < 1:
        return default
    source = value[1] if len(value) > 1 and isinstance(value[1], str) else ""
    text = value[0] if isinstance(value[0], str) else default.value
    return SensorReading(text, source)


def _optional_float(value):
    if isinstance(value, bool) or value is None or not isinstance(value, (int, float)):
        return None
    return float(value)


def snapshot_to_payload(snap: SensorSnapshot) -> dict:
    payload = {}
    for name, kind in _SNAPSHOT_FIELDS:
        value = getattr(snap, name)
        payload[name] = _reading_json(value) if kind == "reading" else value
    payload["extra_sensors"] = [
        {
            **{name: getattr(item, name) for name in _EXTRA_FIELDS},
            "reading": _reading_json(item.reading),
        }
        for item in snap.extra_sensors
    ]
    return payload


def snapshot_from_payload(data: dict) -> SensorSnapshot:
    blank = SensorSnapshot()
    fields = {}
    for name, kind in _SNAPSHOT_FIELDS:
        if kind == "reading":
            fields[name] = _reading_from_json(data.get(name), getattr(blank, name))
        else:
            fields[name] = _optional_float(data.get(name))
    extras = []
    for item in data.get("extra_sensors") or []:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        extras.append(ExtraSensor(
            key=item["key"],
            group=str(item.get("group") or ""),
            name=str(item.get("name") or item["key"]),
            unit=str(item.get("unit") or ""),
            value_min=float(item.get("value_min") or 0),
            value_max=float(item.get("value_max") or 0),
            numeric_value=float(item.get("numeric_value") or 0),
            reading=_reading_from_json(item.get("reading"), SensorReading("Unavailable")),
        ))
    fields["extra_sensors"] = extras
    return SensorSnapshot(**fields)


def format_sensors_response(snap: SensorSnapshot) -> str:
    payload = json.dumps(snapshot_to_payload(snap), separators=(",", ":"))
    return f"OK\t{payload}\n"


def parse_sensors_response(response: str) -> SensorSnapshot:
    parts = response.strip().split("\t", 1)
    tag = parts[0] if parts else ""
    if tag == "ERR":
        message = parts[1] if len(parts) > 1 else "daemon error"
        raise RuntimeError(message)
    if tag != "OK" or len(parts) < 2 or not parts[1]:
        raise RuntimeError("missing sensor snapshot")
    try:
        data = json.loads(parts[1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid sensor snapshot") from exc
    if not isinstance(data, dict):
        raise RuntimeError("sensor snapshot must be an object")
    return snapshot_from_payload(data)


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
