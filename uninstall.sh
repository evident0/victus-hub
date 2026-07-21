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

echo '==> Cloning victus-hub (for uninstall scripts)...'
rm -rf "$CLONE_DIR"
git clone --depth 1 "$REPO_URL" "$CLONE_DIR"

echo '==> Running uninstaller...'
cd "$CLONE_DIR"
bash scripts/uninstall

# Remove the persistent install dir the one-liner installer left behind.
rm -rf "$CLONE_DIR"
