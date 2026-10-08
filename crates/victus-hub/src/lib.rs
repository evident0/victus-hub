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

#[derive(Debug, Clone)]
pub struct Model {
    pub page: usize,
    pub offline: bool,
    pub socket: PathBuf,
    pub state: DaemonState,
    pub zones: i32,
    pub status: String,
    pub snapshot: SensorSnapshot,
    pub history: VecDeque<f64>,
    pub visible: bool,
    pub quit: bool,
}

impl Model {
    pub fn offline(zones: i32) -> Self {
        Self {
            page: 0,
            offline: true,
            socket: PathBuf::new(),
            state: DaemonState::default(),
            zones: zones.max(1),
            status: "Offline preview".into(),
            snapshot: SensorSnapshot::default(),
            history: VecDeque::new(),
            visible: true,
            quit: false,
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
}
