import logging
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from victus_hub.backend import daemon_client
from victus_hub.logging_config import TerminalDebugFilter


class TerminalDebugTests(unittest.TestCase):
    def test_cumulative_categories_and_errors(self):
        cases = [
            ("fan-control", "adjusting target", 1),
            ("main_window", "suspend cleanup: fans set to auto", 1),
            ("sysfs", "/sys/class/hwmon/hwmon0/pwm1=128", 1),
            ("daemon_client", "→ daemon: power-limits STAPM=30", 2),
            ("power_state", "acquired delay inhibitor", 2),
            ("daemon_client", "→ daemon: cpu-frequency-limits 800 4000", 2),
            ("main_window", "shutdown cleanup: keyboard brightness set to 0", 3),
            ("daemon", "kbd-watch: monitoring device", 3),
            ("other", "unrelated startup message", None),
            ("daemon_client", "→ daemon: gpu-mux-mode 1", None),
        ]
        for level in range(4):
            filter_ = TerminalDebugFilter(level)
            for name, message, minimum in cases:
                with self.subTest(level=level, message=message):
                    record = logging.LogRecord(name, logging.INFO, "", 0, message, (), None)
                    self.assertEqual(
                        filter_.filter(record), minimum is not None and minimum <= level,
                    )
                    record.levelno = logging.ERROR
                    self.assertTrue(filter_.filter(record))
            warning = logging.LogRecord("other", logging.WARNING, "", 0, "warning", (), None)
            self.assertFalse(filter_.filter(warning))

    def test_daemon_replies_keep_request_category(self):
        with patch.object(daemon_client.socket, "socket") as socket_factory:
            socket_factory.return_value.recv.return_value = b"OK\n"
            for request, minimum in [("fan-auto\n", 1), ("power-limits\t30\n", 2),
                                     ("keyboard-brightness\t100\n", 3)]:
                with self.assertLogs(daemon_client.logger, logging.INFO) as logs:
                    daemon_client._request_daemon(request)
                record = logs.records[0]
                self.assertEqual(record.debug_level, minimum)
                self.assertFalse(TerminalDebugFilter(minimum - 1).filter(record))
                self.assertTrue(TerminalDebugFilter(minimum).filter(record))

    def test_quiet_daemon_errors_are_visible_at_zero(self):
        with patch.object(daemon_client.socket, "socket") as socket_factory:
            socket_factory.return_value.recv.return_value = b"ERR\tfailed\n"
            with self.assertLogs(daemon_client.logger, logging.ERROR) as logs:
                daemon_client._request_daemon("cpu-power\n", quiet=True)
            self.assertTrue(TerminalDebugFilter(0).filter(logs.records[0]))

    def test_launcher_level_parsing(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        for args, expected in [([], "0"), (["0"], "0"), (["1"], "1"),
                               (["2"], "2"), (["3"], "3")]:
            result = subprocess.run(
                ["bash", "-c", 'scripts=$1; shift; source "$scripts/debug-level.sh"; '
                 'printf "%s" "$VICTUS_HUB_DEBUG_LEVEL"', "test", str(scripts), *args],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)
        for script in ("dev-run", "ui-test"):
            for args in (["4"], ["-1"], ["abc"], ["1", "2"]):
                result = subprocess.run(
                    [str(scripts / script), *args], capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
