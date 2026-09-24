"""Durable, per-user program keybinds for the daemon's keyboard watcher."""

import json
import os
from pathlib import Path
import threading

from victus_hub.backend.shortcut_policy import validate_shortcut

DEFAULT_PATH = Path("/var/lib/victus-hubd/program-shortcuts.json")


class ProgramShortcuts:
    def __init__(self, path: Path | None = None):
        self._path = path or DEFAULT_PATH
        self._lock = threading.Lock()
        bindings: dict[int, tuple[tuple[int, ...], int]] = {}
        try:
            raw = json.loads(self._path.read_text())
            if isinstance(raw, dict):
                for uid, binding in raw.items():
                    try:
                        user = int(uid)
                        if user <= 0 or str(user) != uid or not isinstance(binding, dict):
                            continue
                        bindings[user] = validate_shortcut(
                            binding.get("mods"), binding.get("key")
                        )
                    except (RuntimeError, ValueError, TypeError):
                        continue
        except (OSError, ValueError):
            pass
        self._snapshot = (bindings, frozenset(binding for binding in bindings.values() if binding[1]))

    def get(self, uid: int) -> tuple[tuple[int, ...], int] | None:
        return self._snapshot[0].get(uid)

    def matches_any(self, mods: tuple[int, ...], key: int) -> bool:
        return (mods, key) in self._snapshot[1]

    def set(self, uid: int, mods: tuple[int, ...], key: int) -> None:
        if uid <= 0:
            raise RuntimeError("a desktop user is required")
        binding = validate_shortcut(mods, key)
        with self._lock:
            if self._snapshot[0].get(uid) == binding:
                return
            updated = dict(self._snapshot[0])
            updated[uid] = binding
            tmp = self._path.with_suffix(".json.tmp")
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as stream:
                    json.dump({str(user): {"mods": m, "key": k}
                               for user, (m, k) in updated.items()}, stream)
                    stream.write("\n")
                tmp.replace(self._path)
            except OSError as error:
                raise RuntimeError(f"could not save program shortcut: {error}") from error
            self._snapshot = (updated, frozenset(item for item in updated.values() if item[1]))
