//! GTK 4 control panel. Offline preview never opens the installed socket.

use std::path::{Path, PathBuf};

use victus_hub::{FrequencyWindow, MuxChoice};

fn main() {
    let offline = std::env::var("VICTUS_HUB_OFFLINE").ok().as_deref() == Some("1");
    let mut model = if offline {
        victus_hub::Model::offline(zone_count(true))
    } else {
        let socket = std::env::var("VICTUS_HUB_SOCKET")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(victus_core::DEFAULT_SOCKET));
        victus_hub::Model::live(socket, zone_count(false))
    };
    fill_host(&mut model, offline);
    if !offline {
        victus_hub::ui::hydrate_live(&mut model);
    }
    let own_bus = !offline && std::env::var("VICTUS_HUB_NO_DBUS").ok().as_deref() != Some("1");
    if let Err(error) = victus_hub::ui::start(model, own_bus) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

/// Zone count is read-only. Offline preview uses `VICTUS_HUB_EMULATE_ZONES`
/// and does not open `/sys`.
fn zone_count(offline: bool) -> i32 {
    if let Ok(value) = std::env::var("VICTUS_HUB_EMULATE_ZONES") {
        if let Ok(zones) = value.parse::<i32>() {
            return zones.max(1);
        }
    }
    if offline {
        return 1;
    }
    std::fs::read_to_string("/sys/devices/platform/hp-kbd-rgb/zone_count")
        .ok()
        .and_then(|text| text.trim().parse().ok())
        .unwrap_or(1)
        .max(1)
}

fn fill_host(model: &mut victus_hub::Model, offline: bool) {
    let host = &mut model.host;
    host.intel = std::fs::read_to_string("/proc/cpuinfo").is_ok_and(|text| text.contains("GenuineIntel"));
    host.product = std::fs::read_to_string("/sys/class/dmi/id/product_name")
        .ok()
        .map(|text| text.trim().to_owned())
        .filter(|text| !text.is_empty())
        .unwrap_or_else(|| "HP Laptop".to_owned());
    let supply = PathBuf::from("/sys/class/power_supply");
    host.power_supply = if supply.is_dir() { supply } else { PathBuf::new() };
    host.conf_path = config_path();
    if !host.conf_path.as_os_str().is_empty() {
        if let Ok(text) = std::fs::read_to_string(&host.conf_path) {
            let (mods, key) = victus_hub::program_shortcut_from_conf(&text);
            host.shortcut_mods = mods;
            host.shortcut_key = key;
        }
    }
    match victus_hw::read_policies(Path::new("/sys/devices/system/cpu/cpufreq")) {
        Ok(policies) => {
            let first = &policies[0];
            let mixed = policies.iter().any(|policy| policy.minimum != first.minimum || policy.maximum != first.maximum);
            host.frequency = Some(FrequencyWindow {
                lower: first.hardware_min,
                upper: first.hardware_max,
                minimum: first.minimum,
                maximum: first.maximum,
                policies: policies.len(),
                mixed,
            });
        }
        Err(error) => host.frequency_error = error.to_string(),
    }
    if offline {
        let emulated = std::env::var("VICTUS_HUB_EMULATE_ZONES").ok().and_then(|value| value.parse::<i32>().ok());
        host.keyboard = emulated.is_some_and(|zones| zones > 0) || model.zones > 1;
        return;
    }
    let caps = victus_hw::detect_capabilities(
        Path::new("/sys/class/hwmon"),
        Path::new("/sys/class/leds"),
        Path::new("/sys/devices/platform/hp-wmi"),
    );
    if !caps.fan_modes.is_empty() {
        host.fan_modes = caps.fan_modes;
    }
    host.keyboard = caps.keyboard_lighting;
    if let Some(mux) = caps.gpu_mux {
        host.mux_index = mux.current_index;
        host.mux = mux
            .modes
            .into_iter()
            .map(|mode| MuxChoice { label: mode.label, index: mode.index })
            .collect();
    }
}

fn config_path() -> PathBuf {
    if let Some(dir) = std::env::var_os("XDG_CONFIG_HOME").filter(|dir| !dir.is_empty()) {
        return PathBuf::from(dir).join("victus-hub/victus-hub.conf");
    }
    std::env::var_os("HOME")
        .filter(|home| !home.is_empty())
        .map(|home| PathBuf::from(home).join(".config/victus-hub/victus-hub.conf"))
        .unwrap_or_default()
}
