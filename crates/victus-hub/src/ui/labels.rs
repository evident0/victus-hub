use gtk4::prelude::*;
use victus_core::{FanConfig, FanMode, FanPoint};

use super::paint;
use super::widgets;
use crate::Model;

pub(super) fn fan_mode_from_key(key: &str) -> Option<FanMode> {
    match key {
        "auto" => Some(FanMode::Auto),
        "smart" => Some(FanMode::Smart),
        "max" => Some(FanMode::Max),
        "custom" => Some(FanMode::Custom),
        _ => None,
    }
}

pub(super) fn fan_mode_key(mode: FanMode) -> &'static str {
    match mode {
        FanMode::Auto => "auto",
        FanMode::Smart => "smart",
        FanMode::Max => "max",
        FanMode::Custom => "custom",
    }
}

pub(super) fn fan_description(mode: FanMode) -> &'static str {
    match mode {
        FanMode::Auto => "Automatic fan control follows the firmware's own fan curve.",
        FanMode::Smart => "Smart fan control follows the built-in curve with faster smoothing.",
        FanMode::Max => "Both fans run at full speed. Select another mode to return to automatic or curve-based control.",
        FanMode::Custom => "Select Custom to edit this profile's CPU and GPU fan curves.",
    }
}

pub(super) fn mode_name(profile: i32) -> &'static str {
    match profile {
        0 => "Eco",
        2 => "Performance",
        _ => "Balanced",
    }
}

pub(super) fn accent_hex(profile: i32) -> &'static str {
    match profile {
        0 => "#2FBF8F",
        2 => "#E2572C",
        _ => "#3F8CFF",
    }
}

pub(super) fn accent_rgb(profile: i32) -> (f64, f64, f64) {
    paint::unit_rgb(accent_hex(profile))
}

pub(super) fn curve_points(fan: &FanConfig, profile: i32, cpu: bool) -> &[FanPoint] {
    let index = usize::try_from(profile.clamp(0, 2)).unwrap_or(1);
    match fan.profiles.get(index) {
        Some(slot) if cpu => &slot.cpu_points,
        Some(slot) => &slot.gpu_points,
        None => &[],
    }
}

pub(super) fn curve_points_mut(fan: &mut FanConfig, profile: i32, cpu: bool) -> &mut Vec<FanPoint> {
    let index = usize::try_from(profile.clamp(0, 2)).unwrap_or(1);
    let slot = fan.profiles.get_mut(index).expect("three fan profiles");
    if cpu { &mut slot.cpu_points } else { &mut slot.gpu_points }
}

pub(super) fn power_subtitle(model: &Model) -> String {
    if !model.state.power.enabled {
        return "Limits off".into();
    }
    if model.host.intel {
        format!("PL1 {} W · PL2 {} W", model.state.power.slow_limit / 1000, model.state.power.fast_limit / 1000)
    } else {
        format!("STAPM {} W", model.state.power.stapm_limit / 1000)
    }
}

pub(super) fn light_subtitle(model: &Model) -> String {
    if !model.state.lighting.enabled {
        return "Off".into();
    }
    let name = title_effect(&model.state.lighting.effect);
    let zones = model.state.lighting.zone_colors.len();
    if zones > 1 {
        format!("{name} · {zones} zones")
    } else {
        name
    }
}

pub(super) fn title_effect(effect: &str) -> String {
    effect.split(['_', ' ']).filter(|part| !part.is_empty()).map(|part| {
        let mut chars = part.chars();
        chars.next().map_or_else(String::new, |first| first.to_ascii_uppercase().to_string() + chars.as_str())
    }).collect::<Vec<_>>().join(" ")
}

pub(super) fn mark_key(buttons: &[gtk4::Button], keys: &[String], key: &str) {
    widgets::mark(buttons, keys.iter().position(|item| item == key));
}

pub(super) fn configure_scale(scale: &gtk4::Scale, lower: f64, upper: f64, step: f64) {
    let adjustment = scale.adjustment();
    // Raise the ceiling first. Setting a floor above the current ceiling clamps
    // the value to that floor and the coupled slider copies it.
    if upper > adjustment.upper() {
        adjustment.set_upper(upper);
    }
    adjustment.set_lower(lower);
    adjustment.set_upper(upper);
    adjustment.set_step_increment(step);
    adjustment.set_page_increment(step);
}

/// CPU limits are kHz on the scale and MHz on the spin, matching the Qt rows.
/// Callers block scale and spin signals first so the new floor is not copied across.
pub(super) fn program_frequency_slider(slider: &widgets::Slider, lower: i32, upper: i32, khz: i32) {
    configure_scale(&slider.scale, f64::from(lower), f64::from(upper), 1000.0);
    let mhz_lower = f64::from(lower) / 1000.0;
    let mhz_upper = f64::from(upper) / 1000.0;
    let spin = slider.value.adjustment();
    if mhz_upper > spin.upper() {
        spin.set_upper(mhz_upper);
    }
    spin.set_lower(mhz_lower);
    spin.set_upper(mhz_upper);
    slider.scale.set_value(f64::from(khz));
    slider.value.set_value(f64::from(khz) / 1000.0);
}

/// Power, Tctl, and reapply rows keep a fixed range. Callers suppress signals
/// first. The scale handler then skips the spin, so the readout would stay on
/// the constructor floor (15 W, 75 °C, 1 s) unless this assigns both widgets.
pub(super) fn program_power_slider(slider: &widgets::Slider, value: f64) {
    slider.scale.set_value(value);
    slider.value.set_value(value / slider.divisor);
}
