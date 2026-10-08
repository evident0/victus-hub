//! egui control panel. Offline preview never opens the installed socket.

use std::path::PathBuf;
use std::sync::mpsc;

fn main() {
    let offline = std::env::var("VICTUS_HUB_OFFLINE").ok().as_deref() == Some("1");
    let (activate_tx, activate_rx) = mpsc::channel();
    let model = if offline {
        victus_hub::Model::offline(zone_count(true))
    } else {
        let socket = std::env::var("VICTUS_HUB_SOCKET")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(victus_core::DEFAULT_SOCKET));
        let mut model = victus_hub::Model::live(socket, zone_count(false));
        victus_hub::ui::hydrate_live(&mut model);
        model
    };
    if !offline && std::env::var("VICTUS_HUB_NO_DBUS").ok().as_deref() != Some("1") {
        std::thread::spawn(move || {
            if let Err(error) = bus::own_session_name(activate_tx) {
                eprintln!("session bus: {error}");
            }
        });
    }
    if let Err(error) = victus_hub::ui::start(model, activate_rx) {
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

mod bus;
