"""Utility functions; ports util.rs exactly."""

import shutil
import subprocess
from pathlib import Path


def command_exists(command: str) -> bool:
    return command_path(command) is not None


def command_path(command: str) -> Path | None:
    if "/" in command:
        path = Path(command)
        return path if path.exists() else None
    result = shutil.which(command)
    return Path(result) if result else None


def _run_command_core(
    path: Path,
    args: list[str],
    *,
    raise_on_error: bool,
    timeout: float,
) -> str | None:
    try:
        result = subprocess.run(
            [str(path)] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        if raise_on_error:
            raise RuntimeError(f"failed to run {path}: {e}") from e
        return None

    if result.returncode != 0:
        if raise_on_error:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            message = stderr if stderr else stdout
            if not message:
                message = f"{path} exited with {result.returncode}"
            raise RuntimeError(message)
        return None

    return result.stdout.strip()


def run_command(path: Path, args: list[str]) -> str | None:
    """Run a command, returning trimmed stdout on success, or None on failure."""
    return _run_command_core(path, args, raise_on_error=False, timeout=5.0)


def run_profile_command(path: Path, args: list[str]) -> None:
    """Run a profile command; raise RuntimeError on failure."""
    _run_command_core(path, args, raise_on_error=True, timeout=10.0)
