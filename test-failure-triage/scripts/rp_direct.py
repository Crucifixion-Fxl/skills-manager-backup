#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Augment failed platform Scenarios with RP data through the isolated read proxy."""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from collect_failures import CollectionError, NoRedirect, redact, write_private

PROJECT = "builder_prod_cn"


class ReadProxy:
    def __init__(self, url, token):
        if url.rstrip("/") != "http://127.0.0.1:9374" or not token:
            raise CollectionError("configure the operator-owned loopback RP read proxy")
        self.url, self.token = url.rstrip("/"), token
        self.opener = urllib.request.build_opener(NoRedirect)

    def get(self, path, params=None):
        url = self.url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
        try:
            with self.opener.open(req, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise CollectionError(f"RP read proxy HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise CollectionError("RP read proxy unavailable or invalid JSON") from None


def pages(client, kind, params, max_pages=30, size=500):
    records, seen = [], set()
    for page in range(1, max_pages + 1):
        value = client.get(f"/api/v1/{PROJECT}/{kind}", {**params, "page.page": page, "page.size": size})
        batch = [v for v in value.get("content", []) if str(v["id"]) not in seen]
        records.extend(batch)
        seen.update(str(v["id"]) for v in batch)
        meta = value.get("page", {})
        if meta.get("totalElements") is not None and len(seen) >= meta["totalElements"]:
            return records, True
        if not batch:
            return records, False
    return records, False


def augment(bundle, client, max_pages=30, max_incidents=20):
    cache = {}
    incidents = bundle.get("incidents", [])
    bundle["coverage"]["diagnostic_incidents_total"] = len(incidents)
    bundle["coverage"]["diagnostic_incidents_budget"] = max_incidents
    bundle["coverage"]["diagnostic_incidents_obtained"] = 0
    for index, incident in enumerate(incidents):
        if index >= max_incidents:
            bundle["coverage"]["sampled"] = True
            incident["missing_evidence"].append("direct RP: detailed incident budget exhausted")
            continue
        job, scenario = incident["job"], incident.get("scenario")
        if not scenario or not scenario.get("reportPortalUuid") or not job.get("reportPortalUuid"):
            incident["missing_evidence"].append("direct RP: exact launch/Scenario UUID unavailable")
            continue
        try:
            key = job["reportPortalUuid"]
            if key not in cache:
                launches, launch_complete = pages(client, "launch", {"filter.eq.uuid": key}, max_pages)
                exact = [x for x in launches if x.get("uuid") == key]
                if not launch_complete or len(exact) != 1:
                    raise CollectionError("direct RP: launch UUID mapping missing or ambiguous")
                launch = exact[0]
                items, complete = pages(client, "item", {"filter.eq.launchId": launch["id"]}, max_pages)
                cache[key] = (launch, items, complete)
            launch, items, complete = cache[key]
            matches = [x for x in items if x.get("uuid") == scenario["reportPortalUuid"]]
            if len(matches) != 1:
                raise CollectionError("direct RP: Scenario UUID mapping missing or ambiguous")
            root = matches[0]
            prefix = str(root.get("path") or root["id"]) + "."
            children = [x for x in items if x["id"] == root["id"] or str(x.get("path", "")).startswith(prefix)]
            logs, all_logs = [], complete
            for item in children:
                entries, done = pages(client, "log", {"filter.eq.item": item["id"]}, max_pages)
                logs.extend(entries)
                all_logs = all_logs and done
            logs = sorted({str(x["id"]): x for x in logs}.values(), key=lambda x: (x.get("time", ""), x["id"]))
            attachments = []
            for entry in logs:
                binary = entry.get("binaryContent")
                if binary and binary.get("id"):
                    attachments.append({"logId": entry["id"], "time": entry.get("time"),
                        "message": entry.get("message"), "contentType": binary.get("contentType"),
                        "source": "rp_read_proxy", "contentPath": f"/api/v1/data/{PROJECT}/{binary['id']}"})
            incident["logs"] = logs
            bundle["coverage"]["diagnostic_incidents_obtained"] += 1
            incident["evidence"] = {"state": "AVAILABLE" if all_logs else "PARTIAL", "truncated": not all_logs,
                                    "attachments": attachments, "source": "rp_read_proxy"}
            incident["report_portal"] = {"launchId": launch["id"], "launchUuid": key,
                "itemId": root["id"], "itemUuid": root["uuid"], "status": root.get("status"),
                "source": "rp_read_proxy", "url": job.get("reportPortalUrl")}
            # Preserve structured DEV_DIAG_EVENT records in logs. Do not pretend we parsed all attempts.
            incident["direct_rp_observation"] = {"item_started_at": root.get("startTime"),
                "item_completed_at": root.get("endTime"), "items_complete": complete,
                "logs_complete": all_logs, "raw_timeline_events": sum(
                    "DEV_DIAG_EVENT " in str(x.get("message", "")) for x in logs)}
            if not all_logs:
                incident["missing_evidence"].append("direct RP: paging incomplete")
                bundle["coverage"]["logs_truncated"] = True
        except CollectionError as exc:
            incident["missing_evidence"].append(str(exc))
    return redact(bundle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-incidents", type=int, default=20)
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_incidents < 1:
        parser.error("positive paging budget required")
    client = ReadProxy(os.environ.get("RP_READ_PROXY_URL", ""), os.environ.get("RP_READ_PROXY_TOKEN"))
    bundle = augment(json.loads(args.input.read_text()), client, args.max_pages, args.max_incidents)
    write_private(args.output, bundle)
    print(json.dumps({"incidents": len(bundle["incidents"]), "output": str(args.output)}))


if __name__ == "__main__":
    try:
        main()
    except CollectionError as exc:
        raise SystemExit(str(exc))
