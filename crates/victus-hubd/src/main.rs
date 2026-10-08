//! Root daemon entry point. This is the only place that opens the installed
//! socket, sysfs, loginctl, or keyboard devices.

use std::env;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use victus_core::{debug_level_from_value, state_path_from_env, terminal_line_visible, transact, DEFAULT_SOCKET};
use victus_hubd::{discover_keyboards, serve_with_workers, Peer, Platform, Runtime, SysPlatform};
use gio::prelude::*;

fn main() {
    let level = env::var("VICTUS_HUB_DEBUG_LEVEL").ok().and_then(|value| debug_level_from_value(&value).ok()).unwrap_or(0);
    let logger = Box::leak(Box::new(HubLogger(level)));
    let _ = log::set_logger(logger);
    log::set_max_level(log::LevelFilter::Info);

    let args = Args::parse();
    if let Some(command) = args.client {
        match transact(&args.socket, &command, Duration::from_secs(5)) {
            Ok(reply) => { print!("{reply}"); if reply.starts_with("ERR\t") { std::process::exit(1); } },
            Err(error) => {
                eprintln!("{error}");
                std::process::exit(1);
            }
        }
        return;
    }

    let signals = blocked_signals();
    let gpu = victus_hubd::gpu::GpuMonitor::start();
    let profile = Arc::new(AtomicI32::new(-1));
    let platform = SysPlatform::installed().with_caches(Arc::clone(&gpu), Arc::clone(&profile));
    let sampler = Arc::new(Mutex::new(SysPlatform::installed().with_caches(Arc::clone(&gpu), Arc::clone(&profile))));
    let commands = Arc::new(Mutex::new(SysPlatform::installed().with_caches(Arc::clone(&gpu), Arc::clone(&profile))));
    if let Some(index) = victus_hubd::system::read_system_profile(Path::new("/etc/tuned/active_profile")) {
        profile.store(index, Ordering::Relaxed);
    }
    let runtime = Arc::new(Mutex::new(Runtime::new(args.state, &args.shortcuts, platform)));
    {
        let mut guard = runtime.lock().expect("runtime lock");
        guard.refresh_host(true);
        guard.apply_fan_mode();
    }
    if let Some((minimum, maximum)) = runtime.lock().expect("runtime lock").snapshot().cpu_frequency {
        if let Err(error) = commands.lock().expect("command worker").cpu_frequency(minimum, maximum) {
            log::error!("startup: cpu frequency restore failed: {error}");
        }
    }
    let stop = Arc::new(AtomicBool::new(false));
    let last_input = Arc::new(AtomicU64::new(0));
    let keyboard_active = Arc::new(AtomicBool::new(false));
    let signal_stop = Arc::clone(&stop);
    let signal_gpu = Arc::clone(&gpu);
    thread::spawn(move || { let _ = signals.wait(); signal_stop.store(true, Ordering::Relaxed); signal_gpu.stop(); });
    spawn_profile_worker(Arc::clone(&runtime), Arc::clone(&commands));
    spawn_loops(Arc::clone(&runtime), Arc::clone(&commands), Arc::clone(&gpu), Arc::clone(&stop), Arc::clone(&last_input), Arc::clone(&keyboard_active));
    spawn_host_watch(Arc::clone(&runtime), profile, Arc::clone(&stop));
    spawn_keyboard(Arc::clone(&runtime), Arc::clone(&stop), last_input, keyboard_active);
    eprintln!("victus-hubd listening on {}", args.socket.display());
    let server_runtime = Arc::clone(&runtime);
    if let Err(error) = serve_with_workers(&args.socket, server_runtime, sampler, commands, authenticate, Arc::clone(&stop)) {
        eprintln!("{error}");
        std::process::exit(1);
    }
    runtime.lock().expect("runtime lock").prepare_sleep();
    gpu.stop();
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
    let args = ["show-session", session, "--no-pager", "-p", "User", "-p", "Active", "-p", "Remote", "-p", "LockedHint", "-p", "Type", "-p", "Seat"].map(str::to_owned);
    let output = victus_hw::run_command_timeout(Path::new("/usr/bin/loginctl"), &args, Duration::from_secs(1))
        .ok()?;
    if output.status != 0 {
        return None;
    }
    victus_hubd::parse_loginctl(&output.stdout)
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

fn spawn_loops(runtime: Arc<Mutex<Runtime<SysPlatform>>>, commands: Arc<Mutex<SysPlatform>>, gpu: Arc<victus_hubd::gpu::GpuMonitor>, stop: Arc<AtomicBool>, last_input: Arc<AtomicU64>, keyboard_active: Arc<AtomicBool>) {
    let fan_runtime = Arc::clone(&runtime);
    let fan_stop = Arc::clone(&stop);
    thread::spawn(move || {
        while !fan_stop.load(Ordering::Relaxed) {
            let policy = {
                let guard = fan_runtime.lock().expect("runtime lock");
                (guard.fan_needs_gpu_temp(), guard.nvidia_queries_disabled(), guard.is_suspended())
            };
            gpu.policy(policy.0, policy.1, policy.2);
            fan_runtime.lock().expect("runtime lock").fan_poll(mono());
            thread::sleep(Duration::from_secs(1));
        }
    });
    let light_runtime = Arc::clone(&runtime);
    let light_stop = Arc::clone(&stop);
    thread::spawn(move || {
        while !light_stop.load(Ordering::Relaxed) {
            let mut guard = light_runtime.lock().expect("runtime lock");
            let idle = idle_seconds(&last_input, &keyboard_active);
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
            let plan = power_runtime.lock().expect("runtime lock").power_plan(mono());
            if let Some((policy, frequency)) = plan {
                let mut worker = commands.lock().expect("command worker");
                let current = power_runtime.lock().expect("runtime lock");
                let valid = !current.is_suspended() && current.snapshot().power == policy;
                drop(current);
                if valid {
                    if let Err(error) = victus_hubd::apply_power_policy(&mut *worker, &policy) { log::error!("apply power limits failed: {error}"); }
                    if let Some((minimum, maximum)) = frequency {
                        if let Err(error) = worker.cpu_frequency(minimum, maximum) { log::error!("cpu frequency apply failed: {error}"); }
                    }
                }
            }
            thread::sleep(Duration::from_secs(1));
        }
    });
}

fn spawn_profile_worker(runtime: Arc<Mutex<Runtime<SysPlatform>>>, commands: Arc<Mutex<SysPlatform>>) {
    let (tx, rx) = std::sync::mpsc::sync_channel(16);
    runtime.lock().expect("runtime lock").set_profile_sender(tx);
    thread::spawn(move || {
        while let Ok(request) = rx.recv() {
            let mut worker = commands.lock().expect("command worker");
            let index = match request {
                victus_hubd::ProfileRequest::Select(index) => index,
                victus_hubd::ProfileRequest::Cycle => (runtime.lock().expect("runtime lock").current_profile().unwrap_or(1) + 1) % 3,
            };
            match worker.apply_profile(index) {
                Ok(_) => runtime.lock().expect("runtime lock").complete_profile(index),
                Err(error) => log::error!("profile apply failed: {error}"),
            }
        }
    });
}

fn spawn_host_watch(runtime: Arc<Mutex<Runtime<SysPlatform>>>, cache: Arc<AtomicI32>, stop: Arc<AtomicBool>) {
    thread::spawn(move || {
        let context = gio::glib::MainContext::new();
        let _ = context.with_thread_default(|| {
            let refresh: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
                if let Some(index) = victus_hubd::system::read_system_profile(Path::new("/etc/tuned/active_profile")) { cache.store(index, Ordering::Relaxed); }
                runtime.lock().expect("runtime lock").refresh_host(false);
            });
            let mut monitors = Vec::new();
            for path in ["/etc/tuned", "/sys/class/power_supply"] {
                if let Ok(monitor) = gio::File::for_path(path).monitor_directory(gio::FileMonitorFlags::NONE, None::<&gio::Cancellable>) {
                    let refresh = Arc::clone(&refresh);
                    monitor.connect_changed(move |_, _, _, _| refresh());
                    monitors.push(monitor);
                }
            }
            let bus = gio::bus_get_sync(gio::BusType::System, None::<&gio::Cancellable>).ok();
            let mut subscriptions = Vec::new();
            if let Some(bus) = &bus {
                for (interface, member) in [("org.freedesktop.DBus.Properties", "PropertiesChanged"), ("com.redhat.tuned.control", "profile_changed")] {
                    let refresh = Arc::clone(&refresh);
                    subscriptions.push(bus.subscribe_to_signal(None, Some(interface), Some(member), None, None, gio::DBusSignalFlags::NONE,
                        move |signal| {
                            let path = signal.object_path;
                            if path.starts_with("/org/freedesktop/UPower") || path == "/net/hadess/PowerProfiles" || path == "/Tuned" { refresh(); }
                        }));
                }
            }
            // Slow fallback also catches sysfs changes on hosts without UPower.
            let main_loop = gio::glib::MainLoop::new(Some(&context), false);
            let tick_loop = main_loop.clone();
            let source = gio::glib::timeout_source_new(Duration::from_secs(30), Some("host-recovery"), gio::glib::Priority::DEFAULT, move || {
                if stop.load(Ordering::Relaxed) { tick_loop.quit(); return gio::glib::ControlFlow::Break; }
                refresh();
                gio::glib::ControlFlow::Continue
            });
            source.attach(Some(&context));
            main_loop.run();
            drop(monitors);
            drop(subscriptions);
        });
    });
}

fn spawn_keyboard(runtime: Arc<Mutex<Runtime<SysPlatform>>>, stop: Arc<AtomicBool>, last_input: Arc<AtomicU64>, active: Arc<AtomicBool>) {
    use std::os::fd::AsFd;
    use nix::poll::{poll, PollFd, PollFlags};
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
            held.clear();
            last_input.store(mono_millis(), Ordering::Relaxed);
            active.store(true, Ordering::Relaxed);
            while !stop.load(Ordering::Relaxed) {
                let ready = {
                    let mut fds: Vec<_> = files.iter().map(|file| PollFd::new(file.as_fd(), PollFlags::POLLIN)).collect();
                    if poll(&mut fds, 1000_u16).unwrap_or(0) == 0 { continue; }
                    fds.iter().map(|fd| fd.revents().unwrap_or(PollFlags::empty())).collect::<Vec<_>>()
                };
                let mut reopen = false;
                for (file, ready) in files.iter_mut().zip(ready) {
                    if ready.intersects(PollFlags::POLLHUP | PollFlags::POLLERR | PollFlags::POLLNVAL) { reopen = true; break; }
                    if !ready.contains(PollFlags::POLLIN) { continue; }
                    let mut buf = [0_u8; std::mem::size_of::<nix::libc::input_event>()];
                    let offset = std::mem::size_of::<nix::libc::timeval>();
                    match file.read(&mut buf) {
                        Ok(count) if count == buf.len() => {
                            let kind = u16::from_ne_bytes([buf[offset], buf[offset + 1]]);
                            let code = i32::from(u16::from_ne_bytes([buf[offset + 2], buf[offset + 3]]));
                            let value = i32::from_ne_bytes(buf[offset + 4..offset + 8].try_into().expect("input value"));
                            if kind != 1 {
                                continue;
                            }
                            if value == 1 {
                                last_input.store(mono_millis(), Ordering::Relaxed);
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
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {},
                        _ => { reopen = true; break; }
                    }
                }
                if reopen { break; }
            }
            active.store(false, Ordering::Relaxed);
            held.clear();
        }
    });
}

fn activate_program_shortcut(runtime: &Arc<Mutex<Runtime<SysPlatform>>>, mods: &[i32], key: i32) {
    use std::os::unix::process::CommandExt;
    static ACTIVATING: AtomicBool = AtomicBool::new(false);
    if ACTIVATING.swap(true, Ordering::Relaxed) { return; }
    struct ActivationGuard;
    impl Drop for ActivationGuard { fn drop(&mut self) { ACTIVATING.store(false, Ordering::Relaxed); } }
    let _guard = ActivationGuard;
    let Some(peer) = active_desktop_peer() else { return };
    let matches = runtime.lock().expect("runtime lock").shortcuts.get(peer.uid).is_some_and(|(bound, bound_key)| bound.as_slice() == mods && *bound_key == key);
    if !matches || peer.uid == 0 {
        return;
    }
    let bus = format!("/run/user/{}/bus", peer.uid);
    if !Path::new(&bus).exists() {
        return;
    }
    let Ok(Some(user)) = nix::unistd::User::from_uid(nix::unistd::Uid::from_raw(peer.uid)) else { return };
    let mut command = Command::new("/usr/bin/gdbus");
    command
        .args(["call", "--address", &format!("unix:path={bus}"), "--dest", "io.github.evident0.VictusHub", "--object-path", "/io/github/evident0/VictusHub", "--method", "org.freedesktop.Application.Activate", "{}"])
        .env("XDG_RUNTIME_DIR", format!("/run/user/{}", peer.uid))
        .env("DBUS_SESSION_BUS_ADDRESS", format!("unix:path={bus}"));
    // SAFETY: after fork, this callback only calls credential-setting syscalls.
    // Clear supplementary groups before dropping root, matching the old daemon.
    unsafe {
        command.pre_exec(move || {
            nix::unistd::setgroups(&[])?;
            nix::unistd::setgid(user.gid)?;
            nix::unistd::setuid(user.uid)?;
            Ok(())
        });
    }
    if let Err(error) = victus_hw::run_prepared_command(&mut command, Duration::from_secs(10)) { log::error!("program shortcut activation: {error}"); }
}

fn active_desktop_peer() -> Option<Peer> {
    let args = ["show-seat", "seat0", "-p", "ActiveSession", "--no-pager"].map(str::to_owned);
    let output = victus_hw::run_command_timeout(Path::new("/usr/bin/loginctl"), &args, Duration::from_secs(1)).ok()?;
    if output.status != 0 {
        return None;
    }
    let text = output.stdout;
    let session = text.lines().find_map(|line| line.strip_prefix("ActiveSession="))?.trim();
    let info = loginctl_session(session)?;
    let peer = Peer { uid: info.user, pid: 0, authorized: victus_hubd::session_ok(info.user, &info) };
    peer.authorized.then_some(peer)
}

fn blocked_signals() -> nix::sys::signal::SigSet {
    let mut set = nix::sys::signal::SigSet::empty();
    set.add(nix::sys::signal::Signal::SIGTERM);
    set.add(nix::sys::signal::Signal::SIGINT);
    set.thread_block().expect("block shutdown signals");
    set
}

fn set_nonblocking(file: &File) {
    use std::os::fd::AsRawFd;
    let fd = file.as_raw_fd();
    let current = nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_GETFL).unwrap_or(0);
    let mut flags = nix::fcntl::OFlag::from_bits_truncate(current);
    flags.insert(nix::fcntl::OFlag::O_NONBLOCK);
    let _ = nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_SETFL(flags));
}

fn idle_seconds(last_input: &AtomicU64, active: &AtomicBool) -> f64 {
    if !active.load(Ordering::Relaxed) { return -1.0; }
    mono_millis().saturating_sub(last_input.load(Ordering::Relaxed)) as f64 / 1000.0
}

fn mono_millis() -> u64 { (mono() * 1000.0) as u64 }

fn mono() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}
