//! Live sysfs platform. Construct it with [`SysPlatform::installed`] only from
//! the daemon binary. Tests use [`crate::platform::FakePlatform`] or the pure
//! helpers below with a scratch directory.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::Value;
use victus_core::{profile_index_for_name, tuned_profile_for, CpuPowerSample, HubError, HubResult, SensorSnapshot, PROFILE_KEYS};
use victus_hw::{
    amd_or_nouveau_temp_c, apply_limits, apply_ryzenadj, apply_undervolt, cpu_temp_c, detect_capabilities, dgpu_suspended,
    find_executable, find_hwmon, intel_power_limits, keyboard_led_names, keyboard_zone_count, manual_fan_supported,
    note_smi_failures, nvidia_hwmon_temp_c, parse_smi_csv, read_pwm_percent, read_smi_line, read_text, smi_due,
    write_gpu_mux, write_led_color, write_pwm, write_pwm_enable, write_sysfs, Nvml, NvidiaFields, PathBufRef, RaplSampler,
    SensorSampler, SmiBackoff,
};

use crate::platform::Platform;

pub struct SysPlatform {
    hwmon_class: PathBuf,
    leds: PathBuf,
    kbd_platform: PathBuf,
    mux: PathBuf,
    cpufreq: PathBuf,
    powercap: PathBuf,
    power_supply: PathBuf,
    tuned_active: PathBuf,
    ryzenadj: PathBuf,
    msr: PathBuf,
    proc_stat: PathBuf,
    meminfo: PathBuf,
    cpu_root: PathBuf,
    dgpu_status: Option<PathBuf>,
    emulate_zones: Option<i32>,
    allow_nvml: bool,
    nvidia_present: bool,
    intel: bool,
    user_brightness: i32,
    idle_elapsed: f64,
    sensors: SensorSampler,
    rapl: RaplSampler,
    nvml: Option<Nvml>,
    smi_backoff: SmiBackoff,
    smi_path: PathBuf,
    started: Instant,
}

impl SysPlatform {
    /// Paths for a normal root daemon. Library tests must not call this.
    pub fn installed() -> Self {
        let pci = Path::new("/sys/bus/pci/devices");
        Self {
            hwmon_class: PathBuf::from("/sys/class/hwmon"),
            leds: PathBuf::from("/sys/class/leds"),
            kbd_platform: PathBuf::from("/sys/devices/platform/hp-kbd-rgb"),
            mux: PathBuf::from("/sys/devices/platform/hp-wmi"),
            cpufreq: PathBuf::from("/sys/devices/system/cpu/cpufreq"),
            powercap: PathBuf::from("/sys/class/powercap"),
            power_supply: PathBuf::from("/sys/class/power_supply"),
            tuned_active: PathBuf::from("/etc/tuned/active_profile"),
            ryzenadj: find_executable(None, &[PathBufRef(Path::new("/usr/local/bin/ryzenadj")), PathBufRef(Path::new("/usr/bin/ryzenadj"))])
                .unwrap_or_else(|| PathBuf::from("/usr/local/bin/ryzenadj")),
            msr: PathBuf::from("/dev/cpu/0/msr"),
            proc_stat: PathBuf::from("/proc/stat"),
            meminfo: PathBuf::from("/proc/meminfo"),
            cpu_root: PathBuf::from("/sys/devices/system/cpu"),
            dgpu_status: find_nvidia_runtime(pci),
            emulate_zones: None,
            allow_nvml: true,
            nvidia_present: Path::new("/sys/module/nvidia").exists(),
            intel: cpu_is_intel(Path::new("/proc/cpuinfo")),
            user_brightness: 255,
            idle_elapsed: -1.0,
            sensors: SensorSampler::default(),
            rapl: RaplSampler::default(),
            nvml: None,
            smi_backoff: SmiBackoff::default(),
            smi_path: PathBuf::from("/usr/bin/nvidia-smi"),
            started: Instant::now(),
        }
    }

    pub fn set_idle_elapsed(&mut self, seconds: f64) {
        self.idle_elapsed = seconds;
    }

    fn hwmon(&self) -> Option<PathBuf> {
        find_hwmon(&self.hwmon_class, &["hp", "hp_wmi", "hp-wmi"])
    }

    fn led(&self, zone: Option<i32>) -> HubResult<Vec<PathBuf>> {
        let names = keyboard_led_names(self.zone_count());
        match zone {
            None => Ok(names.into_iter().map(|name| self.leds.join(name)).collect()),
            Some(zone) => {
                let name = names.get(zone as usize).copied().ok_or_else(|| HubError::new(format!("zone {zone} out of range")))?;
                Ok(vec![self.leds.join(name)])
            }
        }
    }

    fn gpu_temp(&mut self, disable_nvidia: bool) -> Option<f64> {
        if !self.nvidia_present {
            if disable_nvidia {
                return None;
            }
            return amd_or_nouveau_temp_c(&self.hwmon_class);
        }
        self.nvidia_fields(true, false, false, disable_nvidia).temp_c
    }

    /// Hardware order matches the Python query: hwmon, then NVML, then nvidia-smi.
    /// `Nvml::open` and `nvidia-smi` run only from this live platform.
    fn nvidia_fields(&mut self, temperature: bool, power: bool, utilization: bool, disable_nvidia: bool) -> NvidiaFields {
        let suspended = dgpu_suspended(self.dgpu_status.as_deref());
        if disable_nvidia || suspended || !self.nvidia_present {
            self.nvml = None;
            return NvidiaFields::default();
        }
        let mut fields = NvidiaFields::default();
        if temperature {
            if let Some(temp) = nvidia_hwmon_temp_c(&self.hwmon_class) {
                fields.temp_c = Some(temp);
                fields.temp_source = "hwmon:nvidia".into();
            }
        }
        if temperature && fields.temp_c.is_some() && !power && !utilization {
            return fields;
        }
        if self.allow_nvml {
            if self.nvml.is_none() {
                self.nvml = Nvml::open();
            }
            if let Some(nvml) = &self.nvml {
                if power {
                    fields.power_w = nvml.power_w();
                    if fields.power_w.is_some() {
                        fields.power_source = "nvml".into();
                    }
                }
                if utilization {
                    fields.util_pct = nvml.util_pct();
                    if fields.util_pct.is_some() {
                        fields.util_source = "nvml".into();
                    }
                }
                if temperature && fields.temp_c.is_none() {
                    fields.temp_c = nvml.temperature_c();
                    if fields.temp_c.is_some() {
                        fields.temp_source = "nvml".into();
                    }
                }
            }
        }
        let missing_temp = temperature && fields.temp_c.is_none();
        let missing_power = power && fields.power_w.is_none();
        let missing_util = utilization && fields.util_pct.is_none();
        if missing_temp || missing_power || missing_util {
            self.fill_from_smi(&mut fields, missing_temp, missing_util, missing_power);
        }
        fields
    }

    fn fill_from_smi(&mut self, fields: &mut NvidiaFields, temperature: bool, utilization: bool, power: bool) {
        let now = elapsed_secs(self.started, Instant::now()).as_secs_f64();
        if self.smi_backoff.known_missing {
            return;
        }
        let columns = smi_due(&self.smi_backoff, now, temperature, utilization, power);
        if columns.is_empty() {
            return;
        }
        if !self.smi_path.is_file() {
            self.smi_backoff.known_missing = true;
            return;
        }
        let Some(line) = read_smi_line(&self.smi_path, &columns) else {
            note_smi_failures(&mut self.smi_backoff, now, &["temperature", "utilization", "power"]);
            return;
        };
        let parsed = parse_smi_csv(&line, &columns);
        let mut failed = Vec::new();
        for (field, value) in parsed {
            match (field, value) {
                ("temperature", Some(temp)) => {
                    fields.temp_c = Some(temp);
                    fields.temp_source = "nvidia-smi".into();
                }
                ("utilization", Some(util)) => {
                    fields.util_pct = Some(util);
                    fields.util_source = "nvidia-smi".into();
                }
                ("power", Some(watts)) => {
                    fields.power_w = Some(watts);
                    fields.power_source = "nvidia-smi".into();
                }
                (name, None) => failed.push(name),
                _ => {}
            }
        }
        note_smi_failures(&mut self.smi_backoff, now, &failed);
    }
}

impl Platform for SysPlatform {
    fn pwm_enable(&mut self, mode: i32) -> HubResult<String> {
        write_pwm_enable(self.hwmon().as_deref(), mode)
    }
    fn pwm_max(&mut self) -> HubResult<String> {
        self.pwm_enable(0)
    }
    fn pwm(&mut self, value: i32) -> HubResult<String> {
        write_pwm(self.hwmon().as_deref(), value)
    }
    fn keyboard_color(&mut self, zone: Option<i32>, red: u8, green: u8, blue: u8) -> HubResult<String> {
        let leds = self.led(zone)?;
        let mut label = String::new();
        for led in leds {
            label = write_led_color(&led, red, green, blue, self.user_brightness)?;
        }
        Ok(label)
    }
    fn keyboard_brightness(&mut self, level: i32) -> HubResult<String> {
        let level = level.clamp(0, 255);
        let leds = self.led(None)?;
        let mut label = String::new();
        for led in leds {
            label = write_sysfs(&led.join("brightness"), &level.to_string())?;
        }
        Ok(label)
    }
    fn keyboard_user_brightness(&mut self, level: i32) -> HubResult<String> {
        self.user_brightness = level.clamp(0, 255);
        self.keyboard_brightness(self.user_brightness)
    }
    fn zone_count(&self) -> i32 {
        keyboard_zone_count(&self.kbd_platform, self.emulate_zones)
    }
    fn manual_fan(&self) -> bool {
        manual_fan_supported(self.hwmon().as_deref())
    }
    fn pwm_percent(&self) -> Option<f64> {
        read_pwm_percent(self.hwmon().as_deref())
    }
    fn temps(&mut self, disable_nvidia: bool) -> HubResult<(Option<f64>, Option<f64>)> {
        Ok((cpu_temp_c(&self.hwmon_class), self.gpu_temp(disable_nvidia)))
    }
    fn ryzenadj(&mut self, stapm: i32, fast: i32, slow: i32, tctl: i32) -> HubResult<String> {
        apply_ryzenadj(&self.ryzenadj, stapm, fast, slow, tctl)
    }
    fn intel_power(&mut self, pl1: i32, pl2: i32) -> HubResult<String> {
        intel_power_limits(&self.powercap, pl1, pl2, self.intel)
    }
    fn intel_undervolt(&mut self, core_mv: i32, cache_mv: i32) -> HubResult<String> {
        apply_undervolt(&self.msr, self.intel, core_mv, cache_mv)
    }
    fn cpu_frequency(&mut self, minimum: i32, maximum: i32) -> HubResult<String> {
        apply_limits(&self.cpufreq, minimum, maximum)
    }
    fn intel_cpu(&self) -> bool {
        self.intel
    }
    fn profile_index(&self) -> Option<i32> {
        profile_from_active_file(&self.tuned_active)
    }
    fn apply_profile(&mut self, index: i32) -> HubResult<String> {
        let tuned = find_executable(None, &[PathBufRef(Path::new("/usr/sbin/tuned-adm")), PathBufRef(Path::new("/usr/bin/tuned-adm"))]);
        let ppctl = find_executable(None, &[PathBufRef(Path::new("/usr/bin/powerprofilesctl"))]);
        let names = if let Some(tuned) = &tuned {
            let output = victus_hw::run_command(tuned, &["list".into()])?;
            victus_core::parse_tuned_list(&output.stdout)
        } else {
            Vec::new()
        };
        let (program, args, label) = profile_command(index, tuned.as_deref(), &names, ppctl.as_deref())?;
        let output = victus_hw::run_command(&program, &args)?;
        if output.status != 0 {
            let message = if output.stderr.trim().is_empty() { output.stdout.trim() } else { output.stderr.trim() };
            return Err(HubError::new(if message.is_empty() { "profile command failed".into() } else { message.to_owned() }));
        }
        Ok(label)
    }
    fn ac_online(&self) -> Option<bool> {
        ac_online(&self.power_supply)
    }
    fn sensors(&mut self, keys: &[String], disable_nvidia: bool) -> SensorSnapshot {
        let power = self.rapl.read(&self.powercap, elapsed_secs(self.started, Instant::now()));
        let suspended = dgpu_suspended(self.dgpu_status.as_deref());
        let wants = |key: &str| keys.iter().any(|item| item == key);
        let gpu = if self.nvidia_present && (wants("gpu-temp") || wants("gpu-power") || wants("gpu-usage")) {
            Some(self.nvidia_fields(wants("gpu-temp"), wants("gpu-power"), wants("gpu-usage"), disable_nvidia || suspended))
        } else if disable_nvidia || suspended {
            self.nvml = None;
            None
        } else {
            None
        };
        self.sensors.read(
            keys,
            &self.hwmon_class,
            &self.proc_stat,
            &self.meminfo,
            &self.cpu_root,
            Some(&power),
            gpu.as_ref(),
            suspended,
            disable_nvidia,
            self.nvidia_present,
        )
    }
    fn cpu_power(&mut self) -> CpuPowerSample {
        self.rapl.read(&self.powercap, elapsed_secs(self.started, Instant::now()))
    }
    fn gpu_mux(&mut self, mode: i32) -> HubResult<String> {
        write_gpu_mux(&self.mux, mode)
    }
    fn capabilities(&self) -> Value {
        detect_capabilities(&self.hwmon_class, &self.leds, &self.mux).to_value()
    }
    fn idle_elapsed(&self) -> f64 {
        self.idle_elapsed
    }
    fn release_gpu(&mut self) {
        self.nvml = None;
    }
}

pub fn cpu_is_intel(cpuinfo: &Path) -> bool {
    std::fs::read_to_string(cpuinfo).is_ok_and(|text| text.contains("GenuineIntel"))
}

pub fn profile_from_active_file(path: &Path) -> Option<i32> {
    let text = std::fs::read_to_string(path).ok()?;
    profile_index_for_name(text.trim())
}

pub fn ac_online(power_supply: &Path) -> Option<bool> {
    let entries = std::fs::read_dir(power_supply).ok()?;
    let mut saw_mains = false;
    let mut any_online = false;
    let mut paths = Vec::new();
    for entry in entries.flatten() {
        paths.push(entry.path());
    }
    for path in &paths {
        let kind = read_text(&path.join("type")).unwrap_or_default();
        if !matches!(kind.as_str(), "Mains" | "ADP" | "USB") {
            continue;
        }
        saw_mains = true;
        if read_text(&path.join("online")).as_deref() == Some("1") {
            any_online = true;
        }
    }
    if saw_mains {
        return Some(any_online);
    }
    for path in &paths {
        if read_text(&path.join("type")).as_deref() != Some("Battery") {
            continue;
        }
        return match read_text(&path.join("status")).unwrap_or_default().to_ascii_lowercase().as_str() {
            "discharging" => Some(false),
            "charging" | "full" | "not charging" => Some(true),
            _ => None,
        };
    }
    None
}

pub fn find_nvidia_runtime(pci_devices: &Path) -> Option<PathBuf> {
    for entry in std::fs::read_dir(pci_devices).ok()?.flatten() {
        if read_text(&entry.path().join("vendor")).as_deref() == Some("0x10de") {
            return Some(entry.path().join("power/runtime_status"));
        }
    }
    None
}

pub fn profile_command(
    index: i32,
    tuned: Option<&Path>,
    tuned_names: &[String],
    ppctl: Option<&Path>,
) -> HubResult<(PathBuf, Vec<String>, String)> {
    let index = index.clamp(0, 2);
    let names: Vec<&str> = tuned_names.iter().map(String::as_str).collect();
    if let Some(tuned) = tuned {
        if let Some(name) = tuned_profile_for(index, &names) {
            return Ok((tuned.to_path_buf(), vec!["profile".into(), (*name).to_owned()], format!("Applied tuned profile: {name}")));
        }
    }
    if let Some(ppctl) = ppctl {
        let key = PROFILE_KEYS[index as usize];
        return Ok((ppctl.to_path_buf(), vec!["set".into(), key.to_owned()], format!("Applied powerprofilesctl profile: {key}")));
    }
    Err(HubError::new(format!("no backend profile found for index {index}")))
}

/// Monotonic seconds since `origin`, for tests that inject a clock.
pub fn elapsed_secs(origin: Instant, now: Instant) -> Duration {
    now.saturating_duration_since(origin)
}

#[cfg(test)]
mod tests {
    use super::*;
    use victus_core::offline_scratch;

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
        let status = find_nvidia_runtime(&root.join("pci")).unwrap();
        assert!(status.starts_with(&root));
        assert!(status.ends_with("runtime_status"));
        let (program, args, _) = profile_command(0, Some(Path::new("/usr/sbin/tuned-adm")), &["powersave".into()], None).unwrap();
        assert_eq!(args, vec!["profile".to_owned(), "powersave".to_owned()]);
        assert_eq!(program, Path::new("/usr/sbin/tuned-adm"));
        let _ = std::fs::remove_dir_all(root);
    }
}
