#!/usr/bin/env python3
"""Summarize a git push with Claude and post to a Discord webhook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"
FALLBACK_MODELS = (
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
)
MAX_DIFF_CHARS = 40_000
MAX_SUMMARY_CHARS = 1000


def validate_config(*, api_key: str, webhook_url: str, model: str) -> None:
    if not api_key.startswith("sk-ant-"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY does not look valid. Create one at "
            "https://console.anthropic.com/settings/keys (must start with sk-ant-). "
            "Do not use a Claude.ai login token."
        )
    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL must be the full Discord webhook URL "
            "(https://discord.com/api/webhooks/{id}/{token})."
        )
    if not model:
        raise RuntimeError("ANTHROPIC_MODEL resolved to an empty value.")


def post_json(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    service: str,
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip() or exc.reason
        raise RuntimeError(f"{service} HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{service} request failed: {exc.reason}") from exc

    if not raw:
        return {}
    return json.loads(raw)


def normalize_secret(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def is_new_branch(before_sha: str) -> bool:
    return before_sha == "0" * 40


def collect_push_context(before_sha: str, after_sha: str) -> dict[str, str]:
    if is_new_branch(before_sha):
        commit_log = run_git("log", "--format=%h %s (%an)", "-n", "20", after_sha)
        diff_stat = run_git("show", "--stat", "--format=", after_sha)
        diff_patch = run_git("show", "--format=", after_sha)
    else:
        log_range = f"{before_sha}..{after_sha}"
        commit_log = run_git("log", "--format=%h %s (%an)", log_range)
        diff_stat = run_git("diff", "--stat", log_range)
        diff_patch = run_git("diff", log_range)
    if len(diff_patch) > MAX_DIFF_CHARS:
        diff_patch = diff_patch[:MAX_DIFF_CHARS] + "\n\n[diff truncated]"

    return {
        "commit_log": commit_log or "(no commits)",
        "diff_stat": diff_stat or "(no diff stat)",
        "diff_patch": diff_patch or "(no patch)",
    }


def summarize_with_claude(
    *,
    repo: str,
    branch: str,
    pusher: str,
    context: dict[str, str],
    api_key: str,
    model: str,
) -> str:
    prompt = f"""Summarize this git push for a Discord notification in the track-manager repo.

Write 2-4 short sentences for developers. Mention the main areas/files touched and what changed at a high level. Skip boilerplate and test-only churn unless it is the whole push. Do not use markdown headings or bullet lists.

Repository: {repo}
Branch: {branch}
Pushed by: {pusher}

Commits:
{context["commit_log"]}

Diff stat:
{context["diff_stat"]}

Patch (may be truncated):
{context["diff_patch"]}
"""

    models = [model, *(m for m in FALLBACK_MODELS if m != model)]
    last_error: RuntimeError | None = None

    for candidate in models:
        try:
            return _summarize_with_model(
                prompt=prompt,
                api_key=api_key,
                model=candidate,
            )
        except RuntimeError as exc:
            last_error = exc
            message = str(exc)
            if "HTTP 404" in message or "not_found" in message:
                print(f"Model {candidate} unavailable, trying fallback...", flush=True)
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("Claude summarization failed")


def _summarize_with_model(*, prompt: str, api_key: str, model: str) -> str:
    body = post_json(
        url=ANTHROPIC_API_URL,
        payload={
            "model": model,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "User-Agent": "track-manager-discord-notify/1.0",
        },
        service=f"Anthropic ({model})",
        timeout=120,
    )

    parts = body.get("content") or []
    text_parts = [part.get("text", "") for part in parts if part.get("type") == "text"]
    summary = "\n".join(text_parts).strip()
    if not summary:
        raise RuntimeError("Claude returned an empty summary")
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 3] + "..."
    return summary


def post_to_discord(
    *,
    webhook_url: str,
    title: str,
    summary: str,
    compare_url: str,
    commit_count: int,
) -> None:
    post_json(
        url=webhook_url,
        payload={
            "embeds": [
                {
                    "title": title,
                    "description": summary,
                    "url": compare_url,
                    "color": 5763719,
                    "footer": {"text": f"{commit_count} commit(s)"},
                }
            ]
        },
        headers={"Content-Type": "application/json"},
        service="Discord",
        timeout=30,
    )


def main() -> int:
    api_key = normalize_secret(os.environ.get("ANTHROPIC_API_KEY", ""))
    webhook_url = normalize_secret(os.environ.get("DISCORD_WEBHOOK_URL", ""))
    before_sha = os.environ.get("GITHUB_BEFORE_SHA", "").strip()
    after_sha = os.environ.get("GITHUB_AFTER_SHA", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "unknown/repo").strip()
    branch = os.environ.get("GITHUB_REF_NAME", "unknown").strip()
    pusher = os.environ.get("GITHUB_ACTOR", "unknown").strip()
    compare_url = os.environ.get("GITHUB_COMPARE_URL", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    missing = [
        name
        for name, value in [
            ("ANTHROPIC_API_KEY", api_key),
            ("DISCORD_WEBHOOK_URL", webhook_url),
            ("GITHUB_BEFORE_SHA", before_sha),
            ("GITHUB_AFTER_SHA", after_sha),
        ]
        if not value
    ]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        validate_config(api_key=api_key, webhook_url=webhook_url, model=model)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        context = collect_push_context(before_sha, after_sha)
        print(f"Summarizing push with {model}...", flush=True)
        summary = summarize_with_claude(
            repo=repo,
            branch=branch,
            pusher=pusher,
            context=context,
            api_key=api_key,
            model=model,
        )
        commit_count = len(
            [line for line in context["commit_log"].splitlines() if line.strip()]
        )
        print("Posting summary to Discord...", flush=True)
        post_to_discord(
            webhook_url=webhook_url,
            title=f"{repo.split('/')[-1]} → {branch}",
            summary=summary,
            compare_url=compare_url
            or f"https://github.com/{repo}/commit/{after_sha}",
            commit_count=commit_count,
        )
    except subprocess.CalledProcessError as exc:
        print(f"Git command failed: {exc.stderr or exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Posted push summary to Discord")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
