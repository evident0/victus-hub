use super::*;

#[test]
fn pages_request_only_what_they_show() {
    assert!(keys_for_page(1).contains(&"cpu-power"));
    assert!(!keys_for_page(1).contains(&"gpu-power"));
    assert!(keys_for_page(2).iter().all(|key| key.ends_with("-fan")));
    assert!(keys_for_page(3).is_empty());
    assert!(keys_for_page(5).is_empty());
    assert!(keys_for_page(4).contains(&"lm-sensors"));
    assert!(keys_for_page(9).is_empty());
    assert_eq!(keys_for_page(1), POWER_PAGE_KEYS.to_vec());
    assert!(POWER_PAGE_KEYS.iter().all(|key| !GPU_QUERY_KEYS.contains(key)));
    assert_eq!(keys_for_page(2), FANS_PAGE_KEYS.to_vec());
    assert!(keys_for_page(0).contains(&"cpu-temp"));
    assert!(!keys_for_page(0).contains(&"cpu-frequency"));
    assert!(!keys_for_page(0).contains(&"lm-sensors"));
    assert!(!keys_for_page(0).contains(&"pwm-value"));
    assert!(keys_for_page(4).contains(&"gpu-power"));
    assert!(!keys_for_page(4).contains(&"profile"));
}

#[test]
fn graph_keys_collapse_onto_daemon_keys() {
    assert_eq!(request_key_for_graph("cpu-frequency-0"), "cpu-frequency");
    assert_eq!(request_key_for_graph("lm-k10temp-temp1"), "lm-sensors");
    assert_eq!(request_key_for_graph("cpu-temp"), "cpu-temp");
    assert_eq!(request_key_for_graph("cpu-frequency-3"), "cpu-frequency");
    assert_eq!(request_key_for_graph("lm-nvme-composite"), "lm-sensors");
    assert_eq!(request_key_for_graph("gpu-power"), "gpu-power");
}
