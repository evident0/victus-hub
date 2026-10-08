use std::fs;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::Command;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn bash_source_debug(args: &[&str]) -> std::process::Output {
    let scripts = repo_root().join("scripts");
    let mut command = Command::new("/bin/bash");
    command.args([
        "-c",
        r#"scripts=$1; shift; source "$scripts/debug-level.sh"; printf '%s' "$VICTUS_HUB_DEBUG_LEVEL""#,
        "test",
    ]);
    command.arg(scripts);
    command.args(args);
    command.output().expect("bash")
}

#[test]
fn debug_level_script_accepts_only_one_level() {
    for (args, expected) in [(Vec::<&str>::new(), "0"), (vec!["0"], "0"), (vec!["1"], "1"), (vec!["2"], "2"), (vec!["3"], "3")] {
        let output = bash_source_debug(&args);
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        assert_eq!(String::from_utf8_lossy(&output.stdout), expected);
    }
    for args in [vec!["4"], vec!["-1"], vec!["abc"], vec!["1", "2"]] {
        let output = bash_source_debug(&args);
        assert_eq!(output.status.code(), Some(2), "{}", String::from_utf8_lossy(&output.stderr));
    }
}

#[test]
fn ui_test_rejects_arguments_without_launching() {
    let script = repo_root().join("scripts/ui-test");
    let output = Command::new("/bin/bash").arg(&script).arg("1").output().unwrap();
    assert_eq!(output.status.code(), Some(2));
    let output = Command::new("/bin/bash").arg(&script).arg("0").arg("1").output().unwrap();
    assert_eq!(output.status.code(), Some(2));
}

#[test]
fn install_pins_quiet_debug_level_and_the_service_does_not() {
    let root = repo_root();
    let install = fs::read_to_string(root.join("scripts/install")).unwrap();
    assert_eq!(install.matches("Exec=env VICTUS_HUB_DEBUG_LEVEL=0 $VICTUS_HUB_BIN").count(), 2);
    let service = fs::read_to_string(root.join("data/victus-hubd.service")).unwrap();
    assert!(!service.contains("VICTUS_HUB_DEBUG_LEVEL"));
}

#[test]
fn ryzenadj_install_stops_before_clone_without_pkg_config() {
    let scratch = super::offline_scratch("ryzenadj-script");
    let bin = scratch.join("bin");
    let home = scratch.join("home");
    fs::create_dir_all(&bin).unwrap();
    fs::create_dir_all(&home).unwrap();
    let grep = bin.join("grep");
    let mut file = fs::OpenOptions::new().write(true).create(true).mode(0o755).open(&grep).unwrap();
    use std::io::Write;
    writeln!(file, "#!/bin/bash\nexec /usr/bin/grep \"$@\"").unwrap();
    drop(file);
    let script = repo_root().join("scripts/ryzenadj-install");
    let output = Command::new("/bin/bash")
        .arg(&script)
        .env("PATH", &bin)
        .env("HOME", &home)
        .output()
        .unwrap();
    assert!(!output.status.success(), "{}", String::from_utf8_lossy(&output.stdout));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("pkg-config"), "{stderr}");
    assert!(!home.join("RyzenAdj").exists());
    assert!(!stderr.contains("Cloning") && !String::from_utf8_lossy(&output.stdout).contains("Cloning"));
    let _ = fs::remove_dir_all(scratch);
}
