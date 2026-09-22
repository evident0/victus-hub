#!/usr/bin/env bash
# A missing/unsupported keyboard must not prevent installation of fan controls.
load_optional_rgb() {
	local output
	if output=$(LC_ALL=C "${SUDO[@]}" modprobe hp-kbd-rgb 2>&1); then
		printf 'Keyboard RGB driver loaded.\n'
	else
		printf 'Keyboard RGB is unavailable; continuing without RGB control.\n%s\n' "$output" >&2
		printf 'If this keyboard supports RGB, inspect: journalctl -k -b\n' >&2
		"${SUDO[@]}" rm -f /etc/modules-load.d/hp-kbd-rgb.conf
	fi
}
