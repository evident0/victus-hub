#!/usr/bin/env bash
# One-shot Victus Hub uninstaller — fetch + run with:
#   curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/uninstall.sh | sudo bash
set -euo pipefail

REPO_URL="https://github.com/evident0/victus-hub.git"
TMPDIR=$(mktemp -d /tmp/victus-hub-uninstall.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

echo '==> Cloning victus-hub...'
git clone --depth 1 "$REPO_URL" "$TMPDIR"

echo '==> Running uninstaller...'
cd "$TMPDIR"
bash scripts/uninstall
ret=$?
exit $ret
