"""Unit tests for Soulseek / sockseek integration (no live network)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from track_manager import soulseek as sl
from track_manager.audio_guards import is_preview_audio
from track_manager.config import Config
from track_manager.downloader import Downloader


@pytest.fixture(autouse=True)
def _reset_config():
    Config.reset()
    yield
    Config.reset()


def _write_config(tmp_path: Path, body: str) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    Config.reset()
    return Config(config_path=path)


def test_build_song_query_with_duration():
    q = sl.build_song_query("Radiohead", "Creep", duration_s=238.4)
    assert q == "artist=Radiohead, title=Creep, length=238"


def test_build_song_query_without_duration():
    q = sl.build_song_query("Autechre", "Fold4, Wrap5")
    assert q == "artist=Autechre, title=Fold4, Wrap5"
    assert "length=" not in q


def test_extract_artist_title_from_spotify_metadata():
    artist, title = sl.extract_artist_title({"artists": ["A", "B"], "title": "Song"})
    assert artist == "A, B"
    assert title == "Song"


def test_extract_artist_title_missing_skips():
    assert sl.extract_artist_title({"title": "Only"}) == (None, None)
    assert sl.extract_artist_title(None) == (None, None)


def test_extract_duration_seconds_ms_and_seconds():
    assert sl.extract_duration_seconds({"duration_seconds": 200.5}) == 200.5
    assert sl.extract_duration_seconds({"duration_ms": 201000}) == 201.0
    # Values > 1000 treated as milliseconds.
    assert sl.extract_duration_seconds({"duration": 180500}) == 180.5


def test_find_sockseek_binary_configured(tmp_path):
    fake = tmp_path / "sockseek"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    cfg = _write_config(
        tmp_path,
        f"soulseek:\n  username: ''\n  password: ''\n  binary: '{fake}'\n",
    )
    found = sl.find_sockseek_binary(cfg)
    assert found == fake


def test_find_sockseek_binary_path_fallback(monkeypatch, tmp_path):
    fake = tmp_path / "sockseek"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)

    def fake_which(name: str):
        if name == "sockseek":
            return str(fake)
        return None

    monkeypatch.setattr(sl.shutil, "which", fake_which)
    cfg = _write_config(tmp_path, "soulseek:\n  username: ''\n  password: ''\n")
    assert sl.find_sockseek_binary(cfg) == fake


def test_download_track_invokes_sockseek(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    out_dir = tmp_path / "dl"
    out_dir.mkdir()
    flac = out_dir / "track.flac"
    flac.write_bytes(b"fLaCfake")

    cfg = _write_config(
        tmp_path,
        "soulseek:\n"
        "  username: 'user'\n"
        "  password: 'pass'\n"
        f"  binary: '{fake_bin}'\n"
        "  timeout_seconds: 30\n"
        "  length_tol_seconds: 3\n",
    )

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sl.subprocess, "run", fake_run)

    client = sl.SoulseekClient(cfg)
    result = client.download_track(
        "Artist", "Title", duration_s=200.0, output_dir=out_dir
    )
    assert result == flac
    cmd = captured["cmd"]
    assert cmd[0] == str(fake_bin)
    assert "artist=Artist, title=Title, length=200" in cmd
    assert "--song" in cmd
    assert "--format" in cmd and "flac" in cmd
    assert "--user" in cmd and "user" in cmd
    assert "--pass" in cmd and "pass" in cmd
    assert "--no-config" in cmd


def test_download_track_rejects_nonzero_exit(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    out_dir = tmp_path / "dl"
    out_dir.mkdir()

    cfg = _write_config(
        tmp_path,
        "soulseek:\n"
        "  username: 'user'\n"
        "  password: 'pass'\n"
        f"  binary: '{fake_bin}'\n",
    )
    monkeypatch.setattr(
        sl.subprocess,
        "run",
        lambda *a, **k: MagicMock(returncode=1, stdout="", stderr="no peers"),
    )

    assert sl.SoulseekClient(cfg).download_track("A", "B", output_dir=out_dir) is None


def test_try_soulseek_skips_when_not_configured(tmp_path, capsys):
    cfg = _write_config(
        tmp_path,
        "soulseek:\n  username: ''\n  password: ''\n"
        "downloads:\n  default_format: auto\n",
    )
    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    ok = dl._try_soulseek(
        "https://open.spotify.com/track/x",
        "aiff",
        spotify_metadata={"artists": ["A"], "title": "T", "duration_seconds": 200},
    )
    assert ok is False
    err = capsys.readouterr().err
    assert "Skipping Soulseek (not configured)" in err


def test_try_soulseek_skips_without_artist_title(tmp_path, monkeypatch, capsys):
    cfg = _write_config(
        tmp_path,
        "soulseek:\n  username: 'u'\n  password: 'p'\n"
        "downloads:\n  default_format: auto\n",
    )
    fake = tmp_path / "sockseek"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(
        "track_manager.soulseek.find_sockseek_binary", lambda _c=None: fake
    )

    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    ok = dl._try_soulseek(
        "https://open.spotify.com/track/x",
        "aiff",
        spotify_metadata={"title": "Only title"},
    )
    assert ok is False
    assert "need artist + title" in capsys.readouterr().err


def test_try_soulseek_rejects_duration_mismatch(tmp_path, monkeypatch, capsys):
    cfg = _write_config(
        tmp_path,
        "soulseek:\n  username: 'u'\n  password: 'p'\n"
        "downloads:\n  default_format: auto\n",
    )
    fake = tmp_path / "sockseek"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(
        "track_manager.soulseek.find_sockseek_binary", lambda _c=None: fake
    )

    preview = tmp_path / "preview.flac"
    preview.write_bytes(b"fLaC")

    class _Client:
        def __init__(self, _config=None):
            pass

        def download_track(self, *a, **k):
            return preview

    monkeypatch.setattr("track_manager.soulseek.SoulseekClient", _Client)
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _p: {"duration_seconds": 30.0, "codec": "flac", "bitrate_kbps": None},
    )

    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    ok = dl._try_soulseek(
        "https://open.spotify.com/track/x",
        "aiff",
        spotify_metadata={
            "artists": ["A"],
            "title": "Long Song",
            "duration_seconds": 246.0,
        },
    )
    assert ok is False
    assert "duration check" in capsys.readouterr().err
    assert not preview.exists()  # cleaned up after rejection


def test_is_preview_audio_shared_guard_still_via_audio_guards(monkeypatch):
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 30.01, "codec": "mp3"},
    )
    is_preview, actual = is_preview_audio(Path("x.mp3"), expected_duration=246.0)
    assert is_preview is True
    assert actual == 30.01
