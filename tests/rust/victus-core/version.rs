use super::*;

#[test]
fn newer_tag_compares_numerically() {
    assert!(release_is_newer("v1.2.0", "1.0.3").unwrap());
    assert!(!release_is_newer("1.0.3", "v1.0.3").unwrap());
    assert!(release_is_newer("1.0.10", "1.0.9").unwrap());
    assert!(release_is_newer("1.10.0", "1.9.9").unwrap());
    assert!(release_is_newer("v1.0.10", "1.0.9").unwrap());
    assert!(!release_is_newer("1.0.1", "1.0.1").unwrap());
    assert!(!release_is_newer("1.0.0", "1.0.1").unwrap());
}

#[test]
fn rejects_non_release_tags() {
    assert!(release_is_newer("main", "1.0.3").is_err());
    assert!(release_is_newer("1.0", "1.0.3").is_err());
}
