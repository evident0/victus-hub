use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::fan::{config_from_value, config_to_value, FanConfig};
use crate::lighting::{lighting_from_value, lighting_to_value, LightingSettings};
use crate::power::{
    clamp_power_limit, clamp_reapply_seconds, clamp_tctl_temp, PowerPolicy, DEFAULT_POWER_LIMIT_MW,
    DEFAULT_REAPPLY_SECONDS, DEFAULT_TCTL_TEMP_C,
};

pub const DEFAULT_STATE_PATH: &str = "/var/lib/victus-hubd/state.json";

#[derive(Debug, Clone, PartialEq)]
pub struct DaemonState {
    pub fan: FanConfig,
    pub lighting: LightingSettings,
    pub power: PowerPolicy,
    pub cpu_frequency: Option<(i32, i32)>,
    pub battery_power_save: bool,
    pub hardware_shortcuts: bool,
    pub profile_before_battery: Option<i32>,
    pub initialized: bool,
    pub disable_nvidia_queries: bool,
}

impl Default for DaemonState {
    fn default() -> Self {
        Self {
            fan: FanConfig::default(),
            lighting: LightingSettings { enabled: false, ..LightingSettings::default() },
            power: PowerPolicy::default(),
            cpu_frequency: None,
            battery_power_save: false,
            hardware_shortcuts: false,
            profile_before_battery: None,
            initialized: false,
            disable_nvidia_queries: false,
        }
    }
}

pub fn power_from_value(raw: Option<&Value>) -> PowerPolicy {
    let Some(raw) = raw.and_then(Value::as_object) else {
        return PowerPolicy::default();
    };
    let number = |key: &str, fallback: i32| raw.get(key).and_then(Value::as_i64).map(|value| value as i32).unwrap_or(fallback);
    PowerPolicy {
        enabled: raw.get("enabled").and_then(Value::as_bool).unwrap_or(false),
        stapm_limit: clamp_power_limit(number("stapm_limit", DEFAULT_POWER_LIMIT_MW)),
        fast_limit: clamp_power_limit(number("fast_limit", DEFAULT_POWER_LIMIT_MW)),
        slow_limit: clamp_power_limit(number("slow_limit", DEFAULT_POWER_LIMIT_MW)),
        tctl_temp: clamp_tctl_temp(number("tctl_temp", DEFAULT_TCTL_TEMP_C)),
        reapply_seconds: clamp_reapply_seconds(number("reapply_seconds", DEFAULT_REAPPLY_SECONDS)),
    }
}

pub fn power_to_value(policy: &PowerPolicy) -> Value {
    json!({
        "enabled": policy.enabled,
        "stapm_limit": policy.stapm_limit,
        "fast_limit": policy.fast_limit,
        "slow_limit": policy.slow_limit,
        "tctl_temp": policy.tctl_temp,
        "reapply_seconds": policy.reapply_seconds,
    })
}

fn cpu_frequency_from_value(raw: Option<&Value>) -> Option<(i32, i32)> {
    let raw = raw?;
    let (minimum, maximum) = if let Some(object) = raw.as_object() {
        (object.get("min")?.as_i64()? as i32, object.get("max")?.as_i64()? as i32)
    } else if let Some(array) = raw.as_array() {
        (array.first()?.as_i64()? as i32, array.get(1)?.as_i64()? as i32)
    } else {
        return None;
    };
    (0 < minimum && minimum <= maximum).then_some((minimum, maximum))
}

pub fn state_to_value(state: &DaemonState) -> Value {
    json!({
        "fan": config_to_value(&state.fan),
        "lighting": lighting_to_value(&state.lighting),
        "power": power_to_value(&state.power),
        "battery_power_save": state.battery_power_save,
        "hardware_shortcuts": state.hardware_shortcuts,
        "profile_before_battery": state.profile_before_battery,
        "initialized": state.initialized,
        "disable_nvidia_queries": state.disable_nvidia_queries,
        "cpu_frequency": state.cpu_frequency.map(|(min, max)| json!({"min": min, "max": max})),
    })
}

pub fn state_from_value(raw: &Value) -> DaemonState {
    let Some(raw) = raw.as_object() else {
        return DaemonState::default();
    };
    let fan = config_from_value(raw.get("fan").unwrap_or(&Value::Null));
    let lighting = match raw.get("lighting") {
        Some(value) if value.as_object().is_some_and(|object| !object.is_empty()) => lighting_from_value(value),
        _ => LightingSettings { enabled: false, ..LightingSettings::default() },
    };
    let before = raw.get("profile_before_battery").and_then(Value::as_i64).map(|value| (value as i32).clamp(0, 2));
    let initialized = raw.get("initialized").and_then(Value::as_bool).unwrap_or_else(|| !raw.is_empty());
    DaemonState {
        fan,
        lighting,
        power: power_from_value(raw.get("power")),
        cpu_frequency: cpu_frequency_from_value(raw.get("cpu_frequency")),
        battery_power_save: raw.get("battery_power_save").and_then(Value::as_bool).unwrap_or(false),
        hardware_shortcuts: raw.get("hardware_shortcuts").and_then(Value::as_bool).unwrap_or(false),
        profile_before_battery: before,
        initialized,
        disable_nvidia_queries: raw.get("disable_nvidia_queries").and_then(Value::as_bool).unwrap_or(false),
    }
}

pub fn load_state(path: &Path) -> DaemonState {
    let Ok(text) = fs::read_to_string(path) else {
        return DaemonState::default();
    };
    serde_json::from_str::<Value>(&text).map(|value| state_from_value(&value)).unwrap_or_default()
}

pub fn save_state(state: &DaemonState, path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let tmp = path.with_extension("json.tmp");
    let text = serde_json::to_string_pretty(&state_to_value(state)).map_err(|error| error.to_string())?;
    fs::write(&tmp, format!("{text}\n")).map_err(|error| error.to_string())?;
    fs::rename(&tmp, path).map_err(|error| error.to_string())
}

pub fn state_path_from_env(override_path: Option<&str>) -> PathBuf {
    override_path.map_or_else(|| PathBuf::from(DEFAULT_STATE_PATH), PathBuf::from)
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/state.rs"]
mod tests;
