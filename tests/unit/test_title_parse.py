"""Unit tests for SoundCloud title parsing (no network)."""

from track_manager import title_parse as tp


def test_normalize_text_folds_decorative_unicode():
    assert tp.normalize_text("𝘿𝙅-𝟳̶") == "DJ-7"
    assert tp.normalize_text("lijan， 5EB") == "lijan, 5EB"
    assert tp.normalize_text("🔥 Björk  ★") == "Björk"


def test_strip_promo():
    assert tp.strip_promo("Track [FREE DL]") == "Track"
    assert tp.strip_promo("PREMIERE: A - B") == "A - B"
    assert tp.strip_promo("A - B (Original Mix) OUT NOW") == "A - B (Original Mix)"
    assert tp.strip_promo("A - B (Free Download)") == "A - B"


def test_split_artists():
    assert tp.split_artists("A, B & C feat. D") == ["A", "B", "C", "D"]
    assert tp.split_artists("Fred x Skrillex") == ["Fred", "Skrillex"]
    assert tp.split_artists("Lil Nas X") == ["Lil Nas X"]


def test_version_markers():
    assert tp.version_markers("Song (DJ-7 club remix)") == {"remix"}
    assert tp.version_markers("Song - Radio Edit") == {"edit"}
    assert tp.version_markers("Song") == frozenset()


def test_guesses_use_publisher_artists():
    info = {
        "title": "STONER",
        "artists": ["lijan， 5EB"],
        "uploader": "Lijan ism",
        "duration": 139.039,
    }
    assert tp.soundcloud_guesses(info) == [
        {"artists": ["lijan", "5EB"], "title": "STONER", "duration_seconds": 139.039}
    ]


def test_guesses_split_artist_dash_title():
    info = {"title": "Daft Punk - Around the World [FREE DL]", "uploader": "reposts"}
    best = tp.soundcloud_guesses(info)[0]
    assert best["artists"] == ["Daft Punk"]
    assert best["title"] == "Around the World"


def test_guesses_prefer_side_matching_uploader():
    info = {"title": "Around the World - Daft Punk", "uploader": "Daft Punk"}
    best = tp.soundcloud_guesses(info)[0]
    assert best["artists"] == ["Daft Punk"]
    assert best["title"] == "Around the World"


def test_guesses_move_brackets_from_artist_to_title():
    info = {
        "title": "High Fashion - Addison Rae (𝘿𝙅-𝟳̶ club remix)",
        "artist": "DJ-7̶",
        "uploader": "𝘿𝘼𝙑𝙀 𝙅𝙊𝙃𝘼𝙉𝙉𝙀𝙎",
        "duration": 189.0,
    }
    guesses = tp.soundcloud_guesses(info)
    assert {"artists": ["Addison Rae"], "title": "High Fashion (DJ-7 club remix)"} in [
        {"artists": g["artists"], "title": g["title"]} for g in guesses
    ]


def test_guesses_empty_without_title():
    assert tp.soundcloud_guesses({"uploader": "x"}) == []
