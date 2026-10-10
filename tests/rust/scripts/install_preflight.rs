use std::fs::{self, File};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|dir| dir.join(name)).find(|candidate| candidate.is_file())
}

fn scratch(label: &str) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "victus-install-{label}-{}-{}",
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

fn bash_body(path: &Path, body: &str) {
    write_exe(path, &format!("#!/bin/bash\n{body}\n"));
}

fn run(program: &Path, args: &[&str], env: &[(&str, &str)]) -> Output {
    let mut command = Command::new("/bin/bash");
    command.arg(program).args(args);
    for key in ["SUDO_UID", "SUDO_GID", "SUDO_USER", "VICTUS_HUB_BUILD_HOME"] {
        command.env_remove(key);
    }
    for (key, value) in env {
        command.env(key, value);
    }
    command.output().unwrap()
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

#[test]
fn missing_packages_are_reported_without_installing() {
    let root = scratch("preflight-missing");
    let bin = root.join("bin");
    fs::create_dir_all(&bin).unwrap();
    bash_body(&bin.join("uname"), "printf 'test-kernel\\n'");
    bash_body(&bin.join("id"), "printf '0\\n'");
    bash_body(&bin.join("python3"), "exit 0");
    bash_body(&bin.join("systemctl"), "exit 0");
    bash_body(&bin.join("loginctl"), "exit 0");
    std::os::unix::fs::symlink(which("grep").unwrap(), bin.join("grep")).unwrap();
    fs::create_dir_all(root.join("run/systemd/system")).unwrap();
    fs::create_dir_all(root.join("proc")).unwrap();
    fs::write(root.join("proc/cpuinfo"), "GenuineIntel\n").unwrap();
    let source = fs::read_to_string(repo_root().join("scripts/preflight.sh")).unwrap();
    let script = root.join("preflight");
    fs::write(
        &script,
        rewrite(
            &source,
            &root,
            &[
                "/usr/lib/x86_64-linux-gnu",
                "/lib/x86_64-linux-gnu",
                "/usr/lib64",
                "/lib64",
                "/run/systemd",
                "/lib/modules",
                "/usr/src",
                "/sys/firmware",
                "/proc/cpuinfo",
            ],
        ) + "\npreflight \"${1:-0}\"\n",
    )
    .unwrap();
    let path = bin.display().to_string();
    for (manager, label) in [("apt-get", "Ubuntu/Mint"), ("pacman", "Arch"), ("dnf", "Fedora")] {
        bash_body(&bin.join(manager), "printf \"PACKAGE MANAGER WAS EXECUTED\"; exit 99");
        let result = run(&script, &["0"], &[("PATH", &path)]);
        assert!(!result.status.success(), "{manager}");
        let stderr = text(&result.stderr);
        assert!(stderr.contains(label), "{stderr}");
        assert!(stderr.contains("dkms"), "{stderr}");
        assert!(stderr.contains("headers/devel for running kernel test-kernel"), "{stderr}");
        let combined = format!("{}{stderr}", text(&result.stdout));
        assert!(!combined.contains("PACKAGE MANAGER WAS EXECUTED"), "{combined}");
        fs::remove_file(bin.join(manager)).unwrap();
    }
    let _ = fs::remove_dir_all(root);
}

fn plant_shared_libs(root: &Path) {
    let libdir = root.join("usr/lib64");
    fs::create_dir_all(&libdir).unwrap();
    for name in ["libsystemd.so.0", "libgtk-4.so.1", "libadwaita-1.so.0"] {
        fs::write(libdir.join(name), "").unwrap();
    }
}

fn app_stubs(bin: &Path, with_cargo: bool) {
    bash_body(&bin.join("gdbus"), "exit 0");
    bash_body(&bin.join("rustc"), "printf 'rustc 1.90.0\\n'");
    bash_body(&bin.join("pkg-config"), "exit 0");
    if with_cargo {
        bash_body(&bin.join("cargo"), "exit 0");
    }
}

#[test]
fn app_only_does_not_require_build_dependencies() {
    let root = scratch("preflight-app");
    let bin = root.join("bin");
    fs::create_dir_all(&bin).unwrap();
    bash_body(&bin.join("uname"), "printf 'test-kernel\\n'");
    bash_body(&bin.join("id"), "printf '0\\n'");
    bash_body(&bin.join("python3"), "exit 0");
    bash_body(&bin.join("systemctl"), "exit 0");
    bash_body(&bin.join("loginctl"), "exit 0");
    std::os::unix::fs::symlink(which("grep").unwrap(), bin.join("grep")).unwrap();
    fs::create_dir_all(root.join("run/systemd/system")).unwrap();
    let source = fs::read_to_string(repo_root().join("scripts/preflight.sh")).unwrap();
    let script = root.join("preflight");
    fs::write(
        &script,
        rewrite(
            &source,
            &root,
            &[
                "/usr/lib/x86_64-linux-gnu",
                "/lib/x86_64-linux-gnu",
                "/usr/lib64",
                "/lib64",
                "/run/systemd",
                "/lib/modules",
                "/usr/src",
                "/sys/firmware",
                "/proc/cpuinfo",
            ],
        ) + "\npreflight \"${1:-0}\"\n",
    )
    .unwrap();
    app_stubs(&bin, true);
    plant_shared_libs(&root);
    let path = bin.display().to_string();
    let result = run(&script, &["1"], &[("PATH", &path)]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn missing_cargo_is_explicit() {
    let root = scratch("preflight-cargo");
    let bin = root.join("bin");
    fs::create_dir_all(&bin).unwrap();
    bash_body(&bin.join("uname"), "printf 'test-kernel\\n'");
    bash_body(&bin.join("id"), "printf '0\\n'");
    bash_body(&bin.join("python3"), "exit 0");
    bash_body(&bin.join("systemctl"), "exit 0");
    bash_body(&bin.join("loginctl"), "exit 0");
    std::os::unix::fs::symlink(which("grep").unwrap(), bin.join("grep")).unwrap();
    fs::create_dir_all(root.join("run/systemd/system")).unwrap();
    let source = fs::read_to_string(repo_root().join("scripts/preflight.sh")).unwrap();
    let script = root.join("preflight");
    fs::write(
        &script,
        rewrite(
            &source,
            &root,
            &[
                "/usr/lib/x86_64-linux-gnu",
                "/lib/x86_64-linux-gnu",
                "/usr/lib64",
                "/lib64",
                "/run/systemd",
                "/lib/modules",
                "/usr/src",
                "/sys/firmware",
                "/proc/cpuinfo",
            ],
        ) + "\npreflight \"${1:-0}\"\n",
    )
    .unwrap();
    app_stubs(&bin, false);
    plant_shared_libs(&root);
    let path = bin.display().to_string();
    let result = run(&script, &["1"], &[("PATH", &path)]);
    assert!(!result.status.success());
    assert!(text(&result.stderr).contains("cargo"), "{}", text(&result.stderr));
    let _ = fs::remove_dir_all(root);
}

fn preflight_root(label: &str) -> (PathBuf, PathBuf, PathBuf) {
    let root = scratch(label);
    let bin = root.join("bin");
    fs::create_dir_all(&bin).unwrap();
    bash_body(&bin.join("uname"), "printf 'test-kernel\\n'");
    bash_body(&bin.join("id"), "printf '0\\n'");
    bash_body(&bin.join("python3"), "exit 0");
    bash_body(&bin.join("systemctl"), "exit 0");
    bash_body(&bin.join("loginctl"), "exit 0");
    bash_body(&bin.join("gdbus"), "exit 0");
    bash_body(&bin.join("pkg-config"), "exit 0");
    std::os::unix::fs::symlink(which("cut").unwrap(), bin.join("cut")).unwrap();
    fs::create_dir_all(root.join("run/systemd/system")).unwrap();
    let source = fs::read_to_string(repo_root().join("scripts/preflight.sh")).unwrap();
    let script = root.join("preflight");
    fs::write(
        &script,
        rewrite(
            &source,
            &root,
            &[
                "/usr/lib/x86_64-linux-gnu",
                "/lib/x86_64-linux-gnu",
                "/usr/lib64",
                "/lib64",
                "/run/systemd",
                "/lib/modules",
                "/usr/src",
                "/sys/firmware",
                "/proc/cpuinfo",
            ],
        ) + "\npreflight \"${1:-0}\"\n",
    )
    .unwrap();
    plant_shared_libs(&root);
    (root, bin, script)
}

#[test]
fn sudo_uses_invoking_user_rustup() {
    let (root, bin, script) = preflight_root("preflight-sudo-rustup");
    let home = root.join("home/dev");
    let cargo_bin = home.join(".cargo/bin");
    fs::create_dir_all(&cargo_bin).unwrap();
    bash_body(&cargo_bin.join("cargo"), "exit 0");
    bash_body(
        &cargo_bin.join("rustc"),
        &format!(
            "if [ \"${{VICTUS_HUB_INVOKED_AS_USER:-}}\" != 1 ]; then printf 'rustc was not run as the invoking user\\n' >&2; exit 1; fi\nif [ \"$HOME\" != '{home}' ]; then printf 'HOME=%s\\n' \"$HOME\" >&2; exit 1; fi\nprintf 'rustc 1.90.0\\n'",
            home = home.display()
        ),
    );
    bash_body(
        &bin.join("getent"),
        &format!("printf 'user:x:%s:%s::{home}:/bin/bash\\n' \"$2\" \"$2\"", home = home.display()),
    );
    bash_body(
        &bin.join("sudo"),
        "while [ $# -gt 0 ]; do\n  case \"$1\" in\n    --) shift; break ;;\n    -u|--user|-g|--group) shift 2 ;;\n    -*) shift ;;\n    *) break ;;\n  esac\ndone\nexport VICTUS_HUB_INVOKED_AS_USER=1\nexec \"$@\"\n",
    );
    std::os::unix::fs::symlink(which("env").unwrap(), bin.join("env")).unwrap();
    let path = bin.display().to_string();
    let result = run(&script, &["1"], &[("PATH", &path), ("SUDO_UID", "4242")]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stdout).contains("Dependency preflight passed."), "{}", text(&result.stdout));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn sudo_keeps_system_toolchain_without_user_rustup() {
    let (root, bin, script) = preflight_root("preflight-sudo-system");
    bash_body(&bin.join("cargo"), "exit 0");
    bash_body(&bin.join("rustc"), "printf 'rustc 1.90.0\\n'");
    let home = root.join("home/dev");
    bash_body(
        &bin.join("getent"),
        &format!("printf 'user:x:%s:%s::{home}:/bin/bash\\n' \"$2\" \"$2\"", home = home.display()),
    );
    let path = bin.display().to_string();
    let result = run(&script, &["1"], &[("PATH", &path), ("SUDO_UID", "4242")]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn split_ubuntu_mint_headers_reject_unsupported_kernel() {
    let root = scratch("preflight-headers");
    let bin = root.join("bin");
    fs::create_dir_all(&bin).unwrap();
    bash_body(&bin.join("uname"), "printf '6.8.0-100-generic\\n'");
    bash_body(&bin.join("id"), "printf '0\\n'");
    bash_body(&bin.join("python3"), "exit 0");
    bash_body(&bin.join("systemctl"), "exit 0");
    bash_body(&bin.join("loginctl"), "exit 0");
    std::os::unix::fs::symlink(which("grep").unwrap(), bin.join("grep")).unwrap();
    fs::create_dir_all(root.join("run/systemd/system")).unwrap();
    fs::create_dir_all(root.join("proc")).unwrap();
    fs::write(root.join("proc/cpuinfo"), "GenuineIntel\n").unwrap();
    let header = root.join("usr/src/linux-headers-6.8.0-100/include/linux/platform_profile.h");
    fs::create_dir_all(header.parent().unwrap()).unwrap();
    fs::write(&header, "/* Older platform_profile API */\n").unwrap();
    let source = fs::read_to_string(repo_root().join("scripts/preflight.sh")).unwrap();
    let script = root.join("preflight");
    fs::write(
        &script,
        rewrite(
            &source,
            &root,
            &[
                "/usr/lib/x86_64-linux-gnu",
                "/lib/x86_64-linux-gnu",
                "/usr/lib64",
                "/lib64",
                "/run/systemd",
                "/lib/modules",
                "/usr/src",
                "/sys/firmware",
                "/proc/cpuinfo",
            ],
        ) + "\npreflight \"${1:-0}\"\n",
    )
    .unwrap();
    let path = bin.display().to_string();
    let result = run(&script, &["0"], &[("PATH", &path)]);
    assert!(text(&result.stderr).contains("lacks devm_platform_profile_register"), "{}", text(&result.stderr));
    fs::write(&header, "devm_platform_profile_register\n").unwrap();
    let result = run(&script, &["0"], &[("PATH", &path)]);
    assert!(!text(&result.stderr).contains("lacks devm_platform_profile_register"), "{}", text(&result.stderr));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn missing_rgb_does_not_abort_installation() {
    let root = scratch("optional-rgb");
    let bin = root.join("bin");
    fs::create_dir_all(&bin).unwrap();
    bash_body(&bin.join("modprobe"), "printf \"No such device\\n\" >&2; exit 1");
    bash_body(&bin.join("rm"), "exit 0");
    let script = repo_root().join("scripts/optional-rgb.sh");
    let path = bin.display().to_string();
    let mut command = Command::new("/bin/bash");
    command.args([
        "-c",
        "set -e; SUDO=(); source \"$1\"; load_optional_rgb; printf 'CONTINUED\\n'",
        "bash",
    ]);
    command.arg(&script);
    command.env("PATH", &path);
    let result = command.output().unwrap();
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stdout).contains("CONTINUED"));
    assert!(text(&result.stderr).contains("continuing without RGB"), "{}", text(&result.stderr));
    let _ = fs::remove_dir_all(root);
}

#[test]
fn dkms_hooks_refresh_or_drop_the_override() {
    for (location, default_priority) in [
        ("updates/dkms", false),
        ("updates", false),
        ("extra", false),
        ("updates/dkms", true),
        ("updates", true),
        ("extra", true),
    ] {
        check_hooks(default_priority, location);
    }
}

fn check_hooks(default_priority: bool, location: &str) {
    let root = scratch("dkms-hook");
    let binary = root.join("bin");
    fs::create_dir_all(&binary).unwrap();
    let log = root.join("log");
    let (tool, args) = match location {
        "updates/dkms" => ("update-initramfs", "-u -k future-kernel"),
        "updates" => ("mkinitcpio", "-P"),
        "extra" => ("dracut", "--force --kver future-kernel"),
        _ => unreachable!(),
    };
    for command in ["depmod", tool] {
        bash_body(
            &binary.join(command),
            &format!("printf \"{command} %s\\n\" \"$*\" >> \"$TEST_LOG\""),
        );
    }
    for command in ["install", "rm", "readlink"] {
        std::os::unix::fs::symlink(which(command).unwrap(), binary.join(command)).unwrap();
    }
    let module = root.join("lib/modules/future-kernel").join(location).join("hp-wmi.ko.zst");
    fs::create_dir_all(module.parent().unwrap()).unwrap();
    fs::write(&module, "").unwrap();
    let selected = if default_priority { module.clone() } else { root.join("stock/hp-wmi.ko") };
    bash_body(&binary.join("modinfo"), &format!("printf \"%s\\n\" \"{}\"\n", selected.display()));
    let conf = root.join("etc/depmod.d/victus-hub-hp-wmi-future-kernel.conf");
    fs::create_dir_all(conf.parent().unwrap()).unwrap();
    fs::write(&conf, "override hp-wmi future-kernel extra\n").unwrap();
    let path = binary.display().to_string();
    let log_text = log.display().to_string();
    for hook_name in ["dkms-post-install", "dkms-post-remove"] {
        let hook = fs::read_to_string(repo_root().join("kernel").join(hook_name)).unwrap();
        let script = root.join(hook_name);
        fs::write(&script, rewrite(&hook, &root, &["/lib/modules", "/etc/depmod.d"])).unwrap();
        let result = run(&script, &["future-kernel", "hp-wmi"], &[("PATH", &path), ("TEST_LOG", &log_text)]);
        assert!(result.status.success(), "{hook_name} {}\n{}", text(&result.stderr), text(&result.stdout));
        if hook_name.ends_with("install") && !default_priority {
            assert_eq!(fs::read_to_string(&conf).unwrap(), format!("override hp-wmi future-kernel {location}\n"));
        } else {
            assert!(!conf.exists(), "{hook_name} left {conf:?}");
        }
    }
    let mut expected = format!("depmod -a future-kernel\n{tool} {args}\n").repeat(2);
    if !default_priority {
        expected = format!("depmod -a future-kernel\n{expected}");
    }
    assert_eq!(fs::read_to_string(&log).unwrap(), expected);
    let _ = fs::remove_dir_all(root);
}

const DKMS_STUB: &str = r#"
import os, pathlib, sys
root = pathlib.Path(os.environ['TEST_ROOT'])
args = sys.argv[1:]
if args == ['--version']:
    print('dkms-3.2.0')
    sys.exit(0)
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(' '.join(args) + '\n')
action = args[0]
package = args[args.index('-m') + 1]
version = args[args.index('-v') + 1]
state = root / (package + '-state')
stored_version, current = '', ''
if state.exists():
    parts = state.read_text().splitlines()
    if len(parts) >= 2:
        stored_version, current = parts[0], parts[1]
if action == 'status':
    if current and stored_version == version:
        suffix = os.environ.get('TEST_STATUS_SUFFIX', '') if current == 'installed' else ''
        print(f'{package}/{version}, test-kernel, x86_64: {current}{suffix}')
elif action == 'add':
    state.write_text(f'{version}\nadded\n')
elif action == 'build':
    if os.environ.get('TEST_BUILD_FAIL'):
        sys.exit(10)
    state.write_text(f'{version}\nbuilt\n')
    if os.environ.get('TEST_SECURE_BOOT') == '1':
        (root / (package.removeprefix('victus-hub-') + '-signed')).touch()
elif action == 'install':
    module = package.removeprefix('victus-hub-')
    path = root / 'lib/modules/test-kernel/extra' / (module + '.ko')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    state.write_text(f'{version}\ninstalled\n')
elif action == 'remove':
    state.unlink(missing_ok=True)
"#;

struct Dkms {
    root: PathBuf,
    bin: PathBuf,
    log: PathBuf,
    env: Vec<(String, String)>,
}

impl Dkms {
    fn new() -> Self {
        let root = scratch("dkms");
        let bin = root.join("bin");
        let scripts = root.join("scripts");
        fs::create_dir_all(&scripts).unwrap();
        for name in ["dkms-install", "optional-rgb.sh"] {
            let text = fs::read_to_string(repo_root().join("scripts").join(name)).unwrap();
            fs::write(
                scripts.join(name),
                rewrite(&text, &root, &["/usr/src", "/etc/", "/lib/modules", "/sys/"]),
            )
            .unwrap();
        }
        fs::write(
            scripts.join("secure-boot.sh"),
            "secure_boot_prepare() { SECURE_BOOT=${TEST_SECURE_BOOT:-0}; MOK_PENDING=${TEST_MOK_PENDING:-0}; MOK_KEY=test-key; MOK_CERT=test-cert; }\n",
        )
        .unwrap();
        for module in ["hp-wmi", "hp-kbd-rgb"] {
            let directory = root.join("kernel").join(module);
            fs::create_dir_all(&directory).unwrap();
            fs::write(directory.join(format!("{module}.c")), "/* Test source */\n").unwrap();
            fs::copy(repo_root().join("kernel").join(module).join("Makefile"), directory.join("Makefile")).unwrap();
        }
        for name in ["dkms-post-install", "dkms-post-remove"] {
            fs::copy(repo_root().join("kernel").join(name), root.join("kernel").join(name)).unwrap();
        }
        let platform = root.join("sys/devices/platform/hp-wmi");
        fs::create_dir_all(&platform).unwrap();
        fs::write(platform.join("gpu_mux_supported_names"), "").unwrap();
        let log = root.join("log");
        let mut harness = Self { root, bin, log, env: std::env::vars().collect() };
        harness.command("sudo", "exec \"$@\"");
        harness.command("id", "printf '0\\n'");
        harness.command("uname", "printf 'test-kernel\\n'");
        harness.command(
            "modprobe",
            "printf \"modprobe %s\\n\" \"$*\" >> \"$TEST_LOG\"\n[ \"$1\" != hp-kbd-rgb ] || { printf \"No such device\\n\" >&2; exit 1; }",
        );
        harness.command(
            "modinfo",
            "printf \"modinfo %s\\n\" \"$*\" >> \"$TEST_LOG\"\ncase \" $* \" in\n  *\" -n \"*)\n    module=${@: -1}\n    if [ \"${TEST_MODINFO_KIND:-extra}\" = stock ]; then\n      printf \"%s/lib/modules/test-kernel/kernel/drivers/%s.ko\\n\" \"$TEST_ROOT\" \"$module\"\n    else\n      printf \"%s/%s/modules/test-kernel/extra/%s.ko%s\\n\" \"$TEST_ROOT\" \"${TEST_MODULE_LIB:-lib}\" \"$module\" \"${TEST_MODULE_SUFFIX:-}\"\n    fi\n    ;;\n  *)\n    module=${@: -1}\n    if [ -f \"$TEST_ROOT/$module-signed\" ]; then printf \"Victus-Hub\\n\";\n    else printf \"%s\\n\" \"${TEST_SIGNER:-Victus-Hub}\"; fi ;;\nesac",
        );
        harness.command(
            "objcopy",
            "[ -z \"${TEST_OBJCOPY_FAIL:-}\" ] || exit 1\ndest=\nprev=\nfor arg in \"$@\"; do\n  if [ \"$prev\" = \"--dump-section\" ]; then dest=${arg#*=}; fi\n  prev=$arg\ndone\n[ -n \"$dest\" ] || exit 1\nprintf \"%s\" \"${TEST_BUILD_NOTE:-same-build}\" > \"$dest\"",
        );
        let python = which("python3").unwrap();
        write_exe(&harness.bin.join("dkms"), &format!("#!{}\n{DKMS_STUB}", python.display()));
        for tool in ["sha256sum", "cut", "grep", "install", "tee", "mktemp", "cat", "cmp", "rm", "dirname", "readlink"] {
            let dest = harness.bin.join(tool);
            if !dest.exists() {
                std::os::unix::fs::symlink(which(tool).unwrap(), dest).unwrap();
            }
        }
        harness.env_set("PATH", &harness.bin.display().to_string());
        harness.env_set("TEST_ROOT", &harness.root.display().to_string());
        harness.env_set("TEST_LOG", &harness.log.display().to_string());
        harness
    }

    fn command(&self, name: &str, body: &str) {
        bash_body(&self.bin.join(name), body);
    }

    fn env_set(&mut self, key: &str, value: &str) {
        if let Some(slot) = self.env.iter_mut().find(|(name, _)| name == key) {
            slot.1 = value.to_owned();
        } else {
            self.env.push((key.to_owned(), value.to_owned()));
        }
    }

    fn install(&self) -> Output {
        let mut command = Command::new("/bin/bash");
        command.arg(self.root.join("scripts/dkms-install"));
        command.env_clear();
        for (key, value) in &self.env {
            command.env(key, value);
        }
        command.output().unwrap()
    }

    fn load_module(&self, sys_name: &str, build_note: &str) {
        let notes = self.root.join("sys/module").join(sys_name).join("notes");
        fs::create_dir_all(&notes).unwrap();
        fs::write(notes.join(".note.gnu.build-id"), build_note).unwrap();
        let ko = self
            .root
            .join("lib/modules/test-kernel/extra")
            .join(format!("{}.ko", sys_name.replace('_', "-")));
        fs::create_dir_all(ko.parent().unwrap()).unwrap();
        fs::write(&ko, "").unwrap();
    }

    fn again(&mut self, note: &str, source: Option<&str>, extra: &[(&str, &str)]) -> Output {
        let first = self.install();
        assert!(first.status.success(), "{}", text(&first.stderr));
        self.load_module("hp_wmi", note);
        self.load_module("hp_kbd_rgb", note);
        if let Some(source) = source {
            fs::write(self.root.join("kernel/hp-wmi/hp-wmi.c"), source).unwrap();
        }
        for (key, value) in extra {
            self.env_set(key, value);
        }
        fs::write(&self.log, "").unwrap();
        self.install()
    }
}

impl Drop for Dkms {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn registration_reinstall_and_unsupported_rgb() {
    let harness = Dkms::new();
    let result = harness.install();
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stderr).contains("continuing without RGB"), "{}", text(&result.stderr));
    assert!(!harness.root.join("etc/modules-load.d/hp-kbd-rgb.conf").exists());
    let sources: Vec<_> = fs::read_dir(harness.root.join("usr/src")).unwrap().map(|entry| entry.unwrap().path()).collect();
    assert_eq!(sources.len(), 2);
    for source in sources {
        let conf = source.join("dkms.conf");
        let result = Command::new("/bin/bash")
            .args([
                "-c",
                "kernelver=future-kernel; source \"$1\"; printf '%s\\n' \"$AUTOINSTALL\" \"${MAKE[0]}\" \"$POST_INSTALL\" \"$POST_REMOVE\"",
                "bash",
            ])
            .arg(&conf)
            .output()
            .unwrap();
        assert!(result.status.success(), "{}", text(&result.stderr));
        let stdout = text(&result.stdout);
        assert!(stdout.contains("yes\nmake KDIR="), "{stdout}");
        assert!(stdout.contains("/future-kernel/build"), "{stdout}");
        assert!(stdout.contains("dkms-post-install future-kernel"), "{stdout}");
    }
    let before = fs::read_to_string(&harness.log).unwrap();
    let before_build = before.matches("build -m").count();
    let before_install = before.matches("install -m").count();
    let result = harness.install();
    assert!(result.status.success(), "{}", text(&result.stderr));
    let after = fs::read_to_string(&harness.log).unwrap();
    assert_eq!(after.matches("build -m").count(), before_build);
    assert_eq!(after.matches("install -m").count(), before_install);
    let stdout = text(&result.stdout);
    assert!(stdout.contains("hp-wmi is already installed; loading it without rebuilding"), "{stdout}");
    assert!(stdout.contains("hp-kbd-rgb is unchanged and not in use; skipping rebuild and reload"), "{stdout}");
}

#[test]
fn build_failure_is_not_hidden_as_optional_rgb() {
    let mut harness = Dkms::new();
    harness.env_set("TEST_BUILD_FAIL", "1");
    let result = harness.install();
    assert!(!result.status.success());
    let log = fs::read_to_string(&harness.log).unwrap_or_default();
    assert!(!log.contains("install -m"), "{log}");
}

#[test]
fn unchanged_loaded_module_skips_rebuild_and_reload() {
    let mut harness = Dkms::new();
    let result = harness.again("same-build", None, &[]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stdout).contains("Kernel modules unchanged; skipped rebuild and reload."));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(!log.contains("build -m"), "{log}");
    assert!(!log.contains("install -m"), "{log}");
    assert!(!log.contains("modprobe"), "{log}");
}

#[test]
fn loaded_module_reloads_without_rebuild_when_build_id_differs() {
    let mut harness = Dkms::new();
    let result = harness.again("old-build", None, &[]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stdout).contains("loaded module differs; reloading"));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(!log.contains("build -m"), "{log}");
    assert!(!log.contains("install -m"), "{log}");
    assert!(log.contains("modprobe -r hp-wmi"), "{log}");
    assert!(log.contains("modprobe hp-wmi"), "{log}");
    assert!(log.contains("modprobe -r hp-kbd-rgb"), "{log}");
}

#[test]
fn unreadable_build_id_reloads_without_rebuilding() {
    let mut harness = Dkms::new();
    let result = harness.again("same-build", None, &[("TEST_OBJCOPY_FAIL", "1")]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(!log.contains("build -m"), "{log}");
    assert!(log.contains("modprobe -r hp-wmi"), "{log}");
}

#[test]
fn real_build_ids_match_for_plain_and_compressed_modules() {
    if ["cc", "ld", "objcopy", "xz", "gzip", "zstd"].iter().any(|tool| which(tool).is_none()) {
        eprintln!("requires binutils, compiler, and module compression tools; skipped");
        return;
    }
    let mut harness = Dkms::new();
    assert!(harness.install().status.success());
    let source = harness.root.join("module.c");
    fs::write(&source, "int identity = 1;\n").unwrap();
    let obj = harness.root.join("module.o");
    let ko = harness.root.join("module.ko");
    assert!(Command::new("cc")
        .env("CCACHE_DISABLE", "1")
        .env("CCACHE_TEMPDIR", &harness.root)
        .args(["-c"])
        .arg(&source)
        .arg("-o")
        .arg(&obj)
        .status()
        .unwrap()
        .success());
    assert!(Command::new("ld").args(["-r", "--build-id"]).arg(&obj).arg("-o").arg(&ko).status().unwrap().success());
    let note = harness.root.join("build-id");
    assert!(Command::new("objcopy")
        .arg("--dump-section")
        .arg(format!(".note.gnu.build-id={}", note.display()))
        .arg(&ko)
        .arg("/dev/null")
        .status()
        .unwrap()
        .success());
    fs::remove_file(harness.bin.join("objcopy")).unwrap();
    for tool in ["objcopy", "xz", "gzip", "zstd"] {
        std::os::unix::fs::symlink(which(tool).unwrap(), harness.bin.join(tool)).unwrap();
    }
    let note_bytes = fs::read(&note).unwrap();
    for module in ["hp_wmi", "hp_kbd_rgb"] {
        harness.load_module(module, "");
        fs::write(harness.root.join("sys/module").join(module).join("notes/.note.gnu.build-id"), &note_bytes).unwrap();
    }
    for (suffix, compressor) in [("", None), (".xz", Some("xz")), (".gz", Some("gzip")), (".zst", Some("zstd"))] {
        let data = if let Some(compressor) = compressor {
            Command::new(compressor).arg("-c").arg(&ko).output().unwrap().stdout
        } else {
            fs::read(&ko).unwrap()
        };
        for module in ["hp-wmi", "hp-kbd-rgb"] {
            fs::write(harness.root.join("lib/modules/test-kernel/extra").join(format!("{module}.ko{suffix}")), &data).unwrap();
        }
        harness.env_set("TEST_MODULE_SUFFIX", suffix);
        fs::write(&harness.log, "").unwrap();
        let result = harness.install();
        assert!(result.status.success(), "{suffix} {}", text(&result.stderr));
        assert!(text(&result.stdout).contains("Kernel modules unchanged"), "{suffix} {}", text(&result.stdout));
        assert!(!fs::read_to_string(&harness.log).unwrap().contains("modprobe"), "{suffix}");
    }
}

#[test]
fn missing_required_module_and_autoload_entry_are_restored() {
    let harness = Dkms::new();
    assert!(harness.install().status.success());
    let autoload = harness.root.join("etc/modules-load.d/hp-wmi.conf");
    fs::remove_file(&autoload).unwrap();
    fs::write(&harness.log, "").unwrap();
    let result = harness.install();
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert_eq!(fs::read_to_string(&autoload).unwrap(), "hp-wmi\n");
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(!log.contains("build -m"), "{log}");
    assert!(log.contains("modprobe hp-wmi"), "{log}");
    assert!(!log.contains("modprobe hp-kbd-rgb"), "{log}");
}

#[test]
fn module_path_alias_skips_rebuild_and_reload() {
    let mut harness = Dkms::new();
    let usr = harness.root.join("usr");
    fs::create_dir_all(&usr).unwrap();
    std::os::unix::fs::symlink(harness.root.join("lib"), usr.join("lib")).unwrap();
    let result = harness.again("same-build", None, &[("TEST_MODULE_LIB", "usr/lib")]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(!log.contains("install -m"), "{log}");
    assert!(!log.contains("modprobe"), "{log}");
}

#[test]
fn secure_boot_rebuilds_cached_modules_with_wrong_signer() {
    let mut harness = Dkms::new();
    let result = harness.again("same-build", None, &[("TEST_SECURE_BOOT", "1"), ("TEST_SIGNER", "old-key")]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    let log = fs::read_to_string(&harness.log).unwrap();
    for module in ["hp-wmi", "hp-kbd-rgb"] {
        assert!(
            log.lines().any(|line| line.contains(&format!("build -m victus-hub-{module}")) && line.contains("--force")),
            "{log}"
        );
        assert!(log.contains(&format!("install -m victus-hub-{module}")), "{log}");
    }
}

#[test]
fn secure_boot_pending_enrollment_defers_loading() {
    let mut harness = Dkms::new();
    harness.env_set("TEST_SECURE_BOOT", "1");
    harness.env_set("TEST_MOK_PENDING", "1");
    let result = harness.install();
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stdout).contains("deferred until MOK enrollment"));
    let log = fs::read_to_string(&harness.log).unwrap_or_default();
    assert!(!log.contains("modprobe"), "{log}");
}

#[test]
fn changed_source_rebuilds_and_reloads_only_that_module() {
    let mut harness = Dkms::new();
    let result = harness.again("same-build", Some("/* changed */\n"), &[]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stdout).contains("Kernel module hp-kbd-rgb is unchanged; skipping rebuild and reload."));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(log.contains("build -m victus-hub-hp-wmi"), "{log}");
    assert!(log.contains("install -m victus-hub-hp-wmi"), "{log}");
    assert!(log.contains("modprobe -r hp-wmi"), "{log}");
    assert!(!log.contains("build -m victus-hub-hp-kbd-rgb"), "{log}");
    assert!(!log.contains("install -m victus-hub-hp-kbd-rgb"), "{log}");
    assert!(!log.contains("modprobe hp-kbd-rgb"), "{log}");
    assert!(!log.contains("modprobe -r hp-kbd-rgb"), "{log}");
}

#[test]
fn archived_original_module_still_counts_as_installed() {
    let mut harness = Dkms::new();
    let result = harness.again("same-build", None, &[("TEST_STATUS_SUFFIX", " (Original modules exist)")]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(!log.contains("build -m") && !log.contains("install -m") && !log.contains("modprobe"), "{log}");
}

#[test]
fn stock_module_selection_is_reinstalled() {
    let mut harness = Dkms::new();
    let result = harness.again("same-build", None, &[("TEST_MODINFO_KIND", "stock")]);
    assert!(result.status.success(), "{}", text(&result.stderr));
    assert!(text(&result.stdout).contains("Custom hp-wmi is not the selected module; reinstalling."));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(log.contains("install -m victus-hub-hp-wmi"), "{log}");
    assert!(log.contains("install -m victus-hub-hp-kbd-rgb"), "{log}");
}

#[test]
fn installed_module_mismatch_is_reinstalled() {
    let mut harness = Dkms::new();
    let result = harness.again(
        "same-build",
        None,
        &[("TEST_STATUS_SUFFIX", " (Differences between built and installed modules)")],
    );
    assert!(result.status.success(), "{}", text(&result.stderr));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(!log.contains("build -m"), "{log}");
    assert!(log.contains("install -m victus-hub-hp-wmi"), "{log}");
    assert!(log.contains("install -m victus-hub-hp-kbd-rgb"), "{log}");
    assert!(log.contains("modprobe -r hp-wmi"), "{log}");
}

const CARGO_STUB: &str = r#"
import os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write('cargo ' + ' '.join(args) + '\n')
code = int(os.environ.get('TEST_CARGO_EXIT', '0'))
if code != 0:
    sys.exit(code)
manifest = None
target = None
for index, arg in enumerate(args):
    if arg == '--manifest-path' and index + 1 < len(args):
        manifest = pathlib.Path(args[index + 1])
    if arg == '--target-dir' and index + 1 < len(args):
        target = pathlib.Path(args[index + 1])
if manifest is None:
    sys.exit('cargo stub expected --manifest-path')
release = (target if target is not None else manifest.parent / 'target') / 'release'
release.mkdir(parents=True, exist_ok=True)
for name in ('victus-hub', 'victus-hubd'):
    binary = release / name
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_LOG"\nexit 0\n')
    binary.chmod(0o755)
"#;

struct App {
    root: PathBuf,
    bin: PathBuf,
    log: PathBuf,
    env: Vec<(String, String)>,
}

impl App {
    fn new() -> Self {
        let root = scratch("app");
        let bin = root.join("bin");
        let scripts = root.join("scripts");
        fs::create_dir_all(&scripts).unwrap();
        let text = fs::read_to_string(repo_root().join("scripts/install")).unwrap();
        fs::write(
            scripts.join("install"),
            rewrite(&text, &root, &["/opt/", "/etc/", "/usr/local/", "/usr/share/", "/usr/lib/", "/run/"]),
        )
        .unwrap();
        fs::write(scripts.join("stop-gui"), "# test stub\n").unwrap();
        fs::write(scripts.join("kmod-prompts.sh"), "").unwrap();
        fs::write(scripts.join("preflight.sh"), "preflight() { return \"${TEST_PREFLIGHT_EXIT:-0}\"; }\n").unwrap();
        fs::create_dir_all(root.join("data")).unwrap();
        for name in ["victus-hubd.service", "victus-hub-sleep"] {
            fs::copy(repo_root().join("data").join(name), root.join("data").join(name)).unwrap();
        }
        let log = root.join("log");
        let mut harness = Self { root, bin, log, env: std::env::vars().collect() };
        for (name, body) in [
            ("sudo", "exec \"$@\""),
            ("chown", "exit 0"),
            ("systemctl", "printf \"systemctl %s\\n\" \"$*\" >> \"$TEST_LOG\""),
            ("update-desktop-database", "exit 0"),
        ] {
            harness.command(name, body);
        }
        let python = which("python3").unwrap();
        write_exe(
            &harness.bin.join("python3"),
            &format!(
                "#!{python}\nimport sys\nargs = sys.argv[1:]\nif len(args) == 1 and args[0].endswith('/scripts/stop-gui'):\n    with open(__import__('os').environ['TEST_LOG'], 'a') as log:\n        log.write('stop-gui\\n')\n    sys.exit(0)\nsys.stderr.write('unexpected python3 call: %s\\n' % args)\nsys.exit(1)\n",
                python = python.display()
            ),
        );
        write_exe(&harness.bin.join("cargo"), &format!("#!{}\n{CARGO_STUB}", python.display()));
        let path = format!("{}:{}", harness.bin.display(), std::env::var("PATH").unwrap_or_default());
        harness.env_set("PATH", &path);
        harness.env_set("TEST_LOG", &harness.log.display().to_string());
        harness
    }

    fn command(&self, name: &str, body: &str) {
        bash_body(&self.bin.join(name), body);
    }

    fn env_set(&mut self, key: &str, value: &str) {
        if let Some(slot) = self.env.iter_mut().find(|(name, _)| name == key) {
            slot.1 = value.to_owned();
        } else {
            self.env.push((key.to_owned(), value.to_owned()));
        }
    }

    fn install(&self) -> Output {
        let mut command = Command::new("/bin/bash");
        command.arg(self.root.join("scripts/install")).arg("--app-only");
        command.env_clear();
        for (key, value) in &self.env {
            command.env(key, value);
        }
        command.output().unwrap()
    }
}

impl Drop for App {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn cargo_failure_preserves_current_and_does_not_restart_service() {
    let mut harness = App::new();
    let app = harness.root.join("opt/victus-hub-app");
    let previous = app.join("releases/previous");
    fs::create_dir_all(&previous).unwrap();
    std::os::unix::fs::symlink(&previous, app.join("current")).unwrap();
    harness.env_set("TEST_CARGO_EXIT", "1");
    let result = harness.install();
    assert!(!result.status.success(), "{}", text(&result.stdout));
    assert_eq!(fs::canonicalize(app.join("current")).unwrap(), fs::canonicalize(&previous).unwrap());
    let log = fs::read_to_string(&harness.log).unwrap_or_default();
    assert!(!log.contains("systemctl"), "{log}");
    let releases: Vec<_> = fs::read_dir(app.join("releases")).unwrap().map(|entry| entry.unwrap().path()).collect();
    assert_eq!(releases, vec![previous]);
}

#[test]
fn success_installs_isolated_service_and_launcher() {
    let harness = App::new();
    let result = harness.install();
    assert!(result.status.success(), "{}\n{}", text(&result.stdout), text(&result.stderr));
    let app = harness.root.join("opt/victus-hub-app");
    assert!(app.join("current/bin/victus-hubd").is_file());
    assert!(app.join("current/bin/victus-hub").is_file());
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(log.contains("cargo build --locked --release"), "{log}");
    assert!(log.contains("--manifest-path"), "{log}");
    assert!(log.find("stop-gui").unwrap() < log.find("cargo build").unwrap(), "{log}");
    assert!(log.contains("systemctl restart victus-hubd.service"), "{log}");
    assert!(log.contains("--socket"), "{log}");
    let service = fs::read_to_string(harness.root.join("etc/systemd/system/victus-hubd.service")).unwrap();
    assert!(service.contains("/current/bin/victus-hubd"));
    assert!(!service.contains("python"));
    assert!(service.contains("DeviceAllow=char-nvidia* rw"));
    assert!(!service.contains("PYTHONPATH"));
    assert!(!service.contains("@ROOT_DIR@"));
    let launcher = harness.root.join("usr/local/bin/victus-hub");
    assert!(fs::metadata(&launcher).unwrap().permissions().mode() & 0o111 != 0);
    let launcher_text = fs::read_to_string(&launcher).unwrap();
    assert!(launcher_text.contains("exec env GSK_RENDERER=cairo "), "{launcher_text}");
    assert!(launcher_text.contains("current/bin/victus-hub"), "{launcher_text}");
    assert!(!launcher_text.contains("python"));
    assert!(!launcher_text.contains("GDK_BACKEND"));
    for path in [
        "usr/share/applications/victus-hub.desktop",
        "usr/share/dbus-1/services/io.github.evident0.VictusHub.service",
    ] {
        let text = fs::read_to_string(harness.root.join(path)).unwrap();
        assert!(text.contains("GSK_RENDERER=cairo"), "{path}: {text}");
        assert!(text.contains("VICTUS_HUB_DEBUG_LEVEL=0"), "{path}: {text}");
        assert!(!text.contains("QT_QPA_PLATFORM"), "{path}: {text}");
        assert!(!text.contains("GDK_BACKEND"), "{path}: {text}");
    }
}

#[test]
fn sudo_build_uses_invoking_user_cargo() {
    let mut harness = App::new();
    let home = harness.root.join("home/dev");
    let cargo_bin = home.join(".cargo/bin");
    fs::create_dir_all(&cargo_bin).unwrap();
    fs::remove_file(harness.bin.join("cargo")).unwrap();
    let home_text = home.display().to_string();
    let cargo_text = cargo_bin.display().to_string();
    let cargo_body = r#"case "$PATH" in
  CARGO_BIN:*) ;;
  *) printf 'PATH=%s\n' "$PATH" >&2; exit 1 ;;
esac
if [ "$HOME" != 'HOME_DIR' ]; then printf 'HOME=%s\n' "$HOME" >&2; exit 1; fi
printf 'cargo-path %s\n' "$PATH" >> "$TEST_LOG"
printf 'cargo %s\n' "$*" >> "$TEST_LOG"
target=
prev=
for arg in "$@"; do
  if [ "$prev" = --target-dir ]; then target=$arg; fi
  prev=$arg
done
mkdir -p "$target/release"
for name in victus-hub victus-hubd; do
  printf '%s\n' '#!/bin/sh' 'printf "%s\n" "$*" >> "$TEST_LOG"' 'exit 0' > "$target/release/$name"
  chmod 755 "$target/release/$name"
done
"#
    .replace("CARGO_BIN", &cargo_text)
    .replace("HOME_DIR", &home_text);
    bash_body(&cargo_bin.join("cargo"), &cargo_body);
    bash_body(&harness.bin.join("id"), "printf '0\\n'");
    bash_body(
        &harness.bin.join("getent"),
        &format!("printf 'user:x:%s:%s::{home_text}:/bin/bash\\n' \"$2\" \"$2\""),
    );
    bash_body(
        &harness.bin.join("sudo"),
        "while [ $# -gt 0 ]; do\n  case \"$1\" in\n    --) shift; break ;;\n    -u|--user|-g|--group) shift 2 ;;\n    -*) shift ;;\n    *) break ;;\n  esac\ndone\nexec \"$@\"\n",
    );
    for name in [
        "mktemp", "install", "chmod", "rm", "ln", "mv", "tee", "cmp", "seq", "readlink", "dirname", "find", "sort",
        "cut", "env", "mkdir",
    ] {
        let dest = harness.bin.join(name);
        if !dest.exists() {
            std::os::unix::fs::symlink(which(name).unwrap(), &dest).unwrap();
        }
    }
    harness.env_set("PATH", &harness.bin.display().to_string());
    harness.env_set("SUDO_UID", "4242");
    harness.env_set("SUDO_GID", "4242");
    let result = harness.install();
    assert!(result.status.success(), "{}\n{}", text(&result.stdout), text(&result.stderr));
    let log = fs::read_to_string(&harness.log).unwrap();
    assert!(log.contains("cargo build --locked --release"), "{log}");
    assert!(log.contains(&cargo_text), "{log}");
}

fn seed_old_releases(root: &Path) -> PathBuf {
    let releases = root.join("opt/victus-hub-app/releases");
    let older = releases.join("older");
    let old = releases.join("old");
    fs::create_dir_all(&older).unwrap();
    fs::create_dir_all(&old).unwrap();
    File::open(&older).unwrap().set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(1)).unwrap();
    File::open(&old).unwrap().set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(2)).unwrap();
    releases
}

#[test]
fn default_keeps_only_the_release_current_points_at() {
    let harness = App::new();
    let releases = seed_old_releases(&harness.root);
    let result = harness.install();
    assert!(result.status.success(), "{}\n{}", text(&result.stdout), text(&result.stderr));
    let current = fs::canonicalize(releases.parent().unwrap().join("current")).unwrap();
    let names: Vec<_> = fs::read_dir(&releases)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(names.len(), 1, "{names:?}");
    assert_eq!(names[0], current.file_name().unwrap().to_string_lossy());
    assert!(!names.contains(&"old".to_owned()));
    assert!(!names.contains(&"older".to_owned()));
}

#[test]
fn keep_releases_leaves_the_newest_previous_install() {
    let mut harness = App::new();
    let releases = seed_old_releases(&harness.root);
    harness.env_set("VICTUS_HUB_KEEP_RELEASES", "2");
    let result = harness.install();
    assert!(result.status.success(), "{}\n{}", text(&result.stdout), text(&result.stderr));
    let current = fs::canonicalize(releases.parent().unwrap().join("current")).unwrap();
    let mut names: Vec<_> = fs::read_dir(&releases)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    let mut expected = vec!["old".to_owned(), current.file_name().unwrap().to_string_lossy().into_owned()];
    expected.sort();
    assert_eq!(names, expected);
}

#[test]
fn preflight_failure_makes_no_installation_changes() {
    let mut harness = App::new();
    harness.env_set("TEST_PREFLIGHT_EXIT", "1");
    let result = harness.install();
    assert!(!result.status.success());
    assert!(!harness.root.join("opt").exists());
    assert!(!harness.log.exists());
}

#[test]
fn icons_prefer_magick_and_support_legacy_convert() {
    let mut harness = App::new();
    for name in [
        "dirname", "id", "install", "mktemp", "chmod", "rm", "ln", "mv", "tee", "cp", "cmp", "seq", "readlink", "touch",
        "find", "sort", "cut",
    ] {
        let dest = harness.bin.join(name);
        if !dest.exists() {
            std::os::unix::fs::symlink(which(name).unwrap(), &dest).unwrap();
        }
    }
    harness.env_set("PATH", &harness.bin.display().to_string());
    let icon = harness.root.join("crates/victus-hub/assets/icons/logoV.png");
    fs::create_dir_all(icon.parent().unwrap()).unwrap();
    fs::write(&icon, "").unwrap();
    for name in ["magick", "convert"] {
        bash_body(
            &harness.bin.join(name),
            &format!("printf \"{name} %s\\n\" \"$*\" >> \"$TEST_LOG\"\noutput=${{@: -1}}; touch \"${{output#png32:}}\""),
        );
    }
    bash_body(&harness.bin.join("gtk-update-icon-cache"), "exit 0");
    for converter in ["magick", "convert"] {
        fs::write(&harness.log, "").unwrap();
        let result = harness.install();
        assert!(result.status.success(), "{converter} {}\n{}", text(&result.stdout), text(&result.stderr));
        let lines: Vec<_> = fs::read_to_string(&harness.log).unwrap().lines().map(str::to_owned).collect();
        assert_eq!(lines.iter().filter(|line| line.starts_with(&format!("{converter} "))).count(), 10, "{lines:?}");
        if converter == "magick" {
            assert!(lines.iter().all(|line| !line.starts_with("convert ")));
            fs::remove_file(harness.bin.join("magick")).unwrap();
        }
        for size in [16, 22, 24, 32, 48, 64, 128, 256, 512, 1024] {
            assert!(harness.root.join(format!("usr/share/icons/hicolor/{size}x{size}/apps/victus-hub.png")).exists());
        }
    }
}
