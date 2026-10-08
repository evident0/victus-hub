use serde_json::{json, Value};
use victus_core::{CpuPowerSample, HubResult, SensorSnapshot};

/// Hardware side effects. The daemon passes this in; tests use [`FakePlatform`].
pub trait Platform: Send {
    fn pwm_enable(&mut self, mode: i32) -> HubResult<String>;
    fn pwm_max(&mut self) -> HubResult<String>;
    fn pwm(&mut self, value: i32) -> HubResult<String>;
    fn keyboard_color(&mut self, zone: Option<i32>, red: u8, green: u8, blue: u8) -> HubResult<String>;
    fn keyboard_brightness(&mut self, level: i32) -> HubResult<String>;
    fn keyboard_user_brightness(&mut self, level: i32) -> HubResult<String>;
    fn zone_count(&self) -> i32;
    fn manual_fan(&self) -> bool;
    fn pwm_percent(&self) -> Option<f64>;
    fn temps(&mut self, disable_nvidia: bool) -> HubResult<(Option<f64>, Option<f64>)>;
    fn ryzenadj(&mut self, stapm: i32, fast: i32, slow: i32, tctl: i32) -> HubResult<String>;
    fn intel_power(&mut self, pl1: i32, pl2: i32) -> HubResult<String>;
    fn intel_undervolt(&mut self, core_mv: i32, cache_mv: i32) -> HubResult<String>;
    fn cpu_frequency(&mut self, minimum: i32, maximum: i32) -> HubResult<String>;
    fn intel_cpu(&self) -> bool;
    fn profile_index(&self) -> Option<i32>;
    fn apply_profile(&mut self, index: i32) -> HubResult<String>;
    fn ac_online(&self) -> Option<bool>;
    fn sensors(&mut self, keys: &[String], disable_nvidia: bool) -> SensorSnapshot;
    fn cpu_power(&mut self) -> CpuPowerSample;
    fn gpu_mux(&mut self, mode: i32) -> HubResult<String>;
    fn capabilities(&self) -> Value;
    fn idle_elapsed(&self) -> f64;
    fn release_gpu(&mut self) {}
}

#[derive(Debug, Clone)]
pub struct FakePlatform {
    pub log: Vec<String>,
    pub temps: (Option<f64>, Option<f64>),
    pub temp_reads: usize,
    pub sensor_reads: usize,
    pub manual: bool,
    pub zones: i32,
    pub profile: Option<i32>,
    pub ac: Option<bool>,
    pub intel: bool,
    pub idle: f64,
    pub pwm_pct: Option<f64>,
    pub sensors_snap: SensorSnapshot,
    pub power: CpuPowerSample,
    pub opened_msr: bool,
}

impl Default for FakePlatform {
    fn default() -> Self {
        Self {
            log: Vec::new(),
            temps: (Some(50.0), Some(50.0)),
            temp_reads: 0,
            sensor_reads: 0,
            manual: true,
            zones: 4,
            profile: Some(1),
            ac: Some(true),
            intel: false,
            idle: -1.0,
            pwm_pct: Some(20.0),
            sensors_snap: SensorSnapshot::default(),
            power: CpuPowerSample::Sampling { source: "scratch".into() },
            opened_msr: false,
        }
    }
}

impl Platform for FakePlatform {
    fn pwm_enable(&mut self, mode: i32) -> HubResult<String> {
        self.log.push(format!("pwm-enable {mode}"));
        Ok(format!("pwm-enable {mode}"))
    }
    fn pwm_max(&mut self) -> HubResult<String> {
        self.log.push("pwm-max".into());
        Ok("pwm-max".into())
    }
    fn pwm(&mut self, value: i32) -> HubResult<String> {
        self.log.push(format!("pwm {value}"));
        Ok(format!("pwm {value}"))
    }
    fn keyboard_color(&mut self, zone: Option<i32>, red: u8, green: u8, blue: u8) -> HubResult<String> {
        self.log.push(format!("color {zone:?} {red} {green} {blue}"));
        Ok("color".into())
    }
    fn keyboard_brightness(&mut self, level: i32) -> HubResult<String> {
        self.log.push(format!("brightness {level}"));
        Ok("brightness".into())
    }
    fn keyboard_user_brightness(&mut self, level: i32) -> HubResult<String> {
        self.log.push(format!("user-brightness {level}"));
        Ok("user-brightness".into())
    }
    fn zone_count(&self) -> i32 {
        self.zones
    }
    fn manual_fan(&self) -> bool {
        self.manual
    }
    fn pwm_percent(&self) -> Option<f64> {
        self.pwm_pct
    }
    fn temps(&mut self, _disable_nvidia: bool) -> HubResult<(Option<f64>, Option<f64>)> {
        self.temp_reads += 1;
        Ok(self.temps)
    }
    fn ryzenadj(&mut self, stapm: i32, fast: i32, slow: i32, tctl: i32) -> HubResult<String> {
        self.log.push(format!("ryzenadj {stapm} {fast} {slow} {tctl}"));
        Ok("ryzenadj".into())
    }
    fn intel_power(&mut self, pl1: i32, pl2: i32) -> HubResult<String> {
        self.log.push(format!("intel-power {pl1} {pl2}"));
        Ok("intel-power".into())
    }
    fn intel_undervolt(&mut self, core_mv: i32, cache_mv: i32) -> HubResult<String> {
        self.opened_msr = true;
        self.log.push(format!("undervolt {core_mv} {cache_mv}"));
        Ok("undervolt".into())
    }
    fn cpu_frequency(&mut self, minimum: i32, maximum: i32) -> HubResult<String> {
        self.log.push(format!("cpufreq {minimum} {maximum}"));
        Ok("cpufreq".into())
    }
    fn intel_cpu(&self) -> bool {
        self.intel
    }
    fn profile_index(&self) -> Option<i32> {
        self.profile
    }
    fn apply_profile(&mut self, index: i32) -> HubResult<String> {
        self.profile = Some(index);
        self.log.push(format!("profile {index}"));
        Ok(format!("profile {index}"))
    }
    fn ac_online(&self) -> Option<bool> {
        self.ac
    }
    fn sensors(&mut self, _keys: &[String], _disable_nvidia: bool) -> SensorSnapshot {
        self.sensor_reads += 1;
        self.sensors_snap.clone()
    }
    fn cpu_power(&mut self) -> CpuPowerSample {
        self.power.clone()
    }
    fn gpu_mux(&mut self, mode: i32) -> HubResult<String> {
        self.log.push(format!("mux {mode}"));
        Ok(format!("mux {mode}"))
    }
    fn capabilities(&self) -> Value {
        json!({
            "fan_modes": ["auto", "max", "smart", "custom"],
            "keyboard_lighting": true,
            "gpu_mux": null,
        })
    }
    fn idle_elapsed(&self) -> f64 {
        self.idle
    }
}
