"""Tests for track-URL identity normalization."""

import pytest

from track_manager.duplicates import normalize_track_url


class TestNormalizeTrackUrl:
    """YouTube identity is ``v=``; tracking params must not become identity."""

    def test_empty(self) -> None:
        assert normalize_track_url("") == ""

    def test_youtube_watch_keeps_video_id(self) -> None:
        assert (
            normalize_track_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            == "https://youtube.com/watch?v=dQw4w9WgXcQ"
        )

    def test_different_youtube_videos_do_not_collide(self) -> None:
        a = normalize_track_url("https://www.youtube.com/watch?v=AAAAAAAAAAA")
        b = normalize_track_url("https://www.youtube.com/watch?v=BBBBBBBBBBB")
        assert a != b

    def test_youtube_playlist_and_timestamp_are_not_identity(self) -> None:
        assert normalize_track_url(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxxx&index=3&t=30s"
        ) == normalize_track_url("https://youtube.com/watch?v=dQw4w9WgXcQ")

    def test_youtu_be_canonicalizes_to_watch(self) -> None:
        assert normalize_track_url(
            "https://youtu.be/dQw4w9WgXcQ"
        ) == normalize_track_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_youtube_shorts_and_embed_canonicalizes_to_watch(self) -> None:
        watch = normalize_track_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert (
            normalize_track_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") == watch
        )
        assert normalize_track_url("https://www.youtube.com/embed/dQw4w9WgXcQ") == watch

    def test_spotify_tracking_param_is_stripped(self) -> None:
        assert normalize_track_url(
            "https://open.spotify.com/track/abc123?si=xyz"
        ) == normalize_track_url("https://open.spotify.com/track/abc123")

    def test_spotify_trailing_slash(self) -> None:
        assert normalize_track_url(
            "https://open.spotify.com/track/abc123/"
        ) == normalize_track_url("https://open.spotify.com/track/abc123")

    @pytest.mark.parametrize(
        "host",
        [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
        ],
    )
    def test_youtube_hosts(self, host: str) -> None:
        assert (
            normalize_track_url(f"https://{host}/watch?v=dQw4w9WgXcQ")
            == "https://youtube.com/watch?v=dQw4w9WgXcQ"
        )
