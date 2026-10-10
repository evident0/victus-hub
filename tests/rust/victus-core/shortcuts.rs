use super::*;

#[test]
fn shortcut_requires_a_command_modifier_or_special_key() {
    assert!(validate_shortcut(&[KEY_LEFTCTRL], 24).is_ok());
    assert!(validate_shortcut(&[], 149).is_ok());
    assert!(validate_shortcut(&[KEY_LEFTSHIFT], 24).is_err());
    assert!(validate_shortcut(&[1], 24).is_err());
    assert!(validate_shortcut(&[], 30).is_err());
    assert!(validate_shortcut(&[], 149).is_ok());
    assert!(validate_shortcut(&[KEY_LEFTCTRL, KEY_LEFTSHIFT], 30).is_ok());
    let (mods, key) = validate_shortcut(&[KEY_LEFTALT, KEY_LEFTCTRL], 16).unwrap();
    assert_eq!(mods, vec![KEY_LEFTCTRL, KEY_LEFTALT]);
    assert_eq!(key, 16);
}

#[test]
fn labels_and_hardware_chords() {
    assert_eq!(keybind_label(&[KEY_LEFTCTRL, KEY_LEFTSHIFT], 24), "Ctrl + Shift + O");
    assert_eq!(keybind_label(&[], 149), "Omen Key");
    assert_eq!(keybind_label(&[], 0), "Not set");
    assert_eq!(
        hardware_action(&[KEY_LEFTSHIFT, KEY_LEFTCTRL], 103),
        Some(HardwareAction::Brightness(1))
    );
    assert_eq!(hardware_action(&[KEY_LEFTCTRL], 103), None);
}
