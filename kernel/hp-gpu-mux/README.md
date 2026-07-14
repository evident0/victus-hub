# HP GPU MUX companion module

Out-of-tree companion driver based on the upstream `hp-wmi` GPU MUX patch

Does **not** claim the HP WMI GUID, so stock `hp-wmi` can remain loaded.

## Sysfs

Platform device: `/sys/devices/platform/hp-gpu-mux/`

| File | Access | Content |
|---|---|---|
| `gpu_mux_mode` | read/write | Requested/current mode index from firmware (`0x52`) |
| `gpu_mux_supported` | read-only | Capability bitmask as hex (e.g. `0x06`) |
| `gpu_mux_supported_names` | read-only | Supported modes in plain English |

### Mode index (raw write/read values)

These are the values you write to `gpu_mux_mode` and usually read back from it:

| Value | Mode |
|---:|---|
| `0` | hybrid |
| `1` | discrete |
| `2` | optimus |
| `3` | uma |

On many Omen/Victus boards (including hybrid + discrete only), only `0` and `1` are supported.

### Capability bitmask (`gpu_mux_supported`)

Separate from the current mode — this is what the hardware *supports*:

| Bit | Value | Mode |
|---:|---:|---|
| 0 | `0x01` | uma |
| 1 | `0x02` | hybrid |
| 2 | `0x04` | discrete |
| 3 | `0x08` | optimus |

Example: `0x06` = `0x02 | 0x04` → hybrid and discrete supported.

## Read values (raw and English)

**Raw — current mode index:**

```bash
cat /sys/devices/platform/hp-gpu-mux/gpu_mux_mode
# 0 = hybrid, 1 = discrete (on typical boards)
```

**Raw — supported capability mask:**

```bash
cat /sys/devices/platform/hp-gpu-mux/gpu_mux_supported
# e.g. 0x06
```

**English — supported modes:**

```bash
cat /sys/devices/platform/hp-gpu-mux/gpu_mux_supported_names
# e.g. hybrid discrete
```

**English — current mode** (no dedicated sysfs file; map the raw value):

```bash
case "$(cat /sys/devices/platform/hp-gpu-mux/gpu_mux_mode)" in
  0) echo hybrid ;;
  1) echo discrete ;;
  2) echo optimus ;;
  3) echo uma ;;
  *) echo "unknown ($(cat /sys/devices/platform/hp-gpu-mux/gpu_mux_mode))" ;;
esac
```

## Switch hybrid ↔ discrete

Like Omen Gaming Hub, this **requests** a mode via WMI. The physical MUX change applies on **reboot** — you can keep using the laptop until then.

**Hybrid → discrete:**

```bash
echo 1 | sudo tee /sys/devices/platform/hp-gpu-mux/gpu_mux_mode
sudo reboot
```

**Discrete → hybrid:**

```bash
echo 0 | sudo tee /sys/devices/platform/hp-gpu-mux/gpu_mux_mode
sudo reboot
```

**Verify after reboot:**

```bash
cat /sys/devices/platform/hp-gpu-mux/gpu_mux_mode
cat /sys/devices/platform/hp-gpu-mux/gpu_mux_supported_names
```

In hybrid, the internal panel is typically on the AMD iGPU (`card1-eDP-1`). In discrete, it moves to the NVIDIA GPU (`card0-eDP-1`):

```bash
ls /sys/class/drm/card*-eDP-*
```

Unsupported modes (e.g. `2` or `3` when the board only reports `hybrid discrete`) return an error from sysfs and do not change the setting.

## Build / install

```bash
cd kernel/hp-gpu-mux
make
./scripts/install    # needs sudo; builds, installs, modprobe
# or: sudo insmod ./hp-gpu-mux.ko
```

Unload / remove:

```bash
./scripts/uninstall
```
##
- Lore: https://lore.kernel.org/platform-driver-x86/20260711001723.14279-1-hello@kursatabayli.dev/

