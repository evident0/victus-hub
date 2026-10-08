use eframe::egui;

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, Sender};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use victus_core::{
    effects_for_zone_count, hex_to_rgb, parse_sensors_response, parse_status_response, transact, FanMode, PROFILE_LABELS,
    PROFILE_KEYS,
};

use crate::{connects_to_socket, diagnostics_from_logs, sensor_request, update_choice, update_shell, Model, PAGES, RELEASE_URL, UpdateChoice, GITHUB_INSTALL_COMMAND};

const BG: egui::Color32 = egui::Color32::from_rgb(0x16, 0x16, 0x16);
const PILL: egui::Color32 = egui::Color32::from_rgb(0x20, 0x20, 0x20);
const TEXT: egui::Color32 = egui::Color32::from_rgb(0xED, 0xED, 0xED);
const ECO: egui::Color32 = egui::Color32::from_rgb(0x2F, 0xBF, 0x8F);
const BALANCED: egui::Color32 = egui::Color32::from_rgb(0x3F, 0x8C, 0xFF);
const PERF: egui::Color32 = egui::Color32::from_rgb(0xE2, 0x57, 0x2C);

pub struct VictusApp {
    model: Model,
    activate: Receiver<()>,
    samples: Receiver<victus_core::SensorSnapshot>,
    stop: Arc<AtomicBool>,
    page_slot: Arc<AtomicUsize>,
    visible: Arc<AtomicBool>,
    confirm_update: Option<String>,
    color_text: String,
}

impl VictusApp {
    pub fn new(model: Model, activate: Receiver<()>, samples: Receiver<victus_core::SensorSnapshot>, stop: Arc<AtomicBool>, page_slot: Arc<AtomicUsize>, visible: Arc<AtomicBool>) -> Self {
        let color_text = model.state.lighting.color.clone();
        Self { model, activate, samples, stop, page_slot, visible, confirm_update: None, color_text }
    }
}

impl eframe::App for VictusApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if self.activate.try_recv().is_ok() {
            self.model.visible = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
        }
        if ctx.input(|input| input.viewport().close_requested()) && !self.model.quit {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
            self.model.visible = false;
        }
        self.visible.store(self.model.visible, Ordering::Relaxed);
        self.page_slot.store(self.model.page, Ordering::Relaxed);
        while let Ok(snapshot) = self.samples.try_recv() {
            self.model.note_sample(snapshot.cpu_temp_c);
            self.model.snapshot = snapshot;
        }
        egui::SidePanel::left("sidebar").exact_width(200.0).show(ctx, |ui| {
            ui.heading("Victus Hub");
            ui.add_space(12.0);
            for (index, name) in PAGES.iter().enumerate() {
                let selected = self.model.page == index;
                if ui.selectable_label(selected, *name).clicked() {
                    self.model.page = index;
                }
            }
            ui.with_layout(egui::Layout::bottom_up(egui::Align::LEFT), |ui| {
                ui.label(self.model.status.chars().take(80).collect::<String>());
            });
        });
        egui::CentralPanel::default().show(ctx, |ui| match self.model.page {
            0 => self.home(ui),
            1 => self.power(ui),
            2 => self.fans(ui),
            3 => self.keyboard(ui),
            4 => self.sensors(ui),
            _ => self.settings(ui),
        });
        if let Some(tag) = self.confirm_update.clone() {
            egui::Window::new("Update available").show(ctx, |ui| {
                ui.label(format!("Install Victus Hub {tag}? The app closes during installation."));
                ui.horizontal(|ui| {
                    if ui.button("Update").clicked() {
                        launch_update();
                        self.model.quit = true;
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                    if ui.button("Cancel").clicked() {
                        self.confirm_update = None;
                    }
                });
            });
        }
        ctx.request_repaint_after(Duration::from_millis(200));
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

impl VictusApp {
    fn send(&mut self, line: &str) {
        let Some(socket) = connects_to_socket(&self.model).map(std::path::Path::to_path_buf) else {
            self.model.status = "Offline preview".into();
            return;
        };
        match transact(&socket, line, Duration::from_secs(2)) {
            Ok(reply) => match parse_status_response(&reply) {
                Ok(message) => self.model.status = message,
                Err(error) => self.model.status = error.to_string(),
            },
            Err(error) => self.model.status = error.to_string(),
        }
    }

    fn home(&mut self, ui: &mut egui::Ui) {
        ui.heading("Profile");
        ui.horizontal(|ui| {
            for (index, label) in PROFILE_LABELS.iter().enumerate() {
                let color = [ECO, BALANCED, PERF][index];
                if ui.add(egui::Button::new(*label).fill(color)).clicked() {
                    self.model.state.profile_before_battery = None;
                    self.send(&format!("set-profile\t{index}"));
                }
            }
        });
        ui.add_space(16.0);
        ui.label(format!("CPU {}", self.model.snapshot.cpu_temp.value));
        ui.label(format!("GPU {}", self.model.snapshot.gpu_temp.value));
        ui.label(format!("Fans {} / {}", self.model.snapshot.cpu_fan.value, self.model.snapshot.gpu_fan.value));
        ui.label(format!("Power {}", self.model.snapshot.cpu_power.value));
        ui.label(format!("Memory {}", self.model.snapshot.ram_usage.value));
    }

    fn power(&mut self, ui: &mut egui::Ui) {
        ui.heading("Power limits");
        let policy = &mut self.model.state.power;
        ui.checkbox(&mut policy.enabled, "Reapply limits");
        slider_mw(ui, "STAPM / PL1", &mut policy.stapm_limit);
        slider_mw(ui, "Fast / PL2", &mut policy.fast_limit);
        slider_mw(ui, "Slow", &mut policy.slow_limit);
        ui.add(egui::Slider::new(&mut policy.tctl_temp, 75..=100).text("Tctl °C"));
        ui.add(egui::Slider::new(&mut policy.reapply_seconds, 1..=120).text("Reapply seconds"));
        if ui.button("Apply power").clicked() {
            let line = crate::power_request(&self.model.state);
            self.send(&line);
        }
        ui.separator();
        let mut battery = self.model.state.battery_power_save;
        if ui.checkbox(&mut battery, "Battery power saver").changed() {
            self.model.state.battery_power_save = battery;
            self.send(&format!("battery-power-save\t{}", i32::from(battery)));
        }
        let mut minimum = self.model.state.cpu_frequency.map(|pair| pair.0).unwrap_or(1_400_000);
        let mut maximum = self.model.state.cpu_frequency.map(|pair| pair.1).unwrap_or(5_000_000);
        ui.add(egui::Slider::new(&mut minimum, 400_000..=5_000_000).text("CPU min kHz"));
        ui.add(egui::Slider::new(&mut maximum, 400_000..=6_000_000).text("CPU max kHz"));
        if ui.button("Apply CPU frequency").clicked() && minimum > 0 && minimum <= maximum {
            self.model.state.cpu_frequency = Some((minimum, maximum));
            self.send(&format!("cpu-frequency-config\t{minimum}\t{maximum}"));
        }
    }

    fn fans(&mut self, ui: &mut egui::Ui) {
        ui.heading("Fans");
        ui.horizontal(|ui| {
            for (label, mode) in [("Auto", FanMode::Auto), ("Max", FanMode::Max), ("Smart", FanMode::Smart), ("Custom", FanMode::Custom)] {
                if ui.button(label).clicked() {
                    let requests = self.model.select_fan_mode(mode);
                    for request in requests {
                        self.send(&request);
                    }
                }
            }
        });
        ui.label(format!("Mode {}", victus_core::FanMode::from_config(&self.model.state.fan).label()));
        ui.label(format!("CPU fan {}", self.model.snapshot.cpu_fan.value));
        ui.label(format!("GPU fan {}", self.model.snapshot.gpu_fan.value));
        if let Some(profile) = self.model.state.fan.profiles.get_mut(1) {
            ui.label("Balanced CPU curve");
            for point in &mut profile.cpu_points {
                ui.add(egui::Slider::new(&mut point.speed, 0..=100).text(format!("{} °C", point.temp)));
            }
        }
        if ui.button("Apply curve").clicked() {
            let line = format!("fan-config\t{}", victus_core::config_to_value(&self.model.state.fan));
            self.send(&line);
        }
    }

    fn keyboard(&mut self, ui: &mut egui::Ui) {
        ui.heading("Keyboard");
        let lighting = &mut self.model.state.lighting;
        ui.checkbox(&mut lighting.enabled, "Backlight");
        let zones = self.model.zones.max(1);
        let mut effect = lighting.effect.clone();
        egui::ComboBox::from_label("Effect").selected_text(&effect).show_ui(ui, |ui| {
            for (id, label) in effects_for_zone_count(zones) {
                ui.selectable_value(&mut effect, (*id).to_owned(), *label);
            }
        });
        lighting.effect = effect;
        ui.add(egui::Slider::new(&mut lighting.brightness, 0..=255).text("Brightness"));
        ui.add(egui::Slider::new(&mut lighting.speed, 1..=100).text("Speed"));
        ui.add(egui::Slider::new(&mut lighting.idle_timeout, 0..=3600).text("Idle dim seconds"));
        ui.label("Color");
        ui.text_edit_singleline(&mut self.color_text);
        let color = hex_to_rgb(&self.color_text);
        let (rect, _) = ui.allocate_exact_size(egui::vec2(48.0, 24.0), egui::Sense::hover());
        ui.painter().rect_filled(rect, 4.0, egui::Color32::from_rgb(color.red, color.green, color.blue));
        ui.label("Zone preview");
        ui.horizontal_wrapped(|ui| {
            for (index, label) in ["Q", "A", "W", "S", "D", "P"].iter().enumerate() {
                let zone = self.model.keyboard_zone(label, index as f64 * 40.0, 240.0);
                if ui.button(format!("{label} z{zone}")).clicked() {
                    self.model.status = format!("{label} is zone {zone}");
                }
            }
        });
        if ui.button("Apply lighting").clicked() {
            self.model.state.lighting.color = self.color_text.clone();
            let line = crate::lighting_request(&self.model.state.lighting);
            self.send(&line);
        }
    }

    fn sensors(&mut self, ui: &mut egui::Ui) {
        ui.heading("Sensors");
        let snap = &self.model.snapshot;
        for line in [
            format!("CPU temp {}", snap.cpu_temp.value),
            format!("CPU usage {}", snap.cpu_usage.value),
            format!("CPU power {}", snap.cpu_power.value),
            format!("GPU temp {}", snap.gpu_temp.value),
            format!("GPU usage {}", snap.gpu_usage.value),
            format!("GPU power {}", snap.gpu_power.value),
            format!("PWM {} {}", snap.pwm_mode.value, snap.pwm_value.value),
            format!("RAM {}", snap.ram_usage.value),
        ] {
            ui.label(line);
        }
        let (response, painter) = ui.allocate_painter(egui::vec2(ui.available_width(), 160.0), egui::Sense::hover());
        let rect = response.rect;
        painter.rect_filled(rect, 6.0, PILL);
        let points: Vec<f64> = self.model.history.iter().copied().collect();
        if points.len() >= 2 {
            let max = points.iter().copied().fold(1.0_f64, f64::max).max(1.0);
            let step = rect.width() / (points.len() - 1) as f32;
            for (index, window) in points.windows(2).enumerate() {
                let x1 = rect.left() + step * index as f32;
                let x2 = x1 + step;
                let y1 = rect.bottom() - (window[0] / max) as f32 * rect.height();
                let y2 = rect.bottom() - (window[1] / max) as f32 * rect.height();
                painter.line_segment([egui::pos2(x1, y1), egui::pos2(x2, y2)], egui::Stroke::new(2.0, BALANCED));
            }
        }
    }

    fn settings(&mut self, ui: &mut egui::Ui) {
        ui.heading("Settings");
        let mut shortcuts = self.model.state.hardware_shortcuts;
        if ui.checkbox(&mut shortcuts, "Hardware shortcuts (Ctrl+Shift)").changed() {
            self.model.state.hardware_shortcuts = shortcuts;
            self.send(&format!("hardware-shortcuts\t{}", i32::from(shortcuts)));
        }
        let mut nvidia = self.model.state.disable_nvidia_queries;
        if ui.checkbox(&mut nvidia, "Disable NVIDIA queries on Power Saver").changed() {
            self.model.state.disable_nvidia_queries = nvidia;
            self.send(&format!("disable-nvidia-queries\t{}", i32::from(nvidia)));
        }
        ui.label(format!("Installed version {}", victus_core::PROGRAM_VERSION));
        if ui.button("Check for updates").clicked() {
            self.confirm_update = check_release();
            if self.confirm_update.is_none() {
                self.model.status = "Program is up to date.".into();
            }
        }
        if ui.button("Save diagnostics").clicked() {
            self.model.status = save_diagnostics();
        }
        if ui.button("Quit").clicked() {
            self.model.quit = true;
            ui.ctx().send_viewport_cmd(egui::ViewportCommand::Close);
        }
        let _ = PROFILE_KEYS;
        let _ = GITHUB_INSTALL_COMMAND;
    }
}

fn slider_mw(ui: &mut egui::Ui, label: &str, mw: &mut i32) {
    let mut watts = *mw / 1000;
    if ui.add(egui::Slider::new(&mut watts, 15..=120).text(label)).changed() {
        *mw = watts * 1000;
    }
}

pub fn install_theme(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    fonts.font_data.insert(
        "plex".to_owned(),
        std::sync::Arc::new(egui::FontData::from_static(include_bytes!(
            "../../../victus_hub/resources/fonts/IBMPlexSans-Regular.ttf"
        ))),
    );
    if let Some(family) = fonts.families.get_mut(&egui::FontFamily::Proportional) {
        family.insert(0, "plex".to_owned());
    }
    ctx.set_fonts(fonts);
    let mut visuals = egui::Visuals::dark();
    visuals.panel_fill = BG;
    visuals.window_fill = BG;
    visuals.extreme_bg_color = PILL;
    visuals.override_text_color = Some(TEXT);
    ctx.set_visuals(visuals);
}

pub fn spawn_sensor_thread(model: &Model, samples: Sender<victus_core::SensorSnapshot>, stop: Arc<AtomicBool>, page: Arc<AtomicUsize>, visible: Arc<AtomicBool>) {
    let Some(socket) = connects_to_socket(model).map(std::path::Path::to_path_buf) else { return };
    thread::spawn(move || {
        while !stop.load(Ordering::Relaxed) {
            if visible.load(Ordering::Relaxed) {
                if let Some(request) = sensor_request(page.load(Ordering::Relaxed)) {
                    if let Ok(reply) = transact(&socket, &request, Duration::from_millis(800)) {
                        if let Ok(snapshot) = parse_sensors_response(&reply) {
                            let _ = samples.send(snapshot);
                        }
                    }
                }
            }
            thread::sleep(Duration::from_secs(1));
        }
    });
}

pub fn hydrate_live(model: &mut Model) {
    let Some(socket) = connects_to_socket(model).map(std::path::Path::to_path_buf) else { return };
    let Ok(reply) = transact(&socket, "get-state", Duration::from_secs(2)) else { return };
    let Some(json) = reply.trim().strip_prefix("OK\t") else { return };
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(json) {
        model.hydrate(&value);
    }
}

fn check_release() -> Option<String> {
    let output = std::process::Command::new("curl")
        .args(["-fsSL", "--max-time", "10", "-H", "Accept: application/vnd.github+json", "-H", "User-Agent: victus-hub", RELEASE_URL])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let body = String::from_utf8_lossy(&output.stdout);
    let tag = crate::parse_release_tag(&body)?;
    (update_choice(&tag, victus_core::PROGRAM_VERSION) == UpdateChoice::Available).then_some(tag)
}

fn launch_update() {
    let script = update_shell();
    for (name, args) in [
        ("gnome-terminal", vec!["--"]),
        ("konsole", vec!["--separate", "--hold", "-e"]),
        ("ptyxis", vec!["--new-window", "--"]),
        ("kgx", vec!["--"]),
        ("xfce4-terminal", vec!["--disable-server", "-x"]),
        ("xterm", vec!["-hold", "-T", "Victus Hub Update", "-e"]),
    ] {
        if which(name) {
            let mut command = std::process::Command::new(name);
            command.args(args).arg("/bin/bash").arg("-c").arg(&script);
            let _ = command.spawn();
            return;
        }
    }
}

fn which(name: &str) -> bool {
    ["/usr/bin", "/bin", "/usr/local/bin"].iter().any(|dir| std::path::Path::new(dir).join(name).is_file())
}

fn save_diagnostics() -> String {
    let modules = read_command("journalctl", &["-k", "-b", "--no-pager"]);
    let daemon = read_command("journalctl", &["-u", "victus-hubd", "-b", "--no-pager"]);
    let text = diagnostics_from_logs(modules.as_deref(), modules.as_deref(), daemon.as_deref());
    let path = dirs_download().join(format!("victus-hub-diagnostics-{}.md", unix_stamp()));
    match std::fs::write(&path, text) {
        Ok(()) => format!("Saved {}", path.display()),
        Err(error) => format!("Could not save diagnostics: {error}"),
    }
}

fn read_command(program: &str, args: &[&str]) -> Option<String> {
    let output = std::process::Command::new(program).args(args).output().ok()?;
    output.status.success().then(|| String::from_utf8_lossy(&output.stdout).into_owned())
}

fn dirs_download() -> std::path::PathBuf {
    std::env::var_os("HOME").map(|home| std::path::PathBuf::from(home).join("Downloads")).filter(|path| path.is_dir()).unwrap_or_else(std::env::temp_dir)
}

fn unix_stamp() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|duration| duration.as_secs()).unwrap_or(0)
}

pub fn start(model: Model, activate: Receiver<()>) -> eframe::Result {
    let stop = Arc::new(AtomicBool::new(false));
    let page = Arc::new(AtomicUsize::new(model.page));
    let visible = Arc::new(AtomicBool::new(true));
    let (sample_tx, sample_rx) = std::sync::mpsc::channel();
    spawn_sensor_thread(&model, sample_tx, Arc::clone(&stop), Arc::clone(&page), Arc::clone(&visible));
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1100.0, 720.0]).with_min_inner_size([420.0, 640.0]),
        ..Default::default()
    };
    eframe::run_native(
        "Victus Hub",
        options,
        Box::new(move |cc| {
            install_theme(&cc.egui_ctx);
            Ok(Box::new(VictusApp::new(model, activate, sample_rx, stop, page, visible)))
        }),
    )
}

#[allow(dead_code)]
fn _clock() -> Instant {
    Instant::now()
}
