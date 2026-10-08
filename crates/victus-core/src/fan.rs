use serde_json::{Map, Value};

use crate::{HubError, HubResult};

pub const TEMP_MIN_C: i32 = 30;
pub const CPU_TEMP_MAX_C: i32 = 100;
pub const GPU_TEMP_MAX_C: i32 = 90;
pub const PROFILE_KEYS: [&str; 3] = ["power-saver", "balanced", "performance"];
pub const CURVE_RESPONSE_SMOOTH: &str = "smooth";
pub const CURVE_RESPONSE_AGGRESSIVE: &str = "aggressive";

pub const EWMA_LAMBDA_INCREASE: f64 = 0.1;
pub const EWMA_LAMBDA_DECREASE: f64 = 0.1;
pub const SMART_EWMA_LAMBDA_INCREASE: f64 = 0.7;
pub const SMART_EWMA_LAMBDA_DECREASE: f64 = 0.05;
pub const CURVE_HYSTERESIS_C: f64 = 5.0;
pub const OVERHEAT_THRESHOLD_C: f64 = 90.0;
pub const OVERHEAT_RELEASE_C: f64 = 85.0;
pub const OVERHEAT_MIN_FAN_PCT: f64 = 50.0;
pub const OVERHEAT_COOLDOWN_S: f64 = 10.0;

const SMART_CPU: &[(i32, i32)] = &[
    (30, 0),
    (58, 0),
    (60, 32),
    (70, 42),
    (80, 62),
    (90, 85),
    (100, 100),
];
const SMART_GPU: &[(i32, i32)] = &[
    (30, 0),
    (52, 0),
    (54, 32),
    (65, 48),
    (75, 68),
    (82, 88),
    (90, 100),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FanPoint {
    pub temp: i32,
    pub speed: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FanProfileConfig {
    pub cpu_points: Vec<FanPoint>,
    pub gpu_points: Vec<FanPoint>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FanConfig {
    pub profiles: Vec<FanProfileConfig>,
    pub custom_enabled: bool,
    pub manual_preset: Option<String>,
    pub min_fan_change_pct: f64,
    pub smart_enabled: bool,
    pub curve_response: String,
}

impl Default for FanConfig {
    fn default() -> Self {
        config_from_value(&Value::Null)
    }
}

pub fn default_cpu_points() -> Vec<FanPoint> {
    vec![
        FanPoint { temp: TEMP_MIN_C, speed: 0 },
        FanPoint { temp: CPU_TEMP_MAX_C, speed: 100 },
    ]
}

pub fn default_gpu_points() -> Vec<FanPoint> {
    vec![
        FanPoint { temp: TEMP_MIN_C, speed: 0 },
        FanPoint { temp: GPU_TEMP_MAX_C, speed: 100 },
    ]
}

pub fn normalize_fan_points(mut points: Vec<FanPoint>, temp_max: i32) -> Vec<FanPoint> {
    if points.len() < 2 {
        points = if temp_max == GPU_TEMP_MAX_C {
            default_gpu_points()
        } else {
            default_cpu_points()
        };
    }
    points.sort_by_key(|point| point.temp);
    if let Some(first) = points.first_mut() {
        first.temp = TEMP_MIN_C;
    }
    if let Some(last) = points.last_mut() {
        last.temp = temp_max;
    }
    let mut minimum_speed = 0;
    for point in &mut points {
        point.speed = point.speed.clamp(minimum_speed, 100);
        minimum_speed = point.speed;
    }
    points
}

pub fn smart_cpu_points() -> Vec<FanPoint> {
    normalize_fan_points(
        SMART_CPU.iter().map(|(temp, speed)| FanPoint { temp: *temp, speed: *speed }).collect(),
        CPU_TEMP_MAX_C,
    )
}

pub fn smart_gpu_points() -> Vec<FanPoint> {
    normalize_fan_points(
        SMART_GPU.iter().map(|(temp, speed)| FanPoint { temp: *temp, speed: *speed }).collect(),
        GPU_TEMP_MAX_C,
    )
}

fn points_from_value(value: Option<&Value>, temp_max: i32, fallback: Vec<FanPoint>) -> Vec<FanPoint> {
    let Some(Value::Array(raw)) = value else {
        return fallback;
    };
    if raw.is_empty() {
        return fallback;
    }
    let mut points = Vec::new();
    for item in raw {
        let Value::Array(pair) = item else {
            return fallback;
        };
        if pair.len() != 2 {
            return fallback;
        }
        let Some(temp) = pair[0].as_i64() else {
            return fallback;
        };
        let Some(speed) = pair[1].as_i64() else {
            return fallback;
        };
        points.push(FanPoint { temp: temp as i32, speed: speed as i32 });
    }
    normalize_fan_points(points, temp_max)
}

pub fn config_from_value(stored: &Value) -> FanConfig {
    let stored = stored.as_object();
    let get = |key: &str| stored.and_then(|map| map.get(key));
    let custom_enabled = get("custom_curve_enabled").and_then(Value::as_bool).unwrap_or(false);
    let manual_preset = get("manual_preset").and_then(|value| match value {
        Value::String(text) if !text.is_empty() => Some(text.clone()),
        _ => None,
    });
    let min_fan_change_pct = get("min_fan_change_pct")
        .and_then(Value::as_f64)
        .unwrap_or(2.0)
        .max(0.0);
    let smart_enabled = get("smart_curve_enabled").and_then(Value::as_bool).unwrap_or(false) && custom_enabled;
    let curve_response = get("fan_curve_response")
        .and_then(Value::as_str)
        .filter(|value| *value == CURVE_RESPONSE_SMOOTH || *value == CURVE_RESPONSE_AGGRESSIVE)
        .unwrap_or(CURVE_RESPONSE_SMOOTH)
        .to_owned();
    let cpu_map = get("curve_points_by_profile").and_then(Value::as_object);
    let gpu_map = get("gpu_curve_points_by_profile").and_then(Value::as_object);
    let profiles = PROFILE_KEYS
        .iter()
        .map(|key| FanProfileConfig {
            cpu_points: points_from_value(cpu_map.and_then(|map| map.get(*key)), CPU_TEMP_MAX_C, default_cpu_points()),
            gpu_points: points_from_value(gpu_map.and_then(|map| map.get(*key)), GPU_TEMP_MAX_C, default_gpu_points()),
        })
        .collect();
    FanConfig {
        profiles,
        custom_enabled,
        manual_preset,
        min_fan_change_pct,
        smart_enabled,
        curve_response,
    }
}

pub fn config_to_value(config: &FanConfig) -> Value {
    let mut cpu = Map::new();
    let mut gpu = Map::new();
    for (index, profile) in config.profiles.iter().enumerate() {
        let key = PROFILE_KEYS.get(index).copied().unwrap_or("balanced");
        cpu.insert(key.to_owned(), points_value(&profile.cpu_points));
        gpu.insert(key.to_owned(), points_value(&profile.gpu_points));
    }
    serde_json::json!({
        "custom_tuned_profile": "balanced",
        "custom_curve_enabled": config.custom_enabled,
        "smart_curve_enabled": config.smart_enabled,
        "fan_curve_response": config.curve_response,
        "manual_preset": config.manual_preset,
        "min_fan_change_pct": config.min_fan_change_pct,
        "curve_points_by_profile": cpu,
        "gpu_curve_points_by_profile": gpu,
    })
}

fn points_value(points: &[FanPoint]) -> Value {
    Value::Array(points.iter().map(|point| serde_json::json!([point.temp, point.speed])).collect())
}

pub fn load_config(text: &str) -> FanConfig {
    match serde_json::from_str::<Value>(text) {
        Ok(value) if value.is_object() => config_from_value(&value),
        _ => config_from_value(&Value::Null),
    }
}

pub fn save_config_text(config: &FanConfig) -> String {
    serde_json::to_string_pretty(&config_to_value(config)).unwrap_or_else(|_| "{}".to_owned())
}

pub fn interpolate_fan(points: &[FanPoint], temp: i32) -> i32 {
    let Some(first) = points.first() else {
        return 0;
    };
    if temp <= first.temp {
        return first.speed;
    }
    for window in points.windows(2) {
        let previous = window[0];
        let next = window[1];
        if temp <= next.temp {
            let dx = next.temp - previous.temp;
            if dx == 0 {
                return next.speed;
            }
            let span = f64::from(temp - previous.temp) / f64::from(dx);
            return previous.speed + (span * f64::from(next.speed - previous.speed)) as i32;
        }
    }
    points.last().map(|point| point.speed).unwrap_or(0)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FanMode {
    Auto,
    Max,
    Smart,
    Custom,
}

impl FanMode {
    pub fn label(self) -> &'static str {
        match self {
            Self::Auto => "Auto",
            Self::Max => "Max",
            Self::Smart => "Smart",
            Self::Custom => "Custom",
        }
    }

    pub fn from_config(config: &FanConfig) -> Self {
        if config.manual_preset.as_deref() == Some("max") {
            Self::Max
        } else if config.manual_preset.as_deref() == Some("auto") || !config.custom_enabled {
            Self::Auto
        } else if config.smart_enabled {
            Self::Smart
        } else {
            Self::Custom
        }
    }
}

/// The three policy writes the UI sends, in order, for one mode button.
pub fn fan_mode_steps(start: &FanConfig, mode: FanMode) -> Vec<FanConfig> {
    let mut config = start.clone();
    let mut steps = Vec::new();
    match mode {
        FanMode::Auto | FanMode::Max => {
            config.smart_enabled = false;
            steps.push(config.clone());
            config.manual_preset = Some(if mode == FanMode::Max { "max" } else { "auto" }.to_owned());
            steps.push(config.clone());
            config.custom_enabled = false;
            config.smart_enabled = false;
            steps.push(config);
        }
        FanMode::Smart => {
            config.smart_enabled = true;
            config.custom_enabled = true;
            config.manual_preset = None;
            steps.push(config.clone());
            config.custom_enabled = true;
            steps.push(config);
        }
        FanMode::Custom => {
            config.smart_enabled = false;
            steps.push(config.clone());
            config.manual_preset = None;
            steps.push(config.clone());
            config.custom_enabled = true;
            steps.push(config);
        }
    }
    steps
}

pub fn compute_ewma(previous: Option<f64>, sample: f64, lambda_increase: f64, lambda_decrease: f64) -> f64 {
    match previous {
        None => sample,
        Some(previous) => {
            let smoothing = if sample > previous { lambda_increase } else { lambda_decrease };
            previous + smoothing * (sample - previous)
        }
    }
}

fn rounded_temp(temp: f64) -> i32 {
    (temp + 0.5).floor() as i32
}

pub fn hysteretic_curve_target(
    points: &[FanPoint],
    ema: f64,
    previous_ema: Option<f64>,
    previous_demand: Option<f64>,
) -> f64 {
    let normal = f64::from(interpolate_fan(points, rounded_temp(ema)));
    let (Some(previous_ema), Some(previous_demand)) = (previous_ema, previous_demand) else {
        return normal;
    };
    if ema > previous_ema {
        previous_demand.max(normal)
    } else if ema < previous_ema {
        let cooling = f64::from(interpolate_fan(points, rounded_temp(ema + CURVE_HYSTERESIS_C)));
        previous_demand.min(cooling)
    } else {
        previous_demand
    }
}

pub fn profile_index(raw: Option<i32>) -> usize {
    raw.unwrap_or(1).clamp(0, 2) as usize
}

pub fn pct_to_pwm(pct: f64) -> i32 {
    ((pct * 255.0 / 100.0) as i32).clamp(0, 255)
}

fn sensor_overheat(previous: bool, temp: Option<f64>) -> bool {
    match temp {
        None => previous,
        Some(temp) if !previous => temp >= OVERHEAT_THRESHOLD_C,
        Some(temp) => temp > OVERHEAT_RELEASE_C,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct LoopState {
    pub last_written_pct: Option<f64>,
    pub was_custom: bool,
    pub force_write: bool,
    pub ema_cpu: Option<f64>,
    pub ema_gpu: Option<f64>,
    pub cpu_demand: Option<f64>,
    pub gpu_demand: Option<f64>,
    pub curve_signature: Option<String>,
    pub cpu_overheating: bool,
    pub gpu_overheating: bool,
    pub overheat_active: bool,
    pub overheat_clear_at: Option<f64>,
}

impl Default for LoopState {
    fn default() -> Self {
        Self {
            last_written_pct: None,
            was_custom: false,
            force_write: false,
            ema_cpu: None,
            ema_gpu: None,
            cpu_demand: None,
            gpu_demand: None,
            curve_signature: None,
            cpu_overheating: false,
            gpu_overheating: false,
            overheat_active: false,
            overheat_clear_at: None,
        }
    }
}

impl LoopState {
    pub fn reset_algorithm(&mut self) {
        self.ema_cpu = None;
        self.ema_gpu = None;
        self.cpu_demand = None;
        self.gpu_demand = None;
        self.curve_signature = None;
        self.cpu_overheating = false;
        self.gpu_overheating = false;
        self.overheat_active = false;
        self.overheat_clear_at = None;
    }

    pub fn reset_curve_hysteresis(&mut self, signature: String) {
        self.cpu_demand = None;
        self.gpu_demand = None;
        self.curve_signature = Some(signature);
    }

    pub fn on_suspend(&mut self) {
        self.last_written_pct = None;
        self.was_custom = false;
        self.force_write = false;
        self.reset_algorithm();
    }

    pub fn on_manual_preset(&mut self) {
        self.on_suspend();
    }

    pub fn on_leave_custom(&mut self) {
        self.on_suspend();
    }

    pub fn on_enter_custom(&mut self, seeded_pct: Option<f64>) {
        self.reset_algorithm();
        self.last_written_pct = seeded_pct;
        self.force_write = true;
    }
}

pub fn update_overheat(state: &mut LoopState, now: f64) -> bool {
    state.cpu_overheating = sensor_overheat(state.cpu_overheating, state.ema_cpu);
    state.gpu_overheating = sensor_overheat(state.gpu_overheating, state.ema_gpu);
    if state.cpu_overheating || state.gpu_overheating {
        state.overheat_clear_at = None;
        state.overheat_active = true;
    } else if state.overheat_active {
        if state.overheat_clear_at.is_none() {
            state.overheat_clear_at = Some(now + OVERHEAT_COOLDOWN_S);
        }
        if now >= state.overheat_clear_at.unwrap_or(now) {
            state.overheat_clear_at = None;
            state.overheat_active = false;
        }
    }
    state.overheat_active
}

pub fn update_curve_target(
    state: &mut LoopState,
    profile: &FanProfileConfig,
    cpu_sample: Option<f64>,
    gpu_sample: Option<f64>,
    now: f64,
    lambda_increase: f64,
    lambda_decrease: f64,
) -> Option<f64> {
    if let Some(sample) = cpu_sample {
        let previous = state.ema_cpu;
        state.ema_cpu = Some(compute_ewma(state.ema_cpu, sample, lambda_increase, lambda_decrease));
        state.cpu_demand = Some(hysteretic_curve_target(
            &profile.cpu_points,
            state.ema_cpu.unwrap_or(sample),
            previous,
            state.cpu_demand,
        ));
    }
    if let Some(sample) = gpu_sample {
        let previous = state.ema_gpu;
        state.ema_gpu = Some(compute_ewma(state.ema_gpu, sample, lambda_increase, lambda_decrease));
        state.gpu_demand = Some(hysteretic_curve_target(
            &profile.gpu_points,
            state.ema_gpu.unwrap_or(sample),
            previous,
            state.gpu_demand,
        ));
    }
    let target = [state.cpu_demand, state.gpu_demand]
        .into_iter()
        .flatten()
        .reduce(f64::max)?;
    let target = if update_overheat(state, now) {
        target.max(OVERHEAT_MIN_FAN_PCT)
    } else {
        target
    };
    Some(target)
}

pub fn curve_signature(profile_idx: usize, profile: &FanProfileConfig, smart: bool) -> String {
    let tag = if smart { "smart".to_owned() } else { profile_idx.to_string() };
    format!(
        "{tag}|{}|{}",
        points_key(&profile.cpu_points),
        points_key(&profile.gpu_points)
    )
}

fn points_key(points: &[FanPoint]) -> String {
    points.iter().map(|point| format!("{},{}", point.temp, point.speed)).collect::<Vec<_>>().join(";")
}

/// `(min change percent, lambda increase, lambda decrease)`.
pub fn curve_response_params(config: &FanConfig) -> (f64, f64, f64) {
    let aggressive = config.smart_enabled || config.curve_response == CURVE_RESPONSE_AGGRESSIVE;
    let min_change = if config.smart_enabled { 2.0 } else { config.min_fan_change_pct };
    if aggressive {
        (min_change, SMART_EWMA_LAMBDA_INCREASE, SMART_EWMA_LAMBDA_DECREASE)
    } else {
        (min_change, EWMA_LAMBDA_INCREASE, EWMA_LAMBDA_DECREASE)
    }
}

pub trait FanIo {
    fn read_temps(&mut self) -> HubResult<(Option<f64>, Option<f64>)>;
    fn profile(&mut self) -> Option<i32>;
    fn load_config(&mut self) -> FanConfig;
    fn request_auto(&mut self) -> HubResult<()>;
    fn request_manual(&mut self) -> HubResult<()>;
    fn request_pwm(&mut self, pwm: i32) -> HubResult<()>;
    fn read_pwm_pct(&mut self) -> Option<f64>;
    fn is_suspended(&self) -> bool;
}

#[derive(Debug, Default)]
pub struct FanController {
    pub state: LoopState,
    logged_init: bool,
}

impl FanController {
    pub fn poll_once(&mut self, io: &mut dyn FanIo, now: f64) {
        if io.is_suspended() {
            self.state.on_suspend();
            return;
        }
        let config = io.load_config();
        if config.manual_preset.is_some() {
            self.state.on_manual_preset();
            return;
        }
        if !config.custom_enabled {
            if self.state.was_custom {
                let _ = io.request_auto();
                self.state.on_leave_custom();
            }
            return;
        }
        let (cpu_sample, gpu_sample) = match io.read_temps() {
            Ok(temps) => temps,
            Err(_) => return,
        };
        let raw_profile = io.profile().or(None);
        let profile_idx = profile_index(raw_profile);
        if !self.logged_init {
            log::info!("fan-control init: profile={raw_profile:?}, idx={profile_idx}");
            self.logged_init = true;
        }
        let smart = config.smart_enabled;
        let profile = if smart {
            FanProfileConfig { cpu_points: smart_cpu_points(), gpu_points: smart_gpu_points() }
        } else {
            config.profiles.get(profile_idx).cloned().unwrap_or_else(|| FanProfileConfig {
                cpu_points: default_cpu_points(),
                gpu_points: default_gpu_points(),
            })
        };
        if !self.state.was_custom {
            self.state.on_enter_custom(io.read_pwm_pct());
            let _ = io.request_manual();
        }
        self.state.was_custom = true;
        let signature = curve_signature(profile_idx, &profile, smart);
        if self.state.curve_signature.as_deref() != Some(signature.as_str()) {
            self.state.reset_curve_hysteresis(signature);
        }
        let (min_change, lambda_increase, lambda_decrease) = curve_response_params(&config);
        self.control_tick(&profile, cpu_sample, gpu_sample, now, min_change, lambda_increase, lambda_decrease, io);
    }

    pub fn control_tick(
        &mut self,
        profile: &FanProfileConfig,
        cpu_sample: Option<f64>,
        gpu_sample: Option<f64>,
        now: f64,
        min_fan_change_pct: f64,
        lambda_increase: f64,
        lambda_decrease: f64,
        io: &mut dyn FanIo,
    ) {
        let Some(target) = update_curve_target(
            &mut self.state,
            profile,
            cpu_sample,
            gpu_sample,
            now,
            lambda_increase,
            lambda_decrease,
        ) else {
            return;
        };
        if let Some(previous) = self.state.last_written_pct {
            if !self.state.force_write && (target - previous).abs() <= min_fan_change_pct {
                return;
            }
        }
        let pwm = pct_to_pwm(target);
        if io.request_pwm(pwm).is_err() {
            return;
        }
        self.state.last_written_pct = Some(target);
        self.state.force_write = false;
    }
}

pub fn active_curve_drives_fans(config: &FanConfig, manual_supported: bool) -> bool {
    config.custom_enabled && config.manual_preset.is_none() && manual_supported
}

/// Hardware fan mode the daemon applies outside the curve loop.
pub fn fan_enable_mode(config: &FanConfig, manual_supported: bool) -> Result<FanEnable, HubError> {
    if config.manual_preset.as_deref() == Some("max") {
        Ok(FanEnable::Max)
    } else if config.manual_preset.as_deref() == Some("auto") || !config.custom_enabled {
        Ok(FanEnable::Auto)
    } else if !manual_supported {
        Err(HubError::new("Manual fan control unavailable; using EC automatic mode"))
    } else {
        Ok(FanEnable::Manual)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FanEnable {
    Auto,
    Manual,
    Max,
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/fan.rs"]
mod tests;
