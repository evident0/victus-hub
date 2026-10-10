use super::*;

#[test]
fn journal_filters_keep_real_errors_and_distinct_placeholders() {
    assert!(kernel_module_error_line("hp_wmi: Unknown EC layout"));
    assert!(!kernel_module_error_line("hp_wmi: Registered as platform profile handler"));
    assert!(acpi_error_line("ACPI BIOS Error on WMID"));
    assert!(!acpi_error_line("ACPI: battery"));
    let errors = filter_journal_lines(Some("ok\nhp-kbd-rgb: verification failed\n"), kernel_module_error_line, 80);
    assert!(errors.unwrap().contains("verification failed"));
    let report = render_markdown("2026-10-08 12:00:00", &[("Board", "8BD4")], &[], &[], &[], None, None, Some(""));
    assert!(report.contains("## hp-wmi / RGB module errors"));
    assert!(report.contains("_Kernel journal not available._"));
    assert!(report.contains("_No ACPI errors._"));
    assert!(report.contains("_Daemon journal not available._"));
}

#[test]
fn markdown_escaping_filters_and_caps_match_python() {
    let report = render_markdown(
        "2026-09-13 15:30:00",
        &[("Board", "8BD4"), ("BIOS", "F.22 | vendor")],
        &[
            Capability { name: "GPU MUX".into(), available: true, details: "hybrid, discrete".into() },
            Capability { name: "Keyboard RGB".into(), available: false, details: "hp-kbd-rgb not loaded".into() },
        ],
        &[("hp_wmi", true), ("hp_kbd_rgb", false)],
        &["\u{1b}[31m← daemon: ERR\u{1b}[0m".into(), "→ daemon: keyboard-brightness 0/255".into()],
        Some("Sep 13 daemon start"),
        Some("hp_wmi: Unknown EC layout for board 8BD4\nhp_kbd_rgb: module verification failed"),
        Some("ACPI BIOS Error (bug): Attempt to CreateField of length zero\nACPI Error: Aborting method"),
    );
    assert!(report.contains("# Victus Hub diagnostics"));
    assert!(report.contains("F.22 \\| vendor"));
    assert!(report.contains("| GPU MUX | yes |"));
    assert!(report.contains("| Keyboard RGB | no |"));
    assert!(report.contains("`hp_wmi`"));
    assert!(report.contains("keyboard-brightness 0/255"));
    assert!(!report.contains('\u{1b}'));
    assert!(report.contains("Sep 13 daemon start"));

    let empty = render_markdown("2026-09-13 15:30:00", &[], &[], &[], &[], None, Some(""), Some(""));
    assert!(empty.contains("_No session log captured._"));
    assert!(empty.contains("_Daemon journal not available._"));
    assert!(empty.contains("_No hp-wmi / RGB module errors._"));
    assert!(empty.contains("_No ACPI errors._"));
    let missing = render_markdown("2026-09-13 15:30:00", &[], &[], &[], &[], None, None, None);
    assert_eq!(missing.matches("_Kernel journal not available._").count(), 2);

    assert!(kernel_module_error_line("hp_wmi: query 0x4 returned error 0x5"));
    assert!(kernel_module_error_line("hp_kbd_rgb: module verification failed: signature and/or required key missing"));
    assert!(kernel_module_error_line("platform hp-wmi: Could not register hp hwmon device"));
    assert!(!kernel_module_error_line("platform hp-kbd-rgb: registered keyboard RGB type 0x04 with 1 zone(s)"));
    assert!(!kernel_module_error_line("hp_kbd_rgb: loading out-of-tree module taints kernel."));
    assert!(!kernel_module_error_line("ACPI Error: Aborting method \\_SB.WMID.WHCM"));
    assert!(acpi_error_line("ACPI BIOS Error (bug): Attempt to CreateField of length zero"));
    assert!(acpi_error_line("ACPI Exception: AE_NOT_FOUND, Evaluating _PLD"));
    assert!(acpi_error_line("ACPI Warning: something"));
    assert!(!acpi_error_line("ACPI: button: Lid Switch"));
    assert!(!acpi_error_line("hp_wmi: query 0x4 returned error 0x5"));

    let journal = [
        "hp_wmi: Registered as platform profile handler",
        "hp_wmi: Unknown EC layout for board 8BD4",
        "ACPI BIOS Error (bug): Attempt to CreateField of length zero",
        "ACPI Error: Aborting method \\_SB.WMID.WHCM",
        "hp_kbd_rgb: module verification failed",
        "unrelated line",
    ]
    .join("\n");
    assert_eq!(
        filter_journal_lines(Some(&journal), kernel_module_error_line, 80).unwrap(),
        "hp_wmi: Unknown EC layout for board 8BD4\nhp_kbd_rgb: module verification failed"
    );
    assert_eq!(
        filter_journal_lines(Some(&journal), acpi_error_line, 80).unwrap(),
        "ACPI BIOS Error (bug): Attempt to CreateField of length zero\nACPI Error: Aborting method \\_SB.WMID.WHCM"
    );
    assert_eq!(
        filter_journal_lines(Some(&journal), kernel_module_error_line, 1).unwrap(),
        "hp_kbd_rgb: module verification failed"
    );
    assert_eq!(filter_journal_lines(Some(""), kernel_module_error_line, 80).as_deref(), Some(""));
    assert!(filter_journal_lines(None, kernel_module_error_line, 80).is_none());
}
