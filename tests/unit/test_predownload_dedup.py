"""Pre-download duplicate checks for the yt-dlp (YouTube/SoundCloud) and direct paths.

These run even with ``--dumb`` (the smart-download dedup is bypassed there), so a
re-download of an already-owned track is caught before any audio is fetched.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from track_manager.sources.direct import DirectDownloader
from track_manager.sources.soundcloud import SoundCloudDownloader
from track_manager.sources.youtube import YouTubeDownloader


class _FakeYDL:
    """Minimal stand-in for yt_dlp.YoutubeDL used by the metadata-only pre-check."""

    def __init__(self, meta=None, raise_exc=False):
        self._meta = meta or {}
        self._raise = raise_exc
        self.download_calls = []

    def extract_info(self, url, download=False):
        self.download_calls.append(download)
        if self._raise:
            raise RuntimeError("extract failed")
        return self._meta


class TestYouTubePredownloadCheck:
    def _downloader(self, tmp_path: Path) -> YouTubeDownloader:
        config = SimpleNamespace(duplicate_handling="skip")
        return YouTubeDownloader(config, tmp_path)

    def test_duplicate_detected_metadata_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dl = self._downloader(tmp_path)
        ydl = _FakeYDL(meta={"artist": "A", "title": "T"})
        monkeypatch.setattr(dl, "check_duplicate_for", lambda a, t, **k: True)

        assert dl._check_predownload_duplicate(ydl, "http://x") is True
        # Only a metadata-only probe was done — no audio download requested.
        assert ydl.download_calls == [False]

    def test_not_duplicate_proceeds(self, tmp_path: Path, monkeypatch) -> None:
        dl = self._downloader(tmp_path)
        ydl = _FakeYDL(meta={"uploader": "A", "title": "T"})
        monkeypatch.setattr(dl, "check_duplicate_for", lambda a, t, **k: False)

        assert dl._check_predownload_duplicate(ydl, "http://x") is False

    def test_extract_failure_is_not_a_duplicate(self, tmp_path: Path) -> None:
        dl = self._downloader(tmp_path)
        ydl = _FakeYDL(raise_exc=True)
        # A failed probe must not block the download (fall through, not skip).
        assert dl._check_predownload_duplicate(ydl, "http://x") is False


class TestDirectPredownloadCheck:
    def test_skips_without_fetching_when_url_owned(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config = SimpleNamespace(
            duplicate_handling="skip", failed_log=tmp_path / "failed.log"
        )
        dl = DirectDownloader(config, tmp_path)

        from track_manager import duplicates as tm_dup

        monkeypatch.setattr(
            tm_dup,
            "find_duplicates_by_track_url",
            lambda url, lib: [tmp_path / "existing.mp3"],
        )

        # Any network fetch would mean the pre-check failed to short-circuit.
        from track_manager.sources import direct as direct_mod

        def _boom(*a, **k):
            raise AssertionError("requests.get must not be called for an owned URL")

        monkeypatch.setattr(direct_mod.requests, "get", _boom)

        dl.download("https://example.com/song.mp3")

    def test_downloads_when_url_not_owned(self, tmp_path: Path, monkeypatch) -> None:
        config = SimpleNamespace(
            duplicate_handling="skip", failed_log=tmp_path / "failed.log"
        )
        dl = DirectDownloader(config, tmp_path)

        from track_manager import duplicates as tm_dup

        monkeypatch.setattr(tm_dup, "find_duplicates_by_track_url", lambda url, lib: [])

        from track_manager.sources import direct as direct_mod

        called = {"get": False}

        def _fake_get(*a, **k):
            called["get"] = True
            raise RuntimeError("stop after pre-check passes")

        monkeypatch.setattr(direct_mod.requests, "get", _fake_get)

        # We don't care that the (faked) download then fails — only that the
        # pre-check let us through to the fetch.
        try:
            dl.download("https://example.com/song.mp3")
        except Exception:
            pass
        assert called["get"] is True


class TestSoundCloudShareUrlRouting:
    def test_in_query_sets_does_not_trigger_playlist_path(self, tmp_path: Path) -> None:
        """Share links put ``/sets/`` in ``?in=`` but the path is still a track."""
        config = SimpleNamespace(
            duplicate_handling="skip", failed_log=tmp_path / "failed.log"
        )
        dl = SoundCloudDownloader(config, tmp_path)

        share_url = (
            "https://soundcloud.com/davejohannes/high-fashion-remix"
            "?in=nikan_prod/sets/mejeriet/s-rDahfT6wfRM"
        )
        called = {"single": False, "playlist": False}

        def fake_single(url: str, fmt: str, **kwargs) -> bool:
            called["single"] = True
            return True

        def fake_playlist(url: str, fmt: str) -> None:
            called["playlist"] = True

        with patch.object(dl, "_download_single", fake_single):
            with patch.object(dl, "_download_playlist", fake_playlist):
                dl.download(share_url)

        assert called["single"] is True
        assert called["playlist"] is False
