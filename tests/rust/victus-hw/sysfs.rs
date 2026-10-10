use super::*;
use victus_core::offline_scratch;

#[test]
fn fan_and_keyboard_writes_stay_in_the_scratch_tree() {
    let root = offline_scratch("sysfs");
    assert!(root.starts_with(std::env::temp_dir()));
    let hwmon = root.join("hwmon/hwmon0");
    fs::create_dir_all(&hwmon).unwrap();
    fs::write(hwmon.join("name"), "hp-wmi\n").unwrap();
    fs::write(hwmon.join("pwm1_enable"), "2\n").unwrap();
    fs::write(hwmon.join("pwm1"), "0\n").unwrap();
    fs::write(hwmon.join("pwm2"), "0\n").unwrap();
    let class = root.join("hwmon");
    let found = find_hwmon(&class, &["hp-wmi"]).unwrap();
    write_pwm(Some(found.as_path()), 128).unwrap();
    assert_eq!(read_text(&found.join("pwm1_enable")).as_deref(), Some("1"));
    assert_eq!(read_text(&found.join("pwm1")).as_deref(), Some("128"));
    assert_eq!(read_text(&found.join("pwm2")).as_deref(), Some("128"));

    let leds = root.join("leds/hp::kbd_backlight");
    fs::create_dir_all(&leds).unwrap();
    fs::write(leds.join("multi_intensity"), "0 0 0\n").unwrap();
    fs::write(leds.join("brightness"), "0\n").unwrap();
    write_led_color(&leds, 1, 2, 3, 40).unwrap();
    assert_eq!(read_text(&leds.join("multi_intensity")).as_deref(), Some("1 2 3"));
    assert!(keyboard_lighting_supported(&root.join("leds")));

    let mux = root.join("hp-wmi");
    fs::create_dir_all(&mux).unwrap();
    fs::write(mux.join("gpu_mux_supported_names"), "hybrid discrete\n").unwrap();
    fs::write(mux.join("gpu_mux_mode"), "0\n").unwrap();
    let state = read_gpu_mux(&mux).unwrap();
    assert_eq!(state.current_index, 0);
    assert_eq!(state.modes.len(), 2);
    let caps = detect_capabilities(&class, &root.join("leds"), &mux);
    assert!(caps.fan_modes.contains(&"custom".to_owned()));
    assert!(caps.keyboard_lighting);
    let _ = fs::remove_dir_all(root);
}

#[test]
fn mux_pwm_capabilities_and_zone_count_match_python() {
    let root = offline_scratch("sysfs-matrix");
    let mux = root.join("hp-wmi");
    fs::create_dir_all(&mux).unwrap();
    assert!(read_gpu_mux(&mux).is_none());
    fs::write(mux.join("gpu_mux_supported_names"), "hybrid discrete uma\n").unwrap();
    assert!(read_gpu_mux(&mux).is_none());
    for value in ["invalid", "127"] {
        fs::write(mux.join("gpu_mux_mode"), value).unwrap();
        assert!(read_gpu_mux(&mux).is_none(), "{value}");
    }
    fs::write(mux.join("gpu_mux_mode"), "3\n").unwrap();
    let state = read_gpu_mux(&mux).unwrap();
    assert_eq!(state.modes.iter().map(|mode| mode.index).collect::<Vec<_>>(), vec![0, 1, 3]);
    assert_eq!(state.current_index, 3);
    write_gpu_mux(&mux, 1).unwrap();
    assert_eq!(read_text(&mux.join("gpu_mux_mode")).as_deref(), Some("1"));

    let single = root.join("hwmon/single");
    fs::create_dir_all(&single).unwrap();
    fs::write(single.join("name"), "hp-wmi\n").unwrap();
    fs::write(single.join("pwm1_enable"), "2\n").unwrap();
    fs::write(single.join("pwm1"), "0\n").unwrap();
    write_pwm(Some(&single), 200).unwrap();
    assert_eq!(read_text(&single.join("pwm1")).as_deref(), Some("200"));
    assert!(!single.join("pwm2").exists());
    assert!(!single.join("pwm2_enable").exists());

    let broken = root.join("hwmon/broken");
    fs::create_dir_all(&broken).unwrap();
    fs::write(broken.join("pwm1_enable"), "2").unwrap();
    fs::write(broken.join("pwm1"), "0").unwrap();
    fs::create_dir(broken.join("pwm2")).unwrap();
    let error = write_pwm(Some(&broken), 180).unwrap_err();
    assert!(error.to_string().contains("pwm2"), "{error}");

    let leds = root.join("leds");
    fs::create_dir_all(leds.join("input3::capslock")).unwrap();
    fs::write(leds.join("input3::capslock/multi_intensity"), "").unwrap();
    fs::write(leds.join("input3::capslock/brightness"), "").unwrap();
    assert!(!keyboard_lighting_supported(&leds));
    let zoned = leds.join("hp::kbd_backlight_zoned_backlight-left");
    fs::create_dir_all(&zoned).unwrap();
    fs::write(zoned.join("multi_intensity"), "").unwrap();
    assert!(!keyboard_lighting_supported(&leds));
    fs::write(zoned.join("brightness"), "").unwrap();
    assert!(keyboard_lighting_supported(&leds));

    let case = |label: &str, nodes: &[&str]| {
        let class = root.join(label);
        let hwmon = class.join("hwmon0");
        fs::create_dir_all(&hwmon).unwrap();
        fs::write(hwmon.join("name"), "hp-wmi\n").unwrap();
        for node in nodes {
            fs::write(hwmon.join(node), "").unwrap();
        }
        let caps = detect_capabilities(&class, &root.join("no-leds"), &root.join("no-mux"));
        (caps.fan_modes, manual_fan_supported(Some(&hwmon)))
    };
    assert_eq!(case("none", &[]).0, vec!["auto".to_owned()]);
    assert!(!case("none", &[]).1);
    assert_eq!(case("enable", &["pwm1_enable"]).0, vec!["auto".to_owned(), "max".to_owned()]);
    assert!(!case("enable", &["pwm1_enable"]).1);
    assert_eq!(case("pwm", &["pwm1"]).0, vec!["auto".to_owned()]);
    assert_eq!(
        case("both", &["pwm1_enable", "pwm1"]).0,
        vec!["auto".to_owned(), "smart".to_owned(), "max".to_owned(), "custom".to_owned()]
    );
    assert!(case("both", &["pwm1_enable", "pwm1"]).1);
    let empty = detect_capabilities(&root.join("empty-class"), &root.join("no-leds"), &root.join("no-mux"));
    assert_eq!(empty.fan_modes, vec!["auto".to_owned()]);

    assert_eq!(keyboard_zone_count(&mux, Some(4)), 4);
    assert_eq!(keyboard_zone_count(&mux, Some(0)), 1);
    assert_eq!(keyboard_zone_count(&mux, Some(-3)), 1);
    assert_eq!(keyboard_zone_count(&mux, None), 1);
    fs::write(mux.join("zone_count"), "2\n").unwrap();
    assert_eq!(keyboard_zone_count(&mux, None), 2);
    assert_eq!(keyboard_zone_count(&mux, Some(0)), 2);
    assert_eq!(keyboard_zone_count(&mux, Some(4)), 4);
    let _ = fs::remove_dir_all(root);
}
