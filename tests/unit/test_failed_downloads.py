"""Unit tests for failed_downloads module."""

from pathlib import Path

from track_manager.failed_downloads import (
    append_failure,
    clear_log,
    parse_failed_log,
    remove_urls,
    summarize_failed,
)


def test_parse_failed_log_round_trip(tmp_path: Path) -> None:
    log_path = tmp_path / "failed.txt"
    append_failure(log_path, "https://example.com/a", "timeout")
    append_failure(log_path, "https://example.com/b", "not found")

    entries = parse_failed_log(log_path)
    assert len(entries) == 2
    assert entries[0].url == "https://example.com/a"
    assert entries[0].error == "timeout"
    assert entries[1].url == "https://example.com/b"


def test_parse_failed_log_skips_malformed_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "failed.txt"
    log_path.write_text(
        "2026-01-01 12:00 | https://good.example/track | ok error\n"
        "not a valid line\n"
    )

    entries = parse_failed_log(log_path)
    assert len(entries) == 1
    assert entries[0].url == "https://good.example/track"


def test_summarize_failed_dedupes_newest_first(tmp_path: Path) -> None:
    log_path = tmp_path / "failed.txt"
    log_path.write_text(
        "2026-01-01 10:00 | https://example.com/a | first failure\n"
        "2026-01-01 11:00 | https://example.com/b | b error\n"
        "2026-01-01 12:00 | https://example.com/a | latest failure\n"
    )

    summary = summarize_failed(parse_failed_log(log_path))
    assert [item[0] for item in summary] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert summary[0][2] == "latest failure"


def test_remove_urls_and_clear(tmp_path: Path) -> None:
    log_path = tmp_path / "failed.txt"
    append_failure(log_path, "https://example.com/a", "err a")
    append_failure(log_path, "https://example.com/b", "err b")

    removed = remove_urls(log_path, {"https://example.com/a"})
    assert removed == 1
    assert len(parse_failed_log(log_path)) == 1

    clear_log(log_path)
    assert parse_failed_log(log_path) == []
