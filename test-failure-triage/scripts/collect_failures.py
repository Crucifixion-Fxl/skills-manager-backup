#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Read Device Cloud + RP diagnostics without executing or changing tests."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from platform_api import (JOBS, JOB, PLAN, REPORT, LOGS, EVIDENCE, TIMELINE, FAILED,
    CollectionError, NoRedirect, Platform, redact, safe_metadata, timestamp, iso, in_window)

def collect_evidence(client, job, item_uuid, timeline, logs, max_windows=24):
    times = [timestamp(a.get(k)) for a in (timeline or {}).get("attempts", [])
             for k in ["startedAt", "completedAt"]]
    times = [t for t in times if t] or [timestamp(x.get("time")) for x in logs if x.get("time")]
    times = [t for t in times if t] or [timestamp(job.get(k)) for k in ["startedAt", "completedAt"]]
    times = [t for t in times if t]
    if not times:
        raise CollectionError("evidence requires observed timestamps; no safe time window available")
    start, end = min(times), max(times)
    seen, attachments, partial = set(), [], False
    windows = 0
    while start <= end and windows < max_windows:
        stop = min(start + dt.timedelta(hours=1), end)
        result = client.query(EVIDENCE, {"job": str(job["id"]), "item": item_uuid,
            "from": iso(start), "to": iso(stop)})["jobDiagnosticEvidence"]
        partial = partial or result.get("state") != "AVAILABLE" or bool(result.get("truncated"))
        for attachment in result.get("attachments", []):
            key = str(attachment["logId"])
            if key not in seen:
                seen.add(key)
                attachments.append(attachment)
        windows += 1
        if stop == end:
            return {"state": "PARTIAL" if partial else "AVAILABLE", "truncated": partial,
                    "attachments": attachments, "time_windows": windows}
        start = stop
    return {"state": "PARTIAL", "truncated": True, "attachments": attachments, "time_windows": windows}

def collect(client, start, end, *, max_pages=100, page_size=200, max_log_pages=10, job_ids=None):
    coverage = {"jobs_complete": False, "plans_complete": True,
                "recovered_attempts_complete": False, "logs_truncated": False, "sampled": False,
                "missing_evidence": [], "listed_jobs": 0, "listed_total": None}
    listed, seen = [], set()
    if job_ids is not None:
        coverage["sampled"] = True
        coverage["missing_evidence"].append("explicit Job sample; no global completeness claim")
        for job_id in sorted(job_ids):
            job = client.query(JOB, {"id": str(job_id)})["job"]
            if job:
                listed.append(job)
            else:
                coverage["missing_evidence"].append(f"job:{job_id} missing")
    for page in ([] if job_ids is not None else range(1, max_pages + 1)):
        data = client.query(JOBS, {"page": page, "size": page_size})["jobs"]
        coverage["listed_total"] = data["total"]
        records = data.get("records") or []
        new = [j for j in records if str(j["id"]) not in seen]
        for job in new:
            seen.add(str(job["id"]))
            if (job_ids is None and in_window(job, start, end)) or (
                    job_ids is not None and str(job["id"]) in job_ids):
                listed.append(job)
        coverage["listed_jobs"] = len(seen)
        if len(seen) >= data["total"]:
            coverage["jobs_complete"] = True
            break
        if not new:
            coverage["missing_evidence"].append("job pagination did not advance")
            break
    if job_ids is None and not coverage["jobs_complete"]:
        coverage["missing_evidence"].append("job listing incomplete; unobserved failures may exist")
    plans, incidents = {}, []
    for job in listed:
        if "features" not in job:
            try:
                job = client.query(JOB, {"id": str(job["id"])})["job"] or job
            except CollectionError:
                coverage["missing_evidence"].append(f"job:{job['id']} detail unavailable")
                coverage["jobs_complete"] = False
        plan_id = job.get("testPlanId")
        if plan_id and str(plan_id) not in plans:
            try:
                plan = client.query(PLAN, {"id": str(plan_id)})["testPlan"]
                if plan:
                    plan["metadata"] = safe_metadata(plan.get("metadata"))
                else:
                    coverage["plans_complete"] = False
                    coverage["missing_evidence"].append(f"plan:{plan_id} missing")
                plans[str(plan_id)] = plan
            except CollectionError as exc:
                coverage["plans_complete"] = False
                coverage["missing_evidence"].append(f"plan:{plan_id} {exc}")
        failures = []
        for feature in job.get("features") or []:
            for scenario in feature.get("scenarios") or []:
                if str(scenario.get("status", "")).lower() in FAILED:
                    failures.append((feature, scenario))
        job_failed = str(job.get("status", "")).lower().startswith(("failed", "aborted"))
        if not failures and not job_failed:
            continue
        safe_job = {k: v for k, v in job.items() if k not in {"features", "metadata"}}
        safe_job["metadata"] = safe_metadata(job.get("metadata"))
        report = None
        report_error = None
        try:
            report = client.query(REPORT, {"job": str(job["id"])})["jobDiagnosticReport"]
        except CollectionError as exc:
            report_error = str(exc)
        if not failures:
            failures = [(None, None)]  # Pre-Scenario resource/bootstrap failure still matters.
        for feature, scenario in failures:
            missing = []
            if report_error:
                missing.append(report_error)
            if report and report.get("state") != "AVAILABLE":
                missing.append("RP diagnostic report state=" + str(report.get("state")))
            item_uuid = None
            if report and scenario:
                for rf in report.get("features") or []:
                    for rs in rf.get("scenarios") or []:
                        if str(rs["id"]) == str(scenario["id"]):
                            item_uuid = rs.get("reportPortalItemUuid")
            logs, evidence, timeline = [], None, None
            if scenario:
                try:
                    timeline = client.query(TIMELINE, {"job": str(job["id"]),
                        "scenario": str(scenario["id"])})["jobDiagnosticTimeline"]
                except CollectionError as exc:
                    missing.append(str(exc))
            if item_uuid:
                cursor, cursors = None, set()
                for log_page in range(max_log_pages):
                    try:
                        ld = client.query(LOGS, {"job": str(job["id"]), "item": item_uuid,
                            "after": cursor, "size": 100})["jobDiagnosticLogs"]
                    except CollectionError as exc:
                        missing.append(str(exc))
                        break
                    logs.extend(ld.get("entries") or [])
                    if ld.get("truncated"):
                        coverage["logs_truncated"] = True
                        missing.append("RP log provider truncated data")
                    if not ld.get("hasNextPage"):
                        break
                    cursor = ld.get("endCursor")
                    if not cursor or cursor in cursors or log_page == max_log_pages - 1:
                        coverage["logs_truncated"] = True
                        missing.append("RP log paging incomplete")
                        break
                    cursors.add(cursor)
                try:
                    evidence = collect_evidence(client, job, item_uuid, timeline, logs)
                except CollectionError as exc:
                    missing.append(str(exc))
            else:
                missing.append("no mapped RP Scenario item; no logs or attachments obtained")
            if scenario:
                scenario = dict(scenario)
                scenario["metadata"] = safe_metadata(scenario.get("metadata"))
            for label, value in [("evidence", evidence), ("timeline", timeline)]:
                if value and (value.get("state") != "AVAILABLE" or value.get("truncated")):
                    missing.append(f"{label} partial: {value.get('state')}; truncated={bool(value.get('truncated'))}")
            incidents.append({"job": safe_job, "feature": None if not feature else
                {k: feature.get(k) for k in ["id", "name", "filename", "status"]},
                "scenario": scenario, "report_portal": None if not report else report.get("reportPortal"),
                "runtime": None if not report else report.get("job"),
                "logs": logs, "evidence": evidence, "timeline": timeline, "missing_evidence": missing})
    return redact({"schema": "test-failure-evidence/v1", "window": {"from": iso(start), "to": iso(end)},
                   "coverage": coverage, "plans": plans, "incidents": incidents})

def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as file:
        os.fchmod(file.fileno(), 0o600)
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("DEVICE_CLOUD_GRAPHQL_URL"))
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--job-id", action="append")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-log-pages", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    end = timestamp(args.end) if args.end else dt.datetime.now(dt.timezone.utc)
    if not end:
        parser.error("--to must be an ISO timestamp")
    start = timestamp(args.start) if args.start else end - dt.timedelta(days=7)
    if not start or not end or start >= end or args.max_pages < 1 or args.max_log_pages < 1:
        parser.error("provide a valid window and positive paging budgets")
    if not args.endpoint:
        parser.error("DEVICE_CLOUD_GRAPHQL_URL / --endpoint is required")
    client = Platform(args.endpoint, os.environ.get("DEVICE_CLOUD_READ_TOKEN"))
    value = collect(client, start, end, max_pages=args.max_pages,
        max_log_pages=args.max_log_pages, job_ids=None if not args.job_id else set(args.job_id))
    write_private(args.output, value)
    print(json.dumps({"output": str(args.output), "incidents": len(value["incidents"]),
                      "coverage": value["coverage"]}, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except CollectionError as error:
        raise SystemExit(str(error))
