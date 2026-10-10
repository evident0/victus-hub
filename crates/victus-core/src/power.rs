pub const POWER_MIN_MW: i32 = 15_000;
pub const POWER_MAX_MW: i32 = 120_000;
pub const POWER_STEP_MW: i32 = 1_000;
pub const DEFAULT_POWER_LIMIT_MW: i32 = 25_000;
pub const DEFAULT_REAPPLY_SECONDS: i32 = 5;
pub const REAPPLY_MIN_S: i32 = 1;
pub const REAPPLY_MAX_S: i32 = 120;
pub const TCTL_TEMP_MIN_C: i32 = 75;
pub const TCTL_TEMP_MAX_C: i32 = 100;
pub const DEFAULT_TCTL_TEMP_C: i32 = 95;

/// ryzenadj's own acceptance window. The UI clamps more tightly before this.
pub const RYZENADJ_MIN_MW: i32 = 1_000;
pub const RYZENADJ_MAX_MW: i32 = 100_000;
pub const RYZENADJ_TCTL_MAX_C: i32 = 95;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PowerPolicy {
    pub enabled: bool,
    pub stapm_limit: i32,
    pub fast_limit: i32,
    pub slow_limit: i32,
    pub tctl_temp: i32,
    pub reapply_seconds: i32,
}

impl Default for PowerPolicy {
    fn default() -> Self {
        Self {
            enabled: false,
            stapm_limit: DEFAULT_POWER_LIMIT_MW,
            fast_limit: DEFAULT_POWER_LIMIT_MW,
            slow_limit: DEFAULT_POWER_LIMIT_MW,
            tctl_temp: DEFAULT_TCTL_TEMP_C,
            reapply_seconds: DEFAULT_REAPPLY_SECONDS,
        }
    }
}

/// Python 3 bankers rounding for halfway cases.
pub fn python_round(value: f64) -> i64 {
    let floor = value.floor();
    let diff = value - floor;
    if diff > 0.5 {
        (floor + 1.0) as i64
    } else if diff < 0.5 {
        floor as i64
    } else if (floor as i64).rem_euclid(2) == 0 {
        floor as i64
    } else {
        (floor + 1.0) as i64
    }
}

pub fn clamp_power_limit(value: i32) -> i32 {
    let rounded = python_round(f64::from(value) / f64::from(POWER_STEP_MW)) as i32 * POWER_STEP_MW;
    rounded.clamp(POWER_MIN_MW, POWER_MAX_MW)
}

pub fn clamp_tctl_temp(value: i32) -> i32 {
    value.clamp(TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C)
}

pub fn clamp_reapply_seconds(value: i32) -> i32 {
    value.clamp(REAPPLY_MIN_S, REAPPLY_MAX_S)
}

pub fn validate_ryzenadj(stapm: i32, fast: i32, slow: i32, tctl: i32) -> Result<(), super::HubError> {
    for (name, value) in [("stapm-limit", stapm), ("fast-limit", fast), ("slow-limit", slow)] {
        if !(RYZENADJ_MIN_MW..=RYZENADJ_MAX_MW).contains(&value) {
            return Err(super::HubError::new(format!(
                "{name} must be between {RYZENADJ_MIN_MW} and {RYZENADJ_MAX_MW} mW"
            )));
        }
    }
    if !(TCTL_TEMP_MIN_C..=RYZENADJ_TCTL_MAX_C).contains(&tctl) {
        return Err(super::HubError::new(format!(
            "tctl-temp must be between {TCTL_TEMP_MIN_C} and {RYZENADJ_TCTL_MAX_C} °C"
        )));
    }
    Ok(())
}

pub fn ryzenadj_args(stapm: i32, fast: i32, slow: i32, tctl: i32) -> Vec<String> {
    vec![
        format!("--stapm-limit={stapm}"),
        format!("--fast-limit={fast}"),
        format!("--slow-limit={slow}"),
        format!("--tctl-temp={tctl}"),
    ]
}

pub fn validate_intel_power(pl1_mw: i32, pl2_mw: i32) -> Result<(), super::HubError> {
    if !(POWER_MIN_MW..=pl2_mw).contains(&pl1_mw) || pl2_mw > POWER_MAX_MW {
        return Err(super::HubError::new(
            "Intel power limits must satisfy 15 W <= PL1 <= PL2 <= 120 W",
        ));
    }
    Ok(())
}

pub fn validate_undervolt(core_mv: i32, cache_mv: i32) -> Result<(), super::HubError> {
    if !((-250..=0).contains(&core_mv) && (-250..=0).contains(&cache_mv)) {
        return Err(super::HubError::new(
            "Intel undervolt offsets must be between -250 and 0 mV",
        ));
    }
    Ok(())
}

pub fn validate_frequency(minimum: i32, maximum: i32) -> Result<(), super::HubError> {
    if !(0 < minimum && minimum <= maximum) {
        return Err(super::HubError::new(
            "CPU minimum frequency must be positive and no greater than maximum",
        ));
    }
    Ok(())
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/power.rs"]
mod tests;
