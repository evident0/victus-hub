use super::*;
use crate::offline_scratch;

#[test]
fn state_round_trips_in_a_temp_file() {
    let dir = offline_scratch("state");
    let path = dir.join("state.json");
    assert!(path.starts_with(std::env::temp_dir()));
    let mut state = DaemonState::default();
    state.battery_power_save = true;
    state.cpu_frequency = Some((1_400_000, 5_000_000));
    state.power.enabled = true;
    state.initialized = true;
    save_state(&state, &path).unwrap();
    let loaded = load_state(&path);
    assert!(loaded.battery_power_save);
    assert_eq!(loaded.cpu_frequency, Some((1_400_000, 5_000_000)));
    assert!(loaded.power.enabled);
    assert!(!loaded.lighting.enabled);
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn missing_file_does_not_touch_the_default_path() {
    let dir = offline_scratch("state-missing");
    let loaded = load_state(&dir.join("nope.json"));
    assert!(!loaded.initialized);
    assert_eq!(loaded.fan.profiles.len(), 3);
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn legacy_partial_json_is_initialized_and_compact() {
    let legacy = state_from_value(&serde_json::json!({"fan": {"custom_curve_enabled": true}}));
    assert!(legacy.initialized);
    assert!(legacy.fan.custom_enabled);
    assert!(!state_from_value(&serde_json::json!({})).initialized);
    let compact = serde_json::to_string(&state_to_value(&DaemonState::default())).unwrap();
    assert!(!compact.contains('\n') && !compact.contains('\t'));
    let mut state = DaemonState::default();
    state.hardware_shortcuts = true;
    state.initialized = true;
    state.lighting.enabled = true;
    state.lighting.brightness = 64;
    state.disable_nvidia_queries = true;
    let round = state_from_value(&state_to_value(&state));
    assert!(round.hardware_shortcuts && round.initialized && round.disable_nvidia_queries);
    assert!(round.lighting.enabled);
    assert_eq!(round.lighting.brightness, 64);
}
