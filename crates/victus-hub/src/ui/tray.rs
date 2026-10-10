//! Status icon and the same menu the Qt window puts on `QSystemTrayIcon`.
//!
//! `tray-icon` is built with the KSNI backend so this GTK 4 process does not
//! also load GTK 3.

use gtk4::gdk::prelude::TextureExtManual;
use gtk4::prelude::*;
use tray_icon::menu::{CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use std::io::Write;
use std::os::unix::net::UnixStream;
use std::sync::{Arc, Mutex, OnceLock};

static ACTIONS: OnceLock<Mutex<Vec<Action>>> = OnceLock::new();

pub enum Action {
    Show,
    Toggle,
    Quit,
    Profile(i32),
    Fan(String),
}

pub struct Tray {
    _icon: TrayIcon,
    profiles: Vec<CheckMenuItem>,
    fans: Vec<(String, CheckMenuItem)>,
}

impl Tray {
    pub fn install(profile: i32, fan_mode: &str, fan_modes: &[(String, String)], wake: Arc<UnixStream>) -> Result<Self, String> {
        let icon_wake = Arc::clone(&wake);
        TrayIconEvent::set_event_handler(Some(move |event| {
            if activates(&event) {
                ACTIONS.get_or_init(|| Mutex::new(Vec::new())).lock().expect("tray actions").push(Action::Show);
                let _ = (&*icon_wake).write(&[1]);
            }
        }));
        MenuEvent::set_event_handler(Some(move |event: MenuEvent| {
            if let Some(action) = menu_action(event.id().as_ref()) {
                ACTIONS.get_or_init(|| Mutex::new(Vec::new())).lock().expect("tray actions").push(action);
                let _ = (&*wake).write(&[1]);
            }
        }));
        let icon = logo().ok_or_else(|| "the Victus Hub icon could not be loaded".to_owned())?;
        let menu = Menu::new();
        let show = MenuItem::with_id("show", "Show/Hide", true, None);
        append(&menu, &show)?;
        append(&menu, &PredefinedMenuItem::separator())?;
        let performance = MenuItem::with_id("performance", "Performance", false, None);
        append(&menu, &performance)?;
        let mut profiles = Vec::new();
        for (index, label) in ["Eco", "Balanced", "Performance"].into_iter().enumerate() {
            let item = CheckMenuItem::with_id(format!("profile-{index}"), label, true, i32::try_from(index).ok() == Some(profile), None);
            append(&menu, &item)?;
            profiles.push(item);
        }
        append(&menu, &PredefinedMenuItem::separator())?;
        let fans_header = MenuItem::with_id("fans", "Fan Mode", false, None);
        append(&menu, &fans_header)?;
        let mut fans = Vec::new();
        for (key, label) in fan_modes {
            let item = CheckMenuItem::with_id(format!("fan-{key}"), label, true, key == fan_mode, None);
            append(&menu, &item)?;
            fans.push((key.clone(), item));
        }
        append(&menu, &PredefinedMenuItem::separator())?;
        let quit = MenuItem::with_id("quit", "Quit", true, None);
        append(&menu, &quit)?;
        let icon = TrayIconBuilder::new()
            .with_tooltip("Victus Hub")
            .with_icon(icon)
            .with_menu(Box::new(menu))
            .build()
            .map_err(|error| error.to_string())?;
        Ok(Self { _icon: icon, profiles, fans })
    }

    pub fn sync(&self, profile: i32, fan_mode: &str) {
        for (index, item) in self.profiles.iter().enumerate() {
            let checked = i32::try_from(index).ok() == Some(profile);
            if item.is_checked() != checked { item.set_checked(checked); }
        }
        for (key, item) in &self.fans {
            let checked = key == fan_mode;
            if item.is_checked() != checked { item.set_checked(checked); }
        }
    }

    pub fn poll() -> Vec<Action> {
        let mut actions = std::mem::take(&mut *ACTIONS.get_or_init(|| Mutex::new(Vec::new())).lock().expect("tray actions"));
        while let Ok(event) = TrayIconEvent::receiver().try_recv() {
            if activates(&event) {
                actions.push(Action::Show);
            }
        }
        while let Ok(event) = MenuEvent::receiver().try_recv() {
            let id = event.id().as_ref();
            if id == "show" {
                actions.push(Action::Toggle);
            } else if id == "quit" {
                actions.push(Action::Quit);
            } else if let Some(index) = id.strip_prefix("profile-") {
                if let Ok(index) = index.parse() {
                    actions.push(Action::Profile(index));
                }
            } else if let Some(mode) = id.strip_prefix("fan-") {
                actions.push(Action::Fan(mode.to_owned()));
            }
        }
        actions
    }
}

fn menu_action(id: &str) -> Option<Action> {
    match id {
        "show" => Some(Action::Toggle), "quit" => Some(Action::Quit),
        _ if id.starts_with("profile-") => id[8..].parse().ok().map(Action::Profile),
        _ => id.strip_prefix("fan-").map(|key| Action::Fan(key.to_owned())),
    }
}

fn append(menu: &Menu, item: &dyn tray_icon::menu::IsMenuItem) -> Result<(), String> {
    menu.append(item).map_err(|error| error.to_string())
}

fn activates(event: &TrayIconEvent) -> bool {
    match event {
        TrayIconEvent::Click { button, button_state, .. } => *button == MouseButton::Left && *button_state == MouseButtonState::Up,
        TrayIconEvent::DoubleClick { button, .. } => *button == MouseButton::Left,
        _ => false,
    }
}

fn logo() -> Option<Icon> {
    let bytes = include_bytes!("../../assets/icons/logoV.png");
    let texture = gtk4::gdk::Texture::from_bytes(&gtk4::glib::Bytes::from_static(bytes)).ok()?;
    let width = texture.width();
    let height = texture.height();
    if width <= 0 || height <= 0 {
        return None;
    }
    let stride = width * 4;
    let mut data = vec![0u8; (stride * height) as usize];
    texture.download(&mut data, stride as usize);
    let straight = cairo_argb_to_rgba(&data, width as usize, height as usize, stride as usize);
    let (rgba, side) = if width > 64 || height > 64 {
        (downscale(&straight, width as usize, height as usize, 64, 64), 64)
    } else {
        (straight, width as u32)
    };
    Icon::from_rgba(rgba, side, side).ok()
}

fn cairo_argb_to_rgba(data: &[u8], width: usize, height: usize, stride: usize) -> Vec<u8> {
    let mut rgba = Vec::with_capacity(width * height * 4);
    for y in 0..height {
        let row = &data[y * stride..y * stride + width * 4];
        for pixel in row.chunks_exact(4) {
            let blue = pixel[0];
            let green = pixel[1];
            let red = pixel[2];
            let alpha = pixel[3];
            if alpha == 0 {
                rgba.extend([0, 0, 0, 0]);
            } else {
                rgba.push(((u16::from(red) * 255) / u16::from(alpha)) as u8);
                rgba.push(((u16::from(green) * 255) / u16::from(alpha)) as u8);
                rgba.push(((u16::from(blue) * 255) / u16::from(alpha)) as u8);
                rgba.push(alpha);
            }
        }
    }
    rgba
}

fn downscale(src: &[u8], width: usize, height: usize, target_w: usize, target_h: usize) -> Vec<u8> {
    let mut out = vec![0u8; target_w * target_h * 4];
    for y in 0..target_h {
        let y0 = y * height / target_h;
        let y1 = ((y + 1) * height / target_h).max(y0 + 1);
        for x in 0..target_w {
            let x0 = x * width / target_w;
            let x1 = ((x + 1) * width / target_w).max(x0 + 1);
            let mut acc = [0u32; 4];
            let mut count = 0u32;
            for sy in y0..y1 {
                for sx in x0..x1 {
                    let index = (sy * width + sx) * 4;
                    for channel in 0..4 {
                        acc[channel] += u32::from(src[index + channel]);
                    }
                    count += 1;
                }
            }
            let index = (y * target_w + x) * 4;
            for channel in 0..4 {
                out[index + channel] = (acc[channel] / count.max(1)) as u8;
            }
        }
    }
    out
}
