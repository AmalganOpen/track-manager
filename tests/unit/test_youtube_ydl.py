"""yt-dlp option construction and 403 fallback for YouTube downloads."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from track_manager.sources import youtube as youtube_mod


def _patch_config(monkeypatch, **kwargs) -> None:
    defaults = {
        "youtube_cookies_file": None,
        "youtube_cookies_from_browser": None,
        "youtube_player_clients": None,
        "youtube_po_token": None,
    }
    defaults.update(kwargs)
    monkeypatch.setattr(youtube_mod, "Config", lambda: SimpleNamespace(**defaults))


def test_auth_opts_player_clients_use_dict_not_cli_strings(monkeypatch) -> None:
    _patch_config(
        monkeypatch,
        youtube_player_clients=["mweb", "tv_embedded"],
        youtube_po_token="web.gvs+abc",
    )
    opts = youtube_mod._auth_opts()
    assert opts["extractor_args"] == {
        "youtube": {
            "player_client": ["mweb", "tv_embedded"],
            "po_token": ["web.gvs+abc"],
        }
    }
    youtube_args = opts["extractor_args"]["youtube"]
    assert not isinstance(youtube_args, list)
    assert not any(isinstance(v, str) and "=" in v for v in youtube_args.values())


def test_auth_opts_cookies_from_browser(monkeypatch) -> None:
    _patch_config(monkeypatch, youtube_cookies_from_browser="firefox")
    opts = youtube_mod._auth_opts()
    assert opts["cookiesfrombrowser"] == ("firefox",)
    assert "extractor_args" not in opts


def test_ydl_opts_can_omit_auth(monkeypatch, tmp_path) -> None:
    _patch_config(monkeypatch, youtube_cookies_from_browser="firefox")
    opts = youtube_mod._ydl_opts(tmp_path, include_auth=False)
    assert "cookiesfrombrowser" not in opts
    assert "extractor_args" not in opts
    assert opts["logger"] is youtube_mod._YDL_LOGGER


def test_is_retryable_youtube_error_matches_403() -> None:
    err = RuntimeError(
        "ERROR: unable to download video data: HTTP Error 403: Forbidden"
    )
    assert youtube_mod._is_retryable_youtube_error(err)
    assert not youtube_mod._is_retryable_youtube_error(
        RuntimeError("video unavailable")
    )


def test_extract_info_retries_without_auth_on_403(monkeypatch, tmp_path) -> None:
    _patch_config(monkeypatch, youtube_cookies_from_browser="firefox")

    class FirstYDL:
        def extract_info(self, url, download=True):
            raise RuntimeError(
                "unable to download video data: HTTP Error 403: Forbidden"
            )

    created = []

    class RetryYDL:
        def __init__(self, opts):
            created.append(opts)
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            assert "cookiesfrombrowser" not in self.opts
            return {"id": "ok"}

    monkeypatch.setattr(youtube_mod.yt_dlp, "YoutubeDL", RetryYDL)
    info = youtube_mod._extract_info_or_fallback(
        FirstYDL(), "https://www.youtube.com/watch?v=x", tmp_path, download=True
    )
    assert info["id"] == "ok"
    assert len(created) == 1


def test_stale_cookie_warning_printed_once(capsys) -> None:
    youtube_mod._YtdlpLogger.reset()
    logger = youtube_mod._YtdlpLogger()
    stale = (
        "[youtube] The provided YouTube account cookies are no longer valid. "
        "They have likely been rotated in the browser as a security measure."
    )
    logger.warning(stale)
    logger.warning(stale)
    logger.warning("[youtube] Some other warning")
    logger.warning("[youtube] Some other warning")
    err = capsys.readouterr().err
    assert err.count("YouTube cookies are stale") == 1
    assert err.count("Some other warning") == 1


def test_extract_info_does_not_retry_without_auth_configured(
    monkeypatch, tmp_path
) -> None:
    _patch_config(monkeypatch)
    ydl = MagicMock()
    ydl.extract_info.side_effect = RuntimeError("HTTP Error 403: Forbidden")
    monkeypatch.setattr(youtube_mod.yt_dlp, "YoutubeDL", MagicMock())

    try:
        youtube_mod._extract_info_or_fallback(
            ydl, "https://www.youtube.com/watch?v=x", tmp_path, download=True
        )
    except RuntimeError as exc:
        assert "403" in str(exc)
    else:
        raise AssertionError("expected 403 to propagate when no auth is configured")
    youtube_mod.yt_dlp.YoutubeDL.assert_not_called()
