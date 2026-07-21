#!/usr/bin/env bash
# One-shot Victus Hub installer — fetch + run with:
#   curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/install.sh | sudo bash
#
# Clones into a PERSISTENT /opt/victus-hub, not a tmpdir. scripts/install runs
# `pip install -e .` (editable) and templates the systemd unit's
# WorkingDirectory/PYTHONPATH to this path, so the source must survive this
# script. A tmpdir + EXIT `rm -rf` leaves the editable install pointing at
# deleted files → `victus-hub` fails with ModuleNotFoundError: No module named
# 'victus_hub' and the daemon's unit points at a gone directory.
set -euo pipefail

REPO_URL="https://github.com/evident0/victus-hub.git"
INSTALL_DIR="/opt/victus-hub"

echo '==> Cloning victus-hub...'
# Fresh, deterministic clone: clear any prior copy (previous install or an
# aborted run) so re-runs don't leave stale files.
rm -rf "$INSTALL_DIR"
git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"

echo '==> Running installer...'
cd "$INSTALL_DIR"
bash scripts/install
