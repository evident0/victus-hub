#!/usr/bin/env bash
# Read-only dependency checks. Never install distribution packages here.
preflight() {
	local app_only=${1:-0} kernel missing=() tool packages header
	kernel=$(uname -r)
	for tool in python3 systemctl loginctl gdbus; do
		command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
	done
	if [ "$(id -u)" -ne 0 ]; then
		command -v sudo >/dev/null 2>&1 || missing+=(sudo)
	fi
	if [ -x /usr/bin/python3 ]; then
		/usr/bin/python3 -I -c 'import sys; sys.exit(sys.version_info < (3, 10))' || missing+=("System Python >= 3.10")
		/usr/bin/python3 -I -c 'import venv, ensurepip' 2>/dev/null || missing+=("Python venv/ensurepip")
		/usr/bin/python3 -I -c 'import ctypes; ctypes.CDLL("libsystemd.so.0")' 2>/dev/null || missing+=("libsystemd")
		# PySide wheels still require these system libraries for the xcb plugin.
		for tool in libdbus-1.so.3 libfontconfig.so.1 libEGL.so.1 libGL.so.1 libwayland-client.so.0 libwayland-cursor.so.0 libwayland-egl.so.1 libX11-xcb.so.1 libxkbcommon.so.0 libxkbcommon-x11.so.0 libxcb-cursor.so.0 libxcb-icccm.so.4 libxcb-image.so.0 libxcb-keysyms.so.1 libxcb-render-util.so.0 libxcb-shape.so.0 libxcb-xinerama.so.0; do
			/usr/bin/python3 -I -c 'import ctypes, sys; ctypes.CDLL(sys.argv[1])' "$tool" 2>/dev/null || missing+=("$tool")
		done
	else
		missing+=("/usr/bin/python3")
	fi
	[ -d /run/systemd/system ] || missing+=("a booted systemd system")
	if [ "$app_only" -eq 0 ]; then
		for tool in git make gcc ld objcopy dkms modprobe modinfo depmod xz gzip zstd; do
			command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
		done
		[ -d "/lib/modules/$kernel/build" ] || missing+=("headers/devel for running kernel $kernel")
		# Ubuntu/Mint split common headers from the flavour-specific build tree.
		for header in "/lib/modules/$kernel/"{build,source}/include/linux/platform_profile.h "/usr/src/linux-headers-${kernel%-*}/include/linux/platform_profile.h"; do
			[ -f "$header" ] || continue
			grep -q devm_platform_profile_register "$header" || missing+=("a newer supported kernel: $kernel lacks devm_platform_profile_register")
			break
		done
		if [ -d /sys/firmware/efi ]; then
			for tool in mokutil openssl; do
				command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
			done
		fi
		if grep -qm1 AuthenticAMD /proc/cpuinfo; then
			for tool in cmake g++ pkg-config; do
				command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
			done
			pkg-config --exists libpci 2>/dev/null || missing+=("libpci development headers")
		fi
	fi
	if [ "${#missing[@]}" -eq 0 ]; then
		printf 'Dependency preflight passed.\n'
		return 0
	fi
	printf 'Install prerequisites yourself, then rerun the installer. Missing requirements:\n' >&2
	printf '  - %s\n' "${missing[@]}" >&2
	if command -v apt-get >/dev/null 2>&1; then
		packages="python3 python3-venv libsystemd0 libglib2.0-bin libdbus-1-3 libfontconfig1 libegl1 libgl1 libwayland-client0 libwayland-cursor0 libwayland-egl1 libx11-xcb1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0"
		[ "$app_only" -eq 1 ] || packages+=" git build-essential dkms linux-headers-$kernel mokutil openssl cmake pkg-config libpci-dev xz-utils gzip zstd"
		printf '\nUbuntu/Mint/Debian package names (install the missing ones):\n  sudo apt install %s\n' "$packages" >&2
	elif command -v pacman >/dev/null 2>&1; then
		printf '\nArch package names (install the missing ones):\n  sudo pacman -S --needed python systemd glib2 dbus fontconfig mesa libglvnd wayland libxkbcommon libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil libxcb\n' >&2
		[ "$app_only" -eq 1 ] || printf '  sudo pacman -S --needed base-devel git dkms linux-headers mokutil openssl cmake pciutils xz gzip zstd\nUse linux-lts-headers/linux-zen-headers instead if that is your kernel.\n' >&2
	elif command -v dnf >/dev/null 2>&1; then
		printf '\nFedora package names (install the missing ones):\n  sudo dnf install python3 python3-pip systemd-libs glib2 dbus-libs fontconfig mesa-libEGL libglvnd-glx libwayland-client libwayland-cursor libwayland-egl libX11-xcb libxkbcommon libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil libxcb\n' >&2
		[ "$app_only" -eq 1 ] || printf '  sudo dnf install git gcc gcc-c++ make binutils dkms kernel-devel-%s mokutil openssl cmake pkgconf-pkg-config pciutils-devel xz gzip zstd\n' "$kernel" >&2
	else
		printf '\nUse your distribution package manager to install the requirements listed above.\n' >&2
	fi
	printf 'Kernel headers must match uname -r. Older kernel APIs require a kernel upgrade, not just headers.\n' >&2
	return 1
}
