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
