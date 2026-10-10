/// `VICTUS_HUB_DEBUG_LEVEL` is 0 (errors) through 3 (keyboard).
pub fn debug_level_from_value(raw: &str) -> Result<i32, crate::HubError> {
    match raw {
        "0" | "1" | "2" | "3" => Ok(raw.parse().expect("digit")),
        _ => Err(crate::HubError::new(
            "VICTUS_HUB_DEBUG_LEVEL must be 0, 1, 2 or 3",
        )),
    }
}

pub fn message_debug_level(text: &str) -> Option<i32> {
    let text = text.to_ascii_lowercase();
    if contains_any(&text, &["keyboard", "kbd", "shortcut"]) {
        return Some(3);
    }
    if contains_any(&text, &["power", "ryzenadj", "rapl"])
        || text.contains("cpu frequency")
        || text.contains("cpu-frequency")
        || text.contains("scaling_min_freq")
        || text.contains("scaling_max_freq")
    {
        return Some(2);
    }
    if text.split(|ch: char| !ch.is_ascii_alphanumeric() && ch != '_').any(|word| word == "fan" || word == "fans")
        || text.contains("fan_control")
        || text.contains("pwm")
    {
        return Some(1);
    }
    None
}

fn contains_any(text: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| text.contains(needle))
}

pub fn terminal_line_visible(text: &str, level: i32, is_error: bool) -> bool {
    if is_error {
        return true;
    }
    match message_debug_level(text) {
        Some(category) => category > 0 && category <= level,
        None => false,
    }
}

const DAEMON_LOG_NOISE: &[&str] = &["keyboard-last-event", "keyboard-last-input", "[cpu-power]"];

pub fn journal_line_visible(line: &str, level: i32) -> bool {
    if DAEMON_LOG_NOISE.iter().any(|noise| line.contains(noise)) {
        return false;
    }
    let lower = line.to_ascii_lowercase();
    let is_error = ["error", "err", "traceback", "exception", "failed"]
        .iter()
        .any(|word| lower.split(|ch: char| !ch.is_ascii_alphanumeric()).any(|part| part == *word));
    // "err" as a whole word also matches the python regex `\berr(?:or)?\b`.
    terminal_line_visible(line, level, is_error)
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/loglevel.rs"]
mod tests;
