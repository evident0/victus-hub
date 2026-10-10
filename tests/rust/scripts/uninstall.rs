use std::fs;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn scratch(label: &str) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "victus-uninstall-{label}-{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&path).unwrap();
    path
}

fn rewrite(text: &str, root: &Path, prefixes: &[&str]) -> String {
    let root = root.to_string_lossy();
    let mut out = String::new();
    let mut index = 0;
    while index < text.len() {
        if let Some(prefix) = prefixes.iter().copied().find(|prefix| text[index..].starts_with(prefix)) {
            out.push_str(&root);
            out.push_str(prefix);
            index += prefix.len();
        } else {
            let ch = text[index..].chars().next().unwrap();
            out.push(ch);
            index += ch.len_utf8();
        }
    }
    out
}

fn write_exe(path: &Path, body: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o755)
        .open(path)
        .unwrap();
    writeln!(file, "{body}").unwrap();
}

struct Uninstall {
    root: PathBuf,
    env_path: String,
    home: PathBuf,
    log: PathBuf,
    rmmod_exit: &'static str,
}

impl Uninstall {
    fn new(label: &str) -> Self {
        let root = scratch(label);
        let bin = root.join("bin");
        fs::create_dir_all(&bin).unwrap();
        let home = root.join("home");
        fs::create_dir_all(&home).unwrap();
        let log = root.join("log");
        let path = format!("{}:{}", bin.display(), std::env::var("PATH").unwrap_or_default());
        let harness = Self { root, env_path: path, home, log, rmmod_exit: "0" };
        harness.command("id", "printf '0\\n'");
        harness.command("sudo", "exec \"$@\"");
        harness
    }

    fn command(&self, name: &str, body: &str) {
        write_exe(&self.root.join("bin").join(name), &format!("#!/bin/bash\n{body}"));
    }

    fn script(&self, relative: &str, prefixes: &[&str]) -> PathBuf {
        let text = fs::read_to_string(repo_root().join(relative)).unwrap();
        let script = self.root.join(relative);
        if let Some(parent) = script.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(&script, rewrite(&text, &self.root, prefixes)).unwrap();
        script
    }

    fn run(&self, script: &Path, args: &[&str]) -> Output {
        let mut command = Command::new("/bin/bash");
        command.arg(script).args(args);
        command.env("PATH", &self.env_path);
        command.env("HOME", &self.home);
        command.env("SUDO_USER", "");
        command.env("TEST_LOG", &self.log);
        command.env("TEST_RMMOD_EXIT", self.rmmod_exit);
        command.output().unwrap()
    }
}

#[test]
fn rgb_unloads_even_after_dkms_deleted_module_file() {
    let mut harness = Uninstall::new("rgb");
    let script = harness.script("kernel/hp-kbd-rgb/scripts/uninstall", &["/sys/", "/etc/", "/lib/modules"]);
    fs::create_dir_all(harness.root.join("sys/module/hp_kbd_rgb")).unwrap();
    harness.command("modprobe", "printf \"Module not found\\n\" >&2; exit 1");
    harness.command(
        "rmmod",
        "printf \"rmmod %s\\n\" \"$*\" >> \"$TEST_LOG\"; exit \"${TEST_RMMOD_EXIT:-0}\"",
    );
    harness.command("depmod", "exit 0");
    let result = harness.run(&script, &[]);
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    assert!(result.stderr.is_empty());
    assert_eq!(fs::read_to_string(&harness.log).unwrap(), "rmmod hp_kbd_rgb\n");
    harness.rmmod_exit = "1";
    let result = harness.run(&script, &[]);
    assert!(!result.status.success());
    assert!(!String::from_utf8_lossy(&result.stdout).contains("Uninstalled hp-kbd-rgb"));
    let _ = fs::remove_dir_all(&harness.root);
}

#[test]
fn all_icon_sizes_removed_before_cache_refresh() {
    let harness = Uninstall::new("icons");
    let script = harness.script(
        "scripts/uninstall",
        &["/usr/local", "/usr/lib", "/usr/share", "/etc/", "/opt/", "/var/", "/run/"],
    );
    harness.script("scripts/stop-gui", &["/run/user"]);
    let icons = harness.root.join("usr/share/icons/hicolor");
    for size in [16, 22, 24, 32, 48, 64, 128, 256, 512, 1024] {
        let icon = icons.join(format!("{size}x{size}/apps/victus-hub.png"));
        fs::create_dir_all(icon.parent().unwrap()).unwrap();
        fs::write(&icon, "").unwrap();
    }
    let other = icons.join("16x16/apps/another-app.png");
    fs::write(&other, "").unwrap();
    harness.command("systemctl", "exit 1");
    harness.command(
        "python3",
        "if [[ \"$1\" == */scripts/stop-gui ]]; then exec /usr/bin/python3 \"$@\"; fi; exit 1",
    );
    let icons_text = icons.display().to_string();
    harness.command(
        "gtk-update-icon-cache",
        &format!(
            "for icon in \"{icons_text}\"/*/apps/victus-hub.png; do\n  [ ! -e \"$icon\" ] || {{ printf \"stale icon\\n\" >> \"$TEST_LOG\"; exit 1; }}\ndone\nprintf \"cache refreshed\\n\" >> \"$TEST_LOG\""
        ),
    );
    let result = harness.run(&script, &["--app-only"]);
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    for size in [16, 22, 24, 32, 48, 64, 128, 256, 512, 1024] {
        assert!(!icons.join(format!("{size}x{size}/apps/victus-hub.png")).exists());
    }
    assert!(other.exists());
    assert_eq!(String::from_utf8_lossy(&result.stdout).matches("Removing icon ").count(), 10);
    assert_eq!(fs::read_to_string(&harness.log).unwrap(), "cache refreshed\n");
    let result = harness.run(&script, &["--app-only"]);
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    assert_eq!(fs::read_to_string(&harness.log).unwrap(), "cache refreshed\n");
    let _ = fs::remove_dir_all(&harness.root);
}

#[test]
fn legacy_modules_and_overrides_refresh_each_affected_kernel() {
    let harness = Uninstall::new("legacy");
    let script = harness.script(
        "scripts/uninstall",
        &[
            "/usr/local",
            "/usr/lib",
            "/usr/share",
            "/usr/src",
            "/etc/",
            "/opt/",
            "/var/",
            "/run/",
            "/sys/",
            "/lib/modules",
        ],
    );
    harness.script("kernel/dkms-post-remove", &["/lib/modules", "/etc/"]);
    for relative in [
        "kernel/hp-wmi/scripts/uninstall",
        "kernel/hp-kbd-rgb/scripts/uninstall",
        "scripts/ryzenadj-uninstall",
    ] {
        write_exe(&harness.root.join(relative), "#!/bin/bash\nexit 0");
    }
    harness.command("systemctl", "exit 1");
    harness.command("python3", "exit 0");
    harness.command("depmod", "printf \"depmod %s\\n\" \"$*\" >> \"$TEST_LOG\"");
    harness.command("update-initramfs", "printf \"initramfs %s\\n\" \"$*\" >> \"$TEST_LOG\"");
    for release in ["old-kernel", "other-kernel"] {
        let extra = harness.root.join("lib/modules").join(release).join("extra");
        fs::create_dir_all(&extra).unwrap();
        for module in ["hp-wmi", "hp-kbd-rgb"] {
            fs::write(extra.join(format!("{module}.ko")), "").unwrap();
        }
        let conf = harness.root.join("etc/depmod.d").join(format!("victus-hub-hp-wmi-{release}.conf"));
        fs::create_dir_all(conf.parent().unwrap()).unwrap();
        fs::write(&conf, "").unwrap();
    }
    let result = harness.run(&script, &[]);
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    let log = fs::read_to_string(&harness.log).unwrap();
    for release in ["old-kernel", "other-kernel"] {
        assert_eq!(log.matches(&format!("initramfs -u -k {release}\n")).count(), 1, "{log}");
    }
    for release in ["old-kernel", "other-kernel"] {
        let extra = harness.root.join("lib/modules").join(release).join("extra");
        assert!(fs::read_dir(&extra).unwrap().next().is_none());
    }
    assert!(fs::read_dir(harness.root.join("etc/depmod.d")).unwrap().next().is_none());
    let _ = fs::remove_dir_all(&harness.root);
}
