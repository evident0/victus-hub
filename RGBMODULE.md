# HP Keyboard RGB Kernel Module (hp-kbd-rgb)

This document describes the `hp-kbd-rgb` out-of-tree kernel module located in `kernel/hp-kbd-rgb/`.

## Overview

The module provides userspace control over HP Omen/Victus keyboard RGB backlights via sysfs. It is a companion driver that does **not** claim the HP WMI GUID (so the main `hp-wmi` driver can stay loaded for fans, hotkeys, etc.).

It only supports static per-zone colors. All animation effects are implemented in userspace.

**Supported keyboard types** (from `HPWMI_GET_KEYBOARD_TYPE_QUERY`):
- `0x01` / `0x02` — 4-zone RGB (with/without numpad)
- `0x04` / `0x05` — single-zone (with/without numpad)
- `0x03` (per-key) and `0x00` (no backlight) are rejected.

## Sysfs Interface Exposed

Platform device:
```
/sys/devices/platform/hp-kbd-rgb/
├── keyboard_type          (ro)   e.g. 0x01
├── zone_count             (ro)   1 or 4
├── zero_insize_support    (ro)
└── color                  (rw)   "R G B" (0-255)
```

### Global `color` attribute

Writing to `color` sets the color table via `hp_kbd_backlight_set_rgb_color(0, r, g, b)` **and** keeps the per-zone `mc_subled` intensity fields in sync, so subsequent writes to the LED `brightness` file scale the correct color instead of a stale snapshot.

- On **4-zone** keyboards this only updates zone 0 (right).
- On **single-zone** it fills all slots with the same color.
- It also forces the backlight on and reflects the on state in the LED class device.

This is a simplified global knob and **not** suitable for per-zone control.

### LED Class Multicolor Devices

The real per-zone interface is the standard `led_classdev_mc` devices.

**4-zone keyboard** (zone order in driver):
- `hp::kbd_backlight_zoned_backlight-right`   (zone 0)
- `hp::kbd_backlight_zoned_backlight-center`  (zone 1)
- `hp::kbd_backlight_zoned_backlight-left`    (zone 2)
- `hp::kbd_backlight_zoned_backlight-wasd`    (zone 3)

**Single-zone keyboard**:
- `hp::kbd_backlight`

Standard files per LED (under `/sys/class/leds/<name>/`):
- `brightness` (0 or 255)
- `max_brightness`
- `multi_index` → `red green blue`
- `multi_intensity` → `R G B` (write target for color)

Writing `multi_intensity` triggers:
1. `led_mc_calc_color_components`
2. `hp_kbd_backlight_set_rgb_color(zone, r, g, b)` — zone-aware
3. `hp_kbd_backlight_set_on(true)`

## Commands

### 4-Zone (recommended method)

```bash
# Per-zone colors
echo "255 0 0"   | sudo tee /sys/class/leds/hp::kbd_backlight_zoned_backlight-right/multi_intensity
echo "0 255 0"   | sudo tee /sys/class/leds/hp::kbd_backlight_zoned_backlight-center/multi_intensity
echo "0 0 255"   | sudo tee /sys/class/leds/hp::kbd_backlight_zoned_backlight-left/multi_intensity
echo "255 255 0" | sudo tee /sys/class/leds/hp::kbd_backlight_zoned_backlight-wasd/multi_intensity

# Turn a zone completely off
echo 0 | sudo tee /sys/class/leds/hp::kbd_backlight_zoned_backlight-right/brightness
```

### Single-Zone

```bash
echo "128 0 255" | sudo tee /sys/devices/platform/hp-kbd-rgb/color
# or
echo "128 0 255" | sudo tee /sys/class/leds/hp::kbd_backlight/multi_intensity
```

### Global shortcut (limited)

```bash
echo "255 128 64" | sudo tee /sys/devices/platform/hp-kbd-rgb/color
```

### Inspection

```bash
cat /sys/devices/platform/hp-kbd-rgb/keyboard_type
cat /sys/devices/platform/hp-kbd-rgb/zone_count
cat /sys/class/leds/hp::kbd_backlight*/multi_intensity
```

## Effects and Animations

**The driver sends no effect commands.**

Internally it only uses:
- `HPWMI_BACKLIGHT_COLOR_SET_QUERY` (0x03) — static 128-byte color table
- `HPWMI_BACKLIGHT_BRIGHTNESS_SET_QUERY` (0x05) — on/off only

Color table layout (from source):
- `HP_COLOR_TABLE_PADDING = 25`
- 4-zone: each zone occupies 3 bytes at `PADDING + zone*3`
- Single-zone: fills 8 slots (24 bytes) with the same RGB

There are no:
- Effect/mode bytes
- Speed parameters
- Breathing, wave, cycle, strobe commands
- Any animation state in the driver

All "effects" in this project (`LightingEffect = "static" | "breathing" | "color-cycle" | "strobe"`) are **software animations** implemented in:
- `hp_helper/keyboard_lighting.py`
- `hp_helper/lighting_controller.py`

These repeatedly write new static colors to the sysfs path on a timer. The daemon (`hp_helperd/sysfs.py`) exposes `write_keyboard_color()` (LED `multi_intensity` + `brightness=255`) and `write_keyboard_brightness()` (LED `brightness`), both targeting `/sys/class/leds/hp::kbd_backlight*`.

## Internal Details

- Color table is always read-modify-written (128 bytes).
- For single-zone the driver loops over all 8 slots.
- Brightness=0 goes through `led_mc_calc_color_components` (produces black) then `set_rgb_color`, matching upstream hp-wmi. The driver also restores other zones' cached colors when forcing the global backlight on.
- Module creates a platform device named `hp-kbd-rgb`.
- Requires `led-class-multicolor` dependency.
- Mutex-protected WMI calls.

## Limitations

1. Top-level `color` attribute is zone-0 only.
2. No native hardware effects — all animation costs CPU and generates many WMI calls.
3. Per-zone support exists in the driver but is **not wired up** in the current Python daemon or UI.
4. Per-key RGB keyboards (`0x03`) are explicitly unsupported.
5. No persistence across reboots or suspend (driver sets `LED_RETAIN_AT_SHUTDOWN`).

## Current Project Usage

- Daemon writes the LED multicolor interface (`multi_intensity` + `brightness`), not the legacy platform `color` node.
- Lighting effects are purely software.
- The "RGB enabled" checkbox and idle dim turn the backlight off via `brightness=0` (the proper LED off path), not by sending color `0 0 0`.
- No code currently writes individual `multi_intensity` files for the four zones (all zones get the same color).

## References

- Source: `kernel/hp-kbd-rgb/hp-kbd-rgb.c`
- Install test: `echo "0 255 0" | sudo tee /sys/devices/platform/hp-kbd-rgb/color`
- Color table handling: `hp_kbd_backlight_set_rgb_color()` and `hp_kbd_backlight_get_color_table()`
- LED registration: `hp_kbd_rgb_register_zone()`

---

Generated from direct source analysis (2026-07-06). Module not loaded on this system during investigation.