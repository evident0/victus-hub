//! Hardware helpers. Production callers pass real `/sys` paths. Tests pass a
//! directory created by [`victus_core::offline_scratch`] and never the live
//! sysfs tree.

#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc, clippy::must_use_candidate)]
#![allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, clippy::cast_precision_loss)]
#![allow(clippy::module_name_repetitions, clippy::doc_markdown, clippy::too_many_arguments)]

mod cpufreq;
mod lm;
mod nvidia;
mod rapl;
mod sensors;
mod sysfs;
mod undervolt;

pub use cpufreq::{apply_limits, read_policies, FrequencyPolicy};
pub use nvidia::{
    amd_or_nouveau_temp_c, note_smi_failures, nvidia_hwmon_temp_c, parse_smi_csv, read_smi_line, runtime_suspended,
    smi_due, Nvml, NvidiaFields, SmiBackoff,
};
pub use rapl::RaplSampler;
pub use sensors::{cpu_temp_c, dgpu_suspended, SensorSampler};
pub use undervolt::apply_undervolt;
pub use sysfs::{
    detect_capabilities, find_hwmon, keyboard_led_names, keyboard_lighting_supported, keyboard_zone_count,
    manual_fan_supported, read_gpu_mux, read_int, read_pwm_percent, read_text, write_gpu_mux, write_led_color,
    write_pwm, write_pwm_enable, write_sysfs, GpuMuxState, HardwareCapabilities,
};

use std::path::Path;
use std::io::Read;
use std::process::{Command, Stdio};
use std::os::unix::process::CommandExt;
use std::os::fd::{AsFd, AsRawFd};
use std::time::{Duration, Instant};

use victus_core::{ryzenadj_args, validate_ryzenadj, HubError, HubResult};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandOutput {
    pub status: i32,
    pub stdout: String,
    pub stderr: String,
}

pub fn run_command(program: &Path, args: &[String]) -> HubResult<CommandOutput> {
    run_command_timeout(program, args, Duration::from_secs(10))
}

/// Drain both pipes while waiting, and always reap timed-out children.
pub fn run_command_timeout(program: &Path, args: &[String], timeout: Duration) -> HubResult<CommandOutput> {
    run_prepared_command(Command::new(program).args(args), timeout)
}

pub fn run_prepared_command(command: &mut Command, timeout: Duration) -> HubResult<CommandOutput> {
    let program = command.get_program().to_string_lossy().into_owned();
    let mut child = command.process_group(0).stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().map_err(|error| HubError::new(format!("failed to run {program}: {error}")))?;
    let mut stdout_pipe = child.stdout.take().expect("piped stdout");
    let mut stderr_pipe = child.stderr.take().expect("piped stderr");
    let nonblocking = |fd| -> HubResult<()> {
        let flags = nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_GETFL).map_err(|error| HubError::new(error.to_string()))?;
        nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_SETFL(nix::fcntl::OFlag::from_bits_truncate(flags) | nix::fcntl::OFlag::O_NONBLOCK))
            .map_err(|error| HubError::new(error.to_string()))?;
        Ok(())
    };
    let deadline = Instant::now() + timeout;
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let result = (|| -> HubResult<std::process::ExitStatus> {
        nonblocking(stdout_pipe.as_raw_fd())?;
        nonblocking(stderr_pipe.as_raw_fd())?;
        let mut status = None;
        let mut out_done = false;
        let mut err_done = false;
        loop {
            if Instant::now() >= deadline { return Err(HubError::new(format!("{program} timed out"))); }
            if status.is_none() { status = child.try_wait().map_err(|error| HubError::new(format!("{program}: {error}")))?; }
            if !out_done { out_done = drain_available(&mut stdout_pipe, &mut stdout)?; }
            if !err_done { err_done = drain_available(&mut stderr_pipe, &mut stderr)?; }
            if let Some(status) = status {
                if out_done && err_done { return Ok(status); }
                // Close pipes inherited by descendants of an exited command.
                let _ = nix::sys::signal::killpg(nix::unistd::Pid::from_raw(child.id() as i32), nix::sys::signal::Signal::SIGKILL);
            }
            let mut fds = Vec::with_capacity(2);
            if !out_done { fds.push(nix::poll::PollFd::new(stdout_pipe.as_fd(), nix::poll::PollFlags::POLLIN)); }
            if !err_done { fds.push(nix::poll::PollFd::new(stderr_pipe.as_fd(), nix::poll::PollFlags::POLLIN)); }
            let wait = deadline.saturating_duration_since(Instant::now()).as_millis().clamp(1, if fds.is_empty() { 10 } else { 250 }) as u16;
            match nix::poll::poll(&mut fds, wait) {
                Ok(_) | Err(nix::errno::Errno::EINTR) => {},
                Err(error) => return Err(HubError::new(error.to_string())),
            }
        }
    })();
    if result.is_err() {
        let _ = nix::sys::signal::killpg(nix::unistd::Pid::from_raw(child.id() as i32), nix::sys::signal::Signal::SIGKILL);
        let _ = child.kill();
        let _ = child.wait();
    }
    let status = result?;
    Ok(CommandOutput {
        status: status.code().unwrap_or(1),
        stdout: String::from_utf8_lossy(&stdout).into_owned(),
        stderr: String::from_utf8_lossy(&stderr).into_owned(),
    })
}

fn drain_available(pipe: &mut impl Read, output: &mut Vec<u8>) -> HubResult<bool> {
    let mut buffer = [0_u8; 8192];
    // Bound each drain to give the other pipe and timeout checks a turn.
    for _ in 0..16 {
        match pipe.read(&mut buffer) {
            Ok(0) => return Ok(true),
            Ok(count) => {
                let remaining = (1024 * 1024_usize).saturating_sub(output.len());
                output.extend_from_slice(&buffer[..count.min(remaining)]);
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return Ok(false),
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(HubError::new(error.to_string())),
        }
    }
    Ok(false)
}

pub fn apply_ryzenadj(program: &Path, stapm: i32, fast: i32, slow: i32, tctl: i32) -> HubResult<String> {
    validate_ryzenadj(stapm, fast, slow, tctl)?;
    let output = run_command(program, &ryzenadj_args(stapm, fast, slow, tctl))?;
    if output.status == 0 {
        return Ok(format!("applied STAPM {stapm} mW, fast {fast} mW, slow {slow} mW, tctl {tctl} °C"));
    }
    let message = if !output.stderr.trim().is_empty() {
        output.stderr.trim().to_owned()
    } else if !output.stdout.trim().is_empty() {
        output.stdout.trim().to_owned()
    } else {
        format!("{} exited with {}", program.display(), output.status)
    };
    Err(HubError::new(message))
}

pub fn find_executable(explicit: Option<&Path>, candidates: &[PathBufRef<'_>]) -> Option<std::path::PathBuf> {
    if let Some(path) = explicit.filter(|path| path.is_file()) {
        return Some(path.to_path_buf());
    }
    candidates.iter().map(|PathBufRef(path)| path.to_path_buf()).find(|path| path.is_file())
}

pub struct PathBufRef<'a>(pub &'a Path);

pub fn intel_power_limits(root: &Path, pl1_mw: i32, pl2_mw: i32, intel: bool) -> HubResult<String> {
    if !intel {
        return Err(HubError::new("Intel CPU controls are unavailable on this processor"));
    }
    victus_core::validate_intel_power(pl1_mw, pl2_mw)?;
    let mut packages = Vec::new();
    if let Ok(entries) = std::fs::read_dir(root) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with("intel-rapl:") && name.matches(':').count() == 1 {
                packages.push(entry.path());
            }
        }
    }
    packages.sort();
    let mut writes = Vec::new();
    for package in &packages {
        let package_name = std::fs::read_to_string(package.join("name")).unwrap_or_default();
        if !package_name.trim().starts_with("package-") {
            continue;
        }
        let mut constraints = Vec::new();
        if let Ok(entries) = std::fs::read_dir(package) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if let Some(prefix) = name.strip_suffix("_name") {
                    if let Some(label) = crate::sysfs::read_text(&entry.path()) {
                        constraints.push((label, prefix.to_owned()));
                    }
                }
            }
        }
        for (label, value) in [("long_term", pl1_mw), ("short_term", pl2_mw)] {
            let prefix = constraints
                .iter()
                .find(|(name, _)| name == label)
                .map(|(_, prefix)| prefix.clone())
                .ok_or_else(|| HubError::new(format!("{} does not expose {label} power limits", package.file_name().unwrap_or_default().to_string_lossy())))?;
            let target = package.join(format!("{prefix}_power_limit_uw"));
            let _ = std::fs::read_to_string(&target).map_err(|error| HubError::new(format!("Could not apply Intel power limits: {error}. Some limits may have changed.")))?;
            for (bound, below) in [("min", true), ("max", false)] {
                let path = package.join(format!("{prefix}_{bound}_power_uw"));
                if path.exists() {
                    let limit = read_int(&path).ok_or_else(|| HubError::new(format!("Cannot read {}", path.display())))?;
                    let requested = i64::from(value) * 1000;
                    if limit > 0 && ((below && requested < limit) || (!below && requested > limit)) {
                        return Err(HubError::new(format!("{label} power exceeds {} hardware range", package.display())));
                    }
                }
            }
            writes.push((target, value * 1000));
        }
        let enabled = package.join("enabled");
        if enabled.exists() && crate::sysfs::read_text(&enabled).as_deref() != Some("1") {
            writes.push((enabled, 1));
        }
    }
    if writes.is_empty() {
        return Err(HubError::new("Intel RAPL package power limits are unavailable"));
    }
    for (path, value) in &writes {
        std::fs::write(path, value.to_string()).map_err(|error| HubError::new(format!("Could not apply Intel power limits: {error}. Some limits may have changed.")))?;
        let accepted = read_int(path).ok_or_else(|| HubError::new(format!("Cannot read back {}", path.display())))?;
        let tolerance = if path.to_string_lossy().ends_with("_uw") { 125_000 } else { 0 };
        if (accepted - i64::from(*value)).abs() > tolerance {
            return Err(HubError::new(format!("{} rejected the requested power limit. Some limits may have changed.", path.display())));
        }
    }
    Ok("Intel PL1/PL2 power limits applied".into())
}

#[cfg(test)]
#[path = "../../../tests/rust/victus-hw/lib.rs"]
mod tests;
