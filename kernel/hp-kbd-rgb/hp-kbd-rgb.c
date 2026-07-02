// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * hp-kbd-rgb - HP Omen/Victus keyboard RGB companion driver.
 *
 * This is an out-of-tree companion module based on the upstream hp-wmi
 * multicolor keyboard backlight patch series.  It does not claim the HP WMI
 * GUID, so the stock hp-wmi driver can remain loaded for hotkeys, fan hwmon,
 * platform profiles, and rfkill.
 */

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/acpi.h>
#include <linux/errno.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/led-class-multicolor.h>
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/types.h>
#include <linux/wmi.h>

MODULE_AUTHOR("Evident / upstream hp-wmi RGB patch authors");
MODULE_DESCRIPTION("HP Omen/Victus keyboard RGB companion driver");
MODULE_LICENSE("GPL");

#define HPWMI_BIOS_GUID "5FB7F034-2C63-45E9-BE91-3D44E2C707E4"
#define HP_COLOR_TABLE_SIZE 128
#define HP_COLOR_TABLE_PADDING 25
#define HP_MAX_KBD_ZONES 4
#define HP_COLOR_TABLE_ZONE_SLOTS 8

#define HPWMI_BACKLIGHT_SET_OFF_QUERY 0x64
#define HPWMI_BACKLIGHT_SET_ON_QUERY 0xE4
#define HPWMI_HARDWARE_QUERY 0x04
#define HPWMI_RET_INVALID_PARAMETERS 0x05


enum hp_keyboard_type {
	HP_KEYBOARD_TYPE_NOBACKLIGHT = 0x0,
	HP_KEYBOARD_TYPE_FOURZONE_WITH_NUMPAD = 0x1,
	HP_KEYBOARD_TYPE_FOURZONE_WITHOUT_NUMPAD = 0x2,
	HP_KEYBOARD_TYPE_RGB_PER_KEY = 0x3,
	HP_KEYBOARD_TYPE_SINGLEZONE_WITH_NUMPAD = 0x4,
	HP_KEYBOARD_TYPE_SINGLEZONE_WITHOUT_NUMPAD = 0x5,
};

enum hp_wmi_gm_commandtype {
	HPWMI_GET_KEYBOARD_TYPE_QUERY = 0x2b,
};

enum hp_wmi_backlight_commandtype {
	HPWMI_BACKLIGHT_COLOR_GET_QUERY = 0x02,
	HPWMI_BACKLIGHT_COLOR_SET_QUERY = 0x03,
	HPWMI_BACKLIGHT_BRIGHTNESS_GET_QUERY = 0x04,
	HPWMI_BACKLIGHT_BRIGHTNESS_SET_QUERY = 0x05,
};

enum hp_wmi_command {
	HPWMI_READ = 0x01,
	HPWMI_WRITE = 0x02,
	HPWMI_GM = 0x20008,
	HPWMI_BACKLIGHT = 0x20009,
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

struct hp_kbd_led_priv {
	unsigned int zone;
	enum led_brightness last_brightness;
};

struct hp_kbd_rgb_state {
	struct led_classdev_mc leds[HP_MAX_KBD_ZONES];
	struct hp_kbd_led_priv priv[HP_MAX_KBD_ZONES];
	struct mc_subled subleds[HP_MAX_KBD_ZONES][3];
	u8 keyboard_type;
	unsigned int zone_count;
};

static DEFINE_MUTEX(hp_kbd_wmi_mutex);
static bool zero_insize_support;
static bool zero_insize;
module_param(zero_insize, bool, 0444);
MODULE_PARM_DESC(zero_insize, "Force zero input size for WMI read queries");

static struct platform_device *hp_kbd_rgb_pdev;
static struct hp_kbd_rgb_state hp_kbd_rgb;

static const char * const hp_zone_names_4[HP_MAX_KBD_ZONES] = {
	[0] = "zoned_backlight-right",
	[1] = "zoned_backlight-center",
	[2] = "zoned_backlight-left",
	[3] = "zoned_backlight-wasd",
};

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
	args->signature = 0x55434553;
	args->command = command;
	args->commandtype = query;
	args->datasize = insize;
	if (insize > 0)
		memcpy(args->data, buffer, flex_array_size(args, data, insize));

	mutex_lock(&hp_kbd_wmi_mutex);
	ret = wmi_evaluate_method(HPWMI_BIOS_GUID, 0, mid, &input, &output);
	mutex_unlock(&hp_kbd_wmi_mutex);
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

static int hp_kbd_backlight_get_color_table(u8 color_table[HP_COLOR_TABLE_SIZE])
{
	int insize = (zero_insize || zero_insize_support) ? 0 : HP_COLOR_TABLE_SIZE;

	memset(color_table, 0, HP_COLOR_TABLE_SIZE);
	return hp_wmi_perform_query(HPWMI_BACKLIGHT_COLOR_GET_QUERY,
				     HPWMI_BACKLIGHT, color_table,
				     insize, HP_COLOR_TABLE_SIZE);
}

static int hp_kbd_backlight_set_rgb_color(unsigned int zone, int red, int green,
					  int blue)
{
	u8 color_table[HP_COLOR_TABLE_SIZE];
	int ret;

	if (zone >= hp_kbd_rgb.zone_count)
		return -EINVAL;

	ret = hp_kbd_backlight_get_color_table(color_table);
	if (ret)
		return ret;

	if (hp_kbd_rgb.zone_count == 1) {
		int slot;

		for (slot = 0; slot < HP_COLOR_TABLE_ZONE_SLOTS; slot++) {
			color_table[HP_COLOR_TABLE_PADDING + slot * 3] = red;
			color_table[HP_COLOR_TABLE_PADDING + slot * 3 + 1] = green;
			color_table[HP_COLOR_TABLE_PADDING + slot * 3 + 2] = blue;
		}
	} else {
		color_table[HP_COLOR_TABLE_PADDING + zone * 3] = red;
		color_table[HP_COLOR_TABLE_PADDING + zone * 3 + 1] = green;
		color_table[HP_COLOR_TABLE_PADDING + zone * 3 + 2] = blue;
	}

	ret = hp_wmi_perform_query(HPWMI_BACKLIGHT_COLOR_SET_QUERY,
				     HPWMI_BACKLIGHT, color_table,
				     HP_COLOR_TABLE_SIZE, HP_COLOR_TABLE_SIZE);
	if (ret < 0)
		return ret;
	if (ret)
		return -EINVAL;

	return 0;
}

static bool hp_kbd_backlight_is_on(void)
{
	u8 data = 0;
	int ret;

	ret = hp_wmi_perform_query(HPWMI_BACKLIGHT_BRIGHTNESS_GET_QUERY,
				     HPWMI_BACKLIGHT, &data,
				     sizeof(data), sizeof(data));
	if (ret)
		return false;

	return data == HPWMI_BACKLIGHT_SET_ON_QUERY;
}

static int hp_kbd_backlight_set_on(bool on)
{
	u8 data = on ? HPWMI_BACKLIGHT_SET_ON_QUERY : HPWMI_BACKLIGHT_SET_OFF_QUERY;
	int ret;

	ret = hp_wmi_perform_query(HPWMI_BACKLIGHT_BRIGHTNESS_SET_QUERY,
				     HPWMI_BACKLIGHT, &data,
				     sizeof(data), sizeof(data));
	if (ret < 0)
		return ret;
	if (ret)
		return -EINVAL;

	return 0;
}

static struct hp_kbd_led_priv *hp_led_get_priv(struct led_classdev *led_cdev)
{
	struct led_classdev_mc *mc_cdev = lcdev_to_mccdev(led_cdev);
	int zone = mc_cdev - hp_kbd_rgb.leds;

	return &hp_kbd_rgb.priv[zone];
}

static int hp_kbd_set_brightness(struct led_classdev *led_cdev,
				 enum led_brightness brightness)
{
	struct hp_kbd_led_priv *priv = hp_led_get_priv(led_cdev);
	struct led_classdev_mc *mc_cdev = lcdev_to_mccdev(led_cdev);
	int ret;

	if (brightness == LED_OFF) {
		priv->last_brightness = led_cdev->brightness;
		led_cdev->brightness = LED_OFF;
		return hp_kbd_backlight_set_on(false);
	}

	led_cdev->brightness = brightness;
	led_mc_calc_color_components(mc_cdev, brightness);

	ret = hp_kbd_backlight_set_rgb_color(priv->zone,
				mc_cdev->subled_info[0].brightness,
				mc_cdev->subled_info[1].brightness,
				mc_cdev->subled_info[2].brightness);
	if (ret)
		return ret;

	return hp_kbd_backlight_set_on(true);
}

static ssize_t keyboard_type_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "0x%02x\n", hp_kbd_rgb.keyboard_type);
}
static DEVICE_ATTR_RO(keyboard_type);

static ssize_t zone_count_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%u\n", hp_kbd_rgb.zone_count);
}
static DEVICE_ATTR_RO(zone_count);

static ssize_t zero_insize_support_show(struct device *dev,
					struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%d\n", zero_insize || zero_insize_support);
}
static DEVICE_ATTR_RO(zero_insize_support);

static ssize_t color_show(struct device *dev, struct device_attribute *attr,
			  char *buf)
{
	u8 color_table[HP_COLOR_TABLE_SIZE];
	int ret;

	ret = hp_kbd_backlight_get_color_table(color_table);
	if (ret)
		return ret;

	return sysfs_emit(buf, "%u %u %u\n",
			  color_table[HP_COLOR_TABLE_PADDING],
			  color_table[HP_COLOR_TABLE_PADDING + 1],
			  color_table[HP_COLOR_TABLE_PADDING + 2]);
}

static ssize_t color_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	unsigned int red, green, blue;
	int ret;

	if (sscanf(buf, "%u %u %u", &red, &green, &blue) != 3)
		return -EINVAL;
	if (red > 255 || green > 255 || blue > 255)
		return -EINVAL;

	ret = hp_kbd_backlight_set_rgb_color(0, red, green, blue);
	if (ret)
		return ret;

	ret = hp_kbd_backlight_set_on(true);
	if (ret)
		return ret;

	return count;
}
static DEVICE_ATTR_RW(color);


static struct attribute *hp_kbd_rgb_attrs[] = {
	&dev_attr_keyboard_type.attr,
	&dev_attr_zone_count.attr,
	&dev_attr_zero_insize_support.attr,
	&dev_attr_color.attr,
	NULL,
};
ATTRIBUTE_GROUPS(hp_kbd_rgb);

static int hp_kbd_rgb_register_zone(struct device *dev, unsigned int zone,
				    const u8 color_table[HP_COLOR_TABLE_SIZE])
{
	struct led_classdev_mc *mc_cdev = &hp_kbd_rgb.leds[zone];
	struct led_classdev *led_cdev = &mc_cdev->led_cdev;
	struct hp_kbd_led_priv *priv = &hp_kbd_rgb.priv[zone];
	struct mc_subled *subleds = hp_kbd_rgb.subleds[zone];
	int i, ret;

	if (hp_kbd_rgb.zone_count == 1)
		led_cdev->name = "hp::kbd_backlight";
	else
		led_cdev->name = devm_kasprintf(dev, GFP_KERNEL,
						 "hp::kbd_backlight_%s",
						 hp_zone_names_4[zone]);
	if (!led_cdev->name)
		return -ENOMEM;

	led_cdev->brightness = hp_kbd_backlight_is_on() ? LED_FULL : LED_OFF;
	led_cdev->max_brightness = LED_FULL;
	led_cdev->brightness_set_blocking = hp_kbd_set_brightness;
	led_cdev->flags = LED_CORE_SUSPENDRESUME | LED_RETAIN_AT_SHUTDOWN;

	subleds[0].color_index = LED_COLOR_ID_RED;
	subleds[1].color_index = LED_COLOR_ID_GREEN;
	subleds[2].color_index = LED_COLOR_ID_BLUE;

	for (i = 0; i < 3; i++) {
		int offset = HP_COLOR_TABLE_PADDING + zone * 3 + i;

		subleds[i].channel = zone * 3 + i;
		subleds[i].intensity = color_table[offset];
		subleds[i].brightness = LED_FULL;
	}

	mc_cdev->subled_info = subleds;
	mc_cdev->num_colors = 3;

	ret = devm_led_classdev_multicolor_register(dev, mc_cdev);
	if (ret)
		return ret;

	priv->zone = zone;
	priv->last_brightness = LED_FULL;
	return 0;
}

static int hp_kbd_rgb_setup(struct device *dev)
{
	u8 color_table[HP_COLOR_TABLE_SIZE];
	u8 keyboard_type = 0;
	unsigned int zone_count;
	int ret;
	unsigned int zone;

	ret = hp_wmi_perform_query(HPWMI_GET_KEYBOARD_TYPE_QUERY, HPWMI_GM,
				     &keyboard_type, sizeof(keyboard_type),
				     sizeof(keyboard_type));
	if (ret)
		return ret;

	switch (keyboard_type) {
	case HP_KEYBOARD_TYPE_FOURZONE_WITH_NUMPAD:
	case HP_KEYBOARD_TYPE_FOURZONE_WITHOUT_NUMPAD:
		zone_count = 4;
		break;
	case HP_KEYBOARD_TYPE_SINGLEZONE_WITH_NUMPAD:
	case HP_KEYBOARD_TYPE_SINGLEZONE_WITHOUT_NUMPAD:
		zone_count = 1;
		break;
	case HP_KEYBOARD_TYPE_RGB_PER_KEY:
		dev_info(dev, "per-key RGB keyboard reported; unsupported by this driver\n");
		return -EOPNOTSUPP;
	case HP_KEYBOARD_TYPE_NOBACKLIGHT:
		dev_info(dev, "keyboard reports no RGB backlight\n");
		return -ENODEV;
	default:
		dev_info(dev, "unsupported keyboard type 0x%02x\n", keyboard_type);
		return -EOPNOTSUPP;
	}

	ret = hp_kbd_backlight_get_color_table(color_table);
	if (ret)
		return ret;

	hp_kbd_rgb.keyboard_type = keyboard_type;
	hp_kbd_rgb.zone_count = zone_count;

	for (zone = 0; zone < zone_count; zone++) {
		ret = hp_kbd_rgb_register_zone(dev, zone, color_table);
		if (ret)
			return ret;
	}

	dev_info(dev, "registered keyboard RGB type 0x%02x with %u zone(s)\n",
		 keyboard_type, zone_count);
	return 0;
}

static int __init hp_kbd_rgb_init(void)
{
	int ret, tmp = 0;

	if (!wmi_has_guid(HPWMI_BIOS_GUID))
		return -ENODEV;

	hp_kbd_rgb_pdev = platform_device_register_simple("hp-kbd-rgb",
						       PLATFORM_DEVID_NONE, NULL, 0);
	if (IS_ERR(hp_kbd_rgb_pdev))
		return PTR_ERR(hp_kbd_rgb_pdev);

	if (hp_wmi_perform_query(HPWMI_HARDWARE_QUERY, HPWMI_READ, &tmp,
				 sizeof(tmp), sizeof(tmp)) ==
	    HPWMI_RET_INVALID_PARAMETERS)
		zero_insize_support = true;

	ret = sysfs_create_groups(&hp_kbd_rgb_pdev->dev.kobj, hp_kbd_rgb_groups);
	if (ret)
		goto err_unregister;

	ret = hp_kbd_rgb_setup(&hp_kbd_rgb_pdev->dev);
	if (ret)
		goto err_groups;

	return 0;

err_groups:
	sysfs_remove_groups(&hp_kbd_rgb_pdev->dev.kobj, hp_kbd_rgb_groups);
err_unregister:
	platform_device_unregister(hp_kbd_rgb_pdev);
	return ret;
}

static void __exit hp_kbd_rgb_exit(void)
{
	sysfs_remove_groups(&hp_kbd_rgb_pdev->dev.kobj, hp_kbd_rgb_groups);
	platform_device_unregister(hp_kbd_rgb_pdev);
}

module_init(hp_kbd_rgb_init);
module_exit(hp_kbd_rgb_exit);
