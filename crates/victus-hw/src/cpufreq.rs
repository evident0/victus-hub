use std::fs;
use std::path::{Path, PathBuf};

use victus_core::{validate_frequency, HubError, HubResult};

#[derive(Debug, Clone)]
pub struct FrequencyPolicy {
    pub path: PathBuf,
    pub hardware_min: i32,
    pub hardware_max: i32,
    pub minimum: i32,
    pub maximum: i32,
}

pub fn read_policies(root: &Path) -> HubResult<Vec<FrequencyPolicy>> {
    let mut paths = Vec::new();
    if let Ok(entries) = fs::read_dir(root) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with("policy") && name[6..].chars().all(|ch| ch.is_ascii_digit()) {
                paths.push(entry.path());
            }
        }
    }
    paths.sort();
    let mut policies = Vec::new();
    for path in paths {
        let read = |name: &str| -> HubResult<i32> {
            fs::read_to_string(path.join(name))
                .ok()
                .and_then(|text| text.trim().parse().ok())
                .ok_or_else(|| HubError::new(format!("Cannot read CPU frequency policy {}", path.file_name().unwrap_or_default().to_string_lossy())))
        };
        let hwmin = read("cpuinfo_min_freq")?;
        let mut hwmax = read("cpuinfo_max_freq")?;
        let minimum = read("scaling_min_freq")?;
        let maximum = read("scaling_max_freq")?;
        if let Some(amd) = fs::read_to_string(path.join("amd_pstate_max_freq")).ok().and_then(|text| text.trim().parse::<i32>().ok()) {
            hwmax = hwmax.max(amd);
        }
        if !(0 < hwmin && hwmin <= minimum && minimum <= maximum && maximum <= hwmax) {
            return Err(HubError::new(format!(
                "Invalid CPU frequency limits in {}",
                path.file_name().unwrap_or_default().to_string_lossy()
            )));
        }
        policies.push(FrequencyPolicy { path, hardware_min: hwmin, hardware_max: hwmax, minimum, maximum });
    }
    if policies.is_empty() {
        return Err(HubError::new("CPU frequency control is unavailable on this system"));
    }
    Ok(policies)
}

pub fn apply_limits(root: &Path, minimum: i32, maximum: i32) -> HubResult<String> {
    validate_frequency(minimum, maximum)?;
    let policies = read_policies(root)?;
    for policy in &policies {
        if minimum < policy.hardware_min || maximum > policy.hardware_max {
            return Err(HubError::new(format!(
                "CPU frequency limits exceed hardware range for {}",
                policy.path.file_name().unwrap_or_default().to_string_lossy()
            )));
        }
    }
    for policy in &policies {
        let mut writes = [("scaling_min_freq", minimum), ("scaling_max_freq", maximum)];
        if minimum > policy.maximum {
            writes.swap(0, 1);
        }
        for (name, value) in writes {
            fs::write(policy.path.join(name), value.to_string()).map_err(|error| {
                HubError::new(format!(
                    "Could not set {}: {error}. Some CPU limits may have changed.",
                    policy.path.file_name().unwrap_or_default().to_string_lossy()
                ))
            })?;
        }
    }
    Ok(format!("CPU frequency limits applied to {} policies", policies.len()))
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hw/cpufreq.rs"]
mod tests;
