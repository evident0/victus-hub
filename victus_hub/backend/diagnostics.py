"""Collect a markdown diagnostics report for Settings."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

from victus_hub.backend.daemon_client import SOCKET_PATH
from victus_hub.backend.cpu import is_intel_cpu
from victus_hub.backend.modules import (
    emulated_keyboard_zone_count,
    fan_control_module,
    keyboard_rgb_module,
    mux_module,
    platform_profile_backend,
    ryzenadj_available,
)
from victus_hub.backend.profiles import current_profile_reading, tuned_adm_path
from victus_hub.backend.session_log import lines as session_log_lines
from victus_hub.backend.sysfs_read import find_hwmon_by_name, read_text
from victus_hub.backend.util import command_path, run_command
from victus_hub.features.gpu.mux import read_gpu_mux_state

_DMI = Path("/sys/devices/virtual/dmi/id")
_ACPI_PROFILE = Path("/sys/firmware/acpi/platform_profile")
_ACPI_PROFILE_CHOICES = Path("/sys/firmware/acpi/platform_profile_choices")
_KBD_RGB = Path("/sys/devices/platform/hp-kbd-rgb")
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

_KERNEL_MODULES = (
    "hp_wmi",
    "hp_kbd_rgb",
    "nvidia",
    "amdgpu",
)
_DAEMON_LOG_NOISE = (
    "keyboard-last-event",
    "keyboard-last-input",
    "[cpu-power]",
)


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    details: str = ""


def program_version() -> str:
    try:
        return pkg_version("victus-hub")
    except PackageNotFoundError:
        return "0.1.0"


def documents_dir() -> Path:
    """User Documents folder (xdg-user-dir when available)."""
    cmd = command_path("xdg-user-dir")
    if cmd is not None:
        out = run_command(cmd, ["DOCUMENTS"])
        if out:
            return Path(out)
    return Path.home() / "Documents"


def write_diagnostics_report(dest_dir: Path | None = None) -> Path:
    """Write a timestamped markdown report and return its path."""
    folder = dest_dir if dest_dir is not None else documents_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = folder / f"victus-hub-diagnostics-{stamp}.md"
    path.write_text(build_report(), encoding="utf-8")
    return path


def build_report() -> str:
    return render_markdown(
        generated=datetime.now(),
        system=collect_system_info(),
        capabilities=collect_capabilities(),
        modules=collect_kernel_modules(),
        session_log=session_log_lines(),
        daemon_log=collect_daemon_journal(),
    )


def collect_system_info() -> list[tuple[str, str]]:
    bios_ver = _dmi("bios_version")
    bios_date = _dmi("bios_date")
    bios = bios_ver or "unknown"
    if bios_ver and bios_date:
        bios = f"{bios_ver} ({bios_date})"

    rows: list[tuple[str, str]] = [
        ("Program version", program_version()),
        ("Python", platform.python_version()),
        ("Qt", _qt_version()),
        ("Vendor", _dmi("sys_vendor") or "unknown"),
        ("Model", _dmi("product_name") or "unknown"),
        ("SKU", _dmi("product_sku") or "unknown"),
        ("Board", _dmi("board_name") or "unknown"),
        ("BIOS", bios),
        ("BIOS vendor", _dmi("bios_vendor") or "unknown"),
        ("OS", _os_pretty_name()),
        ("Kernel", f"{platform.release()} ({platform.machine()})"),
        ("Secure Boot", _secure_boot()),
        ("CPU", _cpu_model() or "unknown"),
        ("GPU", _gpu_name() or "unknown"),
        ("Platform profile tool", _profile_tool_summary()),
        ("Userspace profile", _userspace_profile()),
        ("ACPI platform profile", _acpi_profile_summary()),
        ("Daemon socket", _daemon_socket_status()),
    ]
    return rows


def collect_capabilities() -> list[Capability]:
    choices = _acpi_profile_choices()
    if choices:
        profile_details = ", ".join(choices)
        profile_ok = True
    else:
        profile_details = "sysfs platform_profile not exposed"
        profile_ok = False

    fan_label, _fan_color = fan_control_module()
    hwmon = find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")
    if fan_label == "hp-wmi":
        custom_ok = True
        custom_details = "hp-wmi pwm1_enable (auto / max / custom)"
        auto_details = "hp-wmi hwmon"
    elif fan_label == "limited":
        custom_ok = False
        custom_details = "hp-wmi present; auto / max only (no pwm1_enable)"
        auto_details = "hp-wmi hwmon (no pwm1_enable)"
    else:
        custom_ok = False
        custom_details = "no hp-wmi hwmon"
        auto_details = "no hp-wmi hwmon"

    mux_label, _mux_color = mux_module()
    mux_state = read_gpu_mux_state()
    if mux_state is not None:
        names = ", ".join(m.name for m in mux_state.modes) or "none"
        current = "unknown"
        for m in mux_state.modes:
            if m.index == mux_state.current_index:
                current = m.name
                break
        mux_ok, mux_details = True, f"{names} (current: {current})"
    elif mux_label != "not supported":
        mux_ok, mux_details = False, "hp-wmi MUX state unreadable"
    else:
        mux_ok, mux_details = False, "hp-wmi MUX unavailable"

    kbd_label, _kbd_color = keyboard_rgb_module()
    emulated_zones = emulated_keyboard_zone_count()
    if emulated_zones is not None:
        kbd_ok, kbd_details = True, f"{emulated_zones} zone(s), emulated"
    elif kbd_label != "not supported" and _KBD_RGB.is_dir():
        zones = read_text(_KBD_RGB / "zone_count") or "unknown"
        kbd_type = read_text(_KBD_RGB / "keyboard_type") or "unknown"
        kbd_ok, kbd_details = True, f"{zones} zone(s), type {kbd_type}"
    else:
        kbd_ok, kbd_details = False, "hp-kbd-rgb not loaded"

    if is_intel_cpu():
        rapl = [
            path for path in Path("/sys/class/powercap").glob("intel-rapl:*")
            if path.name.count(":") == 1
        ]
        power_capability = Capability(
            "Power limits (Intel RAPL)", bool(rapl),
            "RAPL packages exposed; write support checked on apply" if rapl else "Intel RAPL unavailable",
        )
    else:
        ry_label, _ry_color = ryzenadj_available()
        ry_ok = ry_label != "not supported"
        power_capability = Capability(
            "Power limits (ryzenadj)", ry_ok, "ryzenadj found" if ry_ok else "ryzenadj not found",
        )

    hp_wmi = Path("/sys/module/hp_wmi").is_dir()
    acpi_ec = _acpi_ec_present()
    if hp_wmi or hwmon is not None:
        ec_ok = True
        parts = []
        if hp_wmi:
            parts.append("hp_wmi module")
        if hwmon is not None:
            parts.append(f"hwmon {hwmon.name}")
        if acpi_ec:
            parts.append("ACPI EC (PNP0C09)")
        ec_details = ", ".join(parts)
    else:
        ec_ok = acpi_ec
        ec_details = "ACPI EC (PNP0C09)" if acpi_ec else "no hp-wmi / ACPI EC found"

    sock = Path(SOCKET_PATH)
    daemon_ok = sock.exists()
    daemon_details = str(sock) if daemon_ok else f"{sock} missing"

    return [
        Capability("Platform profiles (perf / bal / cool)", profile_ok, profile_details),
        Capability("Custom fan control", custom_ok, custom_details),
        Capability("Fan auto / max", hwmon is not None, auto_details),
        Capability("Embedded Controller (hp-wmi)", ec_ok, ec_details),
        Capability("GPU MUX", mux_ok, mux_details),
        Capability("Keyboard RGB", kbd_ok, kbd_details),
        power_capability,
        Capability("Daemon (victus-hubd)", daemon_ok, daemon_details),
    ]


def collect_kernel_modules() -> list[tuple[str, bool]]:
    return [
        (name, Path(f"/sys/module/{name}").is_dir())
        for name in _KERNEL_MODULES
    ]


def collect_daemon_journal(n: int = 80) -> str | None:
    cmd = command_path("journalctl")
    if cmd is None:
        return None
    out = run_command(
        cmd,
        ["-u", "victus-hubd.service", "-n", "400", "--no-pager", "-o", "short-iso"],
    )
    if not out:
        return None
    kept = [
        line for line in out.splitlines()
        if not any(noise in line for noise in _DAEMON_LOG_NOISE)
    ]
    if not kept:
        return None
    return "\n".join(kept[-n:])


def render_markdown(
    *,
    generated: datetime,
    system: list[tuple[str, str]],
    capabilities: list[Capability],
    modules: list[tuple[str, bool]],
    session_log: list[str],
    daemon_log: str | None,
) -> str:
    lines = [
        "# Victus Hub diagnostics",
        "",
        f"Generated: {generated.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## System",
        "",
        "| | |",
        "| --- | --- |",
    ]
    for key, value in system:
        lines.append(f"| {_cell(key)} | {_cell(value)} |")

    lines += [
        "",
        "## Capabilities",
        "",
        "| Capability | Available | Details |",
        "| --- | --- | --- |",
    ]
    for cap in capabilities:
        avail = "yes" if cap.available else "no"
        lines.append(
            f"| {_cell(cap.name)} | {avail} | {_cell(cap.details)} |"
        )

    lines += [
        "",
        "## Kernel modules",
        "",
        "| Module | Loaded |",
        "| --- | --- |",
    ]
    for name, loaded in modules:
        lines.append(f"| `{name}` | {'yes' if loaded else 'no'} |")

    lines += ["", "## Session log", ""]
    if session_log:
        lines.append("```")
        lines.extend(_ANSI_RE.sub("", line) for line in session_log)
        lines.append("```")
    else:
        lines.append("_No session log captured._")

    lines += ["", "## Daemon journal", ""]
    if daemon_log:
        lines.append("```")
        lines.append(daemon_log)
        lines.append("```")
    else:
        lines.append("_Daemon journal not available._")

    lines.append("")
    return "\n".join(lines)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip() or "—"


def _dmi(name: str) -> str | None:
    return read_text(_DMI / name)


def _os_pretty_name() -> str:
    text = read_text(Path("/etc/os-release"))
    if not text:
        return " ".join(platform.uname())
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return platform.platform()


def _cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def _gpu_name() -> str | None:
    try:
        from victus_hub.backend.nvidia import get_gpu_name
        return get_gpu_name()
    except Exception:
        return None


def _qt_version() -> str:
    try:
        from PySide6.QtCore import qVersion
        return qVersion()
    except Exception:
        return "unknown"


def _secure_boot() -> str:
    if not Path("/sys/firmware/efi").is_dir():
        return "not available (legacy BIOS)"
    efivars = Path("/sys/firmware/efi/efivars")
    try:
        matches = sorted(efivars.glob("SecureBoot-*"))
    except OSError:
        matches = []
    for path in matches:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) >= 5:
            return "enabled" if data[4] == 1 else "disabled"
        if data:
            return "enabled" if data[-1] == 1 else "disabled"
    mok = command_path("mokutil")
    if mok is not None:
        out = run_command(mok, ["--sb-state"])
        if out:
            low = out.lower()
            if "enabled" in low:
                return "enabled"
            if "disabled" in low:
                return "disabled"
            return out.splitlines()[0]
    return "unknown"


def _profile_tools() -> list[str]:
    tools: list[str] = []
    if tuned_adm_path() is not None:
        tools.append("tuned")
    if command_path("powerprofilesctl") is not None:
        tools.append("power-profiles-daemon")
    return tools


def _profile_tool_summary() -> str:
    tools = _profile_tools()
    if not tools:
        return "none"
    if len(tools) == 1:
        return tools[0]
    return f"{', '.join(tools)} (active backend: {platform_profile_backend()[0]})"


def _userspace_profile() -> str:
    reading = current_profile_reading()
    if reading.value in ("Unavailable", ""):
        return f"unavailable ({reading.source})"
    return f"{reading.value} ({reading.source})"


def _acpi_profile_choices() -> list[str]:
    raw = read_text(_ACPI_PROFILE_CHOICES)
    if not raw:
        return []
    return raw.split()


def _acpi_profile_summary() -> str:
    current = read_text(_ACPI_PROFILE)
    choices = _acpi_profile_choices()
    if current and choices:
        return f"{current} (choices: {', '.join(choices)})"
    if current:
        return current
    if choices:
        return f"unknown (choices: {', '.join(choices)})"
    return "not exposed"


def _daemon_socket_status() -> str:
    sock = Path(SOCKET_PATH)
    return f"connected ({sock})" if sock.exists() else f"not running ({sock})"


def _acpi_ec_present() -> bool:
    acpi = Path("/sys/bus/acpi/devices")
    if not acpi.is_dir():
        return False
    try:
        for entry in acpi.iterdir():
            if entry.name.startswith("PNP0C09"):
                return True
            hid = read_text(entry / "hid")
            if hid and "PNP0C09" in hid:
                return True
    except OSError:
        return False
    return False
