//! GTK 4 / libadwaita panel. The page layout follows the previous Qt UI.
//!
//! This glib's `SourceId` does not remove its callback when dropped. Timers
//! still forget the id so a later `Drop` cannot cancel them.

#![allow(clippy::cognitive_complexity, clippy::similar_names, clippy::cast_possible_wrap, clippy::wildcard_imports)]
#![allow(clippy::items_after_statements, clippy::too_many_lines, clippy::option_if_let_else)]

pub mod graph;
pub mod pages;
pub mod paint;
pub mod tray;
pub mod widgets;
mod controls;
mod keyboard;
mod maintenance;
mod readings;

use std::cell::{Cell, RefCell};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::rc::{Rc, Weak};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use gtk4::gdk::{Key, ModifierType};
use gtk4::gio::ApplicationFlags;
use gtk4::glib::{self, ControlFlow, Propagation};
use gtk4::prelude::*;
use libadwaita::prelude::*;
use victus_core::{
    config_to_value, effect_is_animated, fan_mode_steps, is_modifier,
    keybind_label, normalize_fan_points, normalize_lighting_settings,
    parse_sensors_response, parse_status_response, power_to_value, step_increment, transact, validate_frequency,
    validate_shortcut, validate_undervolt, ExtraSensor, FanConfig, FanMode, FanPoint, PowerPolicy,
    request_key_for_graph, SensorSnapshot, CPU_TEMP_MAX_C, CURVE_RESPONSE_AGGRESSIVE, GPU_TEMP_MAX_C,
    KEY_LEFTALT, KEY_LEFTCTRL, KEY_LEFTMETA, KEY_LEFTSHIFT,
};

use crate::{
    connects_to_socket, fan_requests, lighting_request, power_status_line,
    upsert_program_shortcut, Model,
};

use self::keyboard::{active_color, hsv_parts, ignores_color, needs_color2, paint_keys, sync_color_entries};
use self::maintenance::{check_updates, collect_diagnostics, show_diagnostics, show_release, Release};
use self::pages::SensorRow;
use self::readings::{cpu_caption, current_text, format_stat_unit, gpu_caption, merge_snapshot, power_head, ram_status, reading_source, rpm_text, sample_value, temp_text};

const PAGE_NAMES: [&str; 6] = ["home", "power", "fans", "keyboard", "sensors", "settings"];
const DEBOUNCE: Duration = Duration::from_millis(250);

const CSS: &str = include_str!("style.css");

struct Running {
    min: f64,
    max: f64,
    sum: f64,
    count: u32,
}

enum BackgroundEvent {
    Release(Release),
    Diagnostics(Result<PathBuf, String>),
    State(serde_json::Value),
    Frequency(Result<crate::FrequencyWindow, String>),
    Control(ControlRequest, Result<String, String>),
}

enum ControlRequest {
    Profile(i32),
    Power(victus_core::PowerPolicy),
    Frequency(i32, i32),
    Undervolt(i32, i32),
}

struct SensorUpdate {
    snapshot: SensorSnapshot,
    keys: Vec<String>,
    frequency: Option<Result<crate::FrequencyWindow, String>>,
}

#[derive(Clone)]
struct EventSender {
    tx: mpsc::SyncSender<BackgroundEvent>,
    wake: Arc<UnixStream>,
}

impl EventSender {
    fn send(&self, event: BackgroundEvent) {
        // Producers run on background threads. Never discard a completion:
        // doing so could leave its UI controls permanently marked busy.
        if self.tx.send(event).is_ok() { let _ = (&*self.wake).write(&[1]); }
    }
}

struct Session {
    model: Rc<RefCell<Model>>,
    suppress: Cell<bool>,
    page: Arc<AtomicUsize>,
    visible: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    window: gtk4::ApplicationWindow,
    built: pages::Built,
    accent: gtk4::CssProvider,
    hover: Cell<Option<usize>>,
    anim: Cell<f64>,
    light_at: Cell<Option<Instant>>,
    fan_at: Cell<Option<Instant>>,
    curve_cpu: Cell<bool>,
    drag: Cell<Option<usize>>,
    capturing: Cell<bool>,
    zone_target: Cell<i32>,
    applied_power: RefCell<PowerPolicy>,
    applied_freq: Cell<Option<(i32, i32)>>,
    last_frequency: RefCell<Option<crate::FrequencyWindow>>,
    applied_uv: Cell<Option<(i32, i32)>>,
    profile_busy: Cell<bool>,
    captured_mods: RefCell<Vec<i32>>,
    selected_point: Cell<Option<usize>>,
    fan_hover: Cell<Option<usize>>,
    self_weak: RefCell<Weak<Session>>,
    timer: RefCell<Option<glib::SourceId>>,
    timer_due: Cell<Option<Instant>>,
    state_instance: RefCell<String>,
    state_revision: Cell<u64>,
    accent_profile: Cell<Option<i32>>,
    wake: Arc<UnixStream>,
    sensor_signal: Arc<(Mutex<u64>, Condvar)>,
    stats: RefCell<HashMap<String, Running>>,
    log: RefCell<VecDeque<String>>,
    sensor_rows: RefCell<Vec<SensorRow>>,
    extra_sig: RefCell<String>,
    charts: Rc<graph::Charts>,
    tray: RefCell<Option<tray::Tray>>,
    sensor_menu: RefCell<Option<gtk4::Popover>>,
    preview_at: Cell<Instant>,
    preview_step: Cell<u64>,
    sensor_rx: RefCell<Option<mpsc::Receiver<SensorUpdate>>>,
    events_tx: EventSender,
    events_rx: RefCell<mpsc::Receiver<BackgroundEvent>>,
}

pub fn start(model: Model, own_bus: bool) -> Result<(), String> {
    if !have_display() {
        return Ok(());
    }
    let _fonts = prepare_fonts();
    let (reader, wake) = UnixStream::pair().map_err(|error| error.to_string())?;
    reader.set_nonblocking(true).map_err(|error| error.to_string())?;
    wake.set_nonblocking(true).map_err(|error| error.to_string())?;
    let wake = Arc::new(wake);
    let reader = Rc::new(RefCell::new(Some(reader)));
    let page = Arc::new(AtomicUsize::new(model.page));
    let visible = Arc::new(AtomicBool::new(true));
    let stop = Arc::new(AtomicBool::new(false));
    let graph_keys = Arc::new(Mutex::new(Vec::new()));
    let sensor_signal = Arc::new((Mutex::new(0_u64), Condvar::new()));
    let sensor_rx = Rc::new(RefCell::new(spawn_sensor_thread(
        &model,
        Arc::clone(&page),
        Arc::clone(&visible),
        Arc::clone(&stop),
        Arc::clone(&graph_keys),
        Arc::clone(&wake),
        Arc::clone(&sensor_signal),
    )));
    let model = Rc::new(RefCell::new(model));
    let flags = if own_bus { ApplicationFlags::empty() } else { ApplicationFlags::NON_UNIQUE };
    let app = libadwaita::Application::new(Some("io.github.evident0.VictusHub"), flags);
    let stop_for_shutdown = Arc::clone(&stop);
    app.connect_shutdown(move |_| stop_for_shutdown.store(true, Ordering::Relaxed));
    let page_for_activate = Arc::clone(&page);
    let visible_for_activate = Arc::clone(&visible);
    let stop_for_activate = Arc::clone(&stop);
    let graphs_for_activate = Arc::clone(&graph_keys);
    let current = Rc::new(RefCell::new(Weak::<Session>::new()));
    app.connect_activate(move |app| {
        if let Some(session) = current.borrow().upgrade() {
            reveal(&session);
            return;
        }
        libadwaita::StyleManager::default().set_color_scheme(libadwaita::ColorScheme::ForceDark);
        configure_font_rendering();
        let session = build_ui(app, &model, &page_for_activate, &visible_for_activate, &stop_for_activate, &sensor_rx, &graphs_for_activate, &wake, &reader, &sensor_signal);
        *current.borrow_mut() = Rc::downgrade(&session);
    });
    let code = app.run();
    stop.store(true, Ordering::Relaxed);
    if code == glib::ExitCode::SUCCESS {
        Ok(())
    } else {
        Err(format!("GTK exited with status {}", glib::ExitCode::get(&code)))
    }
}

pub fn hydrate_live(model: &mut Model) {
    let socket = connects_to_socket(model).map(Path::to_path_buf);
    let Some(socket) = socket else { return };
    match transact(&socket, "get-state", Duration::from_secs(2)) {
        Ok(response) => match parse_status_response(&response) {
            Ok(body) => match serde_json::from_str(&body) {
                Ok(value) => {
                    model.hydrate(&value);
                    if !model.state.initialized {
                        let text = std::fs::read_to_string(&model.host.conf_path).unwrap_or_default();
                        let fan = model.host.conf_path.parent().and_then(|dir| std::fs::read_to_string(dir.join("config.json")).ok())
                            .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok()).filter(serde_json::Value::is_object);
                        if let Some(state) = crate::legacy::migrated_state(&text, fan.as_ref(), model.host.intel) {
                            let request = format!("initialize-state\t{}", victus_core::state_to_value(&state));
                            match transact(&socket, &request, Duration::from_secs(10)).and_then(|line| parse_status_response(&line)) {
                                Ok(_) => {
                                    // Another client may have initialized it first.
                                    if let Ok(value) = transact(&socket, "get-state", Duration::from_secs(2))
                                        .and_then(|line| parse_status_response(&line))
                                        .and_then(|body| serde_json::from_str::<serde_json::Value>(&body).map_err(|error| victus_core::HubError::new(error.to_string()))) {
                                        model.hydrate(&value);
                                    }
                                }
                                Err(error) => model.status = format!("Legacy settings migration failed: {error}"),
                            }
                        }
                    }
                    let text = std::fs::read_to_string(&model.host.conf_path).unwrap_or_default();
                    let settings = crate::legacy::settings(&text);
                    if settings.contains_key("programShortcut") || settings.contains_key("programShortcut/key") {
                        let body = serde_json::json!({"mods": model.host.shortcut_mods, "key": model.host.shortcut_key});
                        if let Err(error) = transact(&socket, &format!("program-shortcut\t{body}"), Duration::from_secs(2)).and_then(|line| parse_status_response(&line)) {
                            model.status = format!("Could not sync program shortcut: {error}");
                        }
                    }
                }
                Err(_) => model.status = "Daemon state was not valid".into(),
            },
            Err(error) => model.status = error.to_string(),
        },
        Err(error) => model.status = error.to_string(),
    }
}

fn have_display() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some_and(|value| !value.is_empty())
        || std::env::var_os("DISPLAY").is_some_and(|value| !value.is_empty())
}

struct FontFiles(Option<PathBuf>);
impl Drop for FontFiles { fn drop(&mut self) { if let Some(path) = &self.0 { let _ = std::fs::remove_dir_all(path); } } }

fn configure_font_rendering() {
    let Some(settings) = gtk4::Settings::default() else { return };
    // GTK 4.16+ automatic rendering can override the hinting settings. Look up
    // its enum at runtime to keep compatibility with the GTK 4.14 minimum.
    if let Some(manual) = settings.find_property("gtk-font-rendering")
        .and_then(|property| glib::EnumClass::with_type(property.value_type()))
        .and_then(|class| class.to_value_by_nick("manual")) {
        settings.set_property("gtk-font-rendering", manual);
    }
    // Keep Plex's measured outlines and rendered ink in the same coordinate
    // space. Even light hinting can snap capital tops outside fractional
    // glyph bounds; label padding cannot repair clipping inside a glyph.
    settings.set_gtk_hint_font_metrics(false);
    settings.set_gtk_xft_hinting(0);
    settings.set_gtk_xft_hintstyle(Some("hintnone"));
}

fn prepare_fonts() -> FontFiles {
    if std::env::var_os("FONTCONFIG_FILE").is_some() {
        return FontFiles(std::env::var_os("VICTUS_HUB_FONT_DIR").map(PathBuf::from).filter(|path| {
            path.parent() == Some(std::env::temp_dir().as_path())
                && path.file_name().is_some_and(|name| name.to_string_lossy().starts_with("victus-hub-fonts-"))
        }));
    }
    let dir = std::env::temp_dir().join(format!("victus-hub-fonts-{}", std::process::id()));
    if std::fs::create_dir_all(&dir).is_err() {
        return FontFiles(None);
    }
    let fonts: &[(&str, &[u8])] = &[
        ("IBMPlexSans-Regular.ttf", include_bytes!("../../../../victus_hub/resources/fonts/IBMPlexSans-Regular.ttf")),
        ("IBMPlexSans-Medium.ttf", include_bytes!("../../../../victus_hub/resources/fonts/IBMPlexSans-Medium.ttf")),
        ("IBMPlexSans-SemiBold.ttf", include_bytes!("../../../../victus_hub/resources/fonts/IBMPlexSans-SemiBold.ttf")),
        ("IBMPlexMono-Regular.ttf", include_bytes!("../../../../victus_hub/resources/fonts/IBMPlexMono-Regular.ttf")),
        ("IBMPlexMono-Medium.ttf", include_bytes!("../../../../victus_hub/resources/fonts/IBMPlexMono-Medium.ttf")),
    ];
    for (name, bytes) in fonts {
        let _ = std::fs::write(dir.join(name), bytes);
    }
    let conf = dir.join("fonts.conf");
    let xml = format!(
        "<?xml version=\"1.0\"?>\n<fontconfig>\n  <include ignore_missing=\"yes\">/etc/fonts/fonts.conf</include>\n  <dir>{}</dir>\n</fontconfig>\n",
        dir.display()
    );
    if std::fs::write(&conf, xml).is_err() {
        return FontFiles(Some(dir));
    }
    // `std::env::set_var` is unsafe on this toolchain, and this crate forbids
    // unsafe. Restart once so fontconfig reads the bundled faces.
    let Ok(exe) = std::env::current_exe() else { return FontFiles(Some(dir)) };
    let mut command = Command::new(exe);
    command.args(std::env::args_os().skip(1));
    command.env("FONTCONFIG_FILE", &conf);
    command.env("VICTUS_HUB_FONT_DIR", &dir);
    use std::os::unix::process::CommandExt;
    eprintln!("font setup: {}", command.exec());
    FontFiles(Some(dir))
}

fn spawn_sensor_thread(
    model: &Model,
    page: Arc<AtomicUsize>,
    visible: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    graph_keys: Arc<Mutex<Vec<String>>>,
    wake: Arc<UnixStream>,
    signal: Arc<(Mutex<u64>, Condvar)>,
) -> Option<mpsc::Receiver<SensorUpdate>> {
    let socket = connects_to_socket(model)?.to_path_buf();
    let (tx, rx) = mpsc::sync_channel(2);
    let _ = std::thread::Builder::new().name("victus-sensors".into()).spawn(move || {
        let mut had_requests = false;
        while !stop.load(Ordering::Relaxed) {
            let gate = signal.0.lock().expect("sensor wake");
            let generation = *gate;
            let active = visible.load(Ordering::Relaxed);
            drop(gate);
            let mut requested = false;
            if active {
                let index = page.load(Ordering::Relaxed);
                let extra = graph_keys.lock().map(|keys| keys.clone()).unwrap_or_default();
                if let Some(request) = sensor_request_with(index, &extra) {
                    requested = true;
                    had_requests = true;
                    if let Ok(response) = transact(&socket, &request, Duration::from_secs(2)) {
                        if let Ok(snapshot) = parse_sensors_response(&response) {
                            let keys = request.split_once('\t').map(|(_, body)| body.split(',').map(str::to_owned).collect()).unwrap_or_default();
                            let frequency = (index == 1).then(|| read_frequency_window());
                            if tx.try_send(SensorUpdate { snapshot, keys, frequency }).is_ok() { let _ = (&*wake).write(&[1]); }
                        }
                    }
                } else if had_requests {
                    let _ = transact(&socket, "sensors", Duration::from_secs(1));
                    had_requests = false;
                }
            } else if had_requests {
                let _ = transact(&socket, "sensors", Duration::from_secs(1));
                had_requests = false;
            }
            let gate = signal.0.lock().expect("sensor wake");
            if stop.load(Ordering::Relaxed) { break; }
            if *gate != generation { continue; }
            if active && requested && visible.load(Ordering::Relaxed) {
                drop(signal.1.wait_timeout(gate, Duration::from_secs(1)).expect("sensor wake"));
            } else {
                drop(signal.1.wait(gate).expect("sensor wake"));
            }
        }
    });
    Some(rx)
}

fn sensor_request_with(page: usize, extra: &[String]) -> Option<String> {
    let mut keys: Vec<String> = victus_core::keys_for_page(page).into_iter().map(str::to_owned).collect();
    for key in extra {
        let mapped = request_key_for_graph(key);
        if !keys.iter().any(|item| item == mapped) {
            keys.push(mapped.to_owned());
        }
    }
    if keys.is_empty() { None } else { Some(format!("sensors\t{}", keys.join(","))) }
}

fn build_ui(
    app: &libadwaita::Application,
    model: &Rc<RefCell<Model>>,
    page: &Arc<AtomicUsize>,
    visible: &Arc<AtomicBool>,
    stop: &Arc<AtomicBool>,
    sensor_rx: &Rc<RefCell<Option<mpsc::Receiver<SensorUpdate>>>>,
    graph_keys: &Arc<Mutex<Vec<String>>>,
    wake: &Arc<UnixStream>,
    wake_reader: &Rc<RefCell<Option<UnixStream>>>,
    sensor_signal: &Arc<(Mutex<u64>, Condvar)>,
) -> Rc<Session> {
    let mut built = pages::build(&model.borrow());
    let rows = std::mem::take(&mut built.sensors.rows);
    // AdwApplicationWindow has no titlebar, so the compositor draws no frame.
    // A plain application window keeps the desktop's window decorations.
    let window = gtk4::ApplicationWindow::new(app);
    window.set_title(Some("Victus Hub"));
    window.set_icon_name(Some("victus-hub"));
    window.set_decorated(true);
    window.set_default_size(460, 680);
    window.set_size_request(420, 680);
    window.set_hide_on_close(true);
    window.add_css_class("victus");
    let accent = gtk4::CssProvider::new();
    install_css(&window, &accent);
    window.set_child(Some(&built.root));
    let (tx, events_rx) = mpsc::sync_channel(64);
    let events_tx = EventSender { tx, wake: Arc::clone(wake) };
    let session = Rc::new(Session {
        model: Rc::clone(model),
        suppress: Cell::new(false),
        page: Arc::clone(page),
        visible: Arc::clone(visible),
        stop: Arc::clone(stop),
        window: window.clone(),
        built,
        accent,
        hover: Cell::new(None),
        anim: Cell::new(0.0),
        light_at: Cell::new(None),
        fan_at: Cell::new(None),
        curve_cpu: Cell::new(true),
        drag: Cell::new(None),
        capturing: Cell::new(false),
        zone_target: Cell::new(-1),
        applied_power: RefCell::new(model.borrow().state.power.clone()),
        applied_freq: Cell::new(None),
        last_frequency: RefCell::new(None),
        applied_uv: Cell::new(None),
        profile_busy: Cell::new(false),
        captured_mods: RefCell::new(Vec::new()),
        selected_point: Cell::new(None),
        fan_hover: Cell::new(None),
        self_weak: RefCell::new(Weak::new()),
        timer: RefCell::new(None),
        timer_due: Cell::new(None),
        state_instance: RefCell::new(String::new()),
        state_revision: Cell::new(0),
        accent_profile: Cell::new(None),
        wake: Arc::clone(wake),
        sensor_signal: Arc::clone(sensor_signal),
        stats: RefCell::new(HashMap::new()),
        log: RefCell::new(VecDeque::new()),
        sensor_rows: RefCell::new(rows),
        extra_sig: RefCell::new(String::new()),
        charts: graph::Charts::new(Arc::clone(graph_keys), Arc::clone(sensor_signal)),
        tray: RefCell::new(None),
        sensor_menu: RefCell::new(None),
        preview_at: Cell::new(Instant::now() + Duration::from_secs(1)),
        preview_step: Cell::new(graph::preview_origin()),
        sensor_rx: RefCell::new(sensor_rx.borrow_mut().take()),
        events_tx,
        events_rx: RefCell::new(events_rx),
    });
    *session.self_weak.borrow_mut() = Rc::downgrade(&session);
    wire(&session);
    sync_controls(&session);
    show_page(&session, 0);
    let weak = Rc::downgrade(&session);
    session.window.connect_close_request(move |window| {
        let Some(session) = weak.upgrade() else { return Propagation::Proceed };
        if window.hides_on_close() {
            session.charts.hide_all();
            session.visible.store(false, Ordering::Relaxed);
            notify_sensors(&session);
        } else {
            session.stop.store(true, Ordering::Relaxed);
        }
        Propagation::Proceed
    });
    if let Some(mut reader) = wake_reader.borrow_mut().take() {
        let tick = Rc::clone(&session);
        let fd = reader.as_raw_fd();
        retain(glib::source::unix_fd_add_local(fd, glib::IOCondition::IN, move |_, _| {
            let mut bytes = [0_u8; 256];
            while reader.read(&mut bytes).is_ok_and(|count| count > 0) {}
            if tick.stop.load(Ordering::Relaxed) { return ControlFlow::Break; }
            on_tick(&tick);
            deliver_events(&tick);
            arm_tick(&tick);
            ControlFlow::Continue
        }));
    }
    spawn_state_stream(&session);
    install_tray(&session);
    let weak = Rc::downgrade(&session);
    session.window.connect_realize(move |window| {
        if let Some(surface) = window.surface().and_downcast::<gtk4::gdk::Toplevel>() {
            let weak = weak.clone();
            surface.connect_state_notify(move |surface| {
                if let Some(session) = weak.upgrade() {
                    session.visible.store(session.window.is_visible() && !surface.state().contains(gtk4::gdk::ToplevelState::MINIMIZED), Ordering::Relaxed);
                    notify_sensors(&session);
                    arm_tick(&session);
                }
            });
        }
    });
    session.window.present();
    session
}

fn notify_sensors(session: &Session) { let mut guard = session.sensor_signal.0.lock().expect("sensor wake"); *guard = guard.wrapping_add(1); session.sensor_signal.1.notify_all(); }

fn spawn_state_stream(session: &Session) {
    let Some(socket) = connects_to_socket(&session.model.borrow()).map(Path::to_path_buf) else { return };
    let tx = session.events_tx.clone();
    let stop = Arc::clone(&session.stop);
    std::thread::spawn(move || {
        let mut retry = 2;
        while !stop.load(Ordering::Relaxed) {
            if let Ok(mut stream) = UnixStream::connect(&socket) {
                stream.set_read_timeout(Some(Duration::from_secs(1))).ok();
                stream.set_write_timeout(Some(Duration::from_secs(1))).ok();
                if stream.write_all(b"shortcut-events\n").is_ok() {
                    let mut reader = BufReader::new(stream);
                    let mut line = String::new();
                    while !stop.load(Ordering::Relaxed) {
                        match reader.read_line(&mut line) {
                            Ok(0) => break,
                            Ok(_) => {
                                if line.len() > 65_536 { break; }
                                if line.trim() == "OK\tshortcut-events" { retry = 2; }
                                if let Some(body) = line.strip_prefix("STATE\t") {
                                    if let Ok(value) = serde_json::from_str(body.trim()) { tx.send(BackgroundEvent::State(value)); }
                                } else if line.starts_with("ERR\t") { break; }
                                line.clear();
                            }
                            Err(error) if matches!(error.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {},
                            Err(_) => break,
                        }
                    }
                }
            }
            std::thread::sleep(Duration::from_secs(retry));
            retry = (retry * 2).min(30);
        }
    });
}

pub fn frequency_window(policies: &[victus_hw::FrequencyPolicy]) -> Result<crate::FrequencyWindow, String> {
    let first = policies.first().ok_or("CPU frequency control is unavailable")?;
    let lower = policies.iter().map(|policy| policy.hardware_min).max().unwrap_or(first.hardware_min);
    let upper = policies.iter().map(|policy| policy.hardware_max).min().unwrap_or(first.hardware_max);
    if lower > upper { return Err("CPU policies have no common frequency range".into()); }
    let minimum = policies.iter().map(|policy| policy.minimum).max().unwrap_or(lower).clamp(lower, upper);
    let maximum = policies.iter().map(|policy| policy.maximum).min().unwrap_or(upper).clamp(minimum, upper);
    Ok(crate::FrequencyWindow { lower, upper, minimum, maximum, policies: policies.len(), mixed: policies.iter().any(|policy| (policy.minimum, policy.maximum) != (first.minimum, first.maximum)) })
}

fn refresh_frequency(session: &Session) {
    let tx = session.events_tx.clone();
    std::thread::spawn(move || {
        let result = read_frequency_window();
        tx.send(BackgroundEvent::Frequency(result));
    });
}

fn read_frequency_window() -> Result<crate::FrequencyWindow, String> {
    victus_hw::read_policies(Path::new("/sys/devices/system/cpu/cpufreq"))
        .map_err(|error| error.to_string()).and_then(|policies| frequency_window(&policies))
}

pub fn gpu_name() -> String {
    let Ok(entries) = std::fs::read_dir("/proc/driver/nvidia/gpus") else { return String::new() };
    for entry in entries.flatten() {
        if let Ok(text) = std::fs::read_to_string(entry.path().join("information")) {
            if let Some(name) = text.lines().find_map(|line| line.strip_prefix("Model:")) {
                return name.trim().trim_start_matches("NVIDIA GeForce ").to_owned();
            }
        }
    }
    String::new()
}

fn arm_tick(session: &Session) {
    if session.stop.load(Ordering::Relaxed) { return; }
    let model = session.model.borrow();
    let animated = session.visible.load(Ordering::Relaxed) && matches!(model.page, 0 | 3)
        && model.state.lighting.enabled && effect_is_animated(&model.state.lighting.effect);
    let preview = model.offline && session.charts.has_visible();
    drop(model);
    let delay = if animated || session.light_at.get().is_some() || session.fan_at.get().is_some() { Duration::from_millis(50) }
        else if preview { Duration::from_secs(1) } else { return };
    let due = Instant::now() + delay;
    if session.timer_due.get().is_some_and(|existing| existing <= due) { return; }
    if let Some(id) = session.timer.borrow_mut().take() { id.remove(); }
    session.timer_due.set(Some(due));
    let weak = session.self_weak.borrow().clone();
    let id = glib::timeout_add_local_once(delay, move || {
        if let Some(session) = weak.upgrade() {
            session.timer.borrow_mut().take();
            session.timer_due.set(None);
            on_tick(&session);
            arm_tick(&session);
        }
    });
    *session.timer.borrow_mut() = Some(id);
}

fn install_tray(session: &Rc<Session>) {
    let (profile, fan_mode, modes) = {
        let model = session.model.borrow();
        let mode = fan_mode_key(FanMode::from_config(&model.state.fan)).to_owned();
        let modes = pages::fan_pairs(&model.host.fan_modes).into_iter().map(|(key, label, _)| (key.to_owned(), label.to_owned())).collect::<Vec<_>>();
        (model.profile, mode, modes)
    };
    match tray::Tray::install(profile, &fan_mode, &modes, Arc::clone(&session.wake)) {
        Ok(icon) => *session.tray.borrow_mut() = Some(icon),
        Err(error) => eprintln!("tray icon: {error}"),
    }
}

#[allow(clippy::forget_non_drop)]
fn retain(id: glib::SourceId) {
    std::mem::forget(id);
}

fn install_css(window: &impl IsA<gtk4::Widget>, accent: &gtk4::CssProvider) {
    let provider = gtk4::CssProvider::new();
    provider.load_from_string(CSS);
    let display = window.display();
    gtk4::style_context_add_provider_for_display(&display, &provider, gtk4::STYLE_PROVIDER_PRIORITY_USER);
    accent.load_from_string("");
    gtk4::style_context_add_provider_for_display(&display, accent, gtk4::STYLE_PROVIDER_PRIORITY_USER + 1);
}

fn wire(session: &Rc<Session>) {
    controls::wire(session);
    keyboard::wire(session);
    wire_sensors(session);
    wire_settings(session);
    wire_draws(session);
}

fn on_click(session: &Rc<Session>, button: &gtk4::Button, action: impl Fn(&Session) + 'static) {
    let weak = Rc::downgrade(session);
    button.connect_clicked(move |_| {
        if let Some(session) = weak.upgrade() { action(&session); }
    });
}

struct GraphPick {
    key: String,
    name: String,
    group: String,
    unit: String,
    min: f64,
    max: f64,
    graphable: bool,
}

fn wire_sensors(session: &Rc<Session>) {
    let menu = gtk4::Popover::new();
    menu.set_parent(&session.window);
    menu.set_has_arrow(false);
    menu.add_css_class("sensor-menu");
    let item = gtk4::Button::with_label("Graph");
    menu.set_child(Some(&item));
    *session.sensor_menu.borrow_mut() = Some(menu);

    let pending = Rc::new(RefCell::new(None::<GraphPick>));
    let click = gtk4::GestureClick::new();
    click.set_button(3);
    click.set_propagation_phase(gtk4::PropagationPhase::Capture);
    let weak = Rc::downgrade(session);
    let pending_click = Rc::clone(&pending);
    click.connect_pressed(move |gesture, _, x, y| {
        gesture.set_state(gtk4::EventSequenceState::Claimed);
        let Some(session) = weak.upgrade() else { return };
        let Some(y_px) = finite_i32(y) else { return };
        let Some(row) = session.built.sensors.list.row_at_y(y_px) else { return };
        let key = row.widget_name().to_string();
        if key.is_empty() {
            return;
        }
        let pick = {
            let rows = session.sensor_rows.borrow();
            let Some(sensor) = rows.iter().find(|item| item.key == key) else { return };
            GraphPick {
                key: sensor.key.clone(),
                name: sensor.name.clone(),
                group: sensor.group.clone(),
                unit: sensor.unit.clone(),
                min: sensor.min,
                max: sensor.max,
                graphable: sensor.graphable,
            }
        };
        let graphable = pick.graphable;
        *pending_click.borrow_mut() = Some(pick);
        let Some(menu) = session.sensor_menu.borrow().clone() else { return };
        if let Some(button) = menu.child().and_downcast::<gtk4::Button>() {
            button.set_sensitive(graphable);
            button.set_tooltip_text(if graphable { None } else { Some("Not graphable") });
        }
        let Some(x_px) = finite_i32(x) else { return };
        let Some(point) = session.built.sensors.list.compute_point(
            &session.window,
            &gtk4::graphene::Point::new(x_px as f32, y_px as f32),
        ) else {
            return;
        };
        let origin_x = finite_i32(f64::from(point.x())).unwrap_or(0);
        let origin_y = finite_i32(f64::from(point.y())).unwrap_or(0);
        menu.set_pointing_to(Some(&gtk4::gdk::Rectangle::new(origin_x, origin_y, 1, 1)));
        menu.popup();
    });
    session.built.sensors.list.add_controller(click);

    let weak = Rc::downgrade(session);
    item.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        if let Some(menu) = session.sensor_menu.borrow().as_ref() {
            menu.popdown();
        }
        let Some(pick) = pending.borrow_mut().take() else { return };
        if !pick.graphable {
            return;
        }
        let offline = session.model.borrow().offline;
        session.charts.open(
            graph::Meta { key: pick.key, name: pick.name, group: pick.group, unit: pick.unit, min: pick.min, max: pick.max },
            offline,
        );
        arm_tick(&session);
    });
}

fn wire_settings(session: &Rc<Session>) {
    bind_switch(session, &session.built.settings.battery, SwitchKind::Battery);
    bind_switch(session, &session.built.settings.nvidia, SwitchKind::Nvidia);
    bind_switch(session, &session.built.settings.hardware, SwitchKind::Hardware);
    on_click(session, &session.built.settings.shortcut_set, |session| {
        if session.capturing.get() {
            session.capturing.set(false);
            session.built.settings.shortcut_set.set_label("Set");
            show_shortcut(session, None);
        } else {
            session.capturing.set(true);
            session.captured_mods.borrow_mut().clear();
            session.built.settings.shortcut_set.set_label("Press a key…");
            session.built.settings.shortcut.set_text("(waiting)");
        }
    });
    on_click(session, &session.built.settings.shortcut_clear, |session| {
        session.capturing.set(false);
        session.built.settings.shortcut_set.set_label("Set");
        save_shortcut(session, &[], 0);
    });
    on_click(session, &session.built.settings.update, check_updates);
    on_click(session, &session.built.settings.diagnostics, collect_diagnostics);
    on_click(session, &session.built.settings.quit, quit);
    let keys = gtk4::EventControllerKey::new();
    keys.set_propagation_phase(gtk4::PropagationPhase::Capture);
    let weak = Rc::downgrade(session);
    keys.connect_key_pressed(move |_, keyval, keycode, state| {
        let Some(session) = weak.upgrade() else { return Propagation::Proceed };
        if !session.capturing.get() {
            if session.page.load(Ordering::Relaxed) == 2 && session.built.fans.chart.has_focus() && keyval == Key::Delete {
                delete_selected_point(&session);
                return Propagation::Stop;
            }
            return Propagation::Proceed;
        }
        if keyval == Key::Escape {
            session.capturing.set(false);
            session.built.settings.shortcut_set.set_label("Set");
            show_shortcut(&session, None);
            return Propagation::Stop;
        }
        if keycode < 8 {
            return Propagation::Proceed;
        }
        let code = i32::try_from(keycode).unwrap_or(0) - 8;
        if is_modifier(code) {
            let mut mods = session.captured_mods.borrow_mut();
            if !mods.contains(&code) { mods.push(code); }
            return Propagation::Stop;
        }
        if code <= 0 {
            return Propagation::Proceed;
        }
        let mut mods = session.captured_mods.borrow().clone();
        for fallback in modifier_codes(state) {
            let side = match fallback { 29 => 97, 42 => 54, 56 => 100, 125 => 126, other => other };
            if !mods.contains(&fallback) && !mods.contains(&side) { mods.push(fallback); }
        }
        session.capturing.set(false);
        session.built.settings.shortcut_set.set_label("Set");
        match validate_shortcut(&mods, code) {
            Ok((mods, key)) => save_shortcut(&session, &mods, key),
            Err(error) => session.built.settings.shortcut.set_text(&error.to_string()),
        }
        Propagation::Stop
    });
    let weak = Rc::downgrade(session);
    keys.connect_key_released(move |_, _, keycode, _| {
        if let Some(session) = weak.upgrade() {
            let code = i32::try_from(keycode).unwrap_or(0) - 8;
            session.captured_mods.borrow_mut().retain(|held| *held != code);
        }
    });
    let weak = Rc::downgrade(session);
    session.window.connect_is_active_notify(move |window| {
        if !window.is_active() {
            if let Some(session) = weak.upgrade() {
                session.capturing.set(false);
                session.captured_mods.borrow_mut().clear();
                session.built.settings.shortcut_set.set_label("Set");
                show_shortcut(&session, None);
            }
        }
    });
    session.window.add_controller(keys);
}

#[derive(Clone, Copy)]
enum SwitchKind {
    Battery,
    Nvidia,
    Hardware,
}

fn bind_switch(session: &Rc<Session>, switch: &gtk4::Switch, kind: SwitchKind) {
    let weak = Rc::downgrade(session);
    switch.connect_active_notify(move |switch| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() {
            return;
        }
        let active = switch.is_active();
        let request = match kind {
            SwitchKind::Battery => format!("battery-power-save\t{}", i32::from(active)),
            SwitchKind::Nvidia => format!("disable-nvidia-queries\t{}", i32::from(active)),
            SwitchKind::Hardware => format!("hardware-shortcuts\t{}", i32::from(active)),
        };
        {
            let mut model = session.model.borrow_mut();
            match kind {
                SwitchKind::Battery => model.state.battery_power_save = active,
                SwitchKind::Nvidia => model.state.disable_nvidia_queries = active,
                SwitchKind::Hardware => model.state.hardware_shortcuts = active,
            }
        }
        if !commit(&session, &request) {
            session.suppress.set(true);
            switch.set_active(!active);
            let mut model = session.model.borrow_mut();
            match kind {
                SwitchKind::Battery => model.state.battery_power_save = !active,
                SwitchKind::Nvidia => model.state.disable_nvidia_queries = !active,
                SwitchKind::Hardware => model.state.hardware_shortcuts = !active,
            }
            session.suppress.set(false);
        }
    });
}

fn wire_draws(session: &Rc<Session>) {
    on_draw(session, &session.built.sidebar, |session, cr, width, height| {
        let keyboard = session.model.borrow().host.keyboard;
        paint::sidebar(cr, f64::from(width), f64::from(height), keyboard, session.page.load(Ordering::Relaxed), session.hover.get());
    });
    on_draw(session, &session.built.fans.chart, paint_chart);
    for (area, compact) in [(&session.built.keyboard.visual, false), (&session.built.home.mini, true)] {
        on_draw(session, area, move |session, cr, width, height| paint_keys(session, cr, width, height, compact));
    }
    for (area, secondary) in [(&session.built.keyboard.chip, false), (&session.built.keyboard.chip2, true)] {
        on_draw(session, area, move |session, cr, width, height| {
            let hex = if secondary { session.model.borrow().state.lighting.color2.clone() } else { active_color(&session.model.borrow(), session.zone_target.get()) };
            paint::chip(cr, f64::from(width), f64::from(height), &hex);
        });
    }
    on_draw(session, &session.built.keyboard.hue, |session, cr, width, height| {
        let hex = active_color(&session.model.borrow(), session.zone_target.get());
        paint::hue_strip(cr, f64::from(width), f64::from(height), paint::unit_rgb(&hex));
    });
    on_draw(session, &session.built.keyboard.shade, |session, cr, width, height| {
        let hex = active_color(&session.model.borrow(), session.zone_target.get());
        let (hue, _, _) = hsv_parts(&hex);
        paint::shade_strip(cr, f64::from(width), f64::from(height), hue, paint::unit_rgb(&hex));
    });
}

fn on_draw(session: &Rc<Session>, area: &gtk4::DrawingArea, draw: impl Fn(&Session, &gtk4::cairo::Context, i32, i32) + 'static) {
    let weak = Rc::downgrade(session);
    area.set_draw_func(move |_, cr, width, height| {
        if let Some(session) = weak.upgrade() { draw(&session, cr, width, height); }
    });
}

fn sync_controls(session: &Session) {
    session.suppress.set(true);
    let model = session.model.borrow();
    let power = &model.state.power;
    session.built.power.enabled.set_active(power.enabled);
    set_scale(&session.built.power.stapm.scale, f64::from(power.stapm_limit) / 1000.0);
    set_scale(&session.built.power.fast.scale, f64::from(power.fast_limit) / 1000.0);
    set_scale(&session.built.power.slow.scale, f64::from(power.slow_limit) / 1000.0);
    set_scale(&session.built.power.tctl.scale, f64::from(power.tctl_temp));
    set_scale(&session.built.power.reapply.scale, f64::from(power.reapply_seconds));
    *session.applied_power.borrow_mut() = power.clone();
    session.built.power.limits.set_visible(power.enabled);
    session.built.power.note.set_visible(!power.enabled);
    if let Some(window) = model.host.frequency.clone() {
        let step = 1000.0;
        configure_scale(&session.built.power.freq_min.scale, f64::from(window.lower), f64::from(window.upper), step);
        configure_scale(&session.built.power.freq_max.scale, f64::from(window.lower), f64::from(window.upper), step);
        let (mut minimum, mut maximum) = model.state.cpu_frequency.unwrap_or((window.minimum, window.maximum));
        minimum = minimum.clamp(window.lower, window.upper);
        maximum = maximum.clamp(window.lower, window.upper);
        if minimum > maximum {
            minimum = window.lower;
            maximum = window.upper;
        }
        set_scale(&session.built.power.freq_min.scale, f64::from(minimum));
        set_scale(&session.built.power.freq_max.scale, f64::from(maximum));
        session.applied_freq.set(Some((window.minimum, window.maximum)));
        session.built.power.freq_sliders.set_visible(true);
        let note = if window.mixed { "CPU policies disagree. The sliders use their common hardware range." } else { "" };
        session.built.power.freq_note.set_text(note);
    } else {
        session.built.power.freq_sliders.set_visible(false);
        let note = if model.host.frequency_error.is_empty() {
            "CPU frequency control is unavailable on this system"
        } else {
            model.host.frequency_error.as_str()
        };
        session.built.power.freq_note.set_text(note);
    }
    set_scale(&session.built.power.uv_core.scale, f64::from(model.host.undervolt.0));
    set_scale(&session.built.power.uv_cache.scale, f64::from(model.host.undervolt.1));
    session.applied_uv.set(None);
    session.built.settings.battery.set_active(model.state.battery_power_save);
    session.built.settings.nvidia.set_active(model.state.disable_nvidia_queries);
    session.built.settings.hardware.set_active(model.state.hardware_shortcuts);
    let response = if model.state.fan.curve_response == CURVE_RESPONSE_AGGRESSIVE { 1 } else { 0 };
    session.built.fans.response.set_selected(response);
    session.built.fans.min_change.set_value(model.state.fan.min_fan_change_pct);
    let lighting = normalize_lighting_settings(&model.state.lighting, model.zones);
    set_scale(&session.built.keyboard.brightness, f64::from(lighting.brightness));
    set_scale(&session.built.keyboard.speed, f64::from(lighting.speed));
    session.built.keyboard.idle_enabled.set_active(lighting.idle_timeout > 0);
    session.built.keyboard.idle.set_sensitive(lighting.idle_timeout > 0);
    if lighting.idle_timeout > 0 { session.built.keyboard.idle.set_value(f64::from(lighting.idle_timeout)); }
    drop(model);
    session.suppress.set(false);
    sync_color_entries(session);
    show_shortcut(session, None);
    refresh_view(session);
}

fn show_page(session: &Session, page: usize) {
    let page = page.min(PAGE_NAMES.len() - 1);
    if page == 3 && !session.model.borrow().host.keyboard {
        return;
    }
    session.page.store(page, Ordering::Relaxed);
    session.model.borrow_mut().page = page;
    session.built.stack.set_visible_child_name(PAGE_NAMES[page]);
    session.built.root.set_size_request(page_width(page), -1);
    session.window.set_default_size(page_width(page), 680);
    session.built.sidebar.queue_draw();
    notify_sensors(session);
    if page == 1 { refresh_frequency(session); }
    arm_tick(session);
}

fn page_width(page: usize) -> i32 {
    match page {
        2 | 3 | 4 => 700,
        _ => 480,
    }
}

fn select_profile(session: &Session, profile: i32) {
    let profile = profile.clamp(0, 2);
    if session.profile_busy.replace(true) { return; }
    for button in &session.built.home.profile_buttons { button.set_sensitive(false); }
    session.model.borrow_mut().status = "Changing power profile…".into();
    show_status(session);
    submit_control(session, format!("set-profile\t{profile}"), ControlRequest::Profile(profile));
}

fn apply_fan_mode(session: &Session, mode: FanMode) {
    if FanMode::from_config(&session.model.borrow().state.fan) == mode {
        refresh_view(session);
        return;
    }
    session.fan_at.set(None);
    session.drag.set(None);
    let requests = {
        let model = session.model.borrow();
        fan_requests(&model.state.fan, mode)
    };
    let mut ok = true;
    for request in &requests {
        if !commit(session, request) {
            ok = false;
            break;
        }
    }
    if ok {
        let mut model = session.model.borrow_mut();
        if let Some(last) = fan_mode_steps(&model.state.fan, mode).last() {
            model.state.fan = last.clone();
        }
    }
    refresh_view(session);
}

fn apply_power(session: &Session) {
    if !session.built.power.apply.is_sensitive() { return; }
    let policy = power_form(session);
    if policy == *session.applied_power.borrow() {
        return;
    }
    let request = format!("power-config\t{}", power_to_value(&policy));
    session.built.power.apply.set_sensitive(false);
    session.built.power.enabled.set_sensitive(false);
    submit_control(session, request, ControlRequest::Power(policy));
}

fn apply_frequency(session: &Session) {
    if !session.built.power.freq_apply.is_sensitive() { return; }
    let minimum = paint::round_i32(session.built.power.freq_min.scale.value());
    let maximum = paint::round_i32(session.built.power.freq_max.scale.value());
    if session.applied_freq.get() == Some((minimum, maximum)) && session.model.borrow().state.cpu_frequency == Some((minimum, maximum)) {
        return;
    }
    if let Err(error) = validate_frequency(minimum, maximum) {
        session.model.borrow_mut().status = error.to_string();
        show_status(session);
        return;
    }
    session.built.power.freq_apply.set_sensitive(false);
    submit_control(session, format!("cpu-frequency-config\t{minimum}\t{maximum}"), ControlRequest::Frequency(minimum, maximum));
}

fn apply_undervolt(session: &Session) {
    if !session.built.power.uv_apply.is_sensitive() { return; }
    let core = paint::round_i32(session.built.power.uv_core.scale.value()).clamp(-250, 0);
    let cache = paint::round_i32(session.built.power.uv_cache.scale.value()).clamp(-250, 0);
    if let Err(error) = validate_undervolt(core, cache) {
        session.built.power.uv_status.set_text(&error.to_string());
        return;
    }
    session.built.power.uv_apply.set_sensitive(false);
    session.built.power.uv_status.set_text("Applying Intel undervolt…");
    submit_control(session, format!("intel-undervolt\t{core}\t{cache}"), ControlRequest::Undervolt(core, cache));
}

fn submit_control(session: &Session, request: String, kind: ControlRequest) {
    let socket = connects_to_socket(&session.model.borrow()).map(Path::to_path_buf);
    let tx = session.events_tx.clone();
    std::thread::spawn(move || {
        let result = match socket.as_ref() {
            Some(socket) => {
                // Profile discovery and application can each take ten seconds.
                let timeout = if matches!(&kind, ControlRequest::Profile(_)) { 60 } else { 10 };
                transact(socket, &request, Duration::from_secs(timeout)).and_then(|line| parse_status_response(&line)).map_err(|error| error.to_string())
            }
            None => Ok("Offline preview".into()),
        };
        let refresh_profile = result.is_ok() && matches!(&kind, ControlRequest::Profile(_));
        tx.send(BackgroundEvent::Control(kind, result));
        if refresh_profile {
            if let Some(socket) = socket {
                if let Ok(value) = transact(&socket, "get-state", Duration::from_secs(2))
                    .and_then(|line| parse_status_response(&line))
                    .and_then(|body| serde_json::from_str(&body).map_err(|error| victus_core::HubError::new(error.to_string()))) {
                    tx.send(BackgroundEvent::State(value));
                }
            }
        }
    });
}

fn control_result(session: &Session, kind: ControlRequest, result: Result<String, String>) {
    let success = result.is_ok();
    session.model.borrow_mut().status = result.as_ref().err().cloned().unwrap_or_default();
    match kind {
        ControlRequest::Profile(index) => {
            session.profile_busy.set(false);
            for button in &session.built.home.profile_buttons { button.set_sensitive(true); }
            if success && session.model.borrow().offline {
                session.model.borrow_mut().profile = index;
                refresh_frequency(session);
            }
            refresh_view(session);
        }
        ControlRequest::Power(policy) => {
            session.built.power.apply.set_sensitive(true);
            session.built.power.enabled.set_sensitive(true);
            if success {
                *session.applied_power.borrow_mut() = policy.clone();
                session.model.borrow_mut().state.power = policy;
            } else {
                session.suppress.set(true);
                session.built.power.enabled.set_active(session.model.borrow().state.power.enabled);
                session.suppress.set(false);
            }
            refresh_view(session);
        }
        ControlRequest::Frequency(minimum, maximum) => {
            session.built.power.freq_apply.set_sensitive(true);
            if success { session.model.borrow_mut().state.cpu_frequency = Some((minimum, maximum)); session.applied_freq.set(Some((minimum, maximum))); }
            session.last_frequency.borrow_mut().take();
            refresh_frequency(session);
        }
        ControlRequest::Undervolt(core, cache) => {
            session.built.power.uv_apply.set_sensitive(true);
            if !success { session.built.power.uv_status.set_text(result.as_ref().err().map(String::as_str).unwrap_or("Undervolt failed")); show_status(session); return; }
        session.applied_uv.set(Some((core, cache)));
        let path = {
            let mut model = session.model.borrow_mut();
            model.host.undervolt = (core, cache);
            if model.offline { PathBuf::new() } else { model.host.conf_path.clone() }
        };
        if !path.as_os_str().is_empty() {
            let existing = std::fs::read_to_string(&path).unwrap_or_default();
            if let Some(parent) = path.parent() { let _ = std::fs::create_dir_all(parent); }
            if let Err(error) = std::fs::write(&path, crate::upsert_undervolt(&existing, core, cache)) {
                session.built.power.uv_status.set_text(&format!("Undervolt applied; could not save offsets: {error}"));
                return;
            }
        }
        session.built.power.uv_status.set_text("Undervolt applied.");
        }
    }
    show_status(session);
}

fn power_form(session: &Session) -> PowerPolicy {
    let power = &session.built.power;
    PowerPolicy {
        enabled: power.enabled.is_active(),
        stapm_limit: victus_core::clamp_power_limit(paint::round_i32(power.stapm.scale.value()) * 1000),
        fast_limit: victus_core::clamp_power_limit(paint::round_i32(power.fast.scale.value()) * 1000),
        slow_limit: victus_core::clamp_power_limit(paint::round_i32(power.slow.scale.value()) * 1000),
        tctl_temp: victus_core::clamp_tctl_temp(paint::round_i32(power.tctl.scale.value())),
        reapply_seconds: victus_core::clamp_reapply_seconds(paint::round_i32(power.reapply.scale.value())),
    }
}

fn confirm_mux(session: &Rc<Session>, index: i32, label: String) {
    if session.model.borrow().host.mux_index == index {
        return;
    }
    let dialog = libadwaita::AlertDialog::new(
        Some(&format!("Switch to {label}?")),
        Some("You must restart for this change to take effect."),
    );
    dialog.add_response("cancel", "Cancel");
    dialog.add_response("apply", "Apply");
    dialog.set_response_appearance("apply", libadwaita::ResponseAppearance::Suggested);
    dialog.set_default_response(Some("cancel"));
    dialog.set_close_response("cancel");
    let weak = Rc::downgrade(session);
    dialog.connect_response(None, move |_, response| {
        if response != "apply" {
            return;
        }
        let Some(session) = weak.upgrade() else { return };
        if commit(&session, &format!("gpu-mux-mode\t{index}")) {
            session.model.borrow_mut().host.mux_index = index;
            refresh_view(&session);
        }
    });
    dialog.present(Some(&session.window));
}

fn schedule_fan_if_custom(session: &Session) {
    let custom = FanMode::from_config(&session.model.borrow().state.fan) == FanMode::Custom;
    if custom {
        schedule(session, false);
    }
}

fn begin_fan_drag(session: &Session, x: f64, y: f64) {
    session.built.fans.chart.grab_focus();
    let (width, height, temp_max) = chart_size(session);
    let plot = paint::Plot::new(width, height, temp_max);
    let mut model = session.model.borrow_mut();
    let profile = model.profile;
    let cpu = session.curve_cpu.get();
    let points = curve_points_mut(&mut model.state.fan, profile, cpu);
    let index = if let Some(index) = plot.nearest(points, x, y) {
        Some(index)
    } else {
        let (temp, speed) = plot.temp_speed(x, y);
        paint::insert_point(points, temp, speed)
    };
    drop(model);
    session.drag.set(index);
    session.selected_point.set(index);
    session.built.fans.chart.queue_draw();
}

fn update_fan_drag(session: &Session, x: f64, y: f64) {
    let Some(index) = session.drag.get() else { return };
    let (width, height, temp_max) = chart_size(session);
    let plot = paint::Plot::new(width, height, temp_max);
    let (temp, speed) = plot.temp_speed(x, y);
    let mut model = session.model.borrow_mut();
    let profile = model.profile;
    let cpu = session.curve_cpu.get();
    let points = curve_points_mut(&mut model.state.fan, profile, cpu);
    paint::move_point(points, index, temp, speed);
    drop(model);
    session.built.fans.chart.queue_draw();
}

fn end_fan_drag(session: &Session) {
    if session.drag.get().is_none() {
        return;
    }
    session.drag.set(None);
    normalize_curve(session);
    schedule_fan_if_custom(session);
    session.built.fans.chart.queue_draw();
}

fn delete_fan_point(session: &Session, x: f64, y: f64) {
    let (width, height, temp_max) = chart_size(session);
    let plot = paint::Plot::new(width, height, temp_max);
    let mut model = session.model.borrow_mut();
    let profile = model.profile;
    let cpu = session.curve_cpu.get();
    let points = curve_points_mut(&mut model.state.fan, profile, cpu);
    let Some(index) = plot.nearest(points, x, y) else { return };
    let last = points.len().saturating_sub(1);
    if index == 0 || index == last || points.len() <= 2 {
        return;
    }
    points.remove(index);
    session.selected_point.set(None);
    drop(model);
    session.drag.set(None);
    normalize_curve(session);
    schedule_fan_if_custom(session);
    session.built.fans.chart.queue_draw();
}

fn delete_selected_point(session: &Session) {
    let Some(index) = session.selected_point.get() else { return };
    let mut model = session.model.borrow_mut();
    let profile = model.profile;
    let points = curve_points_mut(&mut model.state.fan, profile, session.curve_cpu.get());
    if index == 0 || index + 1 >= points.len() { return; }
    points.remove(index);
    drop(model);
    session.selected_point.set(None);
    normalize_curve(session);
    schedule_fan_if_custom(session);
    session.built.fans.chart.queue_draw();
}

fn normalize_curve(session: &Session) {
    let mut model = session.model.borrow_mut();
    let profile = model.profile;
    let cpu = session.curve_cpu.get();
    let temp_max = if cpu { CPU_TEMP_MAX_C } else { GPU_TEMP_MAX_C };
    let points = curve_points_mut(&mut model.state.fan, profile, cpu);
    let normalized = normalize_fan_points(std::mem::take(points), temp_max);
    *points = normalized;
}

fn chart_size(session: &Session) -> (f64, f64, i32) {
    let width = f64::from(session.built.fans.chart.width()).max(1.0);
    let height = f64::from(session.built.fans.chart.height()).max(1.0);
    let temp_max = if session.curve_cpu.get() { CPU_TEMP_MAX_C } else { GPU_TEMP_MAX_C };
    (width, height, temp_max)
}

fn save_shortcut(session: &Session, mods: &[i32], key: i32) {
    let body = serde_json::json!({ "mods": mods, "key": key });
    let request = format!("program-shortcut\t{body}");
    if !commit(session, &request) {
        show_shortcut(session, None);
        return;
    }
    let path = {
        let mut model = session.model.borrow_mut();
        model.host.shortcut_mods = mods.to_vec();
        model.host.shortcut_key = key;
        if model.offline { PathBuf::new() } else { model.host.conf_path.clone() }
    };
    if !path.as_os_str().is_empty() {
        let existing = std::fs::read_to_string(&path).unwrap_or_default();
        let updated = upsert_program_shortcut(&existing, mods, key);
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                let _ = std::fs::create_dir_all(parent);
            }
        }
        let _ = std::fs::write(path, updated);
    }
    show_shortcut(session, None);
}

fn show_shortcut(session: &Session, override_text: Option<&str>) {
    if let Some(text) = override_text {
        session.built.settings.shortcut.set_text(text);
        return;
    }
    let model = session.model.borrow();
    session.built.settings.shortcut.set_text(&keybind_label(&model.host.shortcut_mods, model.host.shortcut_key));
}

fn quit(session: &Session) {
    session.stop.store(true, Ordering::Relaxed);
    session.visible.store(false, Ordering::Relaxed);
    notify_sensors(session);
    if let Some(id) = session.timer.borrow_mut().take() { id.remove(); }
    session.charts.close_all();
    session.tray.borrow_mut().take();
    session.window.set_hide_on_close(false);
    session.window.close();
    if let Some(app) = session.window.application() {
        app.quit();
    }
}

fn on_tick(session: &Session) {
    let latest = {
        let mut slot = session.sensor_rx.borrow_mut();
        let mut latest = None;
        if let Some(rx) = slot.as_mut() {
            while let Ok(snapshot) = rx.try_recv() {
                latest = Some(snapshot);
            }
        }
        latest
    };
    if let Some(SensorUpdate { snapshot, keys, frequency }) = latest {
        let page = session.page.load(Ordering::Relaxed);
        if victus_core::keys_for_page(page).iter().all(|key| keys.iter().any(|requested| requested == key)) {
            apply_snapshot(session, snapshot, &keys);
            if page == 1 { if let Some(frequency) = frequency { apply_frequency_view(session, frequency); } }
        }
    }
    if take_due(&session.light_at) {
        flush_lighting(session);
    }
    if take_due(&session.fan_at) {
        flush_fan(session);
    }
    if session.visible.load(Ordering::Relaxed) {
        tick_animation(session);
    }
    if session.model.borrow().offline && Instant::now() >= session.preview_at.get() {
        session.preview_at.set(Instant::now() + Duration::from_secs(1));
        let step = session.preview_step.get();
        session.charts.preview(step);
        session.preview_step.set(step.saturating_add(1));
    }
    for action in tray::Tray::poll() {
        match action {
            tray::Action::Show => reveal(session),
            tray::Action::Toggle => {
                if session.visible.load(Ordering::Relaxed) {
                    session.charts.hide_all();
                    session.window.set_visible(false);
                    session.visible.store(false, Ordering::Relaxed);
                    notify_sensors(session);
                } else {
                    reveal(session);
                }
            }
            tray::Action::Quit => quit(session),
            tray::Action::Profile(index) => select_profile(session, index),
            tray::Action::Fan(key) => {
                if let Some(mode) = fan_mode_from_key(&key) {
                    apply_fan_mode(session, mode);
                }
            }
        }
    }
    show_status(session);
}

fn reveal(session: &Session) {
    session.window.unminimize();
    session.window.present();
    session.charts.show_all();
    session.visible.store(true, Ordering::Relaxed);
    notify_sensors(session);
    arm_tick(session);
}

fn deliver_events(session: &Rc<Session>) {
    let events = {
        let rx = session.events_rx.borrow();
        let mut events = Vec::new();
        while let Ok(event) = rx.try_recv() {
            events.push(event);
        }
        events
    };
    for event in events {
        match event {
            BackgroundEvent::Release(release) => show_release(session, release),
            BackgroundEvent::Diagnostics(path) => show_diagnostics(session, path),
            BackgroundEvent::State(value) => apply_remote_state(session, &value),
            BackgroundEvent::Frequency(result) => apply_frequency_view(session, result),
            BackgroundEvent::Control(kind, result) => control_result(session, kind, result),
        }
    }
}

fn apply_snapshot(session: &Session, snapshot: SensorSnapshot, requested: &[String]) {
    let profile = session.model.borrow().profile;
    let signature = extra_signature(&snapshot.extra_sensors);
    if session.page.load(Ordering::Relaxed) == 4 && signature != *session.extra_sig.borrow() {
        *session.extra_sig.borrow_mut() = signature;
        pages::clear_list(&session.built.sensors.list);
        let rows = pages::fill_sensors(&session.built.sensors.list, &snapshot.extra_sensors, &session.built.sensors.collapsed);
        *session.sensor_rows.borrow_mut() = rows;
    }
    {
        let mut stats = session.stats.borrow_mut();
        for row in session.sensor_rows.borrow().iter() {
            if !requested.iter().any(|key| key == request_key_for_graph(&row.key)) && row.key != "profile" { continue; }
            row.row.set_tooltip_text(Some(&reading_source(&snapshot, &row.key)));
            if row.key == "profile" {
                row.current.set_text(mode_name(profile));
                continue;
            }
            let Some(value) = sample_value(&snapshot, &row.key) else {
                row.current.set_text(&current_text(&snapshot, &row.key, profile));
                continue;
            };
            if row.graphable && session.page.load(Ordering::Relaxed) == 4 {
                let stat = stats.entry(row.key.clone()).or_insert(Running { min: value, max: value, sum: 0.0, count: 0 });
                stat.min = stat.min.min(value);
                stat.max = stat.max.max(value);
                stat.sum += value;
                stat.count = stat.count.saturating_add(1);
                row.maximum.set_text(&format_stat_unit(stat.max, &row.unit));
                row.minimum.set_text(&format_stat_unit(stat.min, &row.unit));
                let count = f64::from(stat.count.max(1));
                row.average.set_text(&format_stat_unit(stat.sum / count, &row.unit));
            }
            row.current.set_text(&current_text(&snapshot, &row.key, profile));
            if row.unit == "°C" && value > 95.0 { row.current.add_css_class("hot"); } else { row.current.remove_css_class("hot"); }
        }
    }
    session.charts.apply(|key| requested.iter().any(|requested| requested == request_key_for_graph(key)).then(|| graph::Sample {
        value: sample_value(&snapshot, key),
        text: current_text(&snapshot, key, profile),
        source: reading_source(&snapshot, key),
        ram_total: snapshot.ram_total_gb,
    }));
    merge_snapshot(&mut session.model.borrow_mut().snapshot, &snapshot, requested);
    refresh_view(session);
}

fn flush_lighting(session: &Session) {
    let request = {
        let mut model = session.model.borrow_mut();
        let zones = model.zones;
        model.state.lighting = normalize_lighting_settings(&model.state.lighting, zones);
        lighting_request(&model.state.lighting)
    };
    let _ = commit(session, &request);
    refresh_view(session);
}

fn flush_fan(session: &Session) {
    let request = {
        let model = session.model.borrow();
        format!("fan-config\t{}", config_to_value(&model.state.fan))
    };
    let _ = commit(session, &request);
    refresh_view(session);
}

fn tick_animation(session: &Session) {
    let (enabled, effect, speed) = {
        let model = session.model.borrow();
        (model.state.lighting.enabled, model.state.lighting.effect.clone(), model.state.lighting.speed)
    };
    if matches!(session.page.load(Ordering::Relaxed), 0 | 3) && enabled && effect_is_animated(&effect) {
        session.anim.set(session.anim.get() + step_increment(speed, 0.05));
        session.built.keyboard.visual.queue_draw();
        session.built.home.mini.queue_draw();
    }
}

fn refresh_view(session: &Session) {
    let model = session.model.borrow();
    let profile = model.profile;
    let mode = FanMode::from_config(&model.state.fan);
    let keyboard = model.host.keyboard;
    let enabled = model.state.lighting.enabled;
    let effect = model.state.lighting.effect.clone();
    session.built.home.title.set_text(mode_name(profile));
    session.built.home.head_status.set_text(&ram_status(&model.snapshot));
    session.built.home.cpu_value.set_text(&temp_text(model.snapshot.cpu_temp_c));
    session.built.home.cpu_caption.set_text(&cpu_caption(&model.snapshot));
    session.built.home.gpu_value.set_text(&temp_text(model.snapshot.gpu_temp_c));
    session.built.home.gpu_caption.set_text(&gpu_caption(&model.snapshot));
    session.built.home.cpu_fan.set_text(&rpm_text(&model.snapshot.cpu_fan.value));
    session.built.home.gpu_fan.set_text(&rpm_text(&model.snapshot.gpu_fan.value));
    session.built.home.power_sub.set_text(&power_subtitle(&model));
    session.built.home.light_sub.set_text(&light_subtitle(&model));
    session.built.home.light_row.set_visible(keyboard);
    session.built.home.gpu_name.set_visible(!(model.state.disable_nvidia_queries && model.profile == 0));
    session.built.home.footer_right.set_text(&power_status_line(&model.host.power_supply));
    session.built.fans.head_status.set_text(&format!(
        "{} · CPU {} · GPU {}",
        mode_name(profile),
        model.snapshot.cpu_fan.value,
        model.snapshot.gpu_fan.value
    ));
    session.built.fans.info.set_text(fan_description(mode));
    session.built.power.head_status.set_text(&power_head(&model.snapshot));
    let mux_index = model.host.mux.iter().position(|choice| choice.index == model.host.mux_index);
    drop(model);
    if let Some(icon) = session.tray.borrow().as_ref() {
        icon.sync(profile, fan_mode_key(mode));
    }
    widgets::mark(&session.built.home.profile_buttons, usize::try_from(profile.clamp(0, 2)).ok());
    mark_key(&session.built.home.fan_buttons, &session.built.home.fan_keys, fan_mode_key(mode));
    mark_key(&session.built.fans.mode_buttons, &session.built.fans.mode_keys, fan_mode_key(mode));
    session.built.home.curve_row.set_visible(mode == FanMode::Custom);
    session.built.fans.editor.set_visible(mode == FanMode::Custom);
    session.built.fans.info.set_visible(mode != FanMode::Custom);
    session.built.fans.cpu_link.set_css_classes(if session.curve_cpu.get() { &["linkish", "selection-link", "on"] } else { &["linkish", "selection-link"] });
    session.built.fans.gpu_link.set_css_classes(if session.curve_cpu.get() { &["linkish", "selection-link"] } else { &["linkish", "selection-link", "on"] });
    widgets::mark(&session.built.home.mux_buttons, mux_index);
    let effect_index = if enabled {
        session.built.keyboard.effect_ids.iter().position(|id| id == &effect)
    } else {
        Some(0)
    };
    widgets::mark(&session.built.keyboard.effect_buttons, effect_index);
    let zone_index = if session.zone_target.get() < 0 {
        session.built.keyboard.zone_buttons.len().checked_sub(1)
    } else {
        usize::try_from(session.zone_target.get()).ok()
    };
    widgets::mark(&session.built.keyboard.zone_buttons, zone_index);
    let animated = enabled && effect_is_animated(&effect);
    session.built.keyboard.color_box.set_visible(enabled && !ignores_color(&effect));
    session.built.keyboard.zone_row.set_visible(enabled && !ignores_color(&effect) && session.model.borrow().zones > 1);
    session.built.keyboard.head_status.set_text(&if enabled { format!("{} zone{} · {}", session.model.borrow().zones, if session.model.borrow().zones == 1 { "" } else { "s" }, effect.replace('_', " ")) } else { "off".into() });
    session.built.keyboard.color2_box.set_visible(enabled && needs_color2(&effect));
    session.built.keyboard.speed_row.set_visible(animated);
    if session.accent_profile.get() != Some(profile) {
        let color = accent_hex(profile);
        session.accent.load_from_string(&format!("window.victus button.seg-btn.on {{ background: {color}; }} window.victus .linkish.on, window.victus .accent {{ color: {color}; }} window.victus button.linkish.on:not(.selection-link):hover:not(:disabled) {{ color: mix({color}, #ffffff, 0.22); }} window.victus button.selection-link.on {{ color: #ffffff; border-bottom-color: {color}; }} window.victus scale highlight, window.victus switch:checked {{ background: {color}; }}"));
        session.accent_profile.set(Some(profile));
    }
    session.built.home.mini.queue_draw();
    session.built.keyboard.visual.queue_draw();
    session.built.keyboard.chip.queue_draw();
    session.built.keyboard.chip2.queue_draw();
    session.built.keyboard.hue.queue_draw();
    session.built.keyboard.shade.queue_draw();
    session.built.fans.chart.queue_draw();
    session.built.sidebar.queue_draw();
    show_status(session);
    arm_tick(session);
}

fn paint_chart(session: &Session, cr: &gtk4::cairo::Context, width: i32, height: i32) {
    let model = session.model.borrow();
    let cpu = session.curve_cpu.get();
    let temp_max = if cpu { CPU_TEMP_MAX_C } else { GPU_TEMP_MAX_C };
    let points = curve_points(&model.state.fan, model.profile, cpu);
    let accent = if cpu { accent_rgb(model.profile) } else { paint::unit_rgb("#E2572C") };
    let current = None;
    let selected = session.selected_point.get();
    paint::chart(cr, f64::from(width), f64::from(height), temp_max, points, accent, selected, session.fan_hover.get(), current);
}

fn commit(session: &Session, request: &str) -> bool {
    let ok = {
        let mut model = session.model.borrow_mut();
        dispatch(&mut model, request)
    };
    show_status(session);
    ok
}

fn dispatch(model: &mut Model, request: &str) -> bool {
    let socket = connects_to_socket(model).map(Path::to_path_buf);
    let Some(socket) = socket else {
        model.status = "Offline preview".into();
        return true;
    };
    match transact(&socket, request, Duration::from_secs(2)).and_then(|response| parse_status_response(&response)) {
        Ok(_) => {
            model.status.clear();
            true
        }
        Err(error) => {
            model.status = error.to_string();
            false
        }
    }
}

fn show_status(session: &Session) {
    let status = session.model.borrow().status.clone();
    if !status.is_empty() && status != "Offline preview" {
        let mut log = session.log.borrow_mut();
        if log.back() != Some(&status) { log.push_back(status.clone()); while log.len() > 80 { log.pop_front(); } }
    }
    session.built.status.set_visible(!status.is_empty());
    session.built.status.set_text(&status);
}

fn schedule(session: &Session, lighting: bool) {
    let slot = if lighting { &session.light_at } else { &session.fan_at };
    slot.set(Some(Instant::now() + DEBOUNCE));
    arm_tick(session);
}

fn take_due(slot: &Cell<Option<Instant>>) -> bool {
    match slot.get() {
        Some(deadline) if Instant::now() >= deadline => {
            slot.set(None);
            true
        }
        _ => false,
    }
}

fn modifier_codes(state: ModifierType) -> Vec<i32> {
    let mut mods = Vec::new();
    if state.contains(ModifierType::CONTROL_MASK) {
        mods.push(KEY_LEFTCTRL);
    }
    if state.contains(ModifierType::SHIFT_MASK) {
        mods.push(KEY_LEFTSHIFT);
    }
    if state.contains(ModifierType::ALT_MASK) {
        mods.push(KEY_LEFTALT);
    }
    if state.contains(ModifierType::SUPER_MASK) || state.contains(ModifierType::META_MASK) {
        mods.push(KEY_LEFTMETA);
    }
    mods
}

fn fan_mode_from_key(key: &str) -> Option<FanMode> {
    match key {
        "auto" => Some(FanMode::Auto),
        "smart" => Some(FanMode::Smart),
        "max" => Some(FanMode::Max),
        "custom" => Some(FanMode::Custom),
        _ => None,
    }
}

fn fan_mode_key(mode: FanMode) -> &'static str {
    match mode {
        FanMode::Auto => "auto",
        FanMode::Smart => "smart",
        FanMode::Max => "max",
        FanMode::Custom => "custom",
    }
}

fn fan_description(mode: FanMode) -> &'static str {
    match mode {
        FanMode::Auto => "Automatic fan control follows the firmware's own fan curve.",
        FanMode::Smart => "Smart fan control follows the built-in curve with faster smoothing.",
        FanMode::Max => "Both fans run at full speed. Select another mode to return to automatic or curve-based control.",
        FanMode::Custom => "Select Custom to edit this profile's CPU and GPU fan curves.",
    }
}

fn mode_name(profile: i32) -> &'static str {
    match profile {
        0 => "Eco",
        2 => "Performance",
        _ => "Balanced",
    }
}

fn accent_hex(profile: i32) -> &'static str {
    match profile {
        0 => "#2FBF8F",
        2 => "#E2572C",
        _ => "#3F8CFF",
    }
}

fn accent_rgb(profile: i32) -> (f64, f64, f64) {
    paint::unit_rgb(accent_hex(profile))
}

fn curve_points(fan: &FanConfig, profile: i32, cpu: bool) -> &[FanPoint] {
    let index = usize::try_from(profile.clamp(0, 2)).unwrap_or(1);
    match fan.profiles.get(index) {
        Some(slot) if cpu => &slot.cpu_points,
        Some(slot) => &slot.gpu_points,
        None => &[],
    }
}

fn curve_points_mut(fan: &mut FanConfig, profile: i32, cpu: bool) -> &mut Vec<FanPoint> {
    let index = usize::try_from(profile.clamp(0, 2)).unwrap_or(1);
    let slot = fan.profiles.get_mut(index).expect("three fan profiles");
    if cpu { &mut slot.cpu_points } else { &mut slot.gpu_points }
}

fn power_subtitle(model: &Model) -> String {
    if !model.state.power.enabled {
        return "Limits off".into();
    }
    if model.host.intel {
        format!("PL1 {} W · PL2 {} W", model.state.power.slow_limit / 1000, model.state.power.fast_limit / 1000)
    } else {
        format!("STAPM {} W", model.state.power.stapm_limit / 1000)
    }
}

fn light_subtitle(model: &Model) -> String {
    if !model.state.lighting.enabled {
        return "Off".into();
    }
    let name = title_effect(&model.state.lighting.effect);
    if model.zones > 1 {
        format!("{name} · {} zones", model.zones)
    } else {
        name
    }
}

fn title_effect(effect: &str) -> String {
    effect
        .split(['_', ' '])
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut chars = part.chars();
            match chars.next() {
                Some(first) => first.to_ascii_uppercase().to_string() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn apply_remote_state(session: &Session, value: &serde_json::Value) {
    let instance = value.get("instance").and_then(serde_json::Value::as_str).unwrap_or("");
    let revision = value.get("revision").and_then(serde_json::Value::as_u64).unwrap_or(0);
    if instance == *session.state_instance.borrow() && revision < session.state_revision.get() { return; }
    *session.state_instance.borrow_mut() = instance.to_owned();
    session.state_revision.set(revision);
    let old = session.model.borrow().state.clone();
    let profile = session.model.borrow().profile;
    let pristine_power = power_form(session) == *session.applied_power.borrow();
    let old_frequency = old.cpu_frequency;
    {
        let mut model = session.model.borrow_mut();
        model.hydrate(value);
        if session.light_at.get().is_some() { model.state.lighting = old.lighting.clone(); }
        if session.fan_at.get().is_some() || session.drag.get().is_some() { model.state.fan = old.fan.clone(); }
    }
    let model = session.model.borrow();
    let remote_power = model.state.power.clone();
    session.suppress.set(true);
    if pristine_power {
        session.built.power.enabled.set_active(remote_power.enabled);
        for (scale, value) in [(&session.built.power.stapm.scale, remote_power.stapm_limit / 1000), (&session.built.power.fast.scale, remote_power.fast_limit / 1000),
            (&session.built.power.slow.scale, remote_power.slow_limit / 1000), (&session.built.power.tctl.scale, remote_power.tctl_temp), (&session.built.power.reapply.scale, remote_power.reapply_seconds)] { scale.set_value(f64::from(value)); }
    }
    *session.applied_power.borrow_mut() = remote_power;
    session.built.settings.battery.set_active(model.state.battery_power_save);
    session.built.settings.nvidia.set_active(model.state.disable_nvidia_queries);
    session.built.settings.hardware.set_active(model.state.hardware_shortcuts);
    if session.light_at.get().is_none() && model.state.lighting != old.lighting {
        session.built.keyboard.brightness.set_value(f64::from(model.state.lighting.brightness));
        session.built.keyboard.speed.set_value(f64::from(model.state.lighting.speed));
        session.built.keyboard.idle_enabled.set_active(model.state.lighting.idle_timeout > 0);
        session.built.keyboard.idle.set_sensitive(model.state.lighting.idle_timeout > 0);
        if model.state.lighting.idle_timeout > 0 { session.built.keyboard.idle.set_value(f64::from(model.state.lighting.idle_timeout)); }
    }
    session.built.fans.response.set_selected(u32::from(model.state.fan.curve_response == CURVE_RESPONSE_AGGRESSIVE));
    session.built.fans.min_change.set_value(model.state.fan.min_fan_change_pct);
    let frequency_changed = model.state.cpu_frequency != old_frequency || model.profile != profile;
    if model.profile != profile { session.selected_point.set(None); session.drag.set(None); session.fan_hover.set(None); }
    drop(model);
    session.suppress.set(false);
    if session.light_at.get().is_none() { sync_color_entries(session); }
    if frequency_changed { refresh_frequency(session); }
    refresh_view(session);
}

fn apply_frequency_view(session: &Session, result: Result<crate::FrequencyWindow, String>) {
    let Ok(window) = result else {
        session.built.power.freq_sliders.set_visible(false);
        session.built.power.freq_note.set_text(&result.err().unwrap_or_default());
        session.model.borrow_mut().host.frequency = None;
        session.last_frequency.borrow_mut().take();
        return;
    };
    if session.last_frequency.borrow().as_ref() == Some(&window) { return; }
    let pending = (paint::round_i32(session.built.power.freq_min.scale.value()), paint::round_i32(session.built.power.freq_max.scale.value()));
    let dirty = session.applied_freq.get().is_some_and(|applied| pending != applied);
    let (minimum, maximum) = if dirty { (pending.0.clamp(window.lower, window.upper), pending.1.clamp(window.lower, window.upper)) } else { (window.minimum, window.maximum) };
    session.suppress.set(true);
    for (slider, value) in [(&session.built.power.freq_min, minimum.min(maximum)), (&session.built.power.freq_max, maximum)] {
        configure_scale(&slider.scale, f64::from(window.lower), f64::from(window.upper), 1000.0);
        slider.value.set_range(f64::from(window.lower) / 1000.0, f64::from(window.upper) / 1000.0);
        slider.scale.set_value(f64::from(value));
        slider.value.set_value(f64::from(value) / 1000.0);
    }
    session.suppress.set(false);
    session.applied_freq.set(Some((window.minimum, window.maximum)));
    session.built.power.freq_note.set_text(&format!("Applies to all {} CPU policies. {}Hardware range: {:.3}–{:.3} MHz.", window.policies,
        if window.mixed { "Current limits differ between policies. " } else { "" }, f64::from(window.lower) / 1000.0, f64::from(window.upper) / 1000.0));
    session.built.power.freq_sliders.set_visible(true);
    *session.last_frequency.borrow_mut() = Some(window.clone());
    session.model.borrow_mut().host.frequency = Some(window);
}

fn extra_signature(extras: &[ExtraSensor]) -> String {
    extras.iter().map(|extra| format!("{}:{}", extra.group, extra.key)).collect::<Vec<_>>().join("\n")
}

fn finite_i32(value: f64) -> Option<i32> {
    if !value.is_finite() {
        return None;
    }
    let rounded = value.round();
    if rounded < f64::from(i32::MIN) || rounded > f64::from(i32::MAX) {
        return None;
    }
    Some(rounded as i32)
}

fn mark_key(buttons: &[gtk4::Button], keys: &[String], key: &str) {
    widgets::mark(buttons, keys.iter().position(|item| item == key));
}

fn set_scale(scale: &gtk4::Scale, value: f64) {
    scale.set_value(value);
}

fn configure_scale(scale: &gtk4::Scale, lower: f64, upper: f64, step: f64) {
    let adjustment = scale.adjustment();
    adjustment.set_lower(lower);
    adjustment.set_upper(upper);
    adjustment.set_step_increment(step);
    adjustment.set_page_increment(step);
}

#[cfg(test)]
mod regression_tests {
    use super::*;

    #[test]
    #[ignore = "requires a GTK display; run under Xvfb"]
    fn gtk_controls_preserve_pending_edits_and_wire_lighting_and_navigation() {
        gtk4::init().unwrap();
        libadwaita::init().unwrap();
        let app = libadwaita::Application::new(None, ApplicationFlags::NON_UNIQUE);
        app.register(None::<&gtk4::gio::Cancellable>).unwrap();
        let model = Rc::new(RefCell::new(Model::offline(4)));
        let (_, wake) = UnixStream::pair().unwrap();
        let session = build_ui(
            &app, &model, &Arc::new(AtomicUsize::new(0)), &Arc::new(AtomicBool::new(true)),
            &Arc::new(AtomicBool::new(false)), &Rc::new(RefCell::new(None)),
            &Arc::new(Mutex::new(Vec::new())), &Arc::new(wake), &Rc::new(RefCell::new(None)),
            &Arc::new((Mutex::new(0), Condvar::new())),
        );

        session.built.home.curve.emit_clicked();
        assert_eq!(model.borrow().page, 2);
        session.selected_point.set(Some(1));
        session.built.fans.gpu_link.emit_clicked();
        assert!(!session.curve_cpu.get());
        assert_eq!(session.selected_point.get(), None);
        session.built.fans.cpu_link.emit_clicked();
        assert!(session.curve_cpu.get());

        session.built.home.light_row.emit_clicked();
        assert_eq!(model.borrow().page, 3);
        let static_index = session.built.keyboard.effect_ids.iter().position(|id| id == "static").unwrap();
        session.built.keyboard.effect_buttons[static_index].emit_clicked();
        session.built.keyboard.brightness.set_value(128.0);
        session.built.keyboard.hex.set_text("123456");
        session.built.keyboard.hex2.set_text("ABCDEF");
        assert_eq!(model.borrow().state.lighting.brightness, 128);
        assert_eq!(model.borrow().state.lighting.zone_colors, vec!["#123456"; 4]);
        assert_eq!(model.borrow().state.lighting.color2, "#ABCDEF");
        session.built.keyboard.zone_buttons[0].emit_clicked();
        session.built.keyboard.hex.set_text("654321");
        assert_eq!(model.borrow().state.lighting.zone_colors, ["#654321", "#123456", "#123456", "#123456"]);

        let mut remote = model.borrow().state.clone();
        remote.lighting.brightness = 255;
        remote.power.stapm_limit = 40_000;
        session.built.power.stapm.scale.set_value(35.0);
        let mut value = victus_core::state_to_value(&remote);
        value["instance"] = "test-daemon".into();
        value["revision"] = 2.into();
        apply_remote_state(&session, &value);
        assert_eq!(model.borrow().state.lighting.brightness, 128, "pending lighting edit was overwritten");
        assert_eq!(power_form(&session).stapm_limit, 35_000, "unsaved power form was overwritten");
        assert_eq!(session.applied_power.borrow().stapm_limit, 40_000);

        session.light_at.set(None);
        apply_remote_state(&session, &value);
        assert_eq!(model.borrow().state.lighting.brightness, 255);
        assert_eq!(paint::round_i32(session.built.keyboard.brightness.value()), 255);
        value["revision"] = 1.into();
        value["power"]["stapm_limit"] = 50_000.into();
        apply_remote_state(&session, &value);
        assert_eq!(session.applied_power.borrow().stapm_limit, 40_000, "stale state was accepted");
        session.sensor_menu.borrow_mut().take().unwrap().unparent();
        quit(&session);
    }

    #[test]
    fn frequency_range_is_the_intersection_of_all_policies() {
        let policy = |lower, upper, minimum, maximum| victus_hw::FrequencyPolicy {
            path: PathBuf::new(), hardware_min: lower, hardware_max: upper, minimum, maximum,
        };
        let policies = [policy(400_000, 5_000_000, 400_000, 5_000_000), policy(800_000, 3_800_000, 800_000, 3_800_000)];
        let common = frequency_window(&policies).unwrap();
        assert_eq!((common.lower, common.upper, common.minimum, common.maximum), (800_000, 3_800_000, 800_000, 3_800_000));
        assert!(common.mixed);
        assert!(frequency_window(&[policy(400_000, 700_000, 400_000, 700_000), policy(800_000, 3_800_000, 800_000, 3_800_000)]).is_err());
    }

}
