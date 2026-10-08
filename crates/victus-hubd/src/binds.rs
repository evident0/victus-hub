use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use victus_core::{validate_shortcut, HubError, HubResult};

/// Per-user program shortcuts. Files are mode 0600 under the path the caller passes.
#[derive(Debug, Clone)]
pub struct ProgramShortcuts {
    path: PathBuf,
    bindings: BTreeMap<u32, (Vec<i32>, i32)>,
}

impl ProgramShortcuts {
    pub fn load(path: &Path) -> Self {
        let mut bindings = BTreeMap::new();
        if let Ok(text) = fs::read_to_string(path) {
            if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) {
                for (uid, binding) in map {
                    let Ok(user) = uid.parse::<u32>() else { continue };
                    if user == 0 || user.to_string() != uid {
                        continue;
                    }
                    let Some(object) = binding.as_object() else { continue };
                    let mods = object.get("mods").and_then(Value::as_array).map(|items| {
                        items.iter().filter_map(Value::as_i64).map(|value| value as i32).collect::<Vec<_>>()
                    });
                    let key = object.get("key").and_then(Value::as_i64).map(|value| value as i32);
                    if let (Some(mods), Some(key)) = (mods, key) {
                        if let Ok(binding) = validate_shortcut(&mods, key) {
                            bindings.insert(user, binding);
                        }
                    }
                }
            }
        }
        Self { path: path.to_path_buf(), bindings }
    }

    pub fn get(&self, uid: u32) -> Option<&(Vec<i32>, i32)> {
        self.bindings.get(&uid)
    }

    pub fn matches_any(&self, mods: &[i32], key: i32) -> bool {
        self.bindings.values().any(|(bound_mods, bound_key)| *bound_key == key && bound_mods == mods)
    }

    pub fn set(&mut self, uid: u32, mods: &[i32], key: i32) -> HubResult<()> {
        if uid == 0 {
            return Err(HubError::new("a desktop user is required"));
        }
        let binding = validate_shortcut(mods, key)?;
        if self.bindings.get(&uid) == Some(&binding) {
            return Ok(());
        }
        self.bindings.insert(uid, binding);
        self.store()
    }

    fn store(&self) -> HubResult<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(|error| HubError::new(format!("could not save program shortcut: {error}")))?;
        }
        let mut body = serde_json::Map::new();
        for (uid, (mods, key)) in &self.bindings {
            body.insert(uid.to_string(), json!({"mods": mods, "key": key}));
        }
        let text = serde_json::to_string(&Value::Object(body)).map_err(|error| HubError::new(error.to_string()))?;
        let tmp = self.path.with_extension("json.tmp");
        let mut file = OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp).map_err(|error| {
            HubError::new(format!("could not save program shortcut: {error}"))
        })?;
        file.write_all(text.as_bytes()).map_err(|error| HubError::new(format!("could not save program shortcut: {error}")))?;
        file.write_all(b"\n").ok();
        fs::rename(&tmp, &self.path).map_err(|error| HubError::new(format!("could not save program shortcut: {error}")))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use victus_core::{offline_scratch, KEY_LEFTCTRL};

    #[test]
    fn shortcuts_stay_in_the_scratch_file_and_skip_root() {
        let dir = offline_scratch("shortcuts");
        let path = dir.join("program-shortcuts.json");
        assert!(path.starts_with(std::env::temp_dir()));
        let mut store = ProgramShortcuts::load(&path);
        assert!(store.set(0, &[KEY_LEFTCTRL], 24).is_err());
        store.set(1000, &[KEY_LEFTCTRL], 24).unwrap();
        let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        let loaded = ProgramShortcuts::load(&path);
        assert!(loaded.matches_any(&[KEY_LEFTCTRL], 24));
        assert!(loaded.get(0).is_none());
        let _ = fs::remove_dir_all(dir);
    }
}
