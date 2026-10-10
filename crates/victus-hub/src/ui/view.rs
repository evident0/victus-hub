use std::rc::Rc;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use gtk4::glib;
use gtk4::prelude::*;
use victus_core::{
    config_to_value, effect_is_animated, normalize_lighting_settings, request_key_for_graph, step_increment, ExtraSensor,
    FanMode, SensorSnapshot, CPU_TEMP_MAX_C, CURVE_RESPONSE_AGGRESSIVE, GPU_TEMP_MAX_C,
};

use super::actions::{
    apply_fan_mode, commit, control_result, frequency_note, power_form, refresh_power_actions, select_profile,
    show_status, take_due,
};
use super::graph;
use super::host::{notify_sensors, refresh_frequency};
use super::keyboard::{ignores_color, needs_color2, sync_color_entries};
use super::labels::{
    accent_hex, accent_rgb, curve_points, fan_description, fan_mode_from_key, fan_mode_key,
    light_subtitle, mark_key, mode_name, power_subtitle, program_frequency_slider, program_power_slider,
};
use super::maintenance::{show_diagnostics, show_release};
use super::pages;
use super::paint;
use super::readings::{
    cpu_caption, current_text, format_stat_unit, gpu_caption, merge_snapshot, power_head, ram_status, reading_source,
    rpm_text, sample_value, temp_text,
};
use super::session::{BackgroundEvent, Running, SensorUpdate, Session};
use super::shell::{arm_tick, quit, reveal};
use super::tray;
use super::widgets;
use crate::{lighting_request, power_status_line};

const ACCENT_CSS: &str = "\
window.victus button.seg-btn.on:not(.animated-seg) { background: {color}; } \
window.victus .linkish.on, window.victus label.accent { color: {color}; } \
window.victus button.linkish.on:not(.selection-link):hover:not(:disabled) { color: mix({color}, #ffffff, 0.22); } \
window.victus button.selection-link.on { color: #f4f4f4; } \
window.victus button.selection-link.on:not(.animated-link) { border-bottom-color: {color}; } \
window.victus button.accent-btn:not(:disabled) { background: {color}; } \
window.victus button.accent-btn:hover:not(:disabled) { background: mix({color}, #ffffff, 0.22); } \
window.victus scale highlight, window.victus scale slider, window.victus switch:checked { background: {color}; }";

pub(super) fn on_tick(session: &Session) {
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

pub(super) fn deliver_events(session: &Rc<Session>) {
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

pub(super) fn apply_snapshot(session: &Session, snapshot: SensorSnapshot, requested: &[String]) {
    let profile = session.model.borrow().profile;
    let signature = extra_signature(&snapshot.extra_sensors);
    if session.page.load(Ordering::Relaxed) == 4 && signature != *session.extra_sig.borrow() {
        *session.extra_sig.borrow_mut() = signature;
        let scroll = session.built.sensors.list.ancestor(gtk4::ScrolledWindow::static_type()).and_downcast::<gtk4::ScrolledWindow>();
        let saved_scroll = scroll.as_ref().map(|scroll| (scroll.vadjustment(), scroll.vadjustment().value()));
        let selected_key = session.built.sensors.list.selected_row().map(|row| row.widget_name().to_string());
        pages::clear_list(&session.built.sensors.list);
        let rows = pages::fill_sensors(&session.built.sensors.list, &snapshot.extra_sensors, &session.built.sensors.collapsed);
        if let Some(row) = rows.iter().find(|row| Some(&row.key) == selected_key.as_ref()) { session.built.sensors.list.select_row(Some(&row.row)); }
        *session.sensor_rows.borrow_mut() = rows;
        if let Some((adjustment, value)) = saved_scroll { let _idle = glib::idle_add_local_once(move || adjustment.set_value(value)); }
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

pub(super) fn flush_lighting(session: &Session) {
    let (request, offline, conf, lighting) = {
        let mut model = session.model.borrow_mut();
        let zones = model.zones;
        model.state.lighting = normalize_lighting_settings(&model.state.lighting, zones);
        (lighting_request(&model.state.lighting), model.offline, model.host.conf_path.clone(), model.state.lighting.clone())
    };
    // Python writes the local copy before the daemon push, and keeps it if the push fails.
    let saved = crate::persist::remember_lighting(offline, &conf, &lighting);
    let _ = commit(session, &request);
    note_save(session, saved);
    refresh_view(session);
}

pub(super) fn flush_fan(session: &Session) {
    let (request, offline, conf, fan) = {
        let model = session.model.borrow();
        (format!("fan-config\t{}", config_to_value(&model.state.fan)), model.offline, model.host.conf_path.clone(), model.state.fan.clone())
    };
    let saved = crate::persist::remember_fan(offline, &conf, &fan);
    let _ = commit(session, &request);
    note_save(session, saved);
    refresh_view(session);
}

pub(super) fn note_save(session: &Session, saved: Result<(), String>) {
    if let Err(error) = saved {
        let mut model = session.model.borrow_mut();
        if model.status.is_empty() {
            model.status = error;
        }
    }
    show_status(session);
}

pub(super) fn tick_animation(session: &Session) {
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

pub(super) fn refresh_view(session: &Session) {
    refresh_power_actions(session);
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
    widgets::mark(&[session.built.fans.cpu_link.clone(), session.built.fans.gpu_link.clone()], Some(usize::from(!session.curve_cpu.get())));
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
    let zones = session.model.borrow().zones;
    let head = if enabled {
        format!("{zones} zone{} · {}", if zones == 1 { "" } else { "s" }, effect.replace('_', " "))
    } else {
        "off".into()
    };
    session.built.keyboard.head_status.set_text(&head);
    session.built.keyboard.color2_box.set_visible(enabled && needs_color2(&effect));
    session.built.keyboard.speed_row.set_visible(animated);
    if session.accent_profile.get() != Some(profile) {
        let color = accent_hex(profile);
        widgets::set_accent_color(accent_rgb(profile));
        session.accent.load_from_string(&ACCENT_CSS.replace("{color}", color));
        session.accent_profile.set(Some(profile));
    }
    for area in [
        &session.built.home.mini, &session.built.keyboard.visual, &session.built.keyboard.chip,
        &session.built.keyboard.chip2, &session.built.keyboard.hue, &session.built.keyboard.shade,
        &session.built.fans.chart, &session.built.sidebar,
    ] {
        area.queue_draw();
    }
    show_status(session);
    arm_tick(session);
}

pub(super) fn paint_chart(session: &Session, cr: &gtk4::cairo::Context, width: i32, height: i32) {
    let model = session.model.borrow();
    let cpu = session.curve_cpu.get();
    let temp_max = if cpu { CPU_TEMP_MAX_C } else { GPU_TEMP_MAX_C };
    let points = curve_points(&model.state.fan, model.profile, cpu);
    let accent = if cpu { accent_rgb(model.profile) } else { paint::unit_rgb("#E2572C") };
    let current = None;
    let selected = session.selected_point.get();
    paint::chart(cr, f64::from(width), f64::from(height), temp_max, points, accent, selected, session.fan_hover.get(), current);
}

pub(super) fn apply_remote_state(session: &Session, value: &serde_json::Value) {
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
        for (slider, value) in [
            (&session.built.power.stapm, f64::from(remote_power.stapm_limit) / 1000.0),
            (&session.built.power.fast, f64::from(remote_power.fast_limit) / 1000.0),
            (&session.built.power.slow, f64::from(remote_power.slow_limit) / 1000.0),
            (&session.built.power.tctl, f64::from(remote_power.tctl_temp)),
            (&session.built.power.reapply, f64::from(remote_power.reapply_seconds)),
        ] {
            program_power_slider(slider, value);
        }
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
    let save_lighting = session.light_at.get().is_none() && model.state.lighting != old.lighting;
    let lighting = model.state.lighting.clone();
    let offline = model.offline;
    let conf = model.host.conf_path.clone();
    let frequency_changed = model.state.cpu_frequency != old_frequency || model.profile != profile;
    if model.profile != profile { session.selected_point.set(None); session.curve_selections.set([None, None]); session.drag.set(None); session.fan_hover.set(None); }
    drop(model);
    if save_lighting {
        note_save(session, crate::persist::remember_lighting(offline, &conf, &lighting));
    }
    session.suppress.set(false);
    if session.light_at.get().is_none() { sync_color_entries(session); }
    if frequency_changed { refresh_frequency(session); }
    refresh_view(session);
}

pub(super) fn apply_frequency_view(session: &Session, result: Result<crate::FrequencyWindow, String>) {
    let Ok(window) = result else {
        session.built.power.freq_sliders.set_sensitive(false);
        session.built.power.freq_note.set_text(&result.err().unwrap_or_default());
        session.model.borrow_mut().host.frequency = None;
        session.last_frequency.borrow_mut().take();
        refresh_power_actions(session);
        return;
    };
    if session.last_frequency.borrow().as_ref() == Some(&window) { return; }
    let pending = (paint::round_i32(session.built.power.freq_min.scale.value()), paint::round_i32(session.built.power.freq_max.scale.value()));
    let dirty = session.applied_freq.get().is_some_and(|applied| pending != applied);
    let (minimum, maximum) = if dirty { (pending.0.clamp(window.lower, window.upper), pending.1.clamp(window.lower, window.upper)) } else { (window.minimum, window.maximum) };
    session.suppress.set(true);
    program_frequency_slider(&session.built.power.freq_min, window.lower, window.upper, minimum.min(maximum));
    program_frequency_slider(&session.built.power.freq_max, window.lower, window.upper, maximum);
    session.suppress.set(false);
    session.applied_freq.set(Some((window.minimum, window.maximum)));
    session.built.power.freq_note.set_text(&frequency_note(&window));
    session.built.power.freq_sliders.set_sensitive(true);
    *session.last_frequency.borrow_mut() = Some(window.clone());
    session.model.borrow_mut().host.frequency = Some(window);
    refresh_power_actions(session);
}

pub(super) fn extra_signature(extras: &[ExtraSensor]) -> String {
    extras.iter().map(|extra| format!("{}:{}", extra.group, extra.key)).collect::<Vec<_>>().join("\n")
}

