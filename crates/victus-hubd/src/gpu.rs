//! One demand-driven NVIDIA session, sampled without the runtime/hardware lock.

use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use victus_hw::NvidiaFields;

use crate::platform::Platform;
use crate::system::SysPlatform;

#[derive(Default)]
struct Demand {
    fan: bool,
    disabled: bool,
    suspended: bool,
    display: u8,
    expires: Option<Instant>,
    stopped: bool,
    revision: u64,
}

pub struct GpuMonitor {
    demand: Mutex<Demand>,
    wake: Condvar,
    sample: Mutex<Option<(NvidiaFields, Instant)>>,
}

impl GpuMonitor {
    pub fn start() -> Arc<Self> {
        let monitor = Arc::new(Self { demand: Mutex::new(Demand::default()), wake: Condvar::new(), sample: Mutex::new(None) });
        let worker = Arc::clone(&monitor);
        std::thread::spawn(move || worker.run());
        monitor
    }

    pub fn policy(&self, fan: bool, disabled: bool, suspended: bool) {
        let mut demand = self.demand.lock().expect("GPU demand");
        if (demand.fan, demand.disabled, demand.suspended) != (fan, disabled, suspended) {
            demand.fan = fan;
            demand.disabled = disabled;
            demand.suspended = suspended;
            demand.revision = demand.revision.wrapping_add(1);
            self.wake.notify_one();
        }
    }

    pub fn display(&self, keys: &[String]) {
        let mut fields = 0;
        for key in keys {
            fields |= match key.as_str() { "gpu-temp" => 1, "gpu-power" => 2, "gpu-usage" => 4, _ => 0 };
        }
        let mut demand = self.demand.lock().expect("GPU demand");
        let changed = demand.display != fields || (fields != 0 && demand.expires.is_none_or(|until| until <= Instant::now()));
        demand.display = fields;
        demand.expires = (fields != 0).then(|| Instant::now() + Duration::from_secs(3));
        if changed { demand.revision = demand.revision.wrapping_add(1); self.wake.notify_one(); }
    }

    pub fn snapshot(&self) -> NvidiaFields {
        let demand = self.demand.lock().expect("GPU demand");
        if demand.disabled || demand.suspended { return NvidiaFields::default(); }
        drop(demand);
        self.sample.lock().expect("GPU sample").as_ref()
            .filter(|(_, at)| at.elapsed() <= Duration::from_secs(3))
            .map(|(fields, _)| fields.clone()).unwrap_or_default()
    }

    pub fn stop(&self) {
        self.demand.lock().expect("GPU demand").stopped = true;
        self.wake.notify_one();
    }

    fn run(&self) {
        let mut platform = SysPlatform::installed();
        loop {
            let mut demand = self.demand.lock().expect("GPU demand");
            if demand.stopped { break; }
            if demand.expires.is_some_and(|until| until <= Instant::now()) { demand.display = 0; }
            let fields = if demand.disabled || demand.suspended { 0 } else { demand.display | u8::from(demand.fan) };
            if fields == 0 {
                let revision = demand.revision;
                drop(demand);
                platform.release_gpu();
                *self.sample.lock().expect("GPU sample") = None;
                let demand = self.demand.lock().expect("GPU demand");
                if demand.stopped { break; }
                if demand.revision != revision { continue; }
                drop(self.wake.wait(demand).expect("GPU wake"));
                continue;
            }
            drop(demand);
            let sample = platform.nvidia_fields(fields & 1 != 0, fields & 2 != 0, fields & 4 != 0, false);
            *self.sample.lock().expect("GPU sample") = Some((sample, Instant::now()));
            let demand = self.demand.lock().expect("GPU demand");
            if demand.stopped { break; }
            drop(self.wake.wait_timeout(demand, Duration::from_secs(1)).expect("GPU wake"));
        }
    }
}
