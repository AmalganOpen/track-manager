"""Tests for the smart-download path's quality-aware dedup/upgrade pre-check."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from track_manager import duplicates as tm_dup
from track_manager import upgrade as tm_upgrade
from track_manager.downloader import Downloader


def _make_downloader(tmp_path: Path, handling: str = "skip") -> Downloader:
    """Build a Downloader with a stub config pointed at a temp library."""
    config = SimpleNamespace(duplicate_handling=handling, output_dir=tmp_path)
    return Downloader(config, output_dir=tmp_path)


class TestIsLosslessSource:
    """The lossless-codec classifier used to decide skip-vs-upgrade."""

    @pytest.mark.parametrize(
        "fmt", ["flac", "FLAC", "alac", "aiff", "aif", "wav", "pcm_s16le", "pcm_s24be"]
    )
    def test_lossless_formats(self, fmt: str) -> None:
        assert Downloader._is_lossless_source({"format": fmt}) is True

    @pytest.mark.parametrize("fmt", ["aac", "mp3", "mp4a.40.2", "opus", "", None])
    def test_lossy_or_unknown_formats(self, fmt) -> None:
        assert Downloader._is_lossless_source({"format": fmt}) is False


class TestFindOwnedCopy:
    """ISRC takes precedence over TRACK_URL; artist/title is intentionally ignored."""

    def test_prefers_isrc(self, tmp_path: Path, monkeypatch) -> None:
        dl = _make_downloader(tmp_path)
        isrc_hit = tmp_path / "by_isrc.m4a"
        monkeypatch.setattr(
            tm_dup, "find_duplicates_by_isrc", lambda isrc, lib: [isrc_hit]
        )
        monkeypatch.setattr(
            tm_dup,
            "find_duplicates_by_track_url",
            lambda url, lib: [tmp_path / "by_url.m4a"],
        )
        assert dl._find_owned_copy("http://x", "USRC12345678") == isrc_hit

    def test_falls_back_to_url(self, tmp_path: Path, monkeypatch) -> None:
        dl = _make_downloader(tmp_path)
        url_hit = tmp_path / "by_url.m4a"
        monkeypatch.setattr(tm_dup, "find_duplicates_by_isrc", lambda isrc, lib: [])
        monkeypatch.setattr(
            tm_dup, "find_duplicates_by_track_url", lambda url, lib: [url_hit]
        )
        assert dl._find_owned_copy("http://x", None) == url_hit

    def test_none_when_unowned(self, tmp_path: Path, monkeypatch) -> None:
        dl = _make_downloader(tmp_path)
        monkeypatch.setattr(tm_dup, "find_duplicates_by_isrc", lambda isrc, lib: [])
        monkeypatch.setattr(tm_dup, "find_duplicates_by_track_url", lambda url, lib: [])
        assert dl._find_owned_copy("http://x", "USRC12345678") is None


class TestDedupOrUpgrade:
    """The skip / upgrade / proceed decision."""

    def test_unowned_proceeds(self, tmp_path: Path, monkeypatch) -> None:
        dl = _make_downloader(tmp_path)
        monkeypatch.setattr(dl, "_find_owned_copy", lambda url, isrc: None)
        assert dl._dedup_or_upgrade("http://x", "USRC1", None) is None

    def test_lossless_skip_mode_skips(self, tmp_path: Path, monkeypatch) -> None:
        dl = _make_downloader(tmp_path, handling="skip")
        owned = tmp_path / "owned.flac"
        monkeypatch.setattr(dl, "_find_owned_copy", lambda url, isrc: owned)
        monkeypatch.setattr(
            tm_upgrade,
            "_source_quality",
            lambda p: {"format": "flac", "bitrate_kbps": 900},
        )
        monkeypatch.setattr(tm_dup, "extract_metadata", lambda p: ("Artist", "Title"))
        # Skip mode: a lossless dup is handled (skipped), no upgrade attempted.
        monkeypatch.setattr(
            tm_upgrade,
            "upgrade_track",
            lambda *a, **k: pytest.fail("should not upgrade a lossless copy"),
        )
        assert dl._dedup_or_upgrade("http://x", "USRC1", None) is True

    def test_lossless_keep_mode_proceeds(self, tmp_path: Path, monkeypatch) -> None:
        dl = _make_downloader(tmp_path, handling="keep")
        owned = tmp_path / "owned.flac"
        monkeypatch.setattr(dl, "_find_owned_copy", lambda url, isrc: owned)
        monkeypatch.setattr(
            tm_upgrade,
            "_source_quality",
            lambda p: {"format": "flac", "bitrate_kbps": 900},
        )
        monkeypatch.setattr(tm_dup, "extract_metadata", lambda p: ("Artist", "Title"))
        # keep mode → handle_duplicates returns False → proceed (download a 2nd copy).
        assert dl._dedup_or_upgrade("http://x", "USRC1", None) is None

    def test_lossy_under_cap_upgrades(self, tmp_path: Path, monkeypatch) -> None:
        dl = _make_downloader(tmp_path)
        owned = tmp_path / "owned.aiff"
        monkeypatch.setattr(dl, "_find_owned_copy", lambda url, isrc: owned)
        monkeypatch.setattr(
            tm_upgrade,
            "_source_quality",
            lambda p: {"format": "aac", "bitrate_kbps": 128},
        )
        monkeypatch.setattr(tm_dup, "extract_metadata", lambda p: ("Artist", "Title"))
        monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda p: 0)

        calls = {}

        def fake_upgrade(path, url, config, downloader=None):
            calls["path"] = path
            calls["url"] = url
            calls["downloader"] = downloader
            return True, "Upgraded in-place: owned.aiff (source 128 kbps → 900 kbps)"

        monkeypatch.setattr(tm_upgrade, "upgrade_track", fake_upgrade)

        assert dl._dedup_or_upgrade("http://x", "USRC1", None) is True
        assert calls["path"] == owned
        assert calls["url"] == "http://x"
        assert calls["downloader"] is dl

    def test_lossy_at_cap_skips_without_upgrade(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dl = _make_downloader(tmp_path)
        owned = tmp_path / "owned.aiff"
        monkeypatch.setattr(dl, "_find_owned_copy", lambda url, isrc: owned)
        monkeypatch.setattr(
            tm_upgrade,
            "_source_quality",
            lambda p: {"format": "aac", "bitrate_kbps": 128},
        )
        monkeypatch.setattr(tm_dup, "extract_metadata", lambda p: ("Artist", "Title"))
        monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda p: 2)
        monkeypatch.setattr(
            tm_upgrade,
            "upgrade_track",
            lambda *a, **k: pytest.fail("should not upgrade past the attempt cap"),
        )
        assert dl._dedup_or_upgrade("http://x", "USRC1", None) is True

    def test_lossy_failed_upgrade_still_handled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dl = _make_downloader(tmp_path)
        owned = tmp_path / "owned.aiff"
        monkeypatch.setattr(dl, "_find_owned_copy", lambda url, isrc: owned)
        monkeypatch.setattr(
            tm_upgrade,
            "_source_quality",
            lambda p: {"format": "aac", "bitrate_kbps": 128},
        )
        monkeypatch.setattr(tm_dup, "extract_metadata", lambda p: ("Artist", "Title"))
        monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda p: 0)
        monkeypatch.setattr(
            tm_upgrade,
            "upgrade_track",
            lambda *a, **k: (
                False,
                "New download (128 kbps) is not better than source",
            ),
        )
        # Even a failed upgrade is "handled" — we must not fall through and
        # write a fresh duplicate alongside the existing file.
        assert dl._dedup_or_upgrade("http://x", "USRC1", None) is True

    def test_output_dir_restored_after_upgrade(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dl = _make_downloader(tmp_path)
        owned = tmp_path / "owned.aiff"
        monkeypatch.setattr(dl, "_find_owned_copy", lambda url, isrc: owned)
        monkeypatch.setattr(
            tm_upgrade,
            "_source_quality",
            lambda p: {"format": "aac", "bitrate_kbps": 128},
        )
        monkeypatch.setattr(tm_dup, "extract_metadata", lambda p: ("Artist", "Title"))
        monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda p: 0)

        def fake_upgrade(path, url, config, downloader=None):
            # Mimic upgrade_track repointing the downloader at a temp dir.
            downloader.output_dir = tmp_path / "tmp-upgrade"
            return True, "Upgraded in-place"

        monkeypatch.setattr(tm_upgrade, "upgrade_track", fake_upgrade)

        dl._dedup_or_upgrade("http://x", "USRC1", None)
        assert dl.output_dir == tmp_path
