/// Informational sensors each page may ask the daemon to sample.
///
/// Page order matches the sidebar: Home, Power, Fans, Keyboard, Sensors, Settings.
pub const HOME_PAGE_KEYS: &[&str] = &[
    "cpu-temp",
    "cpu-usage",
    "cpu-power",
    "gpu-temp",
    "gpu-usage",
    "gpu-power",
    "cpu-fan",
    "gpu-fan",
    "ram-usage",
];

pub const POWER_PAGE_KEYS: &[&str] = &["cpu-power", "cpu-frequency"];
pub const FANS_PAGE_KEYS: &[&str] = &["cpu-fan", "gpu-fan"];
pub const KEYBOARD_PAGE_KEYS: &[&str] = &[];
pub const SETTINGS_PAGE_KEYS: &[&str] = &[];

pub const GPU_QUERY_KEYS: &[&str] = &["gpu-temp", "gpu-usage", "gpu-power"];

pub fn sensors_page_keys() -> Vec<&'static str> {
    let mut keys = HOME_PAGE_KEYS.to_vec();
    for key in POWER_PAGE_KEYS {
        if !keys.contains(key) {
            keys.push(key);
        }
    }
    keys.extend(["pwm-value", "pwm-mode", "lm-sensors"]);
    keys
}

pub fn requestable_keys() -> Vec<&'static str> {
    sensors_page_keys()
}

pub fn keys_for_page(index: usize) -> Vec<&'static str> {
    match index {
        0 => HOME_PAGE_KEYS.to_vec(),
        1 => POWER_PAGE_KEYS.to_vec(),
        2 => FANS_PAGE_KEYS.to_vec(),
        3 => KEYBOARD_PAGE_KEYS.to_vec(),
        4 => sensors_page_keys(),
        5 => SETTINGS_PAGE_KEYS.to_vec(),
        _ => Vec::new(),
    }
}

pub fn request_key_for_graph(sensor_key: &str) -> &str {
    if sensor_key.starts_with("cpu-frequency-") {
        "cpu-frequency"
    } else if sensor_key.starts_with("lm-") {
        "lm-sensors"
    } else {
        sensor_key
    }
}

pub fn unknown_sensor_keys(keys: &[String]) -> Vec<String> {
    let allowed = requestable_keys();
    keys.iter()
        .filter(|key| !allowed.contains(&key.as_str()))
        .cloned()
        .collect()
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-core/keys.rs"]
mod tests;
