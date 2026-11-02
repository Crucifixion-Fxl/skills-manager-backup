"""Sanitized MR205-style GET fixtures and a GET-only fake ``glab`` for Issue receipt tests.

Only identifiers, the stable marker and labels are kept from the real comment-only
case (project 773, Issue 24, MR 205); no comment text or personal data.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib.parse import quote

HOST = "gitlab.addx.ai"
REPO = "WEB/micro_app_platform"
PROJECT_ID = 773
ISSUE_IID = 24
MR_IID = 205
HEAD = "d7665a91af44ac06a098ef567de28d5fab1d31cc"
NOTE_ID = 831015
AUTHOR_ID = 22
ISSUE_URL = f"https://{HOST}/{REPO}/-/issues/{ISSUE_IID}"
SECRET = "glpat-FAKE-SECRET-must-not-leak"

ISSUE_PATH = f"projects/{quote(REPO, safe='')}/issues/{ISSUE_IID}"
LABELS_PATH = (f"projects/{quote(REPO, safe='')}/labels?search=status%3A%3Ain-review"
               "&include_ancestor_groups=true&per_page=100")


def mr_url(mr_iid: int = MR_IID, mr_project: str = REPO) -> str:
    return f"https://{HOST}/{mr_project}/-/merge_requests/{mr_iid}"


def marker(head: str = HEAD, mr_iid: int = MR_IID, phase: str = "submitted") -> str:
    return f"<!-- gitlab-mr-issue-sync:v1:{phase}:{PROJECT_ID}:{ISSUE_IID}:{mr_iid}:{head} -->"


def body(head: str = HEAD, mr_iid: int = MR_IID, phase: str = "submitted", mr_project: str = REPO) -> str:
    return (f"{marker(head, mr_iid, phase)}\nMR {phase}: {mr_url(mr_iid, mr_project)}\n"
            f"HEAD: {head}\nNext gate: review\n")


def request(head: str = HEAD, mr_iid: int = MR_IID, mr_project: str = REPO, **changes) -> dict:
    return {"issue_url": ISSUE_URL, "phase": "submitted", "mr_project": mr_project, "mr_iid": mr_iid,
            "revision": head, "note_id": NOTE_ID, "project_mode": "canonical-label",
            "expected_body": None, **changes}


def snapshot(labels: list[str] | None = None, head: str = HEAD, mr_iid: int = MR_IID,
             mr_project: str = REPO) -> dict:
    """MR205 as observed: comment read back, Issue opened, no status label at all."""
    return {
        "user": {"id": AUTHOR_ID, "username": "author"},
        "issue": {"id": 22065, "iid": ISSUE_IID, "project_id": PROJECT_ID, "state": "opened",
                  "web_url": ISSUE_URL, "labels": [] if labels is None else labels,
                  "assignees": [{"id": AUTHOR_ID}], "updated_at": "2026-10-26T00:00:00Z"},
        "note": {"id": NOTE_ID, "body": body(head, mr_iid, mr_project=mr_project), "noteable_type": "Issue",
                 "noteable_iid": ISSUE_IID, "project_id": PROJECT_ID, "system": False, "author": {"id": AUTHOR_ID}},
        "merge_request": {"iid": mr_iid, "project_id": PROJECT_ID, "sha": head, "state": "opened",
                          "web_url": mr_url(mr_iid, mr_project), "description": f"Issue: {ISSUE_URL}\n"},
        "related_merge_requests": [{"project_id": PROJECT_ID, "iid": 204}, {"project_id": PROJECT_ID, "iid": mr_iid}],
        "project_labels": [{"name": "status::in-review"}, {"name": "status::in-progress"},
                           {"name": "status::blocked"}],
    }


def in_review(head: str = HEAD, mr_iid: int = MR_IID, mr_project: str = REPO) -> dict:
    return snapshot(["status::in-review", "type::feature"], head, mr_iid, mr_project)


def closed(head: str = HEAD, mr_iid: int = MR_IID, mr_project: str = REPO) -> dict:
    """Release-flowback: an already delivered, closed Issue natively related to the open MR."""
    snap = snapshot(["status::done", "type::feature"], head, mr_iid, mr_project)
    snap["issue"]["state"] = "closed"
    return snap


def endpoints(snap: dict) -> dict:
    """Map each live GET endpoint to its response, as glab api would return it."""
    mr_project = snap["merge_request"]["web_url"].split(f"{HOST}/", 1)[1].split("/-/", 1)[0]
    result = {
        "user": snap["user"], ISSUE_PATH: snap["issue"], f"{ISSUE_PATH}/notes/{NOTE_ID}": snap["note"],
        f"projects/{quote(mr_project, safe='')}/merge_requests/{snap['merge_request']['iid']}": snap["merge_request"],
        f"{ISSUE_PATH}/related_merge_requests?per_page=100": snap["related_merge_requests"],
    }
    if "project_labels" in snap:
        result[LABELS_PATH] = snap["project_labels"]
    return result


def reader(snap: dict):
    table = endpoints(copy.deepcopy(snap))

    def read(endpoint: str):
        from issue_review_state import ReadFailed

        if endpoint not in table:
            raise ReadFailed("read_failed")
        return table[endpoint]
    return read


FAKE_GLAB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if "--hostname" not in args and os.environ.get("FAKE_GLAB_FALLBACK"):
    os.execv(os.environ["FAKE_GLAB_FALLBACK"], [os.environ["FAKE_GLAB_FALLBACK"], *args])
if args[:1] != ["api"] or "--method" not in args or args[args.index("--method") + 1] != "GET":
    print("refusing non-GET call", file=sys.stderr)
    sys.exit(3)
if args[args.index("--hostname") + 1] != "gitlab.addx.ai":
    sys.exit(4)
endpoint = args[-1]
with open(os.environ["FAKE_GLAB_FIXTURE"], encoding="utf-8") as source:
    table = json.load(source)
with open(os.environ["FAKE_GLAB_FIXTURE"] + ".calls", "a", encoding="utf-8") as log:
    log.write(endpoint + "\n")
if endpoint not in table:
    print("401 Unauthorized PRIVATE-TOKEN=%s" % os.environ.get("FAKE_GLAB_SECRET", ""), file=sys.stderr)
    sys.exit(1)
print(json.dumps(table[endpoint]))
'''


def install_fake_glab(directory: Path, snap: dict | None, env: dict | None = None,
                      fallback: Path | None = None) -> dict:
    """Put a GET-only fake glab first on PATH; returns the subprocess environment.

    ``fallback`` hands other tests' fake ``glab api <endpoint>`` calls to their own fake.
    """
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "glab"
    binary.write_text(FAKE_GLAB, encoding="utf-8")
    binary.chmod(0o755)
    fixture = directory / "fixture.json"
    fixture.write_text(json.dumps(endpoints(snap) if snap is not None else {}), encoding="utf-8")
    base = dict(env if env is not None else os.environ)
    return {**base, "PATH": f"{directory}{os.pathsep}{base.get('PATH', '')}",
            "FAKE_GLAB_FIXTURE": str(fixture), "FAKE_GLAB_SECRET": SECRET,
            **({"FAKE_GLAB_FALLBACK": str(fallback)} if fallback else {})}


def verified_receipt(head: str = HEAD, mr_iid: int = MR_IID, mr_project: str = REPO) -> dict:
    """A receipt exactly as the live verifier produces for the in-review fixture."""
    from issue_review_state import verify

    return verify(request(head, mr_iid, mr_project), reader=reader(in_review(head, mr_iid, mr_project)),
                  host=HOST)


def admission(role: str = "closed-reference", head: str = HEAD, mr_iid: int = MR_IID, snap: dict | None = None,
              history: list | None = None, mr_project: str = REPO) -> dict:
    """An admission record exactly as record_issue_binding.py produces it from a live GET set."""
    from issue_binding import produce

    request = {"issue_url": ISSUE_URL, "mr_project": mr_project, "mr_iid": mr_iid, "revision": head,
               "binding_role": role}
    source = snap if snap is not None else (closed if role == "closed-reference" else in_review)(head, mr_iid,
                                                                                               mr_project)
    return produce("admit", request, history or [], reader=reader(source), host=HOST)


def retirement(head: str = HEAD, mr_iid: int = MR_IID, snap: dict | None = None, history: list | None = None,
               reason: str = "MR no longer delivers this Issue", mr_project: str = REPO) -> dict:
    from issue_binding import produce

    request = {"issue_url": ISSUE_URL, "mr_project": mr_project, "mr_iid": mr_iid, "revision": head,
               "reason": reason}
    return produce("retire", request, history or [], reader=reader(snap if snap is not None else undeclared(
        head, mr_iid, mr_project)), host=HOST)


def undeclared(head: str = HEAD, mr_iid: int = MR_IID, mr_project: str = REPO) -> dict:
    """The MR description no longer declares the Issue; the historical cross-reference relation remains."""
    snap = in_review(head, mr_iid, mr_project)
    snap["merge_request"]["description"] = "Issue: https://gitlab.addx.ai/WEB/micro_app_platform/-/issues/23\n"
    return snap


def binding(record: dict) -> dict:
    """The verifier binding argument resolved from one admission record."""
    from issue_binding import binding_for, issue_key, resolve

    return binding_for(resolve([record], record["mr_iid"], record["mr_project"], HOST)[issue_key(ISSUE_URL, HOST)])
