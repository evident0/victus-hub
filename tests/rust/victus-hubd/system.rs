use super::*;
use victus_core::offline_scratch;

#[test]
fn battery_telemetry_does_not_trigger_profile_queries() {
    use gio::glib::variant::ToVariant;
    let parameters = |key: &str| ("org.freedesktop.UPower", std::collections::HashMap::from([(key.to_owned(), true.to_variant())]), Vec::<String>::new()).to_variant();
    assert!(relevant_host_properties("/org/freedesktop/UPower", &parameters("OnBattery")));
    assert!(!relevant_host_properties("/org/freedesktop/UPower", &parameters("Percentage")));
    assert!(!relevant_host_properties("/org/freedesktop/UPower/devices/battery_BAT0", &parameters("Percentage")));
    assert!(relevant_host_properties("/net/hadess/PowerProfiles", &parameters("ActiveProfile")));
    let invalidated = ("net.hadess.PowerProfiles", std::collections::HashMap::<String, gio::glib::Variant>::new(), vec!["ActiveProfile"]).to_variant();
    assert!(relevant_host_properties("/net/hadess/PowerProfiles", &invalidated));
}

#[test]
fn host_readers_use_the_scratch_tree() {
    let root = offline_scratch("host");
    assert!(root.starts_with(std::env::temp_dir()));
    let tuned = root.join("active_profile");
    std::fs::write(&tuned, "performance\n").unwrap();
    assert_eq!(profile_from_active_file(&tuned), Some(2));
    assert!(!cpu_is_intel(&root.join("missing-cpuinfo")));
    std::fs::write(root.join("cpuinfo"), "vendor_id : AuthenticAMD\n").unwrap();
    assert!(!cpu_is_intel(&root.join("cpuinfo")));
    let supply = root.join("power_supply/AC");
    std::fs::create_dir_all(&supply).unwrap();
    std::fs::write(supply.join("type"), "Mains\n").unwrap();
    std::fs::write(supply.join("online"), "0\n").unwrap();
    assert_eq!(ac_online(&root.join("power_supply")), Some(false));
    let pci = root.join("pci/0000:01:00.0");
    std::fs::create_dir_all(&pci).unwrap();
    std::fs::write(pci.join("vendor"), "0x10de\n").unwrap();
    std::fs::write(pci.join("class"), "0x030200\n").unwrap();
    std::fs::create_dir_all(pci.join("power")).unwrap();
    std::fs::write(pci.join("power/runtime_status"), "active\n").unwrap();
    let status = find_nvidia_runtime(&root.join("pci")).unwrap();
    assert!(status.starts_with(&root));
    assert!(status.ends_with("runtime_status"));
    let (program, args, _) = profile_command(0, Some(Path::new("/usr/sbin/tuned-adm")), &["powersave".into()], None).unwrap();
    assert_eq!(args, vec!["profile".to_owned(), "powersave".to_owned()]);
    assert_eq!(program, Path::new("/usr/sbin/tuned-adm"));
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn ac_online_follows_mains_then_battery_status() {
    let root = offline_scratch("ac-online");
    let supply = root.join("power");
    std::fs::create_dir_all(supply.join("AC")).unwrap();
    std::fs::write(supply.join("AC/type"), "Mains\n").unwrap();
    std::fs::write(supply.join("AC/online"), "1\n").unwrap();
    assert_eq!(ac_online(&supply), Some(true));
    std::fs::remove_dir_all(supply.join("AC")).unwrap();
    std::fs::create_dir_all(supply.join("BAT0")).unwrap();
    std::fs::write(supply.join("BAT0/type"), "Battery\n").unwrap();
    for (status, expected) in [("Discharging", Some(false)), ("Full", Some(true)), ("Charging", Some(true)), ("Not charging", Some(true)), ("Unknown", None)] {
        std::fs::write(supply.join("BAT0/status"), status).unwrap();
        assert_eq!(ac_online(&supply), expected, "{status}");
    }
    std::fs::write(root.join("cpuinfo"), "vendor_id : GenuineIntel\n").unwrap();
    assert!(cpu_is_intel(&root.join("cpuinfo")));
    let _ = std::fs::remove_dir_all(root);
}
