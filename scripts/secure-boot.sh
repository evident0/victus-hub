#!/usr/bin/env bash
# Shared by the kernel installers. Caller supplies SUDO and KERNEL_RELEASE.
# Keep keys outside the checkout (the one-shot installer replaces /opt).
MOK_DIR=/var/lib/victus-hub/mok
MOK_KEY="$MOK_DIR/MOK.priv"
MOK_CERT="$MOK_DIR/MOK.der"
MOK_PENDING=0
SECURE_BOOT=0
SIGN_FILE=

secure_boot_prepare() {
	local state candidate fingerprint pending
	if ! command -v mokutil >/dev/null 2>&1; then
		if [ -d /sys/firmware/efi ]; then
			printf 'Install mokutil (dnf/apt install mokutil, or pacman -S mokutil), then rerun the installer.\n' >&2
			return 1
		fi
		return 0
	fi
	if ! state=$(LC_ALL=C mokutil --sb-state 2>&1); then
		if [ ! -d /sys/firmware/efi ]; then return 0; fi
		printf 'Cannot determine Secure Boot state: %s\n' "$state" >&2
		return 1
	fi
	case "$state" in
		*'SecureBoot enabled'*) SECURE_BOOT=1 ;;
		*'SecureBoot disabled'*) return 0 ;;
		*) printf 'Unrecognized Secure Boot state: %s\n' "$state" >&2; return 1 ;;
	esac
	if ! command -v openssl >/dev/null 2>&1; then
		printf 'Secure Boot requires openssl. Install it and rerun the installer.\n' >&2
		return 1
	fi
	for candidate in \
		"/lib/modules/$KERNEL_RELEASE/build/scripts/sign-file" \
		"/usr/src/kernels/$KERNEL_RELEASE/scripts/sign-file" \
		"/usr/src/linux-headers-$KERNEL_RELEASE/scripts/sign-file"; do
		if [ -x "$candidate" ]; then SIGN_FILE=$candidate; break; fi
	done
	if [ -z "$SIGN_FILE" ]; then
		printf 'Secure Boot requires scripts/sign-file from the headers/devel package for %s.\n' "$KERNEL_RELEASE" >&2
		return 1
	fi
	"${SUDO[@]}" install -d -m700 "$MOK_DIR"
	if ! "${SUDO[@]}" test -f "$MOK_KEY" && ! "${SUDO[@]}" test -f "$MOK_CERT"; then
		printf 'Generating persistent Victus Hub module-signing key...\n'
		"${SUDO[@]}" bash -c 'umask 077; exec openssl req -new -x509 -newkey rsa:2048 -nodes -days 36500 -subj /CN=Victus-Hub/ -keyout "$1" -outform DER -out "$2"' bash "$MOK_KEY" "$MOK_CERT"
	elif ! "${SUDO[@]}" test -f "$MOK_KEY" || ! "${SUDO[@]}" test -f "$MOK_CERT"; then
		printf 'Incomplete signing key pair in %s; restore the missing file before reinstalling.\n' "$MOK_DIR" >&2
		return 1
	fi
	"${SUDO[@]}" chmod 600 "$MOK_KEY"
	if "${SUDO[@]}" mokutil --test-key "$MOK_CERT" >/dev/null 2>&1; then
		printf 'Victus Hub signing key is already enrolled.\n'
		return 0
	fi
	MOK_PENDING=1
	# Reuse an existing request so installing the second module does not prompt again.
	fingerprint=$("${SUDO[@]}" openssl x509 -inform DER -in "$MOK_CERT" -noout -fingerprint -sha1)
	fingerprint=${fingerprint#*=}
	pending=$("${SUDO[@]}" env LC_ALL=C mokutil --list-new) || return 1
	if ! grep -qiF -- "$fingerprint" <<< "$pending"; then
		# stdin may contain the installer itself when invoked with curl | sudo bash.
		if (exec </dev/tty) 2>/dev/null; then
			printf 'Choose a one-time MOK enrollment password; you will enter it again at reboot.\n'
			"${SUDO[@]}" mokutil --import "$MOK_CERT" </dev/tty || return 1
		else
			printf 'No terminal available for MOK enrollment. Run:\n  sudo mokutil --import %s\n' "$MOK_CERT"
		fi
	fi
	secure_boot_instructions
}

secure_boot_instructions() {
	printf '\nSecure Boot: the custom drivers cannot load until the signing key is enrolled.\n'
	printf 'Reboot and select Enroll MOK → Continue → Yes in MOK Manager,\n'
	printf 'enter the password chosen during import, then reboot again.\n'
	printf 'If no enrollment screen appears, use a shim/MOK-capable bootloader and run:\n'
	printf '  sudo mokutil --import %s\n' "$MOK_CERT"
}

# Sign a fresh staged copy, never append signatures to an incremental build output.
# Signing must succeed before replacing a working installed module.
secure_boot_install_module() (
	local source=$1 destination=$2 staging
	if [ "$SECURE_BOOT" -eq 0 ]; then
		"${SUDO[@]}" install -Dm644 "$source" "$destination"
		return
	fi
	staging=$(mktemp -d)
	trap 'rm -rf "$staging"' EXIT
	cp "$source" "$staging/module.ko"
	printf 'Signing %s for Secure Boot...\n' "${source##*/}"
	"${SUDO[@]}" "$SIGN_FILE" sha256 "$MOK_KEY" "$MOK_CERT" "$staging/module.ko"
	"${SUDO[@]}" install -Dm644 "$staging/module.ko" "$destination"
)
