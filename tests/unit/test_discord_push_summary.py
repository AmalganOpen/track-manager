"""Unit tests for discord_push_summary commit parsing."""

import importlib.util
import os
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "discord_push_summary.py"
_spec = importlib.util.spec_from_file_location("discord_push_summary", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_commit_records = _mod.parse_commit_records
FIELD_SEP = _mod.FIELD_SEP
COMMIT_SEP = _mod.COMMIT_SEP
is_silent_commit = _mod.is_silent_commit


def _record(sha: str, subject: str, body: str, author: str) -> str:
    return f"{sha}{FIELD_SEP}{subject}{FIELD_SEP}{body}{FIELD_SEP}{author}{COMMIT_SEP}"


def test_parse_single_commit_no_body() -> None:
    raw = _record("abc123", "feat: add thing", "", "Alice")
    commits = parse_commit_records(raw)
    assert commits == [("abc123", "feat: add thing", "", "Alice")]


def test_parse_commit_with_multiline_body() -> None:
    body = "Line one\nLine two\n\nMore detail"
    raw = _record("def456", "fix: parse commits", body, "Bob")
    commits = parse_commit_records(raw)
    assert commits == [("def456", "fix: parse commits", body, "Bob")]


def test_parse_multiple_commits() -> None:
    raw = _record("aaa", "first", "", "A") + _record("bbb", "second", "body", "B")
    commits = parse_commit_records(raw)
    assert len(commits) == 2
    assert commits[0][0] == "aaa"
    assert commits[1][2] == "body"


def test_silent_marker_in_body() -> None:
    assert is_silent_commit("subject\nbody with --silent marker")


def test_get_commits_for_revisions_dedupes(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run_git(*args: str) -> str:
        calls.append(args)
        if args[:2] == ("rev-parse", "--verify"):
            return args[2]
        if args[0] == "log" and args[-1] == "aaa":
            return _record("aaa", "first", "", "A")
        if args[0] == "log" and args[-1] == "bbb":
            return _record("bbb", "second", "", "B")
        raise AssertionError(args)

    monkeypatch.setattr(_mod, "run_git", fake_run_git)
    commits = _mod.get_commits_for_revisions(["aaa", "aaa"])
    assert len(commits) == 1
    assert commits[0][0] == "aaa"


def test_compare_url_single_commit(monkeypatch) -> None:
    monkeypatch.setattr(_mod, "run_git", lambda *args: "deadbeef" * 5)
    url = _mod.compare_url_for_revisions("owner/repo", ["abc123"])
    assert url == "https://github.com/owner/repo/commit/" + "deadbeef" * 5


def test_load_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "export ANTHROPIC_API_KEY='sk-ant-test'\n"
        "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/t\n"
    )
    loaded = _mod.load_env_file(env_file)
    assert loaded == env_file
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert "discord.com" in os.environ["DISCORD_WEBHOOK_URL"]


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\n")
    _mod.load_env_file(env_file)
    assert os.environ["ANTHROPIC_API_KEY"] == "already-set"
