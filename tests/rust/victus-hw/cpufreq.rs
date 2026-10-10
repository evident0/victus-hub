use super::*;
use victus_core::offline_scratch;

#[test]
fn raises_the_ceiling_before_the_floor() {
    let root = offline_scratch("cpufreq");
    let policy = root.join("policy0");
    std::fs::create_dir_all(&policy).unwrap();
    std::fs::write(policy.join("cpuinfo_min_freq"), "400000\n").unwrap();
    std::fs::write(policy.join("cpuinfo_max_freq"), "5000000\n").unwrap();
    std::fs::write(policy.join("scaling_min_freq"), "400000\n").unwrap();
    std::fs::write(policy.join("scaling_max_freq"), "800000\n").unwrap();
    apply_limits(&root, 1_200_000, 3_000_000).unwrap();
    assert_eq!(std::fs::read_to_string(policy.join("scaling_min_freq")).unwrap().trim(), "1200000");
    assert_eq!(std::fs::read_to_string(policy.join("scaling_max_freq")).unwrap().trim(), "3000000");
    let _ = std::fs::remove_dir_all(root);
}

fn policy_tree(root: &std::path::Path) {
    for name in ["policy0", "policy1"] {
        let path = root.join(name);
        std::fs::create_dir_all(&path).unwrap();
        for (field, value) in [
            ("cpuinfo_min_freq", "1100980"),
            ("cpuinfo_max_freq", "5137904"),
            ("scaling_min_freq", "1100980"),
            ("scaling_max_freq", "4600000"),
        ] {
            std::fs::write(path.join(field), value).unwrap();
        }
        let _ = std::fs::remove_file(path.join("amd_pstate_max_freq"));
    }
}

#[test]
fn policies_preserve_kilohertz_and_reject_bad_limits() {
    let root = offline_scratch("cpufreq-policies");
    policy_tree(&root);
    let policies = read_policies(&root).unwrap();
    assert_eq!(policies.len(), 2);
    assert_eq!(policies[0].minimum, 1_100_980);
    assert_eq!(policies[0].hardware_max, 5_137_904);
    apply_limits(&root, 1_200_000, 4_200_000).unwrap();
    for policy in read_policies(&root).unwrap() {
        assert_eq!((policy.minimum, policy.maximum), (1_200_000, 4_200_000));
    }

    let missing = read_policies(&root.join("missing")).unwrap_err();
    assert!(missing.to_string().contains("unavailable"));
    std::fs::remove_file(root.join("policy1/scaling_max_freq")).unwrap();
    let error = apply_limits(&root, 1_200_000, 4_200_000).unwrap_err();
    assert!(error.to_string().contains("policy1"), "{error}");
    assert_eq!(std::fs::read_to_string(root.join("policy0/scaling_max_freq")).unwrap().trim(), "4200000");
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn amd_ceiling_validation_and_write_order() {
    let root = offline_scratch("cpufreq-amd");
    policy_tree(&root);
    for name in ["policy0", "policy1"] {
        let path = root.join(name);
        std::fs::write(path.join("amd_pstate_max_freq"), "5137904").unwrap();
        std::fs::write(path.join("cpuinfo_max_freq"), "3801000").unwrap();
        std::fs::write(path.join("scaling_max_freq"), "3801000").unwrap();
    }
    assert!(read_policies(&root).unwrap().iter().all(|policy| policy.hardware_max == 5_137_904));
    apply_limits(&root, 1_100_980, 5_137_904).unwrap();
    assert!(read_policies(&root).unwrap().iter().all(|policy| policy.maximum == 5_137_904));

    policy_tree(&root);
    std::fs::write(root.join("policy0/amd_pstate_max_freq"), "invalid").unwrap();
    assert_eq!(read_policies(&root).unwrap()[0].hardware_max, 5_137_904);

    std::fs::write(root.join("policy1/cpuinfo_max_freq"), "4700000").unwrap();
    let error = apply_limits(&root, 1_200_000, 5_000_000).unwrap_err();
    assert!(error.to_string().contains("hardware range"), "{error}");
    assert_eq!(std::fs::read_to_string(root.join("policy0/scaling_min_freq")).unwrap().trim(), "1100980");
    assert_eq!(std::fs::read_to_string(root.join("policy0/scaling_max_freq")).unwrap().trim(), "4600000");

    for (minimum, maximum) in [(0, 4_600_000), (4_700_000, 4_600_000), (1_100_000, 4_600_000)] {
        assert!(apply_limits(&root, minimum, maximum).is_err(), "{minimum} {maximum}");
    }
    assert_eq!(std::fs::read_to_string(root.join("policy0/scaling_max_freq")).unwrap().trim(), "4600000");

    policy_tree(&root);
    apply_limits(&root, 4_800_000, 5_000_000).unwrap();
    assert_eq!(std::fs::read_to_string(root.join("policy0/scaling_min_freq")).unwrap().trim(), "4800000");
    assert_eq!(std::fs::read_to_string(root.join("policy0/scaling_max_freq")).unwrap().trim(), "5000000");
    apply_limits(&root, 1_200_000, 2_000_000).unwrap();
    assert_eq!(std::fs::read_to_string(root.join("policy1/scaling_min_freq")).unwrap().trim(), "1200000");
    assert_eq!(std::fs::read_to_string(root.join("policy1/scaling_max_freq")).unwrap().trim(), "2000000");

    policy_tree(&root);
    for name in ["policy0", "policy1"] {
        for field in ["scaling_min_freq", "scaling_max_freq"] {
            let path = root.join(name).join(field);
            let mut permissions = std::fs::metadata(&path).unwrap().permissions();
            permissions.set_readonly(true);
            std::fs::set_permissions(&path, permissions).unwrap();
        }
    }
    let error = apply_limits(&root, 1_200_000, 4_200_000).unwrap_err();
    assert!(error.to_string().contains("Some CPU limits may have changed."), "{error}");
    let _ = std::fs::remove_dir_all(root);
}
