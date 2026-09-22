"""Unit tests for song.link / Odesli client."""

import json

import pytest
import requests

from track_manager import songlink as tm_songlink
from track_manager.config import Config


@pytest.fixture(autouse=True)
def _reset_songlink(monkeypatch):
    tm_songlink.reset_state()
    monkeypatch.setattr(tm_songlink, "songlink_rate_limit", lambda: None)
    monkeypatch.setattr(tm_songlink, "songlink_note_throttle", lambda _s: None)
    monkeypatch.delenv("SONGLINK_API_KEY", raising=False)
    monkeypatch.delenv("ODESLI_API_KEY", raising=False)
    yield
    tm_songlink.reset_state()


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        if text is not None:
            self.text = text
        elif payload is not None:
            self.text = json.dumps(payload)
        else:
            self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


class RecordingSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if not self._responses:
            raise AssertionError("unexpected extra GET")
        return self._responses.pop(0)


def test_fetch_links_unauthenticated_omits_key_param():
    payload = {
        "linksByPlatform": {
            "spotify": {"url": "https://open.spotify.com/track/abc"},
        }
    }
    session = RecordingSession([FakeResponse(200, payload)])
    data = tm_songlink.fetch_links(
        {"url": "https://soundcloud.com/a/b"},
        session=session,
    )
    assert data == payload
    assert "key" not in session.calls[0]["params"]
    assert tm_songlink.is_disabled() is False


def test_fetch_links_public_access_deprecated_skips_later_calls():
    session = RecordingSession(
        [FakeResponse(401, {"statusCode": 401, "code": "PUBLIC_API_ACCESS_DEPRECATED"})]
    )
    result = tm_songlink.fetch_links(
        {"url": "https://example.com/t"},
        session=session,
    )
    assert result is None
    assert tm_songlink.is_disabled() is True

    session2 = RecordingSession([FakeResponse(200, {"ok": True})])
    assert tm_songlink.fetch_links({"url": "x"}, session=session2) is None
    assert session2.calls == []


def test_fetch_links_sends_key_query_param():
    payload = {
        "linksByPlatform": {
            "spotify": {"url": "https://open.spotify.com/track/abc"},
            "tidal": {"url": "https://tidal.com/browse/track/123"},
        }
    }
    session = RecordingSession([FakeResponse(200, payload)])
    data = tm_songlink.fetch_links(
        {"url": "https://soundcloud.com/a/b"},
        api_key="odesli-secret",
        session=session,
    )
    assert data == payload
    assert session.calls[0]["params"]["key"] == "odesli-secret"
    assert session.calls[0]["params"]["url"] == "https://soundcloud.com/a/b"
    assert tm_songlink.is_disabled() is False


def test_fetch_links_invalid_key_disables():
    session = RecordingSession(
        [FakeResponse(401, {"statusCode": 401, "code": "INVALID_ACCESS_KEY"})]
    )
    result = tm_songlink.fetch_links(
        {"url": "https://example.com/t"},
        api_key="bad-key",
        session=session,
    )
    assert result is None
    assert tm_songlink.is_disabled() is True

    session2 = RecordingSession([FakeResponse(200, {"ok": True})])
    assert (
        tm_songlink.fetch_links({"url": "x"}, api_key="bad-key", session=session2)
        is None
    )
    assert session2.calls == []


def test_songlink_client_find_spotify_url():
    payload = {
        "linksByPlatform": {
            "spotify": {"url": "https://open.spotify.com/track/abc"},
        }
    }
    client = tm_songlink.SongLinkClient()
    client.session = RecordingSession([FakeResponse(200, payload)])
    assert client.find_spotify_url("https://soundcloud.com/a/b") == (
        "https://open.spotify.com/track/abc"
    )


def test_songlink_api_key_from_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "output_dir: '~/test/tracks'\nsonglink:\n  api_key: 'cfg-key'\n"
    )
    Config.reset()
    config = Config(config_file)
    assert config.songlink_api_key == "cfg-key"

    monkeypatch.setenv("SONGLINK_API_KEY", "env-key")
    Config.reset()
    config = Config(config_file)
    assert config.songlink_api_key == "env-key"


def test_songlink_api_key_empty_is_none(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("output_dir: '~/test/tracks'\nsonglink:\n  api_key: ''\n")
    Config.reset()
    config = Config(config_file)
    assert config.songlink_api_key is None
