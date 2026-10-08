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
mod tests {
    use super::*;
    use victus_core::offline_scratch;

    #[test]
    fn discovery_reads_only_the_scratch_tree() {
        let root = offline_scratch("keys");
        assert!(root.starts_with(std::env::temp_dir()));
        let by_path = root.join("by-path");
        fs::create_dir_all(&by_path).unwrap();
        fs::write(by_path.join("platform-i8042-serio-0-event-kbd"), b"").unwrap();
        let sys = root.join("class/input/input0");
        fs::create_dir_all(&sys).unwrap();
        fs::write(sys.join("name"), "HP WMI hotkeys\n").unwrap();
        fs::create_dir_all(sys.join("event5")).unwrap();
        let dev = root.join("dev");
        fs::create_dir_all(&dev).unwrap();
        fs::write(dev.join("event5"), b"").unwrap();
        let found = discover_keyboards(&root.join("class/input"), &by_path, &dev);
        assert!(found.iter().all(|path| path.starts_with(&root)));
        assert!(found.iter().any(|path| path.ends_with("platform-i8042-serio-0-event-kbd")));
        assert!(found.iter().any(|path| path.ends_with("event5")));
        let _ = fs::remove_dir_all(root);
    }
}
