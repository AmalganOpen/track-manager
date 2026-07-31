"""Unicode / Windows filename handling."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from track_manager import metadata
from track_manager.metadata import sanitize_filename
from track_manager.rekordbox_xml import _decode_rb_url, _encode_rb_url


def test_sanitize_preserves_chinese_and_other_unicode() -> None:
    assert sanitize_filename("周杰伦 - 晴天") == "周杰伦 - 晴天"
    assert sanitize_filename("アーティスト - 曲名") == "アーティスト - 曲名"
    assert sanitize_filename("Артист - Песня") == "Артист - Песня"


def test_sanitize_still_replaces_filesystem_unsafe_chars() -> None:
    assert sanitize_filename("艺人/名字:歌*?") == "艺人-名字-歌--"


def test_sanitize_strips_control_characters() -> None:
    assert sanitize_filename("歌\x00名\x1f曲") == "歌名曲"


def test_metadata_csv_roundtrips_chinese_paths(tmp_path: Path, monkeypatch) -> None:
    """CSV must use UTF-8 so Windows ANSI code pages don't corrupt paths."""
    csv_path = tmp_path / "review.csv"
    monkeypatch.setattr(metadata, "get_metadata_csv_path", lambda: csv_path)
    monkeypatch.setattr(metadata, "extract_metadata", lambda _p: ("周杰伦", "晴天"))

    chinese_file = tmp_path / "周杰伦 - 晴天.aiff"
    chinese_file.write_bytes(b"")

    metadata.flag_for_review(chinese_file, "junk patterns", "https://example.com/t")

    # BOM present so Excel on Windows detects UTF-8
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["file_path"] == str(chinese_file)
    assert rows[0]["current_artist"] == "周杰伦"
    assert "晴天" in rows[0]["current_title"]


def test_decode_rb_url_percent_encoded_chinese() -> None:
    url = (
        "file://localhost/Users/nikan/Music/"
        "%E5%91%A8%E6%9D%B0%E4%BC%A6%20-%20%E6%99%B4%E5%A4%A9.aiff"
    )
    path = _decode_rb_url(url)
    assert path.name == "周杰伦 - 晴天.aiff"
    assert path.is_absolute()


def test_encode_rb_url_percent_encodes_chinese(tmp_path: Path) -> None:
    track = tmp_path / "周杰伦 - 晴天.aiff"
    track.write_bytes(b"")
    url = _encode_rb_url(track)
    assert url.startswith("file://localhost/")
    assert "%E5%91%A8%E6%9D%B0%E4%BC%A6" in url  # 周杰伦
    assert _decode_rb_url(url).resolve() == track.resolve()


def test_decode_rb_url_windows_drive_form() -> None:
    """Windows Rekordbox exports use /C:/... (optionally with C%3A)."""
    url = "file://localhost/C:/Users/foo/%E4%BD%A0%E5%A5%BD.aiff"
    if sys.platform == "win32":
        path = _decode_rb_url(url)
        assert path.as_posix().endswith("Users/foo/你好.aiff")
        assert path.drive.upper() == "C:"
    else:
        # On POSIX, url2pathname leaves the Windows-shaped path; still
        # must unquote Unicode correctly and not raise.
        path = _decode_rb_url(url)
        assert "你好.aiff" in path.name


def test_decode_rb_url_windows_encoded_colon() -> None:
    url = "file://localhost/C%3A/Music/track.aiff"
    path = _decode_rb_url(url)
    assert path.name == "track.aiff"
