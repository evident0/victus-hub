use gtk4::prelude::*;
use victus_core::{normalize_fan_points, FanMode, FanPoint, CPU_TEMP_MAX_C, GPU_TEMP_MAX_C};

use super::actions::schedule;
use super::labels::curve_points_mut;
use super::paint;
use super::session::Session;

fn plot_for(session: &Session) -> paint::Plot {
    let (width, height, temp_max) = chart_size(session);
    paint::Plot::new(width, height, temp_max)
}

fn with_points<T>(session: &Session, body: impl FnOnce(&mut Vec<FanPoint>) -> T) -> T {
    let mut model = session.model.borrow_mut();
    let profile = model.profile;
    let cpu = session.curve_cpu.get();
    let points = curve_points_mut(&mut model.state.fan, profile, cpu);
    body(points)
}

pub(super) fn schedule_fan_if_custom(session: &Session) {
    let custom = FanMode::from_config(&session.model.borrow().state.fan) == FanMode::Custom;
    if custom {
        schedule(session, false);
    }
}

pub(super) fn begin_fan_drag(session: &Session, x: f64, y: f64) {
    session.built.fans.chart.grab_focus();
    let plot = plot_for(session);
    let index = with_points(session, |points| {
        plot.nearest(points, x, y).or_else(|| {
            let (temp, speed) = plot.temp_speed(x, y);
            paint::insert_point(points, temp, speed)
        })
    });
    session.drag.set(index);
    session.selected_point.set(index);
    session.built.fans.chart.queue_draw();
}

pub(super) fn update_fan_drag(session: &Session, x: f64, y: f64) {
    let Some(index) = session.drag.get() else { return };
    let plot = plot_for(session);
    let (temp, speed) = plot.temp_speed(x, y);
    with_points(session, |points| paint::move_point(points, index, temp, speed));
    session.built.fans.chart.queue_draw();
}

pub(super) fn end_fan_drag(session: &Session) {
    if session.drag.get().is_none() {
        return;
    }
    session.drag.set(None);
    normalize_curve(session);
    schedule_fan_if_custom(session);
    session.built.fans.chart.queue_draw();
}

pub(super) fn delete_fan_point(session: &Session, x: f64, y: f64) {
    let plot = plot_for(session);
    let removed = with_points(session, |points| {
        let Some(index) = plot.nearest(points, x, y) else { return false };
        let last = points.len().saturating_sub(1);
        if index == 0 || index == last || points.len() <= 2 { return false; }
        points.remove(index);
        true
    });
    if !removed { return; }
    session.selected_point.set(None);
    session.drag.set(None);
    normalize_curve(session);
    schedule_fan_if_custom(session);
    session.built.fans.chart.queue_draw();
}

pub(super) fn delete_selected_point(session: &Session) {
    let Some(index) = session.selected_point.get() else { return };
    let removed = with_points(session, |points| {
        if index == 0 || index + 1 >= points.len() { return false; }
        points.remove(index);
        true
    });
    if !removed { return; }
    session.selected_point.set(None);
    normalize_curve(session);
    schedule_fan_if_custom(session);
    session.built.fans.chart.queue_draw();
}

pub(super) fn normalize_curve(session: &Session) {
    let temp_max = if session.curve_cpu.get() { CPU_TEMP_MAX_C } else { GPU_TEMP_MAX_C };
    with_points(session, |points| {
        let normalized = normalize_fan_points(std::mem::take(points), temp_max);
        *points = normalized;
    });
}

pub(super) fn chart_size(session: &Session) -> (f64, f64, i32) {
    let width = f64::from(session.built.fans.chart.width()).max(1.0);
    let height = f64::from(session.built.fans.chart.height()).max(1.0);
    let temp_max = if session.curve_cpu.get() { CPU_TEMP_MAX_C } else { GPU_TEMP_MAX_C };
    (width, height, temp_max)
}

