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

#[test]
fn four_zone_effects_breathing_and_steps_match_python() {
    let multi: Vec<_> = effects_for_zone_count(4).iter().map(|(value, _)| *value).collect();
    assert_eq!(multi[0], "static");
    assert!(multi.contains(&"wave") && multi.contains(&"wave_rainbow") && multi.contains(&"chase"));
    assert!(!multi[1..].contains(&"static"));
    assert_eq!(spatial_index(0, 1), 0);

    let peak = compute_anim_color("breathing", std::f64::consts::FRAC_PI_2, 0, 1, 200, 100, 50, 0, 0, 0);
    assert_eq!((peak.red, peak.green, peak.blue), (200, 100, 50));
    let left = compute_anim_color("wave_rainbow", 1.2, 0, 4, 0, 0, 0, 0, 0, 0);
    let right = compute_anim_color("wave_rainbow", 1.2, 1, 4, 0, 0, 0, 0, 0, 0);
    assert_ne!((left.red, left.green, left.blue), (right.red, right.green, right.blue));

    let breathing = LightingSettings {
        enabled: true,
        effect: "breathing".to_owned(),
        color: "#ff0000".to_owned(),
        zone_colors: vec!["#c80000".to_owned(), "#00c800".to_owned(), "#0000c8".to_owned(), "#c8c800".to_owned()],
        ..LightingSettings::default()
    };
    let frames = lighting_frames(&breathing, 4, 0.0);
    assert_eq!(frames[0], RgbColor::new(100, 0, 0));
    assert_eq!(frames[1], RgbColor::new(0, 100, 0));

    let static_frames = lighting_frames(
        &LightingSettings {
            zone_colors: vec!["#ff0000".to_owned(), "#00ff00".to_owned(), "#0000ff".to_owned(), "#ffffff".to_owned()],
            ..LightingSettings::default()
        },
        4,
        0.0,
    );
    assert_eq!(
        static_frames,
        vec![
            RgbColor::new(255, 0, 0),
            RgbColor::new(0, 255, 0),
            RgbColor::new(0, 0, 255),
            RgbColor::new(255, 255, 255),
        ]
    );

    let mut settings = LightingSettings { enabled: false, brightness: 0, effect: "static".to_owned(), ..LightingSettings::default() };
    settings = step_brightness_settings(&settings, 1);
    assert!(settings.enabled);
    assert_eq!(settings.brightness, 64);
    let mut bright = LightingSettings { enabled: true, brightness: 0, ..LightingSettings::default() };
    let mut climbed = Vec::new();
    for _ in 0..6 {
        bright = step_brightness_settings(&bright, 1);
        climbed.push(bright.brightness);
    }
    assert_eq!(climbed, vec![64, 128, 191, 255, 255, 255]);
    bright.brightness = 100;
    assert_eq!(step_brightness_settings(&bright, 1).brightness, 128);

    let mut effect = LightingSettings { enabled: true, effect: "static".to_owned(), ..LightingSettings::default() };
    effect = step_effect_settings(&effect, -1, 4);
    assert!(!effect.enabled);
    effect = step_effect_settings(&effect, 1, 4);
    assert!(effect.enabled);
    assert_eq!(effect.effect, "static");

    let stored = lighting_to_value(&LightingSettings {
        enabled: true,
        effect: "wave".to_owned(),
        brightness: 128,
        zone_colors: vec!["#112233".to_owned(), "#445566".to_owned()],
        ..LightingSettings::default()
    });
    let loaded = lighting_from_value(&stored);
    assert!(loaded.enabled);
    assert_eq!(loaded.effect, "wave");
    assert_eq!(loaded.brightness, 128);
    assert_eq!(loaded.zone_colors, vec!["#112233".to_owned(), "#445566".to_owned()]);
}
