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
    build_report,
    journal_line_visible,
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

    def test_empty_logs_use_placeholders(self):
        md = _sample_markdown(session_log=[], daemon_log=None)
        self.assertIn("_No session log captured._", md)
        self.assertIn("_Daemon journal not available._", md)


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
