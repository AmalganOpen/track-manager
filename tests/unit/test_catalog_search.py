"""Unit tests for verified Spotify/Deezer matching (no network)."""

from track_manager import catalog_search as cs

GUESS = {"artists": ["Daft Punk"], "title": "Harder, Better", "duration_seconds": 225.0}


def _candidate(**overrides):
    base = {
        "artists": ["Daft Punk"],
        "title": "Harder, Better",
        "duration_seconds": 226.0,
        "isrc": "GBDUW0000059",
        "source": "spotify",
    }
    return {**base, **overrides}


def test_is_match_accepts_same_recording():
    assert cs.is_match(_candidate(), GUESS)


def test_is_match_rejects_duration_mismatch():
    assert not cs.is_match(_candidate(duration_seconds=240.0), GUESS)
    assert not cs.is_match(_candidate(), {**GUESS, "duration_seconds": None})


def test_is_match_rejects_original_for_remix_upload():
    remix = {
        "artists": ["Addison Rae"],
        "title": "High Fashion (DJ-7 club remix)",
        "duration_seconds": 198.0,
    }
    original = _candidate(artists=["Addison Rae"], title="High Fashion")
    assert not cs.is_match(original, remix)


def test_is_match_accepts_remix_credited_to_original_artist():
    upload = {
        "artists": ["DJ-7"],
        "title": "High Fashion - Addison Rae (DJ-7 club remix)",
        "duration_seconds": 189.0,
    }
    listed = _candidate(
        artists=["Addison Rae", "DJ-7"],
        title="High Fashion - DJ-7 Club Remix",
        duration_seconds=190.0,
    )
    assert cs.is_match(listed, upload)


def test_is_match_rejects_different_artist():
    assert not cs.is_match(_candidate(artists=["Sunfly House Band"]), GUESS)


def test_find_match_prefers_spotify(monkeypatch):
    monkeypatch.setattr(cs, "search_spotify", lambda _sp, _q: [_candidate()])
    monkeypatch.setattr(
        cs, "search_deezer", lambda _q: (_ for _ in ()).throw(AssertionError)
    )
    assert cs.find_match([GUESS], spotify=object())["source"] == "spotify"


def test_find_match_falls_back_to_deezer_and_fetches_isrc(monkeypatch):
    monkeypatch.setattr(
        cs,
        "search_deezer",
        lambda _q: [
            _candidate(duration_seconds=300.0, deezer_id=1),
            _candidate(isrc=None, deezer_id=2, source="deezer"),
        ],
    )
    monkeypatch.setattr(cs, "deezer_isrc", lambda track_id: f"ISRC{track_id}")
    match = cs.find_match([GUESS])
    assert match["source"] == "deezer"
    assert match["isrc"] == "ISRC2"


def test_find_match_survives_network_errors(monkeypatch, capsys):
    def boom(*_a):
        raise ConnectionError("offline")

    monkeypatch.setattr(cs, "search_spotify", boom)
    monkeypatch.setattr(cs, "search_deezer", boom)
    assert cs.find_match([GUESS], spotify=object()) is None
    assert "offline" in capsys.readouterr().err
