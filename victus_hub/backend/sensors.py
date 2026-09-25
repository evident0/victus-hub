"""Sensor reads for the keys the GUI asked the daemon to sample."""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from victus_hub.backend import temps
from victus_hub.backend.rapl import CpuPowerSample, RaplPowerSampler
from victus_hub.backend.sensor_keys import GPU_QUERY_KEYS
from victus_hub.backend.types import ExtraSensor, SensorReading, SensorSnapshot
from victus_hub.backend.sysfs_read import find_hwmon_by_name, iter_hwmon_dirs, read_int, read_text
from victus_hub.backend.util import command_path

CPU_ROOT = Path("/sys/devices/system/cpu")


def reading(value: str, source: str = "") -> SensorReading:
    return SensorReading(value=value, source=source)


# ── CpuUsageReader (cpu_usage.rs) ──

@dataclass
class _CpuTicks:
    user: int
    nice: int
    system: int
    idle: int
    iowait: int
    irq: int
    softirq: int
    steal: int


class _CpuUsageReader:
    def __init__(self):
        self.prev: _CpuTicks | None = None

    def read(self) -> float | None:
        current = _parse_proc_stat()
        if current is None:
            return None
        prev = self.prev
        self.prev = current
        if prev is None:
            return None

        prev_idle = prev.idle + prev.iowait
        idle = current.idle + current.iowait
        prev_total = (
            prev.user + prev.nice + prev.system + prev_idle
            + prev.irq + prev.softirq + prev.steal
        )
        total = (
            current.user + current.nice + current.system + idle
            + current.irq + current.softirq + current.steal
        )
        total_delta = total - prev_total
        if total_delta < 0:
            total_delta = 0
        idle_delta = idle - prev_idle
        if idle_delta < 0:
            idle_delta = 0
        if total_delta == 0:
            return None
        return 100.0 * (1.0 - idle_delta / total_delta)


def _parse_proc_stat() -> _CpuTicks | None:
    try:
        content = Path("/proc/stat").read_text()
    except OSError:
        return None
    cpu_line = content.splitlines()[0]
    fields = cpu_line.split()[1:]
    nums = []
    for f in fields[:8]:
        try:
            nums.append(int(f))
        except ValueError:
            return None
    if len(nums) < 8:
        return None
    return _CpuTicks(
        user=nums[0], nice=nums[1], system=nums[2], idle=nums[3],
        iowait=nums[4], irq=nums[5], softirq=nums[6], steal=nums[7],
    )


# ── SensorReader ──

class SensorReader:
    def __init__(self):
        self._rapl_sampler = RaplPowerSampler()
        self._cpu_usage_reader = _CpuUsageReader()

    # ── HP fans / PWM (hp.rs) ──

    def _format_rpm(self, value: int | None, source: Path) -> SensorReading:
        if value is None:
            return reading("Unavailable", str(source))
        return reading(f"{value} RPM", str(source))

    def _read_hp_fans(self, hp_hwmon: Path | None) -> tuple[SensorReading, SensorReading]:
        if hp_hwmon is None:
            u = reading("Unavailable", "hp hwmon not found")
            return u, u
        return (
            self._format_rpm(read_int(hp_hwmon / "fan1_input"), hp_hwmon / "fan1_input"),
            self._format_rpm(read_int(hp_hwmon / "fan2_input"), hp_hwmon / "fan2_input"),
        )

    def _read_hp_pwm(self, hp_hwmon: Path | None) -> tuple[SensorReading, SensorReading]:
        if hp_hwmon is None:
            u = reading("Unavailable", "hp hwmon not found")
            return u, u

        mode_path = hp_hwmon / "pwm1_enable"
        value_path = hp_hwmon / "pwm1"
        raw = read_text(mode_path)
        if raw == "0":
            mode = reading("Max", str(mode_path))
        elif raw == "1":
            mode = reading("Manual", str(mode_path))
        elif raw == "2":
            mode = reading("Automatic", str(mode_path))
        elif raw is not None:
            mode = reading(raw, str(mode_path))
        else:
            mode = reading("Unavailable", str(mode_path))

        val = read_int(value_path)
        if val is not None:
            pwm_val = reading(f"{val} / 255", str(value_path))
        else:
            pwm_val = reading("Unavailable", str(value_path))
        return mode, pwm_val

    # ── CPU temp (temperature.rs) ──

    def _cpu_temp_candidates(self) -> list[tuple[int, Path, str]]:
        preferred_names = ["coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"]
        candidates = []

        for hwmon in iter_hwmon_dirs():
            name = (read_text(hwmon / "name") or "").lower()
            try:
                entries = list(hwmon.iterdir())
            except OSError:
                continue
            for path in entries:
                fname = path.name
                if not fname.startswith("temp") or not fname.endswith("_input"):
                    continue
                value = read_int(path)
                if value is None or value <= -100_000:
                    continue
                label_path = path.with_name(fname.replace("_input", "_label"))
                label = read_text(label_path) or ""
                label_lower = label.lower()
                if name in preferred_names or "package" in label_lower or "tctl" in label_lower:
                    display = label if label else name
                    candidates.append((value, path, display))
        return candidates

    def _read_cpu_temp(self) -> tuple[SensorReading, float | None]:
        candidates = self._cpu_temp_candidates()
        if not candidates:
            return reading("Unavailable", "no CPU temp hwmon"), None

        best = max(candidates, key=lambda x: x[0])
        value, source, label = best
        temp_c = value / 1000.0
        return reading(f"{temp_c:.1f} C", f"{label}: {str(source)}"), temp_c

    def _format_cpu_power_sample(self, sample: CpuPowerSample, source_prefix: str) -> SensorReading:
        if sample.kind == "watts":
            return reading(f"{sample.watts:.1f} W", f"{source_prefix}: {sample.source}")
        elif sample.kind == "sampling":
            return reading("Sampling...", f"{source_prefix}: {sample.source}")
        elif sample.kind == "unavailable":
            return reading("Unavailable", sample.message)
        raise ValueError(f"unknown CpuPowerSample kind: {sample.kind!r}")

    def _read_cpu_frequencies(self) -> list[ExtraSensor]:
        """Read current frequency per logical CPU; sysfs reports kHz."""
        sensors = []
        cpus = sorted(
            (path for path in CPU_ROOT.glob("cpu[0-9]*") if path.name[3:].isdigit()),
            key=lambda path: int(path.name[3:]),
        )
        for cpu in cpus:
            freq_dir = cpu / "cpufreq"
            source = freq_dir / "scaling_cur_freq"
            value = read_int(source)
            if value is None or value <= 0:
                source = freq_dir / "cpuinfo_cur_freq"
                value = read_int(source)
            if value is None or value <= 0:
                continue
            mhz = value / 1000.0
            maximum = read_int(freq_dir / "cpuinfo_max_freq")
            sensors.append(ExtraSensor(
                key=f"cpu-frequency-{cpu.name[3:]}",
                group="CPU",
                name=f"CPU {cpu.name[3:]} Frequency",
                unit="MHz",
                value_min=0,
                value_max=max(mhz, maximum / 1000.0 if maximum else 6000.0),
                numeric_value=mhz,
                reading=reading(f"{mhz:.1f} MHz", str(source)),
            ))
        return sensors

    # ── lm-sensors (lm.rs) ──

    def _read_lm_sensors(self) -> list[ExtraSensor]:
        cmd = command_path("sensors")
        if cmd is None:
            return []

        try:
            result = subprocess.run(
                [str(cmd), "-j"], capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []

        if result.returncode != 0:
            return []

        try:
            chips = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        if not isinstance(chips, dict):
            return []

        sensors = []
        for chip, chip_value in chips.items():
            if not isinstance(chip_value, dict):
                continue
            for feature, feature_value in chip_value.items():
                if feature == "Adapter":
                    continue
                if not isinstance(feature_value, dict):
                    continue
                for input_name, input_value in feature_value.items():
                    if not input_name.endswith("_input") and not input_name.endswith("_average"):
                        continue
                    if not isinstance(input_value, (int, float)):
                        continue
                    value = float(input_value)

                    unit_info = _unit_for_input(input_name)
                    if unit_info is None:
                        continue
                    unit, value_min, value_max = unit_info

                    if _is_duplicate_builtin(chip, feature, input_name) or _invalid_temperature(input_name, value):
                        continue

                    group = _sensor_group(chip)
                    name = _sensor_name(chip, feature, unit)
                    sensors.append(ExtraSensor(
                        key=f"lm-{_slug(chip)}-{_slug(feature)}",
                        group=group,
                        name=name,
                        unit=unit,
                        value_min=value_min,
                        value_max=value_max,
                        numeric_value=value,
                        reading=reading(_format_value(value, unit), f"sensors: {chip} / {feature}"),
                    ))

        return sensors


    # ── RAM (proc/meminfo) ──

    def _read_ram(self) -> tuple[SensorReading, float | None, float | None, float | None]:
        """Return (reading, usage_pct, used_gb, total_gb) from /proc/meminfo."""
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        key = parts[0].strip()
                        val_str = parts[1].strip().split()[0]
                        try:
                            meminfo[key] = int(val_str)
                        except ValueError:
                            pass
        except OSError:
            return reading("N/A", "proc/meminfo"), None, None, None

        total_kb = meminfo.get("MemTotal")
        available_kb = meminfo.get("MemAvailable")
        if total_kb is None or available_kb is None or total_kb == 0:
            return reading("N/A", "proc/meminfo"), None, None, None

        used_kb = total_kb - available_kb
        usage_pct = used_kb / total_kb * 100.0
        total_gb = total_kb / (1024 * 1024)
        used_gb = used_kb / (1024 * 1024)
        return reading(f"{used_gb:.1f} GB", "proc/meminfo"), usage_pct, used_gb, total_gb

    def read_requested(
        self,
        keys,
        *,
        read_cpu_power: Callable[[], CpuPowerSample] | None = None,
        disable_nvidia: bool = False,
    ) -> SensorSnapshot:
        """Read only the informational keys the GUI asked for.

        An empty set performs no hardware reads. GPU power and utilization
        are not queried unless those keys are present.
        """
        wanted = frozenset(keys)
        snap = SensorSnapshot()
        if not wanted:
            return snap

        if "cpu-temp" in wanted:
            snap.cpu_temp, snap.cpu_temp_c = self._read_cpu_temp()

        if "cpu-usage" in wanted:
            pct = self._cpu_usage_reader.read()
            snap.cpu_usage_pct = pct
            snap.cpu_usage = (
                reading(f"{pct:.1f} %", "proc/stat")
                if pct is not None
                else reading("N/A", "proc/stat")
            )

        if "cpu-power" in wanted:
            if read_cpu_power is not None:
                sample = read_cpu_power()
                source = "victus-hubd"
            else:
                sample = self._rapl_sampler.read()
                source = "direct RAPL"
            snap.cpu_power = self._format_cpu_power_sample(sample, source)

        if wanted & {"cpu-fan", "gpu-fan", "pwm-value", "pwm-mode"}:
            hp_hwmon = find_hwmon_by_name("hp", "hp_wmi", "hp-wmi")
            if wanted & {"cpu-fan", "gpu-fan"}:
                cpu_fan, gpu_fan = self._read_hp_fans(hp_hwmon)
                if "cpu-fan" in wanted:
                    snap.cpu_fan = cpu_fan
                if "gpu-fan" in wanted:
                    snap.gpu_fan = gpu_fan
            if wanted & {"pwm-value", "pwm-mode"}:
                pwm_mode, pwm_value = self._read_hp_pwm(hp_hwmon)
                if "pwm-mode" in wanted:
                    snap.pwm_mode = pwm_mode
                if "pwm-value" in wanted:
                    snap.pwm_value = pwm_value

        if "ram-usage" in wanted:
            snap.ram_usage, snap.ram_usage_pct, snap.ram_used_gb, snap.ram_total_gb = self._read_ram()

        extras: list[ExtraSensor] = []
        if "cpu-frequency" in wanted:
            extras.extend(self._read_cpu_frequencies())
        if "lm-sensors" in wanted:
            extras.extend(self._read_lm_sensors())
        snap.extra_sensors = extras

        if wanted & GPU_QUERY_KEYS:
            self._fill_gpu(snap, wanted, disable_nvidia=disable_nvidia)
        return snap

    def _fill_gpu(self, snap: SensorSnapshot, wanted: frozenset[str], *, disable_nvidia: bool) -> None:
        present = temps._has_nvidia()
        suspended = present and temps.dgpu_runtime_suspended()
        if present and (disable_nvidia or suspended):
            temps.query_nvidia_fields(temperature=True, disable_nvidia=disable_nvidia)
            text = "Disabled" if disable_nvidia else "Suspended"
            source = (
                "NVIDIA queries disabled in Settings"
                if disable_nvidia
                else "dGPU runtime PM (not woken)"
            )
            if "gpu-temp" in wanted:
                snap.gpu_temp = reading(text, source)
            if "gpu-power" in wanted:
                snap.gpu_power = reading(text, source)
            if "gpu-usage" in wanted:
                snap.gpu_usage = reading(text, source)
            return

        if not present:
            if "gpu-temp" in wanted:
                snap.gpu_temp, snap.gpu_temp_c = self._read_amd_gpu_temp()
            if "gpu-power" in wanted:
                snap.gpu_power = self._read_amd_gpu_power()
            if "gpu-usage" in wanted:
                snap.gpu_usage = reading("N/A", "dGPU off or unavailable")
            return

        fields = temps.query_nvidia_fields(
            temperature="gpu-temp" in wanted,
            power="gpu-power" in wanted,
            utilization="gpu-usage" in wanted,
        )
        if "gpu-temp" in wanted:
            if fields.temp_c is None:
                snap.gpu_temp = reading("Unavailable", "no NVIDIA temp (hwmon/NVML/smi)")
            else:
                snap.gpu_temp_c = fields.temp_c
                snap.gpu_temp = reading(f"{fields.temp_c:.0f} C", fields.temp_source or "nvml")
        if "gpu-power" in wanted:
            if fields.power_w is None:
                snap.gpu_power = reading("Unavailable", "no NVIDIA power (hwmon/NVML/smi)")
            else:
                snap.gpu_power = reading(f"{fields.power_w:.1f} W", fields.power_source or "nvml")
        if "gpu-usage" in wanted:
            if fields.util_pct is None:
                snap.gpu_usage = reading("Unavailable", "no NVIDIA utilization (NVML/smi)")
            else:
                snap.gpu_usage_pct = fields.util_pct
                snap.gpu_usage = reading(f"{fields.util_pct:.0f} %", fields.util_source or "nvml")

    def _read_amd_gpu_temp(self) -> tuple[SensorReading, float | None]:
        for hwmon in iter_hwmon_dirs():
            name = (read_text(hwmon / "name") or "").lower()
            if name not in {"amdgpu", "nouveau"}:
                continue
            found = []
            try:
                entries = list(hwmon.iterdir())
            except OSError:
                continue
            for path in entries:
                fname = path.name
                if not fname.startswith("temp") or not fname.endswith("_input"):
                    continue
                value = read_int(path)
                if value is not None:
                    label = read_text(path.with_name(fname.replace("_input", "_label"))) or name
                    found.append((value, path, label))
            if found:
                value, source, label = max(found, key=lambda item: item[0])
                temp_c = value / 1000.0
                return reading(f"{temp_c:.1f} C", f"{label}: {source}"), temp_c
        return reading("Unavailable", "no GPU temp sensor"), None

    def _read_amd_gpu_power(self) -> SensorReading:
        for hwmon in iter_hwmon_dirs():
            if (read_text(hwmon / "name") or "").lower() not in {"amdgpu", "nouveau"}:
                continue
            try:
                entries = list(hwmon.iterdir())
            except OSError:
                continue
            for path in entries:
                fname = path.name
                if fname.startswith("power") and fname.endswith("_input"):
                    value = read_int(path)
                    if value is not None:
                        return reading(f"{value / 1_000_000:.1f} W", str(path))
        return reading("Unavailable", "no GPU power sensor")


# ── lm-sensors helpers (lm.rs standalone fns) ──

def _unit_for_input(input_name: str) -> tuple[str, float, float] | None:
    parts = input_name.split("_", 1)
    if not parts:
        return None
    prefix = parts[0]
    if prefix.startswith("temp"):
        return ("°C", 0.0, 100.0)
    elif prefix.startswith("fan"):
        return ("RPM", 0.0, 6000.0)
    elif prefix.startswith("power"):
        return ("W", 0.0, 120.0)
    elif prefix.startswith("in"):
        return ("V", 0.0, 20.0)
    elif prefix.startswith("curr"):
        return ("A", 0.0, 10.0)
    return None


def _is_duplicate_builtin(chip: str, feature: str, input_name: str) -> bool:
    return (
        chip.startswith("hp-isa-")
        or chip.startswith("k10temp-")
        or (chip.startswith("amdgpu-") and feature in ("edge", "PPT"))
        or (chip.startswith("BAT") and input_name.startswith("power"))
    )


def _invalid_temperature(input_name: str, value: float) -> bool:
    return input_name.startswith("temp") and value <= -100.0


def _sensor_group(chip: str) -> str:
    if chip.startswith("nvme-"):
        return "Drives"
    elif chip.startswith("spd"):
        return "Memory"
    elif chip.startswith("mt7921"):
        return "Network"
    elif chip.startswith("BAT"):
        return "Battery"
    elif chip.startswith("ucsi_source"):
        return "USB-C"
    elif chip.startswith("amdgpu-"):
        return "GPU"
    elif chip.startswith("acpitz-"):
        return "ACPI"
    else:
        return "Other"


def _sensor_name(_chip: str, feature: str, unit: str) -> str:
    metric_map = {"°C": "Temp", "RPM": "Fan", "W": "Power", "V": "Voltage", "A": "Current"}
    metric = metric_map.get(unit, feature)
    return f"{feature} {metric}"


def _format_value(value: float, unit: str) -> str:
    if unit == "RPM":
        return f"{value:.0f} RPM"
    elif unit == "°C":
        return f"{value:.1f} °C"
    elif unit == "W":
        return f"{value:.1f} W"
    elif unit == "V":
        return f"{value:.2f} V"
    elif unit == "A":
        return f"{value:.2f} A"
    else:
        return f"{value:.1f}"


def _slug(value: str) -> str:
    return "".join(c.lower() if c.isascii() and c.isalnum() else "-" for c in value)
