use std::fs;
use std::path::{Path, PathBuf};

use victus_core::{HubError, HubResult};

pub fn read_text(path: &Path) -> Option<String> {
    fs::read_to_string(path).ok().map(|text| text.trim().to_owned()).filter(|text| !text.is_empty())
}

pub fn read_int(path: &Path) -> Option<i64> {
    read_text(path)?.parse().ok()
}

pub fn write_sysfs(path: &Path, value: &str) -> HubResult<String> {
    fs::write(path, value).map_err(|error| HubError::new(format!("{}: {error}", path.display())))?;
    Ok(path.display().to_string())
}

pub fn find_hwmon(class_dir: &Path, names: &[&str]) -> Option<PathBuf> {
    let entries = fs::read_dir(class_dir).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = read_text(&path.join("name")) else { continue };
        if names.iter().any(|wanted| name.eq_ignore_ascii_case(wanted)) {
            return Some(path);
        }
    }
    None
}

pub fn hwmon_dirs(class_dir: &Path) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    let Ok(entries) = fs::read_dir(class_dir) else { return dirs };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.join("name").is_file() {
            dirs.push(path);
        }
    }
    dirs.sort();
    dirs
}

pub fn manual_fan_supported(hwmon: Option<&Path>) -> bool {
    hwmon.is_some_and(|path| path.join("pwm1_enable").exists() && path.join("pwm1").exists())
}

pub fn write_pwm_enable(hwmon: Option<&Path>, mode: i32) -> HubResult<String> {
    let hwmon = hwmon.ok_or_else(|| HubError::new("hp hwmon not found"))?;
    write_sysfs(&hwmon.join("pwm1_enable"), &mode.to_string())
}

pub fn write_pwm(hwmon: Option<&Path>, pwm: i32) -> HubResult<String> {
    let hwmon = hwmon.ok_or_else(|| HubError::new("hp hwmon not found"))?;
    write_sysfs(&hwmon.join("pwm1_enable"), "1")?;
    let label = write_sysfs(&hwmon.join("pwm1"), &pwm.to_string())?;
    if hwmon.join("pwm2").exists() {
        write_sysfs(&hwmon.join("pwm2"), &pwm.to_string())?;
    }
    Ok(label)
}

pub fn keyboard_zone_count(platform: &Path, emulate: Option<i32>) -> i32 {
    if let Some(count) = emulate.filter(|count| *count >= 1) {
        return count;
    }
    read_int(&platform.join("zone_count")).map(|value| value.max(1) as i32).unwrap_or(1)
}

pub fn keyboard_led_names(zone_count: i32) -> Vec<&'static str> {
    if zone_count <= 1 {
        vec!["hp::kbd_backlight"]
    } else {
        ["hp::kbd_backlight_zoned_backlight-right",
         "hp::kbd_backlight_zoned_backlight-center",
         "hp::kbd_backlight_zoned_backlight-left",
         "hp::kbd_backlight_zoned_backlight-wasd"]
            .into_iter()
            .take(zone_count.max(1) as usize)
            .collect()
    }
}

pub fn write_led_color(led: &Path, red: u8, green: u8, blue: u8, brightness: i32) -> HubResult<String> {
    write_sysfs(&led.join("multi_intensity"), &format!("{red} {green} {blue}"))?;
    write_sysfs(&led.join("brightness"), &brightness.clamp(0, 255).to_string())?;
    Ok(led.display().to_string())
}

pub fn keyboard_lighting_supported(leds: &Path) -> bool {
    let Ok(entries) = fs::read_dir(leds) else { return false };
    entries.flatten().any(|entry| {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        let matches = name == "hp::kbd_backlight" || name.starts_with("hp::kbd_backlight_zoned_backlight-");
        matches && entry.path().join("multi_intensity").exists() && entry.path().join("brightness").exists()
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GpuMuxMode {
    pub name: String,
    pub index: i32,
    pub label: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GpuMuxState {
    pub modes: Vec<GpuMuxMode>,
    pub current_index: i32,
}

pub fn read_gpu_mux(platform: &Path) -> Option<GpuMuxState> {
    if !platform.is_dir() {
        return None;
    }
    let names = read_text(&platform.join("gpu_mux_supported_names"))?;
    let known = [("hybrid", 0), ("discrete", 1), ("optimus", 2), ("uma", 3)];
    let modes = names
        .split_whitespace()
        .filter_map(|name| {
            known.iter().find(|(known, _)| *known == name).map(|(name, index)| GpuMuxMode {
                name: (*name).to_owned(),
                index: *index,
                label: {
                    let mut chars = name.chars();
                    match chars.next() {
                        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                        None => String::new(),
                    }
                },
            })
        })
        .collect::<Vec<_>>();
    if modes.is_empty() {
        return None;
    }
    let current = read_int(&platform.join("gpu_mux_mode"))? as i32;
    if !known.iter().any(|(_, index)| *index == current) {
        return None;
    }
    Some(GpuMuxState { modes, current_index: current })
}

pub fn write_gpu_mux(platform: &Path, mode: i32) -> HubResult<String> {
    let path = platform.join("gpu_mux_mode");
    if !path.exists() {
        return Err(HubError::new("hp-wmi GPU MUX control not found"));
    }
    write_sysfs(&path, &mode.to_string())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HardwareCapabilities {
    pub fan_modes: Vec<String>,
    pub keyboard_lighting: bool,
    pub gpu_mux: Option<GpuMuxState>,
}

impl HardwareCapabilities {
    pub fn to_value(&self) -> serde_json::Value {
        serde_json::json!({
            "fan_modes": self.fan_modes,
            "keyboard_lighting": self.keyboard_lighting,
            "gpu_mux": self.gpu_mux.as_ref().map(|mux| serde_json::json!({
                "modes": mux.modes.iter().map(|mode| serde_json::json!({
                    "name": mode.name,
                    "index": mode.index,
                    "label": mode.label,
                })).collect::<Vec<_>>(),
                "current_index": mux.current_index,
            })),
        })
    }
}

pub fn detect_capabilities(hwmon_class: &Path, leds: &Path, mux_platform: &Path) -> HardwareCapabilities {
    let hwmon = find_hwmon(hwmon_class, &["hp", "hp_wmi", "hp-wmi"]);
    let mut modes = vec!["auto".to_owned()];
    if let Some(hwmon) = &hwmon {
        if hwmon.join("pwm1_enable").exists() {
            let manual = hwmon.join("pwm1").exists();
            if manual {
                modes.push("smart".to_owned());
            }
            modes.push("max".to_owned());
            if manual {
                modes.push("custom".to_owned());
            }
        }
    }
    HardwareCapabilities {
        fan_modes: modes,
        keyboard_lighting: keyboard_lighting_supported(leds),
        gpu_mux: read_gpu_mux(mux_platform),
    }
}

pub fn read_pwm_percent(hwmon: Option<&Path>) -> Option<f64> {
    let value = read_int(&hwmon?.join("pwm1"))?;
    Some(value as f64 / 255.0 * 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use victus_core::offline_scratch;

    #[test]
    fn fan_and_keyboard_writes_stay_in_the_scratch_tree() {
        let root = offline_scratch("sysfs");
        assert!(root.starts_with(std::env::temp_dir()));
        let hwmon = root.join("hwmon/hwmon0");
        fs::create_dir_all(&hwmon).unwrap();
        fs::write(hwmon.join("name"), "hp-wmi\n").unwrap();
        fs::write(hwmon.join("pwm1_enable"), "2\n").unwrap();
        fs::write(hwmon.join("pwm1"), "0\n").unwrap();
        fs::write(hwmon.join("pwm2"), "0\n").unwrap();
        let class = root.join("hwmon");
        let found = find_hwmon(&class, &["hp-wmi"]).unwrap();
        write_pwm(Some(found.as_path()), 128).unwrap();
        assert_eq!(read_text(&found.join("pwm1_enable")).as_deref(), Some("1"));
        assert_eq!(read_text(&found.join("pwm1")).as_deref(), Some("128"));
        assert_eq!(read_text(&found.join("pwm2")).as_deref(), Some("128"));

        let leds = root.join("leds/hp::kbd_backlight");
        fs::create_dir_all(&leds).unwrap();
        fs::write(leds.join("multi_intensity"), "0 0 0\n").unwrap();
        fs::write(leds.join("brightness"), "0\n").unwrap();
        write_led_color(&leds, 1, 2, 3, 40).unwrap();
        assert_eq!(read_text(&leds.join("multi_intensity")).as_deref(), Some("1 2 3"));
        assert!(keyboard_lighting_supported(&root.join("leds")));

        let mux = root.join("hp-wmi");
        fs::create_dir_all(&mux).unwrap();
        fs::write(mux.join("gpu_mux_supported_names"), "hybrid discrete\n").unwrap();
        fs::write(mux.join("gpu_mux_mode"), "0\n").unwrap();
        let state = read_gpu_mux(&mux).unwrap();
        assert_eq!(state.current_index, 0);
        assert_eq!(state.modes.len(), 2);
        let caps = detect_capabilities(&class, &root.join("leds"), &mux);
        assert!(caps.fan_modes.contains(&"custom".to_owned()));
        assert!(caps.keyboard_lighting);
        let _ = fs::remove_dir_all(root);
    }
}
