"""Parse and manage the failed-downloads log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_LOG_FIELD_SEP = " | "


@dataclass(frozen=True)
class FailedDownload:
    """One line from the failed-downloads log."""

    timestamp: str
    url: str
    error: str


def append_failure(log_path: Path, url: str, error: str) -> None:
    """Append a failed download entry to the log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_entry = f"{timestamp}{_LOG_FIELD_SEP}{url}{_LOG_FIELD_SEP}{error}\n"
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(log_entry)


def parse_failed_log(path: Path) -> list[FailedDownload]:
    """Parse the failed-downloads log file."""
    if not path.exists():
        return []

    entries: list[FailedDownload] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(_LOG_FIELD_SEP, 2)
        if len(parts) != 3:
            continue
        timestamp, url, error = parts
        entries.append(FailedDownload(timestamp=timestamp, url=url, error=error))
    return entries


def summarize_failed(
    entries: list[FailedDownload],
) -> list[tuple[str, str, str]]:
    """Return unique URLs with their latest failure, newest first.

    Each item is ``(url, timestamp, error)``.
    """
    if not entries:
        return []

    latest: dict[str, tuple[str, str]] = {}
    last_index: dict[str, int] = {}
    for index, entry in enumerate(entries):
        latest[entry.url] = (entry.timestamp, entry.error)
        last_index[entry.url] = index

    urls = sorted(last_index, key=lambda url: last_index[url], reverse=True)
    return [(url, latest[url][0], latest[url][1]) for url in urls]


def rewrite_log(path: Path, entries: list[FailedDownload]) -> None:
    """Rewrite the log from parsed entries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not entries:
        path.write_text("", encoding="utf-8")
        return

    lines = [
        f"{entry.timestamp}{_LOG_FIELD_SEP}{entry.url}{_LOG_FIELD_SEP}{entry.error}"
        for entry in entries
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_urls(path: Path, urls: set[str]) -> int:
    """Remove all log lines matching ``urls``. Returns lines removed."""
    entries = parse_failed_log(path)
    kept = [entry for entry in entries if entry.url not in urls]
    removed = len(entries) - len(kept)
    if removed:
        rewrite_log(path, kept)
    return removed


def clear_log(path: Path) -> None:
    """Clear the failed-downloads log."""
    rewrite_log(path, [])
