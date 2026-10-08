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
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use gtk4::gdk::{Key, ModifierType};
use gtk4::gio::ApplicationFlags;
use gtk4::glib::{self, ControlFlow, Propagation};
use gtk4::prelude::*;
use gtk4::DrawingArea;
use libadwaita::prelude::*;
use victus_core::{
    acpi_error_line, config_to_value, effect_is_animated, fan_mode_steps, filter_journal_lines, is_modifier,
    kernel_module_error_line, keybind_label, lighting_frames, normalize_fan_points, normalize_lighting_settings,
    parse_sensors_response, parse_status_response, power_to_value, step_increment, transact, validate_frequency,
    validate_shortcut, validate_undervolt, ExtraSensor, FanConfig, FanMode, FanPoint,
    request_key_for_graph, SensorSnapshot, CPU_TEMP_MAX_C, CURVE_RESPONSE_AGGRESSIVE, CURVE_RESPONSE_SMOOTH, GPU_TEMP_MAX_C,
    KEY_LEFTALT, KEY_LEFTCTRL, KEY_LEFTMETA, KEY_LEFTSHIFT, PROGRAM_VERSION,
};

use crate::{
    connects_to_socket, fan_requests, lighting_request, parse_release_tag, power_status_line,
    update_choice, update_shell, upsert_program_shortcut, Model, UpdateChoice, RELEASE_URL,
};

use self::pages::SensorRow;

const PAGE_NAMES: [&str; 6] = ["home", "power", "fans", "keyboard", "sensors", "settings"];
const DEBOUNCE: Duration = Duration::from_millis(250);

const CSS: &str = r#"
window.victus {
  background: #161616;
  color: #ededed;
  font-family: "IBM Plex Sans", Cantarell, sans-serif;
  font-size: 13px;
}
window.victus .victus-page,
window.victus .victus-scroll,
window.victus viewport,
window.victus list,
window.victus list row,
window.victus row {
  background: #161616;
  color: #ededed;
}
window.victus .title {
  font-size: 15px;
  font-weight: 600;
}
window.victus .metric {
  font-size: 42px;
  font-weight: 600;
}
window.victus .unit {
  color: #969696;
  font-size: 13px;
}
window.victus .temp-unit { font-size: 18px; }
window.victus .rpm-unit { font-size: 15px; }
window.victus .hot { color: #ff4444; }
window.victus .caption,
window.victus .sub,
window.victus .row-sub {
  color: #969696;
  font-size: 12px;
}
window.victus .row-title {
  font-size: 14px;
  font-weight: 400;
}
window.victus .sensor-group-title { font-weight: 700; }
window.victus .heartbeat { font-size: 8px; }
window.victus .mono {
  font-family: "IBM Plex Mono", monospace;
  color: #8a8a8a;
  font-size: 12px;
}
window.victus .ok { color: #4ac06c; }
window.victus .accent { color: #3f8cff; }
window.victus .hairline { background: #2c2c2c; }
window.victus .seg {
  background: #0f0f0f;
  border-radius: 10px;
  padding: 4px;
}
window.victus button.seg-btn {
  background: transparent;
  color: #a8a8a8;
  border: none;
  box-shadow: none;
  border-radius: 8px;
  padding: 9px 6px;
  min-height: 16px;
  font-size: 13px;
  font-weight: 400;
}
window.victus button.seg-btn.on {
  background: #3f8cff;
  color: #ffffff;
  font-weight: 600;
}
window.graph-win {
  background: #161616;
  color: #ededed;
}
window.graph-win .graph-name { color: #cfcfcf; font-size: 11px; }
window.graph-win .graph-value { color: #ffffff; font-size: 11px; font-weight: 800; }
window.graph-win .graph-source { color: #969696; font-size: 11px; }
window.graph-win .graph-scale { background: #3d3d3d; }
window.graph-win entry {
  background: #303030;
  color: #ffffff;
  border: 1px solid #555555;
  border-radius: 2px;
  min-height: 22px;
  padding: 0 4px;
  font-size: 11px;
}
popover.sensor-menu {
  background: #202020;
  color: #ededed;
}
popover.sensor-menu button {
  background: transparent;
  color: #ededed;
  border: none;
  box-shadow: none;
  padding: 6px 18px;
  min-height: 0;
  font-size: 13px;
}
popover.sensor-menu button:disabled { color: #6a6a6a; }
popover.sensor-menu button:hover { background: #303030; }
window.victus .linkish {
  background: transparent;
  color: #a8a8a8;
  border: none;
  box-shadow: none;
  padding: 0;
  min-height: 0;
}
window.victus .linkish.on { color: #3f8cff; }
window.victus .pill {
  background: #202020;
  color: #ededed;
  border: none;
  box-shadow: none;
  border-radius: 999px;
  padding: 6px 14px;
  min-height: 0;
}
window.victus .row-link {
  background: transparent;
  border: none;
  box-shadow: none;
  padding: 8px 0;
}
window.victus .row-link:hover { background: #202020; border-radius: 10px; }
window.victus .sunken-chip {
  background: #0f0f0f;
  border-radius: 8px;
  padding: 4px 8px;
}
window.victus entry,
window.victus dropdown,
window.victus spinbutton {
  background: #0f0f0f;
  color: #ededed;
  border: 1px solid #2c2c2c;
  border-radius: 8px;
}
window.victus entry.hex { font-family: "IBM Plex Mono", monospace; }
window.victus scale trough {
  background: #313131;
  border-radius: 99px;
  min-height: 6px;
}
window.victus scale highlight { background: #3f8cff; }
window.victus scale slider {
  background: #ededed;
  border-radius: 99px;
  min-width: 16px;
  min-height: 16px;
}
window.victus switch:checked { background: #3f8cff; }
window.victus list row:selected { background: #202020; }
"#;

struct AppliedPower {
    enabled: bool,
    stapm: i32,
    fast: i32,
    slow: i32,
    tctl: i32,
    reapply: i32,
}

impl PartialEq for AppliedPower {
    fn eq(&self, other: &Self) -> bool {
        self.enabled == other.enabled
            && self.stapm == other.stapm
            && self.fast == other.fast
            && self.slow == other.slow
            && self.tctl == other.tctl
            && self.reapply == other.reapply
    }
}

struct Running {
    min: f64,
    max: f64,
    sum: f64,
    count: u32,
}

enum Release {
    Failed,
    Current,
    Available(String),
}

enum BackgroundEvent {
    Release(Release),
    Diagnostics(Result<PathBuf, String>),
    State(serde_json::Value),
    Frequency(Result<crate::FrequencyWindow, String>),
    Control(ControlRequest, Result<String, String>),
}

enum ControlRequest {
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
        if self.tx.try_send(event).is_ok() { let _ = (&*self.wake).write(&[1]); }
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
    applied_power: RefCell<AppliedPower>,
    applied_freq: Cell<Option<(i32, i32)>>,
    last_frequency: RefCell<Option<crate::FrequencyWindow>>,
    applied_uv: Cell<Option<(i32, i32)>>,
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
                Ok(value) => model.hydrate(&value),
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
        applied_power: RefCell::new(AppliedPower { enabled: false, stapm: 0, fast: 0, slow: 0, tctl: 0, reapply: 0 }),
        applied_freq: Cell::new(None),
        last_frequency: RefCell::new(None),
        applied_uv: Cell::new(None),
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
                                if line.trim() == "OK\tshortcut-events" { reader.get_ref().set_read_timeout(None).ok(); }
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
            std::thread::sleep(Duration::from_secs(2));
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
    wire_sidebar(session);
    wire_home(session);
    wire_power(session);
    wire_fans(session);
    wire_keyboard(session);
    wire_sensors(session);
    wire_settings(session);
    wire_draws(session);
}

fn wire_sidebar(session: &Rc<Session>) {
    let weak = Rc::downgrade(session);
    let click = gtk4::GestureClick::new();
    click.set_button(1);
    click.connect_released(move |_, _, _, y| {
        let Some(session) = weak.upgrade() else { return };
        let keyboard = session.model.borrow().host.keyboard;
        let height = f64::from(session.built.sidebar.height());
        if let Some(page) = paint::nav_page(keyboard, height, y) {
            show_page(&session, page);
        }
    });
    session.built.sidebar.add_controller(click);
    let weak = Rc::downgrade(session);
    let motion = gtk4::EventControllerMotion::new();
    motion.connect_motion(move |_, _, y| {
        let Some(session) = weak.upgrade() else { return };
        let keyboard = session.model.borrow().host.keyboard;
        let height = f64::from(session.built.sidebar.height());
        let hover = paint::nav_page(keyboard, height, y);
        if session.hover.get() != hover {
            session.hover.set(hover);
            session.built.sidebar.queue_draw();
        }
    });
    let weak = Rc::downgrade(session);
    motion.connect_leave(move |_| {
        let Some(session) = weak.upgrade() else { return };
        session.hover.set(None);
        session.built.sidebar.queue_draw();
    });
    session.built.sidebar.add_controller(motion);
}

fn wire_home(session: &Rc<Session>) {
    for (index, button) in session.built.home.profile_buttons.iter().enumerate() {
        let weak = Rc::downgrade(session);
        button.connect_clicked(move |_| {
            let Some(session) = weak.upgrade() else { return };
            select_profile(&session, i32::try_from(index).unwrap_or(0));
        });
    }
    for (button, key) in session.built.home.fan_buttons.iter().zip(session.built.home.fan_keys.clone()) {
        let weak = Rc::downgrade(session);
        button.connect_clicked(move |_| {
            let Some(session) = weak.upgrade() else { return };
            let Some(mode) = fan_mode_from_key(&key) else { return };
            let current = FanMode::from_config(&session.model.borrow().state.fan);
            if mode == FanMode::Custom && current == FanMode::Custom {
                show_page(&session, 2);
            } else {
                apply_fan_mode(&session, mode);
            }
        });
    }
    let weak = Rc::downgrade(session);
    session.built.home.curve.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        show_page(&session, 2);
    });
    let weak = Rc::downgrade(session);
    session.built.home.power.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        show_page(&session, 1);
    });
    let weak = Rc::downgrade(session);
    session.built.home.light_row.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        show_page(&session, 3);
    });
    for (button, choice) in session.built.home.mux_buttons.iter().zip(session.model.borrow().host.mux.clone()) {
        let weak = Rc::downgrade(session);
        button.connect_clicked(move |_| {
            let Some(session) = weak.upgrade() else { return };
            confirm_mux(&session, choice.index, choice.label.clone());
        });
    }
}

fn wire_power(session: &Rc<Session>) {
    for (slider, unit) in [(&session.built.power.stapm, "W"), (&session.built.power.fast, "W"), (&session.built.power.slow, "W"),
        (&session.built.power.tctl, "°C"), (&session.built.power.reapply, "s"), (&session.built.power.freq_min, "MHz"),
        (&session.built.power.freq_max, "MHz"), (&session.built.power.uv_core, "mV"), (&session.built.power.uv_cache, "mV")] {
        bind_power_label(slider, unit);
    }
    couple_limits(session, &session.built.power.freq_min.scale, &session.built.power.freq_max.scale);
    if session.model.borrow().host.intel { couple_limits(session, &session.built.power.slow.scale, &session.built.power.fast.scale); }
    let weak = Rc::downgrade(session);
    session.built.power.enabled.connect_active_notify(move |switch| {
        let Some(session) = weak.upgrade() else { return };
        let on = switch.is_active();
        session.built.power.limits.set_visible(on);
        session.built.power.note.set_visible(!on);
        if !session.suppress.get() {
            apply_power(&session);
        }
    });
    let weak = Rc::downgrade(session);
    session.built.power.apply.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        apply_power(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.power.freq_apply.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        apply_frequency(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.power.uv_apply.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        apply_undervolt(&session);
    });
}

fn bind_power_label(slider: &widgets::Slider, unit: &'static str) {
    slider.unit.set_text(unit);
    let value = slider.value.clone();
    let divisor = slider.divisor;
    slider.scale.connect_value_changed(move |scale| {
        value.set_range(scale.adjustment().lower() / divisor, scale.adjustment().upper() / divisor);
        value.set_value(scale.value() / divisor);
    });
    let scale = slider.scale.clone();
    slider.value.connect_value_changed(move |spin| scale.set_value(spin.value() * divisor));
}

fn couple_limits(session: &Rc<Session>, lower: &gtk4::Scale, upper: &gtk4::Scale) {
    let weak = Rc::downgrade(session);
    let other = upper.clone();
    lower.connect_value_changed(move |scale| {
        if weak.upgrade().is_some_and(|session| !session.suppress.get()) && scale.value() > other.value() { other.set_value(scale.value()); }
    });
    let weak = Rc::downgrade(session);
    let other = lower.clone();
    upper.connect_value_changed(move |scale| {
        if weak.upgrade().is_some_and(|session| !session.suppress.get()) && scale.value() < other.value() { other.set_value(scale.value()); }
    });
}

fn wire_fans(session: &Rc<Session>) {
    for (button, key) in session.built.fans.mode_buttons.iter().zip(session.built.fans.mode_keys.clone()) {
        let weak = Rc::downgrade(session);
        button.connect_clicked(move |_| {
            let Some(session) = weak.upgrade() else { return };
            let Some(mode) = fan_mode_from_key(&key) else { return };
            apply_fan_mode(&session, mode);
        });
    }
    let weak = Rc::downgrade(session);
    session.built.fans.cpu_link.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        session.curve_cpu.set(true);
        session.drag.set(None);
        session.selected_point.set(None);
        refresh_view(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.fans.gpu_link.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        session.curve_cpu.set(false);
        session.drag.set(None);
        session.selected_point.set(None);
        refresh_view(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.fans.response.connect_selected_notify(move |dropdown| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() {
            return;
        }
        let response = if dropdown.selected() == 1 { CURVE_RESPONSE_AGGRESSIVE } else { CURVE_RESPONSE_SMOOTH };
        session.model.borrow_mut().state.fan.curve_response = response.to_owned();
        schedule_fan_if_custom(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.fans.min_change.connect_value_changed(move |spin| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() {
            return;
        }
        session.model.borrow_mut().state.fan.min_fan_change_pct = spin.value().max(0.0);
        schedule_fan_if_custom(&session);
    });
    let drag = gtk4::GestureDrag::new();
    drag.set_button(1);
    drag.set_exclusive(true);
    let weak = Rc::downgrade(session);
    drag.connect_drag_begin(move |_, x, y| {
        let Some(session) = weak.upgrade() else { return };
        begin_fan_drag(&session, x, y);
    });
    let weak = Rc::downgrade(session);
    drag.connect_drag_update(move |gesture, dx, dy| {
        let Some(session) = weak.upgrade() else { return };
        let Some((origin_x, origin_y)) = gesture.start_point() else { return };
        update_fan_drag(&session, origin_x + dx, origin_y + dy);
    });
    let weak = Rc::downgrade(session);
    drag.connect_drag_end(move |_, _, _| {
        let Some(session) = weak.upgrade() else { return };
        end_fan_drag(&session);
    });
    session.built.fans.chart.add_controller(drag);
    let click = gtk4::GestureClick::new();
    click.set_button(3);
    click.set_exclusive(true);
    let weak = Rc::downgrade(session);
    click.connect_pressed(move |_, _, x, y| {
        let Some(session) = weak.upgrade() else { return };
        delete_fan_point(&session, x, y);
    });
    session.built.fans.chart.add_controller(click);
    let motion = gtk4::EventControllerMotion::new();
    let weak = Rc::downgrade(session);
    motion.connect_motion(move |_, x, y| {
        let Some(session) = weak.upgrade() else { return };
        let (width, height, maximum) = chart_size(&session);
        let model = session.model.borrow();
        let hover = paint::Plot::new(width, height, maximum).nearest(curve_points(&model.state.fan, model.profile, session.curve_cpu.get()), x, y);
        if hover != session.fan_hover.get() { session.fan_hover.set(hover); session.built.fans.chart.queue_draw(); }
    });
    let weak = Rc::downgrade(session);
    motion.connect_leave(move |_| { if let Some(session) = weak.upgrade() { session.fan_hover.set(None); session.built.fans.chart.queue_draw(); } });
    session.built.fans.chart.add_controller(motion);
}

fn wire_keyboard(session: &Rc<Session>) {
    for (button, id) in session.built.keyboard.effect_buttons.iter().zip(session.built.keyboard.effect_ids.clone()) {
        let weak = Rc::downgrade(session);
        button.connect_clicked(move |_| {
            let Some(session) = weak.upgrade() else { return };
            select_effect(&session, &id);
        });
    }
    for (index, button) in session.built.keyboard.zone_buttons.iter().enumerate() {
        let weak = Rc::downgrade(session);
        button.connect_clicked(move |_| {
            let Some(session) = weak.upgrade() else { return };
            let zones = session.model.borrow().zones;
            let target = if zones > 1 && index + 1 == session.built.keyboard.zone_buttons.len() {
                -1
            } else {
                i32::try_from(index).unwrap_or(0)
            };
            session.zone_target.set(target);
            refresh_view(&session);
            sync_color_entries(&session);
        });
    }
    bind_hex(session, &session.built.keyboard.hex, false);
    bind_hex(session, &session.built.keyboard.hex2, true);
    bind_strip(session, &session.built.keyboard.hue, true);
    bind_strip(session, &session.built.keyboard.shade, false);
    let weak = Rc::downgrade(session);
    session.built.keyboard.brightness.connect_value_changed(move |scale| {
        let Some(session) = weak.upgrade() else { return };
        let percent = paint::round_i32(scale.value() / 255.0 * 100.0).clamp(0, 100);
        session.built.keyboard.brightness_value.set_text(&format!("{percent}%"));
        if session.suppress.get() {
            return;
        }
        session.model.borrow_mut().state.lighting.brightness = paint::round_i32(scale.value()).clamp(0, 255);
            schedule(&session, true);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.speed.connect_value_changed(move |scale| {
        let Some(session) = weak.upgrade() else { return };
        let speed = paint::round_i32(scale.value()).clamp(1, 100);
        session.built.keyboard.speed_value.set_text(&speed.to_string());
        if session.suppress.get() {
            return;
        }
        session.model.borrow_mut().state.lighting.speed = speed;
        schedule(&session, true);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.idle.connect_value_changed(move |spin| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() {
            return;
        }
        if !session.built.keyboard.idle_enabled.is_active() { return; }
        session.model.borrow_mut().state.lighting.idle_timeout = paint::round_i32(spin.value()).clamp(1, 600);
        schedule(&session, true);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.idle_enabled.connect_active_notify(move |switch| {
        let Some(session) = weak.upgrade() else { return };
        session.built.keyboard.idle.set_sensitive(switch.is_active());
        if session.suppress.get() { return; }
        session.model.borrow_mut().state.lighting.idle_timeout = if switch.is_active() { paint::round_i32(session.built.keyboard.idle.value()).clamp(1, 600) } else { 0 };
        schedule(&session, true);
    });
    let click = gtk4::GestureClick::new();
    click.set_button(1);
    let weak = Rc::downgrade(session);
    click.connect_pressed(move |_, _, x, y| {
        let Some(session) = weak.upgrade() else { return };
        let width = f64::from(session.built.keyboard.visual.width());
        let height = f64::from(session.built.keyboard.visual.height());
        if let Some(zone) = paint::key_zone(width, height, x, y) {
            session.zone_target.set(if session.model.borrow().zones <= 1 { 0 } else { i32::try_from(zone).unwrap_or(0) });
            refresh_view(&session);
            sync_color_entries(&session);
        }
    });
    session.built.keyboard.visual.add_controller(click);
    for (area, secondary) in [(&session.built.keyboard.chip, false), (&session.built.keyboard.chip2, true)] {
        let click = gtk4::GestureClick::new();
        click.set_button(1);
        let weak = Rc::downgrade(session);
        click.connect_pressed(move |_, _, _, _| {
            let Some(session) = weak.upgrade() else { return };
            let hex = if secondary { session.model.borrow().state.lighting.color2.clone() } else { active_color(&session.model.borrow(), session.zone_target.get()) };
            let initial = gtk4::gdk::RGBA::parse(&hex).ok();
            let dialog = gtk4::ColorDialog::new();
            dialog.set_title(if secondary { "Secondary Color" } else { "Keyboard Color" });
            dialog.set_with_alpha(false);
            let weak = Rc::downgrade(&session);
            dialog.choose_rgba(Some(&session.window), initial.as_ref(), None::<&gtk4::gio::Cancellable>, move |result| {
                if let (Some(session), Ok(color)) = (weak.upgrade(), result) {
                    let hex = paint::hex_from_unit(f64::from(color.red()), f64::from(color.green()), f64::from(color.blue()));
                    assign_hex(&session, &hex, secondary, false);
                }
            });
        });
        area.add_controller(click);
    }
}

fn bind_hex(session: &Rc<Session>, entry: &gtk4::Entry, secondary: bool) {
    let weak = Rc::downgrade(session);
    entry.connect_changed(move |entry| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() {
            return;
        }
        let text = entry.text().to_string();
        if text.len() == 6 && text.chars().all(|ch| ch.is_ascii_hexdigit()) {
            assign_hex(&session, &format!("#{text}"), secondary, true);
        }
    });
    let weak = Rc::downgrade(session);
    entry.connect_activate(move |_| { if let Some(session) = weak.upgrade() { sync_color_entries(&session); } });
    let focus = gtk4::EventControllerFocus::new();
    let weak = Rc::downgrade(session);
    focus.connect_leave(move |_| { if let Some(session) = weak.upgrade() { sync_color_entries(&session); } });
    entry.add_controller(focus);
}

fn bind_strip(session: &Rc<Session>, area: &DrawingArea, hue_strip: bool) {
    let click = gtk4::GestureClick::new();
    click.set_button(1);
    let weak = Rc::downgrade(session);
    click.connect_pressed(move |_, _, x, _| {
        let Some(session) = weak.upgrade() else { return };
        let width = f64::from(if hue_strip { session.built.keyboard.hue.width() } else { session.built.keyboard.shade.width() }).max(1.0);
        let cell = ((x / width) * 36.0).floor().clamp(0.0, 35.0);
        let fraction = if hue_strip { cell / 36.0 } else { cell / 35.0 };
        let current = {
            let model = session.model.borrow();
            active_color(&model, session.zone_target.get())
        };
        let (hue, _, _) = hsv_parts(&current);
        let (red, green, blue) = if hue_strip {
            paint::hsl_to_rgb(fraction, 0.85, 0.55)
        } else {
            paint::hsl_to_rgb(hue, 0.28 + fraction * 0.6, 0.95 - fraction * 0.76)
        };
        assign_hex(&session, &paint::hex_from_unit(red, green, blue), false, false);
    });
    area.add_controller(click);
    let drag = gtk4::GestureDrag::new();
    drag.set_button(1);
    let weak = Rc::downgrade(session);
    drag.connect_drag_update(move |gesture, dx, _| {
        let Some(session) = weak.upgrade() else { return };
        let Some((origin, _)) = gesture.start_point() else { return };
        let area = if hue_strip { &session.built.keyboard.hue } else { &session.built.keyboard.shade };
        let cell = (((origin + dx) / f64::from(area.width()).max(1.0)) * 36.0).floor().clamp(0.0, 35.0);
        let hue = hsv_parts(&active_color(&session.model.borrow(), session.zone_target.get())).0;
        let rgb = if hue_strip { paint::hsl_to_rgb(cell / 36.0, 0.85, 0.55) } else { let t = cell / 35.0; paint::hsl_to_rgb(hue, 0.28 + t * 0.6, 0.95 - t * 0.76) };
        assign_hex(&session, &paint::hex_from_unit(rgb.0, rgb.1, rgb.2), false, false);
    });
    area.add_controller(drag);
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
    let weak = Rc::downgrade(session);
    session.built.settings.shortcut_set.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        if session.capturing.get() {
            session.capturing.set(false);
            session.built.settings.shortcut_set.set_label("Set");
            show_shortcut(&session, None);
        } else {
            session.capturing.set(true);
            session.captured_mods.borrow_mut().clear();
            session.built.settings.shortcut_set.set_label("Press a key…");
            session.built.settings.shortcut.set_text("(waiting)");
        }
    });
    let weak = Rc::downgrade(session);
    session.built.settings.shortcut_clear.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        session.capturing.set(false);
        session.built.settings.shortcut_set.set_label("Set");
        save_shortcut(&session, &[], 0);
    });
    let weak = Rc::downgrade(session);
    session.built.settings.update.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        check_updates(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.settings.diagnostics.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        collect_diagnostics(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.settings.quit.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        quit(&session);
    });
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
    let weak = Rc::downgrade(session);
    session.built.sidebar.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        let keyboard = session.model.borrow().host.keyboard;
        paint::sidebar(cr, f64::from(width), f64::from(height), keyboard, session.page.load(Ordering::Relaxed), session.hover.get());
    });
    let weak = Rc::downgrade(session);
    session.built.fans.chart.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        paint_chart(&session, cr, width, height);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.visual.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        paint_keys(&session, cr, width, height, false);
    });
    let weak = Rc::downgrade(session);
    session.built.home.mini.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        paint_keys(&session, cr, width, height, true);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.chip.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        let hex = active_color(&session.model.borrow(), session.zone_target.get());
        paint::chip(cr, f64::from(width), f64::from(height), &hex);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.chip2.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        let hex = session.model.borrow().state.lighting.color2.clone();
        paint::chip(cr, f64::from(width), f64::from(height), &hex);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.hue.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        let hex = active_color(&session.model.borrow(), session.zone_target.get());
        paint::hue_strip(cr, f64::from(width), f64::from(height), paint::unit_rgb(&hex));
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.shade.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        let hex = active_color(&session.model.borrow(), session.zone_target.get());
        let (hue, _, _) = hsv_parts(&hex);
        paint::shade_strip(cr, f64::from(width), f64::from(height), hue, paint::unit_rgb(&hex));
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
    *session.applied_power.borrow_mut() = AppliedPower {
        enabled: power.enabled,
        stapm: power.stapm_limit,
        fast: power.fast_limit,
        slow: power.slow_limit,
        tctl: power.tctl_temp,
        reapply: power.reapply_seconds,
    };
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
    refresh_view(session);
}

fn sync_color_entries(session: &Session) {
    session.suppress.set(true);
    let model = session.model.borrow();
    let primary = digits(&active_color(&model, session.zone_target.get()));
    let secondary = digits(&model.state.lighting.color2);
    drop(model);
    session.built.keyboard.hex.set_text(&primary);
    session.built.keyboard.hex2.set_text(&secondary);
    session.suppress.set(false);
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
    let previous = session.model.borrow().profile;
    {
        let mut model = session.model.borrow_mut();
        model.profile = profile;
        model.state.profile_before_battery = None;
    }
    if !commit(session, &format!("set-profile\t{profile}")) {
        session.model.borrow_mut().profile = previous;
    }
    refresh_view(session);
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
    let form = power_form(session);
    if form == *session.applied_power.borrow() {
        return;
    }
    let policy = victus_core::PowerPolicy {
        enabled: form.enabled,
        stapm_limit: form.stapm,
        fast_limit: form.fast,
        slow_limit: form.slow,
        tctl_temp: form.tctl,
        reapply_seconds: form.reapply,
    };
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
        let result = match socket {
            Some(socket) => transact(&socket, &request, Duration::from_secs(10)).and_then(|line| parse_status_response(&line)).map_err(|error| error.to_string()),
            None => Ok("Offline preview".into()),
        };
        tx.send(BackgroundEvent::Control(kind, result));
    });
}

fn control_result(session: &Session, kind: ControlRequest, result: Result<String, String>) {
    let success = result.is_ok();
    session.model.borrow_mut().status = result.as_ref().err().cloned().unwrap_or_default();
    match kind {
        ControlRequest::Power(policy) => {
            session.built.power.apply.set_sensitive(true);
            session.built.power.enabled.set_sensitive(true);
            if success {
                *session.applied_power.borrow_mut() = AppliedPower { enabled: policy.enabled, stapm: policy.stapm_limit, fast: policy.fast_limit,
                    slow: policy.slow_limit, tctl: policy.tctl_temp, reapply: policy.reapply_seconds };
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

fn power_form(session: &Session) -> AppliedPower {
    let power = &session.built.power;
    AppliedPower {
        enabled: power.enabled.is_active(),
        stapm: victus_core::clamp_power_limit(paint::round_i32(power.stapm.scale.value()) * 1000),
        fast: victus_core::clamp_power_limit(paint::round_i32(power.fast.scale.value()) * 1000),
        slow: victus_core::clamp_power_limit(paint::round_i32(power.slow.scale.value()) * 1000),
        tctl: victus_core::clamp_tctl_temp(paint::round_i32(power.tctl.scale.value())),
        reapply: victus_core::clamp_reapply_seconds(paint::round_i32(power.reapply.scale.value())),
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

fn select_effect(session: &Session, id: &str) {
    {
        let mut model = session.model.borrow_mut();
        if id == "off" {
            model.state.lighting.enabled = false;
        } else {
            model.state.lighting.enabled = true;
            model.state.lighting.effect = id.to_owned();
        }
    }
    schedule(session, true);
    refresh_view(session);
}

fn assign_hex(session: &Session, hex: &str, secondary: bool, from_entry: bool) {
    {
        let mut model = session.model.borrow_mut();
        if secondary {
            model.state.lighting.color2 = hex.to_owned();
        } else {
            assign_color(&mut model, session.zone_target.get(), hex.to_owned());
        }
    }
    if !from_entry && !secondary {
        session.suppress.set(true);
        session.built.keyboard.hex.set_text(digits(hex).as_str());
        session.suppress.set(false);
    }
    if secondary && !from_entry {
        session.suppress.set(true);
        session.built.keyboard.hex2.set_text(digits(hex).as_str());
        session.suppress.set(false);
    }
    session.built.keyboard.chip.queue_draw();
    session.built.keyboard.chip2.queue_draw();
    session.built.keyboard.hue.queue_draw();
    session.built.keyboard.shade.queue_draw();
    schedule(session, true);
}

fn assign_color(model: &mut Model, target: i32, hex: String) {
    let zones = usize::try_from(model.zones.max(1)).unwrap_or(1);
    let settings = &mut model.state.lighting;
    if settings.zone_colors.len() < zones {
        settings.zone_colors.resize(zones, settings.color.clone());
    }
    if target < 0 {
        settings.color.clone_from(&hex);
        for slot in &mut settings.zone_colors {
            slot.clone_from(&hex);
        }
        return;
    }
    let index = usize::try_from(target).unwrap_or(0);
    if let Some(slot) = settings.zone_colors.get_mut(index) {
        slot.clone_from(&hex);
    }
    if index == 0 {
        settings.color = hex;
    }
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

fn check_updates(session: &Rc<Session>) {
    session.built.settings.update.set_sensitive(false);
    session.built.settings.update_status.set_text("Checking for updates…");
    let tx = session.events_tx.clone();
    let _ = std::thread::Builder::new().name("victus-update".into()).spawn(move || {
        let _ = tx.send(BackgroundEvent::Release(fetch_release()));
    });
}

fn show_release(session: &Rc<Session>, release: Release) {
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
        if response != "update" {
            return;
        }
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
    else {
        return Release::Failed;
    };
    if !output.status.success() {
        return Release::Failed;
    }
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
        if command.spawn().is_ok() {
            return Ok(());
        }
    }
    Err("No terminal emulator found.")
}

fn collect_diagnostics(session: &Rc<Session>) {
    session.built.settings.diagnostics.set_sensitive(false);
    let tx = session.events_tx.clone();
    let state = victus_core::state_to_value(&session.model.borrow().state);
    let logs = session.log.borrow().iter().cloned().collect::<Vec<_>>();
    let _ = std::thread::Builder::new().name("victus-diagnostics".into()).spawn(move || {
        tx.send(BackgroundEvent::Diagnostics(write_diagnostics(&state, &logs)));
    });
}

fn show_diagnostics(session: &Rc<Session>, result: Result<PathBuf, String>) {
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
        if response == "open" {
            if let Some(program) = which("xdg-open") {
                let _ = Command::new(program).arg(&path).spawn();
            }
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
    let read = |path: &str| std::fs::read_to_string(path).map(|text| text.trim().to_owned()).unwrap_or_else(|_| "unknown".into());
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
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).map(|elapsed| elapsed.as_secs()).unwrap_or(0);
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
    merge_snapshot(&mut session.model.borrow_mut().snapshot, snapshot, requested);
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
    session.built.fans.cpu_link.set_css_classes(if session.curve_cpu.get() { &["linkish", "on"] } else { &["linkish"] });
    session.built.fans.gpu_link.set_css_classes(if session.curve_cpu.get() { &["linkish"] } else { &["linkish", "on"] });
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
        session.accent.load_from_string(&format!("window.victus button.seg-btn.on {{ background: {color}; }} window.victus .linkish.on, window.victus .accent {{ color: {color}; }} window.victus scale highlight, window.victus switch:checked {{ background: {color}; }}"));
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
    let points = curve_points(&model.state.fan, model.profile, cpu).to_vec();
    let accent = if cpu { accent_rgb(model.profile) } else { paint::unit_rgb("#E2572C") };
    let current = None;
    let selected = session.selected_point.get();
    drop(model);
    paint::chart(cr, f64::from(width), f64::from(height), temp_max, &points, accent, selected, session.fan_hover.get(), current);
}

fn paint_keys(session: &Session, cr: &gtk4::cairo::Context, width: i32, height: i32, compact: bool) {
    let model = session.model.borrow();
    let settings = normalize_lighting_settings(&model.state.lighting, model.zones);
    let frames = lighting_frames(&settings, model.zones, session.anim.get());
    let zones = model.zones;
    let enabled = settings.enabled;
    drop(model);
    paint::keyboard(cr, f64::from(width), f64::from(height), compact, zones, enabled, &frames);
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
    match transact(&socket, request, Duration::from_secs(2)) {
        Ok(response) => match parse_status_response(&response) {
            Ok(_) => {
                model.status.clear();
                true
            }
            Err(error) => {
                model.status = error.to_string();
                false
            }
        },
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

fn hsv_parts(hex: &str) -> (f64, f64, f64) {
    let (red, green, blue) = paint::unit_rgb(hex);
    paint::rgb_to_hsv(red, green, blue)
}

fn needs_color2(effect: &str) -> bool {
    matches!(effect, "wave" | "gradient")
}

fn ignores_color(effect: &str) -> bool {
    matches!(effect, "cycle" | "wave_rainbow" | "aurora" | "disco")
}

fn active_color(model: &Model, target: i32) -> String {
    if target < 0 {
        return model.state.lighting.color.clone();
    }
    let index = usize::try_from(target).unwrap_or(0);
    model.state.lighting.zone_colors.get(index).cloned().unwrap_or_else(|| model.state.lighting.color.clone())
}

fn digits(hex: &str) -> String {
    hex.trim().trim_start_matches('#').to_ascii_uppercase()
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

fn ram_status(snapshot: &SensorSnapshot) -> String {
    match (snapshot.ram_used_gb, snapshot.ram_total_gb) {
        (Some(used), Some(total)) => format!("RAM {used:.1}/{total:.0} GB"),
        _ => String::new(),
    }
}

fn cpu_caption(snapshot: &SensorSnapshot) -> String {
    let mut text = format!("CPU · {}", percent_text(snapshot.cpu_usage_pct));
    let power = snapshot.cpu_power.value.as_str();
    if !power.is_empty() && power != "0 W" && power != "0" {
        text.push_str(" · ");
        text.push_str(power);
    }
    text
}

fn gpu_caption(snapshot: &SensorSnapshot) -> String {
    let mut text = format!("GPU · {}", percent_text(snapshot.gpu_usage_pct));
    let power = snapshot.gpu_power.value.as_str();
    if !power.is_empty() && power != "0 W" && power != "0" {
        text.push_str(" · ");
        text.push_str(power);
    }
    text
}

fn percent_text(value: Option<f64>) -> String {
    match value {
        Some(value) => format!("{value:.0}%"),
        None => "—".into(),
    }
}

fn temp_text(value: Option<f64>) -> String {
    match value {
        Some(value) => format!("{value:.0}"),
        None => "—".into(),
    }
}

fn rpm_text(value: &str) -> String {
    leading_token(value).unwrap_or_else(|| "—".into())
}

fn power_head(snapshot: &SensorSnapshot) -> String {
    let frequency = snapshot
        .extra_sensors
        .iter()
        .filter(|extra| extra.key.starts_with("cpu-frequency"))
        .map(|extra| extra.numeric_value)
        .fold(None, |best: Option<f64>, value| Some(best.map_or(value, |best| best.max(value))));
    let frequency = frequency.map(|value| format!("{value:.0} MHz")).unwrap_or_else(|| "— MHz".into());
    format!("CPU {} · Freq {frequency}", snapshot.cpu_power.value)
}

fn current_text(snapshot: &SensorSnapshot, key: &str, profile: i32) -> String {
    match key {
        "profile" => mode_name(profile).to_owned(),
        "cpu-temp" => snapshot.cpu_temp.value.replace(" C", " °C"),
        "cpu-usage" => snapshot.cpu_usage.value.clone(),
        "cpu-power" => snapshot.cpu_power.value.clone(),
        "gpu-temp" => snapshot.gpu_temp.value.replace(" C", " °C"),
        "gpu-usage" => snapshot.gpu_usage.value.clone(),
        "gpu-power" => snapshot.gpu_power.value.clone(),
        "cpu-fan" => snapshot.cpu_fan.value.clone(),
        "gpu-fan" => snapshot.gpu_fan.value.clone(),
        "pwm-value" => snapshot.pwm_value.value.clone(),
        "pwm-mode" => snapshot.pwm_mode.value.clone(),
        "ram-usage" => snapshot.ram_usage.value.clone(),
        other => snapshot
            .extra_sensors
            .iter()
            .find(|extra| extra.key == other)
            .map(|extra| extra.reading.value.clone())
            .unwrap_or_else(|| "—".into()),
    }
}

fn reading_source(snapshot: &SensorSnapshot, key: &str) -> String {
    let reading = match key {
        "cpu-temp" => &snapshot.cpu_temp,
        "cpu-usage" => &snapshot.cpu_usage,
        "cpu-power" => &snapshot.cpu_power,
        "gpu-temp" => &snapshot.gpu_temp,
        "gpu-usage" => &snapshot.gpu_usage,
        "gpu-power" => &snapshot.gpu_power,
        "cpu-fan" => &snapshot.cpu_fan,
        "gpu-fan" => &snapshot.gpu_fan,
        "pwm-value" => &snapshot.pwm_value,
        "pwm-mode" => &snapshot.pwm_mode,
        "ram-usage" => &snapshot.ram_usage,
        "profile" => return String::new(),
        other => {
            return snapshot.extra_sensors.iter().find(|extra| extra.key == other).map(|extra| extra.reading.source.clone()).unwrap_or_default();
        }
    };
    reading.source.clone()
}

fn sample_value(snapshot: &SensorSnapshot, key: &str) -> Option<f64> {
    match key {
        "cpu-temp" => snapshot.cpu_temp_c,
        "cpu-usage" => snapshot.cpu_usage_pct,
        "cpu-power" => leading_f64(&snapshot.cpu_power.value),
        "gpu-temp" => snapshot.gpu_temp_c,
        "gpu-usage" => snapshot.gpu_usage_pct,
        "gpu-power" => leading_f64(&snapshot.gpu_power.value),
        "cpu-fan" => leading_f64(&snapshot.cpu_fan.value),
        "gpu-fan" => leading_f64(&snapshot.gpu_fan.value),
        "pwm-value" => leading_f64(&snapshot.pwm_value.value),
        "ram-usage" => snapshot.ram_used_gb,
        "profile" | "pwm-mode" => None,
        other => snapshot.extra_sensors.iter().find(|extra| extra.key == other).map(|extra| extra.numeric_value),
    }
}

fn leading_token(value: &str) -> Option<String> {
    let token = value.split_whitespace().next().unwrap_or("");
    token.parse::<f64>().ok().map(|_| token.to_owned())
}

fn leading_f64(value: &str) -> Option<f64> {
    value.split_whitespace().next().and_then(|token| token.parse().ok())
}

fn format_stat_unit(value: f64, unit: &str) -> String {
    let number = match unit { "RPM" | "PWM" => format!("{value:.0}"), "V" | "A" => format!("{value:.2}"), _ => format!("{value:.1}") };
    if unit.is_empty() { number } else { format!("{number} {unit}") }
}

fn merge_snapshot(target: &mut SensorSnapshot, incoming: SensorSnapshot, keys: &[String]) {
    for key in keys {
        match key.as_str() {
            "cpu-temp" => { target.cpu_temp = incoming.cpu_temp.clone(); target.cpu_temp_c = incoming.cpu_temp_c; }
            "cpu-usage" => { target.cpu_usage = incoming.cpu_usage.clone(); target.cpu_usage_pct = incoming.cpu_usage_pct; }
            "gpu-temp" => { target.gpu_temp = incoming.gpu_temp.clone(); target.gpu_temp_c = incoming.gpu_temp_c; }
            "gpu-usage" => { target.gpu_usage = incoming.gpu_usage.clone(); target.gpu_usage_pct = incoming.gpu_usage_pct; }
            "cpu-power" => target.cpu_power = incoming.cpu_power.clone(),
            "gpu-power" => target.gpu_power = incoming.gpu_power.clone(),
            "cpu-fan" => target.cpu_fan = incoming.cpu_fan.clone(),
            "gpu-fan" => target.gpu_fan = incoming.gpu_fan.clone(),
            "pwm-value" => target.pwm_value = incoming.pwm_value.clone(),
            "pwm-mode" => target.pwm_mode = incoming.pwm_mode.clone(),
            "ram-usage" => { target.ram_usage = incoming.ram_usage.clone(); target.ram_usage_pct = incoming.ram_usage_pct; target.ram_used_gb = incoming.ram_used_gb; target.ram_total_gb = incoming.ram_total_gb; }
            "cpu-frequency" | "lm-sensors" => {
                let prefix = if key == "cpu-frequency" { "cpu-frequency-" } else { "lm-" };
                target.extra_sensors.retain(|sensor| !sensor.key.starts_with(prefix));
                target.extra_sensors.extend(incoming.extra_sensors.iter().filter(|sensor| sensor.key.starts_with(prefix)).cloned());
            }
            _ => {},
        }
    }
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
    let remote_power = AppliedPower { enabled: model.state.power.enabled, stapm: model.state.power.stapm_limit, fast: model.state.power.fast_limit,
        slow: model.state.power.slow_limit, tctl: model.state.power.tctl_temp, reapply: model.state.power.reapply_seconds };
    session.suppress.set(true);
    if pristine_power {
        session.built.power.enabled.set_active(remote_power.enabled);
        for (scale, value) in [(&session.built.power.stapm.scale, remote_power.stapm / 1000), (&session.built.power.fast.scale, remote_power.fast / 1000),
            (&session.built.power.slow.scale, remote_power.slow / 1000), (&session.built.power.tctl.scale, remote_power.tctl), (&session.built.power.reapply.scale, remote_power.reapply)] { scale.set_value(f64::from(value)); }
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

    #[test]
    fn partial_samples_do_not_replace_unrequested_readings() {
        let mut target = SensorSnapshot::default();
        target.cpu_fan = victus_core::SensorReading::new("2400 RPM");
        target.cpu_temp_c = Some(70.0);
        let mut incoming = SensorSnapshot::default();
        incoming.cpu_power = victus_core::SensorReading::new("15 W");
        merge_snapshot(&mut target, incoming, &["cpu-power".into()]);
        assert_eq!(target.cpu_fan.value, "2400 RPM");
        assert_eq!(target.cpu_temp_c, Some(70.0));
        assert_eq!(target.cpu_power.value, "15 W");
    }
}
