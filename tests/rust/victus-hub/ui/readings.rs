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
