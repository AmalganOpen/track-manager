#!/usr/bin/env python3
"""Summarize a git push with Claude and post to a Discord webhook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GITHUB_API_VERSION = "2022-11-28"
DEFAULT_MODEL = "claude-sonnet-4-6"
FALLBACK_MODELS = (
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
)
MAX_DIFF_CHARS = 40_000
MAX_SUMMARY_CHARS = 1000
DISCORD_USER_AGENT = (
    "TrackManager-GitHubActions/1.0 (+https://github.com/AmalganOpen/track-manager)"
)
SILENT_MARKER = "--silent"
FIELD_SEP = "\x1e"
COMMIT_SEP = "\x1f"
LOG_FORMAT = f"%H{FIELD_SEP}%s{FIELD_SEP}%b{FIELD_SEP}%an{COMMIT_SEP}"


def repo_root() -> Path | None:
    """Return the git repo root, or the package root when not in a git checkout."""
    try:
        return Path(run_git("rev-parse", "--show-toplevel"))
    except subprocess.CalledProcessError:
        candidate = Path(__file__).resolve().parent.parent
        if (candidate / "pyproject.toml").is_file():
            return candidate
        return None


def load_env_file(path: Path | None = None, *, override: bool = False) -> Path | None:
    """Load ``KEY=VALUE`` lines from a dotenv file into :data:`os.environ`.

    Skips comments and blank lines. Supports optional ``export `` prefixes and
    single/double-quoted values. Existing environment variables are left alone
    unless ``override`` is True.

    Returns the path that was loaded, or None if the file does not exist.
    """
    if path is None:
        root = repo_root()
        if root is None:
            return None
        path = root / ".env"

    if not path.is_file():
        return None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
    return path


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


def get_json(
    *,
    url: str,
    headers: dict[str, str],
    service: str,
    timeout: int,
) -> Any:
    request = urllib.request.Request(url, headers=headers, method="GET")
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


def is_silent_commit(message: str) -> bool:
    return SILENT_MARKER in message


def parse_commit_records(raw: str) -> list[tuple[str, str, str, str]]:
    """Parse ``git log`` output produced with :data:`LOG_FORMAT`."""
    commits: list[tuple[str, str, str, str]] = []
    for record in raw.split(COMMIT_SEP):
        record = record.strip()
        if not record:
            continue
        parts = record.split(FIELD_SEP, 3)
        if len(parts) != 4:
            print(
                f"Skipping malformed commit record ({len(parts)} fields): "
                f"{record[:80]!r}",
                file=sys.stderr,
                flush=True,
            )
            continue
        sha, subject, body, author = parts
        commits.append((sha, subject, body, author))
    return commits


def get_commits_from_revision(revision: str) -> list[tuple[str, str, str, str]]:
    """Return commits for one revision (single SHA or ``from..to`` range)."""
    if ".." in revision:
        raw = run_git("log", f"--format={LOG_FORMAT}", revision)
    else:
        sha = run_git("rev-parse", "--verify", revision)
        raw = run_git("log", f"--format={LOG_FORMAT}", "-n", "1", sha)
    return parse_commit_records(raw)


def get_commits_for_revisions(
    revisions: list[str],
) -> list[tuple[str, str, str, str]]:
    """Resolve one or more git revisions, deduplicating by full SHA."""
    commits: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for revision in revisions:
        for commit in get_commits_from_revision(revision):
            if commit[0] in seen:
                continue
            seen.add(commit[0])
            commits.append(commit)
    return commits


def collect_commits_context(
    commits: list[tuple[str, str, str, str]],
) -> dict[str, str] | None:
    """Build LLM context for an explicit list of commits."""
    reportable = [
        (sha, subject, author)
        for sha, subject, body, author in commits
        if not is_silent_commit(f"{subject}\n{body}")
    ]

    if not reportable:
        return None

    silent_count = len(commits) - len(reportable)
    if silent_count:
        print(f"Ignoring {silent_count} commit(s) marked --silent", flush=True)

    commit_log = "\n".join(
        f"{sha[:7]} {subject} ({author})" for sha, subject, author in reportable
    )

    diff_stats: list[str] = []
    diff_patches: list[str] = []
    for sha, _subject, _author in reversed(reportable):
        diff_stats.append(run_git("show", "--stat", "--format=", sha))
        diff_patches.append(run_git("show", "--format=", sha))

    diff_stat = "\n\n".join(diff_stats)
    diff_patch = "\n\n".join(diff_patches)
    if len(diff_patch) > MAX_DIFF_CHARS:
        diff_patch = diff_patch[:MAX_DIFF_CHARS] + "\n\n[diff truncated]"

    return {
        "commit_log": commit_log or "(no commits)",
        "diff_stat": diff_stat or "(no diff stat)",
        "diff_patch": diff_patch or "(no patch)",
        "reportable_shas": [sha for sha, _subject, _author in reportable],
    }


def git_repo_slug() -> str:
    """Best-effort ``owner/repo`` slug from ``origin``."""
    try:
        url = run_git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return "unknown/repo"

    url = url.removesuffix(".git")
    if url.startswith("git@"):
        _, path = url.split(":", 1)
        return path
    if "github.com/" in url:
        return url.split("github.com/", 1)[1]
    return url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1]


def git_branch() -> str:
    """Current branch name, or ``HEAD`` when detached."""
    try:
        return run_git("rev-parse", "--abbrev-ref", "HEAD")
    except subprocess.CalledProcessError:
        return "unknown"


def compare_url_for_revisions(repo: str, revisions: list[str]) -> str:
    """Build a GitHub compare/commit URL for the given revision args."""
    base = f"https://github.com/{repo}"
    if len(revisions) == 1 and ".." not in revisions[0]:
        sha = run_git("rev-parse", "--verify", revisions[0])
        return f"{base}/commit/{sha}"
    if len(revisions) == 1 and ".." in revisions[0]:
        left, _, right = revisions[0].partition("..")
        left_sha = run_git("rev-parse", "--verify", left) if left else ""
        right_sha = run_git("rev-parse", "--verify", right)
        if left_sha:
            return f"{base}/compare/{left_sha}...{right_sha}"
        return f"{base}/commit/{right_sha}"
    if revisions:
        first = run_git("rev-parse", "--verify", revisions[0])
        last = run_git("rev-parse", "--verify", revisions[-1])
        if first != last:
            return f"{base}/compare/{first}...{last}"
        return f"{base}/commit/{first}"
    return base


def get_commits_in_push(
    before_sha: str, after_sha: str
) -> list[tuple[str, str, str, str]]:
    if is_new_branch(before_sha):
        raw = run_git("log", f"--format={LOG_FORMAT}", "-n", "20", after_sha)
    else:
        raw = run_git("log", f"--format={LOG_FORMAT}", f"{before_sha}..{after_sha}")
    return parse_commit_records(raw)


def collect_push_context(before_sha: str, after_sha: str) -> dict[str, str] | None:
    commits = get_commits_in_push(before_sha, after_sha)
    return collect_commits_context(commits)


def github_api_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": DISCORD_USER_AGENT,
    }


def find_pull_request_for_commit(
    repo: str, sha: str, token: str
) -> dict[str, Any] | None:
    """Return the merged PR associated with a commit, if any."""
    if not token:
        return None

    url = f"https://api.github.com/repos/{repo}/commits/{sha}/pulls"
    try:
        pulls = get_json(
            url=url,
            headers=github_api_headers(token),
            service="GitHub",
            timeout=30,
        )
    except RuntimeError as exc:
        print(f"Could not look up PR for {sha[:7]}: {exc}", flush=True)
        return None

    if not isinstance(pulls, list) or not pulls:
        return None

    merged = [pr for pr in pulls if pr.get("merged_at")]
    return merged[0] if merged else pulls[0]


def resolve_push_credit(
    *,
    repo: str,
    after_sha: str,
    pusher: str,
    github_token: str,
) -> tuple[str, str | None, int | None]:
    """Return credited author login, optional PR URL, and optional PR number."""
    pr = find_pull_request_for_commit(repo, after_sha, github_token)
    if pr is None:
        return pusher, None, None

    author = str((pr.get("user") or {}).get("login") or pusher)
    pr_number = pr.get("number")
    pr_url = pr.get("html_url")

    parsed_number = pr_number if isinstance(pr_number, int) else None
    parsed_url = pr_url if isinstance(pr_url, str) else None
    return author, parsed_url, parsed_number


def summarize_with_claude(
    *,
    repo: str,
    branch: str,
    pusher: str,
    context: dict[str, str],
    api_key: str,
    model: str,
) -> str:
    prompt = f"""Summarize this software update for a Discord notification to end users of track-manager (a music download and library tool).

Write 2-3 short sentences in plain, friendly language. Explain what is new, fixed, or improved from the user's perspective — how it affects downloading, playlists, metadata, duplicates, or day-to-day use.

Rules:
- Do NOT mention file names, code, tests, CI, refactors, or internal implementation details
- Do NOT use markdown headings or bullet lists
- Skip changes that are invisible to users (tests, tooling, docs-only) unless that is the whole update
- If the change is mostly internal, say so briefly in user-friendly terms

Repository: {repo}
Branch: {branch}

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
    author: str,
    title: str,
    summary: str,
    compare_url: str,
    commit_count: int,
    pr_number: int | None,
) -> None:
    footer = f"{commit_count} commit(s)"
    if pr_number is not None:
        footer = f"PR #{pr_number} · {footer}"

    post_json(
        url=webhook_url,
        payload={
            "embeds": [
                {
                    "author": {
                        "name": f"New contribution by @{author}",
                        "url": f"https://github.com/{author}",
                    },
                    "title": title,
                    "description": summary,
                    "url": compare_url,
                    "color": 5763719,
                    "footer": {"text": footer},
                }
            ]
        },
        headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_USER_AGENT,
        },
        service="Discord",
        timeout=30,
    )


def main() -> int:
    load_env_file()
    api_key = normalize_secret(os.environ.get("ANTHROPIC_API_KEY", ""))
    webhook_url = normalize_secret(os.environ.get("DISCORD_WEBHOOK_URL", ""))
    before_sha = os.environ.get("GITHUB_BEFORE_SHA", "").strip()
    after_sha = os.environ.get("GITHUB_AFTER_SHA", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "unknown/repo").strip()
    branch = os.environ.get("GITHUB_REF_NAME", "unknown").strip()
    pusher = os.environ.get("GITHUB_ACTOR", "unknown").strip()
    compare_url = os.environ.get("GITHUB_COMPARE_URL", "").strip()
    github_token = normalize_secret(os.environ.get("GITHUB_TOKEN", ""))
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
        print(
            f"Missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    try:
        validate_config(api_key=api_key, webhook_url=webhook_url, model=model)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        context = collect_push_context(before_sha, after_sha)
        if context is None:
            print("Skipping Discord notification: all commits marked --silent")
            return 0

        credited_author, pr_url, pr_number = resolve_push_credit(
            repo=repo,
            after_sha=after_sha,
            pusher=pusher,
            github_token=github_token,
        )
        print(f"Crediting @{credited_author}", flush=True)

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
        link_url = (
            pr_url or compare_url or f"https://github.com/{repo}/commit/{after_sha}"
        )
        print("Posting summary to Discord...", flush=True)
        post_to_discord(
            webhook_url=webhook_url,
            author=credited_author,
            title=f"{repo.split('/')[-1]} → {branch}",
            summary=summary,
            compare_url=link_url,
            commit_count=commit_count,
            pr_number=pr_number,
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
