use std::fs;
use std::path::{Path, PathBuf};

/// Keyboard event nodes under the directories the caller passes.
/// This function only reads directory entries. It does not open devices.
pub fn discover_keyboards(sys_input: &Path, by_path: &Path, dev_input: &Path) -> Vec<PathBuf> {
    let mut devices = Vec::new();
    if let Ok(entries) = fs::read_dir(by_path) {
        let mut found = Vec::new();
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.contains("i8042") && name.ends_with("-event-kbd") {
                found.push(entry.path());
            }
        }
        found.sort();
        devices.extend(found);
    }
    if let Ok(entries) = fs::read_dir(sys_input) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if !name.starts_with("input") {
                continue;
            }
            let Ok(device_name) = fs::read_to_string(entry.path().join("name")) else { continue };
            if device_name.trim() != "HP WMI hotkeys" {
                continue;
            }
            if let Ok(children) = fs::read_dir(entry.path()) {
                for child in children.flatten() {
                    let child_name = child.file_name();
                    let child_name = child_name.to_string_lossy();
                    if !child_name.starts_with("event") {
                        continue;
                    }
                    let dev = dev_input.join(child_name.as_ref());
                    if dev.exists() {
                        devices.push(dev);
                    }
                }
            }
        }
    }
    devices
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hubd/keys.rs"]
mod tests;
