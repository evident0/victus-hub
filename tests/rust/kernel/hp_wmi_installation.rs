use std::fs;
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|dir| dir.join(name)).find(|candidate| candidate.is_file())
}

fn tools_ready() -> bool {
    ["cc", "ld", "objcopy", "depmod", "modinfo", "bash", "openssl"].iter().all(|tool| which(tool).is_some())
}

fn scratch() -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "victus-kmod-install-{}-{}",
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
    write!(file, "{body}").unwrap();
}

struct Install {
    root: PathBuf,
    release: String,
    scripts: PathBuf,
    stock: PathBuf,
    custom: PathBuf,
    stock_bytes: Vec<u8>,
    sign_file: PathBuf,
    env: Vec<(String, String)>,
}

impl Install {
    fn new() -> Option<Self> {
        if !tools_ready() {
            eprintln!("requires kmod and binutils; skipped hp-wmi installation harness");
            return None;
        }
        let root = scratch();
        let release = String::from_utf8(Command::new("uname").arg("-r").output().unwrap().stdout).unwrap();
        let release = release.trim().to_owned();
        let module_dir = root.join("kernel/hp-wmi");
        let scripts = module_dir.join("scripts");
        fs::create_dir_all(&scripts).unwrap();
        let bin = root.join("bin");
        fs::create_dir_all(&bin).unwrap();
        let modules = root.join("lib/modules").join(&release);
        fs::create_dir_all(modules.join("build")).unwrap();
        fs::write(modules.join("modules.order"), "").unwrap();
        fs::write(modules.join("modules.builtin"), "").unwrap();
        let stock = modules.join("kernel/drivers/platform/x86/hp/hp-wmi.ko");
        let custom = module_dir.join("hp-wmi.ko");
        fs::create_dir_all(stock.parent().unwrap()).unwrap();
        for (number, target) in [(0, &stock), (1, &custom)] {
            let source = root.join(format!("module{number}.c"));
            fs::write(
                &source,
                format!(
                    "const char name[] __attribute__((section(\".modinfo\"))) = \"name=hp_wmi\";\nint identity = {number};\n"
                ),
            )
            .unwrap();
            let obj = root.join(format!("module{number}.o"));
            let compiled = Command::new("cc")
                .env("CCACHE_DISABLE", "1")
                .env("CCACHE_TEMPDIR", &root)
                .args(["-c"])
                .arg(&source)
                .arg("-o")
                .arg(&obj)
                .output()
                .unwrap();
            assert!(compiled.status.success(), "{}", String::from_utf8_lossy(&compiled.stderr));
            let linked = Command::new("ld").args(["-r", "--build-id"]).arg(&obj).arg("-o").arg(target).output().unwrap();
            assert!(linked.status.success(), "{}", String::from_utf8_lossy(&linked.stderr));
        }
        let stock_bytes = fs::read(&stock).unwrap();
        let config = root.join("etc/depmod.d");
        fs::create_dir_all(&config).unwrap();
        fs::write(config.join("search.conf"), "search kernel extra\n").unwrap();
        fs::create_dir_all(root.join("etc/modules-load.d")).unwrap();
        let helper = fs::read_to_string(repo_root().join("scripts/secure-boot.sh")).unwrap();
        fs::create_dir_all(root.join("scripts")).unwrap();
        fs::write(
            root.join("scripts/secure-boot.sh"),
            rewrite(&helper, &root, &["/var/lib/", "/lib/modules", "/usr/src/", "/sys/", "/dev/tty"]),
        )
        .unwrap();
        for name in ["dkms-post-install", "dkms-post-remove"] {
            let hook = fs::read_to_string(repo_root().join("kernel").join(name)).unwrap();
            fs::write(root.join("kernel").join(name), rewrite(&hook, &root, &["/lib/modules", "/etc/"])).unwrap();
        }
        let mut install = Self {
            root,
            release,
            scripts,
            stock,
            custom,
            stock_bytes,
            sign_file: PathBuf::new(),
            env: std::env::vars().collect(),
        };
        install.write_command("update-initramfs", "printf \"%s\\n\" \"$*\" >> \"$TEST_ROOT/initramfs-log\"");
        for name in ["install", "uninstall"] {
            let text = fs::read_to_string(repo_root().join("kernel/hp-wmi/scripts").join(name)).unwrap();
            fs::write(
                install.scripts.join(name),
                rewrite(&text, &install.root, &["/usr/lib/modules", "/lib/modules", "/etc/", "/sys/"]),
            )
            .unwrap();
        }
        install.write_command("sudo", "exec \"$@\"");
        install.write_command("make", "exit 0");
        install.write_command(
            "mokutil",
            "case \"$1\" in\n --sb-state) printf \"SecureBoot %s\\n\" \"${TEST_SB_STATE:-disabled}\";;\n --test-key) [ \"${TEST_ENROLLED:-0}\" = 1 ];;\n --list-new) if [ \"${TEST_PENDING:-1}\" = 1 ]; then openssl x509 -inform DER -in \"$TEST_ROOT/var/lib/victus-hub/mok/MOK.der\" -noout -fingerprint -sha1; fi;;\n --import) printf \"import\\n\" >> \"$TEST_ROOT/imports\"; exit \"${TEST_IMPORT_EXIT:-0}\";;\n *) exit 2;;\nesac",
        );
        let sign_dir = modules.join("build/scripts");
        fs::create_dir_all(&sign_dir).unwrap();
        install.sign_file = sign_dir.join("sign-file");
        write_exe(
            &install.sign_file,
            "#!/bin/bash\nset -eu\n[ \"${TEST_SIGN_FAIL:-0}\" = 0 ] || exit 1\ntest -s \"$2\" && test -s \"$3\"\nprintf \"test signature\" >> \"$4\"\n",
        );
        let depmod = which("depmod").unwrap();
        let modinfo = which("modinfo").unwrap();
        install.write_command(
            "depmod",
            &format!("exec \"{}\" -b \"$TEST_ROOT\" -C \"$TEST_ROOT/etc/depmod.d\" \"$@\"", depmod.display()),
        );
        install.write_command(
            "modinfo",
            &format!(
                "if [ \"${{FORCE_STOCK_LOOKUP:-0}}\" = 1 ]; then printf \"%s\\n\" \"$TEST_STOCK\"; exit; fi\nexec \"{}\" -b \"$TEST_ROOT\" \"$@\"",
                modinfo.display()
            ),
        );
        install.write_command(
            "modprobe",
            "module=\"$TEST_ROOT/sys/module/hp_wmi\"\nplatform=\"$TEST_ROOT/sys/devices/platform/hp-wmi\"\nif [ \"${1:-}\" = -r ]; then rm -rf \"$module\" \"$platform\"; exit; fi\nselected=$(modinfo -n hp-wmi)\nif [ \"${FORCE_STOCK_LOAD:-0}\" = 1 ]; then selected=\"$TEST_STOCK\"; fi\nmkdir -p \"$module/notes\" \"$platform\"\nobjcopy --dump-section \".note.gnu.build-id=$module/notes/.note.gnu.build-id\" \"$selected\" /dev/null\ncase \"$selected\" in */extra/*) touch \"$platform/gpu_mux_mode\" \"$platform/gpu_mux_supported_names\";; esac",
        );
        let path = format!("{}:{}", bin.display(), std::env::var("PATH").unwrap_or_default());
        install.env_set("PATH", &path);
        install.env_set("TEST_ROOT", &install.root.display().to_string());
        install.env_set("TEST_STOCK", &install.stock.display().to_string());
        let prepared = install.run(&["depmod".into(), "-a".into(), install.release.clone()]);
        assert!(prepared.status.success(), "{}", String::from_utf8_lossy(&prepared.stderr));
        let loaded = install.run(&["modprobe".into(), "hp-wmi".into()]);
        assert!(loaded.status.success(), "{}", String::from_utf8_lossy(&loaded.stderr));
        Some(install)
    }

    fn env_set(&mut self, key: &str, value: &str) {
        if let Some(slot) = self.env.iter_mut().find(|(name, _)| name == key) {
            slot.1 = value.to_owned();
        } else {
            self.env.push((key.to_owned(), value.to_owned()));
        }
    }

    fn write_command(&self, name: &str, body: &str) {
        write_exe(&self.root.join("bin").join(name), &format!("#!/bin/bash\nset -eu\n{body}\n"));
    }

    fn run(&self, args: &[String]) -> Output {
        let mut command = Command::new(&args[0]);
        command.args(&args[1..]);
        command.env_clear();
        for (key, value) in &self.env {
            command.env(key, value);
        }
        command.output().unwrap()
    }

    fn bash(&self, script: &Path) -> Output {
        self.run(&["bash".into(), script.display().to_string()])
    }
}

impl Drop for Install {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn install_overrides_stock_and_uninstall_restores_it() {
    let Some(install) = Install::new() else { return };
    let listed = install.run(&["modinfo".into(), "-n".into(), "hp-wmi".into()]);
    assert_eq!(stdout(&listed).trim(), install.stock.display().to_string());
    let result = install.bash(&install.scripts.join("install"));
    assert!(stdout(&result).contains("Verified custom module build ID and MUX interfaces"), "{}", stderr(&result));
    let listed = install.run(&["modinfo".into(), "-n".into(), "hp-wmi".into()]);
    assert!(stdout(&listed).contains("/extra/hp-wmi.ko"), "{}", stdout(&listed));
    let result = install.bash(&install.scripts.join("uninstall"));
    assert!(stdout(&result).contains("Restored in-tree hp-wmi"), "{}", stderr(&result));
    let expected = format!("-u -k {}\n", install.release).repeat(2);
    assert_eq!(fs::read_to_string(install.root.join("initramfs-log")).unwrap(), expected);
    let listed = install.run(&["modinfo".into(), "-n".into(), "hp-wmi".into()]);
    assert_eq!(stdout(&listed).trim(), install.stock.display().to_string());
    assert_eq!(fs::read(&install.stock).unwrap(), install.stock_bytes);
    let override_left = fs::read_dir(install.root.join("etc/depmod.d"))
        .unwrap()
        .filter_map(Result::ok)
        .any(|entry| entry.file_name().to_string_lossy().starts_with("victus-hub-"));
    assert!(!override_left);
    assert!(!install.root.join("etc/modules-load.d/hp-wmi.conf").exists());
    assert!(!install.root.join("sys/devices/platform/hp-wmi/gpu_mux_mode").exists());
}

#[test]
fn wrong_lookup_fails_before_unloading_stock() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("FORCE_STOCK_LOOKUP", "1");
    let result = install.bash(&install.scripts.join("install"));
    assert!(!result.status.success());
    assert!(stderr(&result).contains("Module lookup selected"), "{}", stderr(&result));
    assert!(!stdout(&result).contains("Unloading currently loaded"));
    assert!(install.root.join("sys/module/hp_wmi").is_dir());
}

#[test]
fn wrong_loaded_build_is_not_reported_as_success() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("FORCE_STOCK_LOAD", "1");
    let result = install.bash(&install.scripts.join("install"));
    assert!(!result.status.success());
    assert!(stderr(&result).contains("build ID does not match"), "{}", stderr(&result));
    assert!(!stdout(&result).contains("Installed and loaded"));
}

#[test]
fn secure_boot_pending_preserves_loaded_driver_and_signs_copy() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("TEST_SB_STATE", "enabled");
    let original = fs::read(&install.custom).unwrap();
    let loaded_note = install.root.join("sys/module/hp_wmi/notes/.note.gnu.build-id");
    let stock_note = fs::read(&loaded_note).unwrap();
    let result = install.bash(&install.scripts.join("install"));
    assert!(stdout(&result).contains("deferred until MOK enrollment"), "{}\n{}", stdout(&result), stderr(&result));
    assert!(!stdout(&result).contains("Unloading currently loaded"));
    assert_eq!(fs::read(&loaded_note).unwrap(), stock_note);
    let listed = install.run(&["modinfo".into(), "-n".into(), "hp-wmi".into()]);
    let installed = PathBuf::from(stdout(&listed).trim());
    assert_eq!(fs::read(&installed).unwrap(), [original.as_slice(), b"test signature"].concat());
    assert_eq!(fs::read(&install.custom).unwrap(), original);
    assert!(install.root.join("etc/modules-load.d/hp-wmi.conf").exists());
    let key = install.root.join("var/lib/victus-hub/mok/MOK.priv");
    assert_eq!(fs::metadata(&key).unwrap().permissions().mode() & 0o777, 0o600);
    assert_eq!(fs::metadata(key.parent().unwrap()).unwrap().permissions().mode() & 0o777, 0o700);
    let original_key = fs::read(&key).unwrap();
    install.env_set("TEST_ENROLLED", "1");
    let result = install.bash(&install.scripts.join("install"));
    assert!(stdout(&result).contains("Verified custom module build ID"), "{}", stderr(&result));
    assert_eq!(fs::read(&key).unwrap(), original_key);
    assert_eq!(fs::read(&installed).unwrap(), [original.as_slice(), b"test signature"].concat());
}

#[test]
fn secure_boot_signing_failure_preserves_installed_driver() {
    let Some(mut install) = Install::new() else { return };
    let result = install.bash(&install.scripts.join("install"));
    assert!(result.status.success(), "{}", stderr(&result));
    let listed = install.run(&["modinfo".into(), "-n".into(), "hp-wmi".into()]);
    let installed = PathBuf::from(stdout(&listed).trim());
    let original = fs::read(&installed).unwrap();
    install.env_set("TEST_SB_STATE", "enabled");
    install.env_set("TEST_ENROLLED", "1");
    install.env_set("TEST_SIGN_FAIL", "1");
    let result = install.bash(&install.scripts.join("install"));
    assert!(!result.status.success());
    assert_eq!(fs::read(&installed).unwrap(), original);
    assert!(!stdout(&result).contains("Unloading currently loaded"));
}

#[test]
fn secure_boot_missing_signer_fails_before_install() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("TEST_SB_STATE", "enabled");
    fs::remove_file(&install.sign_file).unwrap();
    let result = install.bash(&install.scripts.join("install"));
    assert!(!result.status.success());
    assert!(stderr(&result).contains("requires scripts/sign-file"), "{}", stderr(&result));
    let listed = install.run(&["modinfo".into(), "-n".into(), "hp-wmi".into()]);
    assert_eq!(stdout(&listed).trim(), install.stock.display().to_string());
}

#[test]
fn secure_boot_import_uses_terminal_with_piped_stdin() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("TEST_SB_STATE", "enabled");
    install.env_set("TEST_PENDING", "0");
    let tty = install.root.join("dev/tty");
    fs::create_dir_all(tty.parent().unwrap()).unwrap();
    fs::write(&tty, "").unwrap();
    let result = install.bash(&install.scripts.join("install"));
    assert!(result.status.success(), "{}", stderr(&result));
    assert_eq!(fs::read_to_string(install.root.join("imports")).unwrap(), "import\n");
    assert!(stdout(&result).contains("one-time MOK enrollment password"));
    assert!(stdout(&result).contains("deferred until MOK enrollment"));
}

#[test]
fn secure_boot_import_failure_stops_before_install() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("TEST_SB_STATE", "enabled");
    install.env_set("TEST_PENDING", "0");
    install.env_set("TEST_IMPORT_EXIT", "1");
    let tty = install.root.join("dev/tty");
    fs::create_dir_all(tty.parent().unwrap()).unwrap();
    fs::write(&tty, "").unwrap();
    let result = install.bash(&install.scripts.join("install"));
    assert!(!result.status.success());
    assert!(!stdout(&result).contains("Installing"));
    let listed = install.run(&["modinfo".into(), "-n".into(), "hp-wmi".into()]);
    assert_eq!(stdout(&listed).trim(), install.stock.display().to_string());
}

#[test]
fn secure_boot_without_terminal_prints_manual_import() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("TEST_SB_STATE", "enabled");
    install.env_set("TEST_PENDING", "0");
    let result = install.bash(&install.scripts.join("install"));
    assert!(result.status.success(), "{}", stderr(&result));
    let text = stdout(&result);
    assert!(text.contains("No terminal available"), "{text}");
    assert!(text.contains("sudo mokutil --import"));
    assert!(text.contains("deferred until MOK enrollment"));
    assert!(!install.root.join("imports").exists());
}

#[test]
fn unknown_secure_boot_state_is_not_treated_as_disabled() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("TEST_SB_STATE", "unknown");
    let result = install.bash(&install.scripts.join("install"));
    assert!(!result.status.success());
    assert!(stderr(&result).contains("Unrecognized Secure Boot state"), "{}", stderr(&result));
    assert!(!stdout(&result).contains("Installing"));
}

#[test]
fn keyboard_secure_boot_pending_does_not_unload() {
    let Some(mut install) = Install::new() else { return };
    install.env_set("TEST_SB_STATE", "enabled");
    let module_dir = install.root.join("kernel/hp-kbd-rgb");
    let scripts = module_dir.join("scripts");
    fs::create_dir_all(&scripts).unwrap();
    let source = module_dir.join("hp-kbd-rgb.ko");
    fs::write(&source, fs::read(&install.custom).unwrap()).unwrap();
    fs::create_dir_all(install.root.join("sys/module/hp_kbd_rgb")).unwrap();
    let text = fs::read_to_string(repo_root().join("kernel/hp-kbd-rgb/scripts/install")).unwrap();
    fs::write(&scripts.join("install"), rewrite(&text, &install.root, &["/lib/modules", "/etc/", "/sys/"])).unwrap();
    let result = install.bash(&scripts.join("install"));
    assert!(stdout(&result).contains("deferred until MOK enrollment"), "{}\n{}", stdout(&result), stderr(&result));
    assert!(!stdout(&result).contains("Unloading currently loaded"));
    let installed = install.root.join("lib/modules").join(&install.release).join("extra/hp-kbd-rgb.ko");
    let mut expected = fs::read(&source).unwrap();
    expected.extend_from_slice(b"test signature");
    assert_eq!(fs::read(&installed).unwrap(), expected);
    assert_eq!(
        fs::read_to_string(install.root.join("etc/modules-load.d/hp-kbd-rgb.conf")).unwrap(),
        "led-class-multicolor\nhp-kbd-rgb\n"
    );
}
