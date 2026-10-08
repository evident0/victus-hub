use super::*;
use victus_core::offline_scratch;

#[test]
fn requested_keys_read_only_the_scratch_tree() {
    let root = offline_scratch("sensors");
    let hwmon = root.join("hwmon0");
    fs::create_dir_all(hwmon.join("device")).unwrap();
    fs::write(hwmon.join("name"), "k10temp\n").unwrap();
    fs::write(hwmon.join("temp1_input"), "48500\n").unwrap();
    let hp = root.join("hwmon1");
    fs::create_dir_all(&hp).unwrap();
    fs::write(hp.join("name"), "hp-wmi\n").unwrap();
    fs::write(hp.join("fan1_input"), "2100\n").unwrap();
    fs::write(hp.join("fan2_input"), "0\n").unwrap();
    fs::write(hp.join("pwm1_enable"), "2\n").unwrap();
    fs::write(hp.join("pwm1"), "40\n").unwrap();
    let stat = root.join("stat");
    fs::write(&stat, "cpu 10 0 10 70 0 0 0 0\n").unwrap();
    let mem = root.join("meminfo");
    fs::write(&mem, "MemTotal: 8000000 kB\nMemAvailable: 4000000 kB\n").unwrap();
    let mut sampler = SensorSampler::default();
    let keys = vec!["cpu-temp".into(), "cpu-fan".into(), "ram-usage".into()];
    let snap = sampler.read(&keys, &root, &stat, &mem, &root, None, None, false, false, false);
    assert!(snap.cpu_temp.value.starts_with("48.5"));
    assert_eq!(snap.cpu_fan.value, "2100 RPM");
    assert!(snap.gpu_fan.value.starts_with('0'));
    assert!(snap.ram_used_gb.unwrap() > 3.0);
    let empty = sampler.read(&[], &root, &stat, &mem, &root, None, None, false, false, false);
    assert_eq!(empty.cpu_temp.value, "0 C");
    let _ = fs::remove_dir_all(root);
}

#[test]
fn extra_sensor_identity_and_average_current_inputs_are_preserved() {
    let root = offline_scratch("extra-sensors");
    for (directory, current, temperature) in [("hwmon0", "1500", "42000"), ("hwmon1", "2500", "47000")] {
        let chip = root.join(directory);
        fs::create_dir_all(&chip).unwrap();
        fs::write(chip.join("name"), "nvme\n").unwrap();
        fs::write(chip.join("temp1_input"), temperature).unwrap();
        fs::write(chip.join("curr1_input"), current).unwrap();
        fs::write(chip.join("power1_average"), "12000000").unwrap();
        fs::write(chip.join("temp2_input"), "-128000").unwrap();
    }
    let extras = read_lm(&root, false);
    assert_eq!(extras.len(), 6);
    let keys = extras.iter().map(|sensor| &sensor.key).collect::<std::collections::HashSet<_>>();
    assert_eq!(keys.len(), extras.len());
    assert_eq!(extras.iter().filter(|sensor| sensor.unit == "A").count(), 2);
    assert_eq!(extras.iter().filter(|sensor| sensor.unit == "W" && sensor.numeric_value == 12.0).count(), 2);
    let amd = root.join("hwmon2");
    fs::create_dir_all(&amd).unwrap();
    fs::write(amd.join("name"), "amdgpu").unwrap();
    fs::write(amd.join("power1_average"), "23000000").unwrap();
    assert_eq!(read_gpu_power(&root).value, "23.0 W");
    let _ = fs::remove_dir_all(root);
}

#[test]
fn cpu_temp_skips_gpu_hwmon_and_nvidia_fallback_order() {
    let root = offline_scratch("cpu-temp-skip");
    for (name, directory) in [("nvidia", "hwmon0"), ("amdgpu", "hwmon1")] {
        let hwmon = root.join(directory);
        fs::create_dir_all(&hwmon).unwrap();
        fs::write(hwmon.join("name"), format!("{name}\n")).unwrap();
        fs::write(hwmon.join("temp1_input"), "99000\n").unwrap();
        assert_eq!(cpu_temp_c(&root), None, "{name}");
        let _ = fs::remove_dir_all(&hwmon);
    }
    let cpu = root.join("hwmon0");
    let gpu = root.join("hwmon1");
    fs::create_dir_all(&cpu).unwrap();
    fs::create_dir_all(&gpu).unwrap();
    fs::write(cpu.join("name"), "k10temp\n").unwrap();
    fs::write(cpu.join("temp1_input"), "45000\n").unwrap();
    fs::write(gpu.join("name"), "nvidia\n").unwrap();
    fs::write(gpu.join("temp1_input"), "66000\n").unwrap();
    assert_eq!(cpu_temp_c(&root), Some(45.0));

    let stat = root.join("stat");
    let mem = root.join("meminfo");
    fs::write(&stat, "cpu 1 0 1 1 0 0 0 0\n").unwrap();
    fs::write(&mem, "MemTotal: 1000 kB\nMemAvailable: 1000 kB\n").unwrap();
    let mut sampler = SensorSampler::default();
    let keys = vec!["gpu-temp".into()];
    let suspended = sampler.read(&keys, &root, &stat, &mem, &root, None, None, true, false, true);
    assert_eq!(suspended.gpu_temp.value, "Suspended");
    assert!(suspended.gpu_temp_c.is_none());
    let disabled = sampler.read(&keys, &root, &stat, &mem, &root, None, None, false, true, true);
    assert_eq!(disabled.gpu_temp.value, "Disabled");
    assert!(disabled.gpu_temp_c.is_none());
    let fields = NvidiaFields { temp_c: Some(71.0), temp_source: "nvml".into(), ..NvidiaFields::default() };
    let nvml = sampler.read(&keys, &root, &stat, &mem, &root, None, Some(&fields), false, false, true);
    assert_eq!(nvml.gpu_temp_c, Some(71.0));
    assert!(nvml.gpu_temp.source.contains("nvml"));
    let hwmon = sampler.read(&keys, &root, &stat, &mem, &root, None, Some(&NvidiaFields::default()), false, false, true);
    assert_eq!(hwmon.gpu_temp_c, Some(66.0));
    let _ = fs::remove_dir_all(root);
}
