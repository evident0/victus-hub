//! Stock GTK pieces styled like the previous Ohman panel.

use gtk4::prelude::*;
use gtk4::{Align, Box, Button, DropDown, Entry, Label, ListBox, ListBoxRow, Orientation, PolicyType, Scale, ScrolledWindow, SpinButton, Stack, Switch, Widget};

pub struct Slider {
    pub scale: Scale,
    pub value: Label,
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
    stack.add_named(&scroll(child), Some(name));
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
    status.set_halign(Align::End);
    status.set_ellipsize(gtk4::pango::EllipsizeMode::Start);
    status.set_max_width_chars(36);
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
    let unit_label = Label::new(Some(unit));
    unit_label.add_css_class("unit");
    unit_label.set_valign(Align::Start);
    unit_label.set_margin_top(8);
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
    let left = Label::new(None);
    left.add_css_class("mono");
    left.set_hexpand(true);
    left.set_halign(Align::Start);
    let right = Label::new(Some("—"));
    right.add_css_class("mono");
    row.append(&dot);
    row.append(&left);
    row.append(&right);
    column.append(&hairline());
    column.append(&row);
    (column, left, right)
}

pub fn segment(labels: &[impl AsRef<str>]) -> (Box, Vec<Button>) {
    let row = Box::new(Orientation::Horizontal, 0);
    row.add_css_class("seg");
    row.set_homogeneous(true);
    let mut buttons = Vec::new();
    for label in labels {
        let button = Button::with_label(label.as_ref());
        button.add_css_class("seg-btn");
        button.set_hexpand(true);
        row.append(&button);
        buttons.push(button);
    }
    (row, buttons)
}

pub fn links(labels: &[impl AsRef<str>]) -> (Box, Vec<Button>) {
    let row = Box::new(Orientation::Horizontal, 16);
    let mut buttons = Vec::new();
    for label in labels {
        let button = Button::with_label(label.as_ref());
        button.add_css_class("linkish");
        row.append(&button);
        buttons.push(button);
    }
    (row, buttons)
}

pub fn pill(label: &str) -> Button {
    let button = Button::with_label(label);
    button.add_css_class("pill");
    button
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
    row.set_margin_top(8);
    row.set_margin_bottom(8);
    let name = Label::new(Some(title));
    name.add_css_class("row-title");
    name.set_width_chars(12);
    name.set_halign(Align::Start);
    name.set_xalign(0.0);
    let scale = Scale::with_range(Orientation::Horizontal, min, max, step);
    scale.set_draw_value(false);
    scale.set_hexpand(true);
    let value = Label::new(Some("—"));
    value.add_css_class("mono");
    value.set_width_chars(10);
    value.set_halign(Align::End);
    row.append(&name);
    row.append(&scale);
    row.append(&value);
    Slider { scale, value, row }
}

pub fn switch() -> Switch {
    let switch = Switch::new();
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
    dropdown
}

pub fn spin(min: f64, max: f64, step: f64) -> SpinButton {
    let spin = SpinButton::with_range(min, max, step);
    spin.set_digits(1);
    spin.set_width_chars(6);
    spin
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
    let row = Box::new(Orientation::Horizontal, 8);
    row.set_margin_bottom(6);
    let name = Label::new(Some("Sensor"));
    name.add_css_class("caption");
    name.set_hexpand(true);
    name.set_halign(Align::Start);
    row.append(&name);
    for title in ["Current", "Maximum", "Minimum", "Average"] {
        let label = Label::new(Some(title));
        label.add_css_class("caption");
        label.set_width_chars(8);
        label.set_halign(Align::End);
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
    let line = Box::new(Orientation::Horizontal, 8);
    line.set_margin_top(6);
    line.set_margin_bottom(6);
    let title = Label::new(Some(name));
    title.set_hexpand(true);
    title.set_halign(Align::Start);
    title.set_xalign(0.0);
    line.append(&title);
    let mut labels = Vec::new();
    for _ in 0..4 {
        let label = Label::new(Some("—"));
        label.add_css_class("mono");
        label.set_width_chars(8);
        label.set_halign(Align::End);
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
