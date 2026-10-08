use super::*;
use std::os::unix::fs::PermissionsExt;
use victus_core::{offline_scratch, KEY_LEFTCTRL};

#[test]
fn failed_save_keeps_the_old_binding_and_can_be_retried() {
    let dir = offline_scratch("shortcut-save-failure");
    let path = dir.join("program-shortcuts.json");
    let mut store = ProgramShortcuts::load(&path);
    store.set(1000, &[], 149).unwrap();
    fs::remove_file(&path).unwrap();
    fs::create_dir(&path).unwrap();
    assert!(store.set(1000, &[KEY_LEFTCTRL], 24).is_err());
    assert_eq!(store.get(1000), Some(&(Vec::new(), 149)));
    fs::remove_dir(&path).unwrap();
    store.set(1000, &[KEY_LEFTCTRL], 24).unwrap();
    assert!(ProgramShortcuts::load(&path).matches_any(&[KEY_LEFTCTRL], 24));
    let _ = fs::remove_dir_all(dir);
}

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
