//! Sensor presentation and merging of page-specific samples.

use victus_core::{SensorReading, SensorSnapshot};

use super::mode_name;

pub(super) fn ram_status(snapshot: &SensorSnapshot) -> String {
    match (snapshot.ram_used_gb, snapshot.ram_total_gb) {
        (Some(used), Some(total)) => format!("RAM {used:.1}/{total:.0} GB"),
        _ => String::new(),
    }
}

pub(super) fn cpu_caption(snapshot: &SensorSnapshot) -> String {
    caption("CPU", snapshot.cpu_usage_pct, &snapshot.cpu_power.value)
}

pub(super) fn gpu_caption(snapshot: &SensorSnapshot) -> String {
    caption("GPU", snapshot.gpu_usage_pct, &snapshot.gpu_power.value)
}

fn caption(name: &str, usage: Option<f64>, power: &str) -> String {
    let usage = usage.map_or_else(|| "—".into(), |value| format!("{value:.0}%"));
    let mut text = format!("{name} · {usage}");
    if !power.is_empty() && power != "0 W" && power != "0" {
        text.push_str(" · ");
        text.push_str(power);
    }
    text
}

pub(super) fn temp_text(value: Option<f64>) -> String {
    value.map_or_else(|| "—".into(), |value| format!("{value:.0}"))
}

pub(super) fn rpm_text(value: &str) -> String {
    value.split_whitespace().next().filter(|token| token.parse::<f64>().is_ok())
        .unwrap_or("—").to_owned()
}

pub(super) fn power_head(snapshot: &SensorSnapshot) -> String {
    let frequency = snapshot.extra_sensors.iter()
        .filter(|extra| extra.key.starts_with("cpu-frequency"))
        .map(|extra| extra.numeric_value)
        .fold(None, |best: Option<f64>, value| Some(best.map_or(value, |best| best.max(value))));
    let frequency = frequency.map_or_else(|| "— MHz".into(), |value| format!("{value:.0} MHz"));
    format!("CPU {} · Freq {frequency}", snapshot.cpu_power.value)
}

#[inline]
fn reading<'a>(snapshot: &'a SensorSnapshot, key: &str) -> Option<&'a SensorReading> {
    Some(match key {
        "cpu-temp" => &snapshot.cpu_temp,
        "cpu-usage" => &snapshot.cpu_usage,
        "cpu-power" => &snapshot.cpu_power,
        "gpu-temp" => &snapshot.gpu_temp,
        "gpu-usage" => &snapshot.gpu_usage,
        "gpu-power" => &snapshot.gpu_power,
        "cpu-fan" => &snapshot.cpu_fan,
        "gpu-fan" => &snapshot.gpu_fan,
        "pwm-value" => &snapshot.pwm_value,
        "pwm-mode" => &snapshot.pwm_mode,
        "ram-usage" => &snapshot.ram_usage,
        "profile" => return None,
        other => &snapshot.extra_sensors.iter().find(|extra| extra.key == other)?.reading,
    })
}

pub(super) fn current_text(snapshot: &SensorSnapshot, key: &str, profile: i32) -> String {
    if key == "profile" { return mode_name(profile).to_owned(); }
    let Some(reading) = reading(snapshot, key) else { return "—".into() };
    if matches!(key, "cpu-temp" | "gpu-temp") {
        reading.value.replace(" C", " °C")
    } else {
        reading.value.clone()
    }
}

pub(super) fn reading_source(snapshot: &SensorSnapshot, key: &str) -> String {
    reading(snapshot, key).map(|reading| reading.source.clone()).unwrap_or_default()
}

pub(super) fn sample_value(snapshot: &SensorSnapshot, key: &str) -> Option<f64> {
    match key {
        "cpu-temp" => snapshot.cpu_temp_c,
        "cpu-usage" => snapshot.cpu_usage_pct,
        "gpu-temp" => snapshot.gpu_temp_c,
        "gpu-usage" => snapshot.gpu_usage_pct,
        "cpu-power" => leading_f64(&snapshot.cpu_power.value),
        "gpu-power" => leading_f64(&snapshot.gpu_power.value),
        "cpu-fan" => leading_f64(&snapshot.cpu_fan.value),
        "gpu-fan" => leading_f64(&snapshot.gpu_fan.value),
        "pwm-value" => leading_f64(&snapshot.pwm_value.value),
        "ram-usage" => snapshot.ram_used_gb,
        "profile" | "pwm-mode" => None,
        other => snapshot.extra_sensors.iter().find(|extra| extra.key == other).map(|extra| extra.numeric_value),
    }
}

fn leading_f64(value: &str) -> Option<f64> {
    value.split_whitespace().next().and_then(|token| token.parse().ok())
}

pub(super) fn format_stat_unit(value: f64, unit: &str) -> String {
    let number = match unit { "RPM" | "PWM" => format!("{value:.0}"), "V" | "A" => format!("{value:.2}"), _ => format!("{value:.1}") };
    if unit.is_empty() { number } else { format!("{number} {unit}") }
}

pub(super) fn merge_snapshot(target: &mut SensorSnapshot, incoming: &SensorSnapshot, keys: &[String]) {
    for key in keys {
        match key.as_str() {
            "cpu-temp" => { target.cpu_temp = incoming.cpu_temp.clone(); target.cpu_temp_c = incoming.cpu_temp_c; }
            "cpu-usage" => { target.cpu_usage = incoming.cpu_usage.clone(); target.cpu_usage_pct = incoming.cpu_usage_pct; }
            "gpu-temp" => { target.gpu_temp = incoming.gpu_temp.clone(); target.gpu_temp_c = incoming.gpu_temp_c; }
            "gpu-usage" => { target.gpu_usage = incoming.gpu_usage.clone(); target.gpu_usage_pct = incoming.gpu_usage_pct; }
            "cpu-power" => target.cpu_power = incoming.cpu_power.clone(),
            "gpu-power" => target.gpu_power = incoming.gpu_power.clone(),
            "cpu-fan" => target.cpu_fan = incoming.cpu_fan.clone(),
            "gpu-fan" => target.gpu_fan = incoming.gpu_fan.clone(),
            "pwm-value" => target.pwm_value = incoming.pwm_value.clone(),
            "pwm-mode" => target.pwm_mode = incoming.pwm_mode.clone(),
            "ram-usage" => { target.ram_usage = incoming.ram_usage.clone(); target.ram_usage_pct = incoming.ram_usage_pct; target.ram_used_gb = incoming.ram_used_gb; target.ram_total_gb = incoming.ram_total_gb; }
            "cpu-frequency" | "lm-sensors" => {
                let prefix = if key == "cpu-frequency" { "cpu-frequency-" } else { "lm-" };
                target.extra_sensors.retain(|sensor| !sensor.key.starts_with(prefix));
                target.extra_sensors.extend(incoming.extra_sensors.iter().filter(|sensor| sensor.key.starts_with(prefix)).cloned());
            }
            _ => {},
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn partial_samples_do_not_replace_unrequested_readings() {
        let mut target = SensorSnapshot { cpu_fan: SensorReading::new("2400 RPM"), cpu_temp_c: Some(70.0), ..SensorSnapshot::default() };
        let incoming = SensorSnapshot { cpu_power: SensorReading::new("15 W"), ..SensorSnapshot::default() };
        merge_snapshot(&mut target, &incoming, &["cpu-power".into()]);
        assert_eq!(target.cpu_fan.value, "2400 RPM");
        assert_eq!(target.cpu_temp_c, Some(70.0));
        assert_eq!(target.cpu_power.value, "15 W");
    }

    #[test]
    fn readings_preserve_units_sources_and_unavailable_values() {
        let mut snapshot = SensorSnapshot {
            cpu_temp: SensorReading::with_source("70 C", "hwmon"), cpu_temp_c: Some(70.0),
            gpu_power: SensorReading::with_source("Unavailable", "NVML"), cpu_fan: SensorReading::with_source("2400 RPM", "EC"),
            ..SensorSnapshot::default()
        };
        snapshot.extra_sensors.push(victus_core::ExtraSensor {
            key: "lm-voltage".into(), group: "Board".into(), name: "Voltage".into(), unit: "V".into(),
            value_min: 0.0, value_max: 2.0, numeric_value: 1.2,
            reading: SensorReading::with_source("1.20 V", "libsensors"),
        });
        for (key, text, source, value) in [
            ("cpu-temp", "70 °C", "hwmon", Some(70.0)),
            ("gpu-power", "Unavailable", "NVML", None),
            ("cpu-fan", "2400 RPM", "EC", Some(2400.0)),
            ("lm-voltage", "1.20 V", "libsensors", Some(1.2)),
            ("profile", "Performance", "", None),
            ("unknown", "—", "", None),
        ] {
            assert_eq!(current_text(&snapshot, key, 2), text);
            assert_eq!(reading_source(&snapshot, key), source);
            assert_eq!(sample_value(&snapshot, key), value);
        }
        assert_eq!(cpu_caption(&snapshot), "CPU · —");
        assert_eq!(gpu_caption(&snapshot), "GPU · — · Unavailable");
        assert_eq!(rpm_text("Unavailable"), "—");
        assert_eq!(rpm_text("2400 RPM"), "2400");
    }
}
