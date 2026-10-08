pub const PROFILE_KEYS: [&str; 3] = ["power-saver", "balanced", "performance"];
pub const PROFILE_LABELS: [&str; 3] = ["Power Saver", "Balanced", "Performance"];

const TUNED_POWER_SAVER: &[&str] = &["powersave", "balanced-battery"];
const TUNED_BALANCED: &[&str] = &["balanced", "desktop"];
const TUNED_PERFORMANCE: &[&str] = &[
    "throughput-performance",
    "accelerator-performance",
    "latency-performance",
];

pub fn clamp_profile(index: i32) -> i32 {
    index.clamp(0, 2)
}

pub fn tuned_candidates(profile: i32) -> &'static [&'static str] {
    match clamp_profile(profile) {
        0 => TUNED_POWER_SAVER,
        2 => TUNED_PERFORMANCE,
        _ => TUNED_BALANCED,
    }
}

pub fn tuned_profile_for(profile: i32, available: &[&str]) -> Option<&'static str> {
    tuned_candidates(profile)
        .iter()
        .copied()
        .find(|candidate| available.contains(candidate))
}

pub fn profile_index_for_name(profile: &str) -> Option<i32> {
    for index in 0..3 {
        if tuned_candidates(index).contains(&profile) || profile == PROFILE_KEYS[index as usize] {
            return Some(index);
        }
    }
    None
}

/// Parse `tuned-adm list` output. Lines look like `- balanced`.
pub fn parse_tuned_list(output: &str) -> Vec<String> {
    output
        .lines()
        .filter_map(|line| {
            let mut parts = line.split_whitespace();
            if parts.next() == Some("-") {
                parts.next().map(str::to_owned)
            } else {
                None
            }
        })
        .collect()
}

pub fn parse_tuned_active(output: &str) -> Option<String> {
    let prefix = "Current active profile:";
    let profile = output.trim().strip_prefix(prefix)?.trim();
    if profile.is_empty() {
        None
    } else {
        Some(profile.to_owned())
    }
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/profiles.rs"]
mod tests;
