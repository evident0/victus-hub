//! Read the Qt INI settings used before the Rust port. No Qt runtime is needed.

use std::collections::HashMap;

use serde_json::{Map, Value, json};
use victus_core::{DaemonState, state_from_value};

pub fn settings(text: &str) -> HashMap<String, Value> {
    let mut output = HashMap::new();
    let mut section = String::new();
    for line in text.lines().map(str::trim) {
        if line.starts_with([';', '#']) || line.is_empty() { continue; }
        if let Some(name) = line.strip_prefix('[').and_then(|name| name.strip_suffix(']')) {
            section = if name == "General" { String::new() } else { name.to_owned() };
            continue;
        }
        let Some((key, raw)) = line.split_once('=') else { continue };
        let key = key.trim().replace('\\', "/");
        let key = if section.is_empty() { key } else { format!("{section}/{key}") };
        if let Some(value) = ini_value(raw.trim()) { output.insert(key, value); }
    }
    output
}

fn ini_value(raw: &str) -> Option<Value> {
    let raw = raw.strip_prefix('"').and_then(|text| text.strip_suffix('"')).unwrap_or(raw);
    let bytes = unescape(raw)?;
    if bytes.starts_with(b"@Variant(") && bytes.ends_with(b")") {
        let mut stream = QtStream { bytes: &bytes[9..bytes.len() - 1] };
        let value = stream.variant(0)?;
        return stream.bytes.is_empty().then_some(value);
    }
    let text = String::from_utf8(bytes).ok()?;
    if text == "@Invalid()" { return Some(Value::Null); }
    if text == "true" { return Some(json!(true)); }
    if text == "false" { return Some(json!(false)); }
    if let Ok(number) = text.parse::<i64>() { return Some(json!(number)); }
    if text.contains(',') && !text.starts_with('@') {
        return Some(Value::Array(text.split(',').map(|part| ini_value(part.trim()).unwrap_or(Value::Null)).collect()));
    }
    Some(Value::String(text))
}

fn unescape(text: &str) -> Option<Vec<u8>> {
    let mut chars = text.chars().peekable();
    let mut out = Vec::new();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            if ch.is_ascii() { out.push(ch as u8); }
            else { out.extend_from_slice(ch.to_string().as_bytes()); }
            continue;
        }
        let code = chars.next()?;
        let value = match code {
            'a' => 7, 'b' => 8, 'f' => 12, 'n' => 10, 'r' => 13, 't' => 9, 'v' => 11,
            '\\' => b'\\', '"' => b'"', '\'' => b'\'', '?' => b'?',
            'x' => {
                let mut value = 0_u32;
                let mut digits = 0;
                while let Some(digit) = chars.peek().and_then(|ch| ch.to_digit(16)) {
                    chars.next();
                    value = value.checked_mul(16)?.checked_add(digit)?;
                    digits += 1;
                }
                if digits == 0 { return None; }
                u8::try_from(value).ok()?
            }
            '0'..='7' => {
                let mut value = code.to_digit(8)?;
                while let Some(digit) = chars.peek().and_then(|ch| ch.to_digit(8)) {
                    chars.next();
                    value = value.checked_mul(8)?.checked_add(digit)?;
                }
                u8::try_from(value).ok()?
            }
            _ => continue,
        };
        out.push(value);
    }
    Some(out)
}

// QSettings writes @Variant using the big-endian Qt_4_0 stream format,
// including QVariantMap/List and UTF-16 QStrings, without a null-flag byte.
struct QtStream<'a> { bytes: &'a [u8] }

impl QtStream<'_> {
    fn take<const N: usize>(&mut self) -> Option<[u8; N]> {
        let value = self.bytes.get(..N)?.try_into().ok()?;
        self.bytes = &self.bytes[N..];
        Some(value)
    }
    fn number(&mut self) -> Option<u32> { Some(u32::from_be_bytes(self.take()?)) }
    fn string(&mut self) -> Option<String> {
        let length = self.number()?;
        if length == u32::MAX { return Some(String::new()); }
        let length = usize::try_from(length).ok()?;
        if length % 2 != 0 { return None; }
        let data = self.bytes.get(..length)?;
        let units = data.chunks_exact(2).map(|pair| u16::from_be_bytes([pair[0], pair[1]])).collect::<Vec<_>>();
        self.bytes = &self.bytes[length..];
        String::from_utf16(&units).ok()
    }
    fn variant(&mut self, depth: usize) -> Option<Value> {
        if depth > 16 { return None; }
        let kind = self.number()?;
        match kind {
            1 => Some(json!(self.take::<1>()?[0] != 0)),
            2 => Some(json!(i32::from_be_bytes(self.take()?))),
            3 => Some(json!(self.number()?)),
            4 => Some(json!(i64::from_be_bytes(self.take()?))),
            5 => Some(json!(u64::from_be_bytes(self.take()?))),
            6 => serde_json::Number::from_f64(f64::from_be_bytes(self.take()?)).map(Value::Number),
            8 => {
                let count = self.number()?;
                if count > 4096 { return None; }
                let mut map = Map::new();
                for _ in 0..count { map.insert(self.string()?, self.variant(depth + 1)?); }
                Some(Value::Object(map))
            }
            9 | 11 => {
                let count = self.number()?;
                if count > 4096 { return None; }
                let mut items = Vec::new();
                for _ in 0..count {
                    items.push(if kind == 11 { Value::String(self.string()?) } else { self.variant(depth + 1)? });
                }
                Some(Value::Array(items))
            }
            10 => Some(Value::String(self.string()?)),
            _ => None,
        }
    }
}

pub fn migrated_state(text: &str, fan: Option<&Value>, intel: bool) -> Option<DaemonState> {
    let values = settings(text);
    let relevant = ["keyboardLighting", "power/save_on_battery", "keyboard/fn_shortcuts_enabled", "sensors/disableNvidiaQueries", "cpuFrequency/limits"];
    let power_group = if intel { "intelPowerLimits" } else { "powerLimits" };
    if fan.is_none() && !relevant.iter().any(|key| values.contains_key(*key))
        && !values.keys().any(|key| key.starts_with(&format!("{power_group}/"))) { return None; }
    let get = |key: &str, default: Value| values.get(key).cloned().unwrap_or(default);
    let power = |key: &str, default: Value| get(&format!("{power_group}/{key}"), default);
    let state = json!({
        "fan": fan,
        "lighting": get("keyboardLighting", victus_core::lighting_to_value(&victus_core::LightingSettings::default())),
        "power": {
            "enabled": power("enabled", json!(false)),
            "stapm_limit": power("stapm", json!(25000)),
            "fast_limit": power(if intel { "pl2" } else { "fast" }, json!(25000)),
            "slow_limit": power(if intel { "pl1" } else { "slow" }, json!(25000)),
            "tctl_temp": power("tctlTemp", json!(95)),
            "reapply_seconds": power("reapplySeconds", json!(5)),
        },
        "cpu_frequency": get("cpuFrequency/limits", Value::Null),
        "battery_power_save": get("power/save_on_battery", json!(false)),
        "hardware_shortcuts": get("keyboard/fn_shortcuts_enabled", json!(false)),
        "disable_nvidia_queries": get("sensors/disableNvidiaQueries", json!(false)),
    });
    let mut state = state_from_value(&state);
    if intel { state.power.fast_limit = state.power.fast_limit.max(state.power.slow_limit); }
    Some(state)
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hub/legacy.rs"]
mod tests;
