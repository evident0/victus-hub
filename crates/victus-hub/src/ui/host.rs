use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::Duration;

use victus_core::{parse_sensors_response, request_key_for_graph, transact};

use super::session::{BackgroundEvent, SensorUpdate, Session};
use crate::{connects_to_socket, Model};

pub(super) fn spawn_sensor_thread(
    model: &Model,
    page: Arc<AtomicUsize>,
    visible: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    graph_keys: Arc<Mutex<Vec<String>>>,
    wake: Arc<UnixStream>,
    signal: Arc<(Mutex<u64>, Condvar)>,
) -> Option<mpsc::Receiver<SensorUpdate>> {
    let socket = connects_to_socket(model)?.to_path_buf();
    let (tx, rx) = mpsc::sync_channel(2);
    let _ = std::thread::Builder::new().name("victus-sensors".into()).spawn(move || {
        let mut had_requests = false;
        while !stop.load(Ordering::Relaxed) {
            let gate = signal.0.lock().expect("sensor wake");
            let generation = *gate;
            let active = visible.load(Ordering::Relaxed);
            drop(gate);
            let mut requested = false;
            if active {
                let index = page.load(Ordering::Relaxed);
                let extra = graph_keys.lock().map(|keys| keys.clone()).unwrap_or_default();
                if let Some(request) = sensor_request_with(index, &extra) {
                    requested = true;
                    had_requests = true;
                    if let Ok(response) = transact(&socket, &request, Duration::from_secs(2)) {
                        if let Ok(snapshot) = parse_sensors_response(&response) {
                            let keys = request.split_once('\t').map(|(_, body)| body.split(',').map(str::to_owned).collect()).unwrap_or_default();
                            let frequency = (index == 1).then(|| read_frequency_window());
                            if tx.try_send(SensorUpdate { snapshot, keys, frequency }).is_ok() { let _ = (&*wake).write(&[1]); }
                        }
                    }
                } else if had_requests {
                    let _ = transact(&socket, "sensors", Duration::from_secs(1));
                    had_requests = false;
                }
            } else if had_requests {
                let _ = transact(&socket, "sensors", Duration::from_secs(1));
                had_requests = false;
            }
            let gate = signal.0.lock().expect("sensor wake");
            if stop.load(Ordering::Relaxed) { break; }
            if *gate != generation { continue; }
            if active && requested && visible.load(Ordering::Relaxed) {
                drop(signal.1.wait_timeout(gate, Duration::from_secs(1)).expect("sensor wake"));
            } else {
                drop(signal.1.wait(gate).expect("sensor wake"));
            }
        }
    });
    Some(rx)
}

pub(super) fn sensor_request_with(page: usize, extra: &[String]) -> Option<String> {
    let mut keys: Vec<String> = victus_core::keys_for_page(page).into_iter().map(str::to_owned).collect();
    for key in extra {
        let mapped = request_key_for_graph(key);
        if !keys.iter().any(|item| item == mapped) {
            keys.push(mapped.to_owned());
        }
    }
    if keys.is_empty() { None } else { Some(format!("sensors\t{}", keys.join(","))) }
}

pub(super) fn notify_sensors(session: &Session) { let mut guard = session.sensor_signal.0.lock().expect("sensor wake"); *guard = guard.wrapping_add(1); session.sensor_signal.1.notify_all(); }

pub(super) fn spawn_state_stream(session: &Session) {
    let Some(socket) = connects_to_socket(&session.model.borrow()).map(Path::to_path_buf) else { return };
    let tx = session.events_tx.clone();
    let stop = Arc::clone(&session.stop);
    std::thread::spawn(move || {
        let mut retry = 2;
        while !stop.load(Ordering::Relaxed) {
            if let Ok(mut stream) = UnixStream::connect(&socket) {
                stream.set_read_timeout(Some(Duration::from_secs(1))).ok();
                stream.set_write_timeout(Some(Duration::from_secs(1))).ok();
                if stream.write_all(b"shortcut-events\n").is_ok() {
                    let mut reader = BufReader::new(stream);
                    let mut line = String::new();
                    while !stop.load(Ordering::Relaxed) {
                        match reader.read_line(&mut line) {
                            Ok(0) => break,
                            Ok(_) => {
                                if line.len() > 65_536 { break; }
                                if line.trim() == "OK\tshortcut-events" { retry = 2; }
                                if let Some(body) = line.strip_prefix("STATE\t") {
                                    if let Ok(value) = serde_json::from_str(body.trim()) { tx.send(BackgroundEvent::State(value)); }
                                } else if line.starts_with("ERR\t") { break; }
                                line.clear();
                            }
                            Err(error) if matches!(error.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {},
                            Err(_) => break,
                        }
                    }
                }
            }
            std::thread::sleep(Duration::from_secs(retry));
            retry = (retry * 2).min(30);
        }
    });
}

pub fn frequency_window(policies: &[victus_hw::FrequencyPolicy]) -> Result<crate::FrequencyWindow, String> {
    let first = policies.first().ok_or("CPU frequency control is unavailable")?;
    let lower = policies.iter().map(|policy| policy.hardware_min).max().unwrap_or(first.hardware_min);
    let upper = policies.iter().map(|policy| policy.hardware_max).min().unwrap_or(first.hardware_max);
    if lower > upper { return Err("CPU policies have no common frequency range".into()); }
    let minimum = policies.iter().map(|policy| policy.minimum).max().unwrap_or(lower).clamp(lower, upper);
    let maximum = policies.iter().map(|policy| policy.maximum).min().unwrap_or(upper).clamp(minimum, upper);
    Ok(crate::FrequencyWindow { lower, upper, minimum, maximum, policies: policies.len(), mixed: policies.iter().any(|policy| (policy.minimum, policy.maximum) != (first.minimum, first.maximum)) })
}

pub(super) fn refresh_frequency(session: &Session) {
    let tx = session.events_tx.clone();
    std::thread::spawn(move || {
        let result = read_frequency_window();
        tx.send(BackgroundEvent::Frequency(result));
    });
}

pub(super) fn read_frequency_window() -> Result<crate::FrequencyWindow, String> {
    victus_hw::read_policies(Path::new("/sys/devices/system/cpu/cpufreq"))
        .map_err(|error| error.to_string()).and_then(|policies| frequency_window(&policies))
}

pub fn gpu_name() -> String {
    let Ok(entries) = std::fs::read_dir("/proc/driver/nvidia/gpus") else { return String::new() };
    for entry in entries.flatten() {
        if let Ok(text) = std::fs::read_to_string(entry.path().join("information")) {
            if let Some(name) = text.lines().find_map(|line| line.strip_prefix("Model:")) {
                return name.trim().trim_start_matches("NVIDIA GeForce ").to_owned();
            }
        }
    }
    String::new()
}

