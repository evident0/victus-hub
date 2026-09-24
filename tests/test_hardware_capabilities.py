"""Exposed controls drive the daemon's hardware profile and the UI."""

import json
import os
from unittest.mock import Mock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from victus_hub import api
from victus_hub.backend import hardware_capabilities as hardware
from victus_hub.backend import modules
from victus_hub.backend.fan_config import config_from_dict
from victus_hub.features.gpu.mux import GpuMuxMode, GpuMuxState
from victus_hub.pages import fans_page
from victus_hub.pages.home_page import HomePage
from victus_hub.widgets.profile_section import ProfileSection
from victus_hub.widgets.sidebar import Sidebar
from victus_hubd import daemon, sysfs
from victus_hubd.state import DaemonState


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("nodes,expected", [
    ((), ("auto",)),
    (("pwm1_enable",), ("auto", "max")),
    (("pwm1",), ("auto",)),
    (("pwm1_enable", "pwm1"), ("auto", "smart", "max", "custom")),
])
def test_fan_modes_follow_both_sysfs_controls(tmp_path, monkeypatch, nodes, expected):
    hwmon = tmp_path / "hp"
    hwmon.mkdir()
    for node in nodes:
        (hwmon / node).touch()
    monkeypatch.setattr(hardware, "find_hwmon_by_name", lambda *names: hwmon)
    monkeypatch.setattr(hardware, "keyboard_lighting_supported", lambda: False)
    monkeypatch.setattr(hardware, "read_gpu_mux_state", lambda: None)
    monkeypatch.setattr(sysfs, "hp_hwmon", lambda: hwmon)
    assert hardware.detect_capabilities().fan_modes == expected
    assert sysfs.manual_fan_supported() == ("pwm1_enable" in nodes and "pwm1" in nodes)
    with patch.object(modules, "find_hwmon_by_name", return_value=hwmon):
        assert modules.fan_control_module()[0] == (
            "hp-wmi" if "custom" in expected else "limited"
        )


def test_no_hp_hwmon_exposes_only_auto(monkeypatch):
    monkeypatch.setattr(hardware, "find_hwmon_by_name", lambda *names: None)
    monkeypatch.setattr(hardware, "keyboard_lighting_supported", lambda: False)
    monkeypatch.setattr(hardware, "read_gpu_mux_state", lambda: None)
    assert hardware.detect_capabilities().fan_modes == ("auto",)


def test_keyboard_requires_usable_hp_rgb_led(tmp_path, monkeypatch):
    leds = tmp_path / "leds"
    leds.mkdir()
    path_class = hardware.Path
    monkeypatch.setattr(hardware, "Path", lambda path: leds if path == "/sys/class/leds" else path_class(path))
    unrelated = leds / "input3::capslock"
    unrelated.mkdir()
    (unrelated / "multi_intensity").touch()
    (unrelated / "brightness").touch()
    assert not hardware.keyboard_lighting_supported()
    zoned = leds / "hp::kbd_backlight_zoned_backlight-left"
    zoned.mkdir()
    (zoned / "multi_intensity").touch()
    assert not hardware.keyboard_lighting_supported()
    (zoned / "brightness").touch()
    assert hardware.keyboard_lighting_supported()
    with patch.object(modules, "emulated_keyboard_zone_count", return_value=None):
        assert modules.keyboard_rgb_module()[0] == "hp-kbd-rgb"


def test_daemon_get_state_supplies_nonpersistent_hardware_profile(monkeypatch):
    mux = GpuMuxState((GpuMuxMode("hybrid", 0, "Hybrid"),), 0)
    profile = hardware.HardwareCapabilities(("auto", "max"), True, mux)
    monkeypatch.setattr(hardware, "detect_capabilities", lambda: profile)
    runtime = Mock()
    runtime.snapshot.return_value = DaemonState()
    handlers = dict(daemon._make_dispatch(Mock(), None, runtime))
    raw = handlers["get-state"]("")
    assert json.loads(raw.removeprefix("OK\t"))["capabilities"] == profile.to_dict()
    assert hardware.HardwareCapabilities.from_dict(profile.to_dict()) == profile
    assert "capabilities" not in daemon.state_to_dict(runtime.snapshot())


def test_ui_prefers_daemon_profile_and_falls_back_when_missing(monkeypatch):
    profile = hardware.HardwareCapabilities(("auto", "max"), False)
    monkeypatch.setattr(api.daemon_client, "request_get_state", lambda: {"capabilities": profile.to_dict()})
    monkeypatch.setattr(api, "detect_capabilities", lambda: pytest.fail("unnecessary local probe"))
    monkeypatch.delenv("VICTUS_HUB_EMULATE_ZONES", raising=False)
    assert api.get_hardware_capabilities() == profile
    monkeypatch.setenv("VICTUS_HUB_EMULATE_ZONES", "4")
    assert api.get_hardware_capabilities().keyboard_lighting
    monkeypatch.setattr(api, "detect_capabilities", lambda: profile)
    monkeypatch.setattr(api.daemon_client, "request_get_state", lambda: {})
    assert api.get_hardware_capabilities().fan_modes == ("auto", "max")


def test_filtered_ui_preserves_fan_page_and_hides_lighting(app):
    modes = ("auto", "max")
    with patch.object(fans_page, "get_fan_config", return_value=config_from_dict({})):
        fans = fans_page.FansPage(fan_modes=modes)
    home = HomePage(fan_modes=modes, keyboard_lighting=False, gpu_mux=None)
    sidebar = Sidebar()
    try:
        assert [label.text() for label in fans._mode_seg._labels] == ["Auto", "Max"]
        fans.set_selected_fan_mode("custom")
        assert fans._fan_mode == "auto"
        assert fans._curve_editor.isHidden()
        fans._on_mode_seg(1)
        assert fans._fan_mode == "max"
        assert [label.text() for label in home._profile_section._fan_links._labels] == ["Auto", "Max"]
        assert home._light_row.isHidden()
        assert not home._profile_section._mux_block.isVisible()
        sidebar.set_tab_visible(3, False)
        assert sidebar._buttons[3].isHidden()
        sidebar.set_active(3)
        assert sidebar._active_index == 0
        sidebar.set_active(2)
        assert sidebar._active_index == 2
    finally:
        fans.close()
        home.close()
        sidebar.close()


def test_full_capabilities_keep_all_modes_and_lighting(app):
    with patch.object(fans_page, "get_fan_config", return_value=config_from_dict({})):
        fans = fans_page.FansPage(fan_modes=("auto", "smart", "max", "custom"))
    home = HomePage(fan_modes=("auto", "smart", "max", "custom"),
                    keyboard_lighting=True, gpu_mux=None)
    try:
        assert [label.text() for label in fans._mode_seg._labels] == [
            "Auto", "Smart", "Max", "Custom",
        ]
        fans.set_selected_fan_mode("custom")
        assert not fans._curve_editor.isHidden()
        assert not home._light_row.isHidden()
        assert len(home._profile_section._fan_links._labels) == 4
    finally:
        fans.close()
        home.close()
