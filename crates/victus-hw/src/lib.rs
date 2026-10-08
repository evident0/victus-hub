//! Hardware helpers. Production callers pass real `/sys` paths. Tests pass a
//! directory created by [`victus_core::offline_scratch`] and never the live
//! sysfs tree.

#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc, clippy::must_use_candidate)]
#![allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, clippy::cast_precision_loss)]
#![allow(clippy::module_name_repetitions, clippy::doc_markdown, clippy::too_many_arguments)]

mod cpufreq;
mod nvidia;
mod rapl;
mod sensors;
mod sysfs;
mod undervolt;

pub use cpufreq::{apply_limits, read_policies, FrequencyPolicy};
pub use nvidia::{
    amd_or_nouveau_temp_c, note_smi_failures, nvidia_hwmon_temp_c, parse_smi_csv, read_smi_line, runtime_suspended,
    smi_due, Nvml, NvidiaFields, SmiBackoff,
};
pub use rapl::RaplSampler;
pub use sensors::{cpu_temp_c, dgpu_suspended, SensorSampler};
pub use undervolt::apply_undervolt;
pub use sysfs::{
    detect_capabilities, find_hwmon, keyboard_led_names, keyboard_lighting_supported, keyboard_zone_count,
    manual_fan_supported, read_gpu_mux, read_int, read_pwm_percent, read_text, write_gpu_mux, write_led_color,
    write_pwm, write_pwm_enable, write_sysfs, GpuMuxState, HardwareCapabilities,
};

use std::path::Path;
use std::process::Command;

use victus_core::{ryzenadj_args, validate_ryzenadj, HubError, HubResult};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandOutput {
    pub status: i32,
    pub stdout: String,
    pub stderr: String,
}

pub fn run_command(program: &Path, args: &[String]) -> HubResult<CommandOutput> {
    let output = Command::new(program).args(args).output().map_err(|error| HubError::new(format!("failed to run {}: {error}", program.display())))?;
    Ok(CommandOutput {
        status: output.status.code().unwrap_or(1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

pub fn apply_ryzenadj(program: &Path, stapm: i32, fast: i32, slow: i32, tctl: i32) -> HubResult<String> {
    validate_ryzenadj(stapm, fast, slow, tctl)?;
    let output = run_command(program, &ryzenadj_args(stapm, fast, slow, tctl))?;
    if output.status == 0 {
        return Ok(format!("applied STAPM {stapm} mW, fast {fast} mW, slow {slow} mW, tctl {tctl} °C"));
    }
    let message = if !output.stderr.trim().is_empty() {
        output.stderr.trim().to_owned()
    } else if !output.stdout.trim().is_empty() {
        output.stdout.trim().to_owned()
    } else {
        format!("{} exited with {}", program.display(), output.status)
    };
    Err(HubError::new(message))
}

pub fn find_executable(explicit: Option<&Path>, candidates: &[PathBufRef<'_>]) -> Option<std::path::PathBuf> {
    if let Some(path) = explicit.filter(|path| path.is_file()) {
        return Some(path.to_path_buf());
    }
    candidates.iter().map(|PathBufRef(path)| path.to_path_buf()).find(|path| path.is_file())
}

pub struct PathBufRef<'a>(pub &'a Path);

pub fn intel_power_limits(root: &Path, pl1_mw: i32, pl2_mw: i32, intel: bool) -> HubResult<String> {
    if !intel {
        return Err(HubError::new("Intel CPU controls are unavailable on this processor"));
    }
    victus_core::validate_intel_power(pl1_mw, pl2_mw)?;
    let mut packages = Vec::new();
    if let Ok(entries) = std::fs::read_dir(root) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with("intel-rapl:") && name.matches(':').count() == 1 {
                packages.push(entry.path());
            }
        }
    }
    packages.sort();
    let mut writes = Vec::new();
    for package in &packages {
        let package_name = std::fs::read_to_string(package.join("name")).unwrap_or_default();
        if !package_name.trim().starts_with("package-") {
            continue;
        }
        let mut constraints = Vec::new();
        if let Ok(entries) = std::fs::read_dir(package) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if let Some(prefix) = name.strip_suffix("_name") {
                    if let Some(label) = crate::sysfs::read_text(&entry.path()) {
                        constraints.push((label, prefix.to_owned()));
                    }
                }
            }
        }
        for (label, value) in [("long_term", pl1_mw), ("short_term", pl2_mw)] {
            let prefix = constraints
                .iter()
                .find(|(name, _)| name == label)
                .map(|(_, prefix)| prefix.clone())
                .ok_or_else(|| HubError::new(format!("{} does not expose {label} power limits", package.file_name().unwrap_or_default().to_string_lossy())))?;
            let target = package.join(format!("{prefix}_power_limit_uw"));
            let _ = std::fs::read_to_string(&target).map_err(|error| HubError::new(format!("Could not apply Intel power limits: {error}. Some limits may have changed.")))?;
            writes.push((target, value * 1000));
        }
        let enabled = package.join("enabled");
        if enabled.exists() && crate::sysfs::read_text(&enabled).as_deref() != Some("1") {
            writes.push((enabled, 1));
        }
    }
    if writes.is_empty() {
        return Err(HubError::new("Intel RAPL package power limits are unavailable"));
    }
    for (path, value) in &writes {
        std::fs::write(path, value.to_string()).map_err(|error| HubError::new(format!("Could not apply Intel power limits: {error}. Some limits may have changed.")))?;
    }
    Ok("Intel PL1/PL2 power limits applied".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use victus_core::offline_scratch;

    #[test]
    fn ryzenadj_validation_does_not_execute_a_program() {
        let missing = offline_scratch("ryzen").join("missing-ryzenadj");
        let error = apply_ryzenadj(&missing, 500, 25_000, 25_000, 95).unwrap_err();
        assert!(error.to_string().contains("stapm-limit"));
        let _ = std::fs::remove_dir_all(missing.parent().unwrap());
    }
}
