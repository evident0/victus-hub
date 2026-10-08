//! Copies of the settings the Python UI writes under `~/.config/victus-hub`.
//!
//! Fan curves go to `config.json`. The other keys stay in `victus-hub.conf`,
//! in the same INI and `@Variant` form QSettings reads. Sections this port
//! does not own, such as window geometry, are left untouched.

use std::fs;
use std::path::{Path, PathBuf};

use victus_core::{
    clamp_power_limit, clamp_reapply_seconds, clamp_tctl_temp, config_to_value, DaemonState, FanConfig, LightingSettings,
    PowerPolicy,
};

pub(crate) fn remember_fan(offline: bool, conf: &Path, fan: &FanConfig) -> Result<(), String> {
    let Some(path) = fan_path(offline, conf) else { return Ok(()) };
    let text = serde_json::to_string_pretty(&config_to_value(fan)).map_err(|error| error.to_string())?;
    write_new(&path, &text, "fan settings")
}

pub(crate) fn remember_lighting(offline: bool, conf: &Path, lighting: &LightingSettings) -> Result<(), String> {
    remember_assignments(offline, conf, &[("keyboardLighting", &lighting_variant(lighting))], "keyboard lighting")
}

pub(crate) fn remember_power(offline: bool, conf: &Path, intel: bool, power: &PowerPolicy) -> Result<(), String> {
    let enabled = bool_text(power.enabled).to_owned();
    let reapply = clamp_reapply_seconds(power.reapply_seconds).to_string();
    let (group, limits): (&str, Vec<(String, String)>) = if intel {
        let pl1 = clamp_power_limit(power.slow_limit);
        let pl2 = pl1.max(clamp_power_limit(power.fast_limit));
        ("intelPowerLimits", vec![("pl1".into(), pl1.to_string()), ("pl2".into(), pl2.to_string())])
    } else {
        (
            "powerLimits",
            vec![
                ("stapm".into(), clamp_power_limit(power.stapm_limit).to_string()),
                ("fast".into(), clamp_power_limit(power.fast_limit).to_string()),
                ("slow".into(), clamp_power_limit(power.slow_limit).to_string()),
                ("tctlTemp".into(), clamp_tctl_temp(power.tctl_temp).to_string()),
            ],
        )
    };
    let mut pairs = vec![(format!("{group}/enabled"), enabled), (format!("{group}/reapplySeconds"), reapply)];
    pairs.extend(limits.into_iter().map(|(key, value)| (format!("{group}/{key}"), value)));
    let borrowed = pairs.iter().map(|(key, value)| (key.as_str(), value.as_str())).collect::<Vec<_>>();
    remember_assignments(offline, conf, &borrowed, "power settings")
}

pub(crate) fn remember_frequency(offline: bool, conf: &Path, minimum: i32, maximum: i32) -> Result<(), String> {
    let value = format!("{minimum}, {maximum}");
    remember_assignments(offline, conf, &[("cpuFrequency/limits", value.as_str())], "CPU frequency")
}

pub(crate) fn remember_nvidia(offline: bool, conf: &Path, disabled: bool) -> Result<(), String> {
    let value = bool_text(disabled);
    remember_assignments(offline, conf, &[("sensors/disableNvidiaQueries", value)], "NVIDIA query setting")
}

pub(crate) fn remember_hardware_shortcuts(offline: bool, conf: &Path, enabled: bool) -> Result<(), String> {
    let value = bool_text(enabled);
    remember_assignments(offline, conf, &[("keyboard/fn_shortcuts_enabled", value)], "keyboard shortcuts")
}

/// Rewrite the local files from daemon state. Python does this on startup once
/// the daemon has already been initialized, so a reopen cannot keep a stale copy.
pub(crate) fn mirror_daemon(offline: bool, conf: &Path, intel: bool, state: &DaemonState) -> Result<(), String> {
    remember_fan(offline, conf, &state.fan)?;
    remember_lighting(offline, conf, &state.lighting)?;
    remember_power(offline, conf, intel, &state.power)?;
    if let Some((minimum, maximum)) = state.cpu_frequency.filter(|(minimum, maximum)| *minimum > 0 && *maximum >= *minimum) {
        remember_frequency(offline, conf, minimum, maximum)?;
    }
    let battery = bool_text(state.battery_power_save);
    let shortcuts = bool_text(state.hardware_shortcuts);
    let nvidia = bool_text(state.disable_nvidia_queries);
    remember_assignments(
        offline,
        conf,
        &[
            ("power/save_on_battery", battery),
            ("keyboard/fn_shortcuts_enabled", shortcuts),
            ("sensors/disableNvidiaQueries", nvidia),
        ],
        "settings",
    )
}

fn remember_assignments(offline: bool, conf: &Path, updates: &[(&str, &str)], what: &str) -> Result<(), String> {
    if offline || conf.as_os_str().is_empty() || updates.is_empty() {
        return Ok(());
    }
    let existing = fs::read_to_string(conf).unwrap_or_default();
    write_new(conf, &upsert_ini(&existing, updates), what)
}

fn fan_path(offline: bool, conf: &Path) -> Option<PathBuf> {
    if offline || conf.as_os_str().is_empty() {
        return None;
    }
    Some(conf.parent()?.join("config.json"))
}

fn write_new(path: &Path, text: &str, what: &str) -> Result<(), String> {
    if let Some(parent) = path.parent().filter(|parent| !parent.as_os_str().is_empty()) {
        fs::create_dir_all(parent).map_err(|error| format!("Could not save {what}: {error}"))?;
    }
    let temporary = path.with_extension(format!("{}.tmp", path.extension().and_then(|ext| ext.to_str()).unwrap_or("tmp")));
    fs::write(&temporary, text).map_err(|error| format!("Could not save {what}: {error}"))?;
    fs::rename(&temporary, path).map_err(|error| format!("Could not save {what}: {error}"))
}

fn bool_text(value: bool) -> &'static str {
    if value { "true" } else { "false" }
}

pub(crate) fn upsert_ini(text: &str, updates: &[(&str, &str)]) -> String {
    let mut lines: Vec<String> = text.lines().map(str::to_owned).collect();
    for (path, value) in updates {
        let (section, key) = path.split_once('/').unwrap_or(("General", path));
        assign(&mut lines, section, key, value);
    }
    if lines.is_empty() {
        return String::new();
    }
    let mut out = lines.join("\n");
    out.push('\n');
    out
}

fn assign(lines: &mut Vec<String>, section: &str, key: &str, value: &str) {
    let assignment = format!("{key}={value}");
    if let Some((start, end)) = section_range(lines, section) {
        if let Some(index) = (start..end).find(|index| is_key(&lines[*index], key)) {
            lines[index] = assignment;
            return;
        }
        let mut at = end;
        while at > start && lines[at - 1].trim().is_empty() {
            at -= 1;
        }
        lines.insert(at, assignment);
        return;
    }
    if lines.last().is_some_and(|line| !line.is_empty()) {
        lines.push(String::new());
    }
    lines.push(format!("[{section}]"));
    lines.push(assignment);
}

fn section_range(lines: &[String], section: &str) -> Option<(usize, usize)> {
    let header = format!("[{section}]");
    let start = lines.iter().position(|line| line.trim() == header)?;
    let end = lines
        .iter()
        .skip(start + 1)
        .position(|line| {
            let trimmed = line.trim();
            trimmed.starts_with('[') && trimmed.ends_with(']')
        })
        .map_or(lines.len(), |offset| start + 1 + offset);
    Some((start + 1, end))
}

fn is_key(line: &str, key: &str) -> bool {
    let line = line.trim();
    line.starts_with(key) && line[key.len()..].trim_start().starts_with('=')
}

fn lighting_variant(settings: &LightingSettings) -> String {
    let mut entries = vec![
        ("brightness", Variant::Int(settings.brightness)),
        ("color", Variant::Str(settings.color.clone())),
        ("color2", Variant::Str(settings.color2.clone())),
        ("effect", Variant::Str(settings.effect.clone())),
        ("enabled", Variant::Bool(settings.enabled)),
        ("idle_timeout", Variant::Int(settings.idle_timeout)),
        ("speed", Variant::Int(settings.speed)),
    ];
    if !settings.zone_colors.is_empty() {
        entries.push(("zone_colors", Variant::Strings(settings.zone_colors.clone())));
    }
    entries.sort_by_key(|(key, _)| *key);
    let mut bytes = Vec::new();
    push_u32(&mut bytes, 8);
    push_u32(&mut bytes, u32::try_from(entries.len()).unwrap_or(0));
    for (key, value) in entries {
        push_qstring(&mut bytes, key);
        value.write(&mut bytes);
    }
    format!("@Variant({})", escape_qt(&bytes))
}

enum Variant {
    Bool(bool),
    Int(i32),
    Str(String),
    Strings(Vec<String>),
}

impl Variant {
    fn write(self, out: &mut Vec<u8>) {
        match self {
            Self::Bool(value) => {
                push_u32(out, 1);
                out.push(u8::from(value));
            }
            Self::Int(value) => {
                push_u32(out, 2);
                out.extend(value.to_be_bytes());
            }
            Self::Str(value) => {
                push_u32(out, 10);
                push_qstring(out, &value);
            }
            Self::Strings(values) => {
                push_u32(out, 11);
                push_u32(out, u32::try_from(values.len()).unwrap_or(0));
                for value in values {
                    push_qstring(out, &value);
                }
            }
        }
    }
}

fn push_u32(out: &mut Vec<u8>, value: u32) {
    out.extend(value.to_be_bytes());
}

fn push_qstring(out: &mut Vec<u8>, text: &str) {
    let units = text.encode_utf16().collect::<Vec<_>>();
    push_u32(out, u32::try_from(units.len().saturating_mul(2)).unwrap_or(u32::MAX));
    for unit in units {
        out.extend(unit.to_be_bytes());
    }
}

/// Qt's INI codec. Hex digits are escaped so a following character is not
/// swallowed by `\x`, and the named controls match `QSettings`.
fn escape_qt(bytes: &[u8]) -> String {
    let mut out = String::new();
    for &byte in bytes {
        match byte {
            0 => out.push_str("\\0"),
            7 => out.push_str("\\a"),
            8 => out.push_str("\\b"),
            9 => out.push_str("\\t"),
            10 => out.push_str("\\n"),
            11 => out.push_str("\\v"),
            12 => out.push_str("\\f"),
            13 => out.push_str("\\r"),
            b'\\' => out.push_str("\\\\"),
            b'"' => out.push_str("\\\""),
            // Qt omits the leading zero. A one-digit `\x` cannot swallow the
            // next character: every hex digit is escaped, so it starts with `\`.
            value if value < 0x10 => out.push_str(&format!("\\x{value:x}")),
            value if value.is_ascii_hexdigit() || !value.is_ascii_graphic() && value != b' ' => {
                out.push_str(&format!("\\x{value:02x}"));
            }
            value => out.push(char::from(value)),
        }
    }
    out
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hub/persist.rs"]
mod tests;
