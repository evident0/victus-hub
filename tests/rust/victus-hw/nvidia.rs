use std::os::unix::fs::PermissionsExt;

use super::*;
use victus_core::offline_scratch;

#[test]
fn smi_backoff_is_per_column_and_parsing_ignores_na() {
    let mut backoff = SmiBackoff::default();
    let due = smi_due(&backoff, 0.0, true, true, true);
    assert_eq!(due, vec!["temperature.gpu", "utilization.gpu", "power.draw"]);
    let parsed = parse_smi_csv("61, [N/A], 14.5", &due);
    assert_eq!(parsed[0], ("temperature", Some(61.0)));
    assert_eq!(parsed[1].1, None);
    assert_eq!(parsed[2], ("power", Some(14.5)));
    note_smi_failures(&mut backoff, 5.0, &["utilization"]);
    let later = smi_due(&backoff, 10.0, true, true, true);
    assert_eq!(later, vec!["temperature.gpu", "power.draw"]);
    assert!(!smi_due(&backoff, 10.0, false, true, false).contains(&"utilization.gpu"));

    let root = offline_scratch("nvidia");
    let hwmon = root.join("hwmon1");
    std::fs::create_dir_all(&hwmon).unwrap();
    std::fs::write(hwmon.join("name"), "nvidia\n").unwrap();
    std::fs::write(hwmon.join("temp1_input"), "47000\n").unwrap();
    assert_eq!(nvidia_hwmon_temp_c(&root), Some(47.0));
    let status = root.join("runtime_status");
    std::fs::write(&status, "suspended\n").unwrap();
    assert!(runtime_suspended(Some(&status)));
    let program = root.join("nvidia-smi");
    std::fs::write(&program, "#!/bin/sh\necho '61, 12, 14.5'\n").unwrap();
    std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
    let line = read_smi_line(&program, &["temperature.gpu", "utilization.gpu", "power.draw"]).unwrap();
    assert_eq!(line, "61, 12, 14.5");
    assert!(read_smi_line(&root.join("missing-smi"), &["temperature.gpu"]).is_none());
    let _ = std::fs::remove_dir_all(root);
}
