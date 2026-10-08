use super::*;
use serde_json::json;
use victus_core::DEFAULT_SOCKET;

#[test]
fn offline_model_has_no_socket_and_fan_steps_stay_ordered() {
    let model = Model::offline(4);
    assert!(connects_to_socket(&model).is_none());
    assert_ne!(model.socket, PathBuf::from(DEFAULT_SOCKET));
    assert!(sensor_request(3).is_none());
    assert!(sensor_request(5).is_none());
    assert!(sensor_request(0).unwrap().starts_with("sensors\t"));
    let mut model = model;
    let requests = model.select_fan_mode(FanMode::Auto);
    assert_eq!(requests.len(), 3);
    let flags: Vec<(bool, Option<String>, bool)> = requests
        .iter()
        .map(|line| {
            let json = line.split_once('\t').unwrap().1;
            let value: Value = serde_json::from_str(json).unwrap();
            (
                value["smart_curve_enabled"].as_bool().unwrap(),
                value["manual_preset"].as_str().map(str::to_owned),
                value["custom_curve_enabled"].as_bool().unwrap(),
            )
        })
        .collect();
    assert!(!flags[0].0);
    assert_eq!(flags[1].1.as_deref(), Some("auto"));
    assert!(!flags[2].2);
    let smart = fan_requests(&DaemonState::default().fan, FanMode::Smart);
    let first: Value = serde_json::from_str(smart[0].split_once('\t').unwrap().1).unwrap();
    assert_eq!(first["smart_curve_enabled"], json!(true));
    assert_eq!(first["custom_curve_enabled"], json!(true));
    assert!(first["manual_preset"].is_null());
}

#[test]
fn hydrate_keeps_daemon_policy_and_release_tags_parse_offline() {
    let mut model = Model::offline(1);
    model.hydrate(&json!({"fan": {"manual_preset": "max", "custom_curve_enabled": true}, "initialized": true}));
    assert_eq!(model.state.fan.manual_preset.as_deref(), Some("max"));
    assert!(model.state.initialized);
    assert_eq!(parse_release_tag(r#"{"tag_name":"v1.2.3"}"#).as_deref(), Some("v1.2.3"));
    assert!(parse_release_tag(r#"{"tag_name":"main"}"#).is_none());
    assert_eq!(update_choice("v1.0.3", PROGRAM_VERSION), UpdateChoice::Current);
    assert_eq!(update_choice("v1.0.10", PROGRAM_VERSION), UpdateChoice::Available);
    assert!(update_shell().contains(GITHUB_INSTALL_COMMAND));
    assert!(!update_shell().contains("QT_QPA_PLATFORM"));
    assert_eq!(model.keyboard_zone("W", 10.0, 300.0), 3);
    assert_eq!(model.keyboard_zone("P", 250.0, 300.0), 0);
    let report = diagnostics_from_logs(Some("hp_wmi Unknown EC"), None, Some(""));
    assert!(report.contains("## hp-wmi / RGB module errors"));
    assert!(report.contains("Unknown EC"));
}

#[test]
fn power_status_and_shortcut_conf_stay_on_the_given_text() {
    let root = victus_core::offline_scratch("power-status");
    assert!(root.starts_with(std::env::temp_dir()));
    assert_eq!(power_status_line(&root), "—");
    assert_eq!(power_status_line(Path::new("")), "—");
    let battery = root.join("BAT0");
    std::fs::create_dir_all(&battery).unwrap();
    std::fs::write(battery.join("type"), "Battery\n").unwrap();
    std::fs::write(battery.join("status"), "Discharging\n").unwrap();
    std::fs::write(battery.join("capacity"), "42\n").unwrap();
    assert_eq!(power_status_line(&root), "Battery · 42%");
    std::fs::write(battery.join("status"), "Full\n").unwrap();
    assert_eq!(power_status_line(&root), "AC · 42%");
    let ac = root.join("AC");
    std::fs::create_dir_all(&ac).unwrap();
    std::fs::write(ac.join("type"), "Mains\n").unwrap();
    std::fs::write(ac.join("online"), "1\n").unwrap();
    std::fs::write(battery.join("capacity"), "83\n").unwrap();
    std::fs::write(battery.join("status"), "Charging\n").unwrap();
    assert_eq!(power_status_line(&root), "AC · 83%");
    std::fs::write(ac.join("online"), "0\n").unwrap();
    assert_eq!(power_status_line(&root), "Battery · 83%");
    std::fs::write(battery.join("capacity"), "unknown\n").unwrap();
    assert_eq!(power_status_line(&root), "Battery");
    std::fs::write(ac.join("online"), "1\n").unwrap();
    assert_eq!(power_status_line(&root), "AC");

    let text = "[General]\nheight=680\n\n[programShortcut]\nenabled=true\nkey=149\nmods=\n\n[window]\nwidth=460\n";
    assert_eq!(program_shortcut_from_conf(text), (Vec::<i32>::new(), 149));
    let updated = upsert_program_shortcut(text, &[29], 24);
    assert!(updated.contains("height=680"));
    assert!(updated.contains("width=460"));
    assert!(updated.contains("key=24"));
    assert!(updated.contains("mods=29"));
    assert_eq!(program_shortcut_from_conf(&updated).1, 24);
}

#[test]
fn undervolt_offsets_survive_settings_updates_without_overwriting_other_sections() {
    let text = "[General]\nheight=680\n\n[intelUndervolt]\ncore=-100\ncache=-50\n\n[programShortcut]\nkey=149\nmods=\n";
    assert_eq!(undervolt_from_conf(text), (-100, -50));
    let updated = upsert_undervolt(text, 0, 0);
    assert_eq!(undervolt_from_conf(&updated), (0, 0));
    assert_eq!(program_shortcut_from_conf(&updated).1, 149);
    assert!(updated.contains("height=680"));
    assert_eq!(updated.matches("[intelUndervolt]").count(), 1);
    let mut model = Model::offline(1);
    model.hydrate(&serde_json::json!({"profile": 2}));
    assert_eq!(model.profile, 2);
}
