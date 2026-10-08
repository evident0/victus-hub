use super::*;

#[test]
fn qt_dictionary_and_ini_groups_migrate_without_qt() {
    // Qt_4_0 QVariantMap containing key=149 and mods=QVariantList(29).
    let raw = r"[General]
programShortcut=@Variant(\0\0\0\x8\0\0\0\x2\0\0\0\x6\0k\0\x65\0y\0\0\0\x2\0\0\0\x95\0\0\0\x8\0m\0o\0\x64\0s\0\0\0\x9\0\0\0\x1\0\0\0\x2\0\0\0\x1d)
[power]
save_on_battery=true
[intelPowerLimits]
enabled=true
pl1=30000
pl2=40000
[cpuFrequency]
limits=1400000, 5000000
";
    let values = settings(raw);
    assert_eq!(values["programShortcut"]["key"], 149);
    assert_eq!(values["programShortcut"]["mods"], json!([29]));
    assert_eq!(crate::program_shortcut_from_conf(raw), (vec![29], 149));
    let disabled = raw.replacen(r"@Variant(\0\0\0\x8\0\0\0\x2",
        r"@Variant(\0\0\0\x8\0\0\0\x3\0\0\0\xe\0\x65\0n\0\x61\0\x62\0l\0\x65\0\x64\0\0\0\x1\0", 1);
    assert_eq!(settings(&disabled)["programShortcut"]["enabled"], false);
    assert_eq!(crate::program_shortcut_from_conf(&disabled), (Vec::new(), 0));
    let state = migrated_state(raw, None, true).unwrap();
    assert!(state.battery_power_save);
    assert_eq!(state.cpu_frequency, Some((1_400_000, 5_000_000)));
    assert_eq!((state.power.slow_limit, state.power.fast_limit), (30000, 40000));
    assert!(settings("[General]\nprogramShortcut=@Variant(\\0)").is_empty());
}
