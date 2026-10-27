#!/usr/bin/env python3
"""Read-only Issue binding preflight (single Issue, or legacy Root/Work pair); not a write authorization."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, urlsplit

REPOSITORY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)+")


def load_owner_host(path=None):
    """Consume the platform owner's sole canonical HTTPS origin, without fallback."""
    try:
        owner = path or Path(__file__).resolve().parents[3] / "delivery/gitlab/references/auth-discovery.json"
        instances = json.loads(Path(owner).read_text(encoding="utf-8")).get("instances")
        if not isinstance(instances, list) or len(instances) != 1 or not isinstance(instances[0], str):
            return None
        value = instances[0]
        if any(ord(char) <= 32 or ord(char) == 127 for char in value):
            return None
        url = urlsplit(value)
        if (url.scheme != "https" or value != "https://" + url.netloc or url.path or url.query or url.fragment
                or url.netloc != url.hostname or url.port is not None
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", url.netloc)):
            return None
        return url.hostname
    except (OSError, ValueError, TypeError, AttributeError, IndexError):
        return None


HOST = load_owner_host()


def parse_issue_url(value):
    """Return trusted repository/IID only; never interpolate arbitrary URL text."""
    if not HOST or not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("untrusted_issue_url")
    url = urlsplit(value)
    match = re.fullmatch(r"/(.+)/-/(?:issues|work_items)/([1-9][0-9]*)", url.path)
    if (url.scheme != "https" or url.netloc != HOST or url.query or url.fragment
            or not match or not REPOSITORY.fullmatch(match[1])):
        raise ValueError("untrusted_issue_url")
    return match[1], int(match[2])


def positive_id(value):
    return type(value) is int and value > 0


def identity(issue):
    return tuple(issue.get(key) for key in ("project_id", "iid", "id"))


def validate_binding(root, work, links, repository, require_local_work=True):
    """Validate fixtures or fresh API objects. Returns only safe error codes."""
    errors = []
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        errors.append("invalid_repository")
    for role, obj in (("root", root), ("work", work)):
        if not isinstance(obj, dict):
            errors.append(role + "_invalid_object")
            continue
        if obj.get("state") != "opened":
            errors.append(role + "_not_opened")
        assignees = obj.get("assignees")
        if not isinstance(assignees, list) or not any(
                isinstance(a, dict) and positive_id(a.get("id")) for a in assignees):
            errors.append(role + "_unassigned")
        if not all(positive_id(v) for v in identity(obj)):
            errors.append(role + "_invalid_identity")
        try:
            repo, iid = parse_issue_url(obj.get("web_url"))
            if iid != obj.get("iid") or (require_local_work and role == "work" and repo != repository):
                errors.append(role + "_repository_or_iid_mismatch")
        except ValueError:
            errors.append(role + "_untrusted_url")
    if errors:
        return errors
    if identity(root) != identity(work):
        for role, target in (("work", root), ("root", work)):
            candidates = links.get(role, []) if isinstance(links, dict) else []
            if not isinstance(candidates, list) or not any(
                    isinstance(item, dict) and identity(item) == identity(target)
                    and item.get("link_type") in ("relates_to", "blocks", "is_blocked_by")
                    for item in candidates):
                errors.append(role + "_native_link_missing")
    return errors


def read_api(glab, endpoint):
    if not HOST:
        raise ValueError("platform_owner_unverified")
    result = subprocess.run([glab, "api", "--hostname", HOST, "--method", "GET", endpoint],
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


def summary(obj):
    if not isinstance(obj, dict):
        return None
    result = {key: obj.get(key) if positive_id(obj.get(key)) else None
              for key in ("id", "project_id", "iid")}
    result["state"] = obj.get("state") if obj.get("state") in ("opened", "closed") else None
    updated = obj.get("updated_at")
    try:
        result["updated_at"] = datetime.fromisoformat(updated.replace("Z", "+00:00")).isoformat()
    except (AttributeError, ValueError, TypeError):
        result["updated_at"] = None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", help="single Issue URL; may belong to another project")
    parser.add_argument("--root-issue")
    parser.add_argument("--work-item")
    parser.add_argument("--repository")
    parser.add_argument("--glab", default="glab")
    parser.add_argument("--check-snapshot", metavar="FILE")
    args = parser.parse_args(argv)
    mode = "offline" if args.check_snapshot else "live"
    root, work, links, errors = None, None, {}, []
    repository = args.repository
    single = bool(args.issue)
    try:
        if args.issue and (args.root_issue or args.work_item):
            raise ValueError("ambiguous_binding_arguments")
        if mode == "offline":
            snapshot = Path(args.check_snapshot)
            if snapshot.stat().st_size > 1024 * 1024:
                raise ValueError("snapshot_too_large")
            data = json.loads(snapshot.read_text(encoding="utf-8"))
            single = "issue" in data
            if single:
                root = work = data.get("issue")
            else:
                root, work, links = data.get("root"), data.get("work"), data.get("links", {})
            repository = repository or data.get("repository")
        elif single:
            repo, iid = parse_issue_url(args.issue)
            if not repository or not REPOSITORY.fullmatch(repository):
                raise ValueError("repository_required")
            root = work = read_api(args.glab, f"projects/{quote(repo, safe='')}/issues/{iid}")
            if parse_issue_url(work.get("web_url")) != (repo, iid):
                raise ValueError("response_identity_mismatch")
        else:
            root_repo, root_iid = parse_issue_url(args.root_issue)
            work_repo, work_iid = parse_issue_url(args.work_item)
            if not repository or not REPOSITORY.fullmatch(repository) or work_repo != repository:
                raise ValueError("repository_mismatch")
            root_path = f"projects/{quote(root_repo, safe='')}/issues/{root_iid}"
            work_path = f"projects/{quote(work_repo, safe='')}/issues/{work_iid}"
            root, work = read_api(args.glab, root_path), read_api(args.glab, work_path)
            if (parse_issue_url(root.get("web_url")) != (root_repo, root_iid)
                    or parse_issue_url(work.get("web_url")) != (work_repo, work_iid)):
                raise ValueError("response_identity_mismatch")
            if (root_repo, root_iid) != (work_repo, work_iid):
                links = {"work": read_api(args.glab, work_path + "/links?per_page=100"),
                         "root": read_api(args.glab, root_path + "/links?per_page=100")}
        errors = validate_binding(root, work, links, repository, require_local_work=not single)
    except Exception:
        # Exception messages and CLI stderr can contain response bodies or credentials.
        errors = ["binding_read_or_input_failed"]
    passed = not errors
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "mode": mode,
              "verified": passed and mode == "live", "authorizes_write": False,
              "relationship_scope": "single_issue" if single else "same_issue_or_reciprocal_links_only",
              "verdict": ("VERIFIED_PRECONDITIONS" if mode == "live" else "OFFLINE_VALID")
              if passed else "BLOCKED", "root": summary(root), "work": summary(work),
              "repository": repository if isinstance(repository, str)
              and REPOSITORY.fullmatch(repository) else None, "errors": errors}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
