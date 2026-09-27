# Victus Hub Flatpak

This is the Fedora Atomic stand-in for `./scripts/install --app-only`. The window, icon, and app-menu entry come from the Flatpak. The root daemon is a host systemd unit, because a Flatpak cannot write sysfs or install a system service.

Stock in-tree `hp_wmi` is enough. Kernel modules, DKMS, Secure Boot signing, and ryzenadj are not part of this package.

## What works

With the host daemon running:

- Platform profiles through the host `powerprofilesctl` (or tuned, if the daemon is the one applying them).
- CPU frequency limits.
- Intel RAPL power limits.
- Fan speed and temperature reads when the stock hwmon nodes exist.

Settings still live in `~/.config/victus-hub/`. Daemon state stays in `/var/lib/victus-hubd/`.

## What stays unavailable

- Custom fan curves. Stock `hp_wmi` has no `pwm1_enable` or `pwm1`.
- Keyboard RGB. That needs `hp-kbd-rgb`.
- The MUX switch. That needs the custom `gpu_mux_mode` node.
- AMD power limits. ryzenadj is not installed.
- Suspend and resume park/restore. A Flatpak cannot install `/usr/lib/systemd/system-sleep/victus-hub`, and this package does not add a host sleep unit.

The in-app Update button still runs the GitHub `install.sh` installer. Inside the sandbox that fails. Update the window with `flatpak update`, then restart the daemon:

```bash
sudo systemctl restart victus-hubd
```

## Build and install

The Flatpak must be a system install. The daemon runs as root and reads the app files from `/var/lib/flatpak`.

```bash
flatpak remote-add --if-not-exists --system flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --system flathub org.kde.Platform//6.11 org.kde.Sdk//6.11

flatpak-builder --force-clean --repo=/tmp/victus-hub-repo \
  build packaging/flatpak/io.github.evident0.VictusHub.yml
flatpak build-bundle /tmp/victus-hub-repo /tmp/VictusHub.flatpak io.github.evident0.VictusHub
sudo flatpak install --system /tmp/VictusHub.flatpak

sudo packaging/flatpak/host/install
```

`host/install` writes:

- `/usr/local/libexec/victus-hub/flatpak-daemon`
- `/etc/systemd/system/victus-hubd.service`
- `/etc/tmpfiles.d/victus-hubd.conf`
- `/etc/victus-hub/flatpak-deploy-dir`

It enables and restarts `victus-hubd`. The socket is `/run/victus-hubd/victus-hub.sock`. Host Python 3.10 or newer, `libsystemd`, `/usr/bin/loginctl`, and `busctl` come from the base image. Nothing is written under `/usr` except through the writable `/usr/local` link.

Open Victus Hub from the app menu, or run `flatpak run io.github.evident0.VictusHub`.

If controls fail after the window opens, check `journalctl -u victus-hubd -b`. An access-denied line means the daemon rejected the sandboxed client.

## Remove the host daemon

```bash
sudo packaging/flatpak/host/uninstall
sudo flatpak uninstall --system io.github.evident0.VictusHub
```

`host/uninstall` removes only a unit whose `ExecStart` is the Flatpak wrapper. It leaves `/var/lib/victus-hubd` and `~/.config/victus-hub/` in place. Do not run it against a `scripts/install` unit.
