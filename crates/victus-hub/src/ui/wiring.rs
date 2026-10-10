use std::cell::RefCell;
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::atomic::Ordering;

use gtk4::gdk::{Key, ModifierType};
use gtk4::glib::{self, Propagation};
use gtk4::prelude::*;
use victus_core::{is_modifier, keybind_label, validate_shortcut, KEY_LEFTALT, KEY_LEFTCTRL, KEY_LEFTMETA, KEY_LEFTSHIFT};

use super::actions::commit;
use crate::Model;
use super::curve::delete_selected_point;
use super::graph;
use super::keyboard::{active_color, paint_keys};
use super::maintenance::{check_updates, collect_diagnostics};
use super::paint;
use super::session::Session;
use super::shell::{arm_tick, on_click, on_draw};
use super::view::paint_chart;
use crate::upsert_program_shortcut;

pub(super) struct GraphPick {
    pub(super) key: String,
    pub(super) name: String,
    pub(super) group: String,
    pub(super) unit: String,
    pub(super) min: f64,
    pub(super) max: f64,
    pub(super) graphable: bool,
}

pub(super) fn wire_sensors(session: &Rc<Session>) {
    // Parent the menu to the list so its anchor is in list coordinates.
    // A popover opened during the right-click press is dismissed by that same
    // click, so the menu is shown on release, after the event has finished.
    let menu = gtk4::Popover::new();
    menu.set_parent(&session.built.sensors.list);
    menu.set_has_arrow(false);
    menu.set_position(gtk4::PositionType::Bottom);
    menu.add_css_class("sensor-menu");
    let item = gtk4::Button::with_label("Graph");
    item.add_css_class("flat");
    item.set_hexpand(true);
    item.set_halign(gtk4::Align::Fill);
    menu.set_child(Some(&item));
    *session.sensor_menu.borrow_mut() = Some(menu);

    let pending = Rc::new(RefCell::new(None::<GraphPick>));
    let click = gtk4::GestureClick::new();
    click.set_button(3);
    click.set_propagation_phase(gtk4::PropagationPhase::Capture);
    let weak = Rc::downgrade(session);
    let pending_click = Rc::clone(&pending);
    click.connect_released(move |gesture, n_press, x, y| {
        if n_press != 1 {
            return;
        }
        gesture.set_state(gtk4::EventSequenceState::Claimed);
        let Some(session) = weak.upgrade() else { return };
        let Some((pick, anchor)) = sensor_at_point(&session, x, y) else { return };
        let graphable = pick.graphable;
        *pending_click.borrow_mut() = Some(pick);
        let Some(menu) = session.sensor_menu.borrow().clone() else { return };
        if let Some(button) = menu.child().and_downcast::<gtk4::Button>() {
            button.set_sensitive(graphable);
            button.set_tooltip_text(if graphable { None } else { Some("Not graphable") });
        }
        let _idle = glib::idle_add_local_once(move || {
            menu.set_pointing_to(Some(&anchor));
            menu.popup();
        });
    });
    session.built.sensors.list.add_controller(click);
    let keys = gtk4::EventControllerKey::new();
    let list = session.built.sensors.list.downgrade();
    keys.connect_key_pressed(move |_, key, _, _| {
        if !matches!(key, Key::Left | Key::Right) { return Propagation::Proceed; }
        let Some(list) = list.upgrade() else { return Propagation::Proceed };
        let Some(row) = list.selected_row().filter(|row| row.widget_name().is_empty()) else { return Propagation::Proceed };
        let Some(button) = row.child().and_then(|line| line.first_child()).and_downcast::<gtk4::Button>() else { return Propagation::Proceed };
        let Some(arrow) = button.child().and_downcast::<gtk4::Label>() else { return Propagation::Proceed };
        if (key == Key::Left && arrow.text() == "▾") || (key == Key::Right && arrow.text() == "▸") { button.emit_clicked(); }
        Propagation::Stop
    });
    session.built.sensors.list.add_controller(keys);

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

pub(super) fn wire_settings(session: &Rc<Session>) {
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
pub(super) enum SwitchKind {
    Battery,
    Nvidia,
    Hardware,
}

impl SwitchKind {
    fn command(self) -> &'static str {
        match self {
            Self::Battery => "battery-power-save",
            Self::Nvidia => "disable-nvidia-queries",
            Self::Hardware => "hardware-shortcuts",
        }
    }

    fn store(self, model: &mut Model, active: bool) {
        match self {
            Self::Battery => model.state.battery_power_save = active,
            Self::Nvidia => model.state.disable_nvidia_queries = active,
            Self::Hardware => model.state.hardware_shortcuts = active,
        }
    }
}

pub(super) fn bind_switch(session: &Rc<Session>, switch: &gtk4::Switch, kind: SwitchKind) {
    let weak = Rc::downgrade(session);
    switch.connect_active_notify(move |switch| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() { return; }
        let active = switch.is_active();
        kind.store(&mut session.model.borrow_mut(), active);
        if !commit(&session, &format!("{}\t{}", kind.command(), i32::from(active))) {
            session.suppress.set(true);
            switch.set_active(!active);
            kind.store(&mut session.model.borrow_mut(), !active);
            session.suppress.set(false);
            return;
        }
        // Battery power-save is stored by the daemon. Python writes that
        // checkbox only when it copies daemon state back at startup.
        let saved = {
            let model = session.model.borrow();
            match kind {
                SwitchKind::Nvidia => crate::persist::remember_nvidia(model.offline, &model.host.conf_path, active),
                SwitchKind::Hardware => crate::persist::remember_hardware_shortcuts(model.offline, &model.host.conf_path, active),
                SwitchKind::Battery => Ok(()),
            }
        };
        super::view::note_save(&session, saved);
    });
}

pub(super) fn modifier_codes(state: ModifierType) -> Vec<i32> {
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

pub(super) fn save_shortcut(session: &Session, mods: &[i32], key: i32) {
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

pub(super) fn show_shortcut(session: &Session, override_text: Option<&str>) {
    if let Some(text) = override_text {
        session.built.settings.shortcut.set_text(text);
        return;
    }
    let model = session.model.borrow();
    session.built.settings.shortcut.set_text(&keybind_label(&model.host.shortcut_mods, model.host.shortcut_key));
    session.built.settings.shortcut_clear.set_sensitive(model.host.shortcut_key != 0);
}

pub(super) fn sensor_at_point(session: &Session, x: f64, y: f64) -> Option<(GraphPick, gtk4::gdk::Rectangle)> {
    let x_px = finite_i32(x)?;
    let y_px = finite_i32(y)?;
    let row = session.built.sensors.list.row_at_y(y_px)?;
    let name = row.widget_name();
    if name.is_empty() {
        return None;
    }
    let key = name.to_string();
    let rows = session.sensor_rows.borrow();
    let sensor = rows.iter().find(|item| item.key == key)?;
    let pick = GraphPick {
        key: sensor.key.clone(),
        name: sensor.name.clone(),
        group: sensor.group.clone(),
        unit: sensor.unit.clone(),
        min: sensor.min,
        max: sensor.max,
        graphable: sensor.graphable,
    };
    Some((pick, gtk4::gdk::Rectangle::new(x_px, y_px, 1, 1)))
}

pub(super) fn finite_i32(value: f64) -> Option<i32> {
    if !value.is_finite() {
        return None;
    }
    let rounded = value.round();
    if rounded < f64::from(i32::MIN) || rounded > f64::from(i32::MAX) {
        return None;
    }
    Some(rounded as i32)
}

pub(super) fn wire_draws(session: &Rc<Session>) {
    on_draw(session, &session.built.sidebar, |session, cr, width, height| {
        let keyboard = session.model.borrow().host.keyboard;
        let selected = session.page.load(Ordering::Relaxed);
        let target = paint::nav_buttons(keyboard, f64::from(height)).into_iter().find(|button| button.page == selected).map_or(20.0, |button| button.y);
        let y = if let Some((from, start)) = session.nav_slide.get() {
            let fraction = (start.elapsed().as_secs_f64() / 0.28).min(1.0);
            if fraction >= 1.0 { session.nav_slide.set(None); }
            from + (target - from) * (1.0 - (1.0 - fraction).powi(3))
        } else { target };
        session.nav_position.set(Some(y));
        paint::sidebar_with_pill(cr, f64::from(width), f64::from(height), keyboard, selected, session.hover.get(), Some(y));
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
        paint::shade_strip(cr, f64::from(width), f64::from(height), session.strip_hue.get(), paint::unit_rgb(&hex));
    });
}

