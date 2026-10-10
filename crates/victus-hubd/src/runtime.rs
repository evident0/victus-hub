use std::path::{Path, PathBuf};
use std::cell::Cell;
use std::sync::mpsc::SyncSender;

use victus_core::{
    active_curve_drives_fans, clamp_profile, effect_is_animated, fan_enable_mode, hardware_action, lighting_frames,
    lighting_to_value, normalize_lighting_settings, step_brightness_settings, step_effect_settings, step_increment,
    FanConfig, FanController, FanEnable, FanIo, HardwareAction, HubResult, LightingSettings, PowerPolicy, RgbColor,
    ANIM_INTERVAL_MS, STATIC_INTERVAL_MS,
};

use crate::binds::ProgramShortcuts;
use crate::platform::Platform;

#[derive(Debug, Clone, Copy)]
pub enum ProfileRequest {
    Select(i32),
    Cycle,
}

pub struct Runtime<P: Platform> {
    pub platform: P,
    state_path: PathBuf,
    state: victus_core::DaemonState,
    pub shortcuts: ProgramShortcuts,
    fan: FanController,
    profile: Option<i32>,
    on_battery: Option<bool>,
    last_power_apply: f64,
    suspended: bool,
    light_last_send: f64,
    light_last_color: Option<RgbColor>,
    light_last_zone: Vec<Option<RgbColor>>,
    light_backlight_on: Option<bool>,
    light_last_idle_poll: f64,
    light_dimmed: bool,
    light_last_brightness: Option<i32>,
    light_anim_step: f64,
    light_last_anim: f64,
    #[cfg(test)]
    pub published: Vec<LightingSettings>,
    lighting_tx: Option<SyncSender<String>>,
    profile_tx: Option<SyncSender<ProfileRequest>>,
    revision: Cell<u64>,
    instance: String,
    pub pending_activation: Option<(Vec<i32>, i32)>,
}

impl<P: Platform> Runtime<P> {
    pub fn new(state_path: impl Into<PathBuf>, shortcuts_path: &Path, platform: P) -> Self {
        let state_path = state_path.into();
        let zones = platform.zone_count().max(1) as usize;
        Self {
            platform,
            state: victus_core::load_state(&state_path),
            state_path,
            shortcuts: ProgramShortcuts::load(shortcuts_path),
            fan: FanController::default(),
            profile: None,
            on_battery: None,
            last_power_apply: 0.0,
            suspended: false,
            light_last_send: 0.0,
            light_last_color: None,
            light_last_zone: vec![None; zones],
            light_backlight_on: None,
            light_last_idle_poll: 0.0,
            light_dimmed: false,
            light_last_brightness: None,
            light_anim_step: 0.0,
            light_last_anim: 0.0,
            #[cfg(test)]
            published: Vec::new(),
            lighting_tx: None,
            profile_tx: None,
            revision: Cell::new(0),
            instance: format!("{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos()),
            pending_activation: None,
        }
    }

    pub fn set_lighting_sender(&mut self, sender: SyncSender<String>) {
        self.lighting_tx = Some(sender);
    }

    pub fn set_profile_sender(&mut self, sender: SyncSender<ProfileRequest>) { self.profile_tx = Some(sender); }
    pub fn current_profile(&self) -> Option<i32> { self.profile }
    pub fn is_suspended(&self) -> bool { self.suspended }
    pub fn state_value(&self) -> serde_json::Value {
        let mut value = victus_core::state_to_value(&self.state);
        value["profile"] = serde_json::json!(self.profile);
        value["revision"] = serde_json::json!(self.revision.get());
        value["instance"] = serde_json::json!(self.instance);
        value
    }
    pub fn publish_state(&self) {
        self.revision.set(self.revision.get().wrapping_add(1));
        if let Some(sender) = &self.lighting_tx { let _ = sender.try_send(format!("STATE\t{}\n", self.state_value())); }
    }

    pub fn snapshot(&self) -> victus_core::DaemonState {
        self.state.clone()
    }

    pub fn nvidia_queries_disabled(&self) -> bool {
        self.state.disable_nvidia_queries && self.profile == Some(0)
    }

    pub fn fan_needs_gpu_temp(&self) -> bool {
        active_curve_drives_fans(&self.state.fan, self.platform.manual_fan())
    }

    fn persist(&mut self) {
        self.state.initialized = true;
        if let Err(error) = victus_core::save_state(&self.state, &self.state_path) {
            log::error!("save state: {error}");
        }
        self.publish_state();
    }

    /// Import legacy user policy once. A failed save must remain retryable.
    pub fn initialize_state(&mut self, mut state: victus_core::DaemonState) -> HubResult<String> {
        if self.state.initialized { return Ok("state already initialized".into()); }
        state.lighting = normalize_lighting_settings(&state.lighting, self.platform.zone_count());
        if let Some((minimum, maximum)) = state.cpu_frequency {
            victus_core::validate_frequency(minimum, maximum)?;
            self.platform.cpu_frequency(minimum, maximum)?;
        }
        state.initialized = true;
        victus_core::save_state(&state, &self.state_path).map_err(victus_core::HubError::new)?;
        self.state = state;
        self.sync_gpu_policy();
        self.apply_fan_mode();
        self.invalidate_lighting(true);
        self.last_power_apply = 0.0;
        self.on_battery = None;
        self.refresh_host(true);
        self.publish_state();
        Ok("legacy settings migrated".into())
    }

    /// Load `dir` when this daemon has no initialized policy.
    ///
    /// `dir` contains `victus-hub.conf` and `config.json`. A missing or rejected
    /// config stays uninitialized so a later import can still run. Returns
    /// whether the policy was saved and applied.
    pub fn import_desktop_config(&mut self, dir: &Path) -> bool {
        if self.state.initialized {
            return false;
        }
        let text = std::fs::read_to_string(dir.join("victus-hub.conf")).unwrap_or_default();
        let fan = std::fs::read_to_string(dir.join("config.json"))
            .ok()
            .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
            .filter(serde_json::Value::is_object);
        let Some(state) = victus_core::migrated_state(&text, fan.as_ref(), self.platform.intel_cpu()) else {
            return false;
        };
        if state.power.enabled {
            let check = if self.platform.intel_cpu() {
                victus_core::validate_intel_power(state.power.slow_limit, state.power.fast_limit)
            } else {
                victus_core::validate_ryzenadj(
                    state.power.stapm_limit,
                    state.power.fast_limit,
                    state.power.slow_limit,
                    state.power.tctl_temp,
                )
            };
            if let Err(error) = check {
                log::error!("desktop settings import rejected: {error}");
                return false;
            }
        }
        match self.initialize_state(state) {
            Ok(_) => true,
            Err(error) => {
                log::error!("desktop settings import failed: {error}");
                false
            }
        }
    }

    pub fn set_fan_config(&mut self, config: FanConfig) -> String {
        self.state.fan = config;
        self.sync_gpu_policy();
        self.persist();
        self.apply_fan_mode();
        "fan-config".into()
    }

    pub fn set_lighting(&mut self, settings: LightingSettings) -> String {
        log::info!("[keyboard-rgb] lighting-config {}", lighting_to_value(&settings));
        self.state.lighting = settings;
        self.persist();
        self.invalidate_lighting(true);
        "lighting-config".into()
    }

    pub fn set_power(&mut self, policy: PowerPolicy) -> String {
        self.state.power = policy;
        self.persist();
        self.last_power_apply = 0.0;
        "power-config".into()
    }

    pub fn set_cpu_frequency(&mut self, limits: Option<(i32, i32)>) -> HubResult<String> {
        if let Some((minimum, maximum)) = limits {
            self.platform.cpu_frequency(minimum, maximum)?;
        }
        self.remember_cpu_frequency(limits);
        Ok("cpu-frequency-config".into())
    }

    pub fn remember_cpu_frequency(&mut self, limits: Option<(i32, i32)>) {
        self.state.cpu_frequency = limits;
        self.persist();
    }

    pub fn set_battery_power_save(&mut self, enabled: bool) -> String {
        self.state.battery_power_save = enabled;
        if !enabled {
            self.state.profile_before_battery = None;
        }
        self.persist();
        self.on_battery = None;
        self.refresh_host(true);
        "battery-power-save".into()
    }

    pub fn set_hardware_shortcuts(&mut self, enabled: bool) -> String {
        self.state.hardware_shortcuts = enabled;
        self.persist();
        "hardware-shortcuts".into()
    }

    pub fn set_disable_nvidia_queries(&mut self, enabled: bool) -> String {
        self.state.disable_nvidia_queries = enabled;
        self.sync_gpu_policy();
        self.persist();
        "disable-nvidia-queries".into()
    }

    pub fn set_profile(&mut self, index: i32) -> HubResult<String> {
        let index = clamp_profile(index);
        if let Some(sender) = &self.profile_tx {
            sender.try_send(ProfileRequest::Select(index)).map_err(|_| victus_core::HubError::new("profile change already pending"))?;
            return Ok("profile change queued".into());
        }
        let message = self.platform.apply_profile(index)?;
        self.complete_profile(index);
        Ok(message)
    }

    pub fn complete_profile(&mut self, index: i32) {
        self.profile = Some(clamp_profile(index));
        self.platform.note_profile(index);
        self.sync_gpu_policy();
        self.publish_state();
    }

    pub fn apply_fan_mode(&mut self) {
        if self.suspended {
            return;
        }
        let manual = self.platform.manual_fan();
        let result = match fan_enable_mode(&self.state.fan, manual) {
            Ok(FanEnable::Max) => self.platform.pwm_max(),
            Ok(FanEnable::Auto) | Err(_) => self.platform.pwm_enable(2),
            Ok(FanEnable::Manual) => self.platform.pwm_enable(1),
        };
        if let Err(error) = result {
            log::error!("apply fan mode: {error}");
        }
    }

    pub fn fan_poll(&mut self, now: f64) {
        let config = self.state.fan.clone();
        if !self.platform.manual_fan() && config.custom_enabled && config.manual_preset.is_none() {
            self.fan.state.on_leave_custom();
            self.platform.release_gpu();
            return;
        }
        if self.suspended || !config.custom_enabled || config.manual_preset.is_some() {
            self.platform.release_gpu();
        }
        let profile = self.profile;
        let suspended = self.suspended;
        let disable_nvidia = self.nvidia_queries_disabled();
        let fan = &mut self.fan;
        let platform = &mut self.platform;
        let mut bridge = FanBridge { platform, config, profile, suspended, disable_nvidia };
        fan.poll_once(&mut bridge, now);
    }

    pub fn prepare_sleep(&mut self) {
        self.suspended = true;
        self.sync_gpu_policy();
        if let Err(error) = self.platform.pwm_enable(2) {
            log::error!("prepare-sleep: fan auto failed: {error}");
        }
        if let Err(error) = self.platform.keyboard_brightness(0) {
            log::error!("prepare-sleep: keyboard off failed: {error}");
        }
        self.invalidate_lighting(true);
    }

    pub fn resume(&mut self) {
        self.fan.state.on_suspend();
        self.invalidate_lighting(true);
        self.suspended = false;
        self.sync_gpu_policy();
        self.apply_fan_mode();
        self.last_power_apply = 0.0;
        if let Some((minimum, maximum)) = self.state.cpu_frequency {
            if let Err(error) = self.platform.cpu_frequency(minimum, maximum) {
                log::error!("resume: cpu frequency restore failed: {error}");
            }
        }
    }

    pub fn maybe_apply_power(&mut self, now: f64) {
        if self.suspended || !self.state.power.enabled || self.state.power.reapply_seconds <= 0 {
            return;
        }
        let interval = f64::from(self.state.power.reapply_seconds);
        if self.last_power_apply != 0.0 && now - self.last_power_apply < interval {
            return;
        }
        let policy = self.state.power.clone();
        let freq = self.state.cpu_frequency;
        if self.platform.intel_cpu() {
            if let Err(error) = self.platform.intel_power(policy.slow_limit, policy.fast_limit) {
                log::error!("apply power limits failed: {error}");
            }
        } else if let Err(error) = self.platform.ryzenadj(policy.stapm_limit, policy.fast_limit, policy.slow_limit, policy.tctl_temp) {
            log::error!("apply power limits failed: {error}");
        }
        if let Some((minimum, maximum)) = freq {
            if let Err(error) = self.platform.cpu_frequency(minimum, maximum) {
                log::error!("cpu frequency apply failed: {error}");
            }
        }
        self.last_power_apply = now;
    }

    /// Production executes this plan on the slow-I/O worker, outside this lock.
    pub fn power_plan(&mut self, now: f64) -> Option<(PowerPolicy, Option<(i32, i32)>)> {
        if self.suspended || !self.state.power.enabled || self.state.power.reapply_seconds <= 0
            || (self.last_power_apply != 0.0 && now - self.last_power_apply < f64::from(self.state.power.reapply_seconds)) {
            return None;
        }
        self.last_power_apply = now;
        Some((self.state.power.clone(), self.state.cpu_frequency))
    }

    pub fn refresh_host(&mut self, force: bool) {
        if let Some(index) = self.platform.profile_index() {
            if self.profile != Some(index) { self.complete_profile(index); }
        }
        let Some(on_ac) = self.platform.ac_online() else { return };
        let on_battery = !on_ac;
        if !force && self.on_battery == Some(on_battery) {
            return;
        }
        let previous = self.on_battery;
        self.on_battery = Some(on_battery);
        if !self.state.battery_power_save {
            return;
        }
        if on_battery && previous != Some(true) {
            let current = self.profile.unwrap_or(1);
            if current != 0 {
                self.state.profile_before_battery = Some(current);
                self.persist();
                if let Err(error) = self.set_profile(0) {
                    log::error!("battery power-save: profile apply failed: {error}");
                }
            }
        } else if !on_battery {
            let Some(restore) = self.state.profile_before_battery else { return };
            self.state.profile_before_battery = None;
            self.persist();
            if self.profile == Some(0) {
                if let Err(error) = self.set_profile(restore) {
                    log::error!("battery restore: profile apply failed: {error}");
                }
            }
        }
    }

    pub fn handle_key(&mut self, mods: &[i32], key: i32) {
        if key == 0 {
            return;
        }
        if self.state.hardware_shortcuts {
            if let Some(action) = hardware_action(mods, key) {
                match action {
                    HardwareAction::Brightness(direction) => self.step_brightness(direction),
                    HardwareAction::Effect(direction) => self.step_effect(direction),
                    HardwareAction::CycleProfile => {
                        let result = if let Some(sender) = &self.profile_tx {
                            sender.try_send(ProfileRequest::Cycle).map(|_| "profile cycle queued".into())
                                .map_err(|_| victus_core::HubError::new("profile change already pending"))
                        } else {
                            self.set_profile((self.profile.unwrap_or(1) + 1) % 3)
                        };
                        if let Err(error) = result {
                            log::error!("hardware shortcut: profile cycle failed: {error}");
                        }
                    }
                }
            }
        }
        if self.shortcuts.matches_any(mods, key) {
            self.pending_activation = Some((mods.to_vec(), key));
        }
    }

    pub fn take_activation(&mut self) -> Option<(Vec<i32>, i32)> {
        self.pending_activation.take()
    }

    fn step_brightness(&mut self, direction: i32) {
        self.state.lighting = step_brightness_settings(&self.state.lighting, direction);
        let settings = self.state.lighting.clone();
        self.persist();
        self.invalidate_lighting(true);
        self.publish(&settings);
    }

    fn step_effect(&mut self, direction: i32) {
        let zones = self.platform.zone_count().max(1);
        self.state.lighting = step_effect_settings(&self.state.lighting, direction, zones);
        let settings = self.state.lighting.clone();
        self.persist();
        self.light_anim_step = 0.0;
        self.invalidate_lighting(true);
        self.publish(&settings);
    }

    fn publish(&mut self, settings: &LightingSettings) {
        #[cfg(test)]
        self.published.push(settings.clone());
        if let Some(sender) = &self.lighting_tx {
            let body = lighting_to_value(settings);
            let _ = sender.try_send(format!("LIGHTING\t{body}\n"));
        }
    }

    pub fn lighting_interval(&self) -> f64 {
        let zones = self.platform.zone_count().max(1);
        let settings = normalize_lighting_settings(&self.state.lighting, zones);
        if settings.enabled && effect_is_animated(&settings.effect) {
            ANIM_INTERVAL_MS / 1000.0
        } else {
            STATIC_INTERVAL_MS / 1000.0
        }
    }

    pub fn lighting_tick(&mut self, now: f64) {
        if self.suspended {
            return;
        }
        let zones = self.platform.zone_count().max(1);
        let settings = normalize_lighting_settings(&self.state.lighting, zones);
        let dt = (now - self.light_last_anim).max(0.0);
        self.light_last_anim = now;
        let animated = settings.enabled && effect_is_animated(&settings.effect);
        if animated {
            self.light_anim_step += step_increment(settings.speed, dt);
        } else {
            self.light_anim_step = 0.0;
        }
        if now - self.light_last_idle_poll >= 0.5 {
            self.light_last_idle_poll = now;
            if settings.idle_timeout > 0 && settings.enabled {
                let idle = self.platform.idle_elapsed();
                let timed_out = idle >= f64::from(settings.idle_timeout);
                if idle < 0.0 {
                    if self.light_dimmed {
                        self.light_dimmed = false;
                        self.invalidate_lighting(true);
                    }
                } else if timed_out && !self.light_dimmed {
                    self.light_dimmed = true;
                    self.invalidate_lighting(true);
                } else if !timed_out && self.light_dimmed {
                    self.light_dimmed = false;
                    self.invalidate_lighting(true);
                }
            } else if self.light_dimmed {
                self.light_dimmed = false;
                self.invalidate_lighting(true);
            }
        }
        let want_off = !settings.enabled || self.light_dimmed;
        if want_off {
            if self.light_backlight_on != Some(false) && now - self.light_last_send >= 0.200 {
                let _ = self.platform.keyboard_brightness(0);
                self.light_backlight_on = Some(false);
                self.light_last_send = now;
                self.light_last_brightness = Some(0);
                self.invalidate_lighting(false);
            }
            return;
        }
        if self.light_last_brightness != Some(settings.brightness) {
            if self.platform.keyboard_user_brightness(settings.brightness).is_ok() {
                self.light_last_brightness = Some(settings.brightness);
            } else {
                self.light_last_brightness = None;
            }
        }
        let frames = lighting_frames(&settings, zones, self.light_anim_step);
        let min_interval = if animated { 0.050 } else { 0.200 };
        if zones <= 1 {
            self.tick_single(now, frames.first().copied().unwrap_or(RgbColor::new(0, 0, 0)), min_interval);
        } else {
            self.tick_multi(now, &frames, min_interval);
        }
    }

    fn tick_single(&mut self, now: f64, color: RgbColor, min_interval: f64) {
        let need = self.light_backlight_on != Some(true) || self.light_last_color != Some(color);
        if now - self.light_last_send >= min_interval && need {
            let _ = self.platform.keyboard_color(None, color.red, color.green, color.blue);
            self.light_last_send = now;
            self.light_last_color = Some(color);
            self.light_backlight_on = Some(true);
        }
    }

    fn tick_multi(&mut self, now: f64, frames: &[RgbColor], min_interval: f64) {
        if now - self.light_last_send < min_interval {
            return;
        }
        let mut any_write = false;
        for (zone, color) in frames.iter().enumerate() {
            let last = self.light_last_zone.get(zone).copied().flatten();
            let need = self.light_backlight_on != Some(true) || last != Some(*color);
            if !need {
                continue;
            }
            let _ = self.platform.keyboard_color(Some(zone as i32), color.red, color.green, color.blue);
            if zone < self.light_last_zone.len() {
                self.light_last_zone[zone] = Some(*color);
            }
            any_write = true;
        }
        if any_write || self.light_backlight_on != Some(true) {
            self.light_last_send = now;
            self.light_backlight_on = Some(true);
        }
    }

    fn invalidate_lighting(&mut self, reset_backlight: bool) {
        let zones = self.platform.zone_count().max(1) as usize;
        self.light_last_color = None;
        self.light_last_zone = vec![None; zones];
        if reset_backlight {
            self.light_backlight_on = None;
        }
    }

    fn sync_gpu_policy(&self) {
        self.platform.gpu_policy(self.fan_needs_gpu_temp(), self.nvidia_queries_disabled(), self.suspended);
    }
}

struct FanBridge<'a, P: Platform> {
    platform: &'a mut P,
    config: FanConfig,
    profile: Option<i32>,
    suspended: bool,
    disable_nvidia: bool,
}

impl<P: Platform> FanIo for FanBridge<'_, P> {
    fn read_temps(&mut self) -> HubResult<(Option<f64>, Option<f64>)> {
        self.platform.temps(self.disable_nvidia)
    }
    fn profile(&mut self) -> Option<i32> {
        self.profile
    }
    fn load_config(&mut self) -> FanConfig {
        self.config.clone()
    }
    fn request_auto(&mut self) -> HubResult<()> {
        self.platform.pwm_enable(2).map(|_| ())
    }
    fn request_manual(&mut self) -> HubResult<()> {
        self.platform.pwm_enable(1).map(|_| ())
    }
    fn request_pwm(&mut self, pwm: i32) -> HubResult<()> {
        self.platform.pwm(pwm).map(|_| ())
    }
    fn read_pwm_pct(&mut self) -> Option<f64> {
        self.platform.pwm_percent()
    }
    fn is_suspended(&self) -> bool {
        self.suspended
    }
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hubd/runtime.rs"]
mod tests;
