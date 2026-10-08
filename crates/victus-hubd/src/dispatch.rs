use serde_json::Value;
use victus_core::{
    config_from_value, format_sensors_response, format_status, lighting_from_value, match_request, parse_ints,
    parse_json_object, power_from_value, state_to_value, unknown_sensor_keys, validate_frequency, validate_intel_power,
    validate_ryzenadj, validate_shortcut, validate_undervolt, HubError, HubResult, SensorSnapshot,
};

use crate::auth::Peer;
use crate::platform::Platform;
use crate::runtime::Runtime;

pub fn dispatch<P: Platform>(runtime: &mut Runtime<P>, peer: &Peer, request: &str) -> String {
    if !peer.authorized {
        return format_status(false, "access denied: session is no longer active and unlocked");
    }
    let request = request.trim_end_matches(['\n', '\r']);
    if matches!(request, "prepare-sleep" | "resume") && peer.uid != 0 {
        return format_status(false, "access denied: sleep hooks require root");
    }
    let Some((prefix, body)) = match_request(request) else {
        return format_status(false, "unsupported request");
    };
    match handle(runtime, peer, prefix, body) {
        Ok(line) => line,
        Err(error) => format_status(false, &error.to_string()),
    }
}

fn handle<P: Platform>(runtime: &mut Runtime<P>, peer: &Peer, prefix: &str, body: &str) -> HubResult<String> {
    match prefix {
        "cpu-power" => Ok(runtime.platform.cpu_power().format_line()),
        "gpu-mux-mode\t" => {
            let mode = one_int(body, "gpu-mux-mode")?;
            Ok(ok(&runtime.platform.gpu_mux(mode)?))
        }
        "fan-auto" => Ok(ok(&runtime.platform.pwm_enable(2)?)),
        "fan-max" => Ok(ok(&runtime.platform.pwm_max()?)),
        "fan-manual" => Ok(ok(&runtime.platform.pwm_enable(1)?)),
        "fan-pwm\t" => {
            let pwm = one_int(body, "pwm")?;
            Ok(ok(&runtime.platform.pwm(pwm)?))
        }
        "keyboard-color\t" => keyboard_color(runtime, body),
        "keyboard-brightness\t" => {
            let level = one_int(body, "brightness")?;
            Ok(ok(&runtime.platform.keyboard_brightness(level)?))
        }
        "keyboard-user-brightness\t" => {
            let level = one_int(body, "user-brightness")?;
            Ok(ok(&runtime.platform.keyboard_user_brightness(level)?))
        }
        "power-limits\t" => {
            let values = parse_ints(body, 4, "expected 4 integers (stapm, fast, slow, tctl)")?;
            validate_ryzenadj(values[0], values[1], values[2], values[3])?;
            Ok(ok(&runtime.platform.ryzenadj(values[0], values[1], values[2], values[3])?))
        }
        "intel-power-limits\t" => {
            let values = parse_ints(body, 2, "expected 2 integers (PL1, PL2 in mW)")?;
            validate_intel_power(values[0], values[1])?;
            Ok(ok(&runtime.platform.intel_power(values[0], values[1])?))
        }
        "intel-undervolt\t" => {
            let values = parse_ints(body, 2, "expected 2 integers (core, cache in mV)")?;
            validate_undervolt(values[0], values[1])?;
            Ok(ok(&runtime.platform.intel_undervolt(values[0], values[1])?))
        }
        "cpu-frequency-limits\t" => {
            let values = parse_ints(body, 2, "expected 2 integers (minimum, maximum in kHz)")?;
            validate_frequency(values[0], values[1])?;
            Ok(ok(&runtime.platform.cpu_frequency(values[0], values[1])?))
        }
        "keyboard-last-input" => Ok(format!("OK\t{:.3}\n", runtime.platform.idle_elapsed().max(0.0))),
        "fan-config\t" => {
            let value = parse_json_object(body, "fan-config")?;
            Ok(ok(&runtime.set_fan_config(config_from_value(&value))))
        }
        "lighting-config\t" => {
            let value = parse_json_object(body, "lighting-config")?;
            Ok(ok(&runtime.set_lighting(lighting_from_value(&value))))
        }
        "power-config\t" => {
            let value = parse_json_object(body, "power-config")?;
            Ok(ok(&runtime.set_power(power_from_value(Some(&value)))))
        }
        "cpu-frequency-config" => cpu_frequency_config(runtime, body),
        "battery-power-save\t" => Ok(ok(&runtime.set_battery_power_save(one_int(body, "battery-power-save")? != 0))),
        "hardware-shortcuts\t" => Ok(ok(&runtime.set_hardware_shortcuts(one_int(body, "hardware-shortcuts")? != 0))),
        "program-shortcut\t" => program_shortcut(runtime, peer, body),
        "disable-nvidia-queries\t" => Ok(ok(&runtime.set_disable_nvidia_queries(one_int(body, "disable-nvidia-queries")? != 0))),
        "set-profile\t" => Ok(ok(&runtime.set_profile(one_int(body, "profile")?)?)),
        "sensors" => sensors(runtime, body),
        "get-state" => Ok(state_line(runtime)),
        "prepare-sleep" => {
            runtime.prepare_sleep();
            Ok(ok("prepare-sleep"))
        }
        "resume" => {
            runtime.resume();
            Ok(ok("resume"))
        }
        _ => Err(HubError::new("unsupported request")),
    }
}

fn ok(message: &str) -> String {
    format_status(true, message)
}

fn one_int(body: &str, name: &str) -> HubResult<i32> {
    let values = parse_ints(body, 1, &format!("expected an integer for {name}"))?;
    values.first().copied().ok_or_else(|| HubError::new(format!("expected an integer for {name}")))
}

fn keyboard_color<P: Platform>(runtime: &mut Runtime<P>, body: &str) -> HubResult<String> {
    let parts: Vec<&str> = body.split('\t').filter(|part| !part.is_empty()).collect();
    if parts.len() == 3 {
        let values = parse_ints(&parts.join("\t"), 3, "expected 3 integers (r g b) or 4 (zone r g b)")?;
        let message = runtime.platform.keyboard_color(None, byte(values[0])?, byte(values[1])?, byte(values[2])?)?;
        return Ok(ok(&message));
    }
    if parts.len() == 4 {
        let values = parse_ints(&parts.join("\t"), 4, "expected 3 integers (r g b) or 4 (zone r g b)")?;
        let message = runtime.platform.keyboard_color(Some(values[0]), byte(values[1])?, byte(values[2])?, byte(values[3])?)?;
        return Ok(ok(&message));
    }
    Err(HubError::new("expected 3 integers (r g b) or 4 (zone r g b)"))
}

fn byte(value: i32) -> HubResult<u8> {
    u8::try_from(value).map_err(|_| HubError::new("color channel out of range"))
}

fn cpu_frequency_config<P: Platform>(runtime: &mut Runtime<P>, body: &str) -> HubResult<String> {
    let body = body.trim_start_matches('\t').trim();
    if body.is_empty() {
        return Ok(ok(&runtime.set_cpu_frequency(None)?));
    }
    let values = parse_ints(body, 2, "expected 2 integers (minimum, maximum in kHz)")?;
    if !(0 < values[0] && values[0] <= values[1]) {
        return Err(HubError::new("invalid frequency range"));
    }
    Ok(ok(&runtime.set_cpu_frequency(Some((values[0], values[1])))?))
}

fn program_shortcut<P: Platform>(runtime: &mut Runtime<P>, peer: &Peer, body: &str) -> HubResult<String> {
    if peer.uid == 0 {
        return Err(HubError::new("a desktop user is required"));
    }
    let value = parse_json_object(body, "program-shortcut")?;
    let mods = value.get("mods").and_then(Value::as_array).map(|items| {
        items.iter().filter_map(Value::as_i64).map(|item| item as i32).collect::<Vec<_>>()
    });
    let key = value.get("key").and_then(Value::as_i64).map(|item| item as i32);
    let (Some(mods), Some(key)) = (mods, key) else {
        return Err(HubError::new("program-shortcut must be a JSON object"));
    };
    let (mods, key) = validate_shortcut(&mods, key)?;
    runtime.shortcuts.set(peer.uid, &mods, key)?;
    Ok(ok("program-shortcut"))
}

fn sensors<P: Platform>(runtime: &mut Runtime<P>, body: &str) -> HubResult<String> {
    let raw = body.trim().trim_matches('\t');
    let keys: Vec<String> = if raw.is_empty() {
        Vec::new()
    } else {
        raw.split(',').map(str::trim).filter(|key| !key.is_empty()).map(str::to_owned).collect()
    };
    let unknown = unknown_sensor_keys(&keys);
    if !unknown.is_empty() {
        return Err(HubError::new(format!("unknown sensors: {}", unknown.join(", "))));
    }
    if keys.is_empty() {
        if !runtime.fan_needs_gpu_temp() {
            runtime.platform.release_gpu();
        }
        return Ok(format_sensors_response(&SensorSnapshot::default()));
    }
    let disable = runtime.nvidia_queries_disabled();
    let snap = runtime.platform.sensors(&keys, disable);
    if !keys.iter().any(|key| matches!(key.as_str(), "gpu-temp" | "gpu-usage" | "gpu-power")) && !runtime.fan_needs_gpu_temp() {
        runtime.platform.release_gpu();
    }
    Ok(format_sensors_response(&snap))
}

fn state_line<P: Platform>(runtime: &Runtime<P>) -> String {
    let state = runtime.snapshot();
    let mut value = state_to_value(&state);
    if let Some(object) = value.as_object_mut() {
        object.insert("capabilities".to_owned(), runtime.platform.capabilities());
    }
    format!("OK\t{value}\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::platform::FakePlatform;
    use crate::runtime::Runtime;
    use victus_core::{offline_scratch, FanConfig, FanPoint, KEY_LEFTCTRL};

    fn peer(uid: u32) -> Peer {
        Peer { uid, pid: 1, authorized: true }
    }

    fn runtime() -> (Runtime<FakePlatform>, std::path::PathBuf) {
        let dir = offline_scratch("dispatch");
        let runtime = Runtime::new(dir.join("state.json"), &dir.join("shortcuts.json"), FakePlatform::default());
        (runtime, dir)
    }

    #[test]
    fn policy_commands_do_not_touch_live_paths() {
        let (mut runtime, dir) = runtime();
        assert!(dir.starts_with(std::env::temp_dir()));
        let denied = dispatch(&mut runtime, &Peer { uid: 1000, pid: 1, authorized: false }, "get-state");
        assert!(denied.starts_with("ERR\taccess denied"));
        let state = dispatch(&mut runtime, &peer(0), "get-state");
        assert!(state.starts_with("OK\t"));
        assert!(state.contains("capabilities"));
        let bad = dispatch(&mut runtime, &peer(0), "power-limits\t500\t25000\t25000\t95");
        assert!(bad.contains("stapm-limit"));
        assert!(!runtime.platform.log.iter().any(|line| line.starts_with("ryzenadj")));
        let uv = dispatch(&mut runtime, &peer(0), "intel-undervolt\t-300\t0");
        assert!(uv.contains("-250"));
        assert!(!runtime.platform.opened_msr);
        let sleep = dispatch(&mut runtime, &peer(1000), "prepare-sleep");
        assert!(sleep.contains("sleep hooks require root"));
        let parked = dispatch(&mut runtime, &peer(0), "prepare-sleep");
        assert!(parked.starts_with("OK\t"));
        assert!(runtime.platform.log.iter().any(|line| line == "pwm-enable 2"));
        assert!(runtime.platform.log.iter().any(|line| line == "brightness 0"));
        let unknown = dispatch(&mut runtime, &peer(0), "sensors\tnot-a-sensor");
        assert!(unknown.contains("unknown sensors"));
        let empty = dispatch(&mut runtime, &peer(0), "sensors");
        assert!(empty.starts_with("OK\t"));
        assert_eq!(runtime.platform.sensor_reads, 0);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn custom_fan_poll_writes_pwm_inside_the_fake_platform() {
        let (mut runtime, dir) = runtime();
        let mut config = FanConfig::default();
        config.custom_enabled = true;
        config.manual_preset = None;
        for profile in &mut config.profiles {
            profile.cpu_points = vec![FanPoint { temp: 30, speed: 40 }, FanPoint { temp: 100, speed: 40 }];
            profile.gpu_points = vec![FanPoint { temp: 30, speed: 40 }, FanPoint { temp: 90, speed: 40 }];
        }
        let line = dispatch(&mut runtime, &peer(0), &format!("fan-config\t{}", victus_core::config_to_value(&config)));
        assert!(line.starts_with("OK\tfan-config"));
        runtime.fan_poll(1.0);
        assert!(runtime.platform.log.iter().any(|entry| entry == "pwm 102"), "{:?}", runtime.platform.log);
        let reads = runtime.platform.temp_reads;
        runtime.fan_poll(2.0);
        assert_eq!(runtime.platform.log.iter().filter(|entry| *entry == "pwm 102").count(), 1);
        assert!(runtime.platform.temp_reads > reads);
        runtime.platform.ac = Some(false);
        runtime.platform.profile = Some(1);
        dispatch(&mut runtime, &peer(0), "battery-power-save\t1");
        assert_eq!(runtime.platform.profile, Some(0));
        runtime.platform.ac = Some(true);
        runtime.refresh_host(false);
        assert_eq!(runtime.platform.profile, Some(1));
        let shortcut = format!(r#"program-shortcut\t{{"mods":[{KEY_LEFTCTRL}],"key":24}}"#);
        // The format above escaped the tab. Build the request directly.
        let _ = shortcut;
        let request = format!("program-shortcut\t{{\"mods\":[{KEY_LEFTCTRL}],\"key\":24}}");
        assert!(dispatch(&mut runtime, &peer(0), &request).starts_with("ERR\t"));
        assert!(dispatch(&mut runtime, &peer(1000), &request).starts_with("OK\t"));
        runtime.set_hardware_shortcuts(true);
        runtime.handle_key(&[29, 42], 103);
        assert!(!runtime.published.is_empty());
        let _ = std::fs::remove_dir_all(dir);
    }
}
