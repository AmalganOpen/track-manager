"""Unit tests for Qobuz preview/sample detection."""

from pathlib import Path

from track_manager import qobuz_public as qobuz


def test_is_preview_cdn_url_fmt5_on_lossless_request():
    url = (
        "https://streaming-qobuz-std.akamaized.net/file"
        "?uid=1&eid=2&fmt=5&profile=raw"
    )
    assert qobuz.is_preview_cdn_url(url, requested_quality=27) is True
    assert qobuz.is_preview_cdn_url(url, requested_quality=7) is True


def test_is_preview_cdn_url_fmt6_flac_ok():
    url = (
        "https://streaming-qobuz-std.akamaized.net/file"
        "?uid=1&eid=2&fmt=6&profile=raw"
    )
    assert qobuz.is_preview_cdn_url(url, requested_quality=27) is False


def test_is_preview_cdn_url_fmt5_ok_when_mp3_requested():
    # quality 5/6 are MP3 tiers — fmt=5 is expected, not a preview trap.
    url = (
        "https://streaming-qobuz-std.akamaized.net/file"
        "?uid=1&eid=2&fmt=5&profile=raw"
    )
    assert qobuz.is_preview_cdn_url(url, requested_quality=5) is False
    assert qobuz.is_preview_cdn_url(url, requested_quality=6) is False


def test_is_preview_audio_classic_30s_sample(monkeypatch):
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 30.01, "codec": "mp3"},
    )
    is_preview, actual = qobuz.is_preview_audio(Path("x.mp3"), expected_duration=246.0)
    assert is_preview is True
    assert actual == 30.01


def test_is_preview_audio_full_track_ok(monkeypatch):
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 245.8, "codec": "flac"},
    )
    is_preview, actual = qobuz.is_preview_audio(Path("x.flac"), expected_duration=246.0)
    assert is_preview is False
    assert actual == 245.8


def test_is_preview_audio_short_real_track_ok(monkeypatch):
    # A legitimately short track (~25s) must not be rejected as a preview.
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 24.5, "codec": "flac"},
    )
    is_preview, _ = qobuz.is_preview_audio(Path("x.flac"), expected_duration=25.0)
    assert is_preview is False


def test_is_preview_audio_half_length_truncation(monkeypatch):
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 60.0, "codec": "flac"},
    )
    is_preview, _ = qobuz.is_preview_audio(Path("x.flac"), expected_duration=200.0)
    assert is_preview is True


def test_is_preview_audio_no_expected_mp3_short_is_preview(monkeypatch):
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 30.0, "codec": "mp3"},
    )
    is_preview, _ = qobuz.is_preview_audio(Path("x.mp3"), expected_duration=None)
    assert is_preview is True


def test_is_preview_audio_no_expected_flac_short_not_preview(monkeypatch):
    # Without catalogue duration, a short FLAC could be a real short track.
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 28.0, "codec": "flac"},
    )
    is_preview, _ = qobuz.is_preview_audio(Path("x.flac"), expected_duration=None)
    assert is_preview is False


def test_download_by_isrc_rejects_preview_url(monkeypatch, tmp_path):
    client = qobuz.QobuzPublicClient(bypass_cache=True)
    monkeypatch.setattr(
        client,
        "search_by_isrc",
        lambda _isrc: {"id": 123, "title": "X", "duration": 200},
    )
    monkeypatch.setattr(
        client,
        "_get_download_url",
        lambda _tid, quality=27: (
            "https://streaming-qobuz-std.akamaized.net/file?fmt=5&profile=raw"
        ),
    )

    result = client.download_by_isrc("USWB10904741", tmp_path / "out.flac")
    assert result is None
    assert not (tmp_path / "out.flac").exists()


def test_download_by_isrc_rejects_preview_bytes(monkeypatch, tmp_path):
    client = qobuz.QobuzPublicClient(bypass_cache=True)
    monkeypatch.setattr(
        client,
        "search_by_isrc",
        lambda _isrc: {"id": 123, "title": "X", "duration": 200},
    )
    monkeypatch.setattr(
        client,
        "_get_download_url",
        lambda _tid, quality=27: (
            "https://streaming-qobuz-std.akamaized.net/file?fmt=6&profile=raw"
        ),
    )

    class _Resp:
        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield b"fLaCfake-preview-bytes"

    monkeypatch.setattr(
        client.session,
        "get",
        lambda *a, **k: _Resp(),
    )
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 30.0, "codec": "mp3"},
    )

    out = tmp_path / "out.flac"
    result = client.download_by_isrc("USWB10904741", out)
    assert result is None
    assert not out.exists()
