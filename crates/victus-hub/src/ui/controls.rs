//! Navigation, profile, power, and fan control event bindings.

use std::rc::{Rc, Weak};

use gtk4::prelude::*;
use victus_core::{FanMode, CURVE_RESPONSE_AGGRESSIVE, CURVE_RESPONSE_SMOOTH};

use super::actions::{
    apply_fan_mode, apply_frequency, apply_power, apply_undervolt, confirm_mux, refresh_power_actions,
    select_profile, show_page,
};
use super::curve::{
    begin_fan_drag, chart_size, delete_fan_point, end_fan_drag, schedule_fan_if_custom, update_fan_drag,
};
use super::labels::{curve_points, fan_mode_from_key};
use super::session::Session;
use super::shell::on_click;
use super::view::refresh_view;
use super::{paint, widgets};

pub(super) fn wire(session: &Rc<Session>) {
    wire_sidebar(session);
    wire_home(session);
    wire_power(session);
    wire_fans(session);
}

fn wire_sidebar(session: &Rc<Session>) {
    let weak = Rc::downgrade(session);
    let click = gtk4::GestureClick::new();
    click.set_button(1);
    click.connect_released(move |_, _, x, y| {
        let Some(session) = weak.upgrade() else { return };
        let keyboard = session.model.borrow().host.keyboard;
        let height = f64::from(session.built.sidebar.height());
        if let Some(page) = paint::nav_page(keyboard, height, x, y) { show_page(&session, page); }
    });
    session.built.sidebar.add_controller(click);
    let weak = Rc::downgrade(session);
    let motion = gtk4::EventControllerMotion::new();
    motion.connect_motion(move |_, x, y| {
        sidebar_hover(&weak, x, y);
    });
    let weak = Rc::downgrade(session);
    motion.connect_enter(move |_, x, y| {
        sidebar_hover(&weak, x, y);
    });
    let weak = Rc::downgrade(session);
    motion.connect_leave(move |_| {
        let Some(session) = weak.upgrade() else { return };
        session.hover.set(None);
        session.built.sidebar.set_cursor_from_name(None);
        session.built.sidebar.set_tooltip_text(None);
        session.built.sidebar.queue_draw();
    });
    session.built.sidebar.add_controller(motion);
}

fn sidebar_hover(weak: &Weak<Session>, x: f64, y: f64) {
    let Some(session) = weak.upgrade() else { return };
    let keyboard = session.model.borrow().host.keyboard;
    let height = f64::from(session.built.sidebar.height());
    let hover = paint::nav_page(keyboard, height, x, y);
    if session.hover.replace(hover) != hover {
        session.built.sidebar.set_cursor_from_name(hover.map(|_| "pointer"));
        session.built.sidebar.set_tooltip_text(hover.map(|page| crate::PAGES[page]));
        session.built.sidebar.queue_draw();
    }
}

fn wire_home(session: &Rc<Session>) {
    for (index, button) in session.built.home.profile_buttons.iter().enumerate() {
        on_click(session, button, move |session| select_profile(session, i32::try_from(index).unwrap_or(0)));
    }
    for (button, key) in session.built.home.fan_buttons.iter().zip(session.built.home.fan_keys.clone()) {
        on_click(session, button, move |session| {
            let Some(mode) = fan_mode_from_key(&key) else { return };
            let current = FanMode::from_config(&session.model.borrow().state.fan);
            if mode == FanMode::Custom && current == FanMode::Custom {
                show_page(session, 2);
            } else {
                apply_fan_mode(session, mode);
            }
        });
    }
    for (button, page) in [(&session.built.home.curve, 2), (&session.built.home.power, 1), (&session.built.home.light_row, 3)] {
        on_click(session, button, move |session| show_page(session, page));
    }
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
        let weak = Rc::downgrade(session);
        slider.scale.connect_value_changed(move |_| {
            if let Some(session) = weak.upgrade().filter(|session| !session.suppress.get()) { refresh_power_actions(&session); }
        });
    }
    couple_limits(session, &session.built.power.freq_min.scale, &session.built.power.freq_max.scale);
    if session.model.borrow().host.intel { couple_limits(session, &session.built.power.slow.scale, &session.built.power.fast.scale); }
    let weak = Rc::downgrade(session);
    session.built.power.enabled.connect_active_notify(move |switch| {
        let Some(session) = weak.upgrade() else { return };
        let on = switch.is_active();
        session.built.power.limits.set_visible(on);
        session.built.power.note.set_visible(!on);
        if !session.suppress.get() { apply_power(&session); }
    });
    on_click(session, &session.built.power.apply, apply_power);
    on_click(session, &session.built.power.freq_apply, apply_frequency);
    on_click(session, &session.built.power.uv_apply, apply_undervolt);
}

fn bind_power_label(slider: &widgets::Slider, unit: &'static str) {
    slider.unit.set_text(unit);
    widgets::spin_suffix(&slider.value, unit);
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
        on_click(session, button, move |session| {
            if let Some(mode) = fan_mode_from_key(&key) { apply_fan_mode(session, mode); }
        });
    }
    for (button, cpu) in [(&session.built.fans.cpu_link, true), (&session.built.fans.gpu_link, false)] {
        on_click(session, button, move |session| {
            if session.curve_cpu.get() == cpu { return; }
            let mut selections = session.curve_selections.get();
            selections[usize::from(!session.curve_cpu.get())] = session.selected_point.get();
            session.curve_selections.set(selections);
            session.curve_cpu.set(cpu);
            session.drag.set(None);
            session.selected_point.set(selections[usize::from(!cpu)]);
            session.fan_hover.set(None);
            refresh_view(session);
        });
    }
    let weak = Rc::downgrade(session);
    session.built.fans.response.connect_selected_notify(move |dropdown| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() { return; }
        let response = if dropdown.selected() == 1 { CURVE_RESPONSE_AGGRESSIVE } else { CURVE_RESPONSE_SMOOTH };
        response.clone_into(&mut session.model.borrow_mut().state.fan.curve_response);
        schedule_fan_if_custom(&session);
    });
    let weak = Rc::downgrade(session);
    session.built.fans.min_change.connect_value_changed(move |spin| {
        let Some(session) = weak.upgrade() else { return };
        if session.suppress.get() { return; }
        session.model.borrow_mut().state.fan.min_fan_change_pct = spin.value().max(0.0);
        schedule_fan_if_custom(&session);
    });
    let drag = gtk4::GestureDrag::new();
    drag.set_button(1);
    drag.set_exclusive(true);
    let weak = Rc::downgrade(session);
    drag.connect_drag_begin(move |_, x, y| {
        if let Some(session) = weak.upgrade() { begin_fan_drag(&session, x, y); }
    });
    let weak = Rc::downgrade(session);
    drag.connect_drag_update(move |gesture, dx, dy| {
        let Some(session) = weak.upgrade() else { return };
        let Some((origin_x, origin_y)) = gesture.start_point() else { return };
        update_fan_drag(&session, origin_x + dx, origin_y + dy);
    });
    let weak = Rc::downgrade(session);
    drag.connect_drag_end(move |_, _, _| {
        if let Some(session) = weak.upgrade() { end_fan_drag(&session); }
    });
    session.built.fans.chart.add_controller(drag);
    let click = gtk4::GestureClick::new();
    click.set_button(3);
    click.set_exclusive(true);
    let weak = Rc::downgrade(session);
    click.connect_pressed(move |_, _, x, y| {
        if let Some(session) = weak.upgrade() { delete_fan_point(&session, x, y); }
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
