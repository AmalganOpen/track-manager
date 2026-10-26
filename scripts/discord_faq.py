#!/usr/bin/env python3
"""Post a FAQ markdown file as Discord cards.

Each ``##`` section becomes a container: the question, a divider, and the
answer, all visible. The text before the first ``##`` is the intro.

Discord limits a components-v2 message to 4000 characters of text and 40
components, so a long FAQ is split across several messages.

The bot posts with ``DISCORD_BOT_TOKEN`` and ``FAQ_CHANNEL_ID``. A ``.env``
in the repo root is loaded when present. The push-summary webhook is a
different sender and is not used here.

Usage::

    python scripts/discord_faq.py docs/discord-faq.md --dry-run
    python scripts/discord_faq.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEXT_DISPLAY = 10
SEPARATOR = 14
CONTAINER = 17
IS_COMPONENTS_V2 = 1 << 15
MAX_TEXT_CHARS = 4000
MAX_COMPONENTS = 40
# Discord blurple. One accent so the cards read as a set.
ACCENT_COLOR = 0x5865F2
USER_AGENT = "TrackManager-FAQ/1.0 (+https://github.com/AmalganOpen/track-manager)"
API_BASE = "https://discord.com/api/v10"
SECTION_SPLIT = re.compile(r"(?m)^## ")


@dataclass(frozen=True)
class FaqSection:
    """One ``##`` block from the FAQ file."""

    title: str
    body: str


def repo_root() -> Path:
    """Directory that contains this script's parent (the repository root)."""
    return Path(__file__).resolve().parent.parent


def default_faq_path() -> Path:
    return repo_root() / "docs" / "discord-faq.md"


def load_env_file(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` lines from ``.env`` without overriding existing vars."""
    if path is None:
        path = repo_root() / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def parse_faq(markdown: str) -> tuple[str, list[FaqSection]]:
    """Split FAQ markdown into an intro and ``##`` sections.

    A section title is the rest of the ``##`` line. Its body runs until the
    next ``##`` at the start of a line. ``##`` inside a code sample would
    start a new section, so keep sample headings indented or fenced without
    a leading ``##``.
    """
    text = markdown.replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("FAQ file is empty")

    parts = SECTION_SPLIT.split(text)
    intro = parts[0].strip()
    sections: list[FaqSection] = []
    for part in parts[1:]:
        title, sep, body = part.partition("\n")
        title = title.strip()
        body = body.strip() if sep else ""
        if not title:
            raise ValueError("FAQ section is missing a title")
        if not body:
            raise ValueError(f"FAQ section {title!r} has no body")
        sections.append(FaqSection(title=title, body=body))

    if not intro and not sections:
        raise ValueError("FAQ file is empty")
    return intro, sections


def section_components(section: FaqSection) -> list[dict[str, Any]]:
    """One open card: question, divider, answer."""
    return [
        {
            "type": CONTAINER,
            "accent_color": ACCENT_COLOR,
            "components": [
                {"type": TEXT_DISPLAY, "content": f"## {section.title}"},
                {"type": SEPARATOR, "divider": True, "spacing": 1},
                {"type": TEXT_DISPLAY, "content": section.body},
            ],
        }
    ]


def text_length(components: list[dict[str, Any]]) -> int:
    """Characters across every text display, including ones nested in containers."""
    total = 0
    for component in components:
        if component.get("type") == TEXT_DISPLAY:
            total += len(str(component.get("content", "")))
        children = component.get("components")
        if isinstance(children, list):
            total += text_length(children)
    return total


def component_count(components: list[dict[str, Any]]) -> int:
    """Total components, including nested ones. Discord allows 40 per message."""
    total = 0
    for component in components:
        total += 1
        children = component.get("components")
        if isinstance(children, list):
            total += component_count(children)
    return total


def _fits(current: list[dict[str, Any]], extra: list[dict[str, Any]]) -> bool:
    trial = [*current, *extra]
    return (
        text_length(trial) <= MAX_TEXT_CHARS
        and component_count(trial) <= MAX_COMPONENTS
    )


def build_messages(
    intro: str, sections: list[FaqSection]
) -> list[list[dict[str, Any]]]:
    """Pack intro and sections into messages that respect Discord's limits."""
    messages: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if current:
            messages.append(current)
            current = []

    if intro:
        intro_block = [{"type": TEXT_DISPLAY, "content": intro}]
        if not _fits([], intro_block):
            raise ValueError(
                f"FAQ intro is {len(intro)} characters; Discord allows {MAX_TEXT_CHARS}"
            )
        current.extend(intro_block)

    for section in sections:
        card = section_components(section)
        card_text = text_length(card)
        if card_text > MAX_TEXT_CHARS or component_count(card) > MAX_COMPONENTS:
            raise ValueError(
                f"FAQ section {section.title!r} is {card_text} characters; "
                f"Discord allows {MAX_TEXT_CHARS} per message"
            )
        if current and not _fits(current, card):
            flush()
        current.extend(card)

    flush()
    if not messages:
        raise ValueError("FAQ file is empty")
    return messages


def message_payload(components: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "flags": IS_COMPONENTS_V2,
        "components": components,
        "allowed_mentions": {"parse": []},
    }


def post_json(
    *, url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip() or exc.reason
        raise RuntimeError(f"Discord HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Discord request failed: {exc.reason}") from exc
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Discord returned a non-object response")
    return parsed


def send_messages(
    payloads: list[dict[str, Any]],
    *,
    bot_token: str,
    channel_id: str,
) -> list[str]:
    """Post each payload with the bot. Returns Discord message ids."""
    token = bot_token.removeprefix("Bot ").strip()
    url = f"{API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    ids: list[str] = []
    for payload in payloads:
        sent = post_json(url=url, payload=payload, headers=headers)
        message_id = sent.get("id")
        if isinstance(message_id, str):
            ids.append(message_id)
    return ids


def require_bot(bot_token: str, channel_id: str) -> tuple[str, str]:
    """Return the bot token and channel id, or raise when either is missing."""
    if not bot_token or not channel_id:
        raise RuntimeError("Set DISCORD_BOT_TOKEN and FAQ_CHANNEL_ID.")
    if not channel_id.isdigit():
        raise RuntimeError("FAQ_CHANNEL_ID must be a numeric channel id")
    return bot_token, channel_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "faq",
        nargs="?",
        type=Path,
        help="FAQ markdown file (default: docs/discord-faq.md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Discord payloads and do not send them",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file()

    faq_path = args.faq if args.faq is not None else default_faq_path()
    if not faq_path.is_file():
        raise RuntimeError(f"FAQ file not found: {faq_path}")

    intro, sections = parse_faq(faq_path.read_text(encoding="utf-8"))
    messages = build_messages(intro, sections)
    payloads = [message_payload(components) for components in messages]

    if args.dry_run:
        json.dump(payloads, sys.stdout, indent=2)
        sys.stdout.write("\n")
        print(
            f"dry run: {len(payloads)} message(s), {len(sections)} card(s), not sent",
            file=sys.stderr,
        )
        return 0

    bot_token, channel_id = require_bot(
        os.environ.get("DISCORD_BOT_TOKEN", "").strip(),
        os.environ.get("FAQ_CHANNEL_ID", "").strip(),
    )
    ids = send_messages(
        payloads,
        bot_token=bot_token,
        channel_id=channel_id,
    )
    print(f"sent {len(payloads)} message(s) to Discord ({len(ids)} id(s) returned)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
