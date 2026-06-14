"""Update track-manager from a local git checkout."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def project_root() -> Path | None:
    """Return the source tree root for an editable install."""
    root = Path(__file__).resolve().parent.parent
    if (root / "pyproject.toml").is_file():
        return root
    return None


def run_command(command: list[str], *, cwd: Path) -> None:
    """Run a command in the foreground, streaming output to the terminal."""
    subprocess.run(command, cwd=cwd, check=True)


def update_checkout(
    root: Path,
    *,
    reinstall: bool = True,
) -> None:
    """Fetch, pull, and reinstall track-manager from a git checkout."""
    if not shutil.which("git"):
        raise RuntimeError("git is not installed or not on PATH")

    if not (root / ".git").exists():
        raise RuntimeError(f"Not a git checkout: {root}")

    run_command(["git", "fetch"], cwd=root)
    run_command(["git", "pull"], cwd=root)

    if reinstall:
        run_command(
            [sys.executable, "-m", "pip", "install", "-e", str(root)],
            cwd=root,
        )
