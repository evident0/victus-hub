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


def _reading_json(reading: SensorReading) -> list[str]:
    return [reading.value, reading.source]


def _reading_from_json(value, default: SensorReading) -> SensorReading:
    if not isinstance(value, list) or len(value) < 1:
        return default
    source = value[1] if len(value) > 1 and isinstance(value[1], str) else ""
    text = value[0] if isinstance(value[0], str) else default.value
    return SensorReading(text, source)


def snapshot_to_payload(snap: SensorSnapshot) -> dict:
    return {
        "cpu_fan": _reading_json(snap.cpu_fan),
        "gpu_fan": _reading_json(snap.gpu_fan),
        "cpu_temp": _reading_json(snap.cpu_temp),
        "cpu_temp_c": snap.cpu_temp_c,
        "cpu_usage": _reading_json(snap.cpu_usage),
        "cpu_usage_pct": snap.cpu_usage_pct,
        "gpu_temp": _reading_json(snap.gpu_temp),
        "gpu_temp_c": snap.gpu_temp_c,
        "gpu_usage": _reading_json(snap.gpu_usage),
        "gpu_usage_pct": snap.gpu_usage_pct,
        "cpu_power": _reading_json(snap.cpu_power),
        "gpu_power": _reading_json(snap.gpu_power),
        "pwm_mode": _reading_json(snap.pwm_mode),
        "pwm_value": _reading_json(snap.pwm_value),
        "ram_usage": _reading_json(snap.ram_usage),
        "ram_usage_pct": snap.ram_usage_pct,
        "ram_used_gb": snap.ram_used_gb,
        "ram_total_gb": snap.ram_total_gb,
        "extra_sensors": [
            {
                "key": item.key,
                "group": item.group,
                "name": item.name,
                "unit": item.unit,
                "value_min": item.value_min,
                "value_max": item.value_max,
                "numeric_value": item.numeric_value,
                "reading": _reading_json(item.reading),
            }
            for item in snap.extra_sensors
        ],
    }


def snapshot_from_payload(data: dict) -> SensorSnapshot:
    blank = SensorSnapshot()
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

    def num(name: str):
        value = data.get(name)
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    return SensorSnapshot(
        cpu_fan=_reading_from_json(data.get("cpu_fan"), blank.cpu_fan),
        gpu_fan=_reading_from_json(data.get("gpu_fan"), blank.gpu_fan),
        cpu_temp=_reading_from_json(data.get("cpu_temp"), blank.cpu_temp),
        cpu_temp_c=num("cpu_temp_c"),
        cpu_usage=_reading_from_json(data.get("cpu_usage"), blank.cpu_usage),
        cpu_usage_pct=num("cpu_usage_pct"),
        gpu_temp=_reading_from_json(data.get("gpu_temp"), blank.gpu_temp),
        gpu_temp_c=num("gpu_temp_c"),
        gpu_usage=_reading_from_json(data.get("gpu_usage"), blank.gpu_usage),
        gpu_usage_pct=num("gpu_usage_pct"),
        cpu_power=_reading_from_json(data.get("cpu_power"), blank.cpu_power),
        gpu_power=_reading_from_json(data.get("gpu_power"), blank.gpu_power),
        pwm_mode=_reading_from_json(data.get("pwm_mode"), blank.pwm_mode),
        pwm_value=_reading_from_json(data.get("pwm_value"), blank.pwm_value),
        ram_usage=_reading_from_json(data.get("ram_usage"), blank.ram_usage),
        ram_usage_pct=num("ram_usage_pct"),
        ram_used_gb=num("ram_used_gb"),
        ram_total_gb=num("ram_total_gb"),
        extra_sensors=extras,
    )


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
