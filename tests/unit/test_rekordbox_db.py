"""Unit tests for the Rekordbox master.db editor.

The FileType tests guard a bug that silently broke playback: every
migrated AIFF was recorded as FLAC, so decks handed the file to the wrong
decoder and refused to load it.
"""

from pathlib import Path

import pytest

from track_manager import rekordbox_db as tm_rb


def _track(
    path: str,
    file_type: int,
    *,
    content_id: int = 1,
    file_size: int | None = None,
) -> tm_rb.TrackInfo:
    """A TrackInfo whose recorded size matches the file unless overridden."""
    p = Path(path)
    if file_size is None:
        file_size = p.stat().st_size if p.is_file() else 0
    return tm_rb.TrackInfo(
        content_id=content_id,
        folder_path=p,
        file_name=p.name,
        title=p.stem,
        artist=None,
        file_size=file_size,
        bitrate_kbps=1411,
        sample_rate=44100,
        file_type=file_type,
        inside_library=True,
    )


def _write(path: Path, header: bytes) -> Path:
    path.write_bytes(header + b"\x00" * 64)
    return path


def _aiff(path: Path) -> Path:
    return _write(path, b"FORM\x00\x00\x10\x00AIFF")


# ---------------------------------------------------------------------------
# FileType enum
# ---------------------------------------------------------------------------


def test_filetype_map_matches_pyrekordbox():
    """Our map must track pyrekordbox's enum; drift corrupts collections."""
    FileType = pytest.importorskip("pyrekordbox.db6.tables").FileType

    assert tm_rb.FILETYPE_BY_EXT[".mp3"] == FileType.MP3
    assert tm_rb.FILETYPE_BY_EXT[".m4a"] == FileType.M4A
    assert tm_rb.FILETYPE_BY_EXT[".flac"] == FileType.FLAC
    assert tm_rb.FILETYPE_BY_EXT[".wav"] == FileType.WAV
    assert tm_rb.FILETYPE_BY_EXT[".aiff"] == FileType.AIFF
    assert tm_rb.FILETYPE_BY_EXT[".aif"] == FileType.AIFF


def test_aiff_is_not_flac():
    """The exact regression: AIFF must never be recorded as FLAC (5)."""
    assert tm_rb.FILETYPE_BY_EXT[".aiff"] != tm_rb.FILETYPE_BY_EXT[".flac"]
    assert tm_rb.FILETYPE_BY_EXT[".aiff"] == 12


def test_filetype_codes_are_unique_per_container():
    codes = {ext: code for ext, code in tm_rb.FILETYPE_BY_EXT.items() if ext != ".aif"}
    assert len(set(codes.values())) == len(codes)


# ---------------------------------------------------------------------------
# Container sniffing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header, expected",
    [
        (b"FORM\x00\x00\x10\x00AIFF", ".aiff"),
        (b"FORM\x00\x00\x10\x00AIFC", ".aiff"),
        (b"RIFF\x00\x00\x10\x00WAVE", ".wav"),
        (b"fLaC\x00\x00\x00\x22\x00\x00\x00", ".flac"),
        (b"\x00\x00\x00\x20ftypM4A ", ".m4a"),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00", ".mp3"),
        (b"\xff\xfb\x90\x00\x00\x00\x00\x00\x00\x00\x00\x00", ".mp3"),
        (b"NOPE\x00\x00\x00\x00NOPE", None),
    ],
)
def test_sniff_container(tmp_path, header, expected):
    assert tm_rb.sniff_container(_write(tmp_path / "t.bin", header)) == expected


def test_sniff_container_handles_missing_and_truncated(tmp_path):
    assert tm_rb.sniff_container(tmp_path / "nope.aiff") is None
    assert tm_rb.sniff_container(_write(tmp_path / "tiny.aiff", b"FOR")) is None


# ---------------------------------------------------------------------------
# Repair planning
# ---------------------------------------------------------------------------


def test_plan_repair_flags_aiff_recorded_as_flac(tmp_path, monkeypatch):
    aiff = _aiff(tmp_path / "track.aiff")
    monkeypatch.setattr(
        tm_rb, "list_tracks", lambda _d=None: [_track(str(aiff), tm_rb.FILETYPE_FLAC)]
    )

    fixes, missing, unverified = tm_rb.plan_content_repair()

    assert not missing and not unverified
    assert [(f.current_type, f.correct_type) for f in fixes] == [
        (tm_rb.FILETYPE_FLAC, tm_rb.FILETYPE_AIFF)
    ]
    assert fixes[0].type_changed and not fixes[0].size_changed


def test_plan_repair_flags_stale_file_size(tmp_path, monkeypatch):
    """A file shrunk in place (e.g. by scale-covers) must be re-measured."""
    aiff = _aiff(tmp_path / "track.aiff")
    monkeypatch.setattr(
        tm_rb,
        "list_tracks",
        lambda _d=None: [
            _track(str(aiff), tm_rb.FILETYPE_AIFF, file_size=999_999),
        ],
    )

    fixes, _, _ = tm_rb.plan_content_repair()

    assert len(fixes) == 1
    assert fixes[0].size_changed and not fixes[0].type_changed
    assert fixes[0].correct_size == aiff.stat().st_size


def test_plan_repair_reports_both_problems_on_one_row(tmp_path, monkeypatch):
    aiff = _aiff(tmp_path / "track.aiff")
    monkeypatch.setattr(
        tm_rb,
        "list_tracks",
        lambda _d=None: [_track(str(aiff), tm_rb.FILETYPE_FLAC, file_size=1)],
    )

    fixes, _, _ = tm_rb.plan_content_repair()

    assert len(fixes) == 1
    assert fixes[0].type_changed and fixes[0].size_changed


def test_plan_repair_ignores_rows_already_in_sync(tmp_path, monkeypatch):
    aiff = _aiff(tmp_path / "track.aiff")
    monkeypatch.setattr(
        tm_rb, "list_tracks", lambda _d=None: [_track(str(aiff), tm_rb.FILETYPE_AIFF)]
    )

    assert tm_rb.plan_content_repair() == ([], [], [])


def test_plan_repair_skips_files_missing_from_disk(tmp_path, monkeypatch):
    ghost = tmp_path / "gone.aiff"
    monkeypatch.setattr(
        tm_rb, "list_tracks", lambda _d=None: [_track(str(ghost), tm_rb.FILETYPE_FLAC)]
    )

    fixes, missing, unverified = tm_rb.plan_content_repair()

    assert not fixes and not unverified
    assert [t.folder_path for t in missing] == [ghost]


def test_plan_repair_keeps_flac_label_when_contents_contradict_extension(
    tmp_path, monkeypatch
):
    """A real FLAC named .aiff keeps its FLAC label rather than gaining a lie."""
    liar = _write(tmp_path / "liar.aiff", b"fLaC\x00\x00\x00\x22\x00\x00\x00")
    monkeypatch.setattr(
        tm_rb, "list_tracks", lambda _d=None: [_track(str(liar), tm_rb.FILETYPE_FLAC)]
    )

    fixes, missing, unverified = tm_rb.plan_content_repair()

    assert not fixes and not missing
    assert [t.folder_path for t in unverified] == [liar]


def test_unverified_row_still_gets_its_size_corrected(tmp_path, monkeypatch):
    """Refusing to relabel a suspicious file shouldn't block the size fix."""
    liar = _write(tmp_path / "liar.aiff", b"fLaC\x00\x00\x00\x22\x00\x00\x00")
    monkeypatch.setattr(
        tm_rb,
        "list_tracks",
        lambda _d=None: [_track(str(liar), tm_rb.FILETYPE_FLAC, file_size=42)],
    )

    fixes, _, unverified = tm_rb.plan_content_repair()

    assert len(unverified) == 1
    assert len(fixes) == 1
    assert fixes[0].correct_type == tm_rb.FILETYPE_FLAC  # left alone
    assert fixes[0].correct_size == liar.stat().st_size


def test_plan_repair_ignores_unknown_extensions(tmp_path, monkeypatch):
    other = _write(tmp_path / "notes.txt", b"hello world!")
    monkeypatch.setattr(
        tm_rb, "list_tracks", lambda _d=None: [_track(str(other), 0, file_size=1)]
    )

    assert tm_rb.plan_content_repair() == ([], [], [])


# ---------------------------------------------------------------------------
# Process detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("/Applications/rekordbox 7/rekordbox.app/Contents/MacOS/rekordbox", True),
        ("/Applications/rekordbox 7/rekordboxAgent.app/…/rekordboxAgent", True),
        ("/usr/bin/rekordbox --flag rekordbox", True),
        # Anything that merely mentions rekordbox in an argument.
        ("rg -A4 Out-of-date|Rekordbox already", False),
        ("grep -i rekordbox", False),
        ("/bin/zsh -c pgrep -fil rekordbox", False),
        ("python3 -m track_manager rekordbox-resync", False),
        ("", False),
    ],
)
def test_only_real_rekordbox_binaries_count(cmd, expected):
    assert tm_rb._executable_is_rekordbox(cmd) is expected


def test_running_processes_ignores_command_line_mentions(monkeypatch):
    import subprocess

    class _Result:
        stdout = "111 grep -i rekordbox\n222 /Applications/rekordbox/rekordbox\n"

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _Result())

    procs = tm_rb.running_rekordbox_processes()

    assert [p.pid for p in procs] == [222]


# ---------------------------------------------------------------------------
# Repair safety
# ---------------------------------------------------------------------------


def test_repair_refuses_while_rekordbox_runs(monkeypatch):
    monkeypatch.setattr(
        tm_rb,
        "running_rekordbox_processes",
        lambda: [tm_rb.RekordboxProcess(pid=42, command="rekordbox")],
    )

    with pytest.raises(RuntimeError, match="Rekordbox is running"):
        tm_rb.repair_content_metadata()


def test_repair_dry_run_neither_backs_up_nor_commits(tmp_path, monkeypatch):
    aiff = _aiff(tmp_path / "track.aiff")
    monkeypatch.setattr(tm_rb, "running_rekordbox_processes", lambda: [])
    monkeypatch.setattr(
        tm_rb, "list_tracks", lambda _d=None: [_track(str(aiff), tm_rb.FILETYPE_FLAC)]
    )

    def _boom(*_a, **_k):
        raise AssertionError("dry run must not touch the database")

    monkeypatch.setattr(tm_rb, "_open_db", _boom)
    monkeypatch.setattr(tm_rb, "_backup_master_db", _boom)

    result = tm_rb.repair_content_metadata(dry_run=True)

    assert len(result.fixes) == 1
    assert result.committed is False
    assert result.backup_path is None
