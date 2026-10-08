use serde_json::Value;
use std::sync::{Arc, Mutex};
use victus_core::{
    config_from_value, format_sensors_response, format_status, lighting_from_value, match_request, parse_ints,
    parse_json_object, power_from_value, unknown_sensor_keys, validate_frequency, validate_intel_power,
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
        "initialize-state\t" => {
            let value = parse_json_object(body, "initialize-state")?;
            let state = victus_core::state_from_value(&value);
            if state.power.enabled {
                if runtime.platform.intel_cpu() { validate_intel_power(state.power.slow_limit, state.power.fast_limit)?; }
                else { validate_ryzenadj(state.power.stapm_limit, state.power.fast_limit, state.power.slow_limit, state.power.tctl_temp)?; }
            }
            Ok(ok(&runtime.initialize_state(state)?))
        }
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

/// Slow production I/O uses separate platforms; fan and lighting writes remain
/// serialized by the runtime lock. Tests can still use the direct dispatcher.
pub fn dispatch_with_workers<P: Platform>(
    runtime: &Arc<Mutex<Runtime<P>>>, sampler: &Arc<Mutex<P>>, commands: &Arc<Mutex<P>>, peer: &Peer, request: &str,
) -> String {
    if !peer.authorized { return format_status(false, "access denied: session is no longer active and unlocked"); }
    let result = (|| -> HubResult<Option<String>> {
        let Some((prefix, body)) = match_request(request.trim_end_matches(['\n', '\r'])) else { return Ok(None) };
        match prefix {
            "sensors" => {
                let keys: Vec<String> = body.trim().trim_matches('\t').split(',').map(str::trim)
                    .filter(|key| !key.is_empty()).map(str::to_owned).collect();
                let unknown = unknown_sensor_keys(&keys);
                if !unknown.is_empty() { return Err(HubError::new(format!("unknown sensors: {}", unknown.join(", ")))); }
                let disable = runtime.lock().expect("runtime lock").nvidia_queries_disabled();
                let snap = sampler.lock().expect("sensor worker").sensors(&keys, disable);
                Ok(Some(format_sensors_response(&snap)))
            }
            "cpu-power" => Ok(Some(sampler.lock().expect("sensor worker").cpu_power().format_line())),
            "set-profile\t" => {
                let index = one_int(body, "profile")?.clamp(0, 2);
                let mut worker = commands.try_lock().map_err(|_| HubError::new("hardware command already pending; try again when it finishes"))?;
                let message = worker.apply_profile(index)?;
                runtime.lock().expect("runtime lock").complete_profile(index);
                Ok(Some(ok(&message)))
            }
            "power-config\t" => {
                let policy = power_from_value(Some(&parse_json_object(body, "power-config")?));
                let mut worker = commands.lock().expect("command worker");
                if policy.enabled { apply_power_policy(&mut *worker, &policy)?; }
                let message = runtime.lock().expect("runtime lock").set_power(policy);
                Ok(Some(ok(&message)))
            }
            "cpu-frequency-config" => {
                let body = body.trim_start_matches('\t').trim();
                let limits = if body.is_empty() { None } else {
                    let values = parse_ints(body, 2, "expected minimum and maximum in kHz")?;
                    validate_frequency(values[0], values[1])?;
                    Some((values[0], values[1]))
                };
                let mut worker = commands.lock().expect("command worker");
                if let Some((minimum, maximum)) = limits { worker.cpu_frequency(minimum, maximum)?; }
                runtime.lock().expect("runtime lock").remember_cpu_frequency(limits);
                Ok(Some(ok("cpu-frequency-config")))
            }
            "power-limits\t" | "intel-power-limits\t" | "intel-undervolt\t" | "cpu-frequency-limits\t" => {
                let mut worker = commands.lock().expect("command worker");
                let count = if prefix == "power-limits\t" { 4 } else { 2 };
                let values = parse_ints(body, count, "invalid hardware arguments")?;
                let message = match prefix {
                    "power-limits\t" => { validate_ryzenadj(values[0], values[1], values[2], values[3])?; worker.ryzenadj(values[0], values[1], values[2], values[3])? }
                    "intel-power-limits\t" => { validate_intel_power(values[0], values[1])?; worker.intel_power(values[0], values[1])? }
                    "intel-undervolt\t" => { validate_undervolt(values[0], values[1])?; worker.intel_undervolt(values[0], values[1])? }
                    _ => { validate_frequency(values[0], values[1])?; worker.cpu_frequency(values[0], values[1])? }
                };
                Ok(Some(ok(&message)))
            }
            _ => Ok(None),
        }
    })();
    match result {
        Ok(Some(line)) => line,
        Ok(None) => dispatch(&mut *runtime.lock().expect("runtime lock"), peer, request),
        Err(error) => format_status(false, &error.to_string()),
    }
}

pub fn apply_power_policy<P: Platform>(platform: &mut P, policy: &victus_core::PowerPolicy) -> HubResult<String> {
    if platform.intel_cpu() {
        validate_intel_power(policy.slow_limit, policy.fast_limit)?;
        platform.intel_power(policy.slow_limit, policy.fast_limit)
    } else {
        validate_ryzenadj(policy.stapm_limit, policy.fast_limit, policy.slow_limit, policy.tctl_temp)?;
        platform.ryzenadj(policy.stapm_limit, policy.fast_limit, policy.slow_limit, policy.tctl_temp)
    }
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
    let mut value = runtime.state_value();
    if let Some(object) = value.as_object_mut() {
        object.insert("capabilities".to_owned(), runtime.platform.capabilities());
    }
    format!("OK\t{value}\n")
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hubd/dispatch.rs"]
mod tests;
