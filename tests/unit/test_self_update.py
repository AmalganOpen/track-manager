"""Tests for self-update helpers."""

from pathlib import Path
from unittest.mock import patch

import pytest

from track_manager.self_update import (
    UpdateResult,
    project_root,
    pyproject_hash,
    reinstall_editable,
    update_checkout,
)


def test_project_root_points_at_repo() -> None:
    root = project_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "track_manager").is_dir()


def test_update_checkout_runs_git_and_pip_when_deps_change(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'track-manager'\n")

    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> None:
        calls.append(command)
        assert cwd == tmp_path
        if command[:2] == ["git", "pull"]:
            pyproject.write_text(
                "[project]\nname = 'track-manager'\ndependencies = ['click']\n"
            )

    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with patch("track_manager.self_update.run_command", side_effect=fake_run):
            with patch(
                "track_manager.self_update.reinstall_editable", return_value=False
            ) as mock_reinstall:
                result = update_checkout(tmp_path)

    assert calls[0] == ["git", "fetch"]
    assert calls[1] == ["git", "pull"]
    mock_reinstall.assert_called_once_with(tmp_path)
    assert result == UpdateResult(
        dependencies_changed=True,
        reinstall_ran=True,
        reinstall_deferred=False,
    )


def test_update_checkout_skips_pip_when_deps_unchanged(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'track-manager'\n")

    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> None:
        calls.append(command)

    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with patch("track_manager.self_update.run_command", side_effect=fake_run):
            with patch(
                "track_manager.self_update.reinstall_editable"
            ) as mock_reinstall:
                result = update_checkout(tmp_path)

    assert len(calls) == 2
    mock_reinstall.assert_not_called()
    assert result.reinstall_ran is False
    assert result.dependencies_changed is False


def test_update_checkout_force_reinstall(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'track-manager'\n")

    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with patch("track_manager.self_update.run_command"):
            with patch(
                "track_manager.self_update.reinstall_editable", return_value=False
            ) as mock_reinstall:
                result = update_checkout(tmp_path, force_reinstall=True)

    mock_reinstall.assert_called_once_with(tmp_path)
    assert result.reinstall_ran is True


def test_update_checkout_skips_install_flag(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'track-manager'\n")

    def fake_run(command: list[str], *, cwd: Path) -> None:
        if command[:2] == ["git", "pull"]:
            pyproject.write_text("[project]\ndependencies = ['new']\n")

    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with patch("track_manager.self_update.run_command", side_effect=fake_run):
            with patch(
                "track_manager.self_update.reinstall_editable"
            ) as mock_reinstall:
                result = update_checkout(tmp_path, reinstall=False)

    mock_reinstall.assert_not_called()
    assert result.reinstall_ran is False
    assert result.dependencies_changed is True


def test_reinstall_editable_deferred_on_windows(tmp_path: Path) -> None:
    with patch("track_manager.self_update.sys.platform", "win32"):
        with patch("track_manager.self_update.subprocess.Popen") as mock_popen:
            deferred = reinstall_editable(tmp_path)

    assert deferred is True
    mock_popen.assert_called_once()


def test_reinstall_editable_inline_elsewhere(tmp_path: Path) -> None:
    with patch("track_manager.self_update.sys.platform", "linux"):
        with patch("track_manager.self_update.run_command") as mock_run:
            deferred = reinstall_editable(tmp_path)

    assert deferred is False
    mock_run.assert_called_once()


def test_pyproject_hash_changes_with_content(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text("a")
    first = pyproject_hash(tmp_path)
    path.write_text("b")
    assert pyproject_hash(tmp_path) != first


def test_update_checkout_requires_git(tmp_path: Path) -> None:
    with patch("track_manager.self_update.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="git is not installed"):
            update_checkout(tmp_path)


def test_update_checkout_requires_git_dir(tmp_path: Path) -> None:
    with patch("track_manager.self_update.shutil.which", return_value="/usr/bin/git"):
        with pytest.raises(RuntimeError, match="Not a git checkout"):
            update_checkout(tmp_path)
