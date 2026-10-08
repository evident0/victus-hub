use super::*;

fn linear() -> FanProfileConfig {
    FanProfileConfig { cpu_points: default_cpu_points(), gpu_points: default_gpu_points() }
}

fn low(speed: i32) -> FanProfileConfig {
    FanProfileConfig {
        cpu_points: vec![FanPoint { temp: 30, speed }, FanPoint { temp: 100, speed }],
        gpu_points: vec![FanPoint { temp: 30, speed }, FanPoint { temp: 90, speed }],
    }
}

struct MemIo {
    temps: HubResult<(Option<f64>, Option<f64>)>,
    config: FanConfig,
    pwm: Vec<i32>,
    fail_pwm: bool,
    temp_reads: usize,
    manual: usize,
    auto: usize,
}

impl FanIo for MemIo {
    fn read_temps(&mut self) -> HubResult<(Option<f64>, Option<f64>)> {
        self.temp_reads += 1;
        self.temps.clone()
    }
    fn profile(&mut self) -> Option<i32> {
        Some(1)
    }
    fn load_config(&mut self) -> FanConfig {
        self.config.clone()
    }
    fn request_auto(&mut self) -> HubResult<()> {
        self.auto += 1;
        Ok(())
    }
    fn request_manual(&mut self) -> HubResult<()> {
        self.manual += 1;
        Ok(())
    }
    fn request_pwm(&mut self, pwm: i32) -> HubResult<()> {
        self.pwm.push(pwm);
        if self.fail_pwm { Err(HubError::new("fail")) } else { Ok(()) }
    }
    fn read_pwm_pct(&mut self) -> Option<f64> {
        Some(20.0)
    }
    fn is_suspended(&self) -> bool {
        false
    }
}

#[test]
fn ewma_and_hysteresis_match_the_python_cases() {
    assert_eq!(compute_ewma(None, 52.0, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE), 52.0);
    assert_eq!(compute_ewma(Some(50.0), 60.0, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE), 51.0);
    assert_eq!(compute_ewma(Some(50.0), 40.0, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE), 49.0);
    let mut ema = compute_ewma(None, 50.0, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE);
    ema = compute_ewma(Some(ema), 60.0, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE);
    ema = compute_ewma(Some(ema), 41.0, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE);
    assert_eq!(ema, 50.0);
    assert_eq!(
        compute_ewma(Some(50.0), 60.0, SMART_EWMA_LAMBDA_INCREASE, SMART_EWMA_LAMBDA_DECREASE),
        57.0
    );
    let points = default_cpu_points();
    assert_eq!(hysteretic_curve_target(&points, 65.0, None, None), 50.0);
    assert_eq!(hysteretic_curve_target(&points, 72.0, Some(65.0), Some(50.0)), 60.0);
    assert_eq!(hysteretic_curve_target(&points, 68.0, Some(70.0), Some(57.0)), 57.0);
    assert_eq!(hysteretic_curve_target(&points, 64.0, Some(70.0), Some(57.0)), 55.0);
    assert_eq!(hysteretic_curve_target(&points, 70.0, Some(70.0), Some(57.0)), 57.0);
}

#[test]
fn merged_target_and_overheat_floor() {
    let mut state = LoopState::default();
    let target = update_curve_target(&mut state, &linear(), Some(65.0), Some(60.0), 0.0, 0.1, 0.1);
    assert_eq!(target, Some(50.0));
    let mut state = LoopState::default();
    let target = update_curve_target(&mut state, &low(20), Some(92.0), None, 0.0, 0.1, 0.1);
    assert_eq!(target, Some(50.0));
    assert!(state.cpu_overheating && state.overheat_active);
    let mut state = LoopState { ema_cpu: Some(92.0), ..LoopState::default() };
    assert!(update_overheat(&mut state, 0.0));
    state.ema_cpu = Some(85.0);
    assert!(update_overheat(&mut state, 2.0));
    assert!(!state.cpu_overheating);
    assert_eq!(state.overheat_clear_at, Some(12.0));
    let mut state = LoopState { ema_cpu: Some(92.0), ..LoopState::default() };
    update_overheat(&mut state, 0.0);
    state.ema_cpu = Some(80.0);
    assert!(update_overheat(&mut state, 1.0));
    assert!(update_overheat(&mut state, 10.9));
    assert!(!update_overheat(&mut state, 11.0));
}

#[test]
fn controller_writes_pwm_and_skips_presets() {
    let mut io = MemIo {
        temps: Ok((Some(50.0), None)),
        config: FanConfig { custom_enabled: true, ..FanConfig::default() },
        pwm: Vec::new(),
        fail_pwm: false,
        temp_reads: 0,
        manual: 0,
        auto: 0,
    };
    let mut controller = FanController::default();
    controller.state.on_enter_custom(Some(20.0));
    controller.control_tick(&low(40), Some(50.0), None, 0.0, 2.0, 0.1, 0.1, &mut io);
    controller.control_tick(&low(40), Some(50.0), None, 1.0, 2.0, 0.1, 0.1, &mut io);
    assert_eq!(io.pwm, vec![102]);
    assert_eq!(controller.state.last_written_pct, Some(40.0));
    assert!(!controller.state.force_write);

    let mut io = MemIo { fail_pwm: true, pwm: Vec::new(), ..io };
    let mut controller = FanController::default();
    controller.state.on_enter_custom(Some(20.0));
    controller.control_tick(&low(40), Some(50.0), None, 0.0, 2.0, 0.1, 0.1, &mut io);
    controller.control_tick(&low(40), Some(50.0), None, 1.0, 2.0, 0.1, 0.1, &mut io);
    assert_eq!(io.pwm.len(), 2);
    assert_eq!(controller.state.last_written_pct, Some(20.0));
    assert!(controller.state.force_write);

    io.config.manual_preset = Some("auto".to_owned());
    io.config.custom_enabled = false;
    io.temp_reads = 0;
    controller.poll_once(&mut io, 0.0);
    assert_eq!(io.temp_reads, 0);
    assert_eq!(pct_to_pwm(50.0), 127);
    assert_eq!(pct_to_pwm(100.0), 255);
    assert_eq!(interpolate_fan(&smart_cpu_points(), 60), 32);
    assert_eq!(interpolate_fan(&smart_gpu_points(), 75), 68);
}

#[test]
fn mode_steps_keep_the_ui_write_order() {
    let start = FanConfig { custom_enabled: true, smart_enabled: true, ..FanConfig::default() };
    let steps = fan_mode_steps(&start, FanMode::Auto);
    assert_eq!(steps.len(), 3);
    assert!(!steps[0].smart_enabled && steps[0].custom_enabled);
    assert_eq!(steps[1].manual_preset.as_deref(), Some("auto"));
    assert!(!steps[2].custom_enabled && steps[2].manual_preset.as_deref() == Some("auto"));
    let smart = fan_mode_steps(&FanConfig::default(), FanMode::Smart);
    assert!(smart.last().unwrap().smart_enabled && smart.last().unwrap().manual_preset.is_none());
    let (min_change, up, down) = curve_response_params(&FanConfig {
        smart_enabled: true,
        custom_enabled: true,
        min_fan_change_pct: 4.5,
        ..FanConfig::default()
    });
    assert_eq!((min_change, up, down), (2.0, SMART_EWMA_LAMBDA_INCREASE, SMART_EWMA_LAMBDA_DECREASE));
}

#[test]
fn config_round_trip_preserves_curves() {
    let mut config = FanConfig::default();
    config.profiles[1].cpu_points = normalize_fan_points(
        vec![FanPoint { temp: 30, speed: 0 }, FanPoint { temp: 70, speed: 40 }, FanPoint { temp: 100, speed: 100 }],
        CPU_TEMP_MAX_C,
    );
    config.custom_enabled = true;
    config.curve_response = CURVE_RESPONSE_AGGRESSIVE.to_owned();
    let loaded = config_from_value(&config_to_value(&config));
    assert_eq!(loaded.profiles[1].cpu_points[1].speed, 40);
    assert!(loaded.custom_enabled);
    assert_eq!(loaded.curve_response, CURVE_RESPONSE_AGGRESSIVE);
    assert!(config_from_value(&serde_json::json!({"manual_preset": ""})).manual_preset.is_none());
}

#[test]
fn smart_decrease_gpu_trip_and_retrip_match_python() {
    let decreased = compute_ewma(Some(50.0), 40.0, SMART_EWMA_LAMBDA_INCREASE, SMART_EWMA_LAMBDA_DECREASE);
    assert!((decreased - 49.5).abs() < 1e-9, "{decreased}");

    let mut state = LoopState::default();
    let target = update_curve_target(&mut state, &low(20), Some(50.0), Some(92.0), 0.0, 0.1, 0.1);
    assert_eq!(target, Some(50.0));
    assert!(!state.cpu_overheating);
    assert!(state.gpu_overheating);

    let mut state = LoopState { ema_cpu: Some(92.0), cpu_demand: Some(75.0), ..LoopState::default() };
    assert!(update_overheat(&mut state, 0.0));
    let held = FanProfileConfig {
        cpu_points: vec![FanPoint { temp: 30, speed: 75 }, FanPoint { temp: 100, speed: 75 }],
        gpu_points: vec![FanPoint { temp: 30, speed: 0 }, FanPoint { temp: 90, speed: 0 }],
    };
    assert_eq!(update_curve_target(&mut state, &held, Some(92.0), None, 1.0, 0.1, 0.1), Some(75.0));

    let mut state = LoopState { ema_cpu: Some(92.0), ..LoopState::default() };
    update_overheat(&mut state, 0.0);
    state.ema_cpu = Some(88.0);
    assert!(update_overheat(&mut state, 1.0));
    assert!(state.cpu_overheating);
    state.ema_cpu = Some(80.0);
    update_overheat(&mut state, 1.0);
    assert_eq!(state.overheat_clear_at, Some(11.0));
    state.ema_cpu = Some(91.0);
    assert!(update_overheat(&mut state, 5.0));
    assert!(state.overheat_clear_at.is_none());
    assert!(state.overheat_active);
}

#[test]
fn small_changes_presets_and_helpers_match_python() {
    let mut quiet = MemIo {
        temps: Ok((Some(50.0), None)),
        config: FanConfig::default(),
        pwm: Vec::new(),
        fail_pwm: false,
        temp_reads: 0,
        manual: 0,
        auto: 0,
    };
    let mut held = FanController::default();
    held.state.last_written_pct = Some(40.0);
    held.control_tick(&low(42), Some(50.0), None, 0.0, 2.0, 0.1, 0.1, &mut quiet);
    assert!(quiet.pwm.is_empty());
    assert_eq!(held.state.last_written_pct, Some(40.0));
    let mut moved = FanController::default();
    moved.state.last_written_pct = Some(40.0);
    moved.control_tick(&low(43), Some(50.0), None, 0.0, 2.0, 0.1, 0.1, &mut quiet);
    assert_eq!(quiet.pwm, vec![pct_to_pwm(43.0)]);
    assert_eq!(moved.state.last_written_pct, Some(43.0));

    for preset in ["max", "auto"] {
        let mut io = MemIo {
            temps: Ok((Some(90.0), Some(90.0))),
            config: FanConfig {
                custom_enabled: false,
                manual_preset: Some(preset.to_owned()),
                ..FanConfig::default()
            },
            pwm: Vec::new(),
            fail_pwm: false,
            temp_reads: 0,
            manual: 0,
            auto: 0,
        };
        FanController::default().poll_once(&mut io, 0.0);
        assert_eq!(io.temp_reads, 0, "{preset}");
    }

    let aggressive = FanConfig {
        custom_enabled: true,
        curve_response: CURVE_RESPONSE_AGGRESSIVE.to_owned(),
        min_fan_change_pct: 4.5,
        ..FanConfig::default()
    };
    assert_eq!(
        curve_response_params(&aggressive),
        (4.5, SMART_EWMA_LAMBDA_INCREASE, SMART_EWMA_LAMBDA_DECREASE)
    );
    let smooth = FanConfig { custom_enabled: true, min_fan_change_pct: 4.5, ..FanConfig::default() };
    assert_eq!(curve_response_params(&smooth), (4.5, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE));

    let mut suspended = LoopState {
        last_written_pct: Some(40.0),
        was_custom: true,
        force_write: true,
        ema_cpu: Some(70.0),
        cpu_demand: Some(50.0),
        cpu_overheating: true,
        overheat_active: true,
        ..LoopState::default()
    };
    suspended.on_suspend();
    assert!(suspended.last_written_pct.is_none());
    assert!(!suspended.was_custom && !suspended.force_write);
    assert!(suspended.ema_cpu.is_none() && suspended.cpu_demand.is_none());
    assert!(!suspended.overheat_active);

    let mut hysteresis = LoopState {
        ema_cpu: Some(70.0),
        ema_gpu: Some(60.0),
        cpu_demand: Some(50.0),
        gpu_demand: Some(40.0),
        overheat_active: true,
        ..LoopState::default()
    };
    hysteresis.reset_curve_hysteresis("kept".to_owned());
    assert_eq!(hysteresis.ema_cpu, Some(70.0));
    assert_eq!(hysteresis.ema_gpu, Some(60.0));
    assert!(hysteresis.cpu_demand.is_none() && hysteresis.gpu_demand.is_none());
    assert!(hysteresis.overheat_active);
    assert_eq!(hysteresis.curve_signature.as_deref(), Some("kept"));

    assert_eq!(interpolate_fan(&smart_cpu_points(), 58), 0);
    assert_eq!(interpolate_fan(&smart_gpu_points(), 54), 32);
    let mut cruise = LoopState::default();
    let smart = FanProfileConfig { cpu_points: smart_cpu_points(), gpu_points: smart_gpu_points() };
    assert_eq!(
        update_curve_target(
            &mut cruise,
            &smart,
            Some(80.0),
            None,
            0.0,
            SMART_EWMA_LAMBDA_INCREASE,
            SMART_EWMA_LAMBDA_DECREASE,
        ),
        Some(62.0)
    );
    assert_eq!(profile_index(None), 1);
    assert_eq!(profile_index(Some(0)), 0);
    assert_eq!(profile_index(Some(2)), 2);
    assert_eq!(profile_index(Some(9)), 2);
    assert_eq!(profile_index(Some(-1)), 0);
    assert_eq!(pct_to_pwm(0.0), 0);

    let stored = serde_json::json!({
        "custom_curve_enabled": true,
        "smart_curve_enabled": false,
        "fan_curve_response": "aggressive",
        "min_fan_change_pct": 4.5,
        "curve_points_by_profile": {"balanced": [[30, 10], [100, 90]]}
    });
    let loaded = config_from_value(&config_to_value(&config_from_value(&stored)));
    assert!(loaded.custom_enabled && !loaded.smart_enabled);
    assert_eq!(loaded.curve_response, CURVE_RESPONSE_AGGRESSIVE);
    assert_eq!(loaded.min_fan_change_pct, 4.5);
    assert_eq!(loaded.profiles[1].cpu_points[0], FanPoint { temp: 30, speed: 10 });
    assert_eq!(config_from_value(&serde_json::json!({"fan_curve_response": "nope"})).curve_response, CURVE_RESPONSE_SMOOTH);
    assert_eq!(config_from_value(&serde_json::json!({})).min_fan_change_pct, 2.0);
    assert!(!config_from_value(&serde_json::json!({"smart_curve_enabled": true})).smart_enabled);
}
