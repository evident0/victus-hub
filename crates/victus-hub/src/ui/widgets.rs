//! Stock GTK pieces styled like the previous Ohman panel.

use gtk4::prelude::*;
use gtk4::{Align, Box, Button, DropDown, Entry, Label, ListBox, ListBoxRow, Orientation, PolicyType, Scale, ScrolledWindow, SpinButton, Stack, Switch, Widget};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Instant;

thread_local! {
    static INDICATOR_COLOR: Cell<(f64, f64, f64)> = const { Cell::new((63.0 / 255.0, 140.0 / 255.0, 1.0)) };
    static INDICATORS: RefCell<Vec<gtk4::glib::WeakRef<gtk4::DrawingArea>>> = const { RefCell::new(Vec::new()) };
}

pub fn set_accent_color(color: (f64, f64, f64)) {
    INDICATOR_COLOR.set(color);
    INDICATORS.with_borrow_mut(|areas| areas.retain(|area| {
        if let Some(area) = area.upgrade() { area.queue_draw(); true } else { false }
    }));
}

#[derive(Default)]
struct SelectionAnimation {
    selected: Cell<Option<usize>>,
    current: Cell<Option<[f64; 4]>>,
    from: Cell<Option<[f64; 4]>>,
    start: Cell<Option<Instant>>,
    ticking: Cell<bool>,
}

fn selection_overlay(row: &Box, buttons: &[Button], underline: bool) -> gtk4::Overlay {
    let overlay = gtk4::Overlay::new();
    overlay.set_vexpand(false);
    let area = gtk4::DrawingArea::new();
    area.set_can_target(false);
    area.set_hexpand(true);
    area.set_vexpand(false);
    overlay.set_child(Some(&area));
    overlay.add_overlay(row);
    overlay.set_measure_overlay(row, true);
    INDICATORS.with_borrow_mut(|areas| areas.push(area.downgrade()));
    let state = Rc::new(SelectionAnimation::default());
    let draw_state = Rc::clone(&state);
    let weak_buttons = buttons.iter().map(ObjectExt::downgrade).collect::<Vec<_>>();
    area.set_draw_func(move |area, cr, _, height| {
        let Some(button) = draw_state.selected.get().and_then(|index| weak_buttons.get(index)).and_then(gtk4::glib::WeakRef::upgrade).filter(|button| button.has_css_class("on")) else {
            draw_state.start.set(None);
            return;
        };
        let Some(bounds) = button.compute_bounds(area) else { return };
        let target = [f64::from(bounds.x()), f64::from(bounds.y()), f64::from(bounds.width()), f64::from(bounds.height())];
        let fraction = draw_state.start.get().map_or(1.0, |start| (start.elapsed().as_secs_f64() / 0.28).min(1.0));
        let eased = 1.0 - (1.0 - fraction).powi(3);
        let from = draw_state.from.get().unwrap_or(target);
        let rect = std::array::from_fn(|index| from[index] + (target[index] - from[index]) * eased);
        draw_state.current.set(Some(rect));
        if fraction >= 1.0 { draw_state.start.set(None); }
        let (red, green, blue) = INDICATOR_COLOR.get();
        cr.set_source_rgb(red, green, blue);
        if underline {
            cr.rectangle(rect[0], f64::from(height) - 1.0, rect[2], 1.0);
        } else {
            super::paint::rounded(cr, rect[0], rect[1], rect[2], rect[3], 8.0);
        }
        let _ = cr.fill();
    });
    for (index, button) in buttons.iter().enumerate() {
        let area = area.downgrade();
        let state = Rc::clone(&state);
        button.connect_notify_local(Some("css-classes"), move |button, _| {
            if let Some(area) = area.upgrade() { area.queue_draw(); }
            if !button.has_css_class("on") || state.selected.replace(Some(index)) == Some(index) { return; }
            state.from.set(state.current.get());
            state.start.set(Some(Instant::now()));
            let Some(area) = area.upgrade() else { return };
            area.queue_draw();
            if state.ticking.replace(true) { return; }
            let state = Rc::clone(&state);
            area.add_tick_callback(move |area, _| {
                area.queue_draw();
                if state.start.get().is_none() {
                    state.ticking.set(false);
                    gtk4::glib::ControlFlow::Break
                } else { gtk4::glib::ControlFlow::Continue }
            });
        });
    }
    overlay
}

pub struct Slider {
    pub scale: Scale,
    pub value: SpinButton,
    pub unit: Label,
    pub divisor: f64,
    pub row: Box,
}

pub fn column() -> Box {
    let column = Box::new(Orientation::Vertical, 0);
    column.add_css_class("victus-page");
    column.set_margin_start(24);
    column.set_margin_end(24);
    column.set_margin_top(24);
    column.set_margin_bottom(20);
    column.set_hexpand(true);
    column.set_vexpand(true);
    column
}

pub fn scroll(child: &impl IsAWidget) -> ScrolledWindow {
    let scroll = ScrolledWindow::new();
    scroll.set_policy(PolicyType::Never, PolicyType::Automatic);
    scroll.set_child(Some(child));
    scroll.add_css_class("victus-scroll");
    scroll.set_vexpand(true);
    scroll.set_hexpand(true);
    scroll
}

pub fn stack_page(stack: &Stack, name: &str, child: &impl IsAWidget) {
    if matches!(name, "sensors" | "settings") {
        stack.add_named(child, Some(name));
    } else {
        stack.add_named(&scroll(child), Some(name));
    }
}

pub fn head(title: &str) -> (Box, Label, Label) {
    let row = Box::new(Orientation::Horizontal, 8);
    let title_label = Label::new(Some(title));
    title_label.add_css_class("title");
    title_label.set_halign(Align::Start);
    title_label.set_hexpand(true);
    title_label.set_xalign(0.0);
    let status = Label::new(None);
    status.add_css_class("mono");
    status.add_css_class("head-status");
    status.set_halign(Align::End);
    status.set_ellipsize(gtk4::pango::EllipsizeMode::Start);
    status.set_max_width_chars(30);
    row.append(&title_label);
    row.append(&status);
    (row, title_label, status)
}

pub fn metric(caption: &str, unit: &str) -> (Box, Label, Label) {
    let column = Box::new(Orientation::Vertical, 0);
    column.set_hexpand(true);
    let row = Box::new(Orientation::Horizontal, 4);
    let value = Label::new(Some("—"));
    value.add_css_class("metric");
    value.set_halign(Align::Start);
    value.set_valign(Align::Baseline);
    let unit_label = Label::new(Some(unit));
    unit_label.add_css_class("unit");
    unit_label.add_css_class(if unit.contains("rpm") { "rpm-unit" } else { "temp-unit" });
    unit_label.set_valign(Align::Baseline);
    row.append(&value);
    row.append(&unit_label);
    let caption_label = Label::new(Some(caption));
    caption_label.add_css_class("caption");
    caption_label.set_halign(Align::Start);
    caption_label.set_xalign(0.0);
    caption_label.set_margin_top(6);
    column.append(&row);
    column.append(&caption_label);
    (column, value, caption_label)
}

pub fn hairline() -> Box {
    let line = Box::new(Orientation::Horizontal, 0);
    line.add_css_class("hairline");
    line.set_size_request(-1, 1);
    line
}

pub fn footer() -> (Box, Label, Label) {
    let column = Box::new(Orientation::Vertical, 14);
    column.set_margin_top(24);
    let row = Box::new(Orientation::Horizontal, 8);
    let dot = Label::new(Some("●"));
    dot.add_css_class("ok");
    dot.add_css_class("heartbeat");
    let left = Label::new(None);
    left.add_css_class("mono");
    left.add_css_class("footer-text");
    left.set_hexpand(true);
    left.set_halign(Align::Start);
    left.set_ellipsize(gtk4::pango::EllipsizeMode::End);
    let right = Label::new(Some("—"));
    right.add_css_class("mono");
    right.add_css_class("footer-text");
    row.append(&dot);
    row.append(&left);
    row.append(&right);
    column.append(&hairline());
    column.append(&row);
    (column, left, right)
}

pub fn segment(labels: &[impl AsRef<str>]) -> (Box, Vec<Button>) {
    let outer = Box::new(Orientation::Horizontal, 0);
    outer.add_css_class("seg");
    outer.set_vexpand(false);
    let row = Box::new(Orientation::Horizontal, 0);
    row.set_homogeneous(true);
    let mut buttons = Vec::new();
    for label in labels {
        let button = Button::with_label(label.as_ref());
        button.add_css_class("seg-btn");
        button.add_css_class("animated-seg");
        button.set_cursor_from_name(Some("pointer"));
        button.set_hexpand(true);
        // Page segments in the Qt UI are a 13px label with 9px of vertical padding.
        button.set_size_request(-1, 35);
        row.append(&button);
        buttons.push(button);
    }
    outer.append(&selection_overlay(&row, &buttons, false));
    (outer, buttons)
}

pub fn compact_segment(labels: &[impl AsRef<str>]) -> (Box, Vec<Button>) {
    let (row, buttons) = segment(labels);
    row.add_css_class("compact-seg");
    for button in &buttons { button.set_size_request(-1, -1); }
    (row, buttons)
}

pub fn links(labels: &[impl AsRef<str>]) -> (Box, Vec<Button>) {
    let outer = Box::new(Orientation::Horizontal, 0);
    outer.set_hexpand(false);
    outer.set_vexpand(false);
    let row = Box::new(Orientation::Horizontal, 16);
    let mut buttons = Vec::new();
    for label in labels {
        let button = Button::with_label(label.as_ref());
        button.add_css_class("linkish");
        button.add_css_class("selection-link");
        button.add_css_class("animated-link");
        button.set_cursor_from_name(Some("pointer"));
        row.append(&button);
        buttons.push(button);
    }
    let overlay = selection_overlay(&row, &buttons, true);
    overlay.set_hexpand(false);
    outer.append(&overlay);
    (outer, buttons)
}

pub fn pill(label: &str) -> Button {
    let button = Button::with_label(label);
    button.add_css_class("pill");
    button.set_cursor_from_name(Some("pointer"));
    button
}

pub fn accent(label: &str) -> Button {
    let button = Button::with_label(label);
    button.add_css_class("accent-btn");
    button.set_cursor_from_name(Some("pointer"));
    button.set_halign(Align::Start);
    button
}

pub struct MessageDialog {
    window: gtk4::Window,
    buttons: Vec<(gtk4::ResponseType, Button)>,
}

impl MessageDialog {
    pub fn set_default_response(&self, response: gtk4::ResponseType) {
        if let Some((_, button)) = self.buttons.iter().find(|(id, _)| *id == response) {
            self.window.set_default_widget(Some(button));
        }
    }

    pub fn connect_response(&self, callback: impl Fn(&gtk4::Window, gtk4::ResponseType) + 'static) {
        let callback = std::rc::Rc::new(callback);
        for (response, button) in &self.buttons {
            let callback = std::rc::Rc::clone(&callback);
            let window = self.window.downgrade();
            let response = *response;
            button.connect_clicked(move |_| {
                if let Some(window) = window.upgrade() { callback(&window, response); }
            });
        }
    }

    pub fn present(&self) { self.window.present(); }
}

pub fn message_dialog(parent: &impl IsA<gtk4::Window>, title: &str, text: &str, responses: &[(&str, gtk4::ResponseType)]) -> MessageDialog {
    let window = gtk4::Window::builder().title(title).transient_for(parent).modal(true).resizable(false).build();
    window.add_css_class("victus");
    window.add_css_class("message-dialog");
    let content = Box::new(Orientation::Vertical, 20);
    content.set_margin_top(20);
    content.set_margin_bottom(20);
    content.set_margin_start(24);
    content.set_margin_end(24);
    let label = Label::new(Some(text));
    label.set_wrap(true);
    label.set_selectable(true);
    label.set_xalign(0.0);
    label.set_max_width_chars(60);
    content.append(&label);
    let actions = Box::new(Orientation::Horizontal, 8);
    actions.set_halign(Align::End);
    let mut buttons = Vec::new();
    for (label, response) in responses {
        let button = pill(label);
        actions.append(&button);
        buttons.push((*response, button));
    }
    content.append(&actions);
    window.set_child(Some(&content));
    let keys = gtk4::EventControllerKey::new();
    let weak = window.downgrade();
    keys.connect_key_pressed(move |_, key, _, _| {
        if key == gtk4::gdk::Key::Escape {
            if let Some(window) = weak.upgrade() { window.close(); }
            return gtk4::glib::Propagation::Stop;
        }
        gtk4::glib::Propagation::Proceed
    });
    window.add_controller(keys);
    MessageDialog { window, buttons }
}

pub fn settings_row(title: &str, subtitle: &str, control: &impl IsAWidget) -> Box {
    let row = Box::new(Orientation::Horizontal, 18);
    row.set_margin_top(14);
    row.set_margin_bottom(14);
    let text = Box::new(Orientation::Vertical, 3);
    text.set_hexpand(true);
    let title_label = Label::new(Some(title));
    title_label.add_css_class("row-title");
    title_label.set_halign(Align::Start);
    title_label.set_wrap(true);
    title_label.set_xalign(0.0);
    text.append(&title_label);
    if !subtitle.is_empty() {
        let sub = Label::new(Some(subtitle));
        sub.add_css_class("row-sub");
        sub.set_halign(Align::Start);
        sub.set_wrap(true);
        sub.set_xalign(0.0);
        sub.set_max_width_chars(52);
        text.append(&sub);
    }
    row.append(&text);
    control.set_valign(Align::Center);
    control.set_halign(Align::End);
    row.append(control);
    row
}

pub fn slider(title: &str, min: f64, max: f64, step: f64) -> Slider {
    let row = Box::new(Orientation::Horizontal, 12);
    row.set_margin_top(14);
    row.set_margin_bottom(14);
    let name = Label::new(Some(title));
    name.add_css_class("row-title");
    name.set_size_request(90, -1);
    name.set_xalign(0.0);
    let scale = Scale::with_range(Orientation::Horizontal, min, max, step);
    scale.set_draw_value(false);
    scale.set_hexpand(true);
    let scaled = max > 100_000.0;
    let divisor = if scaled { 1000.0 } else { 1.0 };
    let value = SpinButton::with_range(min / divisor, max / divisor, 1.0);
    value.set_digits(if scaled { 3 } else { 0 });
    value.add_css_class("mono");
    value.set_width_chars(if scaled { 13 } else { 9 });
    gtk4::prelude::EditableExt::set_alignment(&value, 1.0);
    value.set_update_policy(gtk4::SpinButtonUpdatePolicy::IfValid);
    value.set_halign(Align::End);
    let unit = Label::new(None);
    unit.add_css_class("mono");
    row.append(&name);
    row.append(&scale);
    let well = stepper(&value);
    well.add_css_class("power-spin");
    row.append(&well);
    Slider { scale, value, unit, divisor, row }
}

pub fn switch() -> Switch {
    let switch = Switch::new();
    switch.add_css_class("qt-switch");
    switch.set_cursor_from_name(Some("pointer"));
    switch.set_valign(Align::Center);
    switch
}

pub fn hex_entry() -> Entry {
    let entry = Entry::new();
    entry.set_max_length(6);
    entry.set_width_chars(8);
    entry.add_css_class("hex");
    entry
}

pub fn dropdown(labels: &[&str]) -> DropDown {
    let dropdown = DropDown::from_strings(labels);
    dropdown.set_size_request(140, -1);
    dropdown.set_cursor_from_name(Some("pointer"));
    if let Some(button) = dropdown.first_child().and_downcast::<gtk4::ToggleButton>() {
        if let Some(row) = button.child().and_downcast::<Box>() {
            let mut child = row.first_child();
            while let Some(widget) = child {
                child = widget.next_sibling();
                if widget.css_name() == "arrow" { widget.set_visible(false); }
            }
            let down = chevron_texture(false);
            let up = chevron_texture(true);
            let arrow = gtk4::Image::from_paintable(Some(&down));
            arrow.set_pixel_size(14);
            row.append(&arrow);
            button.connect_toggled(move |button| arrow.set_paintable(Some(if button.is_active() { &up } else { &down })));
        }
    }
    dropdown
}

fn chevron_texture(up: bool) -> gtk4::gdk::Texture {
    let bytes: &'static [u8] = if up {
        include_bytes!("../../assets/icons/chevron-up.png")
    } else {
        include_bytes!("../../assets/icons/chevron-down.png")
    };
    gtk4::gdk::Texture::from_bytes(&gtk4::glib::Bytes::from_static(bytes)).expect("bundled chevron")
}

pub fn spin(min: f64, max: f64, step: f64) -> SpinButton {
    let spin = SpinButton::with_range(min, max, step);
    spin.set_digits(1);
    spin.set_width_chars(6);
    spin
}

/// Keep GTK's editable/range behavior, with Qt's stacked arrow buttons.
pub fn stepper(spin: &SpinButton) -> Box {
    spin.set_hexpand(false);
    spin.set_vexpand(false);
    let mut child = spin.first_child();
    while let Some(widget) = child {
        child = widget.next_sibling();
        if widget.css_name() == "button" { widget.set_visible(false); }
    }
    spin.add_css_class("embed-spin");
    let well = Box::new(Orientation::Horizontal, 0);
    well.set_hexpand(false);
    well.set_vexpand(false);
    well.add_css_class("spin-well");
    well.set_valign(Align::Center);
    well.append(spin);
    let arrows = Box::new(Orientation::Vertical, 0);
    arrows.set_vexpand(false);
    arrows.set_valign(Align::Fill);
    for (up, direction) in [(true, gtk4::SpinType::StepForward), (false, gtk4::SpinType::StepBackward)] {
        let texture = chevron_texture(up);
        let image = gtk4::Image::from_paintable(Some(&texture));
        image.set_pixel_size(14);
        let button = Button::new();
        button.set_child(Some(&image));
        button.set_focusable(false);
        button.set_vexpand(true);
        let spin = spin.clone();
        button.connect_clicked(move |_| { spin.update(); spin.spin(direction, 1.0); });
        arrows.append(&button);
    }
    let weak = well.downgrade();
    spin.connect_sensitive_notify(move |spin| {
        if let Some(well) = weak.upgrade() { well.set_sensitive(spin.property::<bool>("sensitive")); }
    });
    well.append(&arrows);
    well
}

pub fn spin_suffix(spin: &SpinButton, suffix: &'static str) {
    spin.set_numeric(false);
    spin.connect_output(move |spin| {
        spin.set_text(&format!("{:.*} {suffix}", spin.digits() as usize, spin.value()));
        gtk4::glib::Propagation::Stop
    });
    spin.connect_input(move |spin| {
        Some(spin.text().trim().trim_end_matches(suffix).trim().parse::<f64>().map_err(|_| ()))
    });
}

pub fn mark(buttons: &[Button], index: Option<usize>) {
    for (button_index, button) in buttons.iter().enumerate() {
        if Some(button_index) == index {
            button.add_css_class("on");
        } else {
            button.remove_css_class("on");
        }
    }
}

pub trait IsAWidget: IsA<Widget> {}
impl<T: IsA<Widget>> IsAWidget for T {}

pub fn sensor_header() -> Box {
    let row = Box::new(Orientation::Horizontal, 0);
    row.add_css_class("sensor-head");
    let name = Label::new(Some("Sensor"));
    name.add_css_class("caption");
    name.set_hexpand(true);
    name.set_xalign(0.0);
    row.append(&name);
    for title in ["Current", "Maximum", "Minimum", "Average"] {
        let label = Label::new(Some(title));
        label.add_css_class("caption");
        label.set_size_request(100, -1);
        label.set_xalign(1.0);
        row.append(&label);
    }
    row
}

pub struct SensorCells {
    pub row: ListBoxRow,
    pub current: Label,
    pub maximum: Label,
    pub minimum: Label,
    pub average: Label,
}

pub fn sensor_row(list: &ListBox, key: &str, name: &str, selectable: bool) -> SensorCells {
    let line = Box::new(Orientation::Horizontal, 0);
    let title = Label::new(Some(name));
    title.set_hexpand(true);
    title.set_xalign(0.0);
    title.set_margin_start(32);
    title.add_css_class("sensor-cell");
    title.set_ellipsize(gtk4::pango::EllipsizeMode::End);
    line.append(&title);
    let mut labels = Vec::new();
    for _ in 0..4 {
        let label = Label::new(Some("—"));
        label.add_css_class("sensor-cell");
        label.set_size_request(100, -1);
        label.set_xalign(1.0);
        line.append(&label);
        labels.push(label);
    }
    let row = ListBoxRow::new();
    row.set_child(Some(&line));
    row.set_widget_name(key);
    row.set_selectable(selectable);
    row.set_activatable(selectable);
    list.append(&row);
    SensorCells { row, current: labels.remove(0), maximum: labels.remove(0), minimum: labels.remove(0), average: labels.remove(0) }
}

pub fn sensor_group(list: &ListBox, name: &str, count: usize) -> (Button, Label) {
    let button = Button::new();
    button.add_css_class("sensor-branch");
    button.set_size_request(16, -1);
    button.set_focusable(false);
    button.set_cursor_from_name(Some("pointer"));
    let line = Box::new(Orientation::Horizontal, 0);
    let arrow = Label::new(Some("▾"));
    button.set_child(Some(&arrow));
    let title = Label::new(Some(name));
    title.add_css_class("sensor-cell");
    title.add_css_class("sensor-group-title");
    title.set_hexpand(true);
    title.set_xalign(0.0);
    title.set_ellipsize(gtk4::pango::EllipsizeMode::End);
    line.append(&button);
    line.append(&title);
    for text in [format!("({count})"), String::new(), String::new(), String::new()] {
        let label = Label::new(Some(&text));
        label.add_css_class("sensor-cell");
        label.set_size_request(100, -1);
        label.set_xalign(1.0);
        line.append(&label);
    }
    let row = ListBoxRow::new();
    row.set_widget_name("");
    row.set_selectable(true);
    row.set_activatable(false);
    row.set_child(Some(&line));
    list.append(&row);
    (button, arrow)
}
