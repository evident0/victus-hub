use std::fs;
use std::path::{Path, PathBuf};

use victus_core::{CpuPowerSample, ExtraSensor, SensorReading, SensorSnapshot};

use crate::nvidia::{amd_or_nouveau_temp_c, nvidia_hwmon_temp_c, runtime_suspended, NvidiaFields};
use crate::sysfs::{find_hwmon, hwmon_dirs, read_int, read_text};

const CPU_NAMES: &[&str] = &["coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"];
const GPU_NAMES: &[&str] = &["nvidia", "amdgpu", "nouveau"];

#[derive(Debug, Clone, Copy, Default)]
struct CpuTicks {
    user: u64,
    nice: u64,
    system: u64,
    idle: u64,
    iowait: u64,
    irq: u64,
    softirq: u64,
    steal: u64,
}

#[derive(Debug, Default)]
pub struct SensorSampler {
    previous_ticks: Option<CpuTicks>,
}

impl SensorSampler {
    pub fn read(
        &mut self,
        keys: &[String],
        hwmon_class: &Path,
        proc_stat: &Path,
        meminfo: &Path,
        cpu_root: &Path,
        power: Option<&CpuPowerSample>,
        gpu: Option<&NvidiaFields>,
        gpu_suspended: bool,
        disable_nvidia: bool,
        nvidia_present: bool,
    ) -> SensorSnapshot {
        let mut snap = SensorSnapshot::default();
        if keys.is_empty() {
            return snap;
        }
        let wanted = |key: &str| keys.iter().any(|item| item == key);
        if wanted("cpu-temp") {
            let (reading, temp) = read_cpu_temp(hwmon_class);
            snap.cpu_temp = reading;
            snap.cpu_temp_c = temp;
        }
        if wanted("cpu-usage") {
            match self.cpu_usage(proc_stat) {
                Some(pct) => {
                    snap.cpu_usage_pct = Some(pct);
                    snap.cpu_usage = SensorReading::with_source(format!("{pct:.1} %"), "proc/stat");
                }
                None => snap.cpu_usage = SensorReading::with_source("N/A", "proc/stat"),
            }
        }
        if wanted("cpu-power") {
            snap.cpu_power = match power {
                Some(CpuPowerSample::Watts { watts, source }) => {
                    SensorReading::with_source(format!("{watts:.1} W"), format!("victus-hubd: {source}"))
                }
                Some(CpuPowerSample::Sampling { source }) => {
                    SensorReading::with_source("Sampling...", format!("victus-hubd: {source}"))
                }
                Some(CpuPowerSample::Unavailable { message }) => SensorReading::with_source("Unavailable", message.clone()),
                None => SensorReading::with_source("Unavailable", "no RAPL package"),
            };
        }
        if wanted("cpu-fan") || wanted("gpu-fan") || wanted("pwm-value") || wanted("pwm-mode") {
            let hp = find_hwmon(hwmon_class, &["hp", "hp_wmi", "hp-wmi"]);
            if wanted("cpu-fan") || wanted("gpu-fan") {
                let (cpu, gpu_fan) = read_fans(hp.as_deref());
                if wanted("cpu-fan") {
                    snap.cpu_fan = cpu;
                }
                if wanted("gpu-fan") {
                    snap.gpu_fan = gpu_fan;
                }
            }
            if wanted("pwm-mode") || wanted("pwm-value") {
                let (mode, value) = read_pwm(hp.as_deref());
                if wanted("pwm-mode") {
                    snap.pwm_mode = mode;
                }
                if wanted("pwm-value") {
                    snap.pwm_value = value;
                }
            }
        }
        if wanted("ram-usage") {
            if let Some((reading, pct, used, total)) = read_ram(meminfo) {
                snap.ram_usage = reading;
                snap.ram_usage_pct = pct;
                snap.ram_used_gb = used;
                snap.ram_total_gb = total;
            }
        }
        if wanted("cpu-frequency") {
            snap.extra_sensors.extend(read_frequencies(cpu_root));
        }
        if wanted("lm-sensors") {
            snap.extra_sensors.extend(read_lm(hwmon_class, disable_nvidia || gpu_suspended));
        }
        if keys.iter().any(|key| matches!(key.as_str(), "gpu-temp" | "gpu-usage" | "gpu-power")) {
            fill_gpu(&mut snap, keys, gpu, gpu_suspended, disable_nvidia, nvidia_present, hwmon_class);
        }
        snap
    }

    fn cpu_usage(&mut self, proc_stat: &Path) -> Option<f64> {
        let current = parse_proc_stat(proc_stat)?;
        let previous = self.previous_ticks.replace(current)?;
        let prev_idle = previous.idle + previous.iowait;
        let idle = current.idle + current.iowait;
        let prev_total = previous.user + previous.nice + previous.system + prev_idle + previous.irq + previous.softirq + previous.steal;
        let total = current.user + current.nice + current.system + idle + current.irq + current.softirq + current.steal;
        let total_delta = total.saturating_sub(prev_total);
        if total_delta == 0 {
            return None;
        }
        let idle_delta = idle.saturating_sub(prev_idle);
        Some(100.0 * (1.0 - idle_delta as f64 / total_delta as f64))
    }
}

pub fn cpu_temp_c(hwmon_class: &Path) -> Option<f64> {
    read_cpu_temp(hwmon_class).1
}

fn read_cpu_temp(hwmon_class: &Path) -> (SensorReading, Option<f64>) {
    let mut best: Option<(i64, PathBuf, String)> = None;
    for hwmon in hwmon_dirs(hwmon_class) {
        let name = read_text(&hwmon.join("name")).unwrap_or_default().to_ascii_lowercase();
        if GPU_NAMES.contains(&name.as_str()) {
            continue;
        }
        let Ok(entries) = fs::read_dir(&hwmon) else { continue };
        let preferred = CPU_NAMES.contains(&name.as_str());
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let fname = fname.to_string_lossy();
            if !(fname.starts_with("temp") && fname.ends_with("_input")) {
                continue;
            }
            let label_name = fname.replace("_input", "_label");
            let label = read_text(&hwmon.join(label_name)).unwrap_or_default();
            let label_lower = label.to_ascii_lowercase();
            if !preferred && !label_lower.contains("package") && !label_lower.contains("tctl") {
                continue;
            }
            let Some(value) = read_int(&entry.path()) else { continue };
            if value <= -100_000 {
                continue;
            }
            if best.as_ref().is_none_or(|current| value > current.0) {
                let display = if label.is_empty() { name.clone() } else { label };
                best = Some((value, entry.path(), display));
            }
        }
    }
    match best {
        None => (SensorReading::with_source("Unavailable", "no CPU temp hwmon"), None),
        Some((value, path, label)) => {
            let temp = value as f64 / 1000.0;
            (SensorReading::with_source(format!("{temp:.1} C"), format!("{label}: {}", path.display())), Some(temp))
        }
    }
}

fn read_fans(hwmon: Option<&Path>) -> (SensorReading, SensorReading) {
    let Some(hwmon) = hwmon else {
        let missing = SensorReading::with_source("Unavailable", "hp hwmon not found");
        return (missing.clone(), missing);
    };
    (rpm(hwmon, "fan1_input"), rpm(hwmon, "fan2_input"))
}

fn rpm(hwmon: &Path, name: &str) -> SensorReading {
    let path = hwmon.join(name);
    match read_int(&path) {
        Some(value) => SensorReading::with_source(format!("{value} RPM"), path.display().to_string()),
        None => SensorReading::with_source("Unavailable", path.display().to_string()),
    }
}

fn read_pwm(hwmon: Option<&Path>) -> (SensorReading, SensorReading) {
    let Some(hwmon) = hwmon else {
        let missing = SensorReading::with_source("Unavailable", "hp hwmon not found");
        return (missing.clone(), missing);
    };
    let mode_path = hwmon.join("pwm1_enable");
    let mode = match read_text(&mode_path).as_deref() {
        Some("0") => SensorReading::with_source("Max", mode_path.display().to_string()),
        Some("1") => SensorReading::with_source("Manual", mode_path.display().to_string()),
        Some("2") => SensorReading::with_source("Automatic", mode_path.display().to_string()),
        Some(other) => SensorReading::with_source(other, mode_path.display().to_string()),
        None => SensorReading::with_source("Unavailable", mode_path.display().to_string()),
    };
    let value_path = hwmon.join("pwm1");
    let value = match read_int(&value_path) {
        Some(value) => SensorReading::with_source(format!("{value} / 255"), value_path.display().to_string()),
        None => SensorReading::with_source("Unavailable", value_path.display().to_string()),
    };
    (mode, value)
}

fn read_ram(meminfo: &Path) -> Option<(SensorReading, Option<f64>, Option<f64>, Option<f64>)> {
    let text = fs::read_to_string(meminfo).ok()?;
    let mut total = None;
    let mut available = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("MemTotal:") {
            total = rest.split_whitespace().next().and_then(|value| value.parse::<f64>().ok());
        }
        if let Some(rest) = line.strip_prefix("MemAvailable:") {
            available = rest.split_whitespace().next().and_then(|value| value.parse::<f64>().ok());
        }
    }
    let total = total?;
    if total <= 0.0 { return None; }
    let available = available?;
    let used_kb = (total - available).max(0.0);
    let used_gb = used_kb / 1_048_576.0;
    let total_gb = total / 1_048_576.0;
    let pct = if total > 0.0 { Some(100.0 * used_kb / total) } else { None };
    Some((SensorReading::with_source(format!("{used_gb:.1} GB"), "proc/meminfo"), pct, Some(used_gb), Some(total_gb)))
}

fn read_frequencies(cpu_root: &Path) -> Vec<ExtraSensor> {
    let mut cpus = Vec::new();
    if let Ok(entries) = fs::read_dir(cpu_root) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if let Some(index) = name.strip_prefix("cpu") {
                if index.chars().all(|ch| ch.is_ascii_digit()) {
                    cpus.push((index.parse::<i32>().unwrap_or(0), entry.path()));
                }
            }
        }
    }
    cpus.sort_by_key(|(index, _)| *index);
    let mut sensors = Vec::new();
    for (index, cpu) in cpus {
        let freq_dir = cpu.join("cpufreq");
        let mut source = freq_dir.join("scaling_cur_freq");
        let mut value = read_int(&source).filter(|value| *value > 0);
        if value.is_none() {
            source = freq_dir.join("cpuinfo_cur_freq");
            value = read_int(&source).filter(|value| *value > 0);
        }
        let Some(value) = value else { continue };
        let mhz = value as f64 / 1000.0;
        let maximum = read_int(&freq_dir.join("cpuinfo_max_freq")).map(|value| value as f64 / 1000.0).unwrap_or(0.0);
        sensors.push(ExtraSensor {
            key: format!("cpu-frequency-{index}"),
            group: "CPU".into(),
            name: format!("CPU {index} Frequency"),
            unit: "MHz".into(),
            value_min: 0.0,
            value_max: mhz.max(if maximum > 0.0 { maximum } else { 6000.0 }),
            numeric_value: mhz,
            reading: SensorReading::with_source(format!("{mhz:.1} MHz"), source.display().to_string()),
        });
    }
    sensors
}

fn read_lm(hwmon_class: &Path, skip_nvidia: bool) -> Vec<ExtraSensor> {
    let mut sensors = Vec::new();
    for hwmon in hwmon_dirs(hwmon_class) {
        let chip = read_text(&hwmon.join("name")).unwrap_or_else(|| "hwmon".into());
        if skip_nvidia && chip == "nvidia" { continue; }
        // libsensors chip addresses distinguished, for example, two NVMe drives.
        let device = fs::canonicalize(hwmon.join("device")).ok();
        let identity = device.as_ref().and_then(|path| path.file_name()).or_else(|| hwmon.file_name()).unwrap_or_default().to_string_lossy();
        let chip_id = format!("{chip}-{identity}");
        let Ok(entries) = fs::read_dir(&hwmon) else { continue };
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let fname = fname.to_string_lossy();
            let Some(feature) = fname.strip_suffix("_input").or_else(|| fname.strip_suffix("_average")) else { continue };
            if fname.ends_with("_average") && hwmon.join(format!("{feature}_input")).exists() { continue; }
            let (unit, scale, maximum, metric) = if feature.starts_with("temp") {
                ("°C", 1000.0, 100.0, "Temp")
            } else if feature.starts_with("fan") {
                ("RPM", 1.0, 6000.0, "Fan")
            } else if feature.starts_with("power") {
                ("W", 1_000_000.0, 120.0, "Power")
            } else if feature.starts_with("in") {
                ("V", 1000.0, 20.0, "Voltage")
            } else if feature.starts_with("curr") {
                ("A", 1000.0, 10.0, "Current")
            } else {
                continue;
            };
            let Some(raw) = read_int(&entry.path()) else { continue };
            let value = raw as f64 / scale;
            if unit == "°C" && value <= -100.0 { continue; }
            let label = read_text(&hwmon.join(format!("{feature}_label"))).unwrap_or_else(|| feature.to_owned());
            if matches!(chip.as_str(), "hp" | "hp-wmi" | "hp_wmi" | "k10temp")
                || (chip == "amdgpu" && matches!(label.as_str(), "edge" | "PPT"))
                || (chip.starts_with("BAT") && unit == "W") { continue; }
            let group = if chip.starts_with("nvme") { "Drives" } else if chip.starts_with("spd") { "Memory" }
                else if chip.starts_with("mt7921") { "Network" } else if chip.starts_with("BAT") { "Battery" }
                else if chip.starts_with("ucsi_source") { "USB-C" } else if matches!(chip.as_str(), "amdgpu" | "nvidia" | "nouveau") { "GPU" }
                else if chip.starts_with("acpitz") { "ACPI" } else { "Other" };
            let slug = |text: &str| text.chars().map(|ch| if ch.is_ascii_alphanumeric() { ch.to_ascii_lowercase() } else { '-' }).collect::<String>();
            sensors.push(ExtraSensor {
                key: format!("lm-{}-{}", slug(&chip_id), slug(feature)),
                group: group.into(),
                name: format!("{label} {metric}"),
                unit: unit.into(),
                value_min: 0.0,
                value_max: value.max(maximum),
                numeric_value: value,
                reading: SensorReading::with_source(format_sensor(value, unit), format!("sensors: {chip} / {}", entry.file_name().to_string_lossy())),
            });
        }
    }
    sensors.sort_by(|left, right| (&left.group, &left.key).cmp(&(&right.group, &right.key)));
    sensors
}

fn format_sensor(value: f64, unit: &str) -> String {
    if unit == "RPM" { format!("{value:.0} {unit}") }
    else if matches!(unit, "V" | "A") { format!("{value:.2} {unit}") }
    else { format!("{value:.1} {unit}") }
}

fn fill_gpu(
    snap: &mut SensorSnapshot,
    keys: &[String],
    gpu: Option<&NvidiaFields>,
    suspended: bool,
    disable_nvidia: bool,
    present: bool,
    hwmon_class: &Path,
) {
    let wanted = |key: &str| keys.iter().any(|item| item == key);
    if present && (disable_nvidia || suspended) {
        let text = if disable_nvidia { "Disabled" } else { "Suspended" };
        let source = if disable_nvidia { "NVIDIA queries disabled in Settings" } else { "dGPU runtime PM (not woken)" };
        if wanted("gpu-temp") {
            snap.gpu_temp = SensorReading::with_source(text, source);
        }
        if wanted("gpu-power") {
            snap.gpu_power = SensorReading::with_source(text, source);
        }
        if wanted("gpu-usage") {
            snap.gpu_usage = SensorReading::with_source(text, source);
        }
        return;
    }
    if !present {
        if wanted("gpu-temp") {
            match amd_or_nouveau_temp_c(hwmon_class) {
                Some(temp) => {
                    snap.gpu_temp_c = Some(temp);
                    snap.gpu_temp = SensorReading::with_source(format!("{temp:.0} C"), "amdgpu/nouveau");
                }
                None => snap.gpu_temp = SensorReading::with_source("Unavailable", "dGPU off or unavailable"),
            }
        }
        if wanted("gpu-power") {
            snap.gpu_power = read_gpu_power(hwmon_class);
        }
        if wanted("gpu-usage") {
            snap.gpu_usage = SensorReading::with_source("N/A", "dGPU off or unavailable");
        }
        return;
    }
    let fields = gpu.cloned().unwrap_or_default();
    if wanted("gpu-temp") {
        match fields.temp_c.or_else(|| nvidia_hwmon_temp_c(hwmon_class)) {
            Some(temp) => {
                snap.gpu_temp_c = Some(temp);
                snap.gpu_temp = SensorReading::with_source(format!("{temp:.0} C"), if fields.temp_source.is_empty() { "hwmon".into() } else { fields.temp_source });
            }
            None => snap.gpu_temp = SensorReading::with_source("Unavailable", "no NVIDIA temp (hwmon/NVML/smi)"),
        }
    }
    if wanted("gpu-power") {
        match fields.power_w {
            Some(watts) => snap.gpu_power = SensorReading::with_source(format!("{watts:.1} W"), fields.power_source),
            None => snap.gpu_power = SensorReading::with_source("Unavailable", "no NVIDIA power (hwmon/NVML/smi)"),
        }
    }
    if wanted("gpu-usage") {
        match fields.util_pct {
            Some(pct) => {
                snap.gpu_usage_pct = Some(pct);
                snap.gpu_usage = SensorReading::with_source(format!("{pct:.0} %"), fields.util_source);
            }
            None => snap.gpu_usage = SensorReading::with_source("Unavailable", "no NVIDIA utilization (NVML/smi)"),
        }
    }
}

fn read_gpu_power(hwmon_class: &Path) -> SensorReading {
    for hwmon in hwmon_dirs(hwmon_class) {
        if !matches!(read_text(&hwmon.join("name")).as_deref(), Some("amdgpu" | "nouveau")) { continue; }
        let Ok(entries) = fs::read_dir(&hwmon) else { continue };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with("power") && (name.ends_with("_input") || name.ends_with("_average")) {
                if let Some(value) = read_int(&entry.path()) {
                    return SensorReading::with_source(format!("{:.1} W", value as f64 / 1_000_000.0), entry.path().display().to_string());
                }
            }
        }
    }
    SensorReading::with_source("Unavailable", "no GPU power sensor")
}

fn parse_proc_stat(path: &Path) -> Option<CpuTicks> {
    let text = fs::read_to_string(path).ok()?;
    let line = text.lines().next()?;
    let mut numbers = line.split_whitespace().skip(1);
    let mut read = || numbers.next()?.parse().ok();
    Some(CpuTicks {
        user: read()?,
        nice: read()?,
        system: read()?,
        idle: read()?,
        iowait: read()?,
        irq: read()?,
        softirq: read()?,
        steal: read()?,
    })
}

pub fn dgpu_suspended(status: Option<&Path>) -> bool {
    runtime_suspended(status)
}

#[cfg(test)]
mod tests {
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
}
