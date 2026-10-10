use super::*;
use victus_core::offline_scratch;

#[test]
fn ryzenadj_validation_does_not_execute_a_program() {
    let missing = offline_scratch("ryzen").join("missing-ryzenadj");
    let error = apply_ryzenadj(&missing, 500, 25_000, 25_000, 95).unwrap_err();
    assert!(error.to_string().contains("stapm-limit"));
    let _ = std::fs::remove_dir_all(missing.parent().unwrap());
}

#[test]
fn command_output_child() {
    use std::io::Write;
    match std::env::var("VICTUS_TEST_CHILD").ok().as_deref() {
        Some("output") => {
            std::io::stdout().write_all(b"stdout-child\n").unwrap();
            std::io::stderr().write_all(b"stderr-child\n").unwrap();
            let chunk = [b'x'; 8192];
            for _ in 0..256 {
                std::io::stdout().write_all(&chunk).unwrap();
                std::io::stderr().write_all(&chunk).unwrap();
            }
        }
        Some("wait") => std::thread::sleep(Duration::from_secs(5)),
        _ => {},
    }
}

#[test]
fn command_pipes_are_bounded_drained_and_timed_out_without_scripts() {
    let executable = std::env::current_exe().unwrap();
    let mut command = Command::new(&executable);
    command.args(["--exact", "tests::command_output_child", "--nocapture"]).env("VICTUS_TEST_CHILD", "output");
    let output = run_prepared_command(&mut command, Duration::from_secs(5)).unwrap();
    assert_eq!(output.status, 0);
    assert_eq!(output.stdout.len(), 1024 * 1024);
    assert_eq!(output.stderr.len(), 1024 * 1024);
    assert!(output.stdout.contains("stdout-child"));
    assert!(output.stderr.contains("stderr-child"));
    let mut command = Command::new(executable);
    command.args(["--exact", "tests::command_output_child", "--nocapture"]).env("VICTUS_TEST_CHILD", "wait");
    let started = Instant::now();
    let error = run_prepared_command(&mut command, Duration::from_millis(50)).unwrap_err();
    assert!(error.to_string().contains("timed out"));
    assert!(started.elapsed() < Duration::from_secs(2));
}

fn rapl_package(root: &std::path::Path, index: i32) {
    let package = root.join(format!("intel-rapl:{index}"));
    std::fs::create_dir_all(&package).unwrap();
    for (name, value) in [
        ("name", format!("package-{index}")),
        ("enabled", "1".to_owned()),
        ("constraint_0_name", "short_term".to_owned()),
        ("constraint_1_name", "long_term".to_owned()),
        ("constraint_0_power_limit_uw", "65000000".to_owned()),
        ("constraint_1_power_limit_uw", "45000000".to_owned()),
        ("constraint_0_max_power_uw", "100000000".to_owned()),
        ("constraint_1_time_window_us", "28000000".to_owned()),
    ] {
        std::fs::write(package.join(name), value).unwrap();
    }
}

#[test]
fn intel_package_limits_use_names_and_reject_bad_input() {
    let root = offline_scratch("rapl");
    rapl_package(&root, 0);
    rapl_package(&root, 1);
    let subdomain = root.join("intel-rapl:0:0");
    std::fs::create_dir_all(&subdomain).unwrap();
    std::fs::write(subdomain.join("name"), "package-core").unwrap();
    std::fs::write(subdomain.join("constraint_0_name"), "short_term").unwrap();
    std::fs::write(subdomain.join("constraint_0_power_limit_uw"), "1000").unwrap();
    std::fs::write(root.join("intel-rapl:0/enabled"), "0").unwrap();
    intel_power_limits(&root, 35_000, 55_000, true).unwrap();
    for index in [0, 1] {
        let package = root.join(format!("intel-rapl:{index}"));
        assert_eq!(std::fs::read_to_string(package.join("constraint_1_power_limit_uw")).unwrap().trim(), "35000000");
        assert_eq!(std::fs::read_to_string(package.join("constraint_0_power_limit_uw")).unwrap().trim(), "55000000");
        assert_eq!(std::fs::read_to_string(package.join("constraint_1_time_window_us")).unwrap().trim(), "28000000");
        assert_eq!(std::fs::read_to_string(package.join("enabled")).unwrap().trim(), "1");
    }
    assert_eq!(std::fs::read_to_string(subdomain.join("constraint_0_power_limit_uw")).unwrap().trim(), "1000");

    rapl_package(&root, 0);
    rapl_package(&root, 1);
    std::fs::write(root.join("intel-rapl:1/constraint_0_max_power_uw"), "40000000").unwrap();
    let error = intel_power_limits(&root, 35_000, 55_000, true).unwrap_err();
    assert!(error.to_string().contains("hardware range"), "{error}");
    assert_eq!(std::fs::read_to_string(root.join("intel-rapl:0/constraint_1_power_limit_uw")).unwrap().trim(), "45000000");

    for (pl1, pl2) in [(14_000, 25_000), (45_000, 35_000), (25_000, 121_000)] {
        assert!(intel_power_limits(&root, pl1, pl2, true).is_err(), "{pl1} {pl2}");
    }
    let missing = intel_power_limits(&root.join("missing"), 25_000, 35_000, true).unwrap_err();
    assert!(missing.to_string().contains("unavailable"), "{missing}");
    let other = intel_power_limits(&root, 25_000, 35_000, false).unwrap_err();
    assert!(other.to_string().contains("processor"), "{other}");

    rapl_package(&root, 0);
    for name in ["constraint_0_power_limit_uw", "constraint_1_power_limit_uw"] {
        let path = root.join("intel-rapl:0").join(name);
        let mut permissions = std::fs::metadata(&path).unwrap().permissions();
        permissions.set_readonly(true);
        std::fs::set_permissions(&path, permissions).unwrap();
    }
    let error = intel_power_limits(&root, 25_000, 35_000, true).unwrap_err();
    assert!(error.to_string().contains("Some limits may have changed."), "{error}");
    let _ = std::fs::remove_dir_all(root);
}
