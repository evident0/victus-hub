use std::path::{Path, PathBuf};
use std::time::Duration;

use victus_core::CpuPowerSample;

#[derive(Debug, Default)]
pub struct RaplSampler {
    previous: Option<(PathBuf, i64, Option<i64>, Duration)>,
}

impl RaplSampler {
    pub fn read(&mut self, powercap: &Path, now: Duration) -> CpuPowerSample {
        let Some(package) = rapl_package(powercap) else {
            return CpuPowerSample::Unavailable { message: "no RAPL package".into() };
        };
        let energy_path = package.join("energy_uj");
        let Some(energy) = read_energy(&energy_path) else {
            return CpuPowerSample::Unavailable { message: energy_path.display().to_string() };
        };
        let max_range = read_energy(&package.join("max_energy_range_uj"));
        let source = energy_path.display().to_string();
        let previous = self.previous.replace((package.clone(), energy, max_range, now));
        let Some((old_package, old_energy, old_max, old_time)) = previous else {
            return CpuPowerSample::Sampling { source };
        };
        if old_package != package {
            return CpuPowerSample::Sampling { source };
        }
        let elapsed = now.saturating_sub(old_time).as_secs_f64();
        let mut delta = energy - old_energy;
        if delta < 0 {
            if let Some(wrap) = max_range.or(old_max) {
                delta += wrap;
            }
        }
        if elapsed <= 0.0 || delta < 0 {
            return CpuPowerSample::Unavailable { message: source };
        }
        CpuPowerSample::Watts { watts: delta as f64 / 1_000_000.0 / elapsed, source }
    }
}

fn rapl_package(powercap: &Path) -> Option<PathBuf> {
    let mut entries = Vec::new();
    for entry in std::fs::read_dir(powercap).ok()? {
        let entry = entry.ok()?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.starts_with("intel-rapl:") && name.matches(':').count() == 1 {
            entries.push(entry.path());
        }
    }
    entries.sort();
    entries.into_iter().next()
}

fn read_energy(path: &Path) -> Option<i64> {
    std::fs::read_to_string(path).ok()?.trim().parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use victus_core::offline_scratch;

    #[test]
    fn two_samples_become_watts_inside_the_scratch_tree() {
        let root = offline_scratch("rapl");
        let package = root.join("intel-rapl:0");
        std::fs::create_dir_all(&package).unwrap();
        std::fs::write(package.join("energy_uj"), "1000000\n").unwrap();
        std::fs::write(package.join("max_energy_range_uj"), "100000000\n").unwrap();
        let mut sampler = RaplSampler::default();
        assert!(matches!(sampler.read(&root, Duration::from_secs(0)), CpuPowerSample::Sampling { .. }));
        std::fs::write(package.join("energy_uj"), "3000000\n").unwrap();
        match sampler.read(&root, Duration::from_secs(1)) {
            CpuPowerSample::Watts { watts, .. } => assert!((watts - 2.0).abs() < 1e-9),
            other => panic!("{other:?}"),
        }
        let _ = std::fs::remove_dir_all(root);
    }
}
