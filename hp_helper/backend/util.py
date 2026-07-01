"""Utility functions; ports util.rs exactly."""

import os
import subprocess
from pathlib import Path


def path_label(path: Path) -> str:
    return str(path)


def command_exists(command: str) -> bool:
    return command_path(command) is not None


def command_path(command: str) -> Path | None:
    if "/" in command:
        path = Path(command)
        return path if path.exists() else None

    for dir_entry in os.environ.get("PATH", "").split(":"):
        if not dir_entry:
            continue
        candidate = Path(dir_entry) / command
        if candidate.exists():
            return candidate
    return None


def run_command(path: Path, args: list[str]) -> str | None:
    """Run a command, returning trimmed stdout on success, or None on failure."""
    try:
        result = subprocess.run(
            [str(path)] + args,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def run_profile_command(path: Path, args: list[str]) -> None:
    """Run a profile command; raise RuntimeError on failure."""
    try:
        result = subprocess.run(
            [str(path)] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError as e:
        raise RuntimeError(f"failed to run {path_label(path)}: {e}")

    if result.returncode == 0:
        return

    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    message = stderr if stderr else stdout
    if not message:
        message = f"{path_label(path)} exited with {result.returncode}"
    raise RuntimeError(message)
