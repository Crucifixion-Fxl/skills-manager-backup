#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""Find likely duplicate GitLab or GitHub issues for a proposed issue."""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is",
    "are", "was", "were", "be", "by", "at", "from", "as", "this", "that", "it",
}

DEFAULT_REPOS = (
    "SWCLIEN/g0-ios",
    "SWCLIEN/g0-android",
    "CLOUD/iot-service-old",
    "CLOUD/iot-service-unified",
    "issues/software/backend",
    "issues/software/frontend",
    "issues/software/unknown",
    "issues/software/natural-business",
    "issues/software/vas",
    "issues/software/frontend-sentry",
    "issues/software/backend-sentry",
    "issues/software/feedback",
)

LEXICON_PATH = Path(__file__).resolve().parents[1] / "references" / "lexicon.json"


@functools.cache
def load_lexicon() -> dict[str, Any]:
    try:
        return json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {"stopwords": [], "aliases": []}


@functools.cache
def stopwords() -> frozenset[str]:
    return frozenset(ENGLISH_STOPWORDS | set(load_lexicon().get("stopwords", [])))


@functools.cache
def aliases() -> tuple[tuple[str, str], ...]:
    return tuple(tuple(item) for item in load_lexicon().get("aliases", []))


def run(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def detect_platform() -> str:
    try:
        remote = run(["git", "remote", "get-url", "origin"]).lower()
    except subprocess.CalledProcessError:
        remote = ""
    if "gitlab" in remote and command_exists("glab"):
        return "gitlab"
    if "github" in remote and command_exists("gh"):
        return "github"
    if command_exists("glab"):
        return "gitlab"
    if command_exists("gh"):
        return "github"
    raise SystemExit("Neither glab nor gh is available.")


def fetch_gitlab(repo: str | None, state: str, per_page: int) -> list[dict[str, Any]]:
    command = ["glab", "issue", "list", "--output", "json", "--per-page", str(per_page)]
    if state == "all":
        command.append("--all")
    elif state == "closed":
        command.append("--closed")
    if repo:
        command.extend(["--repo", repo])
    return json.loads(run(command))


def fetch_github(repo: str | None, state: str, limit: int) -> list[dict[str, Any]]:
    fields = "number,title,body,state,labels,assignees,author,createdAt,updatedAt,closedAt,url"
    command = ["gh", "issue", "list", "--state", state, "--limit", str(limit), "--json", fields]
    if repo:
        command.extend(["--repo", repo])
    return json.loads(run(command))


def normalize_issue(raw: dict[str, Any], platform: str, source_repo: str | None) -> dict[str, Any]:
    if platform == "github":
        return {
            "number": raw.get("number"),
            "title": raw.get("title") or "",
            "body": raw.get("body") or "",
            "state": (raw.get("state") or "").lower(),
            "labels": [label.get("name", "") for label in raw.get("labels", []) if label.get("name")],
            "assignees": [user.get("login", "") for user in raw.get("assignees", []) if user.get("login")],
            "author": (raw.get("author") or {}).get("login") or "",
            "updated_at": raw.get("updatedAt"),
            "url": raw.get("url"),
            "repo": source_repo or "current",
        }
    return {
        "number": raw.get("iid"),
        "title": raw.get("title") or "",
        "body": raw.get("description") or "",
        "state": (raw.get("state") or "").lower(),
        "labels": raw.get("labels") or [],
        "assignees": [user.get("username", "") for user in raw.get("assignees", []) if user.get("username")],
        "author": (raw.get("author") or {}).get("username") or "",
        "updated_at": raw.get("updated_at"),
        "url": raw.get("web_url"),
        "repo": source_repo or "current",
    }


@functools.cache
def normalize_text(text: str) -> str:
    normalized = text.lower()
    for old, new in aliases():
        normalized = normalized.replace(old, new)
    return normalized


@functools.cache
def tokens(text: str) -> tuple[str, ...]:
    normalized = normalize_text(text)
    stops = stopwords()
    result: list[str] = []
    for part in re.findall(r"[a-z0-9_:-]+|[\u4e00-\u9fff]+", normalized):
        if part in stops:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) <= 2:
                result.append(part)
                continue
            result.extend(part[index : index + 2] for index in range(len(part) - 1))
            result.extend(part[index : index + 3] for index in range(len(part) - 2))
            continue
        if len(part) > 1:
            result.append(part)
    return tuple(result)


@functools.cache
def compact_chars(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9\u4e00-\u9fff]", normalize_text(text)))


@functools.cache
def char_ngrams(text: str, size: int) -> frozenset[str]:
    chars = compact_chars(text)
    if len(chars) <= size:
        return frozenset({chars}) if chars else frozenset()
    return frozenset(chars[index : index + size] for index in range(len(chars) - size + 1))


def cosine(left: Counter[str], right: Counter[str]) -> float:
    shared = set(left) & set(right)
    dot = sum(left[word] * right[word] for word in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_issue(title: str, body: str, issue: dict[str, Any]) -> dict[str, float]:
    normalized_title = normalize_text(title)
    normalized_issue_title = normalize_text(issue["title"])
    proposed_text = f"{title}\n{body}"
    issue_text = f"{issue['title']}\n{issue['body']}"
    proposed_title_tokens = tokens(title)
    proposed_body_tokens = tokens(body)
    issue_title_tokens = tokens(issue["title"])
    issue_body_tokens = tokens(issue["body"])

    title_ratio = SequenceMatcher(None, normalized_title, normalized_issue_title).ratio()
    title_token = jaccard(set(proposed_title_tokens), set(issue_title_tokens))
    body_cos = cosine(Counter(proposed_body_tokens), Counter(issue_body_tokens))
    combined_cos = cosine(
        Counter(proposed_title_tokens * 3 + proposed_body_tokens),
        Counter(issue_title_tokens * 3 + issue_body_tokens),
    )
    char_overlap = 0.45 * jaccard(char_ngrams(proposed_text, 2), char_ngrams(issue_text, 2))
    char_overlap += 0.55 * jaccard(char_ngrams(proposed_text, 3), char_ngrams(issue_text, 3))
    label_hint = jaccard(set(extract_label_like(title + " " + body)), set(issue["labels"]))

    score = 0.30 * title_ratio + 0.18 * title_token + 0.22 * combined_cos + 0.10 * body_cos + 0.16 * char_overlap + 0.04 * label_hint
    return {
        "score": round(score, 4),
        "title_ratio": round(title_ratio, 4),
        "title_token": round(title_token, 4),
        "combined_cosine": round(combined_cos, 4),
        "body_cosine": round(body_cos, 4),
        "char_overlap": round(char_overlap, 4),
        "label_hint": round(label_hint, 4),
    }


@functools.cache
def extract_label_like(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\b(?:area|type|priority|status)::[\w-]+\b|\bp[0-3]\b|\bbug\b", text.lower()))


INSUFFICIENT_DATA = "insufficient data — fix CLI/permissions and rerun before deciding"


def recommendation(match: dict[str, Any] | None, *, all_failed: bool = False) -> str:
    if all_failed:
        return INSUFFICIENT_DATA
    if not match:
        return "new issue"
    score = match["score"]["score"]
    state = match["issue"]["state"]
    if score >= 0.55 and state in {"opened", "open"}:
        return "merge/update existing"
    if score >= 0.55 and state == "closed":
        return "reopen/comment if same regression; otherwise new issue with related link"
    if score >= 0.25 and state in {"opened", "open"}:
        return "compare root cause; merge if same, otherwise new issue with related link"
    if score >= 0.25 and state == "closed":
        return "new issue with related link unless it is the same regression"
    return "new issue"


def print_markdown(matches: list[dict[str, Any]], *, all_failed: bool = False) -> None:
    top = matches[0] if matches else None
    print(f"Recommendation: {recommendation(top, all_failed=all_failed)}")
    print()
    if all_failed:
        print("All target repositories returned errors; nothing was searched.")
        return
    if not matches:
        print("No likely duplicates found.")
        return
    print("| Score | Repo | State | Issue | Assignees | Labels |")
    print("| ---: | --- | --- | --- | --- | --- |")
    for match in matches:
        issue = match["issue"]
        assignees = ", ".join(issue["assignees"]) or "unassigned"
        labels = ", ".join(issue["labels"]) or "-"
        issue_link = f"[#{issue['number']} {escape_pipe(issue['title'])}]({issue['url']})"
        print(f"| {match['score']['score']:.2f} | {escape_pipe(issue['repo'])} | {issue['state']} | {issue_link} | {escape_pipe(assignees)} | {escape_pipe(labels)} |")
    print()
    print("Use the score as a candidate ranking. Final decision should compare root cause, affected component, environment, and acceptance criteria.")


def escape_pipe(value: str) -> str:
    return value.replace("|", "\\|")


def configured_repos() -> list[str]:
    repos: list[str] = list(DEFAULT_REPOS)
    env_value = os.environ.get("ISSUE_DEDUPE_REPOS", "")
    repos.extend(split_repos(env_value))
    for path in (Path.cwd() / ".issue-dedupe-repos", Path.home() / ".codex" / "issue-dedupe-repos.txt"):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    repos.extend(split_repos(stripped))
    return dedupe_repos(repos)


def split_repos(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]


def dedupe_repos(repos: list[str]) -> list[str]:
    return list(dict.fromkeys(repos))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["auto", "gitlab", "github"], default="auto")
    parser.add_argument("--repo", action="append", default=[], help="Repository to search. Repeatable.")
    parser.add_argument("--extra-repo", action="append", default=[], help="Additional related repository to search. Repeatable.")
    parser.add_argument("--state", choices=["open", "closed", "all"], default="all")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--body-file")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="Print JSON")
    args = parser.parse_args()

    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")

    platform = detect_platform() if args.platform == "auto" else args.platform
    base_repos = args.repo if args.repo else [None]
    repos = dedupe_repos(base_repos + args.extra_repo + configured_repos())
    if not repos:
        repos = [None]

    cli = "glab" if platform == "gitlab" else "gh"
    if not command_exists(cli):
        raise SystemExit(f"{cli} CLI is not installed.")
    fetch = fetch_gitlab if platform == "gitlab" else fetch_github

    normalized: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(len(repos), 4)) as executor:
        future_to_repo = {executor.submit(fetch, repo, args.state, args.limit): repo for repo in repos}
        for future in as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                raw_issues = future.result()
            except subprocess.CalledProcessError as exc:
                failures.append({"repo": repo or "current", "error": (exc.stderr or str(exc)).strip()})
                continue
            normalized.extend(normalize_issue(issue, platform, repo) for issue in raw_issues)

    matches = []
    for issue in normalized:
        score = score_issue(args.title, body, issue)
        if score["score"] >= 0.18:
            matches.append({"issue": issue, "score": score})
    matches.sort(key=lambda item: item["score"]["score"], reverse=True)
    matches = matches[: args.top]

    all_failed = bool(failures) and len(failures) == len(repos) and not normalized
    payload = {
        "platform": platform,
        "repos": [repo or "current" for repo in repos],
        "searched": len(normalized),
        "failures": failures,
        "recommendation": recommendation(matches[0] if matches else None, all_failed=all_failed),
        "matches": matches,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if failures:
            print("Search warnings:")
            for failure in failures:
                print(f"- {failure['repo']}: {failure['error']}")
            print()
        print_markdown(matches, all_failed=all_failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
