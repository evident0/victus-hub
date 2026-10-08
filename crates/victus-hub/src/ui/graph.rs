//! Standalone sensor graph windows. The layout follows the Qt
//! `SensorGraphWindow`: a title row, a step chart, and a min/max scale.

use std::cell::{Cell, RefCell};
use std::collections::{HashMap, VecDeque};
use std::rc::Rc;
use std::sync::{Arc, Condvar, Mutex};

use gtk4::glib::Propagation;
use gtk4::prelude::*;
use gtk4::{Align, Box, DrawingArea, Entry, Label, Orientation};

use super::paint;

const CAPACITY: usize = 500;
const PREVIEW_SEED: usize = CAPACITY;

pub struct Sample {
    pub value: Option<f64>,
    pub text: String,
    pub source: String,
    pub ram_total: Option<f64>,
}

pub struct Meta {
    pub key: String,
    pub name: String,
    pub group: String,
    pub unit: String,
    pub min: f64,
    pub max: f64,
}

struct Chart {
    key: String,
    unit: String,
    window: gtk4::Window,
    current: Label,
    source: Label,
    area: DrawingArea,
    min_entry: Entry,
    max_entry: Entry,
    samples: RefCell<VecDeque<Option<f64>>>,
    range: Cell<(f64, f64)>,
    hover: Cell<Option<usize>>,
    ram_max_set: Cell<bool>,
}

pub struct Charts {
    windows: RefCell<HashMap<String, Chart>>,
    keys: Arc<Mutex<Vec<String>>>,
    signal: Arc<(Mutex<u64>, Condvar)>,
}

impl Charts {
    pub fn new(keys: Arc<Mutex<Vec<String>>>, signal: Arc<(Mutex<u64>, Condvar)>) -> Rc<Self> {
        Rc::new(Self { windows: RefCell::new(HashMap::new()), keys, signal })
    }
    pub fn has_visible(&self) -> bool { self.windows.borrow().values().any(|chart| chart.window.is_visible()) }

    pub fn open(self: &Rc<Self>, meta: Meta, offline: bool) {
        if let Some(chart) = self.windows.borrow().get(&meta.key) {
            chart.window.present();
            return;
        }
        let key = meta.key.clone();
        let chart = Chart::build(Rc::clone(self), meta, offline);
        self.windows.borrow_mut().insert(key.clone(), chart);
        self.publish();
        if let Some(chart) = self.windows.borrow().get(&key) {
            chart.window.present();
        }
    }

    pub fn apply(&self, mut sample_for: impl FnMut(&str) -> Option<Sample>) {
        for (key, chart) in self.windows.borrow().iter() {
            if chart.window.is_visible() {
                if let Some(sample) = sample_for(key) { chart.push(sample); }
            }
        }
    }

    pub fn preview(&self, step: u64) {
        for (key, chart) in self.windows.borrow().iter() {
            if !chart.window.is_visible() {
                continue;
            }
            let value = preview_value(key, step);
            let text = if chart.unit.is_empty() { format!("{value:.1}") } else { format!("{value:.1} {}", chart.unit) };
            chart.push(Sample { value: Some(value), text, source: "Offline preview".into(), ram_total: None });
        }
    }

    pub fn hide_all(&self) {
        for chart in self.windows.borrow().values() {
            chart.window.set_visible(false);
        }
    }

    pub fn show_all(&self) {
        for chart in self.windows.borrow().values() {
            chart.window.set_visible(true);
            chart.window.present();
        }
    }

    pub fn close_all(&self) {
        let windows: Vec<gtk4::Window> = self.windows.borrow().values().map(|chart| chart.window.clone()).collect();
        for window in windows {
            window.close();
        }
    }

    fn publish(&self) {
        let names = self.windows.borrow().keys().cloned().collect();
        if let Ok(mut slot) = self.keys.lock() {
            *slot = names;
        }
        let mut generation = self.signal.0.lock().expect("sensor wake");
        *generation = generation.wrapping_add(1);
        self.signal.1.notify_all();
    }

    fn remove(&self, key: &str) {
        self.windows.borrow_mut().remove(key);
        self.publish();
    }
}

impl Chart {
    fn build(charts: Rc<Charts>, meta: Meta, offline: bool) -> Self {
        let window = gtk4::Window::new();
        window.set_title(Some(&format!("{} Graph", meta.name)));
        window.set_icon_name(Some("victus-hub"));
        window.set_decorated(true);
        window.set_default_size(820, 260);
        window.set_size_request(500, 200);
        window.add_css_class("victus");
        window.add_css_class("graph-win");

        let root = Box::new(Orientation::Vertical, 0);
        root.set_margin_top(4);
        root.set_margin_bottom(4);
        root.set_margin_start(4);
        root.set_margin_end(4);

        let title = Box::new(Orientation::Horizontal, 8);
        title.set_margin_top(2);
        title.set_margin_bottom(2);
        title.set_margin_start(4);
        title.set_margin_end(4);
        let name = Label::new(Some(&meta.name));
        name.add_css_class("graph-name");
        name.set_halign(Align::Start);
        let current = Label::new(Some("--"));
        current.add_css_class("graph-value");
        let source = Label::new(None);
        source.add_css_class("graph-source");
        source.set_hexpand(true);
        source.set_halign(Align::Start);
        source.set_xalign(0.0);
        title.append(&name);
        title.append(&current);
        title.append(&source);
        root.append(&title);

        let content = Box::new(Orientation::Horizontal, 0);
        content.set_vexpand(true);
        let area = DrawingArea::new();
        area.set_hexpand(true);
        area.set_vexpand(true);
        area.set_content_width(400);
        area.set_content_height(160);
        content.append(&area);

        let scale = Box::new(Orientation::Vertical, 0);
        scale.add_css_class("graph-scale");
        scale.set_size_request(60, -1);
        let scale_inner = Box::new(Orientation::Vertical, 0);
        scale_inner.set_margin_start(2);
        scale_inner.set_margin_end(2);
        scale_inner.set_margin_top(2);
        scale_inner.set_margin_bottom(2);
        scale_inner.set_vexpand(true);
        let max_entry = range_entry(meta.max);
        let min_entry = range_entry(meta.min);
        let gap = Box::new(Orientation::Vertical, 0);
        gap.set_vexpand(true);
        scale_inner.append(&max_entry);
        scale_inner.append(&gap);
        scale_inner.append(&min_entry);
        scale.append(&scale_inner);
        content.append(&scale);
        root.append(&content);
        window.set_child(Some(&root));

        let accent = group_accent(&meta.group);
        let draw_charts = Rc::clone(&charts);
        let draw_key = meta.key.clone();
        let draw_unit = meta.unit.clone();
        area.set_draw_func(move |_, cr, width, height| {
            let map = draw_charts.windows.borrow();
            let Some(chart) = map.get(&draw_key) else { return };
            let samples: Vec<Option<f64>> = chart.samples.borrow().iter().copied().collect();
            let (min, max) = chart.range.get();
            paint::sensor_chart(cr, f64::from(width), f64::from(height), &samples, min, max, accent, chart.hover.get(), &draw_unit);
        });

        let motion = gtk4::EventControllerMotion::new();
        let motion_charts = Rc::clone(&charts);
        let motion_key = meta.key.clone();
        motion.connect_motion(move |_, x, _| {
            let map = motion_charts.windows.borrow();
            let Some(chart) = map.get(&motion_key) else { return };
            let width = f64::from(chart.area.width()).max(1.0);
            let count = chart.samples.borrow().len().max(2);
            let index = ((x / width) * (count - 1) as f64).round() as usize;
            let index = index.min(count - 1);
            if chart.hover.get() != Some(index) {
                chart.hover.set(Some(index));
                chart.area.queue_draw();
            }
        });
        let leave_charts = Rc::clone(&charts);
        let leave_key = meta.key.clone();
        motion.connect_leave(move |_| {
            if let Some(chart) = leave_charts.windows.borrow().get(&leave_key) {
                chart.hover.set(None);
                chart.area.queue_draw();
            }
        });
        area.add_controller(motion);

        let close_charts = Rc::clone(&charts);
        let close_key = meta.key.clone();
        window.connect_close_request(move |_| {
            close_charts.remove(&close_key);
            Propagation::Proceed
        });

        bind_range(&charts, &meta.key, &min_entry);
        bind_range(&charts, &meta.key, &max_entry);

        let samples = if offline { seeded(&meta.key) } else { VecDeque::from(vec![None; CAPACITY]) };
        Self {
            key: meta.key,
            unit: meta.unit,
            window,
            current,
            source,
            area,
            min_entry,
            max_entry,
            samples: RefCell::new(samples),
            range: Cell::new((meta.min, meta.max)),
            hover: Cell::new(None),
            ram_max_set: Cell::new(false),
        }
    }

    fn push(&self, sample: Sample) {
        if self.key == "ram-usage" && !self.ram_max_set.get() {
            if let Some(total) = sample.ram_total {
                self.ram_max_set.set(true);
                let (min, _) = self.range.get();
                self.range.set((min, total));
                self.max_entry.set_text(&format!("{total:.1}"));
            }
        }
        self.current.set_text(&sample.text);
        self.source.set_text(&sample.source);
        {
            let mut samples = self.samples.borrow_mut();
            samples.push_back(sample.value);
            while samples.len() > CAPACITY {
                samples.pop_front();
            }
        }
        self.area.queue_draw();
    }
}

fn bind_range(charts: &Rc<Charts>, key: &str, entry: &Entry) {
    let charts_activate = Rc::clone(charts);
    let key_activate = key.to_owned();
    entry.connect_activate(move |_| apply_range(&charts_activate, &key_activate));
    let focus = gtk4::EventControllerFocus::new();
    let charts_leave = Rc::clone(charts);
    let key_leave = key.to_owned();
    focus.connect_leave(move |_| apply_range(&charts_leave, &key_leave));
    entry.add_controller(focus);
}

fn apply_range(charts: &Charts, key: &str) {
    let map = charts.windows.borrow();
    let Some(chart) = map.get(key) else { return };
    let Ok(min) = chart.min_entry.text().parse::<f64>() else { return };
    let Ok(max) = chart.max_entry.text().parse::<f64>() else { return };
    if min.is_finite() && max.is_finite() && max > min {
        chart.range.set((min, max));
        chart.area.queue_draw();
    }
}

fn range_entry(value: f64) -> Entry {
    let entry = Entry::new();
    entry.set_text(&range_text(value));
    gtk4::prelude::EditableExt::set_alignment(&entry, 0.5);
    entry.set_width_chars(1);
    entry.set_max_width_chars(1);
    entry
}

fn range_text(value: f64) -> String {
    if (value - value.round()).abs() < 0.05 {
        format!("{}", paint_round(value))
    } else {
        format!("{value:.1}")
    }
}

fn paint_round(value: f64) -> i32 {
    let rounded = value.round();
    if rounded >= f64::from(i32::MAX) {
        i32::MAX
    } else if rounded <= f64::from(i32::MIN) {
        i32::MIN
    } else {
        rounded as i32
    }
}

fn seeded(key: &str) -> VecDeque<Option<f64>> {
    let mut samples = VecDeque::from(vec![None; CAPACITY - PREVIEW_SEED]);
    for step in 0..PREVIEW_SEED {
        samples.push_back(Some(preview_value(key, step as u64)));
    }
    samples
}

pub fn preview_origin() -> u64 {
    PREVIEW_SEED as u64
}

fn preview_value(key: &str, step: u64) -> f64 {
    let t = step as f64;
    let wave = (t * 0.17).sin();
    match key {
        "cpu-temp" => 52.0 + wave * 8.0,
        "gpu-temp" => 46.0 + (t * 0.13).cos() * 7.0,
        "cpu-usage" | "gpu-usage" => 28.0 + wave.abs() * 40.0,
        "cpu-power" => 18.0 + wave.abs() * 22.0,
        "gpu-power" => 12.0 + (t * 0.11).sin().abs() * 30.0,
        "cpu-fan" | "gpu-fan" => 1800.0 + wave.abs() * 1400.0,
        "pwm-value" => 80.0 + wave.abs() * 90.0,
        "ram-usage" => 8.0 + (t * 0.05).sin().abs() * 2.0,
        _ => 40.0 + wave * 15.0,
    }
}

fn group_accent(group: &str) -> (f64, f64, f64) {
    match group {
        "CPU" => paint::unit_rgb("#f04b4b"),
        "GPU" => paint::unit_rgb("#3aaeef"),
        "HP Embedded Controller" => paint::unit_rgb("#06b48a"),
        "System" => paint::unit_rgb("#9d9d9d"),
        _ => paint::unit_rgb("#f04b4b"),
    }
}
