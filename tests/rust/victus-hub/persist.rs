use super::*;
use victus_core::{DaemonState, FanConfig, FanPoint, FanProfileConfig, LightingSettings};

fn sample_fan() -> FanConfig {
    FanConfig {
        profiles: vec![FanProfileConfig {
            cpu_points: vec![FanPoint { temp: 30, speed: 40 }, FanPoint { temp: 100, speed: 100 }],
            gpu_points: vec![FanPoint { temp: 30, speed: 35 }, FanPoint { temp: 90, speed: 100 }],
        }],
        custom_enabled: true,
        manual_preset: None,
        min_fan_change_pct: 2.0,
        smart_enabled: false,
        curve_response: "smooth".into(),
    }
}

#[test]
fn fan_curve_is_written_beside_the_conf_and_other_files_stay() {
    let dir = std::env::temp_dir().join(format!("victus-persist-fan-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let conf = dir.join("victus-hub.conf");
    std::fs::write(&conf, "[window]\nwidth=959\n").unwrap();
    remember_fan(false, &conf, &sample_fan()).unwrap();
    let saved = std::fs::read_to_string(dir.join("config.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&saved).unwrap();
    assert_eq!(value["custom_curve_enabled"], true);
    assert_eq!(value["manual_preset"], serde_json::Value::Null);
    assert_eq!(value["min_fan_change_pct"], 2.0);
    assert_eq!(value["curve_points_by_profile"]["power-saver"][0], serde_json::json!([30, 40]));
    assert_eq!(value["gpu_curve_points_by_profile"]["power-saver"][1], serde_json::json!([90, 100]));
    assert!(saved.contains("\"custom_tuned_profile\": \"balanced\""));
    assert_eq!(std::fs::read_to_string(&conf).unwrap(), "[window]\nwidth=959\n");
    remember_fan(true, &conf, &sample_fan()).unwrap();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn settings_updates_keep_window_shortcut_and_undervolt_sections() {
    let original = "\
[General]
height=680

[intelUndervolt]
core=-40
cache=-20

[programShortcut]
enabled=true
key=149
mods=29,42

[window]
geometry=@ByteArray(\\x1\\xd9)
width=959
";
    let updated = upsert_ini(original, &[
        ("power/save_on_battery", "true"),
        ("sensors/disableNvidiaQueries", "false"),
        ("keyboard/fn_shortcuts_enabled", "true"),
        ("powerLimits/stapm", "40000"),
        ("cpuFrequency/limits", "1400000, 5000000"),
    ]);
    assert!(updated.contains("height=680"));
    assert!(updated.contains("core=-40"));
    assert!(updated.contains("key=149"));
    assert!(updated.contains("geometry=@ByteArray(\\x1\\xd9)"));
    assert!(updated.contains("width=959"));
    assert_eq!(updated.matches("[power]").count(), 1);
    assert!(updated.contains("save_on_battery=true"));
    assert!(updated.contains("disableNvidiaQueries=false"));
    assert!(updated.contains("fn_shortcuts_enabled=true"));
    assert!(updated.contains("stapm=40000"));
    assert!(updated.contains("limits=1400000, 5000000"));
    let again = upsert_ini(&updated, &[("power/save_on_battery", "false"), ("powerLimits/stapm", "35000")]);
    assert_eq!(again.matches("save_on_battery=").count(), 1);
    assert!(again.contains("save_on_battery=false"));
    assert!(again.contains("stapm=35000"));
    assert!(!again.contains("stapm=40000"));
}

#[test]
fn keyboard_lighting_uses_the_qsettings_variant_qsettings_writes() {
    let lighting = LightingSettings {
        enabled: true,
        effect: "static".into(),
        color: "#2b8cee".into(),
        color2: "#0000ff".into(),
        speed: 48,
        zone_colors: vec!["#ff0000".into(), "#00ff00".into()],
        idle_timeout: 0,
        brightness: 255,
    };
    let line = format!("keyboardLighting={}", lighting_variant(&lighting));
    // Captured from PySide6 QSettings.setValue("keyboardLighting", {...}).
    let expected = r"keyboardLighting=@Variant(\0\0\0\b\0\0\0\b\0\0\0\x14\0\x62\0r\0i\0g\0h\0t\0n\0\x65\0s\0s\0\0\0\x2\0\0\0\xff\0\0\0\n\0\x63\0o\0l\0o\0r\0\0\0\n\0\0\0\xe\0#\0\x32\0\x62\0\x38\0\x63\0\x65\0\x65\0\0\0\f\0\x63\0o\0l\0o\0r\0\x32\0\0\0\n\0\0\0\xe\0#\0\x30\0\x30\0\x30\0\x30\0\x66\0\x66\0\0\0\f\0\x65\0\x66\0\x66\0\x65\0\x63\0t\0\0\0\n\0\0\0\f\0s\0t\0\x61\0t\0i\0\x63\0\0\0\xe\0\x65\0n\0\x61\0\x62\0l\0\x65\0\x64\0\0\0\x1\x1\0\0\0\x18\0i\0\x64\0l\0\x65\0_\0t\0i\0m\0\x65\0o\0u\0t\0\0\0\x2\0\0\0\0\0\0\0\n\0s\0p\0\x65\0\x65\0\x64\0\0\0\x2\0\0\0\x30\0\0\0\x16\0z\0o\0n\0\x65\0_\0\x63\0o\0l\0o\0r\0s\0\0\0\v\0\0\0\x2\0\0\0\xe\0#\0\x66\0\x66\0\x30\0\x30\0\x30\0\x30\0\0\0\xe\0#\0\x30\0\x30\0\x66\0\x66\0\x30\0\x30)";
    assert_eq!(line, expected);
    let parsed = crate::legacy::settings(&format!("[General]\n{line}\n"));
    let value = &parsed["keyboardLighting"];
    assert_eq!(value["enabled"], true);
    assert_eq!(value["effect"], "static");
    assert_eq!(value["color"], "#2b8cee");
    assert_eq!(value["brightness"], 255);
    assert_eq!(value["speed"], 48);
    assert_eq!(value["idle_timeout"], 0);
    assert_eq!(value["zone_colors"], serde_json::json!(["#ff0000", "#00ff00"]));
}

#[test]
fn mirror_writes_daemon_policy_without_dropping_unrelated_keys() {
    let dir = std::env::temp_dir().join(format!("victus-persist-mirror-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let conf = dir.join("victus-hub.conf");
    std::fs::write(&conf, "[intelUndervolt]\ncore=-15\ncache=-10\n\n[window]\nheight=811\n").unwrap();
    let mut state = DaemonState::default();
    state.initialized = true;
    state.fan = sample_fan();
    state.battery_power_save = true;
    state.hardware_shortcuts = true;
    state.disable_nvidia_queries = true;
    state.power.enabled = true;
    state.power.stapm_limit = 40_000;
    state.cpu_frequency = Some((1_400_000, 5_000_000));
    mirror_daemon(false, &conf, false, &state).unwrap();
    let text = std::fs::read_to_string(&conf).unwrap();
    assert!(text.contains("core=-15"));
    assert!(text.contains("height=811"));
    assert!(text.contains("save_on_battery=true"));
    assert!(text.contains("fn_shortcuts_enabled=true"));
    assert!(text.contains("disableNvidiaQueries=true"));
    assert!(text.contains("[powerLimits]"));
    assert!(text.contains("enabled=true"));
    assert!(text.contains("stapm=40000"));
    assert!(text.contains("limits=1400000, 5000000"));
    assert!(text.contains("keyboardLighting=@Variant("));
    let fan: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(dir.join("config.json")).unwrap()).unwrap();
    assert_eq!(fan["curve_points_by_profile"]["power-saver"][0][1], 40);
    mirror_daemon(true, &conf, false, &state).unwrap();
    let intel = dir.join("intel.conf");
    std::fs::write(&intel, "[window]\nwidth=1\n").unwrap();
    state.power.slow_limit = 30_000;
    state.power.fast_limit = 40_000;
    state.power.reapply_seconds = 8;
    remember_power(false, &intel, true, &state.power).unwrap();
    let intel_text = std::fs::read_to_string(&intel).unwrap();
    assert!(intel_text.contains("width=1"));
    assert!(intel_text.contains("[intelPowerLimits]"));
    assert!(intel_text.contains("pl1=30000"));
    assert!(intel_text.contains("pl2=40000"));
    assert!(intel_text.contains("reapplySeconds=8"));
    assert!(!intel_text.contains("stapm="));
    let _ = std::fs::remove_dir_all(&dir);
}
