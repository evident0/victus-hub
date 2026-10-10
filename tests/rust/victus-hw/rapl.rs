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
