use crate::loglevel::journal_line_visible;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Capability {
    pub name: String,
    pub available: bool,
    pub details: String,
}

pub fn kernel_module_error_line(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    let module = lower.contains("hp_wmi")
        || lower.contains("hp-wmi")
        || lower.contains("hp_kbd_rgb")
        || lower.contains("hp-kbd-rgb");
    if !module {
        return false;
    }
    let problem = ["error", "err", "fail", "failed", "warn", "warning", "unable", "invalid", "unknown", "exception", "abort", "could not"];
    problem.iter().any(|word| contains_word_or_phrase(&lower, word))
}

pub fn acpi_error_line(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    lower.contains("acpi error")
        || lower.contains("acpi bios error")
        || lower.contains("acpi exception")
        || lower.contains("acpi warning")
}

fn contains_word_or_phrase(text: &str, needle: &str) -> bool {
    if needle.contains(' ') {
        return text.contains(needle);
    }
    text.split(|ch: char| !ch.is_ascii_alphanumeric()).any(|part| part == needle)
        || (matches!(needle, "fail" | "warn" | "err") && text.split(|ch: char| !ch.is_ascii_alphanumeric()).any(|part| part.starts_with(needle)))
}

pub fn filter_journal_lines(journal: Option<&str>, predicate: impl Fn(&str) -> bool, limit: usize) -> Option<String> {
    let journal = journal?;
    let kept: Vec<&str> = journal.lines().filter(|line| predicate(line)).collect();
    if kept.is_empty() {
        return Some(String::new());
    }
    let start = kept.len().saturating_sub(limit);
    Some(kept[start..].join("\n"))
}

pub fn filter_daemon_journal(journal: &str, level: i32, limit: usize) -> Option<String> {
    let kept: Vec<&str> = journal.lines().filter(|line| journal_line_visible(line, level)).collect();
    if kept.is_empty() {
        return None;
    }
    let start = kept.len().saturating_sub(limit);
    Some(kept[start..].join("\n"))
}

pub fn render_markdown(
    generated: &str,
    system: &[(&str, &str)],
    capabilities: &[Capability],
    modules: &[(&str, bool)],
    session_log: &[String],
    daemon_log: Option<&str>,
    module_errors: Option<&str>,
    acpi_errors: Option<&str>,
) -> String {
    let mut lines = vec![
        "# Victus Hub diagnostics".to_owned(),
        String::new(),
        format!("Generated: {generated}"),
        String::new(),
        "## System".to_owned(),
        String::new(),
        "| | |".to_owned(),
        "| --- | --- |".to_owned(),
    ];
    for (key, value) in system {
        lines.push(format!("| {} | {} |", cell(key), cell(value)));
    }
    lines.extend([
        String::new(),
        "## Capabilities".to_owned(),
        String::new(),
        "| Capability | Available | Details |".to_owned(),
        "| --- | --- | --- |".to_owned(),
    ]);
    for cap in capabilities {
        let avail = if cap.available { "yes" } else { "no" };
        lines.push(format!("| {} | {avail} | {} |", cell(&cap.name), cell(&cap.details)));
    }
    lines.extend([
        String::new(),
        "## Kernel modules".to_owned(),
        String::new(),
        "| Module | Loaded |".to_owned(),
        "| --- | --- |".to_owned(),
    ]);
    for (name, loaded) in modules {
        lines.push(format!("| `{name}` | {} |", if *loaded { "yes" } else { "no" }));
    }
    lines.extend([String::new(), "## hp-wmi / RGB module errors".to_owned(), String::new()]);
    lines.extend(log_section(module_errors, "_No hp-wmi / RGB module errors._", "_Kernel journal not available._"));
    lines.extend([String::new(), "## ACPI errors".to_owned(), String::new()]);
    lines.extend(log_section(acpi_errors, "_No ACPI errors._", "_Kernel journal not available._"));
    lines.extend([String::new(), "## Session log".to_owned(), String::new()]);
    if session_log.is_empty() {
        lines.push("_No session log captured._".to_owned());
    } else {
        lines.push("```".to_owned());
        lines.extend(session_log.iter().map(|line| strip_ansi(line)));
        lines.push("```".to_owned());
    }
    lines.extend([String::new(), "## Daemon journal".to_owned(), String::new()]);
    if let Some(daemon_log) = daemon_log {
        lines.push("```".to_owned());
        lines.push(daemon_log.to_owned());
        lines.push("```".to_owned());
    } else {
        lines.push("_Daemon journal not available._".to_owned());
    }
    lines.push(String::new());
    lines.join("\n")
}

fn log_section(text: Option<&str>, empty: &str, missing: &str) -> Vec<String> {
    match text {
        None => vec![missing.to_owned()],
        Some("") => vec![empty.to_owned()],
        Some(text) => vec!["```".to_owned(), text.to_owned(), "```".to_owned()],
    }
}

fn cell(value: &str) -> String {
    let cleaned = value.replace('|', "\\|").replace('\n', " ");
    let cleaned = cleaned.trim();
    if cleaned.is_empty() { "—".to_owned() } else { cleaned.to_owned() }
}

fn strip_ansi(line: &str) -> String {
    let mut out = String::new();
    let bytes = line.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == 0x1b && index + 1 < bytes.len() && bytes[index + 1] == b'[' {
            index += 2;
            while index < bytes.len() && bytes[index] != b'm' {
                index += 1;
            }
            index += 1;
            continue;
        }
        out.push(bytes[index] as char);
        index += 1;
    }
    out
}

#[cfg(test)]
mod tests {
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
}
