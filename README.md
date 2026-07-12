# Victus Hub

![sensors tab](showcase_sensors.png)
![settings tab](showcase_settings.png)
![main tab](showcase_main.png)

A control panel for HP Victus and Omen laptops on Linux. It wraps the
hardware pieces that the stock OS drivers don't expose in a useful way —
custom fan curves, keyboard RGB, and Ryzen power limits — behind a single
PySide6 (Qt) app and a small privileged daemon.

It was built and tested on a Victus 16 with a Ryzen 7 7840HS / RTX 4070
under Fedora. Other HP Omen/Victus models with the same WMI firmware
interface should work; your mileage may vary.

## What it does

- **Custom fan control** — a background thread reads CPU/GPU temperatures
  every second and writes PWM values every three seconds, following
  per-profile fan curves you edit in the Fans & Power page. Three modes:
  *auto* (hands control back to the EC), *max* (100%), and *custom* (your
  curve). A P0 floor override kicks the fan up when the GPU hits
  performance state P0, debounced so it doesn't thrash on transient
  spikes.
- **Keyboard RGB** — static color and brightness via a custom
  `hp-kbd-rgb` kernel module (a companion to the upstream hp-wmi RGB
  patch series; it doesn't claim the HP WMI GUID, so the stock `hp-wmi`
  driver stays loaded for hotkeys and fan hwmon). Optional idle-dim when
  you stop typing.
- **Power limits** — set Sustained (sPPT), Fast (fPPT), and Slow limits,
  plus Tctl temperature, through `ryzenadj`. Values are clamped to
  15–55 W and 75–95 °C. A reapply timer fights the firmware resetting
  them on its own.
- **System profiles** — maps the three UI profiles to `power-profilesctl`
  or `tuned-adm` if available.
- **Sensors** — live CPU/GPU temperatures, fan RPM, power draw, and
  utilization. A top-processes card shows what's using CPU and RAM,
  grouped by application (it identifies apps behind interpreters like
  python/electron and groups multi-process apps like browsers). You can
  stop a process from its context menu or the Stop column.
- **Suspend/shutdown cleanup** — watches systemd-logind's
  `PrepareForSleep` / `PrepareForShutdown` D-Bus signals. Before the
  system goes down, it resets fans to auto and turns the keyboard
  backlight off, and pauses the background loops so they don't fight the
  cleanup. A delay inhibitor holds suspend for the few milliseconds it
  takes the writes to land.

## How it's structured

- **`hp_helper/`** — the Qt GUI. The user runs this unprivileged. It
  talks to the daemon over a Unix socket for anything requiring root.
- **`hp_helperd/`** — the root daemon. Runs as a systemd service
  (`hp-helperd.service`), listens on `/run/hp-helperd/hp-helper-rs.sock`,
  and performs the privileged sysfs writes, `ryzenadj` calls, and
  `/dev/mem` power reads. The service is locked down: no new privileges,
  private `/tmp`, read-only `/sys` except for the exact paths it writes,
  and `RestrictAddressFamilies=AF_UNIX`.
- **`kernel/hp-kbd-rgb/`** — the out-of-tree keyboard RGB module.

Settings persist under `~/.config/hp-helper/` (QSettings) and are left in
place on uninstall.

## Requirements

- Linux with systemd (specifically `systemd-logind` — that's how the
  suspend/shutdown signals are received)
- Python 3.9+ with PySide6 (6.8 or newer; `pip install -e .` pulls it)
- An HP Omen or Victus with the WMI multicolor keyboard backlight
  interface for RGB control
- An AMD Ryzen CPU for the power-limit stuff (`ryzenadj` is skipped
  automatically on Intel)
- `tuned-adm` or `power-profilesctl` for system profile management
  (optional, gracefully skipped if not installed)
- Kernel headers matching your running kernel, to build the module

## Installing

One command installs everything:

```
./scripts/install
```

This builds and loads the kernel module, installs `ryzenadj` (on AMD),
installs the daemon + GUI as an editable `pip` package, starts the
systemd service, and drops a `.desktop` entry + icon so the app shows up
in your application menu.

If you just want to run from source during development:

```
./scripts/dev-run
```

It will prompt you about installing/rebuilding the kernel module and
`ryzenadj`, reinstall the daemon (editable), restart the service, then
launch the app. On exit it restores the fan to auto.

## Uninstalling

```
./scripts/uninstall
```

Removes the desktop entry, icon, daemon service, Python packages, kernel
module, and `ryzenadj`. Your settings under `~/.config/hp-helper/` are
left in place.

## Running

From the application menu (look for "HP Helper"), or:

```
hp-helper
```

It runs as a tray app — closing the window hides it to the system tray.
Click the tray icon to bring it back, or use the global hotkey you can
configure in the Settings page. A second launch raises the existing
instance rather than starting a new one.

## Logging

The app logs to the terminal it was launched from (so run it from a
terminal or check the desktop entry's output). The daemon logs via
`journalctl -u hp-helperd`. Fan control and suspend/shutdown cleanup
both emit info-level lines so you can confirm what happened.

## License

The kernel module is GPL-2.0-or-later. Everything else inherits the
project's license unless noted otherwise.