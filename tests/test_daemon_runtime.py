"""Daemon policy persistence and Qt-free control-loop helpers."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from victus_hub.backend.fan_config import config_from_dict, config_to_dict
from victus_hub.backend.types import FanPoint
from victus_hub.features.keyboard.lighting import (
    lighting_from_dict,
    lighting_to_dict,
    step_brightness_settings,
    step_effect_settings,
)
from victus_hubd import host
from victus_hubd.host import ac_online
from victus_hubd.state import (
    DaemonState,
    load_state,
    save_state,
    state_from_dict,
    state_to_dict,
)


class TestFanConfigDict(unittest.TestCase):
    def test_roundtrip_preserves_curves_and_flags(self):
        config = config_from_dict({
            "custom_curve_enabled": True,
            "smart_curve_enabled": False,
            "fan_curve_response": "aggressive",
            "min_fan_change_pct": 4.5,
            "curve_points_by_profile": {
                "balanced": [[30, 10], [100, 90]],
            },
        })
        restored = config_from_dict(config_to_dict(config))
        self.assertTrue(restored.custom_enabled)
        self.assertEqual(restored.curve_response, "aggressive")
        self.assertEqual(restored.min_fan_change_pct, 4.5)
        self.assertEqual(restored.profiles[1].cpu_points[0], FanPoint(30, 10))


class TestLightingPolicy(unittest.TestCase):
    def test_dict_roundtrip_and_brightness_steps(self):
        settings = lighting_from_dict({
            "enabled": True, "effect": "static", "color": "#ff0000",
            "brightness": 0, "speed": 50,
        })
        self.assertEqual(lighting_from_dict(lighting_to_dict(settings)).color, "#ff0000")
        up = step_brightness_settings(settings, 1)
        self.assertEqual(up.brightness, 64)
        self.assertTrue(up.enabled)
        down = step_brightness_settings(up, -1)
        self.assertEqual(down.brightness, 0)

    def test_effect_cycle_includes_off(self):
        settings = lighting_from_dict({"enabled": True, "effect": "static"})
        off = step_effect_settings(settings, -1, 1)
        self.assertFalse(off.enabled)
        back = step_effect_settings(off, 1, 1)
        self.assertTrue(back.enabled)
        self.assertEqual(back.effect, "static")


class TestDaemonStateFile(unittest.TestCase):
    def test_missing_file_is_uninitialized_legacy_file_is_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            self.assertFalse(load_state(path).initialized)
            path.write_text(json.dumps({
                "fan": {"custom_curve_enabled": True},
            }))
            self.assertTrue(load_state(path).initialized)

    def test_load_missing_and_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            empty = load_state(path)
            self.assertFalse(empty.fan.custom_enabled)
            empty.battery_power_save = True
            empty.hardware_shortcuts = True
            empty.cpu_frequency = (1200000, 4200000)
            save_state(empty, path)
            loaded = load_state(path)
            self.assertTrue(loaded.battery_power_save)
            self.assertTrue(loaded.hardware_shortcuts)
            self.assertEqual(loaded.cpu_frequency, (1200000, 4200000))

    def test_json_protocol_has_no_newlines(self):
        payload = json.dumps(state_to_dict(state_from_dict({})), separators=(",", ":"))
        self.assertNotIn("\n", payload)
        self.assertNotIn("\t", payload)


class TestProfileDiscovery(unittest.TestCase):
    def test_event_path_does_not_run_profile_cli(self):
        with patch("victus_hubd.host._tuned_profile_name", return_value=""), \
                patch("victus_hubd.host._busctl_active_profile", return_value=None), \
                patch("victus_hub.backend.profiles.current_ui_profile_index") as cli:
            self.assertIsNone(host.read_profile_index(allow_cli=False))
            cli.assert_not_called()


class TestSettingsSync(unittest.TestCase):
    def test_uninitialized_migrates_local_settings(self):
        from victus_hub import api

        with patch("victus_hub.api.daemon_client.request_get_state", return_value={"initialized": False}), \
                patch("victus_hub.api._migrate_local_to_daemon") as migrate, \
                patch("victus_hub.api._hydrate_local_from_daemon") as hydrate:
            api.sync_settings_with_daemon()
            migrate.assert_called_once()
            hydrate.assert_not_called()

    def test_initialized_state_hydrates_ui(self):
        from victus_hub import api

        remote = {"initialized": True, "lighting": {"brightness": 64}}
        with patch("victus_hub.api.daemon_client.request_get_state", return_value=remote), \
                patch("victus_hub.api._migrate_local_to_daemon") as migrate, \
                patch("victus_hub.api._hydrate_local_from_daemon") as hydrate:
            api.sync_settings_with_daemon()
            hydrate.assert_called_once_with(remote)
            migrate.assert_not_called()


class TestAcOnline(unittest.TestCase):
    def test_mains_online_and_battery_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mains = root / "AC"
            mains.mkdir()
            (mains / "type").write_text("Mains\n")
            (mains / "online").write_text("1\n")
            with patch("victus_hubd.host._POWER_SUPPLY", root):
                self.assertTrue(ac_online())
            (mains / "online").write_text("0\n")
            with patch("victus_hubd.host._POWER_SUPPLY", root):
                self.assertFalse(ac_online())


class TestLightingOffCoalesces(unittest.TestCase):
    def test_disabled_lighting_writes_brightness_once(self):
        from victus_hubd.runtime import Runtime

        with patch("victus_hubd.runtime.load_state", return_value=DaemonState()), \
                patch("victus_hubd.runtime.save_state"), \
                patch("victus_hubd.runtime.sysfs") as sysfs:
            sysfs.get_keyboard_zone_count.return_value = 1
            runtime = Runtime()
            runtime._lighting_tick()
            runtime._lighting_tick()
            runtime._lighting_tick()
            self.assertEqual(sysfs.write_keyboard_brightness.call_count, 1)


class TestRuntimeFanMode(unittest.TestCase):
    def test_set_fan_config_applies_max_without_ui(self):
        from victus_hubd.runtime import Runtime

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with patch("victus_hubd.runtime.load_state", return_value=DaemonState()), \
                    patch("victus_hubd.runtime.save_state"), \
                    patch("victus_hubd.runtime.sysfs") as sysfs, \
                    patch("victus_hubd.state.state_path", return_value=path):
                sysfs.get_keyboard_zone_count.return_value = 1
                runtime = Runtime()
                config = config_from_dict({
                    "manual_preset": "max",
                    "custom_curve_enabled": False,
                })
                runtime.set_fan_config(config)
                sysfs.write_pwm_max.assert_called_once()
                self.assertTrue(runtime.snapshot().initialized)


class TestRuntimeRegressions(unittest.TestCase):
    def setUp(self):
        from victus_hubd.runtime import Runtime

        self.state = DaemonState()
        self.addCleanup(patch.stopall)
        patch("victus_hubd.runtime.load_state", return_value=self.state).start()
        patch("victus_hubd.runtime.save_state").start()
        self.profile = patch("victus_hubd.runtime.host.read_profile_index", return_value=0).start()
        self.hw = patch("victus_hubd.runtime.sysfs").start()
        self.hw.get_keyboard_zone_count.return_value = 1
        self.runtime = Runtime()

    def test_sleep_during_temperature_read_prevents_late_fan_writes(self):
        self.state.fan = config_from_dict({"custom_curve_enabled": True})

        def sample(**kwargs):
            self.runtime.prepare_sleep()
            return 70.0, 60.0

        with patch("victus_hubd.runtime.temps.read_cpu_gpu_temps", side_effect=sample), \
                patch("victus_hubd.runtime.read_hp_pwm_pct", return_value=30.0):
            self.runtime._fan._io.read_pwm_pct = lambda: 30.0
            self.runtime._fan._poll_once()
        self.hw.write_pwm_enable.assert_called_once_with(2)
        self.hw.write_pwm.assert_not_called()

    def test_sleep_waits_for_inflight_lighting_write(self):
        self.state.lighting = lighting_from_dict({"enabled": True})
        entered = threading.Event()
        release = threading.Event()
        parked = threading.Event()

        def write(*args, **kwargs):
            entered.set()
            release.wait(2)

        self.hw.write_keyboard_color.side_effect = write
        tick = threading.Thread(target=self.runtime._lighting_tick)
        sleeper = threading.Thread(target=lambda: (self.runtime.prepare_sleep(), parked.set()))
        tick.start()
        try:
            self.assertTrue(entered.wait(1))
            sleeper.start()
            self.assertFalse(parked.wait(0.05))
        finally:
            release.set()
            tick.join(2)
            if sleeper.ident is not None:
                sleeper.join(2)
        self.assertTrue(parked.is_set())
        self.hw.write_keyboard_brightness.assert_called_once_with(0, quiet=True)
        self.hw.reset_mock()
        self.runtime._lighting_tick()
        self.hw.write_keyboard_color.assert_not_called()

    def test_restart_on_ac_restores_saved_profile_only_from_power_save(self):
        for current, expected in ((0, [2]), (1, [])):
            with self.subTest(current=current):
                self.runtime._on_battery = None
                self.state.battery_power_save = True
                self.state.profile_before_battery = 2
                self.profile.return_value = current
                with patch("victus_hubd.runtime.host.ac_online", return_value=True), \
                        patch("victus_hubd.runtime.profiles.apply_system_profile") as apply:
                    self.runtime._refresh_host(force=True)
                    self.assertEqual([c.args[0] for c in apply.call_args_list], expected)
                self.assertIsNone(self.state.profile_before_battery)

    def test_nvidia_policy_is_profile_dependent_and_persisted(self):
        self.runtime.set_disable_nvidia_queries(True)
        self.assertTrue(state_from_dict(state_to_dict(self.runtime.snapshot())).disable_nvidia_queries)
        with patch("victus_hubd.runtime.temps.read_cpu_gpu_temps") as read:
            self.runtime._read_temps()
            read.assert_called_with(disable_nvidia=True)
            self.runtime._profile = 1
            self.runtime._read_temps()
            read.assert_called_with(disable_nvidia=False)

    def test_host_event_reads_profile_once(self):
        self.profile.reset_mock()
        with patch("victus_hubd.runtime.host.ac_online", return_value=True):
            self.runtime._on_host_event()
        self.profile.assert_called_once_with(allow_cli=False)


class TestHostEventCoalescing(unittest.TestCase):
    def test_monitor_handles_complete_json_messages(self):
        stop = threading.Event()
        watcher = host.HostWatch(Mock(), stop)
        message = {"type": "signal", "payload": {"data": ["ActiveProfile", "balanced"]}}
        process = Mock()
        process.stdout = io.StringIO("monitor header\n" + json.dumps(message) + "\n")
        process.poll.return_value = 0
        with patch("victus_hubd.host.command_path", return_value=Path("/usr/bin/busctl")), \
                patch("victus_hubd.host.subprocess.Popen", return_value=process) as popen, \
                patch.object(watcher, "_notify", side_effect=stop.set) as notify:
            watcher._busctl_monitor()
            notify.assert_called_once()
            self.assertIn("--json=short", popen.call_args.args[0])

    def test_burst_of_notifications_refreshes_once(self):
        stop = threading.Event()
        callback = Mock(side_effect=stop.set)
        watcher = host.HostWatch(callback, stop)
        for _ in range(20):
            watcher._notify()
        thread = threading.Thread(target=watcher._dispatch_events)
        thread.start()
        thread.join(2)
        stop.set()
        self.assertFalse(thread.is_alive())
        callback.assert_called_once()
