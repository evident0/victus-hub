#!/usr/bin/env bash
# Shared optional kernel-module / tool prompts for install and dev-run.
# Caller must set ROOT_DIR to the repository root before sourcing.

: "${ROOT_DIR:?ROOT_DIR must be set before sourcing kmod-prompts.sh}"

HP_WMI_SRC="${ROOT_DIR}/kernel/hp-wmi/hp-wmi.c"
HP_WMI_KMOD_INSTALL="${ROOT_DIR}/kernel/hp-wmi/scripts/install"
HP_WMI_MODULE_SYS_NAME="hp_wmi"
HP_WMI_MODULE_NAME="hp-wmi"
HP_WMI_PLATFORM_PATH="/sys/devices/platform/hp-wmi"

KBD_RGB_KMOD_INSTALL="${ROOT_DIR}/kernel/hp-kbd-rgb/scripts/install"
KBD_RGB_MODULE_SYS_NAME="hp_kbd_rgb"
KBD_RGB_MODULE_NAME="hp-kbd-rgb"

GPU_MUX_KMOD_INSTALL="${ROOT_DIR}/kernel/hp-gpu-mux/scripts/install"
GPU_MUX_MODULE_SYS_NAME="hp_gpu_mux"
GPU_MUX_MODULE_NAME="hp-gpu-mux"

RYZENADJ_INSTALL="${ROOT_DIR}/scripts/ryzenadj-install"
RYZENADJ_BIN="/usr/local/bin/ryzenadj"

# Read DMI board name (trimmed). Empty if unavailable.
hp_wmi_dmi_board_name() {
	tr -d '[:space:]' </sys/class/dmi/id/board_name 2>/dev/null || true
}

# True if board id appears as a quoted DMI entry in our custom hp-wmi.c.
hp_wmi_board_is_listed() {
	local board=$1
	local src=${2:-$HP_WMI_SRC}

	if [ -z "$board" ] || [ ! -f "$src" ]; then
		return 1
	fi
	# Board ids in the driver are 4-char hex strings in double quotes.
	grep -qE "\"${board}\"" "$src"
}

# Prompt; returns 0 on yes, 1 on no.
prompt_yes_no() {
	local prompt=$1
	local reply
	printf '%s' "$prompt"
	# Read from the controlling terminal, not stdin. Under a curl|bash
	# one-liner, stdin is the script itself (already consumed by bash), so a
	# plain `read` hits EOF and every prompt silently defaults to "no".
	# /dev/tty is the user's terminal; sudo preserves it (with or without
	# use_pty). Falls back to stdin when no controlling tty exists.
	if [ -r /dev/tty ]; then
		read -r reply </dev/tty
	else
		read -r reply
	fi
	[[ "$reply" =~ ^[Yy]$ ]]
}

run_install_script() {
	local path=$1
	local label=$2

	if [ ! -x "$path" ]; then
		printf '%s install script not found or not executable: %s\n' "$label" "$path" >&2
		return 1
	fi
	bash "$path"
}

# Offer custom hp-wmi when the board is listed; if not listed, ask before
# installing and skip on no.
maybe_install_hp_wmi() {
	local board
	local listed=0

	board="$(hp_wmi_dmi_board_name)"
	if [ -z "$board" ]; then
		printf 'Could not read DMI board name from /sys/class/dmi/id/board_name.\n'
	else
		printf 'DMI board name: %s\n' "$board"
	fi

	if [ -n "$board" ] && hp_wmi_board_is_listed "$board"; then
		listed=1
		printf 'Custom %s lists this board (thermal / Victus-S profile support).\n' \
			"$HP_WMI_MODULE_NAME"
	else
		printf 'Custom %s has no explicit board entry for %s.\n' \
			"$HP_WMI_MODULE_NAME" "${board:-unknown}"
		printf 'In-tree/generic hp-wmi may still load; custom module adds board-specific tables.\n'
	fi

	# In-tree (or already loaded) driver already exposes the platform device.
	if [ -d "$HP_WMI_PLATFORM_PATH" ]; then
		printf 'Note: %s is available; custom %s module may not be necessary.\n' \
			"$HP_WMI_PLATFORM_PATH" "$HP_WMI_MODULE_NAME"
	fi

	if [ "$listed" -eq 0 ]; then
		if ! prompt_yes_no "Install custom ${HP_WMI_MODULE_NAME} anyway? [y/N] "; then
			printf 'Skipping custom %s.\n' "$HP_WMI_MODULE_NAME"
			return 0
		fi
	else
		local prompt
		if [ -d "/sys/module/${HP_WMI_MODULE_SYS_NAME}" ]; then
			prompt="Install/rebuild custom ${HP_WMI_MODULE_NAME} kernel module? [y/N] "
		else
			prompt="Install custom ${HP_WMI_MODULE_NAME} kernel module? [y/N] "
		fi
		if ! prompt_yes_no "$prompt"; then
			printf 'Skipping custom %s.\n' "$HP_WMI_MODULE_NAME"
			return 0
		fi
	fi

	printf '\033[1;34m── Installing custom %s kernel module ──\033[0m\n' "$HP_WMI_MODULE_NAME"
	run_install_script "$HP_WMI_KMOD_INSTALL" "$HP_WMI_MODULE_NAME"
}

maybe_install_kbd_rgb() {
	local prompt
	if [ -d "/sys/module/${KBD_RGB_MODULE_SYS_NAME}" ]; then
		prompt="${KBD_RGB_MODULE_NAME} is already loaded. Reinstall/rebuild kernel module? [y/N] "
	else
		prompt="Install ${KBD_RGB_MODULE_NAME} kernel module? [y/N] "
	fi
	if ! prompt_yes_no "$prompt"; then
		printf 'Skipping %s kernel module install.\n' "$KBD_RGB_MODULE_NAME"
		return 0
	fi
	printf '\033[1;34m── Installing %s kernel module ──\033[0m\n' "$KBD_RGB_MODULE_NAME"
	run_install_script "$KBD_RGB_KMOD_INSTALL" "$KBD_RGB_MODULE_NAME"
}

maybe_install_gpu_mux() {
	local prompt
	if [ -d "/sys/module/${GPU_MUX_MODULE_SYS_NAME}" ]; then
		prompt="${GPU_MUX_MODULE_NAME} is already loaded. Reinstall/rebuild kernel module? [y/N] "
	else
		prompt="Install ${GPU_MUX_MODULE_NAME} kernel module? [y/N] "
	fi
	if ! prompt_yes_no "$prompt"; then
		printf 'Skipping %s kernel module install.\n' "$GPU_MUX_MODULE_NAME"
		return 0
	fi
	printf '\033[1;34m── Installing %s kernel module ──\033[0m\n' "$GPU_MUX_MODULE_NAME"
	run_install_script "$GPU_MUX_KMOD_INSTALL" "$GPU_MUX_MODULE_NAME"
}

maybe_install_ryzenadj() {
	if ! grep -qm1 'AuthenticAMD' /proc/cpuinfo; then
		printf 'Skipping ryzenadj (not an AMD CPU).\n'
		return 0
	fi

	local prompt
	if [ -x "$RYZENADJ_BIN" ]; then
		prompt='ryzenadj is already installed. Reinstall/rebuild it? [y/N] '
	else
		prompt='Install ryzenadj (clone + build from source)? [y/N] '
	fi
	if ! prompt_yes_no "$prompt"; then
		printf 'Skipping ryzenadj install.\n'
		return 0
	fi
	printf '\033[1;34m── Installing ryzenadj ──\033[0m\n'
	run_install_script "$RYZENADJ_INSTALL" "ryzenadj"
}

# Prompt for all optional kernel modules / tools (hp-wmi, rgb, mux, ryzenadj).
prompt_optional_components() {
	printf '\033[1;34m── Optional kernel modules / tools ──\033[0m\n'
	maybe_install_hp_wmi
	maybe_install_kbd_rgb
	maybe_install_gpu_mux
	maybe_install_ryzenadj
}
