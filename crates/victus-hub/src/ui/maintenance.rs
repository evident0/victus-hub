//! Update checks and diagnostic reports, with their background completion UI.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::rc::Rc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use gtk4::prelude::*;
use libadwaita::prelude::*;
use victus_core::{acpi_error_line, filter_journal_lines, kernel_module_error_line, PROGRAM_VERSION};

use crate::{parse_release_tag, update_choice, update_shell, UpdateChoice, RELEASE_URL};
use super::{gpu_name, quit, BackgroundEvent, Session};

pub(super) enum Release {
    Failed,
    Current,
    Available(String),
}

pub(super) fn check_updates(session: &Session) {
    session.built.settings.update.set_sensitive(false);
    session.built.settings.update_status.set_text("Checking for updates…");
    let tx = session.events_tx.clone();
    let _ = std::thread::Builder::new().name("victus-update".into()).spawn(move || {
        tx.send(BackgroundEvent::Release(fetch_release()));
    });
}

pub(super) fn show_release(session: &Rc<Session>, release: Release) {
    session.built.settings.update.set_sensitive(true);
    match release {
        Release::Failed => session.built.settings.update_status.set_text("Could not check for updates."),
        Release::Current => session.built.settings.update_status.set_text("Program is up to date."),
        Release::Available(tag) => {
            session.built.settings.update_status.set_text(&format!("Release found: {tag}"));
            confirm_update(session, &tag);
        }
    }
}

fn confirm_update(session: &Rc<Session>, tag: &str) {
    let body = format!("Install Victus Hub {tag}?\n\nThe app will close during installation and reopen when it finishes.");
    let dialog = libadwaita::AlertDialog::new(Some("Update available"), Some(&body));
    dialog.add_response("cancel", "Cancel");
    dialog.add_response("update", "Update");
    dialog.set_response_appearance("update", libadwaita::ResponseAppearance::Suggested);
    dialog.set_default_response(Some("cancel"));
    dialog.set_close_response("cancel");
    let weak = Rc::downgrade(session);
    dialog.connect_response(None, move |_, response| {
        if response != "update" { return; }
        let Some(session) = weak.upgrade() else { return };
        match launch_update() {
            Ok(()) => quit(&session),
            Err(message) => session.built.settings.update_status.set_text(message),
        }
    });
    dialog.present(Some(&session.window));
}

fn fetch_release() -> Release {
    let Some(curl) = which("curl") else { return Release::Failed };
    let Ok(output) = Command::new(curl)
        .args(["-fsSL", "--max-time", "10", "-H", "Accept: application/vnd.github+json", "-H", "User-Agent: victus-hub", RELEASE_URL])
        .output()
    else { return Release::Failed };
    if !output.status.success() { return Release::Failed; }
    let Ok(body) = String::from_utf8(output.stdout) else { return Release::Failed };
    let Some(tag) = parse_release_tag(&body) else { return Release::Failed };
    match update_choice(&tag, PROGRAM_VERSION) {
        UpdateChoice::Available => Release::Available(tag),
        UpdateChoice::Current => Release::Current,
        UpdateChoice::Invalid => Release::Failed,
    }
}

fn launch_update() -> Result<(), &'static str> {
    if which("curl").is_none() || which("sudo").is_none() {
        return Err("Updating requires curl and sudo.");
    }
    let shell = update_shell();
    let terminals: &[(&str, &[&str])] = &[
        ("gnome-terminal", &["--"]),
        ("konsole", &["--separate", "--hold", "-e"]),
        ("ptyxis", &["--new-window", "--"]),
        ("kgx", &["--"]),
        ("xfce4-terminal", &["--disable-server", "-x"]),
        ("mate-terminal", &["-x"]),
        ("x-terminal-emulator", &["-e"]),
        ("xterm", &["-hold", "-T", "Victus Hub Update", "-e"]),
    ];
    for (name, args) in terminals {
        let Some(program) = which(name) else { continue };
        let mut command = Command::new(program);
        command.env_remove("FONTCONFIG_FILE").env_remove("VICTUS_HUB_FONT_DIR");
        command.args(*args).arg("/bin/bash").arg("-c").arg(&shell);
        if command.spawn().is_ok() { return Ok(()); }
    }
    Err("No terminal emulator found.")
}

pub(super) fn collect_diagnostics(session: &Session) {
    session.built.settings.diagnostics.set_sensitive(false);
    let tx = session.events_tx.clone();
    let state = victus_core::state_to_value(&session.model.borrow().state);
    let logs = session.log.borrow().iter().cloned().collect::<Vec<_>>();
    let _ = std::thread::Builder::new().name("victus-diagnostics".into()).spawn(move || {
        tx.send(BackgroundEvent::Diagnostics(write_diagnostics(&state, &logs)));
    });
}

pub(super) fn show_diagnostics(session: &Rc<Session>, result: Result<PathBuf, String>) {
    session.built.settings.diagnostics.set_sensitive(true);
    let path = match result {
        Ok(path) => path,
        Err(error) => {
            let dialog = libadwaita::AlertDialog::new(Some("Diagnostics"), Some(&format!("Could not save diagnostics:\n{error}")));
            dialog.add_response("ok", "OK");
            dialog.set_close_response("ok");
            dialog.present(Some(&session.window));
            return;
        }
    };
    let body = format!("Report saved to:\n{}", path.display());
    let dialog = libadwaita::AlertDialog::new(Some("Diagnostics"), Some(&body));
    dialog.add_response("open", "Open");
    dialog.add_response("ok", "OK");
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("ok");
    dialog.connect_response(None, move |_, response| {
        if response == "open" && let Some(program) = which("xdg-open") {
            let _ = Command::new(program).arg(&path).spawn();
        }
    });
    dialog.present(Some(&session.window));
}

fn write_diagnostics(state: &serde_json::Value, logs: &[String]) -> Result<PathBuf, String> {
    let kernel = command_output("journalctl", &["-k", "-n", "8000", "--no-pager", "-o", "short-iso"]);
    let daemon = command_output("journalctl", &["-u", "victus-hubd", "-n", "400", "--no-pager", "-o", "short-iso"]);
    let modules = filter_journal_lines(kernel.as_deref(), kernel_module_error_line, 80);
    let acpi = filter_journal_lines(kernel.as_deref(), acpi_error_line, 80);
    let level = std::env::var("VICTUS_HUB_DEBUG_LEVEL").ok().and_then(|text| victus_core::debug_level_from_value(&text).ok()).unwrap_or(0);
    let daemon = daemon.as_deref().and_then(|text| victus_core::filter_daemon_journal(text, level, 80));
    let read = |path: &str| std::fs::read_to_string(path).map_or_else(|_| "unknown".into(), |text| text.trim().to_owned());
    let mut system = vec![("Program version".to_owned(), PROGRAM_VERSION.to_owned()), ("Kernel".into(), read("/proc/sys/kernel/osrelease")),
        ("OS".into(), read("/etc/os-release")), ("CPU".into(), read("/proc/cpuinfo").lines().find_map(|line| line.strip_prefix("model name").and_then(|text| text.split_once(':').map(|(_, value)| value.trim().to_owned()))).unwrap_or_else(|| "unknown".into())),
        ("GPU".into(), gpu_name()), ("Daemon policy".into(), state.to_string())];
    for (name, attribute) in [("Vendor", "sys_vendor"), ("Model", "product_name"), ("SKU", "product_sku"), ("Board", "board_name"), ("BIOS", "bios_version"), ("BIOS date", "bios_date")] {
        system.push((name.into(), read(&format!("/sys/class/dmi/id/{attribute}"))));
    }
    let caps = victus_hw::detect_capabilities(Path::new("/sys/class/hwmon"), Path::new("/sys/class/leds"), Path::new("/sys/devices/platform/hp-wmi"));
    let capabilities = vec![
        victus_core::Capability { name: "Fan control".into(), available: caps.fan_modes.len() > 1, details: caps.fan_modes.join(", ") },
        victus_core::Capability { name: "Keyboard RGB".into(), available: caps.keyboard_lighting, details: format!("{} zones", victus_hw::keyboard_zone_count(Path::new("/sys/devices/platform/hp-kbd-rgb"), None)) },
        victus_core::Capability { name: "GPU MUX".into(), available: caps.gpu_mux.is_some(), details: caps.gpu_mux.map(|mux| format!("current {}; {}", mux.current_index, mux.modes.iter().map(|mode| mode.name.clone()).collect::<Vec<_>>().join(", "))).unwrap_or_default() },
        victus_core::Capability { name: "CPU frequency".into(), available: victus_hw::read_policies(Path::new("/sys/devices/system/cpu/cpufreq")).is_ok(), details: "/sys/devices/system/cpu/cpufreq".into() },
        victus_core::Capability { name: "Daemon socket".into(), available: Path::new(victus_core::DEFAULT_SOCKET).exists(), details: victus_core::DEFAULT_SOCKET.into() },
    ];
    let kernel_modules = ["hp_wmi", "hp_kbd_rgb", "nvidia", "amdgpu"].map(|name| (name, Path::new("/sys/module").join(name).is_dir()));
    let rows = system.iter().map(|(key, value)| (key.as_str(), value.as_str())).collect::<Vec<_>>();
    let generated = command_output("date", &["--iso-8601=seconds"]).unwrap_or_else(|| "unknown".into());
    let report = victus_core::render_markdown(generated.trim(), &rows, &capabilities, &kernel_modules, logs, daemon.as_deref(), modules.as_deref(), acpi.as_deref());
    let path = report_path()?;
    std::fs::write(&path, report).map_err(|error| format!("{}: {error}", path.display()))?;
    Ok(path)
}

fn report_path() -> Result<PathBuf, String> {
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |elapsed| elapsed.as_secs());
    let name = format!("victus-hub-diagnostics-{stamp}.md");
    let folder = command_output("xdg-user-dir", &["DOCUMENTS"]).map(|path| PathBuf::from(path.trim()))
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join("Documents")))
        .unwrap_or_else(std::env::temp_dir);
    std::fs::create_dir_all(&folder).map_err(|error| format!("{}: {error}", folder.display()))?;
    Ok(folder.join(name))
}

fn command_output(name: &str, args: &[&str]) -> Option<String> {
    let program = which(name)?;
    let args = args.iter().map(|arg| (*arg).to_owned()).collect::<Vec<_>>();
    let output = victus_hw::run_command_timeout(&program, &args, Duration::from_secs(5)).ok()?;
    (output.status == 0).then_some(output.stdout).filter(|text| !text.is_empty())
}

fn which(name: &str) -> Option<PathBuf> {
    ["/usr/bin", "/bin", "/usr/local/bin"].into_iter().map(|dir| Path::new(dir).join(name)).find(|path| path.is_file())
}
