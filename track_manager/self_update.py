"""Update track-manager from a local git checkout."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of an update run."""

    dependencies_changed: bool
    reinstall_ran: bool
    reinstall_deferred: bool


def project_root() -> Path | None:
    """Return the source tree root for an editable install."""
    root = Path(__file__).resolve().parent.parent
    if (root / "pyproject.toml").is_file():
        return root
    return None


def pyproject_hash(root: Path) -> str:
    """Hash pyproject.toml so we can detect dependency changes after git pull."""
    digest = hashlib.sha256()
    digest.update((root / "pyproject.toml").read_bytes())
    return digest.hexdigest()


def run_command(command: list[str], *, cwd: Path) -> None:
    """Run a command in the foreground, streaming output to the terminal."""
    subprocess.run(command, cwd=cwd, check=True)


def _pip_install_command(root: Path) -> list[str]:
    return [sys.executable, "-m", "pip", "install", "-e", str(root)]


def _reinstall_editable_inline(root: Path) -> None:
    run_command(_pip_install_command(root), cwd=root)


def _reinstall_editable_deferred(root: Path, *, parent_pid: int) -> None:
    """Run pip after the parent process exits (needed on Windows for tm.exe)."""
    command = _pip_install_command(root)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix="_tm_update.py",
        delete=False,
        encoding="utf-8",
    ) as handle:
        script_path = handle.name
        handle.write(textwrap.dedent(f"""
                import os
                import subprocess
                import sys
                import time

                parent = {parent_pid}
                while True:
                    try:
                        os.kill(parent, 0)
                    except OSError:
                        break
                    time.sleep(0.25)

                try:
                    subprocess.run({command!r}, check=True)
                finally:
                    try:
                        os.remove({script_path!r})
                    except OSError:
                        pass
                """).strip())

    popen_kwargs: dict[str, object] = {
        "cwd": root,
        "close_fds": True,
    }
    if sys.platform == "win32":
        # These constants exist on Windows only; values are stable Win32 API flags.
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200

    subprocess.Popen([sys.executable, script_path], **popen_kwargs)  # type: ignore[arg-type]


def reinstall_editable(root: Path) -> bool:
    """Reinstall the editable package. Returns True if install was deferred."""
    if sys.platform == "win32":
        _reinstall_editable_deferred(root, parent_pid=os.getpid())
        return True

    _reinstall_editable_inline(root)
    return False


def update_checkout(
    root: Path,
    *,
    reinstall: bool = True,
    force_reinstall: bool = False,
) -> UpdateResult:
    """Fetch, pull, and reinstall track-manager from a git checkout."""
    if not shutil.which("git"):
        raise RuntimeError("git is not installed or not on PATH")

    if not (root / ".git").exists():
        raise RuntimeError(f"Not a git checkout: {root}")

    deps_hash_before = pyproject_hash(root)

    run_command(["git", "fetch"], cwd=root)
    run_command(["git", "pull"], cwd=root)

    deps_changed = pyproject_hash(root) != deps_hash_before
    should_reinstall = reinstall and (force_reinstall or deps_changed)
    reinstall_ran = False
    reinstall_deferred = False

    if should_reinstall:
        reinstall_deferred = reinstall_editable(root)
        reinstall_ran = True

    return UpdateResult(
        dependencies_changed=deps_changed,
        reinstall_ran=reinstall_ran,
        reinstall_deferred=reinstall_deferred,
    )
