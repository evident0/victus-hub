//! Persistent, serialized libsensors access. It applies sensors.conf compute,
//! label and ignore rules without starting `sensors -j` for every sample.

use std::ffi::{CStr, c_char, c_int, c_uint, c_void};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Instant, SystemTime, Duration};

use libloading::Library;
use victus_core::{ExtraSensor, SensorReading};

#[repr(C)]
struct Feature {
    name: *mut c_char,
    number: c_int,
    kind: c_int,
    first_subfeature: c_int,
    padding: c_int,
}

#[repr(C)]
struct Subfeature {
    name: *mut c_char,
    number: c_int,
    kind: c_int,
    mapping: c_int,
    flags: c_uint,
}

type Chips = unsafe extern "C" fn(*const c_void, *mut c_int) -> *const c_void;
type Features = unsafe extern "C" fn(*const c_void, *mut c_int) -> *const Feature;
type Sub = unsafe extern "C" fn(*const c_void, *const Feature, c_int) -> *const Subfeature;
type Label = unsafe extern "C" fn(*const c_void, *const Feature) -> *mut c_char;
type Value = unsafe extern "C" fn(*const c_void, c_int, *mut f64) -> c_int;
type ChipName = unsafe extern "C" fn(*mut c_char, usize, *const c_void) -> c_int;

struct Sensors {
    _library: Library,
    init: unsafe extern "C" fn(*mut c_void) -> c_int,
    cleanup: unsafe extern "C" fn(),
    chips: Chips,
    features: Features,
    sub: Sub,
    label: Label,
    value: Value,
    chip_name: ChipName,
    initialized: bool,
    fingerprint: Vec<(PathBuf, Option<SystemTime>)>,
    checked: Option<Instant>,
}

impl Sensors {
    fn load() -> Option<Self> {
        // SAFETY: These signatures/layouts are the stable libsensors.so.5 ABI
        // from sensors.h. Function pointers cannot outlive the owned library.
        unsafe {
            let library = Library::new("libsensors.so.5").ok()?;
            Some(Self {
                init: *library.get(b"sensors_init\0").ok()?,
                cleanup: *library.get(b"sensors_cleanup\0").ok()?,
                chips: *library.get(b"sensors_get_detected_chips\0").ok()?,
                features: *library.get(b"sensors_get_features\0").ok()?,
                sub: *library.get(b"sensors_get_subfeature\0").ok()?,
                label: *library.get(b"sensors_get_label\0").ok()?,
                value: *library.get(b"sensors_get_value\0").ok()?,
                chip_name: *library.get(b"sensors_snprintf_chip_name\0").ok()?,
                _library: library,
                initialized: false,
                fingerprint: Vec::new(),
                checked: None,
            })
        }
    }

    fn refresh(&mut self) {
        if self.checked.is_some_and(|at| at.elapsed() < Duration::from_secs(30)) { return; }
        self.checked = Some(Instant::now());
        let mut paths = crate::sysfs::hwmon_dirs(Path::new("/sys/class/hwmon")).into_iter()
            .map(|path| std::fs::canonicalize(&path).unwrap_or(path)).collect::<Vec<_>>();
        paths.push(PathBuf::from("/etc/sensors3.conf"));
        paths.push(PathBuf::from("/etc/sensors.conf"));
        paths.push(PathBuf::from("/etc/sensors.d"));
        if let Ok(entries) = std::fs::read_dir("/etc/sensors.d") { paths.extend(entries.flatten().map(|entry| entry.path())); }
        paths.sort();
        let fingerprint = paths.into_iter().map(|path| {
            let modified = std::fs::metadata(&path).ok().and_then(|meta| meta.modified().ok());
            (path, modified)
        }).collect::<Vec<_>>();
        if self.initialized && fingerprint == self.fingerprint { return; }
        self.fingerprint = fingerprint;
        // SAFETY: The global mutex excludes all concurrent libsensors calls.
        // No pointers returned by an earlier enumeration survive this call.
        unsafe {
            if self.initialized { (self.cleanup)(); }
            self.initialized = (self.init)(std::ptr::null_mut()) == 0;
            if !self.initialized { (self.cleanup)(); }
        }
    }

    fn read(&mut self, skip_nvidia: bool) -> Option<Vec<ExtraSensor>> {
        self.refresh();
        let mut output = Vec::new();
        if !self.initialized { return None; }
        // SAFETY: All borrowed chip/feature/subfeature pointers are checked
        // for null and used only while the initialized library is locked.
        // Labels are malloc-owned and freed once after copying their contents.
        unsafe {
            let mut chip_index = 0;
            loop {
                let chip = (self.chips)(std::ptr::null(), &mut chip_index);
                if chip.is_null() { break; }
                let mut name = [0 as c_char; 256];
                let size = (self.chip_name)(name.as_mut_ptr(), name.len(), chip);
                if size < 0 || size as usize >= name.len() { continue; }
                let chip_name = CStr::from_ptr(name.as_ptr()).to_string_lossy().into_owned();
                if (skip_nvidia && chip_name.starts_with("nvidia-")) || chip_name.starts_with("hp-isa-")
                    || chip_name.starts_with("k10temp-") { continue; }
                let mut feature_index = 0;
                loop {
                    // Enumeration applies the configuration's ignore rules.
                    let feature = (self.features)(chip, &mut feature_index);
                    if feature.is_null() { break; }
                    let (unit, maximum, metric, input, average) = match (*feature).kind {
                        0 => ("V", 20.0, "Voltage", 0x000, 0x005),
                        1 => ("RPM", 6000.0, "Fan", 0x100, 0x100),
                        2 => ("°C", 100.0, "Temp", 0x200, 0x200),
                        3 => ("W", 120.0, "Power", 0x303, 0x300),
                        5 => ("A", 10.0, "Current", 0x500, 0x505),
                        _ => continue,
                    };
                    if (*feature).name.is_null() { continue; }
                    let feature_name = CStr::from_ptr((*feature).name).to_string_lossy().into_owned();
                    let label = (self.label)(chip, feature);
                    if label.is_null() { continue; }
                    let label_text = CStr::from_ptr(label).to_string_lossy().into_owned();
                    nix::libc::free(label.cast());
                    if (chip_name.starts_with("amdgpu-") && matches!(label_text.as_str(), "edge" | "PPT"))
                        || (chip_name.starts_with("BAT") && unit == "W") { continue; }
                    let mut sub = (self.sub)(chip, feature, input);
                    if sub.is_null() { sub = (self.sub)(chip, feature, average); }
                    if sub.is_null() || (*sub).flags & 1 == 0 { continue; }
                    let mut value = 0.0;
                    if (self.value)(chip, (*sub).number, &mut value) != 0 || !value.is_finite()
                        || (unit == "°C" && value <= -100.0) { continue; }
                    let slug = |text: &str| text.chars().map(|ch| if ch.is_ascii_alphanumeric() { ch.to_ascii_lowercase() } else { '-' }).collect::<String>();
                    output.push(ExtraSensor {
                        key: format!("lm-{}-{}", slug(&chip_name), slug(&feature_name)),
                        group: super::sensors::lm_group(&chip_name).into(),
                        name: format!("{label_text} {metric}"),
                        unit: unit.into(),
                        value_min: 0.0,
                        value_max: value.max(maximum),
                        numeric_value: value,
                        reading: SensorReading::with_source(super::sensors::format_sensor(value, unit), format!("sensors: {chip_name} / {feature_name}")),
                    });
                }
            }
        }
        output.sort_by(|left, right| (&left.group, &left.key).cmp(&(&right.group, &right.key)));
        Some(output)
    }
}

pub(crate) fn read(skip_nvidia: bool) -> Option<Vec<ExtraSensor>> {
    static SENSORS: OnceLock<Mutex<Option<Sensors>>> = OnceLock::new();
    let mut sensors = SENSORS.get_or_init(|| Mutex::new(Sensors::load())).lock().ok()?;
    sensors.as_mut()?.read(skip_nvidia)
}
