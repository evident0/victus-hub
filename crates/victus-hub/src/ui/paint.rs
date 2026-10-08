//! Cairo drawing for the icon rail, keyboard, fan chart, and strips.
//!
//! Widgets stay stock GTK objects. Nothing here subclasses a widget.

use gtk4::cairo::{self, Context};
use victus_core::{zone_for_key, FanPoint, RgbColor, TEMP_MIN_C};

const RAIL: f64 = 62.0;
const BUTTON: f64 = 42.0;
const STEP: f64 = 46.0;

pub struct NavButton {
    pub page: usize,
    pub x: f64,
    pub y: f64,
}

pub fn nav_buttons(keyboard: bool, height: f64) -> Vec<NavButton> {
    let mut pages = vec![0, 1, 2];
    if keyboard {
        pages.push(3);
    }
    pages.push(4);
    let x = (RAIL - BUTTON) / 2.0;
    let mut buttons = Vec::new();
    for (index, page) in pages.into_iter().enumerate() {
        buttons.push(NavButton { page, x, y: 20.0 + index as f64 * STEP });
    }
    buttons.push(NavButton { page: 5, x, y: (height - 20.0 - BUTTON).max(20.0) });
    buttons
}

pub fn nav_page(keyboard: bool, height: f64, x: f64, y: f64) -> Option<usize> {
    nav_buttons(keyboard, height).into_iter().find(|button| {
        (button.x..button.x + BUTTON).contains(&x)
            && (button.y..button.y + BUTTON).contains(&y)
    }).map(|button| button.page)
}

pub fn sidebar(cr: &Context, width: f64, height: f64, keyboard: bool, selected: usize, hover: Option<usize>) {
    sidebar_with_pill(cr, width, height, keyboard, selected, hover, None);
}

pub fn sidebar_with_pill(cr: &Context, width: f64, height: f64, keyboard: bool, selected: usize, hover: Option<usize>, pill_y: Option<f64>) {
    let _ = cr.set_source_rgb(15.0 / 255.0, 15.0 / 255.0, 15.0 / 255.0);
    let _ = cr.rectangle(0.0, 0.0, width, height);
    let _ = cr.fill();
    let buttons = nav_buttons(keyboard, height);
    if let Some(button) = buttons.iter().find(|button| button.page == selected) {
        let _ = cr.set_source_rgb(32.0 / 255.0, 32.0 / 255.0, 32.0 / 255.0);
        rounded(cr, button.x, pill_y.unwrap_or(button.y), BUTTON, BUTTON, 12.0);
        let _ = cr.fill();
    }
    for button in buttons {
        let color = if button.page == selected {
            "#EDEDED"
        } else if hover == Some(button.page) {
            "#969696"
        } else {
            "#848484"
        };
        let _ = cr.save();
        let _ = cr.translate(button.x + (BUTTON - 18.0) / 2.0, button.y + (BUTTON - 18.0) / 2.0);
        let _ = cr.scale(18.0 / 24.0, 18.0 / 24.0);
        source_hex(cr, color);
        icon(cr, button.page);
        let _ = cr.restore();
    }
    let _ = cr.set_source_rgb(39.0 / 255.0, 39.0 / 255.0, 39.0 / 255.0);
    let _ = cr.move_to(width - 0.5, 0.0);
    let _ = cr.line_to(width - 0.5, height);
    let _ = cr.set_line_width(1.0);
    let _ = cr.stroke();
}

fn icon(cr: &Context, page: usize) {
    // Match the 24-unit SVG paths in victus_hub/widgets/sidebar.py.
    match page {
        0 => {
            let _ = cr.new_sub_path();
            let _ = cr.arc(12.0, 12.0, 8.5, 0.0, std::f64::consts::TAU);
            stroke_only(cr, 1.6);
            let _ = cr.new_sub_path();
            let _ = cr.arc(12.0, 12.0, 3.4, 0.0, std::f64::consts::TAU);
            let _ = cr.fill();
        }
        1 => {
            let _ = cr.move_to(13.0, 2.0);
            let _ = cr.line_to(4.0, 14.0);
            let _ = cr.line_to(11.0, 14.0);
            let _ = cr.line_to(10.0, 22.0);
            let _ = cr.line_to(20.0, 9.0);
            let _ = cr.line_to(13.0, 9.0);
            let _ = cr.close_path();
            let _ = cr.fill();
        }
        2 => {
            wave(cr, 8.5);
            wave(cr, 15.5);
            stroke_only(cr, 1.6);
        }
        3 => {
            rounded(cr, 2.5, 5.5, 19.0, 13.0, 2.0);
            for y in [9.5, 12.5] {
                for x in [6.0, 9.8, 13.6, 17.4] {
                    let _ = cr.move_to(x, y);
                    let _ = cr.line_to(x + 0.4, y);
                }
            }
            let _ = cr.move_to(7.5, 15.5);
            let _ = cr.line_to(16.5, 15.5);
            stroke_only(cr, 1.6);
        }
        4 => {
            let _ = cr.move_to(10.0, 14.2);
            let _ = cr.line_to(10.0, 5.0);
            let _ = cr.arc(12.0, 5.0, 2.0, std::f64::consts::PI, std::f64::consts::TAU);
            let _ = cr.line_to(14.0, 14.2);
            // SVG A4 4 0 1 1 10 14.2: the long arc around the bulb.
            let _ = cr.arc(12.0, 14.2 + 12.0_f64.sqrt(), 4.0, -std::f64::consts::PI / 3.0, 4.0 * std::f64::consts::PI / 3.0);
            let _ = cr.close_path();
            let _ = cr.move_to(12.0, 8.0);
            let _ = cr.line_to(12.0, 17.0);
            stroke_only(cr, 1.6);
            let _ = cr.new_sub_path();
            let _ = cr.arc(12.0, 17.7, 2.2, 0.0, std::f64::consts::TAU);
            let _ = cr.fill();
        }
        _ => gear(cr),
    }
}

fn wave(cr: &Context, y: f64) {
    let _ = cr.move_to(3.0, y);
    let _ = cr.curve_to(5.5, y - 3.0, 8.5, y + 3.0, 12.0, y);
    let _ = cr.curve_to(15.5, y - 3.0, 18.5, y + 3.0, 21.0, y);
}

fn gear(cr: &Context) {
    let (cx, cy) = (12.0, 12.0);
    for index in 0..8 {
        for (corner, (offset, radius)) in [(0.06, 10.0), (0.44, 10.0), (0.56, 7.6), (0.94, 7.6)].into_iter().enumerate() {
            let angle = (index as f64 + offset) * std::f64::consts::FRAC_PI_4;
            let (sin, cos) = angle.sin_cos();
            let x = ((cx + cos * radius) * 100.0).round() / 100.0;
            let y = ((cy + sin * radius) * 100.0).round() / 100.0;
            if index == 0 && corner == 0 {
                let _ = cr.move_to(x, y);
            } else {
                let _ = cr.line_to(x, y);
            }
        }
    }
    let _ = cr.close_path();
    let _ = cr.new_sub_path();
    let _ = cr.arc(cx, cy, 3.4, 0.0, std::f64::consts::TAU);
    let _ = cr.set_fill_rule(cairo::FillRule::EvenOdd);
    let _ = cr.fill();
}

fn stroke_only(cr: &Context, width: f64) {
    let _ = cr.set_line_width(width);
    let _ = cr.set_line_cap(cairo::LineCap::Round);
    let _ = cr.set_line_join(cairo::LineJoin::Round);
    let _ = cr.stroke();
}

pub struct Plot {
    pub left: f64,
    pub top: f64,
    pub width: f64,
    pub height: f64,
    pub temp_max: i32,
}

impl Plot {
    pub fn new(width: f64, height: f64, temp_max: i32) -> Self {
        let left = 36.0;
        let top = 36.0;
        Self { left, top, width: (width - left - 36.0).max(1.0), height: (height - top - 36.0).max(1.0), temp_max }
    }

    pub fn xy(&self, temp: i32, speed: i32) -> (f64, f64) {
        let span = f64::from(self.temp_max - TEMP_MIN_C).max(1.0);
        let x = self.left + f64::from(temp - TEMP_MIN_C) / span * self.width;
        let y = self.top + (1.0 - f64::from(speed.clamp(0, 100)) / 100.0) * self.height;
        (x, y)
    }

    pub fn temp_speed(&self, x: f64, y: f64) -> (i32, i32) {
        let span = f64::from(self.temp_max - TEMP_MIN_C).max(1.0);
        let temp = f64::from(TEMP_MIN_C) + (x - self.left) / self.width * span;
        let speed = (1.0 - (y - self.top) / self.height) * 100.0;
        (round_i32(temp).clamp(TEMP_MIN_C, self.temp_max), round_i32(speed).clamp(0, 100))
    }

    pub fn nearest(&self, points: &[FanPoint], x: f64, y: f64) -> Option<usize> {
        let mut best = None;
        let mut distance = 14.0;
        for (index, point) in points.iter().enumerate() {
            let (px, py) = self.xy(point.temp, point.speed);
            let next = ((px - x).powi(2) + (py - y).powi(2)).sqrt();
            if next < distance {
                distance = next;
                best = Some(index);
            }
        }
        best
    }
}

pub fn move_point(points: &mut [FanPoint], index: usize, temp: i32, speed: i32) {
    if points.len() < 2 || index >= points.len() {
        return;
    }
    let last = points.len().saturating_sub(1);
    let speed = speed.clamp(0, 100);
    if index == 0 || index == last {
        points[index].speed = if index == 0 { speed.min(points[1].speed) } else { speed.max(points[last - 1].speed) };
        return;
    }
    let left = points[index - 1].temp + 1;
    let right = points[index + 1].temp - 1;
    if left <= right {
        points[index].temp = temp.clamp(left, right);
    }
    points[index].speed = speed.clamp(points[index - 1].speed, points[index + 1].speed);
}

/// Insert a middle point. Endpoints stay put, so a click outside the span returns `None`.
pub fn insert_point(points: &mut Vec<FanPoint>, temp: i32, _speed: i32) -> Option<usize> {
    if points.iter().any(|point| point.temp == temp) { return None; }
    let index = points.iter().position(|point| point.temp > temp).unwrap_or(points.len());
    if index == 0 || index == points.len() {
        return None;
    }
    let speed = victus_core::interpolate_fan(points, temp);
    points.insert(index, FanPoint { temp, speed });
    Some(index)
}

pub fn chart(cr: &Context, width: f64, height: f64, temp_max: i32, points: &[FanPoint], accent: (f64, f64, f64), selected: Option<usize>, hover: Option<usize>, current: Option<f64>) {
    let plot = Plot::new(width, height, temp_max);
    let _ = cr.select_font_face("IBM Plex Mono", cairo::FontSlant::Normal, cairo::FontWeight::Normal);
    let _ = cr.set_font_size(11.0);
    source_hex(cr, "#848484");
    for speed in [0, 25, 50, 75, 100] {
        let y = plot.xy(TEMP_MIN_C, speed).1;
        source_hex(cr, "#2c2c2c");
        let _ = cr.move_to(plot.left, y);
        let _ = cr.line_to(plot.left + plot.width, y);
        let _ = cr.set_line_width(1.0);
        let _ = cr.stroke();
        let label = format!("{speed}");
        source_hex(cr, "#848484");
        let label_width = cr.text_extents(&label).map_or(0.0, |text| text.x_advance());
        let _ = cr.move_to(plot.left - 6.0 - label_width, y + 4.0);
        let _ = cr.show_text(&label);
    }
    for temp in (TEMP_MIN_C..=temp_max).step_by(10) {
        let x = plot.xy(temp, 0).0;
        source_hex(cr, "#2c2c2c");
        cr.move_to(x, plot.top);
        cr.line_to(x, plot.top + plot.height);
        let _ = cr.stroke();
        source_hex(cr, "#848484");
        let label = format!("{temp}°");
        let label_width = cr.text_extents(&label).map_or(0.0, |text| text.x_advance());
        cr.move_to(x - label_width / 2.0, plot.top + plot.height + 16.0);
        let _ = cr.show_text(&label);
    }
    if points.len() >= 2 {
        let first = plot.xy(points[0].temp, points[0].speed);
        cr.move_to(first.0, plot.top + plot.height);
        for point in points { let (x, y) = plot.xy(point.temp, point.speed); cr.line_to(x, y); }
        let last = plot.xy(points[points.len() - 1].temp, 0);
        cr.line_to(last.0, plot.top + plot.height);
        cr.close_path();
        cr.set_source_rgba(accent.0, accent.1, accent.2, 40.0 / 255.0);
        let _ = cr.fill();
        let (x, y) = plot.xy(points[0].temp, points[0].speed);
        let _ = cr.move_to(x, y);
        for point in points.iter().skip(1) {
            let (x, y) = plot.xy(point.temp, point.speed);
            let _ = cr.line_to(x, y);
        }
        let _ = cr.set_source_rgb(accent.0, accent.1, accent.2);
        let _ = cr.set_line_width(2.0);
        let _ = cr.set_line_cap(cairo::LineCap::Round);
        let _ = cr.set_line_join(cairo::LineJoin::Round);
        let _ = cr.stroke();
    }
    if let Some(temp) = current {
        let (x, _) = plot.xy(round_i32(temp), 0);
        let _ = cr.set_source_rgba(accent.0, accent.1, accent.2, 0.35);
        let _ = cr.move_to(x, plot.top);
        let _ = cr.line_to(x, plot.top + plot.height);
        let _ = cr.stroke();
    }
    for (index, point) in points.iter().enumerate() {
        let (x, y) = plot.xy(point.temp, point.speed);
        source_hex(cr, "#161616");
        let _ = cr.arc(x, y, 5.0, 0.0, std::f64::consts::TAU);
        let _ = cr.fill_preserve();
        cr.set_source_rgb(accent.0, accent.1, accent.2);
        cr.set_line_width(2.0);
        let _ = cr.stroke();
        if selected == Some(index) {
            source_hex(cr, "#ffffff");
            let _ = cr.arc(x, y, 3.0, 0.0, std::f64::consts::TAU);
            let _ = cr.fill();
        }
    }
    if let Some(point) = hover.and_then(|index| points.get(index)) {
        let (x, y) = plot.xy(point.temp, point.speed);
        let label = format!("{}°C @ {}%", point.temp, point.speed);
        let tip_width = label.chars().count() as f64 * 7.0 + 12.0;
        let left = (x + 14.0).min(plot.left + plot.width - tip_width);
        let top = (y - 28.0).max(plot.top);
        source_hex(cr, "#181818");
        rounded(cr, left, top, tip_width, 22.0, 4.0);
        let _ = cr.fill_preserve();
        source_hex(cr, "#555555");
        cr.set_line_width(1.0);
        let _ = cr.stroke();
        source_hex(cr, "#ededed");
        cr.select_font_face("monospace", cairo::FontSlant::Normal, cairo::FontWeight::Normal);
        cr.set_font_size(12.0);
        cr.move_to(left + 6.0, top + 15.0);
        let _ = cr.show_text(&label);
    }
}

const KEYBOARD: &[&[(&str, f64, f64)]] = &[
    &[
        ("esc", 1.0, 0.0),
        ("f1", 1.0, 0.25),
        ("f2", 1.0, 0.0),
        ("f3", 1.0, 0.0),
        ("f4", 1.0, 0.0),
        ("f5", 1.0, 0.5),
        ("f6", 1.0, 0.0),
        ("f7", 1.0, 0.0),
        ("f8", 1.0, 0.0),
        ("f9", 1.0, 0.5),
        ("f10", 1.0, 0.0),
        ("f11", 1.0, 0.0),
        ("f12", 1.0, 0.0),
    ],
    &[
        ("`", 1.0, 0.0),
        ("1", 1.0, 0.0),
        ("2", 1.0, 0.0),
        ("3", 1.0, 0.0),
        ("4", 1.0, 0.0),
        ("5", 1.0, 0.0),
        ("6", 1.0, 0.0),
        ("7", 1.0, 0.0),
        ("8", 1.0, 0.0),
        ("9", 1.0, 0.0),
        ("0", 1.0, 0.0),
        ("-", 1.0, 0.0),
        ("=", 1.0, 0.0),
        ("⌫", 2.0, 0.0),
    ],
    &[
        ("tab", 1.5, 0.0),
        ("q", 1.0, 0.0),
        ("w", 1.0, 0.0),
        ("e", 1.0, 0.0),
        ("r", 1.0, 0.0),
        ("t", 1.0, 0.0),
        ("y", 1.0, 0.0),
        ("u", 1.0, 0.0),
        ("i", 1.0, 0.0),
        ("o", 1.0, 0.0),
        ("p", 1.0, 0.0),
        ("[", 1.0, 0.0),
        ("]", 1.0, 0.0),
        ("\\", 1.5, 0.0),
    ],
    &[
        ("caps", 1.75, 0.0),
        ("a", 1.0, 0.0),
        ("s", 1.0, 0.0),
        ("d", 1.0, 0.0),
        ("f", 1.0, 0.0),
        ("g", 1.0, 0.0),
        ("h", 1.0, 0.0),
        ("j", 1.0, 0.0),
        ("k", 1.0, 0.0),
        ("l", 1.0, 0.0),
        (";", 1.0, 0.0),
        ("'", 1.0, 0.0),
        ("⏎", 2.25, 0.0),
    ],
    &[
        ("⇧", 2.25, 0.0),
        ("z", 1.0, 0.0),
        ("x", 1.0, 0.0),
        ("c", 1.0, 0.0),
        ("v", 1.0, 0.0),
        ("b", 1.0, 0.0),
        ("n", 1.0, 0.0),
        ("m", 1.0, 0.0),
        (",", 1.0, 0.0),
        (".", 1.0, 0.0),
        ("/", 1.0, 0.0),
        ("⇧", 2.75, 0.0),
    ],
    &[
        ("ctrl", 1.25, 0.0),
        ("fn", 1.25, 0.0),
        ("alt", 1.25, 0.0),
        ("", 4.25, 0.0),
        ("alt", 1.25, 0.0),
        ("ctrl", 1.25, 0.0),
        ("←", 1.0, 0.5),
        ("↓", 1.0, 0.0),
        ("↑", 1.0, 0.0),
        ("→", 1.0, 0.0),
    ],
];

struct KeyBox {
    label: &'static str,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    center_units: f64,
}

fn key_boxes(width: f64, height: f64, compact: bool) -> Vec<KeyBox> {
    let margin = if compact { 0.0 } else { 2.0 };
    let gap = if compact { 2.0 } else { 3.0 };
    let rows = KEYBOARD.len() as f64;
    let inner_w = (width - 2.0 * margin).max(1.0);
    let inner_h = (height - 2.0 * margin).max(1.0);
    let unit_w = inner_w / 15.0;
    let row_h_limit = (inner_h - (rows - 1.0) * gap) / rows;
    let unit = unit_w.min(row_h_limit + gap);
    let row_h = (unit - gap).max(1.0);
    let block_w = 15.0 * unit;
    let block_h = rows * unit - gap;
    let base_x = margin + (inner_w - block_w) / 2.0;
    let base_y = margin + (inner_h - block_h) / 2.0;
    let mut boxes = Vec::new();
    for (row_index, row) in KEYBOARD.iter().enumerate() {
        let flex: f64 = row.iter().map(|(_, width, gap)| width + gap).sum();
        let row_w = flex * unit;
        let mut x = if row_index == 0 { base_x + (block_w - row_w) / 2.0 } else { base_x };
        let y = base_y + row_index as f64 * (row_h + gap);
        let mut flex_x = 0.0;
        for (label, key_w, gap_u) in *row {
            x += gap_u * unit;
            let drawn = key_w * unit - gap;
            boxes.push(KeyBox { label, x, y, w: drawn.max(1.0), h: row_h, center_units: flex_x + gap_u + key_w / 2.0 });
            x += key_w * unit;
            flex_x += key_w + gap_u;
        }
    }
    boxes
}

pub fn keyboard(cr: &Context, width: f64, height: f64, compact: bool, zones: i32, enabled: bool, colors: &[RgbColor]) {
    let radius = if compact { 3.0 } else { 6.0 };
    let layout = pangocairo::functions::create_layout(cr);
    let mut font = gtk4::pango::FontDescription::new();
    font.set_family("IBM Plex Sans");
    for key in key_boxes(width, height, compact) {
        let color = key_color(key.label, key.center_units, zones, enabled, colors);
        let bright = i32::from(color.red) * 299 + i32::from(color.green) * 587 + i32::from(color.blue) * 114;
        if enabled && color.red.max(color.green).max(color.blue) > 30 && !compact {
            let _ = cr.set_source_rgba(f64::from(color.red) / 255.0, f64::from(color.green) / 255.0, f64::from(color.blue) / 255.0, 36.0 / 255.0);
            rounded(cr, key.x - 1.0, key.y - 1.0, key.w + 2.0, key.h + 2.0, radius + 1.0);
            let _ = cr.fill();
        }
        let _ = cr.set_source_rgb(f64::from(color.red) / 255.0, f64::from(color.green) / 255.0, f64::from(color.blue) / 255.0);
        rounded(cr, key.x, key.y, key.w, key.h, radius);
        let _ = cr.fill();
        if !compact && !key.label.is_empty() {
            let size = (key.h * if key.label.chars().count() <= 1 { 0.34 } else { 0.22 }).floor().max(7.0);
            font.set_absolute_size(size * f64::from(gtk4::pango::SCALE));
            layout.set_font_description(Some(&font));
            layout.set_text(key.label);
            if !enabled {
                source_hex(cr, "#5C5C5C");
            } else if bright / 1000 > 150 {
                source_hex(cr, "#161616");
            } else {
                source_hex(cr, "#EDEDED");
            }
            let (text_width, text_height) = layout.pixel_size();
            cr.move_to(key.x + (key.w - f64::from(text_width)) / 2.0, key.y + (key.h - f64::from(text_height)) / 2.0);
            pangocairo::functions::show_layout(cr, &layout);
        }
    }
}

fn key_color(label: &str, center: f64, zones: i32, enabled: bool, colors: &[RgbColor]) -> RgbColor {
    if !enabled || colors.is_empty() {
        return RgbColor::new(32, 32, 32);
    }
    if zones <= 1 {
        return colors[0];
    }
    let zone = zone_for_key(label, center, 15.0);
    colors.get(zone).copied().unwrap_or(colors[0])
}

pub fn key_zone(width: f64, height: f64, x: f64, y: f64) -> Option<usize> {
    key_boxes(width, height, false).into_iter().find(|key| x >= key.x && x <= key.x + key.w && y >= key.y && y <= key.y + key.h).map(|key| zone_for_key(key.label, key.center_units, 15.0))
}

pub fn hue_strip(cr: &Context, width: f64, height: f64, current: (f64, f64, f64)) {
    paint_strip(cr, width, height, current, |index, steps| hsl_to_rgb(index as f64 / steps as f64, 0.85, 0.55));
}

pub fn shade_strip(cr: &Context, width: f64, height: f64, hue: f64, current: (f64, f64, f64)) {
    paint_strip(cr, width, height, current, |index, steps| {
        let t = index as f64 / f64::from(steps - 1);
        hsl_to_rgb(hue, 0.28 + t * 0.6, 0.95 - t * 0.76)
    });
}

fn paint_strip(cr: &Context, width: f64, height: f64, current: (f64, f64, f64), color: impl Fn(i32, i32) -> (f64, f64, f64)) {
    let _ = cr.save();
    cr.set_antialias(cairo::Antialias::None);
    let steps = 36;
    let mut nearest = (0, f64::INFINITY);
    for index in 0..steps {
        let (red, green, blue) = color(index, steps);
        let (red, green, blue) = (f64::from(unit_channel(red)) / 255.0, f64::from(unit_channel(green)) / 255.0, f64::from(unit_channel(blue)) / 255.0);
        let distance = (red - current.0).powi(2) + (green - current.1).powi(2) + (blue - current.2).powi(2);
        if distance < nearest.1 { nearest = (index, distance); }
        let _ = cr.set_source_rgb(red, green, blue);
        let x = (width * f64::from(index) / f64::from(steps)).floor();
        let _ = cr.rectangle(x, 0.0, (width / f64::from(steps) + 1.0).floor(), height);
        let _ = cr.fill();
    }
    if nearest.1 <= 1200.0 / (255.0 * 255.0) {
        source_hex(cr, "#fcfaf9");
        cr.set_line_width(2.0);
        cr.rectangle((f64::from(nearest.0) * width / f64::from(steps) + 1.0).floor(), 1.0, (width / f64::from(steps) - 2.0).floor().max(1.0), height - 2.0);
        let _ = cr.stroke();
    }
    let _ = cr.restore();
}

pub fn hsl_to_rgb(hue: f64, saturation: f64, lightness: f64) -> (f64, f64, f64) {
    let chroma = (1.0 - (2.0 * lightness - 1.0).abs()) * saturation;
    let h = hue.rem_euclid(1.0) * 6.0;
    let x = chroma * (1.0 - (h.rem_euclid(2.0) - 1.0).abs());
    let (r, g, b) = match h as i32 { 0 => (chroma, x, 0.0), 1 => (x, chroma, 0.0), 2 => (0.0, chroma, x),
        3 => (0.0, x, chroma), 4 => (x, 0.0, chroma), _ => (chroma, 0.0, x) };
    let m = lightness - chroma / 2.0;
    (r + m, g + m, b + m)
}

#[cfg(test)]
mod regression_tests {
    use super::*;

    #[test]
    fn dragging_one_point_cannot_raise_its_neighbors() {
        let mut points = vec![FanPoint { temp: 30, speed: 20 }, FanPoint { temp: 60, speed: 40 }, FanPoint { temp: 100, speed: 80 }];
        move_point(&mut points, 1, 70, 95);
        assert_eq!(points[1], FanPoint { temp: 70, speed: 80 });
        assert_eq!(points[2], FanPoint { temp: 100, speed: 80 });
        assert!(insert_point(&mut points, 70, 20).is_none());
        let index = insert_point(&mut points, 50, 100).unwrap();
        assert_eq!(points[index].speed, 50);
    }
}

pub fn chip(cr: &Context, width: f64, height: f64, hex: &str) {
    source_hex(cr, hex);
    rounded(cr, 0.0, 0.0, width, height, if width <= 34.0 { 4.0 } else { 0.0 });
    let _ = cr.fill();
    if width <= 34.0 {
        source_hex(cr, "#2c2c2c");
        rounded(cr, 0.5, 0.5, width - 1.0, height - 1.0, 4.0);
        cr.set_line_width(1.0);
        let _ = cr.stroke();
    }
}

struct Step {
    left: usize,
    left_value: f64,
    right: usize,
    right_value: f64,
}

/// Step chart used by the sensor graph window. The grid is drawn even when
/// every sample is still empty, matching the Qt `SensorLineGraph`.
pub fn sensor_chart(cr: &Context, width: f64, height: f64, samples: &[Option<f64>], min: f64, max: f64, accent: (f64, f64, f64), hover: Option<usize>, unit: &str) {
    if width < 2.0 || height < 2.0 || samples.len() < 2 {
        return;
    }
    let count = samples.len();
    let span = (max - min).max(1.0);
    let x_for = |index: usize| (index as f64 / (count - 1) as f64) * width;
    let y_for = |value: f64| height - ((value - min) / span).clamp(0.0, 1.0) * height;

    source_hex(cr, "#202020");
    rounded(cr, 0.0, 0.0, width, height, 4.0);
    let _ = cr.fill();

    for frac in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0] {
        let y = frac * height;
        source_hex(cr, if frac == 0.0 || frac == 1.0 { "#444444" } else { "#3A3A3A" });
        let _ = cr.set_line_width(1.0);
        let _ = cr.move_to(0.0, y);
        let _ = cr.line_to(width, y);
        let _ = cr.stroke();
    }
    source_hex(cr, "#444444");
    let _ = cr.move_to(0.5, 0.0);
    let _ = cr.line_to(0.5, height);
    let _ = cr.move_to(width - 0.5, 0.0);
    let _ = cr.line_to(width - 0.5, height);
    let _ = cr.stroke();

    let valid: Vec<usize> = samples.iter().enumerate().filter_map(|(index, value)| value.map(|_| index)).collect();
    let mut steps = Vec::new();
    for pair in valid.windows(2) {
        let left = pair[0];
        let right = pair[1];
        let Some(left_value) = samples[left] else { continue };
        let Some(right_value) = samples[right] else { continue };
        steps.push(Step { left, left_value, right, right_value });
    }
    if let (Some(first), Some(last)) = (steps.first(), steps.last()) {
        let _ = cr.new_path();
        let _ = cr.move_to(x_for(first.left), y_for(first.left_value));
        for step in &steps {
            let _ = cr.line_to(x_for(step.right), y_for(step.left_value));
            let _ = cr.line_to(x_for(step.right), y_for(step.right_value));
        }
        let _ = cr.line_to(x_for(last.right), height);
        let _ = cr.line_to(x_for(first.left), height);
        let _ = cr.close_path();
        let _ = cr.set_source_rgba(accent.0, accent.1, accent.2, 128.0 / 255.0);
        let _ = cr.fill();

        let _ = cr.new_path();
        let _ = cr.move_to(x_for(first.left), y_for(first.left_value));
        for step in &steps {
            let _ = cr.line_to(x_for(step.right), y_for(step.left_value));
            let _ = cr.line_to(x_for(step.right), y_for(step.right_value));
        }
        let _ = cr.set_source_rgb(accent.0, accent.1, accent.2);
        let _ = cr.set_line_width(2.0);
        let _ = cr.stroke();
    }

    let Some(index) = hover.filter(|index| steps.iter().any(|step| step.left <= *index && *index <= step.right)) else { return };
    let Some(step) = steps.iter().find(|step| step.left <= index && index <= step.right) else { return };
    let label = if unit.is_empty() { format!("{:.1}", step.left_value) } else { format!("{:.1} {unit}", step.left_value) };
    let _ = cr.select_font_face("IBM Plex Sans", cairo::FontSlant::Normal, cairo::FontWeight::Normal);
    let _ = cr.set_font_size(11.0);
    let width_px = cr.text_extents(&label).map(|extents| extents.width()).unwrap_or(label.len() as f64 * 7.0);
    let tip_w = width_px + 14.0;
    let tip_h = 22.0;
    let hx = x_for(index);
    let hy = y_for(step.left_value);
    let mut tip_x = hx + 12.0;
    if tip_x + tip_w > width {
        tip_x = hx - tip_w - 4.0;
    }
    let mut tip_y = hy - 28.0;
    if tip_y < 4.0 {
        tip_y = hy + 8.0;
    }
    source_hex(cr, "#303030");
    rounded(cr, tip_x, tip_y, tip_w, tip_h, 4.0);
    let _ = cr.fill_preserve();
    let _ = cr.set_source_rgb(accent.0, accent.1, accent.2);
    let _ = cr.set_line_width(1.0);
    let _ = cr.stroke();
    let _ = cr.set_source_rgb(1.0, 1.0, 1.0);
    let _ = cr.move_to(tip_x + 7.0, tip_y + 15.0);
    let _ = cr.show_text(&label);
}

pub fn rgb_to_hsv(red: f64, green: f64, blue: f64) -> (f64, f64, f64) {
    let max = red.max(green).max(blue);
    let min = red.min(green).min(blue);
    let delta = max - min;
    let hue = if delta == 0.0 {
        0.0
    } else if (max - red).abs() < f64::EPSILON {
        ((green - blue) / delta).rem_euclid(6.0) / 6.0
    } else if (max - green).abs() < f64::EPSILON {
        ((blue - red) / delta + 2.0) / 6.0
    } else {
        ((red - green) / delta + 4.0) / 6.0
    };
    let saturation = if max == 0.0 { 0.0 } else { delta / max };
    (hue, saturation, max)
}

pub fn hsv_to_rgb(hue: f64, saturation: f64, value: f64) -> (f64, f64, f64) {
    let hue = hue.rem_euclid(1.0) * 6.0;
    let sector = hue.floor();
    let fraction = hue - sector;
    let p = value * (1.0 - saturation);
    let q = value * (1.0 - fraction * saturation);
    let t = value * (1.0 - (1.0 - fraction) * saturation);
    match round_i32(sector) {
        0 => (value, t, p),
        1 => (q, value, p),
        2 => (p, value, t),
        3 => (p, q, value),
        4 => (t, p, value),
        _ => (value, p, q),
    }
}

pub fn unit_rgb(hex: &str) -> (f64, f64, f64) {
    let color = victus_core::hex_to_rgb(hex);
    (f64::from(color.red) / 255.0, f64::from(color.green) / 255.0, f64::from(color.blue) / 255.0)
}

pub fn hex_from_unit(red: f64, green: f64, blue: f64) -> String {
    victus_core::rgb_to_hex(RgbColor::new(unit_channel(red), unit_channel(green), unit_channel(blue)))
}

fn unit_channel(value: f64) -> u8 {
    round_i32(value.clamp(0.0, 1.0) * 255.0).clamp(0, 255) as u8
}

pub fn round_i32(value: f64) -> i32 {
    let rounded = value.round();
    if rounded >= f64::from(i32::MAX) {
        i32::MAX
    } else if rounded <= f64::from(i32::MIN) {
        i32::MIN
    } else {
        rounded as i32
    }
}

fn source_hex(cr: &Context, hex: &str) {
    let (red, green, blue) = unit_rgb(hex);
    let _ = cr.set_source_rgb(red, green, blue);
}

pub(super) fn rounded(cr: &Context, x: f64, y: f64, width: f64, height: f64, radius: f64) {
    let radius = radius.min(width / 2.0).min(height / 2.0).max(0.0);
    let _ = cr.new_sub_path();
    let _ = cr.arc(x + width - radius, y + radius, radius, -std::f64::consts::FRAC_PI_2, 0.0);
    let _ = cr.arc(x + width - radius, y + height - radius, radius, 0.0, std::f64::consts::FRAC_PI_2);
    let _ = cr.arc(x + radius, y + height - radius, radius, std::f64::consts::FRAC_PI_2, std::f64::consts::PI);
    let _ = cr.arc(x + radius, y + radius, radius, std::f64::consts::PI, std::f64::consts::PI + std::f64::consts::FRAC_PI_2);
    let _ = cr.close_path();
}
