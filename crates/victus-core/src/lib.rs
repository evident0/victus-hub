//! Shared Victus Hub logic. Hardware access lives in `victus-hw` and the
//! daemon. Nothing in this crate opens the installed daemon socket or writes
//! system paths unless a caller passes that path in.

#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc, clippy::must_use_candidate)]
#![allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, clippy::cast_precision_loss)]
#![allow(clippy::float_cmp, clippy::module_name_repetitions, clippy::doc_markdown)]

mod client;
mod diagnostics;
mod error;
mod fan;
mod keys;
mod lighting;
mod loglevel;
mod power;
mod profiles;
mod protocol;
mod shortcuts;
mod state;
mod version;

pub use client::{transact, DEFAULT_SOCKET};
pub use diagnostics::{
    acpi_error_line, filter_daemon_journal, filter_journal_lines, kernel_module_error_line, render_markdown,
    Capability,
};
pub use error::{HubError, HubResult};
pub use fan::{
    active_curve_drives_fans, compute_ewma, config_from_value, config_to_value, curve_response_params,
    default_cpu_points, default_gpu_points, fan_enable_mode, fan_mode_steps, hysteretic_curve_target,
    interpolate_fan, load_config, normalize_fan_points, pct_to_pwm, profile_index, save_config_text, smart_cpu_points,
    smart_gpu_points, update_curve_target, update_overheat, FanConfig, FanController, FanEnable, FanIo,
    FanMode, FanPoint, FanProfileConfig, LoopState, CPU_TEMP_MAX_C, CURVE_RESPONSE_AGGRESSIVE,
    CURVE_RESPONSE_SMOOTH, GPU_TEMP_MAX_C, TEMP_MIN_C,
};
pub use keys::{
    keys_for_page, request_key_for_graph, requestable_keys, unknown_sensor_keys, FANS_PAGE_KEYS, GPU_QUERY_KEYS,
    HOME_PAGE_KEYS, KEYBOARD_PAGE_KEYS, POWER_PAGE_KEYS,
};
pub use lighting::{
    compute_anim_color, effect_is_animated, effects_for_zone_count, hex_to_rgb, lighting_frames,
    lighting_from_value, lighting_to_value, normalize_effect, normalize_lighting_settings, rgb_to_hex,
    spatial_index, step_brightness_settings, step_effect_settings, step_increment, zone_for_key,
    LightingSettings, RgbColor, ANIM_INTERVAL_MS, BRIGHTNESS_STEPS, DEFAULT_COLOR, DEFAULT_COLOR2,
    STATIC_INTERVAL_MS, ZONE_NAMES,
};
pub use loglevel::{debug_level_from_value, journal_line_visible, message_debug_level, terminal_line_visible};
pub use power::{
    clamp_power_limit, clamp_reapply_seconds, clamp_tctl_temp, ryzenadj_args, validate_frequency,
    validate_intel_power, validate_ryzenadj, validate_undervolt, PowerPolicy, DEFAULT_POWER_LIMIT_MW,
    DEFAULT_REAPPLY_SECONDS, DEFAULT_TCTL_TEMP_C, POWER_MAX_MW, POWER_MIN_MW, REAPPLY_MAX_S, REAPPLY_MIN_S,
    TCTL_TEMP_MAX_C, TCTL_TEMP_MIN_C,
};
pub use profiles::{
    clamp_profile, parse_tuned_active, parse_tuned_list, profile_index_for_name, tuned_candidates,
    tuned_profile_for, PROFILE_KEYS, PROFILE_LABELS,
};
pub use protocol::{
    format_sensors_response, format_status, match_request, parse_cpu_power_response, parse_ints, parse_json_object,
    parse_sensors_response, parse_status_response, snapshot_from_value, snapshot_to_value, CpuPowerSample,
    ExtraSensor, SensorReading, SensorSnapshot,
};
pub use shortcuts::{
    hardware_action, is_modifier, key_name, keybind_label, validate_shortcut, HardwareAction, HARDWARE_SHORTCUT_MODS,
    KEY_LEFTALT, KEY_LEFTCTRL, KEY_LEFTMETA, KEY_LEFTSHIFT,
};
pub use state::{
    load_state, power_from_value, power_to_value, save_state, state_from_value, state_path_from_env, state_to_value,
    DaemonState, DEFAULT_STATE_PATH,
};
pub use version::{release_is_newer, PROGRAM_VERSION};

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

/// Directory under the process temp dir for tests and other offline scratch.
/// This never points at `/sys`, `/run/victus-hubd`, or `/var/lib/victus-hubd`.
pub fn offline_scratch(label: &str) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "victus-hub-{label}-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&path).expect("create offline scratch directory");
    path
}
