#!/usr/bin/env bash
# One-shot Victus Hub installer — fetch + run with:
#   curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/install.sh | sudo bash
#
# Keeps a source checkout; the app and DKMS sources are installed separately.
set -euo pipefail

REPO_URL="https://github.com/evident0/victus-hub.git"
INSTALL_DIR="/opt/victus-hub"

# printf, not a heredoc: this file is executed as `curl | bash`, and a
# heredoc would read the rest of the script from that same stdin.
printf '\033[1;36m'
printf '%s\n' \
	'__     _____ ____ _____ _   _ ____    _   _ _   _ ____  ' \
	'\ \   / /_ _/ ___|_   _| | | / ___|  | | | | | | | __ ) ' \
	' \ \ / / | | |     | | | | | \___ \  | |_| | | | |  _ \ ' \
	'  \ V /  | | |___  | | | |_| |___) | |  _  | |_| | |_) |' \
	'   \_/  |___\____| |_|  \___/|____/  |_| |_|\___/|____/ '
printf '\033[0m\n'

echo '==> Cloning victus-hub...'
if ! command -v git >/dev/null 2>&1; then
	printf 'Missing git. Install it yourself and rerun:\n  Ubuntu/Mint: sudo apt install git\n  Arch: sudo pacman -S git\n  Fedora: sudo dnf install git\n' >&2
	exit 1
fi
STAGING=$(mktemp -d /opt/victus-hub-download.XXXXXXXX)
trap 'rm -rf "$STAGING"' EXIT
git clone --depth 1 "$REPO_URL" "$STAGING/source"
# Check requirements before replacing a legacy editable checkout.
source "$STAGING/source/scripts/preflight.sh"
preflight 0
rm -rf "$INSTALL_DIR"
mv "$STAGING/source" "$INSTALL_DIR"

echo '==> Running installer...'
cd "$INSTALL_DIR"
# Banner already printed above; don't print it again from scripts/install.
VICTUS_HUB_BANNER_SHOWN=1 bash scripts/install
