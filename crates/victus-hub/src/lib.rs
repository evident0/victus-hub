//! Unprivileged Victus Hub UI model.
//!
//! Window, socket, and D-Bus setup live in the binary. These functions only
//! plan requests and render text.

#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc, clippy::must_use_candidate)]
#![allow(clippy::module_name_repetitions, clippy::doc_markdown, clippy::too_many_lines)]

use std::collections::VecDeque;
use std::path::{Path, PathBuf};

use serde_json::Value;
use victus_core::{
    config_to_value, fan_mode_steps, keys_for_page, lighting_to_value, power_to_value, release_is_newer, render_markdown,
    state_from_value, zone_for_key, DaemonState, FanConfig, FanMode, LightingSettings, SensorSnapshot, PROGRAM_VERSION,
};

pub const PAGES: [&str; 6] = ["Home", "Power", "Fans", "Keyboard", "Sensors", "Settings"];
pub const GITHUB_INSTALL_COMMAND: &str =
    "curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/install.sh | sudo bash";
pub const RELEASE_URL: &str = "https://api.github.com/repos/evident0/victus-hub/releases/latest";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpdateChoice {
    Current,
    Available,
    Invalid,
}

pub fn update_choice(tag: &str, installed: &str) -> UpdateChoice {
    match (release_is_newer(tag, installed), release_is_newer(installed, tag)) {
        (Ok(true), _) => UpdateChoice::Available,
        (Ok(false), Ok(false)) | (_, Ok(true)) => UpdateChoice::Current,
        _ => UpdateChoice::Invalid,
    }
}

pub fn parse_release_tag(body: &str) -> Option<String> {
    let value: Value = serde_json::from_str(body).ok()?;
    let tag = value.get("tag_name")?.as_str()?.trim().to_owned();
    matches!(update_choice(&tag, "0.0.0"), UpdateChoice::Available).then_some(tag)
}

pub fn fan_requests(start: &FanConfig, mode: FanMode) -> Vec<String> {
    fan_mode_steps(start, mode).into_iter().map(|config| format!("fan-config\t{}", config_to_value(&config))).collect()
}

pub fn sensor_request(page: usize) -> Option<String> {
    let keys = keys_for_page(page);
    if keys.is_empty() {
        None
    } else {
        Some(format!("sensors\t{}", keys.join(",")))
    }
}

pub fn power_request(state: &DaemonState) -> String {
    format!("power-config\t{}", power_to_value(&state.power))
}

pub fn lighting_request(settings: &LightingSettings) -> String {
    format!("lighting-config\t{}", lighting_to_value(settings))
}

pub fn update_shell() -> String {
    format!(
        "set -o pipefail; {GITHUB_INSTALL_COMMAND}; status=$?; if [ -x /usr/local/bin/victus-hub ]; then setsid env VICTUS_HUB_DEBUG_LEVEL=0 /usr/local/bin/victus-hub </dev/null >/dev/null 2>&1 & fi; printf '\\nUpdate finished (exit %s). You can close this window.\\n' \"$status\"; sleep infinity"
    )
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrequencyWindow {
    pub lower: i32,
    pub upper: i32,
    pub minimum: i32,
    pub maximum: i32,
    pub policies: usize,
    pub mixed: bool,
}

#[derive(Debug, Clone)]
pub struct MuxChoice {
    pub label: String,
    pub index: i32,
}

/// Read-only facts gathered by the binary. Library code only uses the paths stored here.
#[derive(Debug, Clone)]
pub struct HostView {
    pub intel: bool,
    pub keyboard: bool,
    pub fan_modes: Vec<String>,
    pub frequency: Option<FrequencyWindow>,
    pub frequency_error: String,
    pub power_supply: PathBuf,
    pub conf_path: PathBuf,
    pub shortcut_mods: Vec<i32>,
    pub shortcut_key: i32,
    pub mux: Vec<MuxChoice>,
    pub mux_index: i32,
    pub product: String,
    pub gpu_name: String,
    pub undervolt: (i32, i32),
}

impl Default for HostView {
    fn default() -> Self {
        Self {
            intel: false,
            keyboard: true,
            fan_modes: ["auto", "smart", "max", "custom"].into_iter().map(str::to_owned).collect(),
            frequency: None,
            frequency_error: String::new(),
            power_supply: PathBuf::new(),
            conf_path: PathBuf::new(),
            shortcut_mods: Vec::new(),
            shortcut_key: 0,
            mux: Vec::new(),
            mux_index: -1,
            product: String::new(),
            gpu_name: String::new(),
            undervolt: (0, 0),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Model {
    pub page: usize,
    pub profile: i32,
    pub offline: bool,
    pub socket: PathBuf,
    pub state: DaemonState,
    pub zones: i32,
    pub status: String,
    pub snapshot: SensorSnapshot,
    pub history: VecDeque<f64>,
    pub visible: bool,
    pub quit: bool,
    pub host: HostView,
}

impl Model {
    pub fn offline(zones: i32) -> Self {
        Self {
            page: 0,
            profile: 1,
            offline: true,
            socket: PathBuf::new(),
            state: DaemonState::default(),
            zones: zones.max(1),
            status: "Offline preview".into(),
            snapshot: SensorSnapshot::default(),
            history: VecDeque::new(),
            visible: true,
            quit: false,
            host: HostView::default(),
        }
    }

    pub fn live(socket: PathBuf, zones: i32) -> Self {
        let mut model = Self::offline(zones);
        model.offline = false;
        model.socket = socket;
        model.status.clear();
        model
    }

    /// Take daemon state as authoritative. Local defaults are not pushed back.
    pub fn hydrate(&mut self, value: &Value) {
        self.state = state_from_value(value);
        if let Some(profile) = value.get("profile").and_then(Value::as_i64).filter(|index| (0..=2).contains(index)) {
            self.profile = profile as i32;
        }
    }

    pub fn select_fan_mode(&mut self, mode: FanMode) -> Vec<String> {
        let requests = fan_requests(&self.state.fan, mode);
        if let Some(last) = fan_mode_steps(&self.state.fan, mode).last() {
            self.state.fan = last.clone();
        }
        requests
    }

    pub fn note_sample(&mut self, value: Option<f64>) {
        if let Some(value) = value {
            self.history.push_back(value);
            while self.history.len() > 120 {
                self.history.pop_front();
            }
        }
    }

    pub fn keyboard_zone(&self, label: &str, x: f64, width: f64) -> usize {
        zone_for_key(label, x, width)
    }
}

/// Footer text from an explicit power-supply directory. An empty or missing
/// directory returns an em dash and does not look at the host `/sys`.
pub fn power_status_line(root: &Path) -> String {
    if root.as_os_str().is_empty() || !root.is_dir() {
        return "—".into();
    }
    let Ok(entries) = std::fs::read_dir(root) else {
        return "—".into();
    };
    let mut supplies = entries.flatten().map(|entry| entry.path()).collect::<Vec<_>>();
    supplies.sort();
    let mut saw_mains = false;
    let mut mains_online = false;
    let mut battery = None;
    for supply in supplies {
        let kind = read_trimmed(&supply.join("type"));
        if matches!(kind.as_deref(), Some("Mains" | "ADP" | "USB")) {
            saw_mains = true;
            mains_online |= read_trimmed(&supply.join("online")).as_deref() == Some("1");
        } else if kind.as_deref() == Some("Battery") && battery.is_none() {
            battery = Some(supply);
        }
    }
    let source = if saw_mains {
        if mains_online { "AC" } else { "Battery" }
    } else if let Some(supply) = &battery {
        match read_trimmed(&supply.join("status")).unwrap_or_default().to_ascii_lowercase().as_str() {
            "charging" | "full" | "not charging" => "AC",
            "discharging" => "Battery",
            _ => return "—".into(),
        }
    } else {
        return "—".into();
    };
    let Some(supply) = battery else {
        return source.into();
    };
    match read_trimmed(&supply.join("capacity")).and_then(|text| text.parse::<i32>().ok()) {
        Some(capacity) if (0..=100).contains(&capacity) => format!("{source} · {capacity}%"),
        _ => source.into(),
    }
}

fn read_trimmed(path: &Path) -> Option<String> {
    std::fs::read_to_string(path).ok().map(|text| text.trim().to_owned()).filter(|text| !text.is_empty())
}

/// Read the `[programShortcut]` section of a Qt settings file.
pub fn program_shortcut_from_conf(text: &str) -> (Vec<i32>, i32) {
    let mut section = false;
    let mut key = 0;
    let mut mods = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix('[') {
            section = rest.trim_end_matches(']').eq_ignore_ascii_case("programShortcut");
            continue;
        }
        if !section {
            continue;
        }
        let Some((name, value)) = line.split_once('=') else { continue };
        match name.trim() {
            "key" => key = value.trim().parse().unwrap_or(0),
            "mods" => {
                mods = value.split([',', ' ']).filter(|part| !part.is_empty()).filter_map(|part| part.parse().ok()).collect();
            }
            _ => {}
        }
    }
    (mods, key)
}

/// Replace or append `[programShortcut]` without touching other sections.
pub fn upsert_program_shortcut(text: &str, mods: &[i32], key: i32) -> String {
    let enabled = if key == 0 { "false" } else { "true" };
    let mods_line = mods.iter().map(i32::to_string).collect::<Vec<_>>().join(",");
    let block = format!("[programShortcut]\nenabled={enabled}\nkey={key}\nmods={mods_line}\n");
    let lines: Vec<&str> = text.lines().collect();
    let Some(start) = lines.iter().position(|line| line.trim().eq_ignore_ascii_case("[programShortcut]")) else {
        let mut out = text.to_owned();
        if !out.is_empty() && !out.ends_with('\n') {
            out.push('\n');
        }
        if !out.is_empty() && !out.ends_with("\n\n") {
            out.push('\n');
        }
        out.push_str(&block);
        return out;
    };
    let end = lines
        .iter()
        .skip(start + 1)
        .position(|line| line.trim().starts_with('['))
        .map_or(lines.len(), |offset| start + 1 + offset);
    let mut out = String::new();
    for (index, line) in lines.iter().enumerate() {
        if index == start {
            out.push_str(&block);
        }
        if (start..end).contains(&index) {
            continue;
        }
        out.push_str(line);
        out.push('\n');
    }
    out
}

pub fn undervolt_from_conf(text: &str) -> (i32, i32) {
    let mut section = false;
    let mut offsets = (0, 0);
    for line in text.lines().map(str::trim) {
        if line.starts_with('[') { section = line.eq_ignore_ascii_case("[intelUndervolt]"); continue; }
        if !section { continue; }
        let Some((key, value)) = line.split_once('=') else { continue };
        let value = value.trim().parse::<i32>().unwrap_or(0).clamp(-250, 0);
        match key.trim() { "core" => offsets.0 = value, "cache" => offsets.1 = value, _ => {} }
    }
    offsets
}

pub fn upsert_undervolt(text: &str, core: i32, cache: i32) -> String {
    let mut output = String::new();
    let mut replacing = false;
    for line in text.lines() {
        if line.trim().starts_with('[') { replacing = line.trim().eq_ignore_ascii_case("[intelUndervolt]"); }
        if !replacing { output.push_str(line); output.push('\n'); }
    }
    output.push_str(&format!("\n[intelUndervolt]\ncore={core}\ncache={cache}\n"));
    output
}

pub fn diagnostics_from_logs(module_errors: Option<&str>, acpi: Option<&str>, daemon: Option<&str>) -> String {
    let capabilities: &[victus_core::Capability] = &[];
    let modules: &[(&str, bool)] = &[];
    let session: &[String] = &[];
    render_markdown("local", &[("version", PROGRAM_VERSION)], capabilities, modules, session, daemon, module_errors, acpi)
}

pub mod ui;

pub fn connects_to_socket(model: &Model) -> Option<&Path> {
    if model.offline || model.socket.as_os_str().is_empty() {
        None
    } else {
        Some(model.socket.as_path())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use victus_core::DEFAULT_SOCKET;

    #[test]
    fn offline_model_has_no_socket_and_fan_steps_stay_ordered() {
        let model = Model::offline(4);
        assert!(connects_to_socket(&model).is_none());
        assert_ne!(model.socket, PathBuf::from(DEFAULT_SOCKET));
        assert!(sensor_request(3).is_none());
        assert!(sensor_request(5).is_none());
        assert!(sensor_request(0).unwrap().starts_with("sensors\t"));
        let mut model = model;
        let requests = model.select_fan_mode(FanMode::Auto);
        assert_eq!(requests.len(), 3);
        let flags: Vec<(bool, Option<String>, bool)> = requests
            .iter()
            .map(|line| {
                let json = line.split_once('\t').unwrap().1;
                let value: Value = serde_json::from_str(json).unwrap();
                (
                    value["smart_curve_enabled"].as_bool().unwrap(),
                    value["manual_preset"].as_str().map(str::to_owned),
                    value["custom_curve_enabled"].as_bool().unwrap(),
                )
            })
            .collect();
        assert!(!flags[0].0);
        assert_eq!(flags[1].1.as_deref(), Some("auto"));
        assert!(!flags[2].2);
        let smart = fan_requests(&DaemonState::default().fan, FanMode::Smart);
        let first: Value = serde_json::from_str(smart[0].split_once('\t').unwrap().1).unwrap();
        assert_eq!(first["smart_curve_enabled"], json!(true));
        assert_eq!(first["custom_curve_enabled"], json!(true));
        assert!(first["manual_preset"].is_null());
    }

    #[test]
    fn hydrate_keeps_daemon_policy_and_release_tags_parse_offline() {
        let mut model = Model::offline(1);
        model.hydrate(&json!({"fan": {"manual_preset": "max", "custom_curve_enabled": true}, "initialized": true}));
        assert_eq!(model.state.fan.manual_preset.as_deref(), Some("max"));
        assert!(model.state.initialized);
        assert_eq!(parse_release_tag(r#"{"tag_name":"v1.2.3"}"#).as_deref(), Some("v1.2.3"));
        assert!(parse_release_tag(r#"{"tag_name":"main"}"#).is_none());
        assert_eq!(update_choice("v1.0.3", PROGRAM_VERSION), UpdateChoice::Current);
        assert_eq!(update_choice("v1.0.10", PROGRAM_VERSION), UpdateChoice::Available);
        assert!(update_shell().contains(GITHUB_INSTALL_COMMAND));
        assert!(!update_shell().contains("QT_QPA_PLATFORM"));
        assert_eq!(model.keyboard_zone("W", 10.0, 300.0), 3);
        assert_eq!(model.keyboard_zone("P", 250.0, 300.0), 0);
        let report = diagnostics_from_logs(Some("hp_wmi Unknown EC"), None, Some(""));
        assert!(report.contains("## hp-wmi / RGB module errors"));
        assert!(report.contains("Unknown EC"));
    }

    #[test]
    fn power_status_and_shortcut_conf_stay_on_the_given_text() {
        let root = victus_core::offline_scratch("power-status");
        assert!(root.starts_with(std::env::temp_dir()));
        assert_eq!(power_status_line(&root), "—");
        assert_eq!(power_status_line(Path::new("")), "—");
        let battery = root.join("BAT0");
        std::fs::create_dir_all(&battery).unwrap();
        std::fs::write(battery.join("type"), "Battery\n").unwrap();
        std::fs::write(battery.join("status"), "Discharging\n").unwrap();
        std::fs::write(battery.join("capacity"), "42\n").unwrap();
        assert_eq!(power_status_line(&root), "Battery · 42%");

        let text = "[General]\nheight=680\n\n[programShortcut]\nenabled=true\nkey=149\nmods=\n\n[window]\nwidth=460\n";
        assert_eq!(program_shortcut_from_conf(text), (Vec::<i32>::new(), 149));
        let updated = upsert_program_shortcut(text, &[29], 24);
        assert!(updated.contains("height=680"));
        assert!(updated.contains("width=460"));
        assert!(updated.contains("key=24"));
        assert!(updated.contains("mods=29"));
        assert_eq!(program_shortcut_from_conf(&updated).1, 24);
    }

    #[test]
    fn undervolt_offsets_survive_settings_updates_without_overwriting_other_sections() {
        let text = "[General]\nheight=680\n\n[intelUndervolt]\ncore=-100\ncache=-50\n\n[programShortcut]\nkey=149\nmods=\n";
        assert_eq!(undervolt_from_conf(text), (-100, -50));
        let updated = upsert_undervolt(text, 0, 0);
        assert_eq!(undervolt_from_conf(&updated), (0, 0));
        assert_eq!(program_shortcut_from_conf(&updated).1, 149);
        assert!(updated.contains("height=680"));
        assert_eq!(updated.matches("[intelUndervolt]").count(), 1);
        let mut model = Model::offline(1);
        model.hydrate(&serde_json::json!({"profile": 2}));
        assert_eq!(model.profile, 2);
    }
}
