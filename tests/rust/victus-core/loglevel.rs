use super::*;

#[test]
fn debug_level_filters_categories() {
    assert!(debug_level_from_value("4").is_err());
    assert_eq!(message_debug_level("keyboard color"), Some(3));
    assert_eq!(message_debug_level("ryzenadj stapm"), Some(2));
    assert_eq!(message_debug_level("fan pwm write"), Some(1));
    assert!(!terminal_line_visible("fan tick", 0, false));
    assert!(terminal_line_visible("fan tick", 1, false));
    assert!(terminal_line_visible("anything", 0, true));
    assert!(!journal_line_visible("keyboard-last-input 1.0", 3));
}

#[test]
fn category_matrix_matches_python_logger_and_message() {
    let cases = [
        ("fan-control adjusting target", Some(1)),
        ("main_window suspend cleanup: fans set to auto", Some(1)),
        ("sysfs /sys/class/hwmon/hwmon0/pwm1=128", Some(1)),
        ("daemon_client → daemon: power-limits STAPM=30", Some(2)),
        ("power_state acquired delay inhibitor", Some(2)),
        ("daemon_client → daemon: cpu-frequency-limits 800 4000", Some(2)),
        ("main_window shutdown cleanup: keyboard brightness set to 0", Some(3)),
        ("daemon kbd-watch: monitoring device", Some(3)),
        ("other unrelated startup message", None),
        ("daemon_client → daemon: gpu-mux-mode 1", None),
    ];
    for level in 0..=3 {
        for (text, minimum) in cases {
            let visible = terminal_line_visible(text, level, false);
            assert_eq!(visible, minimum.is_some_and(|minimum| minimum <= level), "{text} @ {level}");
            assert!(terminal_line_visible(text, level, true), "{text} error @ {level}");
        }
        assert!(!terminal_line_visible("warning", level, false));
    }
    for (raw, expected) in [("0", 0), ("1", 1), ("2", 2), ("3", 3)] {
        assert_eq!(debug_level_from_value(raw).unwrap(), expected);
    }
    for raw in ["4", "-1", "abc"] {
        assert!(debug_level_from_value(raw).is_err());
    }
    assert_eq!(message_debug_level("fan-auto"), Some(1));
    assert_eq!(message_debug_level("power-limits\t30"), Some(2));
    assert_eq!(message_debug_level("keyboard-brightness\t100"), Some(3));
    assert!(journal_line_visible("python3[1]: ERR failed to apply", 0));
    assert!(!journal_line_visible("python3[1]: fan pwm1=128", 0));
    assert!(!journal_line_visible("python3[1]: power-limits STAPM=30", 0));
    assert!(!journal_line_visible("python3[1]: kbd-watch: monitoring", 0));
    assert!(!journal_line_visible("python3[1]: keyboard-last-event 1", 3));
    assert!(journal_line_visible("python3[1]: fan pwm1=128", 1));
    assert!(!journal_line_visible("python3[1]: power-limits STAPM=30", 1));
    assert!(journal_line_visible("python3[1]: power-limits STAPM=30", 2));
    assert!(journal_line_visible("python3[1]: kbd-watch: monitoring", 3));
    assert!(!journal_line_visible("python3[1]: [cpu-power] sample", 3));
}
