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
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_DIFF_CHARS = 40_000
MAX_SUMMARY_CHARS = 1000


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

    payload = {
        "model": model,
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.load(response)

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
    payload: dict[str, Any] = {
        "embeds": [
            {
                "title": title,
                "description": summary,
                "url": compare_url,
                "color": 5763719,
                "footer": {"text": f"{commit_count} commit(s)"},
            }
        ]
    }
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 400:
            raise RuntimeError(f"Discord webhook failed with status {response.status}")


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
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
        context = collect_push_context(before_sha, after_sha)
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
        post_to_discord(
            webhook_url=webhook_url,
            title=f"{repo.split('/')[-1]} → {branch}",
            summary=summary,
            compare_url=compare_url
            or f"https://github.com/{repo}/commit/{after_sha}",
            commit_count=commit_count,
        )
    except (urllib.error.HTTPError, urllib.error.URLError, subprocess.CalledProcessError) as exc:
        print(f"Push summary failed: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Posted push summary to Discord")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
