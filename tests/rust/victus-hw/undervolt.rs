use super::*;
use victus_core::offline_scratch;

#[test]
fn rejected_undervolt_does_not_open_a_device_file() {
    let root = offline_scratch("msr");
    let path = root.join("msr");
    let error = apply_undervolt(&path, true, -300, 0).unwrap_err();
    assert!(error.to_string().contains("-250"));
    assert!(!path.exists());
    let error = apply_undervolt(&path, false, -50, -50).unwrap_err();
    assert!(error.to_string().contains("unavailable"));
    assert!(!path.exists());
    let missing = apply_undervolt(&path, true, -50, -50).unwrap_err();
    assert!(missing.to_string().contains("msr kernel module"), "{missing}");
    assert!(!path.exists());
    std::fs::write(&path, []).unwrap();
    let timed_out = apply_undervolt(&path, true, -50, -50).unwrap_err();
    assert!(timed_out.to_string().contains("timed out"), "{timed_out}");
    let _ = std::fs::remove_dir_all(root);
}
