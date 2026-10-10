use super::*;

#[test]
fn maps_tuned_names_onto_the_three_ui_profiles() {
    assert_eq!(profile_index_for_name("powersave"), Some(0));
    assert_eq!(profile_index_for_name("desktop"), Some(1));
    assert_eq!(profile_index_for_name("throughput-performance"), Some(2));
    assert_eq!(profile_index_for_name("power-saver"), Some(0));
    assert_eq!(profile_index_for_name("missing"), None);
    let available = ["balanced", "powersave"];
    assert_eq!(tuned_profile_for(1, &available), Some("balanced"));
    assert_eq!(tuned_profile_for(2, &available), None);
}

#[test]
fn parses_tuned_adm_text() {
    let list = parse_tuned_list("- balanced\n- powersave\nCurrent active profile: balanced\n");
    assert_eq!(list, vec!["balanced".to_owned(), "powersave".to_owned()]);
    assert_eq!(
        parse_tuned_active("Current active profile: balanced\n").as_deref(),
        Some("balanced")
    );
}
