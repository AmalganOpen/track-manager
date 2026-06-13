from pathlib import Path
from types import SimpleNamespace

from track_manager import upgrade as tm_upgrade


def test_upgrade_track_accepts_aiff_download(monkeypatch, tmp_path: Path) -> None:
    original = tmp_path / "Hether - Nebulous Tango.aiff"
    original.write_bytes(b"old-audio")

    monkeypatch.setattr(
        tm_upgrade,
        "read_original_provenance",
        lambda _p: {
            "track_url": "https://open.spotify.com/track/3VssoiwImUsnSzWSfjoQTW"
        },
    )
    monkeypatch.setattr(
        tm_upgrade,
        "_source_quality",
        lambda _p: {"bitrate_kbps": 120, "track_url": "x", "format": "aac"},
    )
    monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda _p: 0)
    monkeypatch.setattr(tm_upgrade, "_write_upgrade_attempts", lambda _p, _n: None)
    monkeypatch.setattr(tm_upgrade.tm_blob, "read_blob", lambda _p: None)
    monkeypatch.setattr(
        tm_upgrade.tm_audio,
        "probe_audio",
        lambda _p: {"bitrate_kbps": 658, "codec": "flac"},
    )
    monkeypatch.setattr(
        tm_upgrade.tm_audio,
        "encode_to_aiff",
        lambda src, dst: dst.write_bytes(src.read_bytes()),
    )
    monkeypatch.setattr(
        tm_upgrade,
        "_refresh_aiff_metadata",
        lambda *args, **kwargs: None,
    )

    class FakeDownloader:
        def __init__(self) -> None:
            self.output_dir = tmp_path

        def download(
            self, _url: str, format: str = "auto", show_header: bool = False
        ) -> None:
            assert format == "auto"
            assert show_header is False
            (self.output_dir / "Hether - Nebulous Tango.aiff").write_bytes(b"new-audio")

    success, message = tm_upgrade.upgrade_track(
        original,
        "https://open.spotify.com/track/3VssoiwImUsnSzWSfjoQTW",
        config=SimpleNamespace(),
        downloader=FakeDownloader(),
    )

    assert success is True
    assert "Upgraded in-place" in message


def test_upgrade_track_skips_reencode_when_download_already_aiff(
    monkeypatch, tmp_path: Path
) -> None:
    original = tmp_path / "Gucci Mane - Lemonade.aiff"
    original.write_bytes(b"old-audio")

    monkeypatch.setattr(
        tm_upgrade,
        "read_original_provenance",
        lambda _p: {
            "track_url": "https://open.spotify.com/track/6rUcS9i07F6okIe8wujs5J"
        },
    )
    monkeypatch.setattr(
        tm_upgrade,
        "_source_quality",
        lambda _p: {"bitrate_kbps": 128, "track_url": "x", "format": "aac"},
    )
    monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda _p: 0)
    monkeypatch.setattr(tm_upgrade, "_write_upgrade_attempts", lambda _p, _n: None)
    monkeypatch.setattr(tm_upgrade.tm_blob, "read_blob", lambda _p: None)
    monkeypatch.setattr(
        tm_upgrade.tm_audio,
        "probe_audio",
        lambda _p: {"bitrate_kbps": 937, "codec": "flac"},
    )
    monkeypatch.setattr(
        tm_upgrade.tm_audio,
        "encode_to_aiff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("encode_to_aiff should not be called for AIFF input")
        ),
    )
    monkeypatch.setattr(
        tm_upgrade,
        "_refresh_aiff_metadata",
        lambda *args, **kwargs: None,
    )

    class FakeDownloader:
        def __init__(self) -> None:
            self.output_dir = tmp_path

        def download(
            self, _url: str, format: str = "auto", show_header: bool = False
        ) -> None:
            assert format == "auto"
            assert show_header is False
            (self.output_dir / "Gucci Mane - Lemonade.aiff").write_bytes(b"new-audio")

    success, message = tm_upgrade.upgrade_track(
        original,
        "https://open.spotify.com/track/6rUcS9i07F6okIe8wujs5J",
        config=SimpleNamespace(),
        downloader=FakeDownloader(),
    )

    assert success is True
    assert "Upgraded in-place" in message


def test_upgrade_track_uses_download_provenance_for_quality_check(
    monkeypatch, tmp_path: Path
) -> None:
    original = tmp_path / "Lijan ism - STONER.aiff"
    original.write_bytes(b"old-audio")

    monkeypatch.setattr(
        tm_upgrade,
        "read_original_provenance",
        lambda _p: {"track_url": "https://soundcloud.com/lijanism/5ebstoner"},
    )
    monkeypatch.setattr(
        tm_upgrade,
        "_source_quality",
        lambda _p: {"bitrate_kbps": 96, "track_url": "x", "format": "aac"},
    )
    monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda _p: 0)
    monkeypatch.setattr(tm_upgrade, "_write_upgrade_attempts", lambda _p, _n: None)

    def fake_read_blob(path: Path):
        if path.name == "lijan， 5EB - STONER.aiff":
            return {
                "provenance": {
                    "original_bitrate": 96,
                    "original_format": "mp4a.40.2",
                }
            }
        return None

    monkeypatch.setattr(tm_upgrade.tm_blob, "read_blob", fake_read_blob)
    monkeypatch.setattr(
        tm_upgrade.tm_audio,
        "probe_audio",
        lambda _p: {"bitrate_kbps": 1411, "codec": "pcm_s16be"},
    )
    monkeypatch.setattr(
        tm_upgrade,
        "_refresh_aiff_metadata",
        lambda *args, **kwargs: None,
    )

    class FakeDownloader:
        def __init__(self) -> None:
            self.output_dir = tmp_path

        def download(
            self, _url: str, format: str = "auto", show_header: bool = False
        ) -> None:
            assert format == "auto"
            assert show_header is False
            (self.output_dir / "lijan， 5EB - STONER.aiff").write_bytes(b"new-audio")

    success, message = tm_upgrade.upgrade_track(
        original,
        "https://soundcloud.com/lijanism/5ebstoner",
        config=SimpleNamespace(),
        downloader=FakeDownloader(),
    )

    assert success is False
    assert "is not better than source" in message


def test_upgrade_track_reports_source_download_failure(
    monkeypatch, tmp_path: Path
) -> None:
    original = tmp_path / "rei harakami - put off and other.aiff"
    original.write_bytes(b"old-audio")

    monkeypatch.setattr(
        tm_upgrade,
        "read_original_provenance",
        lambda _p: {
            "track_url": "https://soundcloud.com/reiharakami/put-off-and-other-matsuo-ohno"
        },
    )
    monkeypatch.setattr(
        tm_upgrade,
        "_source_quality",
        lambda _p: {"bitrate_kbps": 128, "track_url": "x", "format": "aac"},
    )
    monkeypatch.setattr(tm_upgrade, "_read_upgrade_attempts", lambda _p: 0)
    monkeypatch.setattr(tm_upgrade, "_write_upgrade_attempts", lambda _p, _n: None)
    monkeypatch.setattr(tm_upgrade.tm_blob, "read_blob", lambda _p: None)

    class FakeDownloader:
        def __init__(self) -> None:
            self.output_dir = tmp_path

        def download(
            self, _url: str, format: str = "auto", show_header: bool = False
        ) -> bool:
            assert format == "auto"
            assert show_header is False
            return False

    success, message = tm_upgrade.upgrade_track(
        original,
        "https://soundcloud.com/reiharakami/put-off-and-other-matsuo-ohno",
        config=SimpleNamespace(),
        downloader=FakeDownloader(),
    )

    assert success is False
    assert message == "Source download failed (no file saved)"
