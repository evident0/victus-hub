use std::cell::Cell;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use gtk4::glib::ControlFlow;
use gtk4::prelude::*;
use victus_core::{
    fan_mode_steps, normalize_lighting_settings, parse_status_response, power_to_value, transact, validate_frequency,
    validate_undervolt, FanMode, PowerPolicy, CURVE_RESPONSE_AGGRESSIVE,
};

use super::host::{notify_sensors, refresh_frequency};
use super::keyboard::sync_color_entries;
use super::labels::configure_scale;
use super::paint;
use super::session::{BackgroundEvent, ControlRequest, Session};
use super::shell::arm_tick;
use super::view::refresh_view;
use super::widgets;
use super::wiring::show_shortcut;
use crate::{connects_to_socket, fan_requests, Model};

pub(super) const PAGE_NAMES: [&str; 6] = ["home", "power", "fans", "keyboard", "sensors", "settings"];
const DEBOUNCE: Duration = Duration::from_millis(250);

pub(super) fn sync_controls(session: &Session) {
    session.suppress.set(true);
    let model = session.model.borrow();
    let power = &model.state.power;
    session.built.power.enabled.set_active(power.enabled);
    session.built.power.stapm.scale.set_value(f64::from(power.stapm_limit) / 1000.0);
    session.built.power.fast.scale.set_value(f64::from(power.fast_limit) / 1000.0);
    session.built.power.slow.scale.set_value(f64::from(power.slow_limit) / 1000.0);
    session.built.power.tctl.scale.set_value(f64::from(power.tctl_temp));
    session.built.power.reapply.scale.set_value(f64::from(power.reapply_seconds));
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
        session.built.power.freq_min.scale.set_value(f64::from(minimum));
        session.built.power.freq_max.scale.set_value(f64::from(maximum));
        session.applied_freq.set(Some((window.minimum, window.maximum)));
        session.built.power.freq_sliders.set_sensitive(true);
        session.built.power.freq_note.set_text(&frequency_note(&window));
    } else {
        session.built.power.freq_sliders.set_sensitive(false);
        for slider in [&session.built.power.freq_min, &session.built.power.freq_max] {
            configure_scale(&slider.scale, 0.0, 1.0, 1.0);
            slider.scale.set_value(0.0);
            slider.value.set_range(0.0, 0.001);
            slider.value.set_value(0.0);
        }
        let note = if model.host.frequency_error.is_empty() {
            "CPU frequency control is unavailable on this system"
        } else {
            model.host.frequency_error.as_str()
        };
        session.built.power.freq_note.set_text(note);
    }
    session.built.power.uv_core.scale.set_value(f64::from(model.host.undervolt.0));
    session.built.power.uv_cache.scale.set_value(f64::from(model.host.undervolt.1));
    session.applied_uv.set(None);
    session.built.settings.battery.set_active(model.state.battery_power_save);
    session.built.settings.nvidia.set_active(model.state.disable_nvidia_queries);
    session.built.settings.hardware.set_active(model.state.hardware_shortcuts);
    let response = if model.state.fan.curve_response == CURVE_RESPONSE_AGGRESSIVE { 1 } else { 0 };
    session.built.fans.response.set_selected(response);
    session.built.fans.min_change.set_value(model.state.fan.min_fan_change_pct);
    let lighting = normalize_lighting_settings(&model.state.lighting, model.zones);
    session.built.keyboard.brightness.set_value(f64::from(lighting.brightness));
    session.built.keyboard.speed.set_value(f64::from(lighting.speed));
    session.built.keyboard.idle_enabled.set_active(lighting.idle_timeout > 0);
    session.built.keyboard.idle.set_sensitive(lighting.idle_timeout > 0);
    if lighting.idle_timeout > 0 { session.built.keyboard.idle.set_value(f64::from(lighting.idle_timeout)); }
    drop(model);
    session.suppress.set(false);
    sync_color_entries(session);
    show_shortcut(session, None);
    refresh_view(session);
}

pub(super) fn show_page(session: &Session, page: usize) {
    let page = page.min(PAGE_NAMES.len() - 1);
    if page == 3 && !session.model.borrow().host.keyboard {
        return;
    }
    let previous = session.page.swap(page, Ordering::Relaxed);
    if previous != page {
        if let Some(from) = session.nav_position.get() { session.nav_slide.set(Some((from, Instant::now()))); }
        if !session.nav_ticking.replace(true) {
            let weak = session.self_weak.borrow().clone();
            session.built.sidebar.add_tick_callback(move |area, _| {
                let Some(session) = weak.upgrade() else { return ControlFlow::Break };
                area.queue_draw();
                if session.nav_slide.get().is_none() { session.nav_ticking.set(false); ControlFlow::Break } else { ControlFlow::Continue }
            });
        }
    }
    session.model.borrow_mut().page = page;
    if page == 4 && previous != page { session.built.sidebar.grab_focus(); }
    session.built.stack.set_visible_child_name(PAGE_NAMES[page]);
    session.built.root.set_size_request(page_width(page), 680);
    session.window.set_default_size(page_width(page), 680);
    session.built.sidebar.queue_draw();
    notify_sensors(session);
    if page == 1 { refresh_frequency(session); }
    arm_tick(session);
}

pub(super) fn page_width(page: usize) -> i32 {
    match page {
        2 | 3 | 4 => 700,
        _ => 480,
    }
}

pub(super) fn select_profile(session: &Session, profile: i32) {
    let profile = profile.clamp(0, 2);
    if session.profile_busy.replace(true) { return; }
    for button in &session.built.home.profile_buttons { button.set_sensitive(false); }
    session.model.borrow_mut().status = "Changing power profile…".into();
    show_status(session);
    submit_control(session, format!("set-profile\t{profile}"), ControlRequest::Profile(profile));
}

pub(super) fn apply_fan_mode(session: &Session, mode: FanMode) {
    if FanMode::from_config(&session.model.borrow().state.fan) == mode {
        refresh_view(session);
        return;
    }
    let pending_curve = session.fan_at.get().is_some();
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
        let saved = {
            let mut model = session.model.borrow_mut();
            if let Some(last) = fan_mode_steps(&model.state.fan, mode).last() {
                model.state.fan = last.clone();
            }
            crate::persist::remember_fan(model.offline, &model.host.conf_path, &model.state.fan)
        };
        super::view::note_save(session, saved);
    } else if pending_curve {
        let saved = {
            let model = session.model.borrow();
            crate::persist::remember_fan(model.offline, &model.host.conf_path, &model.state.fan)
        };
        super::view::note_save(session, saved);
    }
    refresh_view(session);
}

pub(super) fn apply_power(session: &Session) {
    if !session.built.power.enabled.is_sensitive() { return; }
    let policy = power_form(session);
    if policy == *session.applied_power.borrow() {
        return;
    }
    let request = format!("power-config\t{}", power_to_value(&policy));
    session.built.power.apply.set_sensitive(false);
    session.built.power.enabled.set_sensitive(false);
    if session.model.borrow().host.intel { session.built.power.status.set_text("Applying Intel power limits…"); }
    submit_control(session, request, ControlRequest::Power(policy));
}

pub(super) fn apply_frequency(session: &Session) {
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
    session.built.power.freq_min.row.set_sensitive(false);
    session.built.power.freq_max.row.set_sensitive(false);
    session.built.power.freq_note.set_text("Applying CPU frequency limits…");
    submit_control(session, format!("cpu-frequency-config\t{minimum}\t{maximum}"), ControlRequest::Frequency(minimum, maximum));
}

pub(super) fn apply_undervolt(session: &Session) {
    if !session.built.power.uv_apply.is_sensitive() { return; }
    let core = paint::round_i32(session.built.power.uv_core.scale.value()).clamp(-250, 0);
    let cache = paint::round_i32(session.built.power.uv_cache.scale.value()).clamp(-250, 0);
    if let Err(error) = validate_undervolt(core, cache) {
        session.built.power.uv_status.set_text(&error.to_string());
        return;
    }
    session.built.power.uv_apply.set_sensitive(false);
    session.built.power.uv_core.row.set_sensitive(false);
    session.built.power.uv_cache.row.set_sensitive(false);
    session.built.power.uv_status.set_text("Applying Intel undervolt…");
    submit_control(session, format!("intel-undervolt\t{core}\t{cache}"), ControlRequest::Undervolt(core, cache));
}

pub(super) fn submit_control(session: &Session, request: String, kind: ControlRequest) {
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

pub(super) fn control_result(session: &Session, kind: ControlRequest, result: Result<String, String>) {
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
            session.built.power.enabled.set_sensitive(true);
            if session.model.borrow().host.intel {
                session.built.power.status.set_text(&if success { "Intel PL1/PL2 limits applied.".into() } else {
                    format!("Could not apply Intel power limits: {}", result.as_ref().err().cloned().unwrap_or_default())
                });
            }
            if success {
                *session.applied_power.borrow_mut() = policy.clone();
                let saved = {
                    let mut model = session.model.borrow_mut();
                    model.state.power = policy;
                    crate::persist::remember_power(model.offline, &model.host.conf_path, model.host.intel, &model.state.power)
                };
                if let Err(error) = saved {
                    let mut model = session.model.borrow_mut();
                    if model.status.is_empty() { model.status = error; }
                }
            } else {
                session.suppress.set(true);
                session.built.power.enabled.set_active(session.model.borrow().state.power.enabled);
                session.suppress.set(false);
            }
            refresh_view(session);
        }
        ControlRequest::Frequency(minimum, maximum) => {
            session.built.power.freq_min.row.set_sensitive(true);
            session.built.power.freq_max.row.set_sensitive(true);
            if success {
                let saved = {
                    let mut model = session.model.borrow_mut();
                    model.state.cpu_frequency = Some((minimum, maximum));
                    crate::persist::remember_frequency(model.offline, &model.host.conf_path, minimum, maximum)
                };
                session.applied_freq.set(Some((minimum, maximum)));
                if let Err(error) = saved {
                    let mut model = session.model.borrow_mut();
                    if model.status.is_empty() { model.status = error; }
                }
            }
            else { session.built.power.freq_note.set_text(&format!("Could not apply CPU frequency: {}", result.as_ref().err().cloned().unwrap_or_default())); }
            refresh_power_actions(session);
            session.last_frequency.borrow_mut().take();
            refresh_frequency(session);
        }
        ControlRequest::Undervolt(core, cache) => {
            session.built.power.uv_apply.set_sensitive(true);
            session.built.power.uv_core.row.set_sensitive(true);
            session.built.power.uv_cache.row.set_sensitive(true);
            if !success {
                let message = result.as_ref().err().map_or("Undervolt failed", String::as_str);
                session.built.power.uv_status.set_text(&format!("Could not apply Intel undervolt: {message}"));
                show_status(session);
                return;
            }
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
            session.built.power.uv_status.set_text(&format!("Undervolt applied: core {core} mV · cache {cache} mV"));
        }
    }
    show_status(session);
}

pub(super) fn power_form(session: &Session) -> PowerPolicy {
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

pub(super) fn refresh_power_actions(session: &Session) {
    let power = &session.built.power;
    let enabled = power.enabled.is_active();
    power.limits.set_visible(enabled);
    power.note.set_visible(!enabled);
    power.apply.set_sensitive(enabled && power.enabled.is_sensitive() && power_form(session) != *session.applied_power.borrow());
    let frequency = (paint::round_i32(power.freq_min.scale.value()), paint::round_i32(power.freq_max.scale.value()));
    power.freq_apply.set_sensitive(power.freq_sliders.is_sensitive() && power.freq_min.row.is_sensitive()
        && (session.applied_freq.get() != Some(frequency) || session.model.borrow().state.cpu_frequency != Some(frequency)));
    for button in [&power.apply, &power.freq_apply, &power.uv_apply] {
        button.set_cursor_from_name(Some(if button.is_sensitive() { "pointer" } else { "default" }));
    }
}

pub(super) fn frequency_note(window: &crate::FrequencyWindow) -> String {
    format!("Applies to all {} CPU policies. {}Hardware range: {:.3}–{:.3} MHz.", window.policies,
        if window.mixed { "Current limits differ between policies. " } else { "" }, f64::from(window.lower) / 1000.0, f64::from(window.upper) / 1000.0)
}

pub(super) fn confirm_mux(session: &Rc<Session>, index: i32, label: String) {
    if session.model.borrow().host.mux_index == index {
        return;
    }
    let dialog = widgets::message_dialog(&session.window, "MUX Switch",
        &format!("Switch to {label}?\n\nYou must restart for this change to take effect."),
        &[("Cancel", gtk4::ResponseType::Cancel), ("Apply", gtk4::ResponseType::Accept)]);
    dialog.set_default_response(gtk4::ResponseType::Cancel);
    let weak = Rc::downgrade(session);
    dialog.connect_response(move |dialog, response| {
        dialog.close();
        if response != gtk4::ResponseType::Accept {
            return;
        }
        let Some(session) = weak.upgrade() else { return };
        if commit(&session, &format!("gpu-mux-mode\t{index}")) {
            session.model.borrow_mut().host.mux_index = index;
            refresh_view(&session);
        }
    });
    dialog.present();
}

pub(super) fn commit(session: &Session, request: &str) -> bool {
    let ok = {
        let mut model = session.model.borrow_mut();
        dispatch(&mut model, request)
    };
    show_status(session);
    ok
}

pub(super) fn dispatch(model: &mut Model, request: &str) -> bool {
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

pub(super) fn show_status(session: &Session) {
    let status = session.model.borrow().status.clone();
    if !status.is_empty() && status != "Offline preview" {
        let mut log = session.log.borrow_mut();
        if log.back() != Some(&status) { log.push_back(status.clone()); while log.len() > 80 { log.pop_front(); } }
    }
    session.built.status.set_visible(!status.is_empty() && status != "Offline preview");
    session.built.status.set_text(&status);
}

pub(super) fn schedule(session: &Session, lighting: bool) {
    let slot = if lighting { &session.light_at } else { &session.fan_at };
    slot.set(Some(Instant::now() + DEBOUNCE));
    arm_tick(session);
}

pub(super) fn take_due(slot: &Cell<Option<Instant>>) -> bool {
    match slot.get() {
        Some(deadline) if Instant::now() >= deadline => {
            slot.set(None);
            true
        }
        _ => false,
    }
}

