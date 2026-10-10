use std::cell::{Cell, RefCell};
use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::rc::{Rc, Weak};
use std::sync::atomic::{AtomicBool, AtomicUsize};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::Instant;

use gtk4::glib;
use victus_core::{PowerPolicy, SensorSnapshot};

use super::graph;
use super::maintenance::Release;
use super::pages::{self, SensorRow};
use super::tray;
use crate::Model;

pub(super) struct Running {
    pub(super) min: f64,
    pub(super) max: f64,
    pub(super) sum: f64,
    pub(super) count: u32,
}

pub(super) enum BackgroundEvent {
    Release(Release),
    Diagnostics(Result<PathBuf, String>),
    State(serde_json::Value),
    Frequency(Result<crate::FrequencyWindow, String>),
    Control(ControlRequest, Result<String, String>),
}

pub(super) enum ControlRequest {
    Profile(i32),
    Power(victus_core::PowerPolicy),
    Frequency(i32, i32),
    Undervolt(i32, i32),
}

pub(super) struct SensorUpdate {
    pub(super) snapshot: SensorSnapshot,
    pub(super) keys: Vec<String>,
    pub(super) frequency: Option<Result<crate::FrequencyWindow, String>>,
}

#[derive(Clone)]
pub(super) struct EventSender {
    pub(super) tx: mpsc::SyncSender<BackgroundEvent>,
    pub(super) wake: Arc<UnixStream>,
}

impl EventSender {
    pub(super) fn send(&self, event: BackgroundEvent) {
        // Producers run on background threads. Never discard a completion:
        // doing so could leave its UI controls permanently marked busy.
        if self.tx.send(event).is_ok() { let _ = (&*self.wake).write(&[1]); }
    }
}

pub(super) struct Session {
    pub(super) model: Rc<RefCell<Model>>,
    pub(super) suppress: Cell<bool>,
    pub(super) page: Arc<AtomicUsize>,
    pub(super) visible: Arc<AtomicBool>,
    pub(super) stop: Arc<AtomicBool>,
    pub(super) window: gtk4::ApplicationWindow,
    pub(super) built: pages::Built,
    pub(super) accent: gtk4::CssProvider,
    pub(super) hover: Cell<Option<usize>>,
    pub(super) nav_position: Cell<Option<f64>>,
    pub(super) nav_slide: Cell<Option<(f64, Instant)>>,
    pub(super) nav_ticking: Cell<bool>,
    pub(super) anim: Cell<f64>,
    pub(super) light_at: Cell<Option<Instant>>,
    pub(super) fan_at: Cell<Option<Instant>>,
    pub(super) curve_cpu: Cell<bool>,
    pub(super) drag: Cell<Option<usize>>,
    pub(super) capturing: Cell<bool>,
    pub(super) zone_target: Cell<i32>,
    pub(super) strip_hue: Cell<f64>,
    pub(super) applied_power: RefCell<PowerPolicy>,
    pub(super) applied_freq: Cell<Option<(i32, i32)>>,
    pub(super) last_frequency: RefCell<Option<crate::FrequencyWindow>>,
    pub(super) applied_uv: Cell<Option<(i32, i32)>>,
    pub(super) profile_busy: Cell<bool>,
    pub(super) captured_mods: RefCell<Vec<i32>>,
    pub(super) selected_point: Cell<Option<usize>>,
    pub(super) curve_selections: Cell<[Option<usize>; 2]>,
    pub(super) fan_hover: Cell<Option<usize>>,
    pub(super) self_weak: RefCell<Weak<Session>>,
    pub(super) timer: RefCell<Option<glib::SourceId>>,
    pub(super) timer_due: Cell<Option<Instant>>,
    pub(super) state_instance: RefCell<String>,
    pub(super) state_revision: Cell<u64>,
    pub(super) accent_profile: Cell<Option<i32>>,
    pub(super) wake: Arc<UnixStream>,
    pub(super) sensor_signal: Arc<(Mutex<u64>, Condvar)>,
    pub(super) stats: RefCell<HashMap<String, Running>>,
    pub(super) log: RefCell<VecDeque<String>>,
    pub(super) sensor_rows: RefCell<Vec<SensorRow>>,
    pub(super) extra_sig: RefCell<String>,
    pub(super) charts: Rc<graph::Charts>,
    pub(super) tray: RefCell<Option<tray::Tray>>,
    pub(super) sensor_menu: RefCell<Option<gtk4::Popover>>,
    pub(super) preview_at: Cell<Instant>,
    pub(super) preview_step: Cell<u64>,
    pub(super) sensor_rx: RefCell<Option<mpsc::Receiver<SensorUpdate>>>,
    pub(super) events_tx: EventSender,
    pub(super) events_rx: RefCell<mpsc::Receiver<BackgroundEvent>>,
}

