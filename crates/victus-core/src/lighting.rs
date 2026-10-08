use serde_json::Value;

pub const ZONE_NAMES: [&str; 4] = ["Right", "Center", "Left", "WASD"];
pub const DEFAULT_COLOR: &str = "#35baf2";
pub const DEFAULT_COLOR2: &str = "#0000ff";
pub const DEFAULT_SPEED: i32 = 50;
pub const STATIC_INTERVAL_MS: f64 = 200.0;
pub const ANIM_INTERVAL_MS: f64 = 50.0;
pub const BRIGHTNESS_STEPS: [i32; 5] = [0, 64, 128, 191, 255];

/// Hardware zone index to left-to-right animation order.
const ZONE_SPATIAL_LTR: [usize; 4] = [3, 2, 0, 1];
const EFFECTS_SINGLE: &[(&str, &str)] = &[
    ("static", "Static"),
    ("breathing", "Breathing"),
    ("cycle", "Color Cycle"),
];
const EFFECTS_MULTI: &[(&str, &str)] = &[
    ("static", "Static"),
    ("breathing", "Breathing"),
    ("blinking", "Blinking"),
    ("cycle", "Color Cycle"),
    ("wave", "Wave"),
    ("wave_rainbow", "Wave Rainbow"),
    ("chase", "Chase"),
    ("sparkle", "Sparkle"),
    ("candle", "Candle"),
    ("aurora", "Aurora"),
    ("disco", "Disco"),
    ("gradient", "Gradient"),
];
const PER_ZONE: &[&str] = &["breathing", "pulse", "blinking", "chase", "sparkle", "candle"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RgbColor {
    pub red: u8,
    pub green: u8,
    pub blue: u8,
}

impl RgbColor {
    pub const fn new(red: u8, green: u8, blue: u8) -> Self {
        Self { red, green, blue }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LightingSettings {
    pub enabled: bool,
    pub effect: String,
    pub color: String,
    pub color2: String,
    pub speed: i32,
    pub zone_colors: Vec<String>,
    pub idle_timeout: i32,
    pub brightness: i32,
}

impl Default for LightingSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            effect: "static".to_owned(),
            color: DEFAULT_COLOR.to_owned(),
            color2: DEFAULT_COLOR2.to_owned(),
            speed: DEFAULT_SPEED,
            zone_colors: Vec::new(),
            idle_timeout: 0,
            brightness: 255,
        }
    }
}

pub fn hex_to_rgb(hex_str: &str) -> RgbColor {
    let digits = hex_str.trim().trim_start_matches('#');
    let value = u32::from_str_radix(digits, 16).unwrap_or(0);
    RgbColor::new(((value >> 16) & 0xff) as u8, ((value >> 8) & 0xff) as u8, (value & 0xff) as u8)
}

pub fn rgb_to_hex(color: RgbColor) -> String {
    format!("#{:02x}{:02x}{:02x}", color.red, color.green, color.blue)
}

fn valid_hex(color: &str, fallback: &str) -> String {
    let bytes = color.as_bytes();
    if bytes.len() == 7
        && bytes[0] == b'#'
        && bytes[1..].iter().all(|byte| byte.is_ascii_hexdigit())
    {
        color.to_owned()
    } else {
        fallback.to_owned()
    }
}

pub fn effects_for_zone_count(zone_count: i32) -> &'static [(&'static str, &'static str)] {
    if zone_count <= 1 { EFFECTS_SINGLE } else { EFFECTS_MULTI }
}

fn effect_alias(effect: &str) -> &str {
    match effect {
        "pulse" => "breathing",
        "rainbow" => "wave_rainbow",
        "color-cycle" => "cycle",
        "strobe" => "blinking",
        _ => effect,
    }
}

fn known_effect(effect: &str) -> bool {
    EFFECTS_SINGLE.iter().chain(EFFECTS_MULTI).any(|(value, _)| *value == effect)
}

pub fn normalize_effect(effect: &str, zone_count: i32) -> String {
    let lowered = effect.trim().to_ascii_lowercase();
    let raw = effect_alias(&lowered);
    let allowed = effects_for_zone_count(zone_count);
    if allowed.iter().any(|(value, _)| *value == raw) {
        return raw.to_owned();
    }
    if known_effect(raw) {
        return "static".to_owned();
    }
    "static".to_owned()
}

pub fn normalize_zone_colors(color: &str, zone_colors: &[String], zone_count: i32) -> Vec<String> {
    let primary = valid_hex(color, DEFAULT_COLOR);
    if zone_count <= 1 {
        return vec![primary];
    }
    (0..zone_count)
        .map(|index| {
            zone_colors.get(index as usize).map(|value| valid_hex(value, &primary)).unwrap_or_else(|| primary.clone())
        })
        .collect()
}

pub fn normalize_lighting_settings(settings: &LightingSettings, zone_count: i32) -> LightingSettings {
    let zone_count = zone_count.max(1);
    let primary = valid_hex(&settings.color, DEFAULT_COLOR);
    let zones = normalize_zone_colors(&primary, &settings.zone_colors, zone_count);
    LightingSettings {
        enabled: settings.enabled,
        effect: normalize_effect(&settings.effect, zone_count),
        color: if zone_count <= 1 { primary } else { zones[0].clone() },
        color2: valid_hex(&settings.color2, DEFAULT_COLOR2),
        speed: settings.speed.clamp(1, 100),
        zone_colors: if zone_count > 1 { zones } else { Vec::new() },
        idle_timeout: settings.idle_timeout.clamp(0, 3600),
        brightness: settings.brightness.clamp(0, 255),
    }
}

pub fn zone_for_key(label: &str, key_center_x: f64, row_width: f64) -> usize {
    if matches!(label.to_ascii_lowercase().as_str(), "w" | "a" | "s" | "d") {
        return 3;
    }
    if row_width <= 0.0 {
        return 1;
    }
    let third = row_width / 3.0;
    if key_center_x < third {
        2
    } else if key_center_x < 2.0 * third {
        1
    } else {
        0
    }
}

pub fn spatial_index(zone: usize, zone_count: i32) -> usize {
    if zone_count <= 1 {
        return 0;
    }
    ZONE_SPATIAL_LTR.get(zone).copied().unwrap_or(zone)
}

pub fn effect_is_animated(effect: &str) -> bool {
    normalize_effect(effect, 4) != "static"
}

fn u8_trunc(value: f64) -> u8 {
    if value <= 0.0 {
        0
    } else if value >= 255.0 {
        255
    } else {
        value as u8
    }
}

fn fnv_mix(eff_idx: i32, step_i: i32) -> u64 {
    let mut hash = 14_695_981_039_346_656_037_u64;
    for value in [eff_idx as u64, step_i as u64] {
        hash ^= value;
        hash = hash.wrapping_mul(1_099_511_628_211);
    }
    hash
}

pub fn compute_anim_color(
    mode: &str,
    step: f64,
    eff_idx: i32,
    zone_count: i32,
    r1: i32,
    g1: i32,
    b1: i32,
    r2: i32,
    g2: i32,
    b2: i32,
) -> RgbColor {
    let n = zone_count.max(1);
    let two_pi = std::f64::consts::TAU;
    let (r1, g1, b1, r2, g2, b2) = (f64::from(r1), f64::from(g1), f64::from(b1), f64::from(r2), f64::from(g2), f64::from(b2));
    match mode {
        "wave" => {
            let phase = step + (f64::from(eff_idx) * (two_pi / f64::from(n)));
            let factor = (phase.sin() * 0.5) + 0.5;
            let inv = 1.0 - factor;
            RgbColor::new(u8_trunc(r1 * factor + r2 * inv), u8_trunc(g1 * factor + g2 * inv), u8_trunc(b1 * factor + b2 * inv))
        }
        "rainbow" | "wave_rainbow" => {
            let hue = step + (f64::from(eff_idx) * (two_pi / f64::from(n)));
            rainbow(hue)
        }
        "cycle" => rainbow(step),
        "breathing" | "pulse" => {
            let factor = (step.sin() * 0.5) + 0.5;
            RgbColor::new(u8_trunc(r1 * factor), u8_trunc(g1 * factor), u8_trunc(b1 * factor))
        }
        "blinking" => {
            let phase = step.rem_euclid(two_pi);
            let factor = if phase < std::f64::consts::PI { 1.0 } else { 0.0 };
            RgbColor::new(u8_trunc(r1 * factor), u8_trunc(g1 * factor), u8_trunc(b1 * factor))
        }
        "chase" => {
            let pos = ((step * 2.0) as i32).rem_euclid(n);
            let factor = if eff_idx == pos { 1.0 } else { 0.15 };
            RgbColor::new(u8_trunc(r1 * factor), u8_trunc(g1 * factor), u8_trunc(b1 * factor))
        }
        "sparkle" => {
            let val = fnv_mix(eff_idx, step as i32);
            let factor = if val % 4 == 0 { 0.1 + (val % 90) as f64 / 100.0 } else { 0.2 };
            RgbColor::new(u8_trunc(r1 * factor), u8_trunc(g1 * factor), u8_trunc(b1 * factor))
        }
        "candle" => {
            let noise = (step.sin() * 0.3) + ((step * 2.3).sin() * 0.15);
            let factor = (0.6 + noise).clamp(0.3, 1.0);
            RgbColor::new(u8_trunc(r1 * factor), u8_trunc(g1 * 0.6 * factor), u8_trunc(b1 * 0.2 * factor))
        }
        "aurora" => {
            let hs = (step * 0.3) + (f64::from(eff_idx) * 0.5);
            RgbColor::new(
                u8_trunc((hs.sin() * 40.0) + 40.0),
                u8_trunc(((hs + 1.0).cos() * 100.0) + 120.0),
                u8_trunc(((hs + 2.0).sin() * 90.0) + 140.0),
            )
        }
        "disco" => {
            let beat = (step * 1.5) as i32;
            let seed = (beat as u64).wrapping_add(eff_idx as u64);
            let lcg = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
            RgbColor::new((lcg >> 56) as u8, (lcg >> 48) as u8, (lcg >> 40) as u8)
        }
        "gradient" => {
            let blend = ((step + f64::from(eff_idx) * 0.7).sin() * 0.5) + 0.5;
            let inv = 1.0 - blend;
            RgbColor::new(u8_trunc(r1 * inv + r2 * blend), u8_trunc(g1 * inv + g2 * blend), u8_trunc(b1 * inv + b2 * blend))
        }
        _ => RgbColor::new(u8_trunc(r1), u8_trunc(g1), u8_trunc(b1)),
    }
}

fn rainbow(hue: f64) -> RgbColor {
    let two_pi = std::f64::consts::TAU;
    RgbColor::new(
        u8_trunc((hue.sin() * 127.0) + 128.0),
        u8_trunc(((hue + two_pi / 3.0).sin() * 127.0) + 128.0),
        u8_trunc(((hue + 4.0 * two_pi / 3.0).sin() * 127.0) + 128.0),
    )
}

pub fn lighting_frames(settings: &LightingSettings, zone_count: i32, step: f64) -> Vec<RgbColor> {
    let n = zone_count.max(1);
    let normalized = normalize_lighting_settings(settings, n);
    let hexes = normalize_zone_colors(&normalized.color, &normalized.zone_colors, n);
    if !normalized.enabled {
        return vec![RgbColor::new(0, 0, 0); n as usize];
    }
    if normalized.effect == "static" {
        return hexes.iter().map(|hex| hex_to_rgb(hex)).collect();
    }
    let primary = hex_to_rgb(&hexes[0]);
    let secondary = hex_to_rgb(&normalized.color2);
    (0..n)
        .map(|zone| {
            let spatial = spatial_index(zone as usize, n) as i32;
            let source = if PER_ZONE.contains(&normalized.effect.as_str()) {
                hex_to_rgb(&hexes[zone as usize])
            } else {
                primary
            };
            compute_anim_color(
                &normalized.effect,
                step,
                spatial,
                n,
                i32::from(source.red),
                i32::from(source.green),
                i32::from(source.blue),
                i32::from(secondary.red),
                i32::from(secondary.green),
                i32::from(secondary.blue),
            )
        })
        .collect()
}

pub fn step_increment(speed: i32, dt: f64) -> f64 {
    dt * (f64::from(speed.clamp(1, 100)) / 20.0)
}

pub fn lighting_to_value(settings: &LightingSettings) -> Value {
    let mut value = serde_json::json!({
        "enabled": settings.enabled,
        "effect": settings.effect,
        "color": settings.color,
        "color2": settings.color2,
        "speed": settings.speed,
        "idle_timeout": settings.idle_timeout,
        "brightness": settings.brightness,
    });
    if !settings.zone_colors.is_empty() {
        value["zone_colors"] = serde_json::json!(settings.zone_colors);
    }
    value
}

pub fn lighting_from_value(raw: &Value) -> LightingSettings {
    let Some(raw) = raw.as_object() else {
        return LightingSettings::default();
    };
    if raw.is_empty() {
        return LightingSettings::default();
    }
    let text = |key: &str, fallback: &str| {
        raw.get(key)
            .and_then(Value::as_str)
            .unwrap_or(fallback)
            .replace(['\n', '\t'], "")
    };
    let number = |key: &str, fallback: i32| raw.get(key).and_then(Value::as_i64).map(|value| value as i32).unwrap_or(fallback);
    let zone_colors = raw
        .get("zone_colors")
        .and_then(Value::as_array)
        .map(|items| {
            items.iter().map(|item| item.as_str().unwrap_or("").replace(['\n', '\t'], "")).collect()
        })
        .unwrap_or_default();
    LightingSettings {
        enabled: raw.get("enabled").and_then(Value::as_bool).unwrap_or(true),
        effect: text("effect", "static"),
        color: text("color", DEFAULT_COLOR),
        color2: text("color2", DEFAULT_COLOR2),
        speed: number("speed", DEFAULT_SPEED),
        zone_colors,
        idle_timeout: number("idle_timeout", 0),
        brightness: number("brightness", 255),
    }
}

pub fn step_brightness_settings(settings: &LightingSettings, direction: i32) -> LightingSettings {
    let current = if settings.enabled { settings.brightness } else { 0 };
    let level = if direction > 0 {
        BRIGHTNESS_STEPS.into_iter().find(|value| *value > current).unwrap_or(255)
    } else {
        BRIGHTNESS_STEPS.into_iter().rev().find(|value| *value < current).unwrap_or(0)
    };
    LightingSettings {
        brightness: level,
        enabled: settings.enabled || level > 0,
        ..settings.clone()
    }
}

pub fn step_effect_settings(settings: &LightingSettings, direction: i32, zone_count: i32) -> LightingSettings {
    let mut items = vec!["off"];
    items.extend(effects_for_zone_count(zone_count).iter().map(|(value, _)| *value));
    let current = if settings.enabled { settings.effect.as_str() } else { "off" };
    let index = items.iter().position(|value| *value == current);
    let index = match index {
        Some(index) => (index as i32 + direction).rem_euclid(items.len() as i32) as usize,
        None if direction > 0 => 0,
        None => items.len() - 1,
    };
    let effect = items[index];
    if effect == "off" {
        LightingSettings { enabled: false, ..settings.clone() }
    } else {
        LightingSettings { enabled: true, effect: effect.to_owned(), ..settings.clone() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn effects_aliases_and_spatial_order() {
        let single: Vec<_> = effects_for_zone_count(1).iter().map(|(value, _)| *value).collect();
        assert_eq!(single, vec!["static", "breathing", "cycle"]);
        assert_eq!(normalize_effect("color-cycle", 1), "cycle");
        assert_eq!(normalize_effect("pulse", 4), "breathing");
        assert_eq!(normalize_effect("strobe", 4), "blinking");
        assert_eq!(normalize_effect("wave", 1), "static");
        assert_eq!(spatial_index(2, 4), 0);
        assert_eq!(spatial_index(3, 4), 1);
        assert_eq!(spatial_index(1, 4), 2);
        assert_eq!(spatial_index(0, 4), 3);
    }

    #[test]
    fn animation_colors_match_the_python_cases() {
        let color = compute_anim_color("static", 1.0, 0, 4, 10, 20, 30, 1, 2, 3);
        assert_eq!((color.red, color.green, color.blue), (10, 20, 30));
        let color = compute_anim_color("breathing", 0.0, 0, 1, 200, 100, 50, 0, 0, 0);
        assert_eq!((color.red, color.green, color.blue), (100, 50, 25));
        let on = compute_anim_color("blinking", 0.0, 0, 1, 255, 0, 0, 0, 0, 0);
        let off = compute_anim_color("blinking", std::f64::consts::PI, 0, 1, 255, 0, 0, 0, 0, 0);
        assert_eq!((on.red, on.green, on.blue), (255, 0, 0));
        assert_eq!((off.red, off.green, off.blue), (0, 0, 0));
        let a = compute_anim_color("cycle", 1.2, 0, 4, 0, 0, 0, 0, 0, 0);
        let b = compute_anim_color("cycle", 1.2, 3, 4, 0, 0, 0, 0, 0, 0);
        assert_eq!((a.red, a.green, a.blue), (b.red, b.green, b.blue));
        let wave = compute_anim_color("wave", 0.0, 0, 1, 255, 0, 0, 0, 0, 255);
        assert_eq!((wave.red, wave.green, wave.blue), (127, 0, 127));
        let hot = compute_anim_color("chase", 0.0, 0, 4, 200, 0, 0, 0, 0, 0);
        let dim = compute_anim_color("chase", 0.0, 1, 4, 200, 0, 0, 0, 0, 0);
        assert_eq!((hot.red, hot.green, hot.blue), (200, 0, 0));
        assert_eq!((dim.red, dim.green, dim.blue), (30, 0, 0));
    }

    #[test]
    fn frames_use_zone_colors_and_speed() {
        let settings = LightingSettings {
            color: "#ff0000".to_owned(),
            zone_colors: vec!["#ff0000".to_owned(), "#00ff00".to_owned(), "#0000ff".to_owned(), "#ffffff".to_owned()],
            ..LightingSettings::default()
        };
        let frames = lighting_frames(&settings, 4, 0.0);
        assert_eq!(frames[1], RgbColor::new(0, 255, 0));
        let off = LightingSettings { enabled: false, effect: "breathing".to_owned(), ..LightingSettings::default() };
        assert!(lighting_frames(&off, 4, 1.0).iter().all(|color| *color == RgbColor::new(0, 0, 0)));
        assert!((step_increment(50, 0.050) - 0.125).abs() < 1e-12);
        assert_eq!(zone_for_key("W", 10.0, 15.0), 3);
        assert_eq!(zone_for_key("p", 1.0, 15.0), 2);
    }
}
