use std::fs;

use super::*;
use crate::offline_scratch;

fn write_config(dir: &std::path::Path) {
    fs::create_dir_all(dir.join(".config/victus-hub")).expect("config dir");
    fs::write(dir.join(".config/victus-hub/config.json"), "{}\n").expect("config.json");
}

#[test]
fn override_dir_wins_and_an_empty_override_is_ignored() {
    let root = offline_scratch("config-override");
    let custom = root.join("custom");
    fs::create_dir_all(&custom).unwrap();
    fs::write(
        custom.join("victus-hub.conf"),
        "[power]\nsave_on_battery=true\n",
    )
    .unwrap();
    let home = root.join("home/alice");
    write_config(&home);
    let homes = root.join("home");
    assert_eq!(
        desktop_config_dir(Some(&custom), Some(&home), &homes),
        Some(custom)
    );
    let empty = root.join("empty");
    fs::create_dir_all(&empty).unwrap();
    assert_eq!(desktop_config_dir(Some(&empty), Some(&home), &homes), None);
}

#[test]
fn seat_home_is_used_when_it_has_settings() {
    let root = offline_scratch("config-seat");
    let home = root.join("home/alice");
    write_config(&home);
    let homes = root.join("home");
    let expected = home.join(".config/victus-hub");
    assert_eq!(
        desktop_config_dir(None, Some(&home), &homes),
        Some(expected)
    );
}

#[test]
fn active_seat_without_settings_does_not_take_another_home() {
    let root = offline_scratch("config-seat-empty");
    let seat = root.join("home/active");
    fs::create_dir_all(&seat).unwrap();
    write_config(&root.join("home/other"));
    assert_eq!(
        desktop_config_dir(None, Some(&seat), &root.join("home")),
        None
    );
}

#[test]
fn only_one_home_config_is_used_before_login() {
    let root = offline_scratch("config-one-home");
    let alice = root.join("home/alice");
    write_config(&alice);
    fs::create_dir_all(root.join("home/bob")).unwrap();
    fs::create_dir_all(root.join("home/lost+found")).unwrap();
    let expected = alice.join(".config/victus-hub");
    assert_eq!(
        desktop_config_dir(None, None, &root.join("home")),
        Some(expected)
    );
}

#[test]
fn several_home_configs_are_left_alone() {
    let root = offline_scratch("config-two-homes");
    write_config(&root.join("home/alice"));
    write_config(&root.join("home/bob"));
    assert_eq!(desktop_config_dir(None, None, &root.join("home")), None);
}
