use crate::{HubError, HubResult};

pub const KEY_LEFTCTRL: i32 = 29;
pub const KEY_LEFTSHIFT: i32 = 42;
pub const KEY_LEFTALT: i32 = 56;
pub const KEY_RIGHTCTRL: i32 = 97;
pub const KEY_RIGHTSHIFT: i32 = 54;
pub const KEY_RIGHTALT: i32 = 100;
pub const KEY_LEFTMETA: i32 = 125;
pub const KEY_RIGHTMETA: i32 = 126;
pub const KEY_FN: i32 = 464;

const MODIFIERS: &[i32] = &[
    KEY_LEFTCTRL,
    KEY_RIGHTCTRL,
    KEY_LEFTSHIFT,
    KEY_RIGHTSHIFT,
    KEY_LEFTALT,
    KEY_RIGHTALT,
    KEY_LEFTMETA,
    KEY_RIGHTMETA,
    KEY_FN,
];

const COMMAND_MODIFIERS: &[i32] = &[
    KEY_LEFTCTRL,
    KEY_LEFTALT,
    KEY_RIGHTCTRL,
    KEY_RIGHTALT,
    KEY_LEFTMETA,
    KEY_RIGHTMETA,
];

/// Ctrl+Shift, the chord the daemon uses for hardware lighting and profile keys.
pub const HARDWARE_SHORTCUT_MODS: &[i32] = &[KEY_LEFTCTRL, KEY_LEFTSHIFT];

const MOD_LABELS: &[(i32, &str)] = &[
    (KEY_LEFTCTRL, "Ctrl"),
    (KEY_RIGHTCTRL, "Ctrl"),
    (KEY_LEFTSHIFT, "Shift"),
    (KEY_RIGHTSHIFT, "Shift"),
    (KEY_LEFTALT, "Alt"),
    (KEY_RIGHTALT, "Alt"),
    (KEY_LEFTMETA, "Super"),
    (KEY_RIGHTMETA, "Super"),
    (KEY_FN, "Fn"),
];

const MOD_ORDER: &[&str] = &["Ctrl", "Shift", "Alt", "Super", "Fn"];

const LETTERS: &[(i32, &str)] = &[
    (16, "Q"),
    (17, "W"),
    (18, "E"),
    (19, "R"),
    (20, "T"),
    (21, "Y"),
    (22, "U"),
    (23, "I"),
    (24, "O"),
    (25, "P"),
    (30, "A"),
    (31, "S"),
    (32, "D"),
    (33, "F"),
    (34, "G"),
    (35, "H"),
    (36, "J"),
    (37, "K"),
    (38, "L"),
    (44, "Z"),
    (45, "X"),
    (46, "C"),
    (47, "V"),
    (48, "B"),
    (49, "N"),
    (50, "M"),
];

pub fn is_modifier(code: i32) -> bool {
    MODIFIERS.contains(&code)
}

pub fn is_special_key(code: i32) -> bool {
    (59..=68).contains(&code)
        || matches!(code, 87 | 88 | 138 | 148 | 149 | 171 | 202 | 203 | 226)
}

pub fn validate_shortcut(mods: &[i32], key: i32) -> HubResult<(Vec<i32>, i32)> {
    if !(0..=0x2ff).contains(&key) || is_modifier(key) {
        return Err(HubError::new("invalid shortcut key"));
    }
    if mods.iter().any(|code| !is_modifier(*code)) {
        return Err(HubError::new("invalid shortcut modifiers"));
    }
    let mut normalized = mods.to_vec();
    normalized.sort_unstable();
    normalized.dedup();
    if key != 0 && !(normalized.iter().any(|code| COMMAND_MODIFIERS.contains(code)) || is_special_key(key))
    {
        return Err(HubError::new(
            "Use Ctrl, Alt or Super with a key, or a function/OMEN key",
        ));
    }
    Ok((normalized, key))
}

pub fn key_name(code: i32) -> String {
    if let Some((_, label)) = LETTERS.iter().find(|(value, _)| *value == code) {
        return (*label).to_owned();
    }
    match code {
        1 => "Esc".to_owned(),
        14 => "Backspace".to_owned(),
        15 => "Tab".to_owned(),
        28 => "Enter".to_owned(),
        57 => "Space".to_owned(),
        58 => "Caps".to_owned(),
        102 => "Home".to_owned(),
        103 => "Up".to_owned(),
        104 => "Page Up".to_owned(),
        105 => "Left".to_owned(),
        106 => "Right".to_owned(),
        107 => "End".to_owned(),
        108 => "Down".to_owned(),
        109 => "Page Down".to_owned(),
        110 => "Insert".to_owned(),
        111 => "Delete".to_owned(),
        138 => "Help".to_owned(),
        148 => "Prog1".to_owned(),
        149 => "Omen Key".to_owned(),
        171 => "Config".to_owned(),
        172 => "Home Key".to_owned(),
        202 => "Prog3".to_owned(),
        203 => "Prog4".to_owned(),
        226 => "Media".to_owned(),
        2..=10 => (code - 1).to_string(),
        11 => "0".to_owned(),
        59..=68 => format!("F{}", code - 58),
        87 => "F11".to_owned(),
        88 => "F12".to_owned(),
        _ => format!("Key {code}"),
    }
}

pub fn keybind_label(mods: &[i32], key: i32) -> String {
    if key == 0 {
        return "Not set".to_owned();
    }
    let mut seen = Vec::new();
    for code in mods {
        if let Some((_, label)) = MOD_LABELS.iter().find(|(value, _)| value == code) {
            if !seen.contains(label) {
                seen.push(*label);
            }
        }
    }
    let mut parts: Vec<&str> = MOD_ORDER.iter().copied().filter(|label| seen.contains(label)).collect();
    let name = key_name(key);
    parts.push(name.as_str());
    parts.join(" + ")
}

/// Hardware shortcut action. Key codes are Linux input events.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HardwareAction {
    Brightness(i32),
    Effect(i32),
    CycleProfile,
}

pub fn hardware_action(mods: &[i32], key: i32) -> Option<HardwareAction> {
    if key == 0 {
        return None;
    }
    let mut mods = mods.to_vec();
    mods.sort_unstable();
    let mut expected = HARDWARE_SHORTCUT_MODS.to_vec();
    expected.sort_unstable();
    if mods != expected {
        return None;
    }
    match key {
        103 => Some(HardwareAction::Brightness(1)),
        108 => Some(HardwareAction::Brightness(-1)),
        106 => Some(HardwareAction::Effect(1)),
        105 => Some(HardwareAction::Effect(-1)),
        50 => Some(HardwareAction::CycleProfile),
        _ => None,
    }
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/shortcuts.rs"]
mod tests;
