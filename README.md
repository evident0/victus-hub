# Victus Hub

![sensors tab](showcase_sensors.png)
![settings tab](showcase_settings.png)
![main tab](showcase_main.png)

A control panel for HP Victus (and maybe Omen laptops) on Linux. 

It was built and tested on a Victus 16 with a Ryzen 7 7840HS / RTX 4070
under Fedora. Other HP Omen/Victus models with the same WMI firmware
interface should work; your mileage may vary.

## What it does
- **System profiles** — maps the three UI profiles to `tuned-adm` (tested on fedora)
   or `power-profilesctl` if available.
- **Custom fan control** — Three modes:
  *auto* (hands control back to the EC), *max* (100%), and *custom* (your
  curve). Fan floor override when GPU hits P0 for 10 seconds.
- **Keyboard RGB** — static color and brightness via a custom
  `hp-kbd-rgb` kernel module (a companion to the upstream hp-wmi RGB
  patch series; it doesn't claim the HP WMI GUID, so the stock `hp-wmi`
  driver stays loaded for hotkeys and fan hwmon). Optional idle-dim when
  you stop typing.
- **Power limits** — set Sustained (sPPT), Fast (fPPT), and Slow limits,
  plus Tctl temperature, through `ryzenadj` for amd cpus.
- **Sensors** — live CPU/GPU temperatures, fan RPM, power draw, and
  utilization. Included "task manager"+right click to stop processes, tracks cpu and ram (PSS).
- **Suspend/shutdown cleanup** — send suspend and shutdown commands before and after.

Settings persist under `~/.config/hp-helper/`.

## Requirements
- Linux with systemd (specifically `systemd-logind`)
- Python 3.9+ with PySide6 (6.8 or newer; `pip install -e .` pulls it)
- An AMD Ryzen CPU for the power-limit stuff `ryzenadj` is skipped
  automatically on Intel (sorry intel users but I can't test Intel for now).
- `tuned-adm` or `power-profilesctl` for system profile management
  (optional, gracefully skipped if not installed)
- Kernel headers matching your running kernel, to build the module

## Installing

One command installs everything:

```
./scripts/install
```
For dev work:

```
./scripts/dev-run
```

## Uninstalling

```
./scripts/uninstall
```
Your settings under `~/.config/hp-helper/` are left in place. Removes everything else

## Running

From the application menu (look for "HP Helper"), or:

```
hp-helper
```

It runs as a tray app — closing the window hides it to the system tray.
Click the tray icon to bring it back, or use the global hotkey you can
configure in the Settings page. A second launch raises the existing
instance rather than starting a new one.

## Logging/Debugging

The app logs to the terminal it was launched from (so run it from a
terminal or check the desktop entry's output). The daemon logs via
`journalctl -u hp-helperd`.

## Project Structure

- **`hp_helper/`** — the Qt GUI. The user runs this unprivileged. It
  talks to the daemon over a Unix socket for anything requiring root.
- **`hp_helperd/`** — the root daemon. Runs as a systemd service
  (`hp-helperd.service`), listens on `/run/hp-helperd/hp-helper-rs.sock`,
- **`kernel/hp-kbd-rgb/`** — keyboard RGB module.

## License

The kernel module is GPL-2.0-or-later. Everything else inherits the
project's license unless noted otherwise.
