"""Exercise the driver's fan setup with mocked firmware, without loading it."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestHpWmiFanSettings(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "requires a host C compiler")
    def test_firmware_probe_and_fallback_paths(self):
        source = (Path(__file__).resolve().parents[1] / "kernel/hp-wmi/hp-wmi.c").read_text()

        def function(name):
            match = re.search(r"^static [^\n]*\b" + name + r"\([^;]*?\n\{.*?^}", source, re.M | re.S)
            self.assertIsNotNone(match, name)
            return match.group()

        def structure(name):
            match = re.search(r"^struct " + name + r" \{.*?^}[^;]*;", source, re.M | re.S)
            self.assertIsNotNone(match, name)
            return match.group()

        declarations = "\n".join(structure(name) for name in (
            "hp_wmi_fan_profile_params", "hp_wmi_hwmon_priv",
            "victus_s_fan_table_header", "victus_s_fan_table_entry", "victus_s_fan_table",
        ))
        fallback = re.search(r"^#define VICTUS_S_FALLBACK_MAX_RPM_FW[^\n]*", source, re.M).group()
        functions = "\n".join(function(name) for name in (
            "hp_wmi_fan_control_supported", "hp_wmi_get_active_fan_speed",
            "hp_wmi_set_fallback_fan_limits", "hp_wmi_fan_speed_probe",
            "hp_wmi_select_fan_reader", "hp_wmi_setup_fallback_fan_settings",
            "hp_wmi_setup_fan_settings",
        ))
        harness = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
struct mutex { int unused; };
struct delayed_work { int unused; };
#define __packed __attribute__((packed))
#define U8_MAX UINT8_MAX
#define CPU_FAN 0
#define GPU_FAN 1
#define PWM_MODE_AUTO 2
#define HPWMI_VICTUS_S_GET_FAN_TABLE_QUERY 0
#define HPWMI_GM 0
#define pr_warn(...) ((void)0)
#define pr_info(...) ((void)0)
""" + declarations + "\n" + fallback + r"""
static int modern_rpm[2], legacy_rpm[2], table_result;
static u8 table_data[128];
static bool force_fan_control_support;
static int modern_reader(int fan) { return modern_rpm[fan]; }
static int legacy_reader(int fan) { return legacy_rpm[fan]; }
static const struct hp_wmi_fan_profile_params victus_s_fan_profile_params = {
    .get_fan_speed = modern_reader, .fan_table = true,
};
static const struct hp_wmi_fan_profile_params legacy_fan_profile_params = {
    .get_fan_speed = legacy_reader,
};
static const struct hp_wmi_fan_profile_params *board_profile;
static const struct hp_wmi_fan_profile_params *hp_wmi_fan_profile(void)
{ return board_profile; }
static bool hp_wmi_fan_table_supported(void)
{ return board_profile && board_profile->fan_table; }
static int hp_wmi_perform_query(int query, int command, void *data, int in, int out)
{
    (void)query; (void)command; (void)in;
    assert(out == sizeof(table_data));
    memcpy(data, table_data, sizeof(table_data));
    return table_result;
}
""" + functions + r"""
int main(void)
{
    struct hp_wmi_hwmon_priv priv = {0};
    struct victus_s_fan_table *table = (void *)table_data;

    /* Unlisted boards get real manual availability and the legacy reader. */
    force_fan_control_support = true;
    legacy_rpm[0] = 2400; legacy_rpm[1] = 2600;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(hp_wmi_fan_control_supported(&priv));
    assert(priv.min_rpm == 0 && priv.max_rpm == 60);
    assert(hp_wmi_get_active_fan_speed(&priv, GPU_FAN) == 2600);

    /* Without force, an unlisted board retains monitoring only. */
    force_fan_control_support = false;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(!hp_wmi_fan_control_supported(&priv));

    /* A valid table keeps its measured limits without requiring force. */
    board_profile = &victus_s_fan_profile_params;
    table->header.num_fans = 2;
    table->entries[0] = (struct victus_s_fan_table_entry){20, 22, 30};
    table->entries[1] = (struct victus_s_fan_table_entry){55, 58, 40};
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(hp_wmi_fan_control_supported(&priv));
    assert(priv.min_rpm == 20 && priv.max_rpm == 55);

    /* A valid table must not prevent switching away from a broken reader. */
    modern_rpm[0] = modern_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &legacy_fan_profile_params);
    legacy_rpm[0] = legacy_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == -EOPNOTSUPP);
    force_fan_control_support = true;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.max_rpm == 55 && hp_wmi_fan_control_supported(&priv));
    legacy_rpm[0] = 2400; legacy_rpm[1] = 2600;

    /* Nonzero but bogus tables (8BBE-style 1800 RPM limit) use fallback. */
    table->entries[0] = (struct victus_s_fan_table_entry){10, 12, 30};
    table->entries[1] = (struct victus_s_fan_table_entry){18, 20, 40};
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.max_rpm == 60);
    force_fan_control_support = false;
    assert(hp_wmi_setup_fan_settings(&priv) == -EINVAL);

    /* Probe errors cannot succeed with unusable zero limits when force is off. */
    table_result = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == -EIO);
    assert(!hp_wmi_fan_control_supported(&priv));
    table_result = 3; /* Positive firmware status must become a Linux errno. */
    assert(hp_wmi_setup_fan_settings(&priv) == -EINVAL);
    table_result = 0;
    memset(table_data, 0, sizeof(table_data));
    assert(hp_wmi_setup_fan_settings(&priv) == -EINVAL);
    table->header.num_fans = 2; /* Header present, but no usable entries. */
    assert(hp_wmi_setup_fan_settings(&priv) == -EINVAL);

    /* One failed modern channel selects the working legacy reader for both. */
    force_fan_control_support = true;
    modern_rpm[0] = 2000; modern_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &legacy_fan_profile_params);
    assert(hp_wmi_fan_control_supported(&priv) && priv.max_rpm == 60);
    assert(hp_wmi_get_active_fan_speed(&priv, GPU_FAN) == 2600);

    /* Failed table query still uses working modern tachometers. */
    table_result = -EIO;
    modern_rpm[1] = 2800;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &victus_s_fan_profile_params);

    /* Unlisted cross-over BIOS: legacy fails, modern succeeds. */
    board_profile = NULL;
    legacy_rpm[0] = legacy_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(priv.fan_profile == &victus_s_fan_profile_params);

    /* Total probe failure may be forced, but read failures remain visible. */
    modern_rpm[0] = modern_rpm[1] = -EIO;
    assert(hp_wmi_setup_fan_settings(&priv) == 0);
    assert(hp_wmi_fan_control_supported(&priv) && priv.max_rpm == 60);
    assert(hp_wmi_get_active_fan_speed(&priv, CPU_FAN) == -EIO);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as directory:
            c_file = Path(directory) / "fan_settings.c"
            executable = Path(directory) / "fan_settings"
            c_file.write_text(harness)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                            str(c_file), "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)
