//! Keyboard lighting controls and preview state.

use std::rc::Rc;

use gtk4::prelude::*;
use gtk4::DrawingArea;
use victus_core::{lighting_frames, normalize_lighting_settings};

use crate::Model;
use super::actions::schedule;
use super::session::Session;
use super::shell::on_click;
use super::view::refresh_view;
use super::paint;

pub(super) fn wire(session: &Rc<Session>) {
    for (button, id) in session.built.keyboard.effect_buttons.iter().zip(session.built.keyboard.effect_ids.clone()) {
        on_click(session, button, move |session| select_effect(session, &id));
    }
    for (index, button) in session.built.keyboard.zone_buttons.iter().enumerate() {
        on_click(session, button, move |session| {
            let zones = session.model.borrow().zones;
            let target = if zones > 1 && index + 1 == session.built.keyboard.zone_buttons.len() {
                -1
            } else {
                i32::try_from(index).unwrap_or(0)
            };
            session.zone_target.set(target);
            refresh_view(session);
            sync_color_entries(session);
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
        scale.set_tooltip_text(Some(&format!("Backlight brightness: {}/255", paint::round_i32(scale.value()))));
        if session.suppress.get() { return; }
        session.model.borrow_mut().state.lighting.brightness = paint::round_i32(scale.value()).clamp(0, 255);
        schedule(&session, true);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.speed.connect_value_changed(move |scale| {
        let Some(session) = weak.upgrade() else { return };
        let speed = paint::round_i32(scale.value()).clamp(1, 100);
        session.built.keyboard.speed_value.set_text(&speed.to_string());
        scale.set_tooltip_text(Some(&format!("Effect speed: {speed}/100")));
        if session.suppress.get() { return; }
        session.model.borrow_mut().state.lighting.speed = speed;
        schedule(&session, true);
    });
    let weak = Rc::downgrade(session);
    session.built.keyboard.idle.connect_value_changed(move |spin| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() || !session.built.keyboard.idle_enabled.is_active() { return; }
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
    entry.connect_activate(move |entry| {
        if let Some(session) = weak.upgrade() { commit_hex_entry(&session, entry, secondary); }
    });
    let focus = gtk4::EventControllerFocus::new();
    let weak = Rc::downgrade(session);
    let entry_weak = entry.downgrade();
    focus.connect_leave(move |_| {
        if let (Some(session), Some(entry)) = (weak.upgrade(), entry_weak.upgrade()) { commit_hex_entry(&session, &entry, secondary); }
    });
    entry.add_controller(focus);
}

fn commit_hex_entry(session: &Session, entry: &gtk4::Entry, secondary: bool) {
    if session.suppress.get() { return; }
    let text = entry.text();
    let text = text.trim().trim_start_matches('#');
    if text.len() == 6 && text.chars().all(|ch| ch.is_ascii_hexdigit()) {
        assign_hex(session, &format!("#{}", text.to_ascii_uppercase()), secondary, true);
    }
    sync_color_entries(session);
}

fn bind_strip(session: &Rc<Session>, area: &DrawingArea, hue_strip: bool) {
    let click = gtk4::GestureClick::new();
    click.set_button(1);
    let weak = Rc::downgrade(session);
    click.connect_pressed(move |_, _, x, _| {
        if let Some(session) = weak.upgrade() { set_strip_color(&session, hue_strip, x); }
    });
    area.add_controller(click);
    let drag = gtk4::GestureDrag::new();
    drag.set_button(1);
    let weak = Rc::downgrade(session);
    drag.connect_drag_update(move |gesture, dx, _| {
        let Some(session) = weak.upgrade() else { return };
        let Some((origin, _)) = gesture.start_point() else { return };
        set_strip_color(&session, hue_strip, origin + dx);
    });
    area.add_controller(drag);
}

fn set_strip_color(session: &Session, hue_strip: bool, x: f64) {
    let area = if hue_strip { &session.built.keyboard.hue } else { &session.built.keyboard.shade };
    let cell = ((x / f64::from(area.width()).max(1.0)) * 36.0).floor().clamp(0.0, 35.0);
    let hue = session.strip_hue.get();
    let (red, green, blue) = if hue_strip {
        paint::hsl_to_rgb(cell / 36.0, 0.85, 0.55)
    } else {
        let fraction = cell / 35.0;
        paint::hsl_to_rgb(hue, 0.28 + fraction * 0.6, 0.95 - fraction * 0.76)
    };
    assign_hex(session, &paint::hex_from_unit(red, green, blue), false, false);
}

pub(super) fn sync_color_entries(session: &Session) {
    session.suppress.set(true);
    let model = session.model.borrow();
    let primary = digits(&active_color(&model, session.zone_target.get()));
    let (hue, saturation, _) = hsv_parts(&format!("#{primary}"));
    if saturation > 0.0 { session.strip_hue.set(hue); }
    let secondary = digits(&model.state.lighting.color2);
    drop(model);
    session.built.keyboard.hex.set_text(&primary);
    session.built.keyboard.hex2.set_text(&secondary);
    session.suppress.set(false);
}

fn select_effect(session: &Session, id: &str) {
    {
        let mut model = session.model.borrow_mut();
        model.state.lighting.enabled = id != "off";
        if id != "off" { id.clone_into(&mut model.state.lighting.effect); }
    }
    schedule(session, true);
    refresh_view(session);
}

fn assign_hex(session: &Session, hex: &str, secondary: bool, from_entry: bool) {
    {
        let mut model = session.model.borrow_mut();
        if secondary {
            hex.clone_into(&mut model.state.lighting.color2);
        } else {
            assign_color(&mut model, session.zone_target.get(), hex.to_owned());
        }
    }
    if !from_entry {
        let entry = if secondary { &session.built.keyboard.hex2 } else { &session.built.keyboard.hex };
        session.suppress.set(true);
        entry.set_text(&digits(hex));
        session.suppress.set(false);
    }
    if !secondary {
        let (hue, saturation, _) = hsv_parts(hex);
        if saturation > 0.0 { session.strip_hue.set(hue); }
    }
    for area in [&session.built.keyboard.chip, &session.built.keyboard.chip2, &session.built.keyboard.hue, &session.built.keyboard.shade] {
        area.queue_draw();
    }
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
        for slot in &mut settings.zone_colors { slot.clone_from(&hex); }
        return;
    }
    let index = usize::try_from(target).unwrap_or(0);
    if let Some(slot) = settings.zone_colors.get_mut(index) { slot.clone_from(&hex); }
    if index == 0 { settings.color = hex; }
}

pub(super) fn paint_keys(session: &Session, cr: &gtk4::cairo::Context, width: i32, height: i32, compact: bool) {
    let model = session.model.borrow();
    let settings = normalize_lighting_settings(&model.state.lighting, model.zones);
    let frames = lighting_frames(&settings, model.zones, session.anim.get());
    let zones = model.zones;
    let enabled = settings.enabled;
    drop(model);
    paint::keyboard(cr, f64::from(width), f64::from(height), compact, zones, enabled, &frames);
}

pub(super) fn hsv_parts(hex: &str) -> (f64, f64, f64) {
    let (red, green, blue) = paint::unit_rgb(hex);
    paint::rgb_to_hsv(red, green, blue)
}

pub(super) fn needs_color2(effect: &str) -> bool {
    matches!(effect, "wave" | "gradient")
}

pub(super) fn ignores_color(effect: &str) -> bool {
    matches!(effect, "cycle" | "wave_rainbow" | "aurora" | "disco")
}

pub(super) fn active_color(model: &Model, target: i32) -> String {
    if target < 0 { return model.state.lighting.color.clone(); }
    let index = usize::try_from(target).unwrap_or(0);
    model.state.lighting.zone_colors.get(index).cloned().unwrap_or_else(|| model.state.lighting.color.clone())
}

fn digits(hex: &str) -> String {
    hex.trim().trim_start_matches('#').to_ascii_uppercase()
}
