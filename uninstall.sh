#!/usr/bin/env bash
# One-shot Victus Hub uninstaller — fetch + run with:
#   curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/uninstall.sh | sudo bash
#
# Clones the repo transiently to run scripts/uninstall (which needs the kmod
# uninstall subscripts), then removes the persistent /opt/victus-hub the
# one-liner installer created. A direct `./scripts/uninstall` from an existing
# checkout also works and won't touch /opt/victus-hub in that case.
set -euo pipefail

REPO_URL="https://github.com/evident0/victus-hub.git"
CLONE_DIR="/opt/victus-hub"

if [ ! -f "$CLONE_DIR/scripts/uninstall" ]; then
	command -v git >/dev/null 2>&1 || { printf 'Install git or use an existing checkout to uninstall.\n' >&2; exit 1; }
	echo '==> Cloning victus-hub (for uninstall scripts)...'
	STAGING=$(mktemp -d /opt/victus-hub-uninstall.XXXXXXXX)
	trap 'rm -rf "$STAGING"' EXIT
	git clone --depth 1 "$REPO_URL" "$STAGING/source"
	bash "$STAGING/source/scripts/uninstall"
	exit
fi

echo '==> Running uninstaller...'
cd "$CLONE_DIR"
bash scripts/uninstall

# Remove the persistent install dir the one-liner installer left behind.
rm -rf "$CLONE_DIR"
