//! Root daemon entry point. This is the only place that opens the installed
//! socket, sysfs, loginctl, or keyboard devices.

use std::env;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use victus_core::{debug_level_from_value, state_path_from_env, terminal_line_visible, transact, DEFAULT_SOCKET};
use victus_hubd::{discover_keyboards, serve, Peer, Runtime, SysPlatform};

fn main() {
    let level = env::var("VICTUS_HUB_DEBUG_LEVEL").ok().and_then(|value| debug_level_from_value(&value).ok()).unwrap_or(0);
    let logger = Box::leak(Box::new(HubLogger(level)));
    let _ = log::set_logger(logger);
    log::set_max_level(log::LevelFilter::Info);

    let args = Args::parse();
    if let Some(command) = args.client {
        match transact(&args.socket, &command, Duration::from_secs(5)) {
            Ok(reply) => print!("{reply}"),
            Err(error) => {
                eprintln!("{error}");
                std::process::exit(1);
            }
        }
        return;
    }

    let platform = SysPlatform::installed();
    let runtime = Arc::new(Mutex::new(Runtime::new(args.state, &args.shortcuts, platform)));
    {
        let mut guard = runtime.lock().expect("runtime lock");
        guard.refresh_host(true);
        guard.apply_fan_mode();
    }
    let stop = Arc::new(AtomicBool::new(false));
    let last_input = Arc::new(AtomicU64::new(0));
    install_signals(Arc::clone(&stop));
    spawn_loops(Arc::clone(&runtime), Arc::clone(&stop), Arc::clone(&last_input));
    spawn_keyboard(Arc::clone(&runtime), Arc::clone(&stop), last_input);
    eprintln!("victus-hubd listening on {}", args.socket.display());
    let server_runtime = Arc::clone(&runtime);
    if let Err(error) = serve(&args.socket, server_runtime, authenticate, Arc::clone(&stop)) {
        eprintln!("{error}");
        std::process::exit(1);
    }
    runtime.lock().expect("runtime lock").prepare_sleep();
}

struct Args {
    socket: PathBuf,
    client: Option<String>,
    state: PathBuf,
    shortcuts: PathBuf,
}

impl Args {
    fn parse() -> Self {
        let mut socket = PathBuf::from(DEFAULT_SOCKET);
        let mut client = None;
        let mut state = state_path_from_env(env::var("VICTUS_HUBD_STATE").ok().as_deref());
        let mut shortcuts = PathBuf::from("/var/lib/victus-hubd/program-shortcuts.json");
        let mut iter = env::args().skip(1);
        while let Some(arg) = iter.next() {
            match arg.as_str() {
                "--socket" => socket = PathBuf::from(iter.next().expect("--socket needs a path")),
                "--client" => {
                    let mut command = iter.next().expect("--client needs a command");
                    let rest: Vec<String> = iter.by_ref().collect();
                    if !rest.is_empty() {
                        command.push('\t');
                        command.push_str(&rest.join("\t"));
                    }
                    client = Some(command);
                }
                "--state" => state = PathBuf::from(iter.next().expect("--state needs a path")),
                "--shortcuts" => shortcuts = PathBuf::from(iter.next().expect("--shortcuts needs a path")),
                "--help" | "-h" => {
                    println!("victus-hubd [--socket PATH] [--state PATH] [--shortcuts PATH] [--client COMMAND]");
                    std::process::exit(0);
                }
                other => eprintln!("unknown argument {other}"),
            }
        }
        Self { socket, client, state, shortcuts }
    }
}

struct HubLogger(i32);

impl log::Log for HubLogger {
    fn enabled(&self, metadata: &log::Metadata<'_>) -> bool {
        metadata.level() <= log::Level::Error || self.0 > 0
    }
    fn log(&self, record: &log::Record<'_>) {
        let text = record.args().to_string();
        let error = record.level() <= log::Level::Error;
        if error || terminal_line_visible(&text, self.0, false) {
            eprintln!("{text}");
        }
    }
    fn flush(&self) {}
}

fn authenticate(uid: u32, pid: i32) -> Peer {
    victus_hubd::authorize(uid, pid, &systemd_session(pid, uid), loginctl_session)
}

fn loginctl_session(session: &str) -> Option<victus_hubd::SessionInfo> {
    if session.is_empty() || session.chars().any(|ch| ch == '/' || ch.is_whitespace()) {
        return None;
    }
    let output = Command::new("/usr/bin/loginctl")
        .args(["show-session", session, "--no-pager", "-p", "User", "-p", "Active", "-p", "Remote", "-p", "LockedHint", "-p", "Type", "-p", "Seat"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    victus_hubd::parse_loginctl(&String::from_utf8_lossy(&output.stdout))
}

fn systemd_session(pid: i32, uid: u32) -> String {
    if uid == 0 || pid <= 0 {
        return String::new();
    }
    // SAFETY: libsystemd's session getters write a malloc'd C string, which we copy and free.
    unsafe {
        let Ok(library) = libloading::Library::new("libsystemd.so.0") else { return String::new() };
        unsafe extern "C" {
            fn free(ptr: *mut std::ffi::c_void);
        }
        type GetPid = unsafe extern "C" fn(i32, *mut *mut i8) -> i32;
        type GetUid = unsafe extern "C" fn(u32, *mut *mut i8) -> i32;
        let mut value = std::ptr::null_mut();
        if let Ok(get_pid) = library.get::<GetPid>(b"sd_pid_get_session") {
            if get_pid(pid, &mut value) >= 0 {
                if let Some(text) = take_c_string(value) {
                    free(value.cast());
                    if !text.is_empty() {
                        return text;
                    }
                }
            }
        }
        value = std::ptr::null_mut();
        if let Ok(get_uid) = library.get::<GetUid>(b"sd_uid_get_display") {
            if get_uid(uid, &mut value) >= 0 {
                if let Some(text) = take_c_string(value) {
                    free(value.cast());
                    return text;
                }
            }
        }
        String::new()
    }
}

/// SAFETY: `ptr` is either null or a C string allocated by libsystemd.
unsafe fn take_c_string(ptr: *mut i8) -> Option<String> {
    if ptr.is_null() {
        return None;
    }
    // SAFETY: caller guarantees a valid C string.
    Some(unsafe { std::ffi::CStr::from_ptr(ptr) }.to_string_lossy().into_owned())
}

fn spawn_loops(runtime: Arc<Mutex<Runtime<SysPlatform>>>, stop: Arc<AtomicBool>, last_input: Arc<AtomicU64>) {
    let fan_runtime = Arc::clone(&runtime);
    let fan_stop = Arc::clone(&stop);
    thread::spawn(move || {
        while !fan_stop.load(Ordering::Relaxed) {
            fan_runtime.lock().expect("runtime lock").fan_poll(mono());
            thread::sleep(Duration::from_secs(1));
        }
    });
    let light_runtime = Arc::clone(&runtime);
    let light_stop = Arc::clone(&stop);
    thread::spawn(move || {
        while !light_stop.load(Ordering::Relaxed) {
            let mut guard = light_runtime.lock().expect("runtime lock");
            let idle = idle_seconds(&last_input);
            guard.platform.set_idle_elapsed(idle);
            let interval = guard.lighting_interval();
            guard.lighting_tick(mono());
            drop(guard);
            thread::sleep(Duration::from_secs_f64(interval));
        }
    });
    let power_runtime = Arc::clone(&runtime);
    let power_stop = Arc::clone(&stop);
    thread::spawn(move || {
        while !power_stop.load(Ordering::Relaxed) {
            power_runtime.lock().expect("runtime lock").maybe_apply_power(mono());
            thread::sleep(Duration::from_secs(1));
        }
    });
    thread::spawn(move || {
        while !stop.load(Ordering::Relaxed) {
            runtime.lock().expect("runtime lock").refresh_host(false);
            thread::sleep(Duration::from_secs(2));
        }
    });
}

fn spawn_keyboard(runtime: Arc<Mutex<Runtime<SysPlatform>>>, stop: Arc<AtomicBool>, last_input: Arc<AtomicU64>) {
    thread::spawn(move || {
        let mut held = Vec::<i32>::new();
        while !stop.load(Ordering::Relaxed) {
            let devices = discover_keyboards(Path::new("/sys/class/input"), Path::new("/dev/input/by-path"), Path::new("/dev/input"));
            if devices.is_empty() {
                thread::sleep(Duration::from_secs(5));
                continue;
            }
            let mut files = Vec::new();
            for path in devices {
                if let Ok(file) = File::open(&path) {
                    set_nonblocking(&file);
                    files.push(file);
                }
            }
            if files.is_empty() {
                thread::sleep(Duration::from_secs(5));
                continue;
            }
            while !stop.load(Ordering::Relaxed) {
                let mut progress = false;
                for file in &mut files {
                    let mut buf = [0_u8; 24];
                    match file.read(&mut buf) {
                        Ok(24) => {
                            progress = true;
                            let kind = u16::from_ne_bytes([buf[16], buf[17]]);
                            let code = i32::from(u16::from_ne_bytes([buf[18], buf[19]]));
                            let value = i32::from_ne_bytes([buf[20], buf[21], buf[22], buf[23]]);
                            if kind != 1 {
                                continue;
                            }
                            if value == 1 {
                                last_input.store(now_millis(), Ordering::Relaxed);
                                if victus_core::is_modifier(code) {
                                    if !held.contains(&code) {
                                        held.push(code);
                                    }
                                } else {
                                    held.sort_unstable();
                                    let mut guard = runtime.lock().expect("runtime lock");
                                    guard.handle_key(&held, code);
                                    if let Some(press) = guard.take_activation() {
                                        drop(guard);
                                        let runtime = Arc::clone(&runtime);
                                        thread::spawn(move || activate_program_shortcut(&runtime, &press.0, press.1));
                                    }
                                }
                            } else if value == 0 && victus_core::is_modifier(code) {
                                held.retain(|item| *item != code);
                            }
                        }
                        Ok(0) => progress = false,
                        _ => {}
                    }
                }
                if !progress {
                    thread::sleep(Duration::from_millis(20));
                }
            }
        }
    });
}

fn activate_program_shortcut(runtime: &Arc<Mutex<Runtime<SysPlatform>>>, mods: &[i32], key: i32) {
    let Some(peer) = active_desktop_peer() else { return };
    let matches = runtime.lock().expect("runtime lock").shortcuts.get(peer.uid).is_some_and(|(bound, bound_key)| bound.as_slice() == mods && *bound_key == key);
    if !matches || peer.uid == 0 {
        return;
    }
    let bus = format!("/run/user/{}/bus", peer.uid);
    if !Path::new(&bus).exists() {
        return;
    }
    let _ = Command::new("/usr/bin/gdbus")
        .args(["call", "--address", &format!("unix:path={bus}"), "--dest", "io.github.evident0.VictusHub", "--object-path", "/io/github/evident0/VictusHub", "--method", "org.freedesktop.Application.Activate", "{}"])
        .env("XDG_RUNTIME_DIR", format!("/run/user/{}", peer.uid))
        .env("DBUS_SESSION_BUS_ADDRESS", format!("unix:path={bus}"))
        .output();
}

fn active_desktop_peer() -> Option<Peer> {
    let output = Command::new("/usr/bin/loginctl").args(["show-seat", "seat0", "-p", "ActiveSession", "--no-pager"]).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let session = text.lines().find_map(|line| line.strip_prefix("ActiveSession="))?.trim();
    let info = loginctl_session(session)?;
    let peer = Peer { uid: info.user, pid: 0, authorized: victus_hubd::session_ok(info.user, &info) };
    peer.authorized.then_some(peer)
}

fn install_signals(stop: Arc<AtomicBool>) {
    extern "C" fn handle(_: i32) {
        STOP.store(true, Ordering::Relaxed);
    }
    unsafe {
        let handler = nix::sys::signal::SigHandler::Handler(handle);
        let _ = nix::sys::signal::signal(nix::sys::signal::Signal::SIGTERM, handler);
        let _ = nix::sys::signal::signal(nix::sys::signal::Signal::SIGINT, handler);
    }
    thread::spawn(move || {
        while !stop.load(Ordering::Relaxed) {
            if STOP.load(Ordering::Relaxed) {
                stop.store(true, Ordering::Relaxed);
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
    });
}

static STOP: AtomicBool = AtomicBool::new(false);

fn set_nonblocking(file: &File) {
    use std::os::fd::AsRawFd;
    let fd = file.as_raw_fd();
    let current = nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_GETFL).unwrap_or(0);
    let mut flags = nix::fcntl::OFlag::from_bits_truncate(current);
    flags.insert(nix::fcntl::OFlag::O_NONBLOCK);
    let _ = nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_SETFL(flags));
}

fn idle_seconds(last_input: &AtomicU64) -> f64 {
    let last = last_input.load(Ordering::Relaxed);
    if last == 0 {
        -1.0
    } else {
        now_millis().saturating_sub(last) as f64 / 1000.0
    }
}

fn now_millis() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|duration| duration.as_millis() as u64).unwrap_or(0)
}

fn mono() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}


