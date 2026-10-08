use serde_json::{json, Value};

use crate::{HubError, HubResult};

#[derive(Debug, Clone, PartialEq)]
pub enum CpuPowerSample {
    Watts { watts: f64, source: String },
    Sampling { source: String },
    Unavailable { message: String },
}

impl CpuPowerSample {
    pub fn format_line(&self) -> String {
        match self {
            Self::Watts { watts, source } => format!("OK\t{watts:.1}\t{source}\n"),
            Self::Sampling { source } => format!("SAMPLING\t{source}\n"),
            Self::Unavailable { message } => format!("ERR\t{message}\n"),
        }
    }
}

pub fn parse_cpu_power_response(response: &str) -> HubResult<CpuPowerSample> {
    let parts: Vec<&str> = response.trim().splitn(3, '\t').collect();
    let tag = parts.first().copied().unwrap_or("");
    match tag {
        "OK" => {
            let watts = parts.get(1).ok_or_else(|| HubError::new("missing wattage"))?;
            let watts = watts.parse::<f64>().map_err(|_| HubError::new("invalid wattage"))?;
            let source = parts.get(2).copied().unwrap_or("victus-hubd RAPL").to_owned();
            Ok(CpuPowerSample::Watts { watts, source })
        }
        "SAMPLING" => Ok(CpuPowerSample::Sampling {
            source: parts.get(1).copied().unwrap_or("victus-hubd RAPL").to_owned(),
        }),
        "ERR" => Err(HubError::new(parts.get(1).copied().unwrap_or("daemon error"))),
        "" => Err(HubError::new("empty response")),
        other => Err(HubError::new(format!("unexpected response: {other}"))),
    }
}

pub fn format_status(ok: bool, message: &str) -> String {
    if ok { format!("OK\t{message}\n") } else { format!("ERR\t{message}\n") }
}

pub fn parse_status_response(response: &str) -> HubResult<String> {
    let line = response.trim();
    let mut parts = line.splitn(2, '\t');
    let tag = parts.next().unwrap_or("");
    let body = parts.next();
    match tag {
        "OK" => Ok(body.unwrap_or("ok").to_owned()),
        "ERR" => Err(HubError::new(body.unwrap_or("daemon error"))),
        "" => Err(HubError::new("empty response")),
        other => Err(HubError::new(format!("unexpected response: {other}"))),
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SensorReading {
    pub value: String,
    pub source: String,
}

impl SensorReading {
    pub fn new(value: impl Into<String>) -> Self {
        Self { value: value.into(), source: String::new() }
    }

    pub fn with_source(value: impl Into<String>, source: impl Into<String>) -> Self {
        Self { value: value.into(), source: source.into() }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ExtraSensor {
    pub key: String,
    pub group: String,
    pub name: String,
    pub unit: String,
    pub value_min: f64,
    pub value_max: f64,
    pub numeric_value: f64,
    pub reading: SensorReading,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SensorSnapshot {
    pub cpu_fan: SensorReading,
    pub gpu_fan: SensorReading,
    pub cpu_temp: SensorReading,
    pub cpu_temp_c: Option<f64>,
    pub cpu_usage: SensorReading,
    pub cpu_usage_pct: Option<f64>,
    pub gpu_temp: SensorReading,
    pub gpu_temp_c: Option<f64>,
    pub gpu_usage: SensorReading,
    pub gpu_usage_pct: Option<f64>,
    pub cpu_power: SensorReading,
    pub gpu_power: SensorReading,
    pub pwm_mode: SensorReading,
    pub pwm_value: SensorReading,
    pub ram_usage: SensorReading,
    pub ram_usage_pct: Option<f64>,
    pub ram_used_gb: Option<f64>,
    pub ram_total_gb: Option<f64>,
    pub extra_sensors: Vec<ExtraSensor>,
}

impl Default for SensorSnapshot {
    fn default() -> Self {
        Self {
            cpu_fan: SensorReading::new("0 RPM"),
            gpu_fan: SensorReading::new("0 RPM"),
            cpu_temp: SensorReading::new("0 C"),
            cpu_temp_c: None,
            cpu_usage: SensorReading::new("0 %"),
            cpu_usage_pct: None,
            gpu_temp: SensorReading::new("0 C"),
            gpu_temp_c: None,
            gpu_usage: SensorReading::new("0 %"),
            gpu_usage_pct: None,
            cpu_power: SensorReading::new("0 W"),
            gpu_power: SensorReading::new("0 W"),
            pwm_mode: SensorReading::new("Automatic"),
            pwm_value: SensorReading::new("0 / 255"),
            ram_usage: SensorReading::new("0 GB"),
            ram_usage_pct: None,
            ram_used_gb: None,
            ram_total_gb: None,
            extra_sensors: Vec::new(),
        }
    }
}

fn reading_json(reading: &SensorReading) -> Value {
    json!([reading.value, reading.source])
}

fn reading_from_json(value: Option<&Value>, default: &SensorReading) -> SensorReading {
    let Some(Value::Array(items)) = value else {
        return default.clone();
    };
    if items.is_empty() {
        return default.clone();
    }
    let text = items[0].as_str().unwrap_or(default.value.as_str()).to_owned();
    let source = items.get(1).and_then(Value::as_str).unwrap_or("").to_owned();
    SensorReading { value: text, source }
}

fn optional_float(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(number)) => number.as_f64(),
        _ => None,
    }
}

pub fn snapshot_to_value(snap: &SensorSnapshot) -> Value {
    let mut payload = serde_json::Map::new();
    let readings = [
        ("cpu_fan", &snap.cpu_fan),
        ("gpu_fan", &snap.gpu_fan),
        ("cpu_temp", &snap.cpu_temp),
        ("cpu_usage", &snap.cpu_usage),
        ("gpu_temp", &snap.gpu_temp),
        ("gpu_usage", &snap.gpu_usage),
        ("cpu_power", &snap.cpu_power),
        ("gpu_power", &snap.gpu_power),
        ("pwm_mode", &snap.pwm_mode),
        ("pwm_value", &snap.pwm_value),
        ("ram_usage", &snap.ram_usage),
    ];
    // Field order matches the Python payload, including numbers between readings.
    payload.insert("cpu_fan".into(), reading_json(readings[0].1));
    payload.insert("gpu_fan".into(), reading_json(readings[1].1));
    payload.insert("cpu_temp".into(), reading_json(readings[2].1));
    payload.insert("cpu_temp_c".into(), json!(snap.cpu_temp_c));
    payload.insert("cpu_usage".into(), reading_json(readings[3].1));
    payload.insert("cpu_usage_pct".into(), json!(snap.cpu_usage_pct));
    payload.insert("gpu_temp".into(), reading_json(readings[4].1));
    payload.insert("gpu_temp_c".into(), json!(snap.gpu_temp_c));
    payload.insert("gpu_usage".into(), reading_json(readings[5].1));
    payload.insert("gpu_usage_pct".into(), json!(snap.gpu_usage_pct));
    payload.insert("cpu_power".into(), reading_json(readings[6].1));
    payload.insert("gpu_power".into(), reading_json(readings[7].1));
    payload.insert("pwm_mode".into(), reading_json(readings[8].1));
    payload.insert("pwm_value".into(), reading_json(readings[9].1));
    payload.insert("ram_usage".into(), reading_json(readings[10].1));
    payload.insert("ram_usage_pct".into(), json!(snap.ram_usage_pct));
    payload.insert("ram_used_gb".into(), json!(snap.ram_used_gb));
    payload.insert("ram_total_gb".into(), json!(snap.ram_total_gb));
    let extras = snap
        .extra_sensors
        .iter()
        .map(|item| {
            json!({
                "key": item.key,
                "group": item.group,
                "name": item.name,
                "unit": item.unit,
                "value_min": item.value_min,
                "value_max": item.value_max,
                "numeric_value": item.numeric_value,
                "reading": reading_json(&item.reading),
            })
        })
        .collect();
    payload.insert("extra_sensors".into(), Value::Array(extras));
    Value::Object(payload)
}

pub fn snapshot_from_value(data: &Value) -> HubResult<SensorSnapshot> {
    let data = data.as_object().ok_or_else(|| HubError::new("sensor snapshot must be an object"))?;
    let blank = SensorSnapshot::default();
    let reading = |name: &str, default: &SensorReading| reading_from_json(data.get(name), default);
    let mut extras = Vec::new();
    if let Some(Value::Array(items)) = data.get("extra_sensors") {
        for item in items {
            let Some(object) = item.as_object() else { continue };
            let Some(key) = object.get("key").and_then(Value::as_str) else { continue };
            extras.push(ExtraSensor {
                key: key.to_owned(),
                group: object.get("group").and_then(Value::as_str).unwrap_or("").to_owned(),
                name: object.get("name").and_then(Value::as_str).unwrap_or(key).to_owned(),
                unit: object.get("unit").and_then(Value::as_str).unwrap_or("").to_owned(),
                value_min: object.get("value_min").and_then(Value::as_f64).unwrap_or(0.0),
                value_max: object.get("value_max").and_then(Value::as_f64).unwrap_or(0.0),
                numeric_value: object.get("numeric_value").and_then(Value::as_f64).unwrap_or(0.0),
                reading: reading_from_json(object.get("reading"), &SensorReading::new("Unavailable")),
            });
        }
    }
    Ok(SensorSnapshot {
        cpu_fan: reading("cpu_fan", &blank.cpu_fan),
        gpu_fan: reading("gpu_fan", &blank.gpu_fan),
        cpu_temp: reading("cpu_temp", &blank.cpu_temp),
        cpu_temp_c: optional_float(data.get("cpu_temp_c")),
        cpu_usage: reading("cpu_usage", &blank.cpu_usage),
        cpu_usage_pct: optional_float(data.get("cpu_usage_pct")),
        gpu_temp: reading("gpu_temp", &blank.gpu_temp),
        gpu_temp_c: optional_float(data.get("gpu_temp_c")),
        gpu_usage: reading("gpu_usage", &blank.gpu_usage),
        gpu_usage_pct: optional_float(data.get("gpu_usage_pct")),
        cpu_power: reading("cpu_power", &blank.cpu_power),
        gpu_power: reading("gpu_power", &blank.gpu_power),
        pwm_mode: reading("pwm_mode", &blank.pwm_mode),
        pwm_value: reading("pwm_value", &blank.pwm_value),
        ram_usage: reading("ram_usage", &blank.ram_usage),
        ram_usage_pct: optional_float(data.get("ram_usage_pct")),
        ram_used_gb: optional_float(data.get("ram_used_gb")),
        ram_total_gb: optional_float(data.get("ram_total_gb")),
        extra_sensors: extras,
    })
}

pub fn format_sensors_response(snap: &SensorSnapshot) -> String {
    format!("OK\t{}\n", snapshot_to_value(snap))
}

pub fn parse_sensors_response(response: &str) -> HubResult<SensorSnapshot> {
    let mut parts = response.trim().splitn(2, '\t');
    let tag = parts.next().unwrap_or("");
    let body = parts.next().unwrap_or("");
    if tag == "ERR" {
        return Err(HubError::new(if body.is_empty() { "daemon error" } else { body }));
    }
    if tag != "OK" || body.is_empty() {
        return Err(HubError::new("missing sensor snapshot"));
    }
    let data: Value = serde_json::from_str(body).map_err(|_| HubError::new("invalid sensor snapshot"))?;
    snapshot_from_value(&data)
}

/// Split a daemon request into the matched command prefix and its body.
pub fn match_request(request: &str) -> Option<(&'static str, &str)> {
    const TABLE: &[&str] = &[
        "cpu-power",
        "gpu-mux-mode\t",
        "fan-config\t",
        "fan-auto",
        "fan-max",
        "fan-pwm\t",
        "fan-manual",
        "lighting-config\t",
        "keyboard-color\t",
        "power-config\t",
        "power-limits\t",
        "intel-power-limits\t",
        "intel-undervolt\t",
        "cpu-frequency-config",
        "cpu-frequency-limits\t",
        "battery-power-save\t",
        "hardware-shortcuts\t",
        "program-shortcut\t",
        "disable-nvidia-queries\t",
        "set-profile\t",
        "sensors",
        "get-state",
        "prepare-sleep",
        "resume",
        "keyboard-brightness\t",
        "keyboard-user-brightness\t",
        "keyboard-last-input",
    ];
    for prefix in TABLE {
        let matched = (prefix.ends_with('\t') && request.starts_with(prefix))
            || request == *prefix
            || (matches!(*prefix, "cpu-frequency-config" | "sensors") && request.starts_with(&format!("{prefix}\t")));
        if matched {
            return Some((*prefix, &request[prefix.len()..]));
        }
    }
    None
}

pub fn parse_ints(body: &str, count: usize, expected: &str) -> HubResult<Vec<i32>> {
    let parts: Vec<&str> = if body.is_empty() { Vec::new() } else { body.split('\t').collect() };
    if parts.len() != count {
        return Err(HubError::new(expected));
    }
    parts
        .iter()
        .map(|part| part.parse::<i32>().map_err(|_| HubError::new(expected)))
        .collect()
}

pub fn parse_json_object(body: &str, name: &str) -> HubResult<Value> {
    let value: Value = serde_json::from_str(body.trim_start_matches('\t').trim())
        .map_err(|_| HubError::new(format!("{name} must be a JSON object")))?;
    if value.is_object() {
        Ok(value)
    } else {
        Err(HubError::new(format!("{name} must be a JSON object")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_and_power_lines_round_trip() {
        assert_eq!(format_status(true, "fan-config"), "OK\tfan-config\n");
        assert_eq!(format_status(false, "nope"), "ERR\tnope\n");
        assert_eq!(parse_status_response("OK\tok\n").unwrap(), "ok");
        assert!(parse_status_response("ERR\tdenied\n").is_err());
        let line = CpuPowerSample::Watts { watts: 12.5, source: "victus-hubd RAPL".into() }.format_line();
        assert_eq!(line, "OK\t12.5\tvictus-hubd RAPL\n");
        match parse_cpu_power_response("SAMPLING\tvictus-hubd RAPL\n").unwrap() {
            CpuPowerSample::Sampling { source } => assert_eq!(source, "victus-hubd RAPL"),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn sensor_snapshot_round_trips_and_commands_match() {
        let mut snap = SensorSnapshot::default();
        snap.cpu_temp = SensorReading::with_source("48.0 C", "k10temp");
        snap.cpu_temp_c = Some(48.0);
        let parsed = parse_sensors_response(&format_sensors_response(&snap)).unwrap();
        assert_eq!(parsed.cpu_temp.value, "48.0 C");
        assert_eq!(parsed.cpu_temp_c, Some(48.0));
        assert_eq!(match_request("sensors\tcpu-temp,gpu-temp").unwrap().0, "sensors");
        assert_eq!(match_request("fan-auto").unwrap().0, "fan-auto");
        assert!(match_request("nope").is_none());
        assert_eq!(match_request("keyboard-user-brightness\t128").unwrap().0, "keyboard-user-brightness\t");
    }
}
