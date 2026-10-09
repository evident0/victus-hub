#!/usr/bin/env bash
# Read-only dependency checks. Never install distribution packages here.
have_shared_lib() {
	local name=$1 dir
	for dir in /usr/lib64 /lib64 /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu; do
		[ -e "$dir/$name" ] && return 0
	done
	return 1
}

# sudo replaces PATH with secure_path, which hides rustup in ~/.cargo/bin.
# `curl | sudo bash` and `sudo ./scripts/install` still set SUDO_UID.
prepare_rustup_path() {
	local home cargo_bin
	unset VICTUS_HUB_BUILD_HOME
	[ "$(id -u)" -eq 0 ] || return 0
	[ -n "${SUDO_UID:-}" ] || return 0
	home=$(getent passwd "$SUDO_UID" 2>/dev/null | cut -d: -f6 || true)
	[ -n "$home" ] || return 0
	cargo_bin="$home/.cargo/bin"
	if [ ! -x "$cargo_bin/cargo" ] && [ ! -x "$cargo_bin/rustc" ]; then
		return 0
	fi
	if [[ ":$PATH:" != *":$cargo_bin:"* ]]; then
		PATH="$cargo_bin:$PATH"
	fi
	export PATH
	export VICTUS_HUB_BUILD_HOME="$home"
}

# Running the user's rustup as root writes root-owned files into ~/.rustup.
as_build_user() {
	if [ -n "${VICTUS_HUB_BUILD_HOME:-}" ]; then
		# -n and stdin from /dev/null: curl | sudo bash must not prompt or consume the script.
		sudo -n -u "#$SUDO_UID" -- env HOME="$VICTUS_HUB_BUILD_HOME" PATH="$VICTUS_HUB_BUILD_HOME/.cargo/bin:$PATH" "$@" </dev/null
	else
		"$@"
	fi
}

preflight() {
	local app_only=${1:-0} kernel missing=() tool packages header
	prepare_rustup_path
	kernel=$(uname -r)
	for tool in python3 cargo rustc pkg-config systemctl loginctl gdbus; do
		command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
	done
	for tool in 'gtk4 >= 4.14' 'libadwaita-1 >= 1.5'; do
		pkg-config --exists "$tool" 2>/dev/null || missing+=("development package: $tool")
	done
	if command -v rustc >/dev/null 2>&1; then
		local rust_version
		rust_version=$(as_build_user rustc --version)
		if ! [[ "$rust_version" =~ ^rustc\ ([0-9]+)\.([0-9]+) ]] ||
			! (( BASH_REMATCH[1] > 1 || (BASH_REMATCH[1] == 1 && BASH_REMATCH[2] >= 90) )); then
			missing+=("Rust >= 1.90")
		fi
	fi
	if [ "$(id -u)" -ne 0 ]; then
		command -v sudo >/dev/null 2>&1 || missing+=(sudo)
	fi
	for tool in libsystemd.so.0 libgtk-4.so.1 libadwaita-1.so.0; do
		have_shared_lib "$tool" || missing+=("$tool")
	done
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
		packages="python3 cargo rustc libsystemd0 libglib2.0-bin libgtk-4-1 libadwaita-1-0 libgtk-4-dev libadwaita-1-dev"
		[ "$app_only" -eq 1 ] || packages+=" git build-essential dkms linux-headers-$kernel mokutil openssl cmake pkg-config libpci-dev xz-utils gzip zstd"
		printf '\nUbuntu/Mint/Debian package names (install the missing ones):\n  sudo apt install %s\n' "$packages" >&2
	elif command -v pacman >/dev/null 2>&1; then
		printf '\nArch package names (install the missing ones):\n  sudo pacman -S --needed python rust systemd glib2 gtk4 libadwaita\n' >&2
		[ "$app_only" -eq 1 ] || printf '  sudo pacman -S --needed base-devel git dkms linux-headers mokutil openssl cmake pciutils xz gzip zstd\nUse linux-lts-headers/linux-zen-headers instead if that is your kernel.\n' >&2
	elif command -v dnf >/dev/null 2>&1; then
		printf '\nFedora package names (install the missing ones):\n  sudo dnf install python3 cargo rust systemd-libs glib2 gtk4 libadwaita gtk4-devel libadwaita-devel\n' >&2
		[ "$app_only" -eq 1 ] || printf '  sudo dnf install git gcc gcc-c++ make binutils dkms kernel-devel-%s mokutil openssl cmake pkgconf-pkg-config pciutils-devel xz gzip zstd\n' "$kernel" >&2
	else
		printf '\nUse your distribution package manager to install the requirements listed above.\n' >&2
	fi
	printf 'Kernel headers must match uname -r. Older kernel APIs require a kernel upgrade, not just headers.\n' >&2
	return 1
}
