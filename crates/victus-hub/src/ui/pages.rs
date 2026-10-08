//! Page trees. Callbacks are attached after the session exists.

use gtk4::prelude::*;
use gtk4::{Align, Box, Button, DrawingArea, DropDown, Entry, FlowBox, Grid, Label, ListBox, Orientation, Scale, SelectionMode, SpinButton, Stack, Switch};
use victus_core::{effects_for_zone_count, ExtraSensor, PROGRAM_VERSION};
use std::cell::RefCell;
use std::collections::HashSet;
use std::rc::Rc;

use super::widgets::{self, column, head, metric, pill, settings_row, slider, stack_page, Slider};

pub struct Home {
    pub title: Label,
    pub head_status: Label,
    pub cpu_value: Label,
    pub cpu_caption: Label,
    pub gpu_value: Label,
    pub gpu_caption: Label,
    pub cpu_fan: Label,
    pub gpu_fan: Label,
    pub profile_buttons: Vec<Button>,
    pub fan_keys: Vec<String>,
    pub fan_buttons: Vec<Button>,
    pub curve: Button,
    pub curve_row: Box,
    pub power: Button,
    pub power_sub: Label,
    pub light_row: Button,
    pub light_sub: Label,
    pub mini: DrawingArea,
    pub footer_left: Label,
    pub footer_right: Label,
    pub mux_wrap: Box,
    pub mux_buttons: Vec<Button>,
    pub gpu_name: Label,
}

pub struct Power {
    pub head_status: Label,
    pub enabled: Switch,
    pub note: Label,
    pub limits: Box,
    pub stapm: Slider,
    pub fast: Slider,
    pub slow: Slider,
    pub tctl: Slider,
    pub reapply: Slider,
    pub apply: Button,
    pub freq_wrap: Box,
    pub freq_sliders: Box,
    pub freq_min: Slider,
    pub freq_max: Slider,
    pub freq_note: Label,
    pub freq_apply: Button,
    pub uv_wrap: Box,
    pub uv_core: Slider,
    pub uv_cache: Slider,
    pub uv_apply: Button,
    pub uv_status: Label,
}

pub struct Fans {
    pub head_status: Label,
    pub mode_keys: Vec<String>,
    pub mode_buttons: Vec<Button>,
    pub editor: Box,
    pub info: Label,
    pub cpu_link: Button,
    pub gpu_link: Button,
    pub chart: DrawingArea,
    pub response: DropDown,
    pub min_change: SpinButton,
}

pub struct Keyboard {
    pub head_status: Label,
    pub effect_ids: Vec<String>,
    pub effect_buttons: Vec<Button>,
    pub zone_row: Box,
    pub zone_buttons: Vec<Button>,
    pub visual: DrawingArea,
    pub color_box: Box,
    pub hex: Entry,
    pub chip: DrawingArea,
    pub hue: DrawingArea,
    pub shade: DrawingArea,
    pub color2_box: Box,
    pub hex2: Entry,
    pub chip2: DrawingArea,
    pub speed_row: Box,
    pub speed: Scale,
    pub speed_value: Label,
    pub brightness: Scale,
    pub brightness_value: Label,
    pub idle: SpinButton,
    pub idle_enabled: Switch,
}

pub struct SensorRow {
    pub row: gtk4::ListBoxRow,
    pub key: String,
    pub name: String,
    pub group: String,
    pub unit: String,
    pub graphable: bool,
    pub min: f64,
    pub max: f64,
    pub current: Label,
    pub maximum: Label,
    pub minimum: Label,
    pub average: Label,
}

pub struct Sensors {
    pub list: ListBox,
    pub rows: Vec<SensorRow>,
    pub collapsed: Rc<RefCell<HashSet<String>>>,
}

pub struct Settings {
    pub head_status: Label,
    pub battery: Switch,
    pub nvidia: Switch,
    pub hardware: Switch,
    pub shortcut: Label,
    pub shortcut_set: Button,
    pub shortcut_clear: Button,
    pub update: Button,
    pub update_status: Label,
    pub diagnostics: Button,
    pub quit: Button,
}

pub struct Built {
    pub root: Box,
    pub sidebar: DrawingArea,
    pub stack: Stack,
    pub status: Label,
    pub home: Home,
    pub power: Power,
    pub fans: Fans,
    pub keyboard: Keyboard,
    pub sensors: Sensors,
    pub settings: Settings,
}

struct SensorMeta {
    key: &'static str,
    group: &'static str,
    name: &'static str,
    unit: &'static str,
    graphable: bool,
    min: f64,
    max: f64,
}

const BASE_SENSORS: &[SensorMeta] = &[
    SensorMeta { key: "cpu-temp", group: "CPU", name: "CPU Temp", unit: "°C", graphable: true, min: 0.0, max: 100.0 },
    SensorMeta { key: "cpu-usage", group: "CPU", name: "CPU Usage", unit: "%", graphable: true, min: 0.0, max: 100.0 },
    SensorMeta { key: "cpu-power", group: "CPU", name: "CPU Power", unit: "W", graphable: true, min: 0.0, max: 80.0 },
    SensorMeta { key: "gpu-temp", group: "GPU", name: "GPU Temp", unit: "°C", graphable: true, min: 0.0, max: 100.0 },
    SensorMeta { key: "gpu-usage", group: "GPU", name: "GPU Usage", unit: "%", graphable: true, min: 0.0, max: 100.0 },
    SensorMeta { key: "gpu-power", group: "GPU", name: "GPU Power", unit: "W", graphable: true, min: 0.0, max: 120.0 },
    SensorMeta { key: "cpu-fan", group: "HP Embedded Controller", name: "CPU Fan", unit: "RPM", graphable: true, min: 0.0, max: 6000.0 },
    SensorMeta { key: "gpu-fan", group: "HP Embedded Controller", name: "GPU Fan", unit: "RPM", graphable: true, min: 0.0, max: 6000.0 },
    SensorMeta { key: "pwm-value", group: "HP Embedded Controller", name: "HP PWM Value", unit: "PWM", graphable: true, min: 0.0, max: 255.0 },
    SensorMeta { key: "pwm-mode", group: "HP Embedded Controller", name: "HP PWM Mode", unit: "", graphable: false, min: 0.0, max: 2.0 },
    SensorMeta { key: "profile", group: "System", name: "System Profile", unit: "", graphable: false, min: 0.0, max: 2.0 },
    SensorMeta { key: "ram-usage", group: "Memory", name: "RAM Usage", unit: "GB", graphable: true, min: 0.0, max: 64.0 },
];

pub fn fan_pairs(modes: &[String]) -> Vec<(&'static str, &'static str, &'static str)> {
    const ALL: &[(&str, &str, &str)] = &[
        ("auto", "Auto", "This model's own curve"),
        ("smart", "Smart", "Built-in curve with faster smoothing"),
        ("max", "Max", "Both fans at full speed"),
        ("custom", "Custom", "Your own curve, remembered per profile"),
    ];
    ALL.iter().copied().filter(|(key, _, _)| modes.is_empty() || modes.iter().any(|mode| mode == key)).collect()
}

pub fn clear_list(list: &ListBox) {
    while let Some(row) = list.row_at_index(0) {
        list.remove(&row);
    }
}

pub fn fill_sensors(list: &ListBox, extras: &[ExtraSensor], collapsed: &Rc<RefCell<HashSet<String>>>) -> Vec<SensorRow> {
    let mut rows = Vec::new();
    let mut groups: Vec<&str> = Vec::new();
    for meta in BASE_SENSORS {
        if !groups.contains(&meta.group) { groups.push(meta.group); }
    }
    for extra in extras {
        if !groups.contains(&extra.group.as_str()) { groups.push(&extra.group); }
    }
    for group in groups {
        let count = BASE_SENSORS.iter().filter(|meta| meta.group == group).count() + extras.iter().filter(|extra| extra.group == group).count();
        let (button, arrow) = widgets::sensor_group(list, group, count);
        let start = rows.len();
        for meta in BASE_SENSORS.iter().filter(|meta| meta.group == group) {
            rows.push(push_sensor(list, meta.key, meta.group, meta.name, meta.unit, meta.graphable, meta.min, meta.max));
        }
        for extra in extras.iter().filter(|extra| extra.group == group) {
            rows.push(push_sensor(list, &extra.key, &extra.group, &extra.name, &extra.unit, true, extra.value_min, extra.value_max));
        }
        let children: Vec<_> = rows[start..].iter().map(|row| row.row.clone()).collect();
        let is_collapsed = collapsed.borrow().contains(group);
        arrow.set_text(if is_collapsed { "▸" } else { "▾" });
        for row in &children { row.set_visible(!is_collapsed); }
        let collapsed = Rc::clone(collapsed);
        let name = group.to_owned();
        button.connect_clicked(move |_| {
            let mut state = collapsed.borrow_mut();
            let show = state.remove(&name);
            if !show { state.insert(name.clone()); }
            arrow.set_text(if show { "▾" } else { "▸" });
            for row in &children { row.set_visible(show); }
        });
    }
    rows
}

fn push_sensor(list: &ListBox, key: &str, group: &str, name: &str, unit: &str, graphable: bool, min: f64, max: f64) -> SensorRow {
    let cells = widgets::sensor_row(list, key, name, true);
    SensorRow {
        row: cells.row,
        key: key.to_owned(),
        name: name.to_owned(),
        group: group.to_owned(),
        unit: unit.to_owned(),
        graphable,
        min,
        max,
        current: cells.current,
        maximum: cells.maximum,
        minimum: cells.minimum,
        average: cells.average,
    }
}

pub fn build(model: &crate::Model) -> Built {
    let sidebar = DrawingArea::new();
    sidebar.set_content_width(62);
    sidebar.set_vexpand(true);
    let stack = Stack::new();
    stack.set_hhomogeneous(false);
    stack.set_vhomogeneous(false);
    stack.set_hexpand(true);
    stack.set_vexpand(true);
    let (home_page, home) = home(model);
    let (power_page, power) = power(model.host.intel);
    let (fans_page, fans) = fans(&model.host.fan_modes);
    let (keyboard_page, keyboard) = keyboard(model.zones);
    let (sensors_page, sensors) = sensors();
    let (settings_page, settings) = settings(&model.host.product);
    stack_page(&stack, "home", &home_page);
    stack_page(&stack, "power", &power_page);
    stack_page(&stack, "fans", &fans_page);
    stack_page(&stack, "keyboard", &keyboard_page);
    stack_page(&stack, "sensors", &sensors_page);
    stack_page(&stack, "settings", &settings_page);
    stack.set_visible_child_name("home");
    let status = Label::new(None);
    status.add_css_class("mono");
    status.set_halign(Align::Start);
    status.set_margin_start(24);
    status.set_margin_end(24);
    status.set_margin_bottom(12);
    status.set_wrap(true);
    status.set_xalign(0.0);
    let content = Box::new(Orientation::Vertical, 0);
    content.append(&stack);
    content.append(&status);
    content.set_hexpand(true);
    let root = Box::new(Orientation::Horizontal, 0);
    root.append(&sidebar);
    root.append(&content);
    Built { root, sidebar, stack, status, home, power, fans, keyboard, sensors, settings }
}

fn home(model: &crate::Model) -> (Box, Home) {
    let page = column();
    let (head_row, title, head_status) = head("Balanced");
    page.append(&head_row);
    let (cpu, cpu_value, cpu_caption) = metric("CPU", "°C");
    let (gpu, gpu_value, gpu_caption) = metric("GPU", "°C");
    let (cpu_fan_box, cpu_fan, _) = metric("CPU fan", " rpm");
    let (gpu_fan_box, gpu_fan, _) = metric("GPU fan", " rpm");
    let grid = Grid::new();
    grid.set_column_homogeneous(true);
    grid.set_margin_top(24);
    grid.set_column_spacing(20);
    grid.attach(&cpu, 0, 0, 1, 1);
    grid.attach(&gpu, 1, 0, 1, 1);
    let rule = widgets::hairline();
    rule.set_margin_top(18);
    rule.set_margin_bottom(18);
    grid.attach(&rule, 0, 1, 2, 1);
    grid.attach(&cpu_fan_box, 0, 2, 1, 1);
    grid.attach(&gpu_fan_box, 1, 2, 1, 1);
    page.append(&grid);
    let (profile_row, profile_buttons) = widgets::segment(&["Eco", "Balanced", "Performance"]);
    profile_row.set_margin_top(24);
    page.append(&profile_row);
    let pairs = fan_pairs(&model.host.fan_modes);
    let fan_keys = pairs.iter().map(|(key, _, _)| (*key).to_owned()).collect::<Vec<_>>();
    let labels = pairs.iter().map(|(_, label, _)| *label).collect::<Vec<_>>();
    let (fan_links, fan_buttons) = widgets::links(&labels);
    for (button, (_, _, tip)) in fan_buttons.iter().zip(pairs.iter()) {
        button.set_tooltip_text(Some(tip));
    }
    let fan_row = Box::new(Orientation::Horizontal, 10);
    fan_row.set_margin_top(24);
    let fans_label = Label::new(Some("Fans"));
    fans_label.add_css_class("row-title");
    fans_label.set_hexpand(true);
    fans_label.set_halign(Align::Start);
    fan_row.append(&fans_label);
    fan_row.append(&fan_links);
    page.append(&fan_row);
    let curve = Button::with_label("Edit curve");
    curve.add_css_class("linkish");
    curve.add_css_class("on");
    let curve_row = Box::new(Orientation::Horizontal, 0);
    curve_row.set_margin_top(8);
    curve_row.set_halign(Align::End);
    curve_row.append(&curve);
    page.append(&curve_row);
    let mux_labels = model.host.mux.iter().map(|choice| choice.label.as_str()).collect::<Vec<_>>();
    let (mux_buttons_row, mux_buttons) = widgets::segment(&mux_labels);
    let mux_wrap = Box::new(Orientation::Vertical, 8);
    mux_wrap.set_margin_top(20);
    let graphics = Label::new(Some("Graphics"));
    graphics.add_css_class("row-title");
    graphics.set_halign(Align::Start);
    let graphics_row = Box::new(Orientation::Horizontal, 8);
    let gpu_name = Label::new(Some(&model.host.gpu_name));
    gpu_name.add_css_class("sub");
    graphics_row.append(&graphics);
    graphics_row.append(&gpu_name);
    mux_wrap.append(&graphics_row);
    mux_wrap.append(&mux_buttons_row);
    mux_wrap.set_visible(!mux_labels.is_empty());
    page.append(&mux_wrap);
    let (power, power_sub) = jump_row("Power", "Limits off");
    power.set_margin_top(20);
    page.append(&power);
    let (light_row, light_sub, mini) = light_jump();
    light_row.set_margin_top(8);
    page.append(&light_row);
    let spacer = Box::new(Orientation::Vertical, 0);
    spacer.set_vexpand(true);
    page.append(&spacer);
    let (footer, footer_left, footer_right) = widgets::footer();
    footer_left.set_text(if model.host.product.is_empty() { "HP Laptop" } else { &model.host.product });
    page.append(&footer);
    let home = Home {
        title,
        head_status,
        cpu_value,
        cpu_caption,
        gpu_value,
        gpu_caption,
        cpu_fan,
        gpu_fan,
        profile_buttons,
        fan_keys,
        fan_buttons,
        curve,
        curve_row,
        power,
        power_sub,
        light_row,
        light_sub,
        mini,
        footer_left,
        footer_right,
        mux_wrap,
        mux_buttons,
        gpu_name,
    };
    (page, home)
}

fn jump_row(title: &str, subtitle: &str) -> (Button, Label) {
    let button = Button::new();
    button.add_css_class("row-link");
    let row = Box::new(Orientation::Horizontal, 12);
    let label = Label::new(Some(title));
    label.add_css_class("row-title");
    label.set_hexpand(true);
    label.set_halign(Align::Start);
    let sub = Label::new(Some(subtitle));
    sub.add_css_class("sub");
    let arrow = Label::new(Some("→"));
    arrow.add_css_class("accent");
    row.append(&label);
    row.append(&sub);
    row.append(&arrow);
    button.set_child(Some(&row));
    (button, sub)
}

fn light_jump() -> (Button, Label, DrawingArea) {
    let button = Button::new();
    button.add_css_class("row-link");
    let row = Box::new(Orientation::Horizontal, 12);
    let text = Box::new(Orientation::Vertical, 3);
    text.set_hexpand(true);
    let label = Label::new(Some("Lighting"));
    label.add_css_class("row-title");
    label.set_halign(Align::Start);
    let sub = Label::new(Some("Off"));
    sub.add_css_class("sub");
    sub.set_halign(Align::Start);
    text.append(&label);
    text.append(&sub);
    let mini = DrawingArea::new();
    mini.set_content_width(145);
    mini.set_content_height(64);
    let arrow = Label::new(Some("→"));
    arrow.add_css_class("accent");
    row.append(&text);
    row.append(&mini);
    row.append(&arrow);
    button.set_child(Some(&row));
    (button, sub, mini)
}

fn power(intel: bool) -> (Box, Power) {
    let page = column();
    let (head_row, _, head_status) = head("Power");
    head_status.set_text("CPU — W · Freq — MHz");
    page.append(&head_row);
    let enabled = widgets::switch();
    let enable_row = Box::new(Orientation::Horizontal, 12);
    enable_row.set_margin_top(24);
    let enable_label = Label::new(Some("Enable power limits"));
    enable_label.add_css_class("row-title");
    enable_label.set_hexpand(true);
    enable_label.set_halign(Align::Start);
    enable_row.append(&enable_label);
    enable_row.append(&enabled);
    page.append(&enable_row);
    let note = Label::new(Some("To reset power limits to firmware defaults a reboot is required."));
    note.add_css_class("sub");
    note.set_wrap(true);
    note.set_halign(Align::Start);
    note.set_xalign(0.0);
    note.set_margin_top(8);
    page.append(&note);
    let stapm = slider("STAPM", 15.0, 120.0, 1.0);
    let fast = slider(if intel { "PL2 (short)" } else { "Fast" }, 15.0, 120.0, 1.0);
    let slow = slider(if intel { "PL1 (long)" } else { "Slow" }, 15.0, 120.0, 1.0);
    let tctl = slider("Tctl", 75.0, 100.0, 1.0);
    let reapply = slider("Reapply", 1.0, 120.0, 1.0);
    stapm.row.set_visible(!intel);
    tctl.row.set_visible(!intel);
    let limits = Box::new(Orientation::Vertical, 0);
    limits.append(&stapm.row);
    if intel { limits.append(&slow.row); limits.append(&fast.row); }
    else { limits.append(&fast.row); limits.append(&slow.row); }
    limits.append(&tctl.row);
    limits.append(&reapply.row);
    page.append(&limits);
    let apply = pill("Apply power");
    apply.set_margin_top(8);
    apply.set_halign(Align::End);
    page.append(&apply);
    let freq_min = slider("Minimum", 400_000.0, 6_000_000.0, 100_000.0);
    let freq_max = slider("Maximum", 400_000.0, 6_000_000.0, 100_000.0);
    let freq_note = Label::new(None);
    freq_note.add_css_class("sub");
    freq_note.set_wrap(true);
    freq_note.set_halign(Align::Start);
    freq_note.set_xalign(0.0);
    let freq_apply = pill("Apply frequency");
    freq_apply.set_halign(Align::End);
    let freq_sliders = Box::new(Orientation::Vertical, 0);
    freq_sliders.append(&freq_min.row);
    freq_sliders.append(&freq_max.row);
    freq_sliders.append(&freq_apply);
    let freq_wrap = Box::new(Orientation::Vertical, 8);
    freq_wrap.set_margin_top(12);
    let freq_title = Label::new(Some("CPU frequency"));
    freq_title.add_css_class("title");
    freq_title.set_halign(Align::Start);
    freq_wrap.append(&widgets::hairline());
    freq_wrap.append(&freq_title);
    freq_wrap.append(&freq_note);
    freq_wrap.append(&freq_sliders);
    page.append(&freq_wrap);
    let uv_core = slider("Core", -250.0, 0.0, 1.0);
    let uv_cache = slider("Cache", -250.0, 0.0, 1.0);
    let uv_apply = pill("Apply undervolt");
    uv_apply.set_halign(Align::End);
    let uv_status = Label::new(None);
    uv_status.set_text("Set both offsets to 0 mV to reset. Requires the msr kernel module and firmware voltage-control support. Saved offsets are applied only when you click Apply undervolt.");
    uv_status.add_css_class("sub");
    uv_status.set_halign(Align::Start);
    uv_status.set_wrap(true);
    let uv_wrap = Box::new(Orientation::Vertical, 8);
    uv_wrap.set_margin_top(12);
    let uv_title = Label::new(Some("Intel undervolt"));
    uv_title.add_css_class("title");
    uv_title.set_halign(Align::Start);
    uv_wrap.append(&widgets::hairline());
    uv_wrap.append(&uv_title);
    uv_wrap.append(&uv_core.row);
    uv_wrap.append(&uv_cache.row);
    uv_wrap.append(&uv_apply);
    uv_wrap.append(&uv_status);
    uv_wrap.set_visible(intel);
    page.append(&uv_wrap);
    let power = Power { head_status, enabled, note, limits, stapm, fast, slow, tctl, reapply, apply, freq_wrap, freq_sliders, freq_min, freq_max, freq_note, freq_apply, uv_wrap, uv_core, uv_cache, uv_apply, uv_status };
    (page, power)
}

fn fans(modes: &[String]) -> (Box, Fans) {
    let page = column();
    let (head_row, _, head_status) = head("Fans");
    head_status.set_max_width_chars(42);
    page.append(&head_row);
    let pairs = fan_pairs(modes);
    let mode_keys = pairs.iter().map(|(key, _, _)| (*key).to_owned()).collect::<Vec<_>>();
    let labels = pairs.iter().map(|(_, label, _)| *label).collect::<Vec<_>>();
    let (mode_row, mode_buttons) = widgets::segment(&labels);
    for (button, (_, _, tip)) in mode_buttons.iter().zip(pairs.iter()) {
        button.set_tooltip_text(Some(tip));
    }
    mode_row.set_margin_top(24);
    page.append(&mode_row);
    let (curve_links, mut curve_buttons) = widgets::links(&["CPU curve", "GPU curve"]);
    let gpu_link = curve_buttons.pop().expect("gpu curve link");
    let cpu_link = curve_buttons.pop().expect("cpu curve link");
    let hint = Label::new(Some("Left-click to add points · Right-click to remove points"));
    hint.add_css_class("mono");
    hint.set_halign(Align::End);
    hint.set_hexpand(true);
    hint.set_wrap(true);
    let links_row = Box::new(Orientation::Horizontal, 16);
    links_row.set_margin_top(24);
    links_row.append(&curve_links);
    links_row.append(&hint);
    let chart = DrawingArea::new();
    chart.set_content_height(220);
    chart.set_focusable(true);
    chart.set_cursor_from_name(Some("crosshair"));
    chart.set_vexpand(true);
    chart.set_hexpand(true);
    chart.set_margin_top(22);
    let response = widgets::dropdown(&["Smooth", "Aggressive"]);
    let response_row = settings_row("Fan response", "How quickly fan speed follows temperature", &response);
    let min_change = widgets::spin(0.0, 20.0, 0.5);
    let min_wrap = Box::new(Orientation::Horizontal, 6);
    min_wrap.append(&min_change);
    let percent = Label::new(Some("%"));
    percent.add_css_class("sub");
    min_wrap.append(&percent);
    let min_row = settings_row("Minimum fan change", "Ignore smaller PWM steps", &min_wrap);
    let editor = Box::new(Orientation::Vertical, 0);
    editor.set_vexpand(true);
    editor.append(&links_row);
    editor.append(&chart);
    editor.append(&response_row);
    editor.append(&min_row);
    page.append(&editor);
    let info = Label::new(None);
    info.set_wrap(true);
    info.set_halign(Align::Start);
    info.set_xalign(0.0);
    info.set_margin_top(24);
    info.add_css_class("row-title");
    page.append(&info);
    let fans = Fans { head_status, mode_keys, mode_buttons, editor, info, cpu_link, gpu_link, chart, response, min_change };
    (page, fans)
}

fn keyboard(zones: i32) -> (Box, Keyboard) {
    let page = column();
    let (head_row, _, head_status) = head("Keyboard");
    let zone_status = if zones > 1 { format!("{zones} zones") } else { "Single zone".to_owned() };
    head_status.set_text(&zone_status);
    page.append(&head_row);
    let mut effect_ids = vec!["off".to_owned()];
    let mut effect_labels = vec!["Off".to_owned()];
    for (id, label) in effects_for_zone_count(zones) {
        effect_ids.push((*id).to_owned());
        effect_labels.push((*label).to_owned());
    }
    let flow = FlowBox::new();
    flow.set_selection_mode(SelectionMode::None);
    flow.set_column_spacing(8);
    flow.set_row_spacing(4);
    flow.set_max_children_per_line(6);
    flow.set_margin_top(24);
    flow.set_hexpand(true);
    let mut effect_buttons = Vec::new();
    for label in &effect_labels {
        let button = Button::with_label(label);
        button.add_css_class("linkish");
        button.add_css_class("selection-link");
        flow.append(&button);
        effect_buttons.push(button);
    }
    page.append(&flow);
    let mut zone_labels = victus_core::ZONE_NAMES.iter().take(usize::try_from(zones.max(1)).unwrap_or(1)).map(|name| (*name).to_owned()).collect::<Vec<_>>();
    if zones > 1 {
        zone_labels.push("All".to_owned());
    }
    let (zone_buttons_row, zone_buttons) = widgets::segment(&zone_labels);
    let zone_row = Box::new(Orientation::Horizontal, 10);
    zone_row.set_margin_top(22);
    let select = Label::new(Some("Select"));
    select.add_css_class("sub");
    zone_row.append(&select);
    zone_row.append(&zone_buttons_row);
    zone_row.set_visible(zones > 1);
    page.append(&zone_row);
    let visual = DrawingArea::new();
    visual.set_content_height(220);
    visual.set_vexpand(true);
    visual.set_hexpand(true);
    visual.set_margin_top(22);
    page.append(&visual);
    page.append(&widgets::hairline());
    let chip = DrawingArea::new();
    chip.set_cursor_from_name(Some("pointer"));
    chip.set_content_width(40);
    chip.set_content_height(34);
    let hex = widgets::hex_entry();
    let hue = DrawingArea::new();
    hue.set_cursor_from_name(Some("crosshair"));
    hue.set_content_height(33);
    hue.set_hexpand(true);
    let shade = DrawingArea::new();
    shade.set_cursor_from_name(Some("crosshair"));
    shade.set_content_height(33);
    shade.set_hexpand(true);
    let strips = Box::new(Orientation::Vertical, 0);
    strips.set_hexpand(true);
    strips.append(&hue);
    strips.append(&shade);
    let color_box = Box::new(Orientation::Horizontal, 12);
    color_box.set_margin_top(18);
    let color_left = Box::new(Orientation::Vertical, 6);
    color_left.set_size_request(210, -1);
    let hex_row = Box::new(Orientation::Horizontal, 0);
    hex_row.append(&chip);
    let hash = Label::new(Some("  #"));
    hash.add_css_class("mono");
    hex_row.append(&hash);
    hex_row.append(&hex);
    color_left.append(&hex_row);
    color_box.append(&color_left);
    color_box.append(&strips);
    page.append(&color_box);
    let chip2 = DrawingArea::new();
    chip2.set_cursor_from_name(Some("pointer"));
    chip2.set_content_width(40);
    chip2.set_content_height(34);
    let hex2 = widgets::hex_entry();
    let color2_box = Box::new(Orientation::Horizontal, 12);
    color2_box.set_margin_top(12);
    let color2_label = Label::new(Some("Second color"));
    color2_label.add_css_class("sub");
    color2_box.append(&color2_label);
    color2_box.append(&chip2);
    color2_box.append(&hex2);
    page.append(&color2_box);
    let brightness = Scale::with_range(Orientation::Horizontal, 0.0, 255.0, 1.0);
    brightness.set_draw_value(false);
    brightness.set_hexpand(true);
    let brightness_value = Label::new(Some("100%"));
    brightness_value.add_css_class("mono");
    brightness_value.set_width_chars(5);
    let brightness_row = Box::new(Orientation::Horizontal, 12);
    brightness_row.append(&brightness);
    brightness_row.append(&brightness_value);
    color_left.append(&brightness_row);
    let speed = Scale::with_range(Orientation::Horizontal, 1.0, 100.0, 1.0);
    speed.set_draw_value(false);
    speed.set_hexpand(true);
    let speed_value = Label::new(Some("50"));
    speed_value.add_css_class("mono");
    speed_value.set_width_chars(4);
    let speed_row = Box::new(Orientation::Horizontal, 12);
    speed_row.set_margin_top(8);
    let speed_label = Label::new(Some("Speed"));
    speed_label.add_css_class("row-title");
    speed_label.set_width_chars(12);
    speed_label.set_halign(Align::Start);
    speed_row.append(&speed_label);
    speed_row.append(&speed);
    speed_row.append(&speed_value);
    page.append(&speed_row);
    let idle = widgets::spin(1.0, 600.0, 1.0);
    idle.set_digits(0);
    idle.set_value(30.0);
    let idle_enabled = widgets::switch();
    let idle_control = Box::new(Orientation::Horizontal, 10);
    idle_control.append(&Label::new(Some("s")));
    idle_control.append(&idle);
    idle_control.append(&idle_enabled);
    page.append(&settings_row("Idle timeout", "Dim the backlight when you stop typing", &idle_control));
    let keyboard = Keyboard {
        head_status,
        effect_ids,
        effect_buttons,
        zone_row,
        zone_buttons,
        visual,
        color_box,
        hex,
        chip,
        hue,
        shade,
        color2_box,
        hex2,
        chip2,
        speed_row,
        speed,
        speed_value,
        brightness,
        brightness_value,
        idle,
        idle_enabled,
    };
    (page, keyboard)
}

fn sensors() -> (Box, Sensors) {
    let page = column();
    let (head_row, _, _) = head("Sensors");
    page.append(&head_row);
    page.append(&widgets::sensor_header());
    let list = ListBox::new();
    list.add_css_class("sensors");
    list.set_selection_mode(SelectionMode::Single);
    let collapsed = Rc::new(RefCell::new(HashSet::new()));
    let rows = fill_sensors(&list, &[], &collapsed);
    page.append(&list);
    (page, Sensors { list, rows, collapsed })
}

fn settings(product: &str) -> (Box, Settings) {
    let page = column();
    let (head_row, _, head_status) = head("Settings");
    head_status.set_text(product);
    page.append(&head_row);
    let battery = widgets::switch();
    let nvidia = widgets::switch();
    let hardware = widgets::switch();
    page.append(&settings_row(
        "Power save on battery",
        "Switch to Power Save on battery; restore the previous mode on AC unless you manually change modes while Victus Hub is running",
        &battery,
    ));
    page.append(&widgets::hairline());
    page.append(&settings_row(
        "Disable NVIDIA GPU queries",
        "Skip NVIDIA sensors and GPU-name detection, including NVML and nvidia-smi, to avoid waking the GPU and save battery. Only applies in Power Save mode.",
        &nvidia,
    ));
    page.append(&widgets::hairline());
    page.append(&settings_row(
        "Keyboard control shortcuts",
        "Left Ctrl + Left Shift + ↑ / ↓: brightness in 25% steps\nLeft Ctrl + Left Shift + ← / →: previous / next lighting effect (including Off)\nLeft Ctrl + Left Shift + M: cycle performance mode",
        &hardware,
    ));
    page.append(&widgets::hairline());
    let shortcut = Label::new(Some("Not set"));
    shortcut.add_css_class("mono");
    shortcut.add_css_class("sunken-chip");
    let shortcut_set = pill("Set");
    let shortcut_clear = pill("Clear");
    let controls = Box::new(Orientation::Horizontal, 8);
    controls.append(&shortcut);
    controls.append(&shortcut_set);
    controls.append(&shortcut_clear);
    page.append(&settings_row("Program shortcut", "Opens Victus Hub from a key, including the Omen key.", &controls));
    page.append(&widgets::hairline());
    let update = pill("Check for updates");
    let update_status = Label::new(None);
    update_status.add_css_class("sub");
    update_status.set_halign(Align::Start);
    update_status.set_wrap(true);
    let update_box = Box::new(Orientation::Vertical, 6);
    update_box.set_halign(Align::End);
    update_box.append(&update);
    update_box.append(&update_status);
    page.append(&settings_row("Updates", "Check GitHub for a newer Victus Hub release.", &update_box));
    page.append(&widgets::hairline());
    let diagnostics = pill("Diagnostics");
    let quit = pill("Quit");
    let actions = Box::new(Orientation::Horizontal, 8);
    actions.set_margin_top(18);
    actions.append(&diagnostics);
    actions.append(&quit);
    page.append(&actions);
    let version = Label::new(Some(&format!("Version {PROGRAM_VERSION}")));
    version.add_css_class("mono");
    version.set_halign(Align::Start);
    version.set_margin_top(16);
    page.append(&version);
    let settings = Settings { head_status, battery, nvidia, hardware, shortcut, shortcut_set, shortcut_clear, update, update_status, diagnostics, quit };
    (page, settings)
}
