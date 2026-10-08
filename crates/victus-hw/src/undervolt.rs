use std::fs::OpenOptions;
use std::os::unix::fs::FileExt;
use std::path::Path;
use std::thread;
use std::time::Duration;

use victus_core::{validate_undervolt, HubError, HubResult};

/// Write Intel OC-mailbox undervolt offsets through the given MSR file.
///
/// Callers pass the device path. Tests pass a validation failure so this
/// function never opens `/dev/cpu/0/msr`.
pub fn apply_undervolt(path: &Path, intel: bool, core_mv: i32, cache_mv: i32) -> HubResult<String> {
    if !intel {
        return Err(HubError::new("Intel CPU controls are unavailable on this processor"));
    }
    validate_undervolt(core_mv, cache_mv)?;
    let file = OpenOptions::new().read(true).write(true).open(path).map_err(|error| {
        HubError::new(format!("Intel undervolting requires writable MSR access (msr kernel module): {error}"))
    })?;
    for domain in [0_u64, 2] {
        mailbox(&file, domain, None)?;
    }
    for (domain, mv) in [(0_u64, core_mv), (2, cache_mv)] {
        let encoded = (mv as f64 * 1.024).round() as i32;
        mailbox(&file, domain, Some(encoded))?;
        if mailbox(&file, domain, None)? != encoded {
            return Err(HubError::new(
                "Intel undervolt was not accepted; firmware may lock voltage control. Some offsets may have changed.",
            ));
        }
    }
    Ok(format!("Intel undervolt applied: core {core_mv} mV, cache {cache_mv} mV"))
}

fn mailbox(file: &std::fs::File, domain: u64, offset: Option<i32>) -> HubResult<i32> {
    let mut command = 0x8000_0010_0000_0000_u64 | (domain << 40);
    if let Some(offset) = offset {
        command |= 0x1_0000_0000 | ((u64::from(offset as u16) & 0x7FF) << 21);
    }
    let bytes = command.to_le_bytes();
    let wrote = file.write_at(&bytes, 0x150).map_err(|error| HubError::new(format!("Could not apply Intel undervolt: {error}")))?;
    if wrote != 8 {
        return Err(HubError::new("Short write to Intel voltage mailbox"));
    }
    for _ in 0..20 {
        let mut buf = [0_u8; 8];
        let read = file.read_at(&mut buf, 0x150).map_err(|error| HubError::new(format!("Could not apply Intel undervolt: {error}")))?;
        if read != 8 {
            return Err(HubError::new("Short read from Intel voltage mailbox"));
        }
        let response = u64::from_le_bytes(buf);
        if response & (1 << 63) == 0 {
            if (response >> 32) & 0xFF != 0 {
                return Err(HubError::new("Intel undervolting is unsupported or locked by firmware"));
            }
            let raw = ((response >> 21) & 0x7FF) as i32;
            return Ok(if raw & 0x400 != 0 { raw - 0x800 } else { raw });
        }
        thread::sleep(Duration::from_millis(1));
    }
    Err(HubError::new("Intel voltage mailbox timed out"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use victus_core::offline_scratch;

    #[test]
    fn rejected_undervolt_does_not_open_a_device_file() {
        let root = offline_scratch("msr");
        let path = root.join("msr");
        let error = apply_undervolt(&path, true, -300, 0).unwrap_err();
        assert!(error.to_string().contains("-250"));
        assert!(!path.exists());
        let error = apply_undervolt(&path, false, -50, -50).unwrap_err();
        assert!(error.to_string().contains("unavailable"));
        assert!(!path.exists());
        let _ = std::fs::remove_dir_all(root);
    }
}
