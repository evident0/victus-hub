"""Compile actual driver functions against firmware/workqueue mocks."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("cc"), "requires a host C compiler")
class TestHpWmiFanRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1] / "kernel/hp-wmi/hp-wmi.c").read_text()

    def function(self, name):
        match = re.search(r"^static [^\n]*\b" + name + r"\([^;]*?\n\{.*?^}",
                          self.source, re.M | re.S)
        self.assertIsNotNone(match, name)
        return match.group()

    def structure(self, name):
        match = re.search(r"^struct " + name + r" \{.*?^}[^;]*;", self.source, re.M | re.S)
        self.assertIsNotNone(match, name)
        return match.group()

    def run_c(self, source):
        with tempfile.TemporaryDirectory() as directory:
            c_file = Path(directory) / "test.c"
            executable = Path(directory) / "test"
            c_file.write_text(source)
            result = subprocess.run(
                ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                 str(c_file), "-o", str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mode_transitions_and_keep_alive(self):
        declarations = "\n".join(self.structure(name) for name in (
            "thermal_profile_params", "hp_wmi_fan_profile_params", "hp_wmi_hwmon_priv"))
        functions = "\n".join(self.function(name) for name in (
            "rpm_to_pwm", "pwm_to_rpm", "hp_wmi_fan_control_supported",
            "hp_wmi_get_active_fan_speed", "hp_wmi_fan_speed_max_set",
            "hp_wmi_fan_speed_set", "hp_wmi_restore_auto_fans",
            "hp_wmi_apply_fan_settings", "hp_wmi_commit_fan_settings",
            "hp_wmi_hwmon_write", "hp_wmi_hwmon_keep_alive_handler"))
        self.run_c(r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
typedef uint8_t u8;
struct mutex { int unused; };
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct device { void *data; };
#define dev_get_drvdata(dev) ((dev)->data)
#define guard(kind) mock_guard
#define mock_guard(lock) ((void)(lock))
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define to_delayed_work(ptr) container_of(ptr, struct delayed_work, work)
#define U8_MAX UINT8_MAX
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define CPU_FAN 0
#define GPU_FAN 1
#define PWM_MODE_MAX 0
#define PWM_MODE_MANUAL 1
#define PWM_MODE_AUTO 2
#define PLATFORM_PROFILE_PERFORMANCE 7
#define PLATFORM_PROFILE_BALANCED 8
#define PLATFORM_PROFILE_LOW_POWER 9
#define HP_FAN_SPEED_AUTOMATIC 0
#define HPWMI_FAN_SPEED_MAX_SET_QUERY 1
#define HPWMI_VICTUS_S_FAN_SPEED_SET_QUERY 2
#define HPWMI_GM 0
#define KEEP_ALIVE_DELAY_SECS 90
#define KEEP_ALIVE_RETRY_DELAY_SECS 5
#define secs_to_jiffies(x) (x)
#define system_dfl_wq NULL
#define pr_warn_ratelimited(...) ((void)0)
static int clamp_val(int x, int lo, int hi)
{ return x < lo ? lo : (x > hi ? hi : x); }
static int fixp_linear_interpolate(int x0, int y0, int x1, int y1, int x)
{ return y0 + (x - x0) * (y1 - y0) / (x1 - x0); }
enum hwmon_sensor_types { hwmon_pwm };
enum { hwmon_pwm_input, hwmon_pwm_enable };
typedef uint32_t u32;
""" + declarations + r"""
static bool force_fan_control_support = true, active_platform_profile_valid = true;
static int active_platform_profile = 7;
static struct mutex active_platform_profile_lock;
static int rpm[2] = {2400, 2600}, writes, fan_status, max_status, trigger_status;
static int queued, last_delay, cancelled, profile_calls, profile_status, profile_kind = 1;
static int generic_profile = 9, generic_reads;
static int trigger_calls, max_calls;
static bool firmware_user_defined, firmware_manual;
static u8 last_fans[2];
static int reader(int fan) { return rpm[fan]; }
static const struct hp_wmi_fan_profile_params fan_profile = {.get_fan_speed = reader};
static int hp_wmi_get_fan_count_userdefine_trigger(void)
{ trigger_calls++; if (trigger_status >= 0) firmware_user_defined = true; return trigger_status; }
static int hp_wmi_perform_query(int query, int cmd, void *buffer, int in, int out)
{
    (void)cmd; (void)in; (void)out;
    if (query == HPWMI_FAN_SPEED_MAX_SET_QUERY) { max_calls++; return max_status; }
    assert(query == HPWMI_VICTUS_S_FAN_SPEED_SET_QUERY);
    last_fans[0] = ((u8 *)buffer)[0]; last_fans[1] = ((u8 *)buffer)[1];
    writes++;
    if (!fan_status) firmware_manual = true;
    return fan_status;
}
static void mod_delayed_work(void *wq, struct delayed_work *work, int delay)
{ (void)wq; (void)work; assert(delay == 90 || delay == 5); last_delay = delay; queued++; }
static void cancel_delayed_work(struct delayed_work *work)
{ (void)work; cancelled++; }
static bool is_omen_thermal_profile(void) { return profile_kind == 1; }
static bool is_victus_thermal_profile(void) { return profile_kind == 2; }
static bool is_victus_s_thermal_profile(void) { return profile_kind == 3; }
static int set_profile(int kind, int profile)
{
    assert(kind == profile_kind); assert(profile == 7); profile_calls++;
    if (!profile_status) firmware_user_defined = firmware_manual = false;
    return profile_status;
}
static int platform_profile_omen_set_ec(int p) { return set_profile(1, p); }
static int platform_profile_victus_set_ec(int p) { return set_profile(2, p); }
static const struct thermal_profile_params thermal_params = {.performance = 1};
static const struct thermal_profile_params *hp_wmi_thermal_profile(void)
{ return &thermal_params; }
static int omen_thermal_profile_set(int p)
{ assert(profile_kind == 3 && p == 1); profile_calls++; return profile_status ? profile_status : p; }
static int thermal_profile_get(void) { generic_reads++; return generic_profile; }
static int thermal_profile_set(int p)
{ assert(p == generic_profile); profile_calls++; return profile_status; }
""" + functions + r"""
int main(void)
{
    struct hp_wmi_hwmon_priv priv = {
        .fan_profile = &fan_profile, .fan_control_available = true,
        .mode = PWM_MODE_AUTO, .max_rpm = 60,
    };
    struct device dev = {.data = &priv};
    int before, old_cpu, old_gpu;

    /* Forced manual entry works even when both tachometers fail. */
    rpm[0] = rpm[1] = -EIO;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == 0);
    assert(priv.mode == PWM_MODE_MANUAL);
    assert(last_fans[0] == 60 && last_fans[1] == 60);
    before = writes;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == 0);
    assert(writes == before); /* Repeated MANUAL preserves existing targets. */

    /* Positive firmware errors are rejected, and accepted targets survive. */
    fan_status = 3;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_input, 0, 128) == -EINVAL);
    assert(priv.cpu_pwm == 255 && priv.gpu_pwm == 255);
    fan_status = 0;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_input, 0, 128) == 0);
    assert(priv.cpu_pwm < 255 && priv.gpu_pwm == 255);
    old_cpu = priv.cpu_pwm; old_gpu = priv.gpu_pwm;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_input, 0, 256) == -EINVAL);
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_input, 0, -1) == -EINVAL);
    assert(priv.cpu_pwm == old_cpu && priv.gpu_pwm == old_gpu);

    /* AUTO reapplies the profile, never a zero-RPM manual command. */
    before = writes;
    profile_status = -EIO;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 2) == -EIO);
    assert(priv.mode == PWM_MODE_MANUAL && priv.cpu_pwm == old_cpu);
    assert(cancelled == 0 && writes == before);
    profile_status = 0;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 2) == 0);
    assert(priv.mode == PWM_MODE_AUTO && cancelled == 1 && writes == before);
    for (profile_kind = 2; profile_kind <= 3; profile_kind++)
        assert(hp_wmi_restore_auto_fans(&priv) == 0);
    profile_kind = 1;
    active_platform_profile_valid = false;
    before = trigger_calls;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == -EOPNOTSUPP);
    assert(trigger_calls == before && priv.mode == PWM_MODE_AUTO);
    /* A board with only MAX support can still clear that override. */
    assert(hp_wmi_restore_auto_fans(&priv) == 0);
    active_platform_profile_valid = true;
    profile_kind = 0;
    generic_profile = -EIO;
    before = profile_calls;
    assert(hp_wmi_restore_auto_fans(&priv) == -EIO && profile_calls == before);
    generic_profile = 9; profile_status = 3;
    assert(hp_wmi_restore_auto_fans(&priv) == -EINVAL);
    profile_status = 0;
    assert(hp_wmi_restore_auto_fans(&priv) == 0 && generic_reads == 3);

    /* 878A-style Max-only boards must not enter manual WMI or profile paths. */
    priv.fan_control_available = false;
    priv.fan_profile = NULL;
    before = writes;
    int old_profiles = profile_calls;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == -EOPNOTSUPP);
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 2) == 0);
    assert(writes == before && profile_calls == old_profiles);
    priv.fan_control_available = true;
    priv.fan_profile = &fan_profile;

    /* MAX remains available when its optional trigger is unsupported. */
    trigger_status = -EOPNOTSUPP;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 0) == 0);
    assert(priv.mode == PWM_MODE_MAX);
    max_status = -EIO;
    before = queued;
    hp_wmi_hwmon_keep_alive_handler(&priv.keep_alive_dwork.work);
    assert(queued == before + 1); /* Retry survives a transient refresh failure. */
    assert(last_delay == 5); /* Retry before the 120-second firmware watchdog. */
    priv.mode = PWM_MODE_AUTO;
    before = queued;
    hp_wmi_hwmon_keep_alive_handler(&priv.keep_alive_dwork.work);
    assert(queued == before); /* Stale work must not restore AUTO again. */
    max_status = 0; trigger_status = 0;

    force_fan_control_support = false;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == -EIO);
    assert(priv.mode == PWM_MODE_AUTO);
    force_fan_control_support = true; fan_status = 3;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == -EINVAL);
    assert(priv.mode == PWM_MODE_AUTO && priv.cpu_pwm == old_cpu);

    /* A failed manual entry must undo the successful user-defined trigger. */
    profile_kind = 1;
    before = profile_calls;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == -EINVAL);
    assert(profile_calls == before + 1);
    assert(!firmware_user_defined && !firmware_manual && !priv.auto_restore_pending);
    assert(priv.mode == PWM_MODE_AUTO);

    /* If compensation fails, AUTO recovery must run despite cached AUTO. */
    profile_status = -EIO;
    before = queued;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == -EINVAL);
    assert(firmware_user_defined && priv.auto_restore_pending);
    assert(queued == before + 1 && last_delay == 5);
    before = queued;
    hp_wmi_hwmon_keep_alive_handler(&priv.keep_alive_dwork.work);
    assert(priv.auto_restore_pending && queued == before + 1 && last_delay == 5);
    profile_status = 0;
    hp_wmi_hwmon_keep_alive_handler(&priv.keep_alive_dwork.work);
    assert(!firmware_user_defined && !firmware_manual && !priv.auto_restore_pending);
    before = profile_calls;
    hp_wmi_hwmon_keep_alive_handler(&priv.keep_alive_dwork.work);
    assert(profile_calls == before);

    /* Every integer firmware target survives the RPM/PWM round trip. */
    fan_status = 0;
    for (int maximum = 30; maximum <= 255; maximum++) {
        priv.max_rpm = maximum;
        for (int target = 0; target <= maximum; target++)
            assert(pwm_to_rpm(rpm_to_pwm(target, &priv), &priv) == target);
    }
    priv.max_rpm = 60;
    assert(rpm_to_pwm(655, &priv) == 255); /* Clamp before narrowing. */
    priv.min_rpm = 30;
    priv.mode = PWM_MODE_AUTO;
    rpm[0] = rpm[1] = 3000;
    assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_enable, 0, 1) == 0);
    assert(last_fans[0] == 30 && last_fans[1] == 30);
    for (int pwm = 0; pwm <= 255; pwm++) {
        assert(hp_wmi_hwmon_write(&dev, hwmon_pwm, hwmon_pwm_input, 0, pwm) == 0);
        assert(last_fans[0] >= 30 && last_fans[0] <= 60);
        assert(last_fans[1] == 30);
    }
    priv.cpu_pwm = 0; /* Even stale cached values obey the final clamp. */
    assert(hp_wmi_fan_speed_set(&priv) == 0 && last_fans[0] == 30);
    priv.min_rpm = 0;
    assert(hp_wmi_fan_speed_set(&priv) == 0 && last_fans[0] == 0);
    return 0;
}
""")

    def test_malformed_firmware_replies(self):
        functions = "\n".join(self.function(name) for name in (
            "encode_outsize_for_pvsz", "hp_wmi_perform_query",
            "hp_wmi_get_fan_speed", "hp_wmi_get_fan_speed_victus_s"))
        self.run_c(r"""
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
enum hp_wmi_command { HPWMI_GM };
typedef uint8_t u8;
struct acpi_buffer { size_t length; void *pointer; };
union acpi_object { int type; struct { int type; unsigned int length; void *pointer; } buffer; };
#define ACPI_ALLOCATE_BUFFER ((size_t)-1)
#define ACPI_TYPE_BUFFER 3
#define GFP_KERNEL 0
#define HPWMI_BIOS_GUID "mock"
#define CPU_FAN 0
#define GPU_FAN 1
#define HPWMI_FAN_SPEED_GET_QUERY 0x11
#define HPWMI_VICTUS_S_FAN_SPEED_GET_QUERY 0x2d
#define HPWMI_RET_UNKNOWN_COMMAND 3
#define HPWMI_RET_UNKNOWN_CMDTYPE 4
#define WARN_ON(x) (x)
#define pr_warn(...) ((void)0)
#define max(x, y) ((x) > (y) ? (x) : (y))
#define min(x, y) ((x) < (y) ? (x) : (y))
#define min_t(type, x, y) ((type)(x) < (type)(y) ? (type)(x) : (type)(y))
#define struct_size(ptr, field, n) (sizeof(*(ptr)) + sizeof((ptr)->field[0]) * (n))
#define flex_array_size(ptr, field, n) (sizeof((ptr)->field[0]) * (n))
#define kzalloc(size, flags) calloc(1, size)
#define kfree(ptr) free(ptr)
""" + self.structure("bios_args") + "\n" + self.structure("bios_return") + r"""
static unsigned int reply_length;
static bool null_buffer;
static int reply_type = ACPI_TYPE_BUFFER;
static struct { struct bios_return header; unsigned char data[4]; } response;
static int wmi_evaluate_method(const char *guid, int instance, int mid,
                               struct acpi_buffer *input, struct acpi_buffer *output)
{
    (void)guid; (void)instance; (void)mid; (void)input;
    union acpi_object *obj = calloc(1, sizeof(*obj));
    obj->buffer.type = reply_type;
    obj->buffer.pointer = null_buffer ? NULL : &response;
    obj->buffer.length = reply_length;
    output->pointer = obj;
    return 0;
}
""" + functions + r"""
int main(void)
{
    unsigned char data[4] = {0};
    for (reply_length = 0; reply_length < sizeof(struct bios_return); reply_length++)
        assert(hp_wmi_perform_query(1, HPWMI_GM, data, 1, 4) == -EINVAL);
    reply_length = sizeof(response); null_buffer = true;
    assert(hp_wmi_perform_query(1, HPWMI_GM, data, 1, 4) == -EINVAL);
    null_buffer = false; reply_type = 1;
    assert(hp_wmi_perform_query(1, HPWMI_GM, data, 1, 4) == -EINVAL);
    reply_type = ACPI_TYPE_BUFFER;
    response.header.return_code = 3;
    assert(hp_wmi_perform_query(1, HPWMI_GM, data, 1, 4) == 3);
    response.header.return_code = 0;
    response.data[0] = 42;
    reply_length = sizeof(struct bios_return) + 1;
    assert(hp_wmi_perform_query(1, HPWMI_GM, data, 1, 4) == 0);
    assert(data[0] == 42 && data[1] == 0 && data[2] == 0 && data[3] == 0);
    /* Tachometer queries require their actual data, not zero padding. */
    for (int bytes = 0; bytes < 4; bytes++) {
        reply_length = sizeof(struct bios_return) + bytes;
        assert(hp_wmi_get_fan_speed(CPU_FAN) == -EINVAL);
        assert(hp_wmi_get_fan_speed(GPU_FAN) == -EINVAL);
        if (bytes < 2) {
            assert(hp_wmi_get_fan_speed_victus_s(CPU_FAN) == -EINVAL);
            assert(hp_wmi_get_fan_speed_victus_s(GPU_FAN) == -EINVAL);
        }
    }
    response.data[0] = 30; response.data[1] = 40;
    reply_length = sizeof(struct bios_return) + 2;
    assert(hp_wmi_get_fan_speed_victus_s(CPU_FAN) == 3000);
    assert(hp_wmi_get_fan_speed_victus_s(GPU_FAN) == 4000);
    assert(hp_wmi_get_fan_speed_victus_s(2) == -EINVAL);
    response.data[2] = 0x0b; response.data[3] = 0xb8;
    reply_length = sizeof(struct bios_return) + 4;
    assert(hp_wmi_get_fan_speed(CPU_FAN) == 3000);
    return 0;
}
""")

    def test_auto_policy_survives_profile_setup_failure(self):
        self.run_c(r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
typedef uint8_t u8;
struct device { int unused; };
struct platform_device { struct device dev; };
struct platform_profile_ops { int unused; };
enum platform_profile_option { PLATFORM_PROFILE_BALANCED = 8 };
#define HP_EC_OFFSET_UNKNOWN 0xff
#define HP_NO_THERMAL_PROFILE_OFFSET 0xfe
#define IS_ERR(ptr) ((intptr_t)(ptr) < 0)
#define PTR_ERR(ptr) ((int)(intptr_t)(ptr))
#define pr_info(...) ((void)0)
""" + self.structure("thermal_profile_params") + r"""
static bool active_platform_profile_valid;
static enum platform_profile_option active_platform_profile;
static struct device *platform_profile_device;
static struct thermal_profile_params params = {.ec_tp_offset = 0x59};
static const struct platform_profile_ops platform_profile_omen_ops = {0};
static const struct platform_profile_ops platform_profile_victus_ops = {0};
static const struct platform_profile_ops platform_profile_victus_s_ops = {0};
static const struct platform_profile_ops hp_wmi_platform_profile_ops = {0};
static int kind, read_error, set_error, registration_error, registrations;
static bool is_omen_thermal_profile(void) { return kind == 1; }
static bool is_victus_thermal_profile(void) { return kind == 2; }
static bool is_victus_s_thermal_profile(void) { return kind == 3; }
static int read_profile(enum platform_profile_option *profile)
{
    if (read_error) return read_error;
    *profile = PLATFORM_PROFILE_BALANCED;
    return 0;
}
static int platform_profile_omen_get_ec(enum platform_profile_option *p)
{ return read_profile(p); }
static int platform_profile_victus_get_ec(enum platform_profile_option *p)
{ return read_profile(p); }
static int platform_profile_victus_s_get_ec(enum platform_profile_option *p)
{ return read_profile(p); }
static int set_profile(enum platform_profile_option p)
{ assert(p == PLATFORM_PROFILE_BALANCED); return set_error; }
static int platform_profile_omen_set_ec(enum platform_profile_option p)
{ return set_profile(p); }
static int platform_profile_victus_set_ec(enum platform_profile_option p)
{ return set_profile(p); }
/* This can fail after the thermal command, e.g. in GPU-power setup. */
static int platform_profile_victus_s_set_ec(enum platform_profile_option p)
{ return set_profile(p); }
static const struct thermal_profile_params *hp_wmi_thermal_profile(void) { return &params; }
static int thermal_profile_get(void) { return read_error ? read_error : 8; }
static int thermal_profile_set(int p) { (void)p; return set_error; }
static struct device *devm_platform_profile_register(struct device *dev, const char *name,
                                                     void *data, const struct platform_profile_ops *ops)
{
    (void)name; (void)data; (void)ops; registrations++;
    return registration_error ? (void *)(intptr_t)registration_error : dev;
}
""" + self.function("thermal_profile_setup") + r"""
int main(void)
{
    struct platform_device dev = {0};
    for (kind = 1; kind <= 3; kind++) {
        active_platform_profile_valid = false;
        read_error = -EIO; set_error = registration_error = 0;
        assert(thermal_profile_setup(&dev) == -EIO);
        assert(!active_platform_profile_valid);
        read_error = 0; set_error = -EIO;
        assert(thermal_profile_setup(&dev) == -EIO);
        assert(active_platform_profile_valid && active_platform_profile == PLATFORM_PROFILE_BALANCED);
        active_platform_profile_valid = false;
        set_error = 0; registration_error = -ENOMEM;
        assert(thermal_profile_setup(&dev) == -ENOMEM);
        assert(active_platform_profile_valid);
        registration_error = 0;
        assert(thermal_profile_setup(&dev) == 0);
    }
    kind = 3; active_platform_profile_valid = false;
    params.ec_tp_offset = HP_EC_OFFSET_UNKNOWN;
    read_error = -EIO; set_error = -EIO;
    assert(thermal_profile_setup(&dev) == -EIO);
    assert(active_platform_profile_valid && active_platform_profile == PLATFORM_PROFILE_BALANCED);
    return 0;
}
""")

    def test_hwmon_lifecycle(self):
        declarations = "\n".join(self.structure(name) for name in (
            "hp_wmi_fan_profile_params", "hp_wmi_hwmon_priv"))
        self.run_c(r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
typedef uint8_t u8;
struct mutex { int unused; };
struct work_struct { int unused; };
struct delayed_work { bool initialized; };
struct device { int unused; };
struct platform_device { struct device dev; };
#define GFP_KERNEL 0
#define IS_ERR(ptr) ((intptr_t)(ptr) < 0)
#define PTR_ERR(ptr) ((int)(intptr_t)(ptr))
#define dev_err(...) ((void)0)
""" + declarations + r"""
static struct platform_device platform;
static struct platform_device *hp_wmi_platform_dev = &platform;
static int chip_info, setup_status, action_status, register_status, cancelled;
static bool exposed;
static void *allocation, *cleanup_data;
static void (*cleanup)(void *);
static void *devm_kzalloc(struct device *dev, size_t size, int flags)
{ (void)dev; (void)flags; allocation = calloc(1, size); return allocation; }
static int devm_mutex_init(struct device *dev, struct mutex *lock)
{ (void)dev; (void)lock; return 0; }
static int hp_wmi_setup_fan_settings(struct hp_wmi_hwmon_priv *priv)
{ (void)priv; return setup_status; }
static void hp_wmi_hwmon_keep_alive_handler(struct work_struct *work)
{ (void)work; }
static void INIT_DELAYED_WORK(struct delayed_work *work, void (*handler)(struct work_struct *))
{ assert(handler == hp_wmi_hwmon_keep_alive_handler); work->initialized = true; }
static void cancel_delayed_work_sync(struct delayed_work *work)
{ assert(work->initialized && !exposed); cancelled++; }
static int devm_add_action_or_reset(struct device *dev, void (*action)(void *), void *data)
{
    (void)dev;
    assert(((struct hp_wmi_hwmon_priv *)data)->keep_alive_dwork.initialized);
    if (action_status) { action(data); return action_status; }
    cleanup = action; cleanup_data = data;
    return 0;
}
static struct device *devm_hwmon_device_register_with_info(struct device *dev, const char *name,
                                                          void *data, void *info, void *groups)
{
    (void)name; (void)info; (void)groups;
    assert(cleanup && ((struct hp_wmi_hwmon_priv *)data)->keep_alive_dwork.initialized);
    if (register_status) return (void *)(intptr_t)register_status;
    exposed = true;
    return dev;
}
static void platform_set_drvdata(struct platform_device *dev, void *data)
{ (void)dev; assert(data == allocation); }
""" + self.function("hp_wmi_hwmon_stop") + "\n" + self.function("hp_wmi_hwmon_init") + r"""
int main(void)
{
    assert(hp_wmi_hwmon_init() == 0 && exposed);
    /* Devres unregisters hwmon before running the cancellation action. */
    exposed = false;
    cleanup(cleanup_data);
    assert(cancelled == 1);
    free(allocation);
    register_status = -EIO;
    assert(hp_wmi_hwmon_init() == -EIO && !exposed);
    cleanup(cleanup_data);
    assert(cancelled == 2);
    free(allocation);
    action_status = -ENOMEM;
    assert(hp_wmi_hwmon_init() == -ENOMEM && cancelled == 3);
    free(allocation);
    setup_status = -EINVAL;
    assert(hp_wmi_hwmon_init() == -EINVAL && cancelled == 3);
    free(allocation);
    return 0;
}
""")
