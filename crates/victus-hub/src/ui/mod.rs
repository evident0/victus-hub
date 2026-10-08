//! GTK 4 / libadwaita panel. The page layout follows the previous Qt UI.
//!
//! This glib's `SourceId` does not remove its callback when dropped. Timers
//! still forget the id so a later `Drop` cannot cancel them.

#![allow(clippy::cognitive_complexity, clippy::similar_names, clippy::cast_possible_wrap, clippy::wildcard_imports)]
#![allow(clippy::items_after_statements, clippy::too_many_lines, clippy::option_if_let_else)]

pub mod pages;
pub mod paint;
pub mod widgets;

use std::cell::{Cell, RefCell};
use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use gtk4::gdk::{Key, ModifierType};
use gtk4::gio::ApplicationFlags;
use gtk4::glib::{self, ControlFlow, Propagation};
use gtk4::prelude::*;
use gtk4::{DrawingArea, ListBox, ListBoxRow};
use libadwaita::prelude::*;
use victus_core::{
    acpi_error_line, config_to_value, effect_is_animated, fan_mode_steps, filter_journal_lines, is_modifier,
    kernel_module_error_line, keybind_label, lighting_frames, normalize_fan_points, normalize_lighting_settings,
    parse_sensors_response, parse_status_response, power_to_value, step_increment, transact, validate_frequency,
    validate_shortcut, validate_undervolt, ExtraSensor, FanConfig, FanMode, FanPoint,
    SensorSnapshot, CPU_TEMP_MAX_C, CURVE_RESPONSE_AGGRESSIVE, CURVE_RESPONSE_SMOOTH, GPU_TEMP_MAX_C, KEY_LEFTALT,
    KEY_LEFTCTRL, KEY_LEFTMETA, KEY_LEFTSHIFT, PROGRAM_VERSION,
};

use crate::{
    connects_to_socket, diagnostics_from_logs, fan_requests, lighting_request, parse_release_tag, power_status_line,
    sensor_request, update_choice, update_shell, upsert_program_shortcut, Model, UpdateChoice, RELEASE_URL,
};

use self::pages::SensorRow;

const PAGE_NAMES: [&str; 6] = ["home", "power", "fans", "keyboard", "sensors", "settings"];
const DEBOUNCE: Duration = Duration::from_millis(250);

const CSS: &str = r#"
window.victus {
  background: #161616;
  color: #ededed;
  font-family: "IBM Plex Sans", Cantarell, sans-serif;
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
window.victus .caption,
window.victus .sub,
window.victus .row-sub {
  color: #969696;
  font-size: 12px;
}
window.victus .row-title {
  font-weight: 600;
}
window.victus .mono {
  font-family: "IBM Plex Mono", monospace;
  color: #8a8a8a;
  font-size: 12px;
}
window.victus .ok { color: #4ac06c; }
window.victus .accent { color: var(--accent, #3f8cff); }
window.victus .hairline { background: #2c2c2c; }
window.victus .seg {
  background: #0f0f0f;
  border-radius: 12px;
  padding: 3px;
}
window.victus .seg-btn {
  background: transparent;
  color: #a8a8a8;
  border: none;
  box-shadow: none;
  border-radius: 9px;
  padding: 6px 8px;
  min-height: 0;
}
window.victus .seg-btn.on {
  background: var(--accent, #3f8cff);
  color: #161616;
}
window.victus .linkish {
  background: transparent;
  color: #a8a8a8;
  border: none;
  box-shadow: none;
  padding: 0;
  min-height: 0;
}
window.victus .linkish.on { color: var(--accent, #3f8cff); }
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
window.victus scale highlight { background: var(--accent, #3f8cff); }
window.victus scale slider {
  background: #ededed;
  border-radius: 99px;
  min-width: 16px;
  min-height: 16px;
}
window.victus switch:checked { background: var(--accent, #3f8cff); }
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
    Diagnostics(PathBuf),
}

struct Session {
    model: Rc<RefCell<Model>>,
    suppress: Cell<bool>,
    page: Arc<AtomicUsize>,
    visible: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    window: libadwaita::ApplicationWindow,
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
    applied_uv: Cell<(i32, i32)>,
    stats: RefCell<HashMap<String, Running>>,
    history: RefCell<HashMap<String, VecDeque<f64>>>,
    sensor_rows: RefCell<Vec<SensorRow>>,
    extra_sig: RefCell<String>,
    graph_key: RefCell<String>,
    graph_window: RefCell<Option<gtk4::Window>>,
    popout: RefCell<Option<DrawingArea>>,
    sensor_rx: RefCell<Option<mpsc::Receiver<SensorSnapshot>>>,
    events_tx: mpsc::Sender<BackgroundEvent>,
    events_rx: RefCell<mpsc::Receiver<BackgroundEvent>>,
}

pub fn start(model: Model, own_bus: bool) -> Result<(), String> {
    if !have_display() {
        return Ok(());
    }
    prepare_fonts();
    let page = Arc::new(AtomicUsize::new(model.page));
    let visible = Arc::new(AtomicBool::new(true));
    let stop = Arc::new(AtomicBool::new(false));
    let sensor_rx = Rc::new(RefCell::new(spawn_sensor_thread(&model, Arc::clone(&page), Arc::clone(&visible), Arc::clone(&stop))));
    let model = Rc::new(RefCell::new(model));
    let flags = if own_bus { ApplicationFlags::empty() } else { ApplicationFlags::NON_UNIQUE };
    let app = libadwaita::Application::new(Some("io.github.evident0.VictusHub"), flags);
    let stop_for_shutdown = Arc::clone(&stop);
    app.connect_shutdown(move |_| stop_for_shutdown.store(true, Ordering::Relaxed));
    let page_for_activate = Arc::clone(&page);
    let visible_for_activate = Arc::clone(&visible);
    let stop_for_activate = Arc::clone(&stop);
    app.connect_activate(move |app| {
        if reveal_existing(app, &visible_for_activate) {
            return;
        }
        libadwaita::StyleManager::default().set_color_scheme(libadwaita::ColorScheme::ForceDark);
        build_ui(app, &model, &page_for_activate, &visible_for_activate, &stop_for_activate, &sensor_rx);
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

fn prepare_fonts() {
    if std::env::var_os("FONTCONFIG_FILE").is_some() {
        return;
    }
    let dir = std::env::temp_dir().join(format!("victus-hub-fonts-{}", std::process::id()));
    if std::fs::create_dir_all(&dir).is_err() {
        return;
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
        return;
    }
    // `std::env::set_var` is unsafe on this toolchain, and this crate forbids
    // unsafe. Restart once so fontconfig reads the bundled faces.
    let Ok(exe) = std::env::current_exe() else { return };
    let mut command = Command::new(exe);
    command.args(std::env::args_os().skip(1));
    command.env("FONTCONFIG_FILE", &conf);
    if let Ok(status) = command.status() {
        std::process::exit(status.code().unwrap_or(1));
    }
}

fn spawn_sensor_thread(
    model: &Model,
    page: Arc<AtomicUsize>,
    visible: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
) -> Option<mpsc::Receiver<SensorSnapshot>> {
    let socket = connects_to_socket(model)?.to_path_buf();
    let (tx, rx) = mpsc::channel();
    let _ = std::thread::Builder::new().name("victus-sensors".into()).spawn(move || {
        while !stop.load(Ordering::Relaxed) {
            if visible.load(Ordering::Relaxed) {
                let index = page.load(Ordering::Relaxed);
                if let Some(request) = sensor_request(index) {
                    if let Ok(response) = transact(&socket, &request, Duration::from_secs(2)) {
                        if let Ok(snapshot) = parse_sensors_response(&response) {
                            let _ = tx.send(snapshot);
                        }
                    }
                }
            }
            std::thread::sleep(Duration::from_secs(1));
        }
    });
    Some(rx)
}

fn reveal_existing(app: &libadwaita::Application, visible: &AtomicBool) -> bool {
    let Some(window) = app.windows().into_iter().next() else { return false };
    visible.store(true, Ordering::Relaxed);
    window.set_visible(true);
    window.present();
    true
}

fn build_ui(
    app: &libadwaita::Application,
    model: &Rc<RefCell<Model>>,
    page: &Arc<AtomicUsize>,
    visible: &Arc<AtomicBool>,
    stop: &Arc<AtomicBool>,
    sensor_rx: &Rc<RefCell<Option<mpsc::Receiver<SensorSnapshot>>>>,
) {
    let mut built = pages::build(&model.borrow());
    let rows = std::mem::take(&mut built.sensors.rows);
    let window = libadwaita::ApplicationWindow::new(app);
    window.set_title(Some("Victus Hub"));
    window.set_default_size(460, 680);
    window.set_size_request(420, 680);
    window.set_hide_on_close(true);
    window.add_css_class("victus");
    let accent = gtk4::CssProvider::new();
    install_css(&window, &accent);
    window.set_content(Some(&built.root));
    let (events_tx, events_rx) = mpsc::channel();
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
        applied_uv: Cell::new((0, 0)),
        stats: RefCell::new(HashMap::new()),
        history: RefCell::new(HashMap::new()),
        sensor_rows: RefCell::new(rows),
        extra_sig: RefCell::new(String::new()),
        graph_key: RefCell::new("cpu-temp".into()),
        graph_window: RefCell::new(None),
        popout: RefCell::new(None),
        sensor_rx: RefCell::new(sensor_rx.borrow_mut().take()),
        events_tx,
        events_rx: RefCell::new(events_rx),
    });
    wire(&session);
    sync_controls(&session);
    show_page(&session, 0);
    let weak = Rc::downgrade(&session);
    session.window.connect_close_request(move |window| {
        let Some(session) = weak.upgrade() else { return Propagation::Proceed };
        if window.hides_on_close() {
            session.visible.store(false, Ordering::Relaxed);
        } else {
            session.stop.store(true, Ordering::Relaxed);
        }
        Propagation::Proceed
    });
    let tick = Rc::clone(&session);
    retain(glib::timeout_add_local(Duration::from_millis(50), move || {
        if tick.stop.load(Ordering::Relaxed) {
            return ControlFlow::Break;
        }
        on_tick(&tick);
        deliver_events(&tick);
        ControlFlow::Continue
    }));
    session.window.present();
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
    accent.load_from_string("window.victus { --accent: #3F8CFF; }");
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
    bind_power_label(session, &session.built.power.stapm.scale, &session.built.power.stapm.value, "W");
    bind_power_label(session, &session.built.power.fast.scale, &session.built.power.fast.value, "W");
    bind_power_label(session, &session.built.power.slow.scale, &session.built.power.slow.value, "W");
    bind_power_label(session, &session.built.power.tctl.scale, &session.built.power.tctl.value, "°C");
    bind_power_label(session, &session.built.power.reapply.scale, &session.built.power.reapply.value, "s");
    bind_power_label(session, &session.built.power.freq_min.scale, &session.built.power.freq_min.value, "MHz");
    bind_power_label(session, &session.built.power.freq_max.scale, &session.built.power.freq_max.value, "MHz");
    bind_power_label(session, &session.built.power.uv_core.scale, &session.built.power.uv_core.value, "mV");
    bind_power_label(session, &session.built.power.uv_cache.scale, &session.built.power.uv_cache.value, "mV");
    let weak = Rc::downgrade(session);
    session.built.power.enabled.connect_active_notify(move |switch| {
        let Some(session) = weak.upgrade() else { return };
        let on = switch.is_active();
        session.built.power.limits.set_visible(on);
        session.built.power.note.set_visible(!on);
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

fn bind_power_label(session: &Rc<Session>, scale: &gtk4::Scale, label: &gtk4::Label, unit: &'static str) {
    let _ = session;
    let label = label.clone();
    let unit = unit.to_owned();
    scale.connect_value_changed(move |scale| {
        label.set_text(&scale_text(scale.value(), &unit));
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
        refresh_view(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.fans.gpu_link.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        session.curve_cpu.set(false);
        session.drag.set(None);
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
        schedule(&session.light_at);
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
        schedule(&session.light_at);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.idle.connect_value_changed(move |spin| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() {
            return;
        }
        session.model.borrow_mut().state.lighting.idle_timeout = paint::round_i32(spin.value()).clamp(0, 3600);
        schedule(&session.light_at);
    });
    let click = gtk4::GestureClick::new();
    click.set_button(1);
    let weak = Rc::downgrade(session);
    click.connect_pressed(move |_, _, x, y| {
        let Some(session) = weak.upgrade() else { return };
        let width = f64::from(session.built.keyboard.visual.width());
        let height = f64::from(session.built.keyboard.visual.height());
        if let Some(zone) = paint::key_zone(width, height, x, y) {
            session.zone_target.set(i32::try_from(zone).unwrap_or(0));
            refresh_view(&session);
            sync_color_entries(&session);
        }
    });
    session.built.keyboard.visual.add_controller(click);
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
}

fn bind_strip(session: &Rc<Session>, area: &DrawingArea, hue_strip: bool) {
    let click = gtk4::GestureClick::new();
    click.set_button(1);
    let weak = Rc::downgrade(session);
    click.connect_pressed(move |_, _, x, _| {
        let Some(session) = weak.upgrade() else { return };
        let width = f64::from(if hue_strip { session.built.keyboard.hue.width() } else { session.built.keyboard.shade.width() }).max(1.0);
        let fraction = (x / width).clamp(0.0, 1.0);
        let current = {
            let model = session.model.borrow();
            active_color(&model, session.zone_target.get())
        };
        let (hue, _, _) = hsv_parts(&current);
        let (red, green, blue) = if hue_strip {
            paint::hsv_to_rgb(fraction, 1.0, 1.0)
        } else {
            paint::hsv_to_rgb(hue, 1.0, fraction)
        };
        assign_hex(&session, &paint::hex_from_unit(red, green, blue), false, false);
    });
    area.add_controller(click);
}

fn wire_sensors(session: &Rc<Session>) {
    let weak = Rc::downgrade(session);
    session.built.sensors.list.connect_row_selected(move |_, row| {
        let Some(session) = weak.upgrade() else { return };
        let Some(row) = row else { return };
        let key = row.widget_name().to_string();
        if key.is_empty() {
            return;
        }
        *session.graph_key.borrow_mut() = key;
        session.built.sensors.graph.queue_draw();
        if let Some(area) = session.popout.borrow().as_ref() {
            area.queue_draw();
        }
        refresh_graph_button(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.sensors.open.connect_clicked(move |_| {
        let Some(session) = weak.upgrade() else { return };
        open_graph(&session);
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
        if code <= 0 || is_modifier(code) {
            return Propagation::Proceed;
        }
        let mods = modifier_codes(state);
        session.capturing.set(false);
        session.built.settings.shortcut_set.set_label("Set");
        match validate_shortcut(&mods, code) {
            Ok((mods, key)) => save_shortcut(&session, &mods, key),
            Err(error) => session.built.settings.shortcut.set_text(&error.to_string()),
        }
        Propagation::Stop
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
        let (hue, _, _) = hsv_parts(&hex);
        paint::hue_strip(cr, f64::from(width), f64::from(height), hue);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.shade.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        let hex = active_color(&session.model.borrow(), session.zone_target.get());
        let (hue, _, value) = hsv_parts(&hex);
        paint::shade_strip(cr, f64::from(width), f64::from(height), hue, value);
    });
    let weak = Rc::downgrade(session);
    session.built.sensors.graph.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        paint_graph(&session, cr, width, height);
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
        let step = if window.upper - window.lower >= 100_000 { 100_000.0 } else { 1.0 };
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
        session.applied_freq.set(Some((minimum, maximum)));
        session.built.power.freq_sliders.set_visible(true);
        let note = if window.mixed { "CPU policies disagree. The sliders follow the first policy." } else { "" };
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
    set_scale(&session.built.power.uv_core.scale, 0.0);
    set_scale(&session.built.power.uv_cache.scale, 0.0);
    session.applied_uv.set((0, 0));
    session.built.settings.battery.set_active(model.state.battery_power_save);
    session.built.settings.nvidia.set_active(model.state.disable_nvidia_queries);
    session.built.settings.hardware.set_active(model.state.hardware_shortcuts);
    let response = if model.state.fan.curve_response == CURVE_RESPONSE_AGGRESSIVE { 1 } else { 0 };
    session.built.fans.response.set_selected(response);
    session.built.fans.min_change.set_value(model.state.fan.min_fan_change_pct);
    let lighting = normalize_lighting_settings(&model.state.lighting, model.zones);
    set_scale(&session.built.keyboard.brightness, f64::from(lighting.brightness));
    set_scale(&session.built.keyboard.speed, f64::from(lighting.speed));
    session.built.keyboard.idle.set_value(f64::from(lighting.idle_timeout));
    drop(model);
    session.suppress.set(false);
    sync_color_entries(session);
    if let Some(row) = find_row(&session.built.sensors.list, "cpu-temp") {
        session.built.sensors.list.select_row(Some(&row));
    }
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
    session.built.sidebar.queue_draw();
}

fn page_width(page: usize) -> i32 {
    match page {
        2 | 3 | 4 => 700,
        _ => 460,
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
    if commit(session, &request) {
        session.model.borrow_mut().state.power = policy;
        *session.applied_power.borrow_mut() = form;
    }
    refresh_view(session);
}

fn apply_frequency(session: &Session) {
    let minimum = paint::round_i32(session.built.power.freq_min.scale.value());
    let maximum = paint::round_i32(session.built.power.freq_max.scale.value());
    if session.applied_freq.get() == Some((minimum, maximum)) {
        return;
    }
    if let Err(error) = validate_frequency(minimum, maximum) {
        session.model.borrow_mut().status = error.to_string();
        show_status(session);
        return;
    }
    if commit(session, &format!("cpu-frequency-config\t{minimum}\t{maximum}")) {
        session.model.borrow_mut().state.cpu_frequency = Some((minimum, maximum));
        session.applied_freq.set(Some((minimum, maximum)));
    }
    show_status(session);
}

fn apply_undervolt(session: &Session) {
    let core = paint::round_i32(session.built.power.uv_core.scale.value()).clamp(-250, 0);
    let cache = paint::round_i32(session.built.power.uv_cache.scale.value()).clamp(-250, 0);
    if session.applied_uv.get() == (core, cache) {
        return;
    }
    if let Err(error) = validate_undervolt(core, cache) {
        session.built.power.uv_status.set_text(&error.to_string());
        return;
    }
    if commit(session, &format!("intel-undervolt\t{core}\t{cache}")) {
        session.applied_uv.set((core, cache));
        session.built.power.uv_status.set_text("Undervolt applied.");
    } else {
        let status = session.model.borrow().status.clone();
        session.built.power.uv_status.set_text(&status);
    }
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
    schedule(&session.light_at);
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
    schedule(&session.light_at);
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
        schedule(&session.fan_at);
    }
}

fn begin_fan_drag(session: &Session, x: f64, y: f64) {
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
    drop(model);
    session.drag.set(None);
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
        model.host.conf_path.clone()
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
    let _ = std::thread::Builder::new().name("victus-diagnostics".into()).spawn(move || {
        let _ = tx.send(BackgroundEvent::Diagnostics(write_diagnostics()));
    });
}

fn show_diagnostics(session: &Rc<Session>, path: PathBuf) {
    session.built.settings.diagnostics.set_sensitive(true);
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

fn write_diagnostics() -> PathBuf {
    let kernel = command_output("journalctl", &["-k", "-b", "--no-pager"]);
    let daemon = command_output("journalctl", &["-u", "victus-hubd", "-b", "--no-pager"]);
    let modules = filter_journal_lines(kernel.as_deref(), kernel_module_error_line, 80);
    let acpi = filter_journal_lines(kernel.as_deref(), acpi_error_line, 80);
    let report = diagnostics_from_logs(modules.as_deref(), acpi.as_deref(), daemon.as_deref());
    let path = report_path();
    let _ = std::fs::write(&path, report);
    path
}

fn report_path() -> PathBuf {
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).map(|elapsed| elapsed.as_secs()).unwrap_or(0);
    let name = format!("victus-hub-diagnostics-{stamp}.md");
    if let Some(home) = std::env::var_os("HOME") {
        let downloads = PathBuf::from(home).join("Downloads");
        if downloads.is_dir() || std::fs::create_dir_all(&downloads).is_ok() {
            return downloads.join(name);
        }
    }
    std::env::temp_dir().join(name)
}

fn command_output(name: &str, args: &[&str]) -> Option<String> {
    let program = which(name)?;
    let output = Command::new(program).args(args).output().ok()?;
    String::from_utf8(output.stdout).ok().filter(|text| !text.is_empty())
}

fn which(name: &str) -> Option<PathBuf> {
    ["/usr/bin", "/bin", "/usr/local/bin"].into_iter().map(|dir| Path::new(dir).join(name)).find(|path| path.is_file())
}

fn quit(session: &Session) {
    session.stop.store(true, Ordering::Relaxed);
    session.visible.store(false, Ordering::Relaxed);
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
    if let Some(snapshot) = latest {
        apply_snapshot(session, snapshot);
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
    show_status(session);
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
        }
    }
}

fn apply_snapshot(session: &Session, snapshot: SensorSnapshot) {
    let profile = session.model.borrow().profile;
    let signature = extra_signature(&snapshot.extra_sensors);
    if signature != *session.extra_sig.borrow() {
        *session.extra_sig.borrow_mut() = signature;
        pages::clear_list(&session.built.sensors.list);
        let rows = pages::fill_sensors(&session.built.sensors.list, &snapshot.extra_sensors);
        *session.sensor_rows.borrow_mut() = rows;
        let key = session.graph_key.borrow().clone();
        if let Some(row) = find_row(&session.built.sensors.list, &key) {
            session.built.sensors.list.select_row(Some(&row));
        }
    }
    {
        let mut stats = session.stats.borrow_mut();
        let mut history = session.history.borrow_mut();
        for row in session.sensor_rows.borrow().iter() {
            if row.key == "profile" {
                row.current.set_text(mode_name(profile));
                continue;
            }
            let Some(value) = sample_value(&snapshot, &row.key) else {
                row.current.set_text(&current_text(&snapshot, &row.key, profile));
                continue;
            };
            if row.graphable {
                let stat = stats.entry(row.key.clone()).or_insert(Running { min: value, max: value, sum: 0.0, count: 0 });
                stat.min = stat.min.min(value);
                stat.max = stat.max.max(value);
                stat.sum += value;
                stat.count = stat.count.saturating_add(1);
                let samples = history.entry(row.key.clone()).or_default();
                samples.push_back(value);
                while samples.len() > 120 {
                    samples.pop_front();
                }
                row.maximum.set_text(&format_stat(stat.max));
                row.minimum.set_text(&format_stat(stat.min));
                let count = f64::from(stat.count.max(1));
                row.average.set_text(&format_stat(stat.sum / count));
            }
            row.current.set_text(&current_text(&snapshot, &row.key, profile));
        }
    }
    session.model.borrow_mut().snapshot = snapshot;
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
    if enabled && effect_is_animated(&effect) {
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
    session.built.keyboard.color2_box.set_visible(enabled && needs_color2(&effect));
    session.built.keyboard.speed_row.set_visible(animated);
    session.accent.load_from_string(&format!("window.victus {{ --accent: {}; }}", accent_hex(profile)));
    session.built.home.mini.queue_draw();
    session.built.keyboard.visual.queue_draw();
    session.built.keyboard.chip.queue_draw();
    session.built.keyboard.chip2.queue_draw();
    session.built.keyboard.hue.queue_draw();
    session.built.keyboard.shade.queue_draw();
    session.built.fans.chart.queue_draw();
    session.built.sensors.graph.queue_draw();
    session.built.sidebar.queue_draw();
    if let Some(area) = session.popout.borrow().as_ref() {
        area.queue_draw();
    }
    refresh_graph_button(session);
    show_status(session);
}

fn refresh_graph_button(session: &Session) {
    let key = session.graph_key.borrow().clone();
    let graphable = session.sensor_rows.borrow().iter().any(|row| row.key == key && row.graphable);
    session.built.sensors.open.set_sensitive(graphable);
}

fn open_graph(session: &Rc<Session>) {
    if session.sensor_rows.borrow().iter().any(|row| row.key == *session.graph_key.borrow() && !row.graphable) {
        return;
    }
    if let Some(window) = session.graph_window.borrow().as_ref() {
        window.present();
        return;
    }
    let title = {
        let key = session.graph_key.borrow().clone();
        session.sensor_rows.borrow().iter().find(|row| row.key == key).map(|row| row.name.clone()).unwrap_or(key)
    };
    let window = gtk4::Window::new();
    window.set_title(Some(&title));
    window.set_default_size(720, 360);
    window.set_transient_for(Some(&session.window));
    let area = DrawingArea::new();
    area.set_hexpand(true);
    area.set_vexpand(true);
    let weak = Rc::downgrade(session);
    area.set_draw_func(move |_, cr, width, height| {
        let Some(session) = weak.upgrade() else { return };
        paint_graph(&session, cr, width, height);
    });
    let weak = Rc::downgrade(session);
    window.connect_destroy(move |_| {
        let Some(session) = weak.upgrade() else { return };
        session.graph_window.borrow_mut().take();
        session.popout.borrow_mut().take();
    });
    window.set_child(Some(&area));
    *session.popout.borrow_mut() = Some(area);
    window.present();
    *session.graph_window.borrow_mut() = Some(window);
}

fn paint_chart(session: &Session, cr: &gtk4::cairo::Context, width: i32, height: i32) {
    let model = session.model.borrow();
    let cpu = session.curve_cpu.get();
    let temp_max = if cpu { CPU_TEMP_MAX_C } else { GPU_TEMP_MAX_C };
    let points = curve_points(&model.state.fan, model.profile, cpu).to_vec();
    let accent = if cpu { accent_rgb(model.profile) } else { paint::unit_rgb("#E2572C") };
    let current = if cpu { model.snapshot.cpu_temp_c } else { model.snapshot.gpu_temp_c };
    let selected = session.drag.get();
    drop(model);
    paint::chart(cr, f64::from(width), f64::from(height), temp_max, &points, accent, selected, current);
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

fn paint_graph(session: &Session, cr: &gtk4::cairo::Context, width: i32, height: i32) {
    let key = session.graph_key.borrow().clone();
    let samples = session.history.borrow().get(&key).map(|samples| samples.iter().copied().collect::<Vec<_>>()).unwrap_or_default();
    let (min, max) = session.sensor_rows.borrow().iter().find(|row| row.key == key).map(|row| (row.min, row.max)).unwrap_or((0.0, 100.0));
    let profile = session.model.borrow().profile;
    paint::sparkline(cr, f64::from(width), f64::from(height), &samples, min, max, accent_rgb(profile));
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
    session.built.status.set_visible(!status.is_empty());
    session.built.status.set_text(&status);
}

fn schedule(slot: &Cell<Option<Instant>>) {
    slot.set(Some(Instant::now() + DEBOUNCE));
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
    hex.trim().trim_start_matches('#').to_owned()
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
        "cpu-temp" => snapshot.cpu_temp.value.clone(),
        "cpu-usage" => snapshot.cpu_usage.value.clone(),
        "cpu-power" => snapshot.cpu_power.value.clone(),
        "gpu-temp" => snapshot.gpu_temp.value.clone(),
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

fn format_stat(value: f64) -> String {
    if value.abs() >= 100.0 { format!("{value:.0}") } else { format!("{value:.1}") }
}

fn extra_signature(extras: &[ExtraSensor]) -> String {
    extras.iter().map(|extra| format!("{}:{}", extra.group, extra.key)).collect::<Vec<_>>().join("\n")
}

fn find_row(list: &ListBox, key: &str) -> Option<ListBoxRow> {
    let mut index = 0;
    while let Some(row) = list.row_at_index(index) {
        if row.widget_name().as_str() == key {
            return Some(row);
        }
        index += 1;
    }
    None
}

fn mark_key(buttons: &[gtk4::Button], keys: &[String], key: &str) {
    widgets::mark(buttons, keys.iter().position(|item| item == key));
}

fn scale_text(value: f64, unit: &str) -> String {
    if unit == "MHz" {
        format!("{} MHz", paint::round_i32(value / 1000.0))
    } else {
        format!("{} {unit}", paint::round_i32(value))
    }
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
