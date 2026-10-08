use std::io::Read;
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use crate::sysfs::{hwmon_dirs, read_int, read_text};

#[derive(Debug, Clone, PartialEq)]
pub struct NvidiaFields {
    pub temp_c: Option<f64>,
    pub temp_source: String,
    pub power_w: Option<f64>,
    pub power_source: String,
    pub util_pct: Option<f64>,
    pub util_source: String,
}

impl Default for NvidiaFields {
    fn default() -> Self {
        Self {
            temp_c: None,
            temp_source: String::new(),
            power_w: None,
            power_source: String::new(),
            util_pct: None,
            util_source: String::new(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct SmiBackoff {
    pub temp_retry_at: f64,
    pub power_retry_at: f64,
    pub util_retry_at: f64,
    pub known_missing: bool,
}

impl Default for SmiBackoff {
    fn default() -> Self {
        Self { temp_retry_at: 0.0, power_retry_at: 0.0, util_retry_at: 0.0, known_missing: false }
    }
}

const SMI_BACKOFF_S: f64 = 30.0;

/// Column order matches the Python query: temperature, utilization, power.
pub fn smi_due(backoff: &SmiBackoff, now: f64, temperature: bool, utilization: bool, power: bool) -> Vec<&'static str> {
    let mut due = Vec::new();
    if temperature && now >= backoff.temp_retry_at {
        due.push("temperature.gpu");
    }
    if utilization && now >= backoff.util_retry_at {
        due.push("utilization.gpu");
    }
    if power && now >= backoff.power_retry_at {
        due.push("power.draw");
    }
    due
}

pub fn parse_smi_csv(line: &str, columns: &[&str]) -> Vec<(&'static str, Option<f64>)> {
    let parts: Vec<&str> = line.split(',').map(str::trim).collect();
    columns
        .iter()
        .enumerate()
        .map(|(index, column)| {
            let field = match *column {
                "temperature.gpu" => "temperature",
                "utilization.gpu" => "utilization",
                "power.draw" => "power",
                other => other,
            };
            // The return type uses static field names only for the known columns.
            let field: &'static str = match field {
                "temperature" => "temperature",
                "utilization" => "utilization",
                "power" => "power",
                _ => "other",
            };
            let value = parts.get(index).and_then(|text| smi_float(text));
            (field, value)
        })
        .collect()
}

fn smi_float(text: &str) -> Option<f64> {
    if text.is_empty() || text.eq_ignore_ascii_case("N/A") || text.eq_ignore_ascii_case("[N/A]") {
        return None;
    }
    text.parse().ok()
}

/// Run the given `nvidia-smi` binary. Callers pass an explicit path. A missing
/// file returns `None` without searching `PATH`.
pub fn read_smi_line(program: &Path, columns: &[&str]) -> Option<String> {
    if columns.is_empty() || !program.is_file() {
        return None;
    }
    let query = columns.join(",");
    let mut child = Command::new(program)
        .args([format!("--query-gpu={query}"), "--format=csv,noheader,nounits".to_owned()])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() > Duration::from_millis(1200) => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
            Ok(None) => thread::sleep(Duration::from_millis(20)),
            Err(_) => return None,
        }
    };
    if !status.success() {
        return None;
    }
    let mut stdout = String::new();
    child.stdout.as_mut()?.read_to_string(&mut stdout).ok()?;
    stdout.lines().next().map(str::trim).filter(|line| !line.is_empty()).map(ToOwned::to_owned)
}

pub fn note_smi_failures(backoff: &mut SmiBackoff, now: f64, failed: &[&str]) {
    let until = now + SMI_BACKOFF_S;
    if failed.contains(&"temperature") {
        backoff.temp_retry_at = until;
    }
    if failed.contains(&"power") {
        backoff.power_retry_at = until;
    }
    if failed.contains(&"utilization") {
        backoff.util_retry_at = until;
    }
}

pub fn nvidia_hwmon_temp_c(hwmon_class: &Path) -> Option<f64> {
    let hwmon = crate::sysfs::find_hwmon(hwmon_class, &["nvidia"])?;
    let value = read_int(&hwmon.join("temp1_input"))?;
    (value > 0).then_some(value as f64 / 1000.0)
}

pub fn amd_or_nouveau_temp_c(hwmon_class: &Path) -> Option<f64> {
    let mut best = None;
    for hwmon in hwmon_dirs(hwmon_class) {
        let name = read_text(&hwmon.join("name")).unwrap_or_default().to_ascii_lowercase();
        if name != "amdgpu" && name != "nouveau" {
            continue;
        }
        let Ok(entries) = std::fs::read_dir(&hwmon) else { continue };
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let fname = fname.to_string_lossy();
            if !(fname.starts_with("temp") && fname.ends_with("_input")) {
                continue;
            }
            if let Some(value) = read_int(&entry.path()) {
                best = Some(best.map_or(value, |current: i64| current.max(value)));
            }
        }
    }
    best.map(|value| value as f64 / 1000.0)
}

pub fn runtime_suspended(status_file: Option<&Path>) -> bool {
    status_file.and_then(read_text).is_some_and(|text| text.eq_ignore_ascii_case("suspended"))
}

/// Load `libnvidia-ml.so.1` only when a caller asks. Tests never call this.
pub struct Nvml {
    library: libloading::Library,
}

impl Nvml {
    pub fn open() -> Option<Self> {
        unsafe {
            let library = libloading::Library::new("libnvidia-ml.so.1").ok()?;
            let init: libloading::Symbol<unsafe extern "C" fn() -> i32> = library.get(b"nvmlInit_v2").ok()?;
            if init() != 0 {
                return None;
            }
            Some(Self { library })
        }
    }

    fn device(&self) -> Option<*mut std::ffi::c_void> {
        unsafe {
            let get_handle: libloading::Symbol<unsafe extern "C" fn(u32, *mut *mut std::ffi::c_void) -> i32> =
                self.library.get(b"nvmlDeviceGetHandleByIndex_v2").ok()?;
            let mut handle = std::ptr::null_mut();
            if get_handle(0, &mut handle) != 0 || handle.is_null() {
                return None;
            }
            Some(handle)
        }
    }

    pub fn temperature_c(&self) -> Option<f64> {
        let handle = self.device()?;
        unsafe {
            let get_temp: libloading::Symbol<unsafe extern "C" fn(*mut std::ffi::c_void, i32, *mut u32) -> i32> =
                self.library.get(b"nvmlDeviceGetTemperature").ok()?;
            let mut temp = 0_u32;
            if get_temp(handle, 0, &mut temp) != 0 {
                return None;
            }
            Some(f64::from(temp))
        }
    }

    pub fn power_w(&self) -> Option<f64> {
        let handle = self.device()?;
        unsafe {
            let get_power: libloading::Symbol<unsafe extern "C" fn(*mut std::ffi::c_void, *mut u32) -> i32> =
                self.library.get(b"nvmlDeviceGetPowerUsage").ok()?;
            let mut milliwatts = 0_u32;
            if get_power(handle, &mut milliwatts) != 0 || milliwatts > 200_000 {
                return None;
            }
            Some(f64::from(milliwatts) / 1000.0)
        }
    }

    pub fn util_pct(&self) -> Option<f64> {
        let handle = self.device()?;
        #[repr(C)]
        struct Rates {
            gpu: u32,
            memory: u32,
        }
        unsafe {
            let get_util: libloading::Symbol<unsafe extern "C" fn(*mut std::ffi::c_void, *mut Rates) -> i32> =
                self.library.get(b"nvmlDeviceGetUtilizationRates").ok()?;
            let mut rates = Rates { gpu: 0, memory: 0 };
            if get_util(handle, &mut rates) != 0 {
                return None;
            }
            Some(f64::from(rates.gpu))
        }
    }
}

impl Drop for Nvml {
    fn drop(&mut self) {
        unsafe {
            if let Ok(shutdown) = self.library.get::<unsafe extern "C" fn() -> i32>(b"nvmlShutdown") {
                shutdown();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::PermissionsExt;

    use super::*;
    use victus_core::offline_scratch;

    #[test]
    fn smi_backoff_is_per_column_and_parsing_ignores_na() {
        let mut backoff = SmiBackoff::default();
        let due = smi_due(&backoff, 0.0, true, true, true);
        assert_eq!(due, vec!["temperature.gpu", "utilization.gpu", "power.draw"]);
        let parsed = parse_smi_csv("61, [N/A], 14.5", &due);
        assert_eq!(parsed[0], ("temperature", Some(61.0)));
        assert_eq!(parsed[1].1, None);
        assert_eq!(parsed[2], ("power", Some(14.5)));
        note_smi_failures(&mut backoff, 5.0, &["utilization"]);
        let later = smi_due(&backoff, 10.0, true, true, true);
        assert_eq!(later, vec!["temperature.gpu", "power.draw"]);
        assert!(!smi_due(&backoff, 10.0, false, true, false).contains(&"utilization.gpu"));

        let root = offline_scratch("nvidia");
        let hwmon = root.join("hwmon1");
        std::fs::create_dir_all(&hwmon).unwrap();
        std::fs::write(hwmon.join("name"), "nvidia\n").unwrap();
        std::fs::write(hwmon.join("temp1_input"), "47000\n").unwrap();
        assert_eq!(nvidia_hwmon_temp_c(&root), Some(47.0));
        let status = root.join("runtime_status");
        std::fs::write(&status, "suspended\n").unwrap();
        assert!(runtime_suspended(Some(&status)));
        let program = root.join("nvidia-smi");
        std::fs::write(&program, "#!/bin/sh\necho '61, 12, 14.5'\n").unwrap();
        std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
        let line = read_smi_line(&program, &["temperature.gpu", "utilization.gpu", "power.draw"]).unwrap();
        assert_eq!(line, "61, 12, 14.5");
        assert!(read_smi_line(&root.join("missing-smi"), &["temperature.gpu"]).is_none());
        let _ = std::fs::remove_dir_all(root);
    }
}
