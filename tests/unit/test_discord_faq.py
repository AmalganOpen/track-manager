"""Unit tests for the Discord FAQ card sender."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "discord_faq.py"
_spec = importlib.util.spec_from_file_location("discord_faq", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["discord_faq"] = _mod
_spec.loader.exec_module(_mod)

parse_faq = _mod.parse_faq
build_messages = _mod.build_messages
section_components = _mod.section_components
text_length = _mod.text_length
component_count = _mod.component_count
message_payload = _mod.message_payload
require_bot = _mod.require_bot
FaqSection = _mod.FaqSection
MAX_TEXT_CHARS = _mod.MAX_TEXT_CHARS
MAX_COMPONENTS = _mod.MAX_COMPONENTS
CONTAINER = _mod.CONTAINER
IS_COMPONENTS_V2 = _mod.IS_COMPONENTS_V2


def test_parse_intro_and_sections() -> None:
    intro, sections = parse_faq(
        "# Title\n\nHello.\n\n## First question\n\nAnswer one.\n\n## Second\n\nAnswer two.\n"
    )
    assert intro == "# Title\n\nHello."
    assert sections == [
        FaqSection("First question", "Answer one."),
        FaqSection("Second", "Answer two."),
    ]


def test_parse_rejects_empty_file() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_faq("  \n")


def test_parse_rejects_section_without_body() -> None:
    with pytest.raises(ValueError, match="no body"):
        parse_faq("## Only a title\n")


def test_cards_show_the_question_and_answer() -> None:
    card = section_components(FaqSection("Spotify", "A single track works."))
    container = card[0]
    assert container["type"] == CONTAINER
    assert "spoiler" not in container
    assert [
        child["content"] for child in container["components"] if "content" in child
    ] == [
        "## Spotify",
        "A single track works.",
    ]


def test_splits_when_text_would_pass_4000() -> None:
    body = "x" * 2000
    sections = [FaqSection(title, body) for title in ("A", "B", "C")]
    messages = build_messages("", sections)
    assert len(messages) == 3
    for message in messages:
        assert text_length(message) <= MAX_TEXT_CHARS


def test_splits_on_component_cap() -> None:
    sections = [FaqSection(f"Q{i}", "a") for i in range(14)]
    messages = build_messages("", sections)
    assert component_count(messages[0]) == 40
    assert len(messages) == 2
    assert all(component_count(message) <= MAX_COMPONENTS for message in messages)


def test_intro_is_its_own_message_when_the_next_card_does_not_fit() -> None:
    intro = "i" * 3000
    messages = build_messages(intro, [FaqSection("A", "b" * 2000)])
    assert len(messages) == 2
    assert messages[0][0]["content"] == intro
    assert messages[1][0]["type"] == CONTAINER
    assert "spoiler" not in messages[1][0]


def test_payload_uses_components_v2_and_does_not_ping() -> None:
    payload = message_payload(section_components(FaqSection("Q", "A")))
    assert payload["flags"] == IS_COMPONENTS_V2
    assert payload["allowed_mentions"] == {"parse": []}


def test_bot_credentials_are_required() -> None:
    assert require_bot("token", "123") == ("token", "123")
    with pytest.raises(RuntimeError, match="FAQ_CHANNEL_ID"):
        require_bot("", "123")
    with pytest.raises(RuntimeError, match="numeric"):
        require_bot("token", "not-a-channel")


def test_checked_in_faq_fits_discord_limits() -> None:
    faq = Path(__file__).resolve().parents[2] / "docs" / "discord-faq.md"
    intro, sections = parse_faq(faq.read_text(encoding="utf-8"))
    messages = build_messages(intro, sections)
    assert sections
    assert messages[0][0]["content"].startswith("# Track Manager")
    titles = [section.title for section in sections]
    rendered_parts: list[str] = []

    def walk(components: list[dict]) -> None:
        for component in components:
            content = component.get("content")
            if isinstance(content, str):
                rendered_parts.append(content)
            children = component.get("components")
            if isinstance(children, list):
                walk(children)

    for message in messages:
        walk(message)
    rendered = "\n".join(rendered_parts)
    for title in titles:
        assert f"## {title}" in rendered
    for message in messages:
        assert text_length(message) <= MAX_TEXT_CHARS
        assert component_count(message) <= MAX_COMPONENTS
        payload = message_payload(message)
        containers = [
            component
            for component in payload["components"]
            if component.get("type") == CONTAINER
        ]
        assert containers
        assert all("spoiler" not in container for container in containers)
