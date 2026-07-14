// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * hp-gpu-mux - HP GPU MUX companion driver.
 *
 * Based on the upstream platform/x86 hp-wmi GPU MUX patch (v4, Kürşat Abaylı):
 *   https://lore.kernel.org/platform-driver-x86/20260711001723.14279-1-hello@kursatabayli.dev/
 *
 * Does not claim the HP WMI GUID, so stock hp-wmi can stay loaded.
 */

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/acpi.h>
#include <linux/array_size.h>
#include <linux/bits.h>
#include <linux/errno.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/types.h>
#include <linux/wmi.h>

MODULE_AUTHOR("Evident");
MODULE_DESCRIPTION("HP GPU MUX companion driver");
MODULE_LICENSE("GPL");

#define HPWMI_BIOS_GUID "5FB7F034-2C63-45E9-BE91-3D44E2C707E4"

/* Capability bits (from system design data / OGH convention). */
#define HPWMI_MUX_MODE_UMA		BIT(0)
#define HPWMI_MUX_MODE_HYBRID		BIT(1)
#define HPWMI_MUX_MODE_DISCRETE		BIT(2)
#define HPWMI_MUX_MODE_OPTIMUS		BIT(3)
#define HPWMI_MUX_MODE_MASK		GENMASK(6, 0)
#define HPWMI_MUX_LEGACY_MASK		(HPWMI_MUX_MODE_HYBRID | HPWMI_MUX_MODE_DISCRETE)

#define HPWMI_HARDWARE_QUERY		0x04
#define HPWMI_GET_SYSTEM_DESIGN_DATA	0x28
#define HPWMI_GRAPHICS_MUX_QUERY	0x52
#define HPWMI_RET_INVALID_PARAMETERS	0x05

enum hp_wmi_command {
	HPWMI_READ = 0x01,
	HPWMI_WRITE = 0x02,
	HPWMI_GM = 0x20008,
};

struct bios_args {
	u32 signature;
	u32 command;
	u32 commandtype;
	u32 datasize;
	u8 data[];
};

struct bios_return {
	u32 sigpass;
	u32 return_code;
};

static const u8 mux_bitmask_map[] = {
	[0] = HPWMI_MUX_MODE_HYBRID,
	[1] = HPWMI_MUX_MODE_DISCRETE,
	[2] = HPWMI_MUX_MODE_OPTIMUS,
	[3] = HPWMI_MUX_MODE_UMA,
};

static DEFINE_MUTEX(hp_mux_wmi_mutex);
static bool zero_insize_support;
static bool zero_insize;
module_param(zero_insize, bool, 0444);
MODULE_PARM_DESC(zero_insize, "Force zero input size for WMI read queries");

static struct platform_device *hp_gpu_mux_pdev;

static int encode_outsize_for_pvsz(int outsize)
{
	if (outsize > 4096)
		return -EINVAL;
	if (outsize > 1024)
		return 5;
	if (outsize > 128)
		return 4;
	if (outsize > 4)
		return 3;
	if (outsize > 0)
		return 2;
	return 1;
}

static int hp_wmi_perform_query(int query, enum hp_wmi_command command,
				void *buffer, int insize, int outsize)
{
	struct acpi_buffer input, output = { ACPI_ALLOCATE_BUFFER, NULL };
	struct bios_return *bios_return;
	union acpi_object *obj = NULL;
	struct bios_args *args = NULL;
	int mid, actual_insize, actual_outsize;
	size_t bios_args_size;
	int ret;

	mid = encode_outsize_for_pvsz(outsize);
	if (mid < 0)
		return mid;

	actual_insize = max(insize, 128);
	bios_args_size = struct_size(args, data, actual_insize);
	args = kzalloc(bios_args_size, GFP_KERNEL);
	if (!args)
		return -ENOMEM;

	input.length = bios_args_size;
	input.pointer = args;
	args->signature = 0x55434553; /* "SECU" */
	args->command = command;
	args->commandtype = query;
	args->datasize = insize;
	if (insize > 0)
		memcpy(args->data, buffer, flex_array_size(args, data, insize));

	mutex_lock(&hp_mux_wmi_mutex);
	ret = wmi_evaluate_method(HPWMI_BIOS_GUID, 0, mid, &input, &output);
	mutex_unlock(&hp_mux_wmi_mutex);
	if (ret)
		goto out_free;

	obj = output.pointer;
	if (!obj || obj->type != ACPI_TYPE_BUFFER || !obj->buffer.pointer ||
	    obj->buffer.length < sizeof(*bios_return)) {
		ret = -EINVAL;
		goto out_free;
	}

	bios_return = (struct bios_return *)obj->buffer.pointer;
	ret = bios_return->return_code;
	if (ret || !outsize)
		goto out_free;

	actual_outsize = min(outsize,
			     (int)(obj->buffer.length - sizeof(*bios_return)));
	memcpy(buffer, obj->buffer.pointer + sizeof(*bios_return), actual_outsize);
	memset(buffer + actual_outsize, 0, outsize - actual_outsize);

out_free:
	kfree(obj);
	kfree(args);
	return ret;
}

static int hp_wmi_get_mux_supported_modes(u8 *supported)
{
	u8 legacy_buffer[4] = {};
	u8 buffer[128] = {};
	u32 req_packet = 0;
	int insize;
	int ret;

	if (!supported)
		return -EINVAL;

	insize = (zero_insize || zero_insize_support) ? 0 : sizeof(req_packet);
	ret = hp_wmi_perform_query(HPWMI_GET_SYSTEM_DESIGN_DATA, HPWMI_GM,
				   buffer, insize, sizeof(buffer));
	if (ret == 0) {
		*supported = buffer[7];
		return 0;
	}

	ret = hp_wmi_perform_query(HPWMI_GRAPHICS_MUX_QUERY, HPWMI_READ,
				   legacy_buffer, sizeof(legacy_buffer), 0);
	if (ret == 0) {
		*supported = HPWMI_MUX_LEGACY_MASK;
		return 0;
	}

	if (ret < 0)
		return ret;
	return -EINVAL;
}

static int hp_wmi_get_mux_mode(u8 *mode)
{
	u8 buffer[4] = {};
	int ret;

	if (!mode)
		return -EINVAL;

	ret = hp_wmi_perform_query(HPWMI_GRAPHICS_MUX_QUERY, HPWMI_READ,
				   buffer, sizeof(buffer), sizeof(buffer));
	if (ret < 0)
		return ret;
	if (ret > 0)
		return -EINVAL;

	*mode = buffer[0] & HPWMI_MUX_MODE_MASK;
	return 0;
}

static int hp_wmi_set_mux_mode(u8 mode)
{
	u8 buffer[4] = { mode, 0x00, 0x00, 0x00 };
	int ret;

	ret = hp_wmi_perform_query(HPWMI_GRAPHICS_MUX_QUERY, HPWMI_WRITE,
				   buffer, sizeof(buffer), sizeof(buffer));
	if (ret < 0)
		return ret;
	if (ret > 0)
		return -EINVAL;

	return 0;
}

/* ── sysfs ── */

static ssize_t gpu_mux_mode_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	u8 mode;
	int ret;

	ret = hp_wmi_get_mux_mode(&mode);
	if (ret)
		return ret;

	return sysfs_emit(buf, "%u\n", mode);
}

static ssize_t gpu_mux_mode_store(struct device *dev,
				  struct device_attribute *attr,
				  const char *buf, size_t count)
{
	u32 requested;
	u8 supported;
	int ret;

	ret = kstrtou32(buf, 0, &requested);
	if (ret)
		return ret;
	if (requested >= ARRAY_SIZE(mux_bitmask_map))
		return -EINVAL;

	ret = hp_wmi_get_mux_supported_modes(&supported);
	if (ret)
		return ret;

	if (!(supported & mux_bitmask_map[requested]))
		return -EOPNOTSUPP;

	ret = hp_wmi_set_mux_mode(requested);
	if (ret)
		return ret;

	return count;
}
static DEVICE_ATTR_RW(gpu_mux_mode);

static ssize_t gpu_mux_supported_show(struct device *dev,
				      struct device_attribute *attr, char *buf)
{
	u8 supported;
	int ret;

	ret = hp_wmi_get_mux_supported_modes(&supported);
	if (ret)
		return ret;

	return sysfs_emit(buf, "0x%02x\n", supported);
}
static DEVICE_ATTR_RO(gpu_mux_supported);

static ssize_t gpu_mux_supported_names_show(struct device *dev,
					    struct device_attribute *attr,
					    char *buf)
{
	u8 supported;
	int ret;
	int n = 0;

	ret = hp_wmi_get_mux_supported_modes(&supported);
	if (ret)
		return ret;

	if (supported & HPWMI_MUX_MODE_UMA)
		n += sysfs_emit_at(buf, n, "uma ");
	if (supported & HPWMI_MUX_MODE_HYBRID)
		n += sysfs_emit_at(buf, n, "hybrid ");
	if (supported & HPWMI_MUX_MODE_DISCRETE)
		n += sysfs_emit_at(buf, n, "discrete ");
	if (supported & HPWMI_MUX_MODE_OPTIMUS)
		n += sysfs_emit_at(buf, n, "optimus ");

	if (n == 0)
		return sysfs_emit(buf, "none\n");

	if (n > 0 && buf[n - 1] == ' ')
		buf[n - 1] = '\n';
	return n;
}
static DEVICE_ATTR_RO(gpu_mux_supported_names);

static struct attribute *hp_gpu_mux_attrs[] = {
	&dev_attr_gpu_mux_mode.attr,
	&dev_attr_gpu_mux_supported.attr,
	&dev_attr_gpu_mux_supported_names.attr,
	NULL,
};
ATTRIBUTE_GROUPS(hp_gpu_mux);

static int __init hp_gpu_mux_init(void)
{
	u8 tmp = 0;
	u8 mode = 0;
	u8 supported = 0;
	int ret;

	if (!wmi_has_guid(HPWMI_BIOS_GUID)) {
		pr_err("HP BIOS WMI GUID not present\n");
		return -ENODEV;
	}

	ret = hp_wmi_perform_query(HPWMI_HARDWARE_QUERY, HPWMI_READ, &tmp,
				   0, sizeof(tmp));
	if (ret == HPWMI_RET_INVALID_PARAMETERS)
		zero_insize_support = true;

	ret = hp_wmi_get_mux_mode(&mode);
	if (ret) {
		pr_info("MUX endpoint 0x52 READ not available (%d); not loading\n",
			ret);
		return -ENODEV;
	}

	ret = hp_wmi_get_mux_supported_modes(&supported);
	if (ret)
		pr_warn("could not read MUX capability mask (%d)\n", ret);
	else
		pr_info("MUX present: mode=%u supported=0x%02x\n", mode, supported);

	hp_gpu_mux_pdev = platform_device_register_simple("hp-gpu-mux",
							  PLATFORM_DEVID_NONE,
							  NULL, 0);
	if (IS_ERR(hp_gpu_mux_pdev))
		return PTR_ERR(hp_gpu_mux_pdev);

	ret = device_add_groups(&hp_gpu_mux_pdev->dev, hp_gpu_mux_groups);
	if (ret) {
		platform_device_unregister(hp_gpu_mux_pdev);
		hp_gpu_mux_pdev = NULL;
		return ret;
	}

	pr_info("loaded\n");
	return 0;
}

static void __exit hp_gpu_mux_exit(void)
{
	if (hp_gpu_mux_pdev) {
		device_remove_groups(&hp_gpu_mux_pdev->dev, hp_gpu_mux_groups);
		platform_device_unregister(hp_gpu_mux_pdev);
		hp_gpu_mux_pdev = NULL;
	}
}

module_init(hp_gpu_mux_init);
module_exit(hp_gpu_mux_exit);