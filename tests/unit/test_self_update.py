"""Tests for self-update helpers."""

from pathlib import Path
from unittest.mock import patch

import pytest

from track_manager.self_update import project_root, update_checkout


def test_project_root_points_at_repo() -> None:
    root = project_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "track_manager").is_dir()


def test_update_checkout_runs_git_and_pip(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'track-manager'\n")

    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> None:
        calls.append(command)
        assert cwd == tmp_path

    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with patch("track_manager.self_update.run_command", side_effect=fake_run):
            update_checkout(tmp_path)

    assert calls[0] == ["git", "fetch"]
    assert calls[1] == ["git", "pull"]
    assert calls[2][1:4] == ["-m", "pip", "install"]
    assert calls[2][4] == "-e"
    assert calls[2][5] == str(tmp_path)


def test_update_checkout_skips_install(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> None:
        calls.append(command)

    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with patch("track_manager.self_update.run_command", side_effect=fake_run):
            update_checkout(tmp_path, reinstall=False)

    assert len(calls) == 2
    assert calls[0] == ["git", "fetch"]
    assert calls[1] == ["git", "pull"]


def test_update_checkout_requires_git(tmp_path: Path) -> None:
    with patch("track_manager.self_update.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="git is not installed"):
            update_checkout(tmp_path)


def test_update_checkout_requires_git_dir(tmp_path: Path) -> None:
    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with pytest.raises(RuntimeError, match="Not a git checkout"):
            update_checkout(tmp_path)
