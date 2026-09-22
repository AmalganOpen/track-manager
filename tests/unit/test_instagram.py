"""Instagram URL parsing, cookies, titles, and routing."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from track_manager import audio as tm_audio
from track_manager.config import Config
from track_manager.downloader import Downloader
from track_manager.duplicates import normalize_track_url
from track_manager.sources import instagram as ig


def _downloader(tmp_path: Path) -> Downloader:
    config = SimpleNamespace(
        output_dir=tmp_path,
        duplicate_handling="skip",
        failed_log=tmp_path / "failed.log",
    )
    return Downloader(config, output_dir=tmp_path)


class TestParseInstagramUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.instagram.com/reel/Chunk8-jurw/",
            "https://www.instagram.com/reels/Cop84x6u7CP/",
            "https://instagram.com/p/aye83DjauH/?igsh=abc",
            "https://www.instagram.com/tv/BkfuX9UB-eK/",
            "https://www.instagram.com/marvelskies.fc/reel/CWqAgUZgCku/",
            "https://www.instagram.com/share/reel/ABC123/",
            "https://www.instagram.com/stories/fruits_zipper/3570766765028588805/",
        ],
    )
    def test_media_urls(self, url: str) -> None:
        assert ig.parse_instagram_url(url) == "media"

    def test_profile(self) -> None:
        assert ig.parse_instagram_url("https://www.instagram.com/someartist/") == (
            "profile"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.instagram.com/explore/tags/house/",
            "https://www.instagram.com/reels/audio/123/",
            "https://www.instagram.com/stories/fruits_zipper/",
            "https://www.instagram.com/",
        ],
    )
    def test_unsupported(self, url: str) -> None:
        assert ig.parse_instagram_url(url) == "unsupported"


class TestDetectSource:
    def test_instagram_hosts(self, tmp_path: Path) -> None:
        dl = _downloader(tmp_path)
        assert (
            dl.detect_source("https://www.instagram.com/reel/Chunk8-jurw/")
            == "instagram"
        )
        assert dl.detect_source("https://instagr.am/p/aye83DjauH/") == "instagram"


class TestArtistTitle:
    def test_prefers_caption_over_generic_title(self) -> None:
        artist, title = ig.instagram_artist_title(
            {
                "title": "Video by naomipq",
                "uploader": "Naomi",
                "channel": "naomipq",
                "description": "Midnight Groove (Original Mix)\n#house #dj",
            }
        )
        assert artist == "Naomi"
        assert title == "Midnight Groove (Original Mix)"

    def test_truncates_long_caption(self) -> None:
        caption = "x" * 200
        _, title = ig.instagram_artist_title(
            {"title": "Video by x", "uploader": "A", "description": caption}
        )
        assert title.endswith("…")
        assert len(title) == ig._TITLE_MAX_CHARS + 1

    def test_keeps_explicit_track_tag(self) -> None:
        artist, title = ig.instagram_artist_title(
            {
                "track": "Real Title",
                "artist": "Real Artist",
                "title": "Video by foo",
                "description": "caption",
            }
        )
        assert artist == "Real Artist"
        assert title == "Real Title"


class TestAuthOpts:
    def _patch_config(self, monkeypatch, **kwargs) -> None:
        defaults = {
            "instagram_cookies_file": None,
            "instagram_cookies_from_browser": None,
        }
        defaults.update(kwargs)
        monkeypatch.setattr(ig, "Config", lambda: SimpleNamespace(**defaults))

    def test_cookies_file_wins(self, monkeypatch) -> None:
        self._patch_config(
            monkeypatch,
            instagram_cookies_file="/tmp/ig.txt",
            instagram_cookies_from_browser="firefox",
        )
        opts = ig._auth_opts()
        assert opts == {"cookiefile": "/tmp/ig.txt"}

    def test_cookies_from_browser(self, monkeypatch) -> None:
        self._patch_config(monkeypatch, instagram_cookies_from_browser="chrome")
        opts = ig._auth_opts()
        assert opts == {"cookiesfrombrowser": ("chrome",)}


class TestConfigFallback:
    def test_instagram_browser_falls_back_to_youtube(self, tmp_path: Path) -> None:
        Config.reset()
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "output_dir: ~/tmp\n"
            "youtube:\n"
            "  cookies_from_browser: firefox\n"
            "instagram:\n"
            "  cookies_file: ''\n"
            "  cookies_from_browser: ''\n"
        )
        config = Config(cfg_path)
        try:
            assert config.instagram_cookies_from_browser == "firefox"
        finally:
            Config.reset()

    def test_explicit_instagram_browser_wins(self, tmp_path: Path) -> None:
        Config.reset()
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "output_dir: ~/tmp\n"
            "youtube:\n"
            "  cookies_from_browser: firefox\n"
            "instagram:\n"
            "  cookies_from_browser: chrome\n"
        )
        config = Config(cfg_path)
        try:
            assert config.instagram_cookies_from_browser == "chrome"
        finally:
            Config.reset()


class TestNormalizeInstagramUrl:
    def test_reel_and_post_same_shortcode(self) -> None:
        reel = normalize_track_url("https://www.instagram.com/reel/Chunk8-jurw/?igsh=x")
        post = normalize_track_url("https://instagram.com/p/Chunk8-jurw/")
        assert reel == post == "https://instagram.com/p/Chunk8-jurw"

    def test_username_prefixed_reel(self) -> None:
        assert (
            normalize_track_url(
                "https://www.instagram.com/marvelskies.fc/reel/CWqAgUZgCku/"
            )
            == "https://instagram.com/p/CWqAgUZgCku"
        )


class TestProfileRejected:
    def test_profile_does_not_call_ytdlp(self, tmp_path: Path) -> None:
        config = SimpleNamespace(
            duplicate_handling="skip", failed_log=tmp_path / "failed.log"
        )
        dl = ig.InstagramDownloader(config, tmp_path)
        with patch.object(ig.yt_dlp, "YoutubeDL") as ydl:
            assert dl.download("https://www.instagram.com/someartist/") is False
            ydl.assert_not_called()


class TestRoutingSkipsSmartDownload:
    def test_instagram_handler_not_smart_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dl = _downloader(tmp_path)
        monkeypatch.setattr(tm_audio, "resolve_format", lambda fmt: "aiff")
        monkeypatch.setattr(dl, "_has_spotify_credentials", lambda: False)

        called = {"smart": False, "ig": False}

        def boom(*a, **k):
            called["smart"] = True
            raise AssertionError("smart download must not run for Instagram")

        monkeypatch.setattr(dl, "try_smart_download", boom)

        class FakeIG:
            def __init__(self, *a, **k):
                pass

            def download(self, url, format="auto"):
                called["ig"] = True
                return True

        monkeypatch.setattr(ig, "InstagramDownloader", FakeIG)
        monkeypatch.setattr(
            "track_manager.deps.ensure_ffmpeg_available",
            lambda: ("ffmpeg", "ffprobe"),
        )

        dl.download("https://www.instagram.com/reel/Chunk8-jurw/")
        assert called["ig"] is True
        assert called["smart"] is False


class TestMp4NotPassthrough:
    def test_aac_in_mp4_is_encoded(self, tmp_path: Path, monkeypatch) -> None:
        src = tmp_path / ".tmp_ig.mp4"
        src.write_bytes(b"video")
        dst = tmp_path / "out.m4a"
        monkeypatch.setattr(tm_audio, "probe_audio", lambda p: {"codec": "aac"})
        monkeypatch.setattr(tm_audio, "is_mp4_container", lambda p: True)

        encoded = {"called": False}

        def fake_encode(fmt, src_path, dst_path):
            encoded["called"] = True
            dst_path.write_bytes(b"audio")
            return dst_path

        monkeypatch.setattr(tm_audio, "encode_to", fake_encode)
        tm_audio.encode_or_passthrough("m4a", src, dst)
        assert encoded["called"] is True
        assert dst.read_bytes() == b"audio"

    def test_aac_m4a_still_passthroughs(self, tmp_path: Path, monkeypatch) -> None:
        src = tmp_path / "track.m4a"
        src.write_bytes(b"aac")
        dst = tmp_path / "out.m4a"
        monkeypatch.setattr(tm_audio, "probe_audio", lambda p: {"codec": "aac"})
        monkeypatch.setattr(tm_audio, "is_mp4_container", lambda p: True)
        monkeypatch.setattr(
            tm_audio,
            "encode_to",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("should passthrough")),
        )
        tm_audio.encode_or_passthrough("m4a", src, dst)
        assert dst.read_bytes() == b"aac"
        assert not src.exists()


class _FakeYDL:
    def __init__(self, info=None, error=None):
        self._info = info or {}
        self._error = error
        self.opts = None

    def __call__(self, opts):
        self.opts = opts
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=True):
        if self._error:
            raise self._error
        return self._info


def _ig_downloader(tmp_path: Path) -> ig.InstagramDownloader:
    config = SimpleNamespace(
        duplicate_handling="skip", failed_log=tmp_path / "failed.log"
    )
    return ig.InstagramDownloader(config, tmp_path)


class TestDownloadFlow:
    def test_empty_title_falls_back_to_unknown(self) -> None:
        artist, title = ig.instagram_artist_title({"uploader": "A"})
        assert artist == "A"
        assert title == "Unknown"

    def test_login_error_prints_hint(self, tmp_path: Path, monkeypatch, capsys) -> None:
        dl = _ig_downloader(tmp_path)
        monkeypatch.setattr(dl, "_check_predownload_duplicate", lambda *a, **k: False)
        fake = _FakeYDL(error=RuntimeError("This content is login required"))
        monkeypatch.setattr(ig.yt_dlp, "YoutubeDL", fake)
        monkeypatch.setattr(ig, "_ydl_opts", lambda d: {})
        monkeypatch.setattr(
            "track_manager.duplicates.find_duplicates_by_track_url",
            lambda *a, **k: [],
        )
        assert dl.download("https://www.instagram.com/reel/abc123/") is False
        err = capsys.readouterr().err
        assert "cookies_from_browser" in err

    def test_single_post_success(self, tmp_path: Path, monkeypatch) -> None:
        dl = _ig_downloader(tmp_path)
        monkeypatch.setattr(dl, "_check_predownload_duplicate", lambda *a, **k: False)
        monkeypatch.setattr(dl, "_process_download", lambda *a, **k: True)
        fake = _FakeYDL(
            info={
                "id": "abc123",
                "title": "Video by foo",
                "uploader": "Foo",
                "description": "Real Track",
            }
        )
        monkeypatch.setattr(ig.yt_dlp, "YoutubeDL", fake)
        monkeypatch.setattr(ig, "_ydl_opts", lambda d: {})
        monkeypatch.setattr(
            "track_manager.duplicates.find_duplicates_by_track_url",
            lambda *a, **k: [],
        )
        assert dl.download("https://www.instagram.com/reel/abc123/") is True

    def test_carousel_processes_each_entry(self, tmp_path: Path, monkeypatch) -> None:
        dl = _ig_downloader(tmp_path)
        monkeypatch.setattr(dl, "_check_predownload_duplicate", lambda *a, **k: False)
        processed: list[str] = []

        def fake_process(info, target_format, playlist_url=None):
            processed.append(info["id"])
            return True

        monkeypatch.setattr(dl, "_process_download", fake_process)
        fake = _FakeYDL(
            info={
                "_type": "playlist",
                "id": "post",
                "entries": [
                    {"id": "a", "title": "Video 1", "uploader": "U"},
                    {"id": "b", "title": "Video 2", "uploader": "U"},
                ],
            }
        )
        monkeypatch.setattr(ig.yt_dlp, "YoutubeDL", fake)
        monkeypatch.setattr(ig, "_ydl_opts", lambda d: {})
        monkeypatch.setattr(
            "track_manager.duplicates.find_duplicates_by_track_url",
            lambda *a, **k: [],
        )
        assert dl.download("https://www.instagram.com/p/carousel/") is True
        assert processed == ["a", "b"]

    def test_skips_owned_url(self, tmp_path: Path, monkeypatch) -> None:
        dl = _ig_downloader(tmp_path)
        monkeypatch.setattr(
            "track_manager.duplicates.find_duplicates_by_track_url",
            lambda *a, **k: [tmp_path / "owned.aiff"],
        )
        with patch.object(ig.yt_dlp, "YoutubeDL") as ydl:
            assert dl.download("https://www.instagram.com/reel/abc123/") is True
            ydl.assert_not_called()

    def test_is_login_error(self) -> None:
        assert ig._is_login_error(RuntimeError("login required"))
        assert not ig._is_login_error(RuntimeError("video unavailable"))
