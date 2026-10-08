use std::cell::{Cell, RefCell};
use std::collections::{HashMap, VecDeque};
use std::io::Read;
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::rc::{Rc, Weak};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use gtk4::gio::ApplicationFlags;
use gtk4::glib::{self, ControlFlow, Propagation};
use gtk4::prelude::*;
use victus_core::{effect_is_animated, parse_status_response, transact, FanMode};

use super::actions::{show_page, sync_controls};
use super::controls;
use super::graph;
use super::host::{notify_sensors, spawn_sensor_thread, spawn_state_stream};
use super::keyboard;
use super::labels::fan_mode_key;
use super::pages;
use super::session::{EventSender, SensorUpdate, Session};
use super::tray;
use super::view::{deliver_events, on_tick};
use super::wiring::{wire_draws, wire_sensors, wire_settings};
use crate::{connects_to_socket, Model};

const CSS: &str = include_str!("style.css");

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
    let Some(socket) = connects_to_socket(model).map(Path::to_path_buf) else { return };
    match fetched_state(&socket) {
        Ok(value) => {
            model.hydrate(&value);
            if model.state.initialized
                && let Err(error) = crate::persist::mirror_daemon(model.offline, &model.host.conf_path, model.host.intel, &model.state)
                && model.status.is_empty()
            {
                model.status = error;
            }
        }
        Err(error) => { model.status = error; return; }
    }
    if !model.state.initialized {
        migrate_legacy(model, &socket);
    }
    sync_saved_shortcut(model, &socket);
}

fn fetched_state(socket: &Path) -> Result<serde_json::Value, String> {
    let body = transact(socket, "get-state", Duration::from_secs(2))
        .and_then(|line| parse_status_response(&line))
        .map_err(|error| error.to_string())?;
    serde_json::from_str(&body).map_err(|_| "Daemon state was not valid".to_owned())
}

fn migrate_legacy(model: &mut Model, socket: &Path) {
    let text = std::fs::read_to_string(&model.host.conf_path).unwrap_or_default();
    let fan = model.host.conf_path.parent()
        .and_then(|dir| std::fs::read_to_string(dir.join("config.json")).ok())
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .filter(serde_json::Value::is_object);
    let Some(state) = crate::legacy::migrated_state(&text, fan.as_ref(), model.host.intel) else { return };
    let request = format!("initialize-state\t{}", victus_core::state_to_value(&state));
    match transact(socket, &request, Duration::from_secs(10)).and_then(|line| parse_status_response(&line)) {
        Ok(_) => {
            // Another client may have initialized it first.
            if let Ok(value) = fetched_state(socket) { model.hydrate(&value); }
        }
        Err(error) => model.status = format!("Legacy settings migration failed: {error}"),
    }
}

fn sync_saved_shortcut(model: &mut Model, socket: &Path) {
    let text = std::fs::read_to_string(&model.host.conf_path).unwrap_or_default();
    let settings = crate::legacy::settings(&text);
    if !(settings.contains_key("programShortcut") || settings.contains_key("programShortcut/key")) { return; }
    let body = serde_json::json!({"mods": model.host.shortcut_mods, "key": model.host.shortcut_key});
    if let Err(error) = transact(socket, &format!("program-shortcut\t{body}"), Duration::from_secs(2)).and_then(|line| parse_status_response(&line)) {
        model.status = format!("Could not sync program shortcut: {error}");
    }
}

pub(super) fn have_display() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some_and(|value| !value.is_empty())
        || std::env::var_os("DISPLAY").is_some_and(|value| !value.is_empty())
}

pub(super) struct FontFiles(Option<PathBuf>);
impl Drop for FontFiles { fn drop(&mut self) { if let Some(path) = &self.0 { let _ = std::fs::remove_dir_all(path); } } }

pub(super) fn configure_font_rendering() {
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

pub(super) fn prepare_fonts() -> FontFiles {
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

pub(super) fn build_ui(
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
        nav_position: Cell::new(None),
        nav_slide: Cell::new(None),
        nav_ticking: Cell::new(false),
        anim: Cell::new(0.0),
        light_at: Cell::new(None),
        fan_at: Cell::new(None),
        curve_cpu: Cell::new(true),
        drag: Cell::new(None),
        capturing: Cell::new(false),
        zone_target: Cell::new(-1),
        strip_hue: Cell::new(210.0 / 360.0),
        applied_power: RefCell::new(model.borrow().state.power.clone()),
        applied_freq: Cell::new(None),
        last_frequency: RefCell::new(None),
        applied_uv: Cell::new(None),
        profile_busy: Cell::new(false),
        captured_mods: RefCell::new(Vec::new()),
        selected_point: Cell::new(None),
        curve_selections: Cell::new([None, None]),
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

pub(super) fn install_tray(session: &Rc<Session>) {
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
pub(super) fn retain(id: glib::SourceId) {
    std::mem::forget(id);
}

pub(super) fn install_css(window: &impl IsA<gtk4::Widget>, accent: &gtk4::CssProvider) {
    let provider = gtk4::CssProvider::new();
    provider.load_from_string(CSS);
    let display = window.display();
    gtk4::style_context_add_provider_for_display(&display, &provider, gtk4::STYLE_PROVIDER_PRIORITY_USER);
    accent.load_from_string("");
    gtk4::style_context_add_provider_for_display(&display, accent, gtk4::STYLE_PROVIDER_PRIORITY_USER + 1);
}

pub(super) fn wire(session: &Rc<Session>) {
    controls::wire(session);
    keyboard::wire(session);
    wire_sensors(session);
    wire_settings(session);
    wire_draws(session);
}

pub(super) fn on_click(session: &Rc<Session>, button: &gtk4::Button, action: impl Fn(&Session) + 'static) {
    let weak = Rc::downgrade(session);
    button.connect_clicked(move |_| {
        if let Some(session) = weak.upgrade() { action(&session); }
    });
}

pub(super) fn on_draw(session: &Rc<Session>, area: &gtk4::DrawingArea, draw: impl Fn(&Session, &gtk4::cairo::Context, i32, i32) + 'static) {
    let weak = Rc::downgrade(session);
    area.set_draw_func(move |_, cr, width, height| {
        if let Some(session) = weak.upgrade() { draw(&session, cr, width, height); }
    });
}

pub(super) fn quit(session: &Session) {
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

pub(super) fn reveal(session: &Session) {
    session.window.unminimize();
    session.window.present();
    session.charts.show_all();
    session.visible.store(true, Ordering::Relaxed);
    notify_sensors(session);
    arm_tick(session);
}

pub(super) fn arm_tick(session: &Session) {
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

