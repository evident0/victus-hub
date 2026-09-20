"""Unit tests for the Settings diagnostics report."""

from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from victus_hub.backend.diagnostics import (
    Capability,
    acpi_error_line,
    build_report,
    collect_acpi_errors,
    collect_module_errors,
    journal_line_visible,
    kernel_module_error_line,
    render_markdown,
    write_diagnostics_report,
)
from victus_hub.backend.session_log import SessionLogHandler


def _sample_markdown(**overrides) -> str:
    kwargs = dict(
        generated=datetime(2026, 9, 13, 15, 30, 0),
        system=[("Board", "8BD4"), ("BIOS", "F.22 | vendor")],
        capabilities=[
            Capability("GPU MUX", True, "hybrid, discrete"),
            Capability("Keyboard RGB", False, "hp-kbd-rgb not loaded"),
        ],
        modules=[("hp_wmi", True), ("hp_kbd_rgb", False)],
        session_log=[
            "\033[31m← daemon: ERR\033[0m",
            "→ daemon: keyboard-brightness 0/255",
        ],
        daemon_log="Sep 13 daemon start",
        module_errors=(
            "hp_wmi: Unknown EC layout for board 8BD4\n"
            "hp_kbd_rgb: module verification failed"
        ),
        acpi_errors=(
            "ACPI BIOS Error (bug): Attempt to CreateField of length zero\n"
            "ACPI Error: Aborting method \\_SB.WMID.WHCM"
        ),
    )
    kwargs.update(overrides)
    return render_markdown(**kwargs)


class TestRenderMarkdown(unittest.TestCase):
    def test_sections_and_escaping(self):
        md = _sample_markdown()
        self.assertIn("# Victus Hub diagnostics", md)
        self.assertIn("2026-09-13 15:30:00", md)
        self.assertIn("8BD4", md)
        self.assertIn("F.22 \\| vendor", md)
        self.assertIn("| GPU MUX | yes |", md)
        self.assertIn("| Keyboard RGB | no |", md)
        self.assertIn("`hp_wmi`", md)
        self.assertIn("keyboard-brightness 0/255", md)
        self.assertNotIn("\033[", md)
        self.assertIn("Sep 13 daemon start", md)
        self.assertIn("## hp-wmi / RGB module errors", md)
        self.assertIn("hp_wmi: Unknown EC layout", md)
        self.assertIn("hp_kbd_rgb: module verification failed", md)
        self.assertIn("## ACPI errors", md)
        self.assertIn("ACPI BIOS Error", md)
        self.assertIn("ACPI Error: Aborting method", md)

    def test_empty_logs_use_placeholders(self):
        md = _sample_markdown(
            session_log=[],
            daemon_log=None,
            module_errors="",
            acpi_errors="",
        )
        self.assertIn("_No session log captured._", md)
        self.assertIn("_Daemon journal not available._", md)
        self.assertIn("_No hp-wmi / RGB module errors._", md)
        self.assertIn("_No ACPI errors._", md)

    def test_missing_kernel_journal_placeholder(self):
        md = _sample_markdown(module_errors=None, acpi_errors=None)
        self.assertEqual(md.count("_Kernel journal not available._"), 2)


class TestWriteReport(unittest.TestCase):
    def test_writes_markdown_in_dest_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            with patch(
                "victus_hub.backend.diagnostics.build_report",
                return_value="# test\n",
            ):
                path = write_diagnostics_report(dest)
            self.assertTrue(path.exists())
            self.assertTrue(path.name.startswith("victus-hub-diagnostics-"))
            self.assertTrue(path.name.endswith(".md"))
            self.assertEqual(path.read_text(encoding="utf-8"), "# test\n")


class TestBuildReportSmoke(unittest.TestCase):
    def test_live_collect_has_required_headings(self):
        md = build_report()
        self.assertIn("# Victus Hub diagnostics", md)
        self.assertIn("## System", md)
        self.assertIn("## Capabilities", md)
        self.assertIn("## Kernel modules", md)
        self.assertIn("## hp-wmi / RGB module errors", md)
        self.assertIn("## ACPI errors", md)
        self.assertIn("## Session log", md)
        self.assertIn("Board", md)
        self.assertIn("Secure Boot", md)
        self.assertIn("Platform profile tool", md)


class TestJournalLineVisible(unittest.TestCase):
    def test_level_zero_keeps_errors_only(self):
        self.assertTrue(journal_line_visible("python3[1]: ERR failed to apply", 0))
        self.assertFalse(journal_line_visible("python3[1]: fan pwm1=128", 0))
        self.assertFalse(journal_line_visible("python3[1]: power-limits STAPM=30", 0))
        self.assertFalse(journal_line_visible("python3[1]: kbd-watch: monitoring", 0))
        self.assertFalse(journal_line_visible("python3[1]: keyboard-last-event 1", 3))

    def test_higher_levels_keep_matching_categories(self):
        self.assertTrue(journal_line_visible("python3[1]: fan pwm1=128", 1))
        self.assertFalse(journal_line_visible("python3[1]: power-limits STAPM=30", 1))
        self.assertTrue(journal_line_visible("python3[1]: power-limits STAPM=30", 2))
        self.assertTrue(journal_line_visible("python3[1]: kbd-watch: monitoring", 3))


class TestKernelErrorFilters(unittest.TestCase):
    def test_hp_module_errors(self):
        self.assertTrue(kernel_module_error_line(
            "hp_wmi: Unknown EC layout for board 8BD4. Thermal profile readback will be disabled."
        ))
        self.assertTrue(kernel_module_error_line(
            "hp_wmi: query 0x4 returned error 0x5"
        ))
        self.assertTrue(kernel_module_error_line(
            "hp_kbd_rgb: module verification failed: signature and/or required key missing"
        ))
        self.assertTrue(kernel_module_error_line(
            "platform hp-wmi: Could not register hp hwmon device"
        ))
        self.assertFalse(kernel_module_error_line(
            "hp_wmi: Registered as platform profile handler"
        ))
        self.assertFalse(kernel_module_error_line(
            "platform hp-kbd-rgb: registered keyboard RGB type 0x04 with 1 zone(s)"
        ))
        self.assertFalse(kernel_module_error_line(
            "hp_kbd_rgb: loading out-of-tree module taints kernel."
        ))
        self.assertFalse(kernel_module_error_line(
            "ACPI Error: Aborting method \\_SB.WMID.WHCM"
        ))

    def test_acpi_errors(self):
        self.assertTrue(acpi_error_line(
            "ACPI BIOS Error (bug): Attempt to CreateField of length zero"
        ))
        self.assertTrue(acpi_error_line(
            "ACPI Error: Aborting method \\_SB.WMID.WHCM due to previous error"
        ))
        self.assertTrue(acpi_error_line(
            "ACPI Exception: AE_NOT_FOUND, Evaluating _PLD"
        ))
        self.assertFalse(acpi_error_line("ACPI: button: Lid Switch"))
        self.assertFalse(acpi_error_line(
            "hp_wmi: query 0x4 returned error 0x5"
        ))

    def test_collect_splits_and_caps(self):
        journal = "\n".join([
            "hp_wmi: Registered as platform profile handler",
            "hp_wmi: Unknown EC layout for board 8BD4",
            "ACPI BIOS Error (bug): Attempt to CreateField of length zero",
            "ACPI Error: Aborting method \\_SB.WMID.WHCM",
            "hp_kbd_rgb: module verification failed",
            "unrelated line",
        ])
        module = collect_module_errors(journal)
        acpi = collect_acpi_errors(journal)
        self.assertEqual(
            module,
            "hp_wmi: Unknown EC layout for board 8BD4\n"
            "hp_kbd_rgb: module verification failed",
        )
        self.assertEqual(
            acpi,
            "ACPI BIOS Error (bug): Attempt to CreateField of length zero\n"
            "ACPI Error: Aborting method \\_SB.WMID.WHCM",
        )
        self.assertEqual(collect_module_errors(journal, n=1), "hp_kbd_rgb: module verification failed")
        self.assertEqual(collect_module_errors(""), "")
        self.assertIsNone(collect_module_errors(None))
        self.assertIsNone(collect_acpi_errors(None))


class TestSessionLogHandler(unittest.TestCase):
    def test_keeps_formatted_lines(self):
        handler = SessionLogHandler(maxlen=3)
        handler.setFormatter(logging.Formatter("%(message)s"))
        log = logging.getLogger("test.session_log")
        log.setLevel(logging.INFO)
        log.addHandler(handler)
        log.propagate = False
        try:
            log.info("one")
            log.info("two")
            log.info("three")
            log.info("four")
            self.assertEqual(list(handler.records), ["two", "three", "four"])
        finally:
            log.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
