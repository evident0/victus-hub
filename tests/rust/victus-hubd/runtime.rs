use super::*;
use crate::platform::FakePlatform;
use victus_core::offline_scratch;

fn runtime(dir: &std::path::Path) -> Runtime<FakePlatform> {
    Runtime::new(
        dir.join("state.json"),
        &dir.join("shortcuts.json"),
        FakePlatform::default(),
    )
}

#[test]
fn uninitialized_daemon_imports_desktop_config_and_applies_it() {
    let dir = offline_scratch("import");
    let config = dir.join("config");
    std::fs::create_dir_all(&config).unwrap();
    std::fs::write(
        config.join("config.json"),
        r#"{"custom_curve_enabled":true,"manual_preset":null}"#,
    )
    .unwrap();
    std::fs::write(
        config.join("victus-hub.conf"),
        "[powerLimits]\nenabled=true\nstapm=40000\nfast=40000\nslow=40000\ntctlTemp=95\nreapplySeconds=5\n[cpuFrequency]\nlimits=1658352, 4600000\n",
    )
    .unwrap();
    let mut runtime = runtime(&dir);
    assert!(!runtime.snapshot().initialized);
    assert!(runtime.import_desktop_config(&config));
    let state = runtime.snapshot();
    assert!(state.initialized);
    assert!(state.fan.custom_enabled);
    assert!(state.fan.manual_preset.is_none());
    assert!(state.power.enabled);
    assert_eq!(state.power.stapm_limit, 40_000);
    assert_eq!(state.power.reapply_seconds, 5);
    assert_eq!(state.cpu_frequency, Some((1_658_352, 4_600_000)));
    assert!(
        runtime
            .platform
            .log
            .iter()
            .any(|line| line == "pwm-enable 1"),
        "{:?}",
        runtime.platform.log
    );
    assert!(
        runtime
            .platform
            .log
            .iter()
            .any(|line| line == "cpufreq 1658352 4600000"),
        "{:?}",
        runtime.platform.log
    );
    let (policy, frequency) = runtime.power_plan(1.0).expect("saved power policy is due");
    assert_eq!(policy.stapm_limit, 40_000);
    assert_eq!(frequency, Some((1_658_352, 4_600_000)));
    let saved = victus_core::load_state(&dir.join("state.json"));
    assert!(saved.initialized);
    assert!(saved.fan.custom_enabled);
    std::fs::write(
        config.join("config.json"),
        r#"{"custom_curve_enabled":false}"#,
    )
    .unwrap();
    assert!(!runtime.import_desktop_config(&config));
    assert!(runtime.snapshot().fan.custom_enabled);
}

#[test]
fn missing_desktop_config_stays_uninitialized() {
    let dir = offline_scratch("import-missing");
    let config = dir.join("config");
    std::fs::create_dir_all(&config).unwrap();
    let mut runtime = runtime(&dir);
    assert!(!runtime.import_desktop_config(&config));
    assert!(!runtime.snapshot().initialized);
    assert!(runtime.platform.log.is_empty());
    assert!(!dir.join("state.json").exists());
}

#[test]
fn initialized_daemon_does_not_reread_desktop_config() {
    let dir = offline_scratch("import-ready");
    let mut state = victus_core::DaemonState::default();
    state.initialized = true;
    victus_core::save_state(&state, &dir.join("state.json")).unwrap();
    let config = dir.join("config");
    std::fs::create_dir_all(&config).unwrap();
    std::fs::write(
        config.join("config.json"),
        r#"{"custom_curve_enabled":true,"manual_preset":null}"#,
    )
    .unwrap();
    let mut runtime = runtime(&dir);
    assert!(runtime.snapshot().initialized);
    assert!(!runtime.import_desktop_config(&config));
    assert!(!runtime.snapshot().fan.custom_enabled);
    assert!(runtime.platform.log.is_empty());
}
