#!/usr/bin/env bash
# One-shot Victus Hub installer — fetch + run with:
#   curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/install.sh | sudo bash
set -euo pipefail

REPO_URL="https://github.com/evident0/victus-hub.git"
TMPDIR=$(mktemp -d /tmp/victus-hub-install.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

echo '==> Cloning victus-hub...'
git clone --depth 1 "$REPO_URL" "$TMPDIR"

echo '==> Running installer...'
cd "$TMPDIR"
bash scripts/install
ret=$?
exit $ret
