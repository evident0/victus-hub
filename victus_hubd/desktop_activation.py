"""Request freedesktop application activation on an authorized user's bus."""

import logging
import pwd
from pathlib import Path
import subprocess

from victus_hubd.auth import Peer

logger = logging.getLogger(__name__)
BUS_NAME = "io.github.evident0.VictusHub"
OBJECT_PATH = "/io/github/evident0/VictusHub"


def activate(peer: Peer) -> None:
    """Talk to the user's session bus as that user, never launch a root GUI."""
    if not peer.authorized() or peer.uid <= 0:
        return
    runtime_dir = Path(f"/run/user/{peer.uid}")
    bus_path = runtime_dir / "bus"
    if not bus_path.is_socket():
        return
    try:
        user = pwd.getpwuid(peer.uid)
        result = subprocess.run(
            ["/usr/bin/gdbus", "call", "--address", f"unix:path={bus_path}",
             "--dest", BUS_NAME, "--object-path", OBJECT_PATH,
             "--method", "org.freedesktop.Application.Activate", "{}"],
            user=peer.uid, group=user.pw_gid, extra_groups=[],
            env={"XDG_RUNTIME_DIR": str(runtime_dir),
                 "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus_path}",
                 "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode:
            logger.warning("program shortcut activation failed: %s", result.stderr.strip())
    except (OSError, KeyError, subprocess.TimeoutExpired):
        logger.exception("program shortcut activation failed")
