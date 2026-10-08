use super::*;

#[test]
fn power_step_uses_bankers_rounding() {
    assert_eq!(clamp_power_limit(14_500), 15_000);
    assert_eq!(clamp_power_limit(25_400), 25_000);
    assert_eq!(clamp_power_limit(25_500), 26_000);
    assert_eq!(clamp_power_limit(200_000), 120_000);
}

#[test]
fn ryzenadj_rejects_values_outside_its_window() {
    assert!(validate_ryzenadj(25_000, 25_000, 25_000, 95).is_ok());
    assert!(validate_ryzenadj(500, 25_000, 25_000, 95).is_err());
    assert!(validate_ryzenadj(25_000, 25_000, 25_000, 96).is_err());
    assert_eq!(
        ryzenadj_args(1, 2, 3, 95),
        vec![
            "--stapm-limit=1",
            "--fast-limit=2",
            "--slow-limit=3",
            "--tctl-temp=95",
        ]
    );
}

#[test]
fn intel_limits_and_offsets_are_bounded() {
    assert!(validate_intel_power(15_000, 120_000).is_ok());
    assert!(validate_intel_power(20_000, 15_000).is_err());
    assert!(validate_undervolt(-250, 0).is_ok());
    assert!(validate_undervolt(1, 0).is_err());
    assert!(validate_frequency(400_000, 5_000_000).is_ok());
    assert!(validate_frequency(0, 1).is_err());
}
