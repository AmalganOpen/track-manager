"""Unit tests for Soulseek / sockseek integration (no live network)."""

import io
import json
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


def test_try_soulseek_removes_temp_dir_on_miss(tmp_path, monkeypatch):
    cfg = _write_config(
        tmp_path,
        "soulseek:\n  username: 'u'\n  password: 'p'\n"
        "downloads:\n  default_format: auto\n",
    )
    monkeypatch.setattr(
        "track_manager.soulseek.find_sockseek_binary", lambda _c=None: tmp_path
    )
    monkeypatch.setattr(
        "track_manager.soulseek.SoulseekClient.download_track",
        lambda *a, **k: None,
    )
    out = tmp_path / "out"
    dl = Downloader(cfg, output_dir=out, dumb=False)
    assert not dl._try_soulseek(SC_URL, "aiff", spotify_metadata=SC_GUESS)
    assert list(out.iterdir()) == []


def _smart_downloader(tmp_path, monkeypatch, *, tidal_track=None, catalog=None):
    """Downloader with network lookups stubbed; records what Soulseek receives."""
    cfg = _write_config(tmp_path, "downloads:\n  default_format: auto\n")
    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    received: dict = {}

    def fake_soulseek(url, fmt, spotify_metadata=None, playlist_url=None, isrc=None):
        received.update(metadata=spotify_metadata, isrc=isrc)
        return True

    monkeypatch.setattr(
        dl,
        "_resolve_isrc_via_tidal",
        lambda _u: ((tidal_track or {}).get("isrc"), tidal_track),
    )
    monkeypatch.setattr(dl, "_has_spotify_credentials", lambda: False)
    monkeypatch.setattr(dl, "_search_catalogs", lambda _g: catalog)
    monkeypatch.setattr(dl, "_try_soulseek", fake_soulseek)
    return dl, received


SC_URL = "https://soundcloud.com/lijanism/5ebstoner"
SC_GUESS = {"artists": ["lijan", "5EB"], "title": "STONER", "duration_seconds": 139.0}


def test_smart_download_uses_parsed_guess_without_catalogue_match(
    tmp_path, monkeypatch
):
    dl, received = _smart_downloader(tmp_path, monkeypatch)
    assert dl.try_smart_download(SC_URL, "aiff", guesses=[SC_GUESS])
    assert sl.extract_artist_title(received["metadata"]) == ("lijan, 5EB", "STONER")
    assert sl.extract_duration_seconds(received["metadata"]) == 139.0
    assert received["isrc"] is None


def test_smart_download_prefers_verified_catalogue_match(tmp_path, monkeypatch):
    match = {**SC_GUESS, "title": "Stoner", "isrc": "QZ123", "source": "deezer"}
    dl, received = _smart_downloader(tmp_path, monkeypatch, catalog=match)
    assert dl.try_smart_download(SC_URL, "aiff", guesses=[SC_GUESS])
    assert received["metadata"]["title"] == "Stoner"
    assert received["isrc"] == "QZ123"


def test_smart_download_keeps_tidal_match_metadata(tmp_path, monkeypatch):
    tidal = {
        "isrc": "US123",
        "title": "Song",
        "artists": [{"name": "A"}, {"name": "B"}],
        "duration": 201,
    }
    dl, received = _smart_downloader(tmp_path, monkeypatch, tidal_track=tidal)
    assert dl.try_smart_download(SC_URL, "aiff")
    assert sl.extract_artist_title(received["metadata"]) == ("A, B", "Song")
    assert sl.extract_duration_seconds(received["metadata"]) == 201.0
    assert received["isrc"] == "US123"


def _hit(name: str, **kwargs) -> sl.SoulseekHit:
    defaults = dict(
        username="peer",
        filename=rf"Music\{name}",
        size=4_000_000,
        length=200,
        bitrate=900,
        has_free_slot=True,
        upload_speed=1.5,
    )
    defaults.update(kwargs)
    return sl.SoulseekHit(**defaults)


def test_parse_seek_query_artist_title():
    parsed = sl.parse_seek_query("Burial - Archangel", duration_s=312.2)
    assert parsed.artist == "Burial"
    assert parsed.title == "Archangel"
    assert parsed.text == "artist=Burial, title=Archangel, length=312"
    assert parsed.duration_s == 312.2


def test_parse_seek_query_keyed_keeps_comma_in_title():
    parsed = sl.parse_seek_query("artist=Autechre, title=Fold4, Wrap5")
    assert parsed.artist == "Autechre"
    assert parsed.title == "Fold4, Wrap5"
    assert parsed.text == "artist=Autechre, title=Fold4, Wrap5"


def test_parse_seek_query_keyed_duration_replaces_length():
    parsed = sl.parse_seek_query(
        "artist=A, title=B, length=10",
        duration_s=20,
    )
    assert parsed.text == "artist=A, title=B, length=20"
    assert parsed.duration_s == 20


def test_parse_seek_query_free_text_has_no_identity():
    parsed = sl.parse_seek_query("burial archangel")
    assert parsed.text == "burial archangel"
    assert parsed.artist is None
    assert parsed.title is None


def test_parse_seek_query_empty():
    with pytest.raises(ValueError):
        sl.parse_seek_query("   ")


def test_artist_title_from_share_name():
    assert sl.artist_title_from_share_name("Burial - Archangel.flac") == (
        "Burial",
        "Archangel",
    )
    assert sl.artist_title_from_share_name("just-a-file.mp3") == (None, "just-a-file")


def test_slash_username_is_not_a_direct_link():
    hit = _hit(
        "10cust - Hush (Original Mix) [rawzik.com].aiff",
        username="https://www.rawzik.com",
        filename=r"RAWZIK - 2026\10cust - Hush (Original Mix) [rawzik.com].aiff",
        length=138,
    )
    assert hit.direct_link() is None
    query = sl.query_for_user_file(hit)
    assert query.startswith("artist=10cust, title=Hush")
    assert "length=138" in query
    assert "https:" not in query


def test_download_hit_targets_slash_username(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    out_dir = tmp_path / "dl"
    out_dir.mkdir()
    audio = out_dir / "track.aiff"
    audio.write_bytes(b"FORM")
    cfg = _write_config(
        tmp_path,
        "soulseek:\n"
        "  username: 'user'\n"
        "  password: 'pass'\n"
        f"  binary: '{fake_bin}'\n",
    )
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        proc = MagicMock()
        proc.stdout = io.StringIO("")
        proc.stderr = io.StringIO("")
        proc.returncode = 0
        proc.wait.return_value = 0
        return proc

    monkeypatch.setattr(sl.subprocess, "Popen", fake_popen)
    hit = _hit(
        "10cust - Hush.aiff",
        username="https://www.rawzik.com",
        filename=r"RAWZIK - 2026\10cust - Hush.aiff",
        length=138,
    )
    found = sl.SoulseekClient(cfg).download_hit(
        hit,
        query="artist=10cust, title=hush",
        output_dir=out_dir,
    )
    assert found == audio
    cmd = captured["cmd"]
    assert cmd[1] == "artist=10cust, title=hush"
    assert "--allowed-users" in cmd
    assert cmd[cmd.index("--allowed-users") + 1] == "https://www.rawzik.com"
    assert "--format" in cmd and "aiff" in cmd
    assert not cmd[1].startswith("slsk://")


def test_soulseek_hit_link_uses_forward_slashes():
    hit = _hit("Burial - Archangel.flac", username="some user")
    assert hit.link() == "slsk://some user/Music/Burial - Archangel.flac"
    assert hit.basename == "Burial - Archangel.flac"


def test_parse_search_results_ignores_log_preamble():
    payload = [
        {
            "User": {
                "Username": "peer",
                "UploadSpeed": 2.5,
                "HasFreeUploadSlot": False,
            },
            "File": {
                "Filename": r"Music\Song.flac",
                "Size": 1234,
                "Length": 180,
                "Bitrate": 1000,
                "SampleRate": 48000,
                "BitDepth": 24,
            },
        }
    ]
    stdout = "Logging in...\n" + json.dumps(payload)
    hits = sl.parse_search_results(stdout)
    assert hits is not None
    assert len(hits) == 1
    assert hits[0].username == "peer"
    assert hits[0].length == 180
    assert hits[0].bitrate == 1000
    assert hits[0].sample_rate == 48000
    assert hits[0].bit_depth == 24
    assert hits[0].has_free_slot is False
    assert hits[0].upload_speed == 2.5


def test_failure_detail_skips_completed_summary():
    text = """
[warn] [jobs] download attempt failed: track
    StaleDownloadException: Download attempt became stale after 30000ms
    Error: Download attempt became stale after 30000ms without peer transfer activity
[cli] Completed: 0 succeeded, 1 failed.
"""
    detail = sl._failure_detail(text)
    assert "without peer transfer activity" in detail
    assert "Completed:" not in detail


def test_parse_search_results_empty_array_and_garbage():
    assert sl.parse_search_results("[]") == []
    assert sl.parse_search_results("not json") is None


def test_rank_hits_keeps_off_duration_results():
    known = _hit("full.flac", length=200)
    off = _hit("preview.mp3", length=30)
    unknown = _hit("mystery.flac", length=None)
    ranked = sl.rank_hits([off, known, unknown], 200, 15)
    assert ranked[-1] is off
    assert ranked[:2] == [known, unknown]
    assert sl.rank_hits([off], None, 15) == [off]


def test_choose_hit_pick_and_cancel(monkeypatch):
    hits = [_hit("a.flac", username="a"), _hit("b.flac", username="b")]
    assert sl.choose_hit(hits, pick=2).username == "b"
    with pytest.raises(ValueError):
        sl.choose_hit(hits, pick=3)
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")
    assert sl.choose_hit(hits) is None


def test_search_invokes_sockseek_print(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    cfg = _write_config(
        tmp_path,
        "soulseek:\n"
        "  username: 'user'\n"
        "  password: 'pass'\n"
        f"  binary: '{fake_bin}'\n"
        "  length_tol_seconds: 3\n",
    )
    payload = json.dumps(
        [
            {
                "User": {
                    "Username": "peer",
                    "UploadSpeed": 1,
                    "HasFreeUploadSlot": True,
                },
                "File": {
                    "Filename": r"Music\Song.flac",
                    "Size": 10,
                    "Length": 200,
                },
            }
        ]
    )
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr(sl.subprocess, "run", fake_run)
    hits = sl.SoulseekClient(cfg).search(sl.parse_seek_query("Burial - Archangel"))
    assert hits is not None and hits[0].username == "peer"
    cmd = captured["cmd"]
    assert "--print" in cmd and "json-all" in cmd
    assert "--song" in cmd
    assert "aiff" in cmd[cmd.index("--format") + 1]
    assert "mp3" in cmd[cmd.index("--format") + 1]
    assert "--pref-format" in cmd
    assert "--length-tol=-1" in cmd
    assert "--pref-length-tol" not in cmd
    assert "--search-timeout" in cmd
    assert "artist=Burial, title=Archangel" in cmd


def test_search_returns_empty_list_on_no_matches(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
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
        lambda *a, **k: MagicMock(returncode=1, stdout="[]", stderr="not found"),
    )
    assert sl.SoulseekClient(cfg).search(sl.parse_seek_query("nope")) == []


def test_download_link_passes_slsk_uri(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    out_dir = tmp_path / "dl"
    out_dir.mkdir()
    flac = out_dir / "track.flac"
    flac.write_bytes(b"fLaC")
    cfg = _write_config(
        tmp_path,
        "soulseek:\n"
        "  username: 'user'\n"
        "  password: 'pass'\n"
        f"  binary: '{fake_bin}'\n",
    )
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        proc = MagicMock()
        proc.stdout = io.StringIO("")
        proc.stderr = io.StringIO("")
        proc.returncode = 0
        proc.wait.return_value = 0
        return proc

    monkeypatch.setattr(sl.subprocess, "Popen", fake_popen)
    link = "slsk://peer/Music/Song.flac"
    found = sl.SoulseekClient(cfg).download_link(link, output_dir=out_dir)
    assert found == flac
    cmd = captured["cmd"]
    assert link in cmd
    assert "--song" not in cmd
    assert "--no-skip-existing" in cmd
    assert f"--max-stale-time={sl.SEEK_STALE_TIMEOUT_MS}" in cmd
    assert "--no-progress" not in cmd


def test_seek_downloads_top_hit(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, "downloads:\n  default_format: auto\n")
    hit = _hit("Burial - Archangel.flac", username="peer", length=312)
    seen: dict = {}

    class _Client:
        def __init__(self, _config=None):
            pass

        def search(self, parsed):
            seen["query"] = parsed
            return [hit]

        def download_hit(self, hit, output_dir=None, query=None):
            seen["link"] = hit.link()
            path = Path(output_dir) / "track.flac"
            path.write_bytes(b"fLaC")
            return path

    def fake_import(self, downloaded, **kwargs):
        seen["import"] = kwargs
        return True

    monkeypatch.setattr("track_manager.soulseek.SoulseekClient", _Client)
    monkeypatch.setattr(Downloader, "import_soulseek", fake_import)

    out = tmp_path / "out"
    dl = Downloader(cfg, output_dir=out, dumb=False)
    assert dl.seek("Burial - Archangel", "aiff", auto=True) is True
    assert seen["query"].artist == "Burial"
    assert seen["link"].startswith("slsk://peer/")
    assert seen["import"]["artist"] == "Burial"
    assert seen["import"]["title"] == "Archangel"
    assert seen["import"]["target_format"] == "aiff"
    assert seen["import"]["duration_seconds"] == 312
    assert list(out.iterdir()) == []


def test_seek_filters_duration_and_cancels(tmp_path, monkeypatch, capsys):
    cfg = _write_config(
        tmp_path,
        "soulseek:\n  length_tol_seconds: 3\n" "downloads:\n  default_format: auto\n",
    )
    short = _hit("preview.flac", length=30, username="short")
    full = _hit("full.flac", length=200, username="full")
    other = _hit("other.flac", length=201, username="other")

    class _Client:
        def __init__(self, _config=None):
            pass

        def search(self, parsed):
            return [short, full, other]

        def download_link(self, link, output_dir=None):
            raise AssertionError("should not download")

    monkeypatch.setattr("track_manager.soulseek.SoulseekClient", _Client)
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")
    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    assert dl.seek("Artist - Title", "aiff", duration_s=200) is False
    output = capsys.readouterr().out
    assert "Cancelled" in output
    assert "preview.flac" in output


def test_search_length_is_not_required(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    cfg = _write_config(
        tmp_path,
        "soulseek:\n"
        "  username: 'user'\n"
        "  password: 'pass'\n"
        f"  binary: '{fake_bin}'\n"
        "  length_tol_seconds: 3\n",
    )
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(sl.subprocess, "run", fake_run)
    parsed = sl.parse_seek_query("Burial - Archangel", duration_s=312)
    assert sl.SoulseekClient(cfg).search(parsed) == []
    cmd = captured["cmd"]
    assert "length=312" in cmd[1]
    assert "--length-tol=-1" in cmd
    assert cmd[cmd.index("--pref-length-tol") + 1] == str(
        sl.SEEK_PREF_LENGTH_TOL_SECONDS
    )


def test_seek_keeps_off_duration_hit(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, "downloads:\n  default_format: auto\n")
    seen: dict = {}

    class _Client:
        def __init__(self, _config=None):
            pass

        def search(self, parsed):
            return [_hit("preview.flac", length=30)]

        def download_hit(self, hit, output_dir=None, query=None):
            seen["link"] = hit.link()
            path = Path(output_dir) / "preview.flac"
            path.write_bytes(b"fLaC")
            return path

    monkeypatch.setattr("track_manager.soulseek.SoulseekClient", _Client)
    monkeypatch.setattr(Downloader, "import_soulseek", lambda *a, **k: True)
    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    assert dl.seek("Artist - Title", "aiff", duration_s=200, auto=True) is True
    assert seen["link"].startswith("slsk://")


def test_seek_command_yes(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from track_manager.cli import cli

    cfg = _write_config(tmp_path, "downloads:\n  default_format: auto\n")
    monkeypatch.setattr("track_manager.cli.Config", lambda: cfg)

    def fake_seek(self, query, format, **kwargs):
        assert query == "Burial - Archangel"
        assert format == "mp3"
        assert kwargs["auto"] is True
        assert kwargs["duration_s"] == 100
        return True

    monkeypatch.setattr(Downloader, "seek", fake_seek)
    result = CliRunner().invoke(
        cli,
        [
            "seek",
            "Burial - Archangel",
            "--yes",
            "--format",
            "mp3",
            "--duration",
            "100",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output


def test_seek_command_rejects_yes_and_pick():
    from click.testing import CliRunner

    from track_manager.cli import cli

    result = CliRunner().invoke(cli, ["seek", "Artist - Title", "--yes", "--pick", "1"])
    assert result.exit_code != 0
    assert "not both" in result.output


def test_is_handshake_failure_is_the_peer_wait():
    assert sl.is_handshake_failure(
        "TimeoutException: The wait timed out after 15000 milliseconds"
    )
    assert sl.is_handshake_failure("User peer appears to be offline")
    assert not sl.is_handshake_failure(
        "StaleDownloadException: The download went stale"
    )
    assert not sl.is_handshake_failure("Soulseek download timed out after 600s")


def test_choose_hit_prompts_when_asked(monkeypatch):
    hits = [_hit("only.flac", username="only")]
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    assert sl.choose_hit(hits, prompt=True).username == "only"
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")
    assert sl.choose_hit(hits, prompt=True) is None


def test_download_records_handshake_failure(monkeypatch, tmp_path):
    fake_bin = tmp_path / "sockseek"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    cfg = _write_config(
        tmp_path,
        "soulseek:\n"
        "  username: 'user'\n"
        "  password: 'pass'\n"
        f"  binary: '{fake_bin}'\n",
    )
    note = "TimeoutException: The wait timed out after 15000 milliseconds\n"

    def fake_popen(cmd, **kwargs):
        proc = MagicMock()
        proc.stdout = io.StringIO("")
        proc.stderr = io.StringIO(note)
        proc.returncode = 1
        proc.wait.return_value = 1
        return proc

    monkeypatch.setattr(sl.subprocess, "Popen", fake_popen)
    client = sl.SoulseekClient(cfg)
    found = client.download_link(
        "slsk://peer/Music/Song.flac",
        output_dir=tmp_path / "empty",
    )
    assert found is None
    assert sl.is_handshake_failure(client.last_failure)


def test_seek_lists_other_peers_after_handshake(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, "downloads:\n  default_format: auto\n")
    slow = _hit("slow.aiff", username="slow")
    same = _hit("also-slow.aiff", username="slow")
    fast = _hit("fast.flac", username="fast")
    calls: list[str] = []

    class _Client:
        def __init__(self, _config=None):
            self.last_failure = ""

        def search(self, parsed):
            return [slow, same, fast]

        def download_hit(self, hit, output_dir=None, query=None):
            calls.append(hit.username)
            if hit.username == "slow":
                self.last_failure = (
                    "TimeoutException: The wait timed out after 15000 milliseconds"
                )
                return None
            path = Path(output_dir) / "fast.flac"
            path.write_bytes(b"fLaC")
            return path

    monkeypatch.setattr("track_manager.soulseek.SoulseekClient", _Client)
    monkeypatch.setattr(Downloader, "import_soulseek", lambda *a, **k: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    assert dl.seek("Artist - Title", "aiff", auto=True) is True
    assert calls == ["slow", "fast"]
    captured = capsys.readouterr()
    assert "Handshake with slow failed" in captured.err
    assert "fast.flac" in captured.out
    assert "also-slow.aiff" not in captured.out


def test_seek_handshake_with_one_result_stops(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, "downloads:\n  default_format: auto\n")

    class _Client:
        def __init__(self, _config=None):
            self.last_failure = ""

        def search(self, parsed):
            return [_hit("only.aiff", username="only")]

        def download_hit(self, hit, output_dir=None, query=None):
            self.last_failure = "User only appears to be offline"
            return None

    monkeypatch.setattr("track_manager.soulseek.SoulseekClient", _Client)

    def fail_input(_prompt):
        raise AssertionError("should not prompt")

    monkeypatch.setattr("builtins.input", fail_input)
    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    assert dl.seek("Artist - Title", "aiff", auto=True) is False
    output = capsys.readouterr().out
    assert "No other Soulseek results" in output


def test_seek_stale_download_does_not_relist(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, "downloads:\n  default_format: auto\n")

    class _Client:
        def __init__(self, _config=None):
            self.last_failure = ""

        def search(self, parsed):
            return [
                _hit("a.flac", username="a"),
                _hit("b.flac", username="b"),
            ]

        def download_hit(self, hit, output_dir=None, query=None):
            self.last_failure = "StaleDownloadException: no bytes received"
            return None

    monkeypatch.setattr("track_manager.soulseek.SoulseekClient", _Client)

    def fail_input(_prompt):
        raise AssertionError("should not prompt")

    monkeypatch.setattr("builtins.input", fail_input)
    dl = Downloader(cfg, output_dir=tmp_path / "out", dumb=False)
    assert dl.seek("Artist - Title", "aiff", auto=True) is False
    captured = capsys.readouterr()
    assert "Handshake" not in captured.out
    assert "Handshake" not in captured.err
    assert "No other Soulseek results" not in captured.out


def test_is_preview_audio_shared_guard_still_via_audio_guards(monkeypatch):
    monkeypatch.setattr(
        "track_manager.audio.probe_audio",
        lambda _path: {"duration_seconds": 30.01, "codec": "mp3"},
    )
    is_preview, actual = is_preview_audio(Path("x.mp3"), expected_duration=246.0)
    assert is_preview is True
    assert actual == 30.01
