use std::cell::{Cell, RefCell};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, AtomicUsize};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use gtk4::gio::ApplicationFlags;
use gtk4::glib;
use gtk4::prelude::*;
use victus_core::FanMode;

use super::actions::*;
use super::host::*;
use super::maintenance::Release;
use super::session::*;
use super::shell::*;
use super::view::*;
use super::wiring::*;
use super::*;
use crate::Model;

#[test]
#[ignore = "requires a GTK display; run under Xvfb"]
fn gtk_controls_preserve_pending_edits_and_wire_lighting_and_navigation() {
    gtk4::init().unwrap();
    gtk4::Settings::default().unwrap().set_gtk_application_prefer_dark_theme(false);
    libadwaita::init().unwrap();
    configure_font_rendering();
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
    verify_page_parity(&session);

    session.built.home.curve.emit_clicked();
    assert_eq!(model.borrow().page, 2);
    session.selected_point.set(Some(1));
    session.built.fans.gpu_link.emit_clicked();
    assert!(!session.curve_cpu.get());
    assert_eq!(session.selected_point.get(), None);
    session.built.fans.cpu_link.emit_clicked();
    assert!(session.curve_cpu.get());
    assert_eq!(session.selected_point.get(), Some(1), "CPU point selection was lost when switching curves");

    session.built.home.light_row.emit_clicked();
    assert_eq!(model.borrow().page, 3);
    let static_index = session.built.keyboard.effect_ids.iter().position(|id| id == "static").unwrap();
    session.built.keyboard.effect_buttons[static_index].emit_clicked();
    session.built.keyboard.brightness.set_value(128.0);
    session.built.keyboard.hex.set_text("123456");
    assert_ne!(model.borrow().state.lighting.color, "#123456", "hex edits should commit only when editing finishes");
    session.built.keyboard.hex.emit_activate();
    session.built.keyboard.hex2.set_text("ABCDEF");
    session.built.keyboard.hex2.emit_activate();
    assert_eq!(model.borrow().state.lighting.brightness, 128);
    assert_eq!(model.borrow().state.lighting.zone_colors, vec!["#123456"; 4]);
    assert_eq!(model.borrow().state.lighting.color2, "#ABCDEF");
    session.built.keyboard.zone_buttons[0].emit_clicked();
    session.built.keyboard.hex.set_text("654321");
    session.built.keyboard.hex.emit_activate();
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
    show_page(&session, 4);
    let list = &session.built.sensors.list;
    let laid_out = Instant::now() + Duration::from_secs(2);
    while Instant::now() < laid_out {
        while glib::MainContext::default().iteration(false) {}
        if list.row_at_index(1).is_some_and(|row| row.height() > 0) {
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(list.row_at_index(1).is_some_and(|row| row.height() > 0), "sensor rows were not allocated");
    let row_y = |name: &str| -> f64 {
        let mut index = 0;
        while let Some(row) = list.row_at_index(index) {
            if row.widget_name() == name {
                let bounds = row.compute_bounds(list).expect("row bounds");
                return f64::from(bounds.y()) + f64::from(bounds.height()) / 2.0;
            }
            index += 1;
        }
        panic!("missing sensor row {name}");
    };
    let y = row_y("cpu-temp");
    assert!(sensor_at_point(&session, 12.0, y).is_some_and(|(pick, _)| pick.key == "cpu-temp" && pick.graphable));
    let group = list.row_at_index(0).expect("group row");
    let group_bounds = group.compute_bounds(list).expect("group bounds");
    assert!(sensor_at_point(&session, 12.0, f64::from(group_bounds.y()) + 2.0).is_none());
    let controllers = list.observe_controllers();
    let mut right_click = None;
    for index in 0..controllers.n_items() {
        if let Some(gesture) = controllers.item(index).and_downcast::<gtk4::GestureClick>() {
            if gesture.button() == 3 {
                right_click = Some(gesture);
                break;
            }
        }
    }
    let right_click = right_click.expect("right-click gesture");
    let release = |gesture: &gtk4::GestureClick, at: f64| {
        let presses = 1i32;
        let x = 12.0f64;
        gesture.emit_by_name::<()>("released", &[&presses, &x, &at]);
        while glib::MainContext::default().iteration(false) {}
    };
    release(&right_click, f64::from(group_bounds.y()) + 2.0);
    let menu = session.sensor_menu.borrow().clone().expect("sensor menu");
    assert!(!menu.is_visible(), "group rows do not open the sensor menu");
    release(&right_click, row_y("pwm-mode"));
    assert!(menu.is_visible(), "sensor menu stays open");
    let graph = menu.child().and_downcast::<gtk4::Button>().expect("graph item");
    assert!(!graph.is_sensitive(), "pwm mode is not graphable");
    assert_eq!(graph.tooltip_text().as_deref(), Some("Not graphable"));
    menu.popdown();
    release(&right_click, y);
    assert!(menu.is_visible(), "sensor menu stays open after right-click release");
    assert!(graph.is_sensitive());
    graph.emit_clicked();
    assert!(session.charts.has_visible(), "graph opens from the sensor menu");
    session.sensor_menu.borrow_mut().take().unwrap().unparent();
    quit(&session);
    verify_hardware_variants(&app);
}

fn settle_ui() {
    let end = Instant::now() + Duration::from_millis(350);
    while Instant::now() < end {
        while glib::MainContext::default().iteration(false) {}
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn snapshot_ui(session: &Session, name: &str) {
    settle_ui();
    let Some(folder) = std::env::var_os("VICTUS_HUB_UI_SNAPSHOTS").map(PathBuf::from) else { return };
    std::fs::create_dir_all(&folder).unwrap();
    let paintable = gtk4::WidgetPaintable::new(Some(&session.built.root));
    let mut node = None;
    for _ in 0..10 {
        let snapshot = gtk4::Snapshot::new();
        paintable.snapshot(&snapshot, f64::from(session.built.root.width()), f64::from(session.built.root.height()));
        node = snapshot.to_node();
        if node.is_some() { break; }
        std::thread::sleep(Duration::from_millis(20));
        while glib::MainContext::default().iteration(false) {}
    }
    let node = node.unwrap_or_else(|| panic!("rendered UI for {name}: {}×{}", session.built.root.width(), session.built.root.height()));
    let texture = session.window.renderer().unwrap().render_texture(&node, None);
    texture.save_to_png(folder.join(format!("{name}-gtk.png"))).unwrap();
}

fn verify_page_parity(session: &Session) {
    let original = session.model.borrow().clone();
    session.model.borrow_mut().state.lighting.enabled = true;
    sync_controls(session);
    assert!(!session.built.power.limits.is_visible());
    assert!(!session.built.power.apply.is_sensitive());
    assert!(session.built.power.freq_sliders.is_visible(), "unavailable frequency controls stay visible but disabled");
    assert!(!session.built.power.freq_sliders.is_sensitive());
    assert!(!session.built.settings.shortcut_clear.is_sensitive());
    for (index, name) in PAGE_NAMES.iter().enumerate() {
        show_page(session, index);
        snapshot_ui(session, name);
        assert_eq!(session.window.width(), page_width(index), "{name} page forces a wider window than Qt");
    }
    let group = session.built.sensors.list.row_at_index(0).unwrap();
    let first_sensor = session.built.sensors.list.row_at_index(1).unwrap();
    let branch = group.child().unwrap().first_child().and_downcast::<gtk4::Button>().unwrap();
    session.built.sensors.list.select_row(Some(&group));
    assert!(first_sensor.is_visible(), "selecting a group must not collapse it");
    branch.emit_clicked();
    assert!(!first_sensor.is_visible());
    branch.emit_clicked();
    assert!(first_sensor.is_visible());
    session.built.sensors.list.unselect_all();
    session.model.borrow_mut().state.power.enabled = true;
    sync_controls(session);
    show_page(session, 1);
    assert!(session.built.power.limits.is_visible());
    session.built.power.stapm.scale.set_value(35.0);
    assert!(session.built.power.apply.is_sensitive(), "power changes enable Apply");
    assert!(session.built.power.stapm.value.text().ends_with(" W"));
    session.built.power.stapm.value.set_text("42 W");
    session.built.power.stapm.value.update();
    assert_eq!(paint::round_i32(session.built.power.stapm.scale.value()), 42, "numeric suffix prevents editing");
    snapshot_ui(session, "power-enabled");
    session.model.borrow_mut().select_fan_mode(FanMode::Custom);
    refresh_view(session);
    show_page(session, 2);
    snapshot_ui(session, "fans-custom");
    show_page(session, 3);
    for effect in ["off", "wave", "cycle"] {
        let index = session.built.keyboard.effect_ids.iter().position(|id| id == effect).unwrap();
        session.built.keyboard.effect_buttons[index].emit_clicked();
        let expects_color = effect == "wave";
        assert_eq!(session.built.keyboard.color_box.is_visible(), expects_color);
        assert_eq!(session.built.keyboard.color2_box.is_visible(), expects_color);
        assert_eq!(session.built.keyboard.zone_row.is_visible(), expects_color);
        assert_eq!(session.built.keyboard.speed_row.is_visible(), effect != "off");
        snapshot_ui(session, &format!("keyboard-{effect}"));
    }
    let old_color = session.model.borrow().state.lighting.color.clone();
    session.built.keyboard.hex.set_text("GGGGGG");
    session.built.keyboard.hex.emit_activate();
    assert_eq!(session.model.borrow().state.lighting.color, old_color, "invalid hex editing changes the lighting policy");
    let hue = session.strip_hue.get();
    session.built.keyboard.hex.set_text("808080");
    session.built.keyboard.hex.emit_activate();
    assert_eq!(session.strip_hue.get().to_bits(), hue.to_bits(), "an achromatic color loses the shade strip's hue");
    session.light_at.set(None);
    session.fan_at.set(None);
    *session.model.borrow_mut() = original;
    sync_controls(session);
    show_page(session, 0);
}

fn verify_hardware_variants(app: &libadwaita::Application) {
    for (intel, zones, name) in [(true, 1, "intel-single"), (false, 4, "amd-mux")] {
        let mut model = Model::offline(zones);
        model.host.intel = intel;
        model.host.frequency = Some(crate::FrequencyWindow { lower: 400_000, upper: 5_000_000, minimum: 800_000, maximum: 4_200_000, policies: 8, mixed: true });
        model.host.product = "HP Victus (test board)".into();
        model.host.gpu_name = "NVIDIA GeForce RTX 4060".into();
        model.host.mux = vec![crate::MuxChoice { label: "Hybrid".into(), index: 0 }, crate::MuxChoice { label: "Discrete".into(), index: 1 }];
        model.host.mux_index = 0;
        model.state.lighting.enabled = true;
        model.state.power.enabled = true;
        let model = Rc::new(RefCell::new(model));
        let (_, wake) = UnixStream::pair().unwrap();
        let session = build_ui(app, &model, &Arc::new(AtomicUsize::new(0)), &Arc::new(AtomicBool::new(true)),
            &Arc::new(AtomicBool::new(false)), &Rc::new(RefCell::new(None)), &Arc::new(Mutex::new(Vec::new())),
            &Arc::new(wake), &Rc::new(RefCell::new(None)), &Arc::new((Mutex::new(0), Condvar::new())));
        assert_eq!(session.built.power.stapm.row.is_visible(), !intel);
        assert_eq!(session.built.power.tctl.row.is_visible(), !intel);
        assert_eq!(session.built.power.uv_wrap.is_visible(), intel);
        assert_eq!(session.built.keyboard.zone_row.is_visible(), zones > 1);
        assert_eq!(session.built.home.gpu_name.text(), "RTX 4060");
        assert_eq!(session.built.home.footer_left.text(), "HP Victus");
        assert!(session.built.power.freq_note.text().contains("all 8 CPU policies"));
        assert!(session.built.power.freq_note.text().contains("Current limits differ"));
        for index in [0, 1, 3, 5] {
            show_page(&session, index);
            snapshot_ui(&session, &format!("{name}-{}", PAGE_NAMES[index]));
            assert_eq!(session.window.width(), page_width(index));
        }
        session.built.keyboard.idle_enabled.set_active(true);
        assert!(session.built.keyboard.idle.is_sensitive());
        session.built.keyboard.idle.set_value(45.0);
        assert_eq!(model.borrow().state.lighting.idle_timeout, 45);
        session.built.keyboard.idle_enabled.set_active(false);
        assert_eq!(model.borrow().state.lighting.idle_timeout, 0);
        session.built.power.slow.scale.set_value(45.0);
        if intel { assert_eq!(paint::round_i32(session.built.power.fast.scale.value()), 45); }
        assert!(session.built.power.apply.is_sensitive());
        control_result(&session, ControlRequest::Power(power_form(&session)), Ok(String::new()));
        assert!(!session.built.power.apply.is_sensitive(), "Apply stays disabled after a successful save");
        maintenance::show_release(&session, Release::Current);
        assert!(session.built.settings.update_status.is_visible());
        assert!(session.built.settings.update_status.has_css_class("ok"));
        maintenance::show_release(&session, Release::Failed);
        assert!(!session.built.settings.update_status.has_css_class("ok"));
        let response = Rc::new(Cell::new(None));
        let received = Rc::clone(&response);
        let dialog = widgets::message_dialog(&session.window, "Parity dialog", "Match Qt's compact modal layout.",
            &[("Cancel", gtk4::ResponseType::Cancel), ("Apply", gtk4::ResponseType::Accept)]);
        dialog.set_default_response(gtk4::ResponseType::Cancel);
        dialog.connect_response(move |window, id| { received.set(Some(id)); window.close(); });
        dialog.present();
        settle_ui();
        let window = gtk4::Window::list_toplevels().into_iter().filter_map(|widget| widget.downcast::<gtk4::Window>().ok())
            .find(|window| window.title().as_deref() == Some("Parity dialog")).unwrap();
        window.default_widget().and_downcast::<gtk4::Button>().unwrap().emit_clicked();
        assert_eq!(response.get(), Some(gtk4::ResponseType::Cancel));
        session.sensor_menu.borrow_mut().take().unwrap().unparent();
        quit(&session);
    }
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
