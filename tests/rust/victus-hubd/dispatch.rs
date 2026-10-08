use super::*;
use crate::platform::FakePlatform;
use crate::runtime::Runtime;
use victus_core::{offline_scratch, FanConfig, FanPoint, KEY_LEFTCTRL};

fn peer(uid: u32) -> Peer {
    Peer { uid, pid: 1, authorized: true }
}

fn runtime() -> (Runtime<FakePlatform>, std::path::PathBuf) {
    let dir = offline_scratch("dispatch");
    let runtime = Runtime::new(dir.join("state.json"), &dir.join("shortcuts.json"), FakePlatform::default());
    (runtime, dir)
}

#[test]
fn legacy_import_cannot_replace_initialized_daemon_policy() {
    let (mut runtime, dir) = runtime();
    let request = "initialize-state\t{\"hardware_shortcuts\":true,\"lighting\":{\"enabled\":false}}";
    assert!(dispatch(&mut runtime, &peer(1000), request).starts_with("OK\t"));
    assert!(runtime.snapshot().initialized);
    assert!(runtime.snapshot().hardware_shortcuts);
    assert!(dispatch(&mut runtime, &peer(1000), "initialize-state\t{\"hardware_shortcuts\":false}").starts_with("OK\t"));
    assert!(runtime.snapshot().hardware_shortcuts);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn policy_commands_do_not_touch_live_paths() {
    let (mut runtime, dir) = runtime();
    assert!(dir.starts_with(std::env::temp_dir()));
    let denied = dispatch(&mut runtime, &Peer { uid: 1000, pid: 1, authorized: false }, "get-state");
    assert!(denied.starts_with("ERR\taccess denied"));
    let state = dispatch(&mut runtime, &peer(0), "get-state");
    assert!(state.starts_with("OK\t"));
    assert!(state.contains("capabilities"));
    let bad = dispatch(&mut runtime, &peer(0), "power-limits\t500\t25000\t25000\t95");
    assert!(bad.contains("stapm-limit"));
    assert!(!runtime.platform.log.iter().any(|line| line.starts_with("ryzenadj")));
    let uv = dispatch(&mut runtime, &peer(0), "intel-undervolt\t-300\t0");
    assert!(uv.contains("-250"));
    assert!(!runtime.platform.opened_msr);
    let sleep = dispatch(&mut runtime, &peer(1000), "prepare-sleep");
    assert!(sleep.contains("sleep hooks require root"));
    let parked = dispatch(&mut runtime, &peer(0), "prepare-sleep");
    assert!(parked.starts_with("OK\t"));
    assert!(runtime.platform.log.iter().any(|line| line == "pwm-enable 2"));
    assert!(runtime.platform.log.iter().any(|line| line == "brightness 0"));
    let unknown = dispatch(&mut runtime, &peer(0), "sensors\tnot-a-sensor");
    assert!(unknown.contains("unknown sensors"));
    let empty = dispatch(&mut runtime, &peer(0), "sensors");
    assert!(empty.starts_with("OK\t"));
    assert_eq!(runtime.platform.sensor_reads, 0);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn custom_fan_poll_writes_pwm_inside_the_fake_platform() {
    let (mut runtime, dir) = runtime();
    let mut config = FanConfig::default();
    config.custom_enabled = true;
    config.manual_preset = None;
    for profile in &mut config.profiles {
        profile.cpu_points = vec![FanPoint { temp: 30, speed: 40 }, FanPoint { temp: 100, speed: 40 }];
        profile.gpu_points = vec![FanPoint { temp: 30, speed: 40 }, FanPoint { temp: 90, speed: 40 }];
    }
    let line = dispatch(&mut runtime, &peer(0), &format!("fan-config\t{}", victus_core::config_to_value(&config)));
    assert!(line.starts_with("OK\tfan-config"));
    runtime.fan_poll(1.0);
    assert!(runtime.platform.log.iter().any(|entry| entry == "pwm 102"), "{:?}", runtime.platform.log);
    let reads = runtime.platform.temp_reads;
    runtime.fan_poll(2.0);
    assert_eq!(runtime.platform.log.iter().filter(|entry| *entry == "pwm 102").count(), 1);
    assert!(runtime.platform.temp_reads > reads);
    runtime.platform.ac = Some(false);
    runtime.platform.profile = Some(1);
    dispatch(&mut runtime, &peer(0), "battery-power-save\t1");
    assert_eq!(runtime.platform.profile, Some(0));
    runtime.platform.ac = Some(true);
    runtime.refresh_host(false);
    assert_eq!(runtime.platform.profile, Some(1));
    let shortcut = format!(r#"program-shortcut\t{{"mods":[{KEY_LEFTCTRL}],"key":24}}"#);
    // The format above escaped the tab. Build the request directly.
    let _ = shortcut;
    let request = format!("program-shortcut\t{{\"mods\":[{KEY_LEFTCTRL}],\"key\":24}}");
    assert!(dispatch(&mut runtime, &peer(0), &request).starts_with("ERR\t"));
    assert!(dispatch(&mut runtime, &peer(1000), &request).starts_with("OK\t"));
    runtime.set_hardware_shortcuts(true);
    runtime.handle_key(&[29, 42], 103);
    assert!(!runtime.published.is_empty());
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn unavailable_manual_control_never_overrides_the_auto_fallback() {
    let (mut runtime, dir) = runtime();
    runtime.platform.manual = false;
    let config = FanConfig { custom_enabled: true, ..FanConfig::default() };
    runtime.set_fan_config(config);
    runtime.fan_poll(1.0);
    assert!(runtime.platform.log.iter().any(|entry| entry == "pwm-enable 2"));
    assert!(!runtime.platform.log.iter().any(|entry| entry == "pwm-enable 1" || entry.starts_with("pwm ")));
    assert_eq!(runtime.platform.temp_reads, 0);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn saved_four_zone_static_lighting_is_written_once_after_startup() {
    let dir = offline_scratch("static-startup");
    let mut state = victus_core::DaemonState::default();
    state.lighting.enabled = true;
    state.lighting.zone_colors = vec!["#123456".into(); 4];
    victus_core::save_state(&state, &dir.join("state.json")).unwrap();
    let mut runtime = Runtime::new(dir.join("state.json"), &dir.join("shortcuts.json"), FakePlatform::default());
    runtime.lighting_tick(1.0);
    runtime.lighting_tick(2.0);
    runtime.lighting_tick(3.0);
    assert_eq!(runtime.platform.log.iter().filter(|entry| entry.starts_with("color ")).count(), 4);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn unsupported_commands_and_protocol_validation_match_python() {
    let (mut runtime, dir) = runtime();
    for request in [
        "keyboard-events",
        "keyboard-last-event",
        "fan-max-not-a-command",
        "shortcut-events\t{\"mods\":[],\"key\":30}",
        "shortcut-events\t{\"mods\":[],\"key\":149}",
    ] {
        let reply = dispatch(&mut runtime, &peer(0), request);
        assert!(reply.contains("unsupported"), "{request}: {reply}");
    }
    assert!(dispatch(&mut runtime, &peer(0), "fan-max").starts_with("OK\t"));
    assert!(runtime.platform.log.iter().any(|line| line == "pwm-max"));
    assert!(dispatch(&mut runtime, &peer(0), "cpu-frequency-limits\t1100980\t4600000").starts_with("OK\t"));
    assert!(runtime.platform.log.iter().any(|line| line == "cpufreq 1100980 4600000"));
    assert!(dispatch(&mut runtime, &peer(0), "intel-power-limits\t25000\t35000").starts_with("OK\t"));
    assert!(runtime.platform.log.iter().any(|line| line == "intel-power 25000 35000"));
    assert!(dispatch(&mut runtime, &peer(0), "intel-undervolt\t-100\t-50").starts_with("OK\t"));
    for request in ["cpu-frequency-limits\t1", "cpu-frequency-limits\t1\t2\t3", "intel-power-limits\tx\t2", "intel-undervolt\t1"] {
        let reply = dispatch(&mut runtime, &peer(0), request);
        assert!(reply.starts_with("ERR\t"), "{request}: {reply}");
    }
    assert!(dispatch(&mut runtime, &peer(1000), "resume").contains("sleep hooks require root"));

    runtime.lighting_tick(1.0);
    runtime.lighting_tick(2.0);
    assert_eq!(runtime.platform.log.iter().filter(|entry| *entry == "brightness 0").count(), 1);
    let reads = runtime.platform.temp_reads;
    runtime.prepare_sleep();
    runtime.fan_poll(3.0);
    assert_eq!(runtime.platform.temp_reads, reads);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn fan_max_nvidia_policy_and_battery_restore_match_python() {
    let (mut runtime, dir) = runtime();
    let config = FanConfig { manual_preset: Some("max".to_owned()), custom_enabled: false, ..FanConfig::default() };
    runtime.set_fan_config(config);
    assert!(runtime.platform.log.iter().any(|line| line == "pwm-max"));
    assert!(runtime.snapshot().initialized);

    runtime.set_disable_nvidia_queries(true);
    assert!(runtime.snapshot().disable_nvidia_queries);
    runtime.set_profile(0).unwrap();
    assert!(runtime.nvidia_queries_disabled());
    runtime.set_profile(1).unwrap();
    assert!(!runtime.nvidia_queries_disabled());

    runtime.platform.profile = Some(2);
    runtime.platform.ac = Some(false);
    runtime.set_battery_power_save(true);
    assert_eq!(runtime.current_profile(), Some(0));
    assert_eq!(runtime.snapshot().profile_before_battery, Some(2));
    runtime.platform.ac = Some(true);
    runtime.refresh_host(true);
    assert_eq!(runtime.current_profile(), Some(2));
    assert!(runtime.snapshot().profile_before_battery.is_none());

    runtime.platform.profile = Some(2);
    runtime.platform.ac = Some(false);
    runtime.set_battery_power_save(true);
    assert_eq!(runtime.snapshot().profile_before_battery, Some(2));
    runtime.set_profile(1).unwrap();
    runtime.platform.ac = Some(true);
    runtime.refresh_host(true);
    assert_eq!(runtime.current_profile(), Some(1));
    assert!(runtime.snapshot().profile_before_battery.is_none());
    let _ = std::fs::remove_dir_all(dir);
}

struct BlockingSensor {
    inner: FakePlatform,
    started: Option<std::sync::mpsc::Sender<()>>,
    release: Option<std::sync::mpsc::Receiver<()>>,
}

macro_rules! forward_mut {
    ($($name:ident($($arg:ident: $ty:ty),*) -> $ret:ty;)*) => { $(
        fn $name(&mut self, $($arg: $ty),*) -> $ret { self.inner.$name($($arg),*) }
    )* };
}

impl Platform for BlockingSensor {
    forward_mut! {
        pwm_enable(mode: i32) -> HubResult<String>;
        pwm_max() -> HubResult<String>;
        pwm(value: i32) -> HubResult<String>;
        keyboard_color(zone: Option<i32>, red: u8, green: u8, blue: u8) -> HubResult<String>;
        keyboard_brightness(level: i32) -> HubResult<String>;
        keyboard_user_brightness(level: i32) -> HubResult<String>;
        temps(disable: bool) -> HubResult<(Option<f64>, Option<f64>)>;
        ryzenadj(stapm: i32, fast: i32, slow: i32, tctl: i32) -> HubResult<String>;
        intel_power(pl1: i32, pl2: i32) -> HubResult<String>;
        intel_undervolt(core: i32, cache: i32) -> HubResult<String>;
        cpu_frequency(minimum: i32, maximum: i32) -> HubResult<String>;
        apply_profile(index: i32) -> HubResult<String>;
        cpu_power() -> victus_core::CpuPowerSample;
        gpu_mux(mode: i32) -> HubResult<String>;
    }
    fn zone_count(&self) -> i32 { self.inner.zone_count() }
    fn manual_fan(&self) -> bool { self.inner.manual_fan() }
    fn pwm_percent(&self) -> Option<f64> { self.inner.pwm_percent() }
    fn intel_cpu(&self) -> bool { self.inner.intel_cpu() }
    fn profile_index(&self) -> Option<i32> { self.inner.profile_index() }
    fn ac_online(&self) -> Option<bool> { self.inner.ac_online() }
    fn capabilities(&self) -> Value { self.inner.capabilities() }
    fn idle_elapsed(&self) -> f64 { self.inner.idle_elapsed() }
    fn sensors(&mut self, keys: &[String], disable: bool) -> SensorSnapshot {
        if let Some(started) = &self.started { started.send(()).unwrap(); }
        if let Some(release) = &self.release { release.recv_timeout(std::time::Duration::from_secs(5)).unwrap(); }
        self.inner.sensors(keys, disable)
    }
}

#[test]
fn blocked_display_sampling_does_not_hold_the_fan_runtime_lock() {
    let dir = offline_scratch("sensor-isolation");
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let platform = || BlockingSensor { inner: FakePlatform::default(), started: None, release: None };
    let mut runtime = Runtime::new(dir.join("state.json"), &dir.join("shortcuts.json"), platform());
    runtime.set_fan_config(FanConfig { custom_enabled: true, ..FanConfig::default() });
    let runtime = Arc::new(Mutex::new(runtime));
    let sampler = Arc::new(Mutex::new(BlockingSensor { inner: FakePlatform::default(), started: Some(started_tx), release: Some(release_rx) }));
    let commands = Arc::new(Mutex::new(platform()));
    let request_runtime = Arc::clone(&runtime);
    let request = std::thread::spawn(move || dispatch_with_workers(&request_runtime, &sampler, &commands, &peer(0), "sensors\tgpu-power"));
    started_rx.recv_timeout(std::time::Duration::from_secs(3)).unwrap();
    let progressed = if let Ok(mut guard) = runtime.try_lock() {
        guard.fan_poll(1.0);
        guard.platform.inner.log.iter().any(|line| line.starts_with("pwm "))
    } else { false };
    release_tx.send(()).unwrap();
    assert!(request.join().unwrap().starts_with("OK\t"));
    assert!(progressed, "display sampling blocked fan control");
    let _ = std::fs::remove_dir_all(dir);
}
