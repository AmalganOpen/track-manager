#!/usr/bin/env python3
"""Summarize arbitrary git commits with Claude and post to Discord.

Secrets are read from the environment. For local use, copy ``.env.example`` to
``.env`` in the repo root (gitignored)::

  cp .env.example .env

Usage::

  python scripts/discord_summarize_commits.py abc1234
  python scripts/discord_summarize_commits.py main~5..main
  python scripts/discord_summarize_commits.py --dry-run HEAD~3..HEAD
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import discord_push_summary as notify  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize git commits with Claude and post to Discord.",
    )
    parser.add_argument(
        "revisions",
        nargs="+",
        metavar="REV",
        help="Commit SHA(s) and/or git ranges (e.g. abc123, HEAD~3..HEAD)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Print the summary without posting to Discord",
    )
    parser.add_argument(
        "--repo",
        help="Repository slug for the embed (default: parse from git origin)",
    )
    parser.add_argument(
        "--branch",
        help="Branch label for the embed (default: current git branch)",
    )
    parser.add_argument(
        "--title",
        help="Discord embed title (default: '<repo> → <branch>')",
    )
    parser.add_argument(
        "--compare-url",
        help="Link for the embed (default: GitHub compare/commit URL)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", notify.DEFAULT_MODEL),
        help=f"Claude model (default: {notify.DEFAULT_MODEL})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    notify.load_env_file()
    args = parse_args(argv)

    api_key = notify.normalize_secret(os.environ.get("ANTHROPIC_API_KEY", ""))
    webhook_url = notify.normalize_secret(os.environ.get("DISCORD_WEBHOOK_URL", ""))
    model = args.model.strip() or notify.DEFAULT_MODEL

    if not api_key:
        print("Missing ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    if not args.dry_run and not webhook_url:
        print(
            "Missing DISCORD_WEBHOOK_URL (or pass --dry-run to skip posting)",
            file=sys.stderr,
        )
        return 1

    try:
        notify.validate_config(
            api_key=api_key,
            webhook_url=webhook_url or "https://discord.com/api/webhooks/0/x",
            model=model,
        )
    except RuntimeError as exc:
        if not args.dry_run:
            print(str(exc), file=sys.stderr)
            return 1

    repo = args.repo or notify.git_repo_slug()
    branch = args.branch or notify.git_branch()

    try:
        commits = notify.get_commits_for_revisions(args.revisions)
        if not commits:
            print("No commits matched the given revision(s)", file=sys.stderr)
            return 1

        context = notify.collect_commits_context(commits)
        if context is None:
            print("Nothing to post: all commits are marked --silent")
            return 0

        print(
            f"Summarizing {len(context['reportable_shas'])} commit(s) with {model}...",
            flush=True,
        )
        summary = notify.summarize_with_claude(
            repo=repo,
            branch=branch,
            pusher=os.environ.get("USER", "local"),
            context=context,
            api_key=api_key,
            model=model,
        )

        if args.dry_run:
            print()
            print(summary)
            return 0

        title = args.title or f"{repo.split('/')[-1]} → {branch}"
        compare_url = args.compare_url or notify.compare_url_for_revisions(
            repo, args.revisions
        )
        commit_count = len(context["reportable_shas"])

        print("Posting summary to Discord...", flush=True)
        notify.post_to_discord(
            webhook_url=webhook_url,
            title=title,
            summary=summary,
            compare_url=compare_url,
            commit_count=commit_count,
        )
    except subprocess.CalledProcessError as exc:
        print(f"Git command failed: {exc.stderr or exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Posted commit summary to Discord")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
