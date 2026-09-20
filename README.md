# Victus Hub

![main tab](showcase_main.png)

A lightweight control panel for HP Victus and Omen laptops on Linux. 

It was built and tested on 8BD4 (HP Victus 16-s0001nv) 
with Fedora.

## Install (One-Liner)
```
curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/install.sh | sudo bash
```
## Uninstall (One-Liner)
```
curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/uninstall.sh | sudo bash
```

## What it does
- **System profiles** — maps the three UI profiles to `tuned-adm`
   or `power-profilesctl`.
- **Custom fan control** — Four modes:
  *auto* (hands control back to the EC), *smart* (built-in curve, fast
  on heat / slow on cooldown), *max* (100%), and *custom* (your
  temperature curves).
- **Mux switch** Hardware Mux switch support.
- **Keyboard RGB** — static color and brightness via a custom
  `hp-kbd-rgb` kernel module. Optional idle-dim when
  you stop typing.
- **Power limits** — set Sustained (sPPT), Fast (fPPT), and Slow limits,
  plus Tctl temperature, through `ryzenadj` for AMD CPUs. Intel CPUs show
  PL1 (long-term) and PL2 (short-term) limits using Linux RAPL.
- **Intel undervolt** — core and cache voltage offsets below the frequency
  controls, from -250 to 0 mV. Requires the `msr` kernel module
  (`sudo modprobe msr`) and unlocked firmware voltage control. Apply errors
  are shown in the panel; successful offsets are saved but only applied on
  clicking **Apply undervolt**. Set both offsets to 0 mV to reset them.
- **Sensors** — live CPU/GPU temperatures, fan RPM, power draw, and
  utilization.
- **Keyboard control shortcuts** Keyboard shortcuts for lighting and performance mode. 

Settings persist under `~/.config/victus-hub/`.

Keyboard Panel            |  Fans Panel
:-------------------------:|:-------------------------:
![](showcase_keyboard.png)  |  ![](showcase_curve.png)

## Installing (manual)

After cloning the repository run:

```
./scripts/install
```

## Uninstalling

```
./scripts/uninstall
```
Your settings under `~/.config/victus-hub/` are left in place. Removes everything else

## Running

From the application menu (look for "Victus Hub"), or:

```
victus-hub
```

It runs as a tray app — closing the window hides it to the system tray.
Click the tray icon to bring it back, or use the global hotkey you can
configure in the Settings page. A second launch raises the existing
instance rather than starting a new one.

Hardware control (fan curves, keyboard lighting, power-limit reapply,
battery power-save, and Ctrl+Shift shortcuts) runs in `victus-hubd`.
Quitting the UI leaves those settings active. Suspend and shutdown still
hand fans back to the EC and turn the keyboard backlight off.

## Logging/Debugging

The app logs to the terminal it was launched from (so run it from a
terminal or check the desktop entry's output). The daemon logs via
`journalctl -u victus-hubd`.

Both development scripts accept an optional terminal debug level:

| Level | Terminal messages |
| --- | --- |
| `0` (default) | Errors only |
| `1` | Errors + fan control |
| `2` | Errors + fan control + power |
| `3` | Errors + fan control + power + keyboard |

```bash
./scripts/dev-run 1
./scripts/ui-test 3
```

For a direct launch, use `VICTUS_HUB_DEBUG_LEVEL=2 victus-hub` (the same
environment variable is supported by `python3 -m victus_hubd`).

## Project Structure

- **`victus_hub/`** — the Qt GUI. The user runs this unprivileged. It
  sends desired state (fan curves, lighting, power policy) to the daemon
  over a Unix socket. Sensor display and the keyboard preview stay in the UI.
- **`victus_hubd/`** — the root daemon. Runs as a systemd service
  (`victus-hubd.service`), listens on `/run/victus-hubd/victus-hub.sock`,
  owns the control loops, and persists last policy under
  `/var/lib/victus-hubd/` so fans, lighting, and power limits keep working
  with the UI closed (a CLI can use the same socket later).
- **`kernel/`** — All custom kernel modules.

The bundled `hp-wmi` is based on `hp-wmi-ilpo-appliednew.c`, with an out-of-tree
ACPI compatibility definition and a read-only `gpu_mux_supported_names` attribute
for the app. MUX nodes live under `/sys/devices/platform/hp-wmi/`. Fan mode is
shared through `pwm1_enable`; the app applies its combined fan target to both
`pwm1` (CPU) and `pwm2` (GPU).

## Credits

- **P4R1H (https://ohmanapp.github.io/)** For the ui design that I recreated in qt with qt creator. 
- **Literally anyone who contributed code to the hp-wmi driver** <3.
