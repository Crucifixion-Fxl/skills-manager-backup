#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Download bounded RP evidence via the authenticated Device Cloud proxy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from collect_failures import CollectionError, NoRedirect, write_private

CONTENT_PATH = re.compile(r"^/api/test/jobs/([0-9]+)/evidence/logs/([0-9]+)/content$")
RP_PATH = re.compile(r"^/api/v1/data/builder_prod_cn/[A-Za-z0-9_.-]{1,256}$")
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "application/xml": ".xml",
              "text/xml": ".xml", "text/plain": ".txt", "application/json": ".json"}


def download(base, token, job_id, attachment, directory, *, max_bytes=20*1024*1024, opener=None):
    base_url = urllib.parse.urlparse(base)
    if not str(job_id).isdigit():
        raise CollectionError("selected Job ID must be numeric")
    direct = attachment.get("source") == "rp_read_proxy"
    if direct:
        if base.rstrip("/") != "http://127.0.0.1:9374" or not RP_PATH.fullmatch(attachment.get("contentPath", "")):
            raise CollectionError("attachment does not match the fixed RP read proxy and project")
        if not str(attachment.get("logId", "")).isdigit():
            raise CollectionError("attachment log ID must be numeric")
        log_id = str(attachment["logId"])
    elif base_url.scheme != "https" or not base_url.hostname or base_url.username or base_url.password:
        raise CollectionError("configure an HTTPS platform endpoint")
    if not direct:
        match = CONTENT_PATH.fullmatch(attachment.get("contentPath", ""))
        if not match or match[1] != str(job_id) or match[2] != str(attachment.get("logId")):
            raise CollectionError("attachment path does not match the selected Job and log")
        log_id = match[2]
    if not token:
        raise CollectionError("DEVICE_CLOUD_READ_TOKEN is missing")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise CollectionError("evidence directory must not be a symlink")
    os.chmod(directory, 0o700)
    url = urllib.parse.urlunparse((base_url.scheme, base_url.netloc, attachment["contentPath"], "", "", ""))
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    opener = opener or urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            mime = response.headers.get("Content-Type", "").split(";")[0].lower()
            payload = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise CollectionError(f"evidence HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise CollectionError("evidence endpoint unavailable") from None
    if len(payload) > max_bytes:
        raise CollectionError("evidence exceeds per-file download budget")
    if not payload:
        raise CollectionError("evidence response is empty")
    destination = directory / (f"job-{job_id}-log-{log_id}" + EXTENSIONS.get(mime, ".bin"))
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(payload)
    return {"evidence_id": f"attachment:{job_id}/{log_id}", "path": str(destination),
            "bytes": len(payload), "content_type": mime, "sha256": hashlib.sha256(payload).hexdigest(),
            "inspection": "not_inspected"}


def fetch(bundle, base, token, directory, *, max_files=40, max_bytes=20*1024*1024, downloader=download):
    files, missing, seen = [], [], set()
    for incident in bundle.get("incidents", []):
        job_id = str(incident["job"]["id"])
        evidence = incident.get("evidence")
        if not evidence or evidence.get("state") != "AVAILABLE" or evidence.get("truncated"):
            missing.append({"job_id": job_id, "reason": "upstream evidence missing or partial"})
        for attachment in (evidence or {}).get("attachments", []):
            key = (job_id, str(attachment.get("logId")))
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > max_files:
                missing.append({"job_id": job_id, "log_id": key[1], "reason": "file budget exhausted"})
                continue
            try:
                files.append(downloader(base, token, job_id, attachment, directory, max_bytes=max_bytes))
            except (CollectionError, OSError) as exc:
                reason = str(exc) if isinstance(exc, CollectionError) else "local evidence write failed"
                missing.append({"job_id": job_id, "log_id": key[1], "reason": reason})
    return {"schema": "test-failure-media/v1", "files": files, "missing_evidence": missing,
            "complete": not missing, "max_files": max_files, "max_file_bytes": max_bytes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--endpoint", default=os.environ.get("DEVICE_CLOUD_GRAPHQL_URL"))
    parser.add_argument("--rp-proxy", action="store_true", help="use the isolated GET-only RP proxy")
    parser.add_argument("--max-files", type=int, default=40)
    parser.add_argument("--max-file-bytes", type=int, default=20*1024*1024)
    args = parser.parse_args()
    if args.rp_proxy:
        args.endpoint = os.environ.get("RP_READ_PROXY_URL")
    if not args.endpoint or args.max_files < 1 or args.max_file_bytes < 1:
        parser.error("endpoint and positive download budgets are required")
    result = fetch(json.loads(args.input.read_text()), args.endpoint,
                   os.environ.get("RP_READ_PROXY_TOKEN" if args.rp_proxy else "DEVICE_CLOUD_READ_TOKEN"), args.output_dir,
                   max_files=args.max_files, max_bytes=args.max_file_bytes)
    write_private(args.output_dir / "manifest.json", result)
    print(json.dumps({"files": len(result["files"]), "complete": result["complete"],
                      "manifest": str(args.output_dir / "manifest.json")}))


if __name__ == "__main__":
    try:
        main()
    except CollectionError as error:
        raise SystemExit(str(error))
