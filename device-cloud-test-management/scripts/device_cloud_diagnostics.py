#!/usr/bin/env python3
"""Read Device Cloud diagnostics with the same authenticated Server APIs as the UI.

This script never accepts or reads ReportPortal credentials.  It performs the
existing Casdoor/Feishu CLI login and sends the resulting user Bearer only to
Device Cloud Server.  Attachments are downloaded only on an explicit command.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Protocol

from device_cloud_auth import DeviceCloudAuthClient, server_base_url


ENV_ENDPOINTS = {
    "prod-cn": "https://device-cloud-server.builder.addx.live/graphql",
    "staging-cn": "https://device-cloud-server-staging.builder.addx.live/graphql",
}
EVIDENCE_PATH = re.compile(
    r"^/api/test/jobs/(?P<job_id>\d+)/evidence/logs/(?P<log_id>\d+)/content$"
)
PREVIEW_RANGE = "bytes=0-1048575"
TERMINAL_JOB_STATUSES = {
    "COMPLETE", "COMPLETED", "PASSED", "FAILED", "CANCELLED", "CANCELED",
    "ABORTED", "ERROR", "FAILED_CLIENT_EXIT", "FAILED_RESOURCE_NOT_AVAILABLE",
    "FAILED_RESOURCE_DISCONNECT", "FAILED_RESOURCE_ALLOCATION", "FAILED_JOB_WS_DISCONNECT",
}
SUCCESS_STATUSES = {"COMPLETE", "COMPLETED", "PASSED", "SUCCESS", "SKIPPED"}


def _terminal_job_status(value: Any) -> bool:
    status = str(value or "").upper()
    return status in TERMINAL_JOB_STATUSES or status.startswith("FAILED_") or status.startswith("ABORTED_")


class DiagnosticError(RuntimeError):
    """A safe user-facing diagnostic request error."""


class DiagnosticHttpError(DiagnosticError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Device Cloud returned HTTP {status_code}")
        self.status_code = status_code


class AuthSession(Protocol):
    @property
    def access_token(self) -> str: ...
    def refresh(self) -> Any: ...


class DiagnosticTransport(Protocol):
    def graphql(
        self, endpoint: str, token: str, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]: ...

    def download(
        self,
        url: str,
        token: str,
        destination: Path,
        byte_range: str | None,
    ) -> dict[str, Any]: ...


class UrllibDiagnosticTransport:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def graphql(
        self, endpoint: str, token: str, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise DiagnosticHttpError(error.code) from None
        except urllib.error.URLError as error:
            raise DiagnosticError("Device Cloud is unavailable") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DiagnosticError("Device Cloud returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise DiagnosticError("Device Cloud returned a non-object response")
        return payload

    def download(
        self,
        url: str,
        token: str,
        destination: Path,
        byte_range: str | None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "*/*"}
        if byte_range:
            headers["Range"] = byte_range
        request = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                destination.parent.mkdir(parents=True, exist_ok=True)
                total = 0
                with destination.open("wb") as output:
                    while chunk := response.read(64 * 1024):
                        output.write(chunk)
                        total += len(chunk)
                return {
                    "path": str(destination.resolve()),
                    "bytes": total,
                    "contentType": response.headers.get_content_type(),
                    "contentRange": response.headers.get("Content-Range"),
                }
        except urllib.error.HTTPError as error:
            raise DiagnosticHttpError(error.code) from None
        except urllib.error.URLError as error:
            raise DiagnosticError("Device Cloud evidence endpoint is unavailable") from error


REPORT_QUERY = """
query JobDiagnosticReport($jobId: ID!) {
  jobDiagnosticReport(jobId: $jobId) {
    state message
    job { id name status testPlanId startedAt completedAt
      application { platform packageName version commit environment builtAt }
      resources { kind name serialNumber platform cabinetName }
    }
    reportPortal { launchUuid launchId status startedAt completedAt url }
    features { id name filename serverStatus reportPortalStatus reportPortalItemUuid
      reportPortalItemId startedAt completedAt background { name steps }
      scenarios { id uid name line serverStatus errorMessage reportPortalStatus
        reportPortalItemUuid reportPortalItemId startedAt completedAt
        definition { name steps }
      }
    }
  }
}
"""

EVIDENCE_QUERY = """
query JobDiagnosticEvidence($jobId: ID!, $itemUuid: String!, $from: DateTime, $to: DateTime) {
  jobDiagnosticEvidence(jobId: $jobId, itemUuid: $itemUuid, from: $from, to: $to) {
    state message itemUuid truncated
    attachments { logId time level message contentType kind contentPath }
  }
}
"""

LOGS_QUERY = """
query JobDiagnosticLogs($jobId: ID!, $itemUuid: String!, $level: String,
  $source: String, $from: DateTime, $to: DateTime, $after: String, $first: Int) {
  jobDiagnosticLogs(jobId: $jobId, itemUuid: $itemUuid, level: $level,
    source: $source, from: $from, to: $to, after: $after, first: $first) {
    entries { id time level source message hasAttachment contentType }
    endCursor hasNextPage totalCount
  }
}
"""

TIMELINE_QUERY = """
query JobDiagnosticTimeline($jobId: ID!, $scenarioId: ID!) {
  jobDiagnosticTimeline(jobId: $jobId, scenarioId: $scenarioId) {
    state message scenarioId itemUuid terminal truncated eventCount warnings
    diagnosticsMode degradationReasons
    attempts { attemptId attemptIndex status startedOffsetMs completedOffsetMs durationMs
      startedAt completedAt
      recordings { recordingId device status startedOffsetMs captureDurationMs attachmentLogId
        contentType contentPath legacySingleRecording diagnosticsStatus
        failure { stage code retryable } failures { stage code retryable }
      }
      steps { attemptId definitionId parentAttemptId depth scope line keyword name attemptIndex
        status startedOffsetMs completedOffsetMs durationMs executionCompletedOffsetMs
        executionDurationMs startedAt completedAt actionCount
        mediaRanges { recordingId device startedVideoOffsetMs finishedVideoOffsetMs startedPtsUs
          finishedPtsUs syncQuality evidenceTrust warningCode }
        actions { actionId recordingId device kind status recovered recoveryStrategy durationMs
          retryCount videoOffsetMs dispatchVideoOffsetMs completedVideoOffsetMs effectVideoOffsetMs
          dispatchPtsUs completedPtsUs visualPtsUs clockSource syncUncertaintyMs syncQuality
          markerSource evidenceTrust warningCode commandAttemptId seekPhase orientation
          viewport { width height }
          geometry { point { x y } path { x y } elementBounds { x y width height } }
        }
      }
    }
  }
}
"""

JOB_STATE_QUERY = """
query DeviceCloudJobState($id: ID!) {
  job(id: $id) { id name status errorMessage testPlanId totalScenarios passedScenarios }
}
"""

PLAN_JOBS_QUERY = """
query DeviceCloudPlanJobs($id: ID!) {
  testPlan(id: $id) { id name status jobs { id name status errorMessage } }
}
"""

CANCEL_JOB_MUTATION = """
mutation DeviceCloudCancelJob($id: ID!) {
  cancelJob(id: $id, cancelSource: WEB_CANCEL)
}
"""


class DeviceCloudDiagnosticsClient:
    def __init__(
        self,
        graphql_endpoint: str,
        auth: AuthSession,
        *,
        transport: DiagnosticTransport | None = None,
    ) -> None:
        self.graphql_endpoint = graphql_endpoint
        self.base_url = server_base_url(graphql_endpoint)
        self.auth = auth
        self.transport = transport or UrllibDiagnosticTransport()

    def _graphql_value(
        self,
        query: str,
        variables: dict[str, Any],
        result_key: str,
    ) -> Any:
        payload: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                payload = self.transport.graphql(
                    self.graphql_endpoint,
                    self.auth.access_token,
                    query,
                    variables,
                )
                break
            except DiagnosticHttpError as error:
                if error.status_code == 401 and attempt == 0:
                    self.auth.refresh()
                    continue
                raise
        assert payload is not None
        errors = payload.get("errors")
        if errors:
            messages = [str(item.get("message", "Diagnostic request failed")) for item in errors]
            raise DiagnosticError("; ".join(messages))
        data = payload.get("data")
        if not isinstance(data, dict) or result_key not in data:
            raise DiagnosticError("Device Cloud returned an incomplete diagnostic response")
        return data[result_key]

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any],
        result_key: str,
    ) -> dict[str, Any]:
        value = self._graphql_value(query, variables, result_key)
        if not isinstance(value, dict):
            raise DiagnosticError("Device Cloud returned an invalid diagnostic object")
        return value

    def job_report(self, job_id: int) -> dict[str, Any]:
        return self._graphql(REPORT_QUERY, {"jobId": str(job_id)}, "jobDiagnosticReport")

    def timeline(self, job_id: int, scenario_id: int) -> dict[str, Any]:
        return self._graphql(
            TIMELINE_QUERY,
            {"jobId": str(job_id), "scenarioId": str(scenario_id)},
            "jobDiagnosticTimeline",
        )

    def evidence(
        self,
        job_id: int,
        item_uuid: str,
        *,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> dict[str, Any]:
        return self._graphql(
            EVIDENCE_QUERY,
            {"jobId": str(job_id), "itemUuid": item_uuid, "from": from_time, "to": to_time},
            "jobDiagnosticEvidence",
        )

    def logs(
        self,
        job_id: int,
        item_uuid: str,
        *,
        level: str | None = None,
        source: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        after: str | None = None,
        first: int = 100,
    ) -> dict[str, Any]:
        if first < 1 or first > 100:
            raise ValueError("first must be between 1 and 100")
        return self._graphql(
            LOGS_QUERY,
            {
                "jobId": str(job_id), "itemUuid": item_uuid, "level": level,
                "source": source, "from": from_time, "to": to_time,
                "after": after, "first": first,
            },
            "jobDiagnosticLogs",
        )

    def download_evidence(
        self,
        job_id: int,
        content_path: str,
        destination: Path,
        *,
        preview: bool = False,
    ) -> dict[str, Any]:
        match = EVIDENCE_PATH.fullmatch(content_path)
        if match is None or int(match.group("job_id")) != job_id:
            raise ValueError("invalid or cross-job diagnostic evidence path")
        if not destination.name or destination.name in {".", ".."}:
            raise ValueError("destination must be a file path")
        url = urllib.parse.urljoin(self.base_url + "/", content_path.lstrip("/"))
        for attempt in range(2):
            try:
                return self.transport.download(
                    url,
                    self.auth.access_token,
                    destination,
                    PREVIEW_RANGE if preview else None,
                )
            except DiagnosticHttpError as error:
                if error.status_code == 401 and attempt == 0:
                    self.auth.refresh()
                    continue
                raise
        raise AssertionError("unreachable")

    def job_state(self, job_id: int) -> dict[str, Any]:
        return self._graphql(JOB_STATE_QUERY, {"id": str(job_id)}, "job")

    def plan_jobs(self, plan_id: int) -> dict[str, Any]:
        return self._graphql(PLAN_JOBS_QUERY, {"id": str(plan_id)}, "testPlan")

    def cancel_job(self, job_id: int, *, timeout_seconds: int = 60) -> dict[str, Any]:
        before = self.job_state(job_id)
        if not _terminal_job_status(before.get("status")):
            accepted = self._graphql_value(
                CANCEL_JOB_MUTATION, {"id": str(job_id)}, "cancelJob"
            )
            if accepted is not True:
                raise DiagnosticError(f"cancelJob({job_id}) was not accepted")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() <= deadline:
            current = self.job_state(job_id)
            if _terminal_job_status(current.get("status")):
                return {
                    "target": "job", "jobId": job_id, "accepted": True,
                    "beforeStatus": before.get("status"),
                    "finalStatus": current.get("status"), "verifiedTerminal": True,
                }
            time.sleep(2)
        raise DiagnosticError(
            f"job {job_id} did not reach a terminal state within {timeout_seconds}s"
        )

    def cancel_plan(self, plan_id: int, *, timeout_seconds: int = 60) -> dict[str, Any]:
        plan = self.plan_jobs(plan_id)
        jobs = plan.get("jobs") or []
        if not isinstance(jobs, list):
            raise DiagnosticError("Device Cloud returned invalid plan jobs")
        results = []
        for job in jobs:
            if not isinstance(job, dict) or job.get("id") is None:
                continue
            results.append(self.cancel_job(int(job["id"]), timeout_seconds=timeout_seconds))
        verified = self.plan_jobs(plan_id)
        remaining = [
            job for job in (verified.get("jobs") or [])
            if not _terminal_job_status(job.get("status"))
        ]
        if remaining:
            raise DiagnosticError(f"plan {plan_id} still has non-terminal jobs")
        return {
            "target": "plan", "planId": plan_id, "planName": plan.get("name"),
            "jobs": results, "verifiedTerminal": True,
            "note": "平台没有 cancelTestPlan mutation；本命令取消并核验计划内全部 Job，不删除计划记录。",
        }


def _failed(value: Any) -> bool:
    status = str(value or "").upper()
    return bool(status) and status not in SUCCESS_STATUSES and status not in {"UNTESTED", "RUNNING"}


def _first_scenario(report: dict[str, Any]) -> dict[str, Any] | None:
    first: dict[str, Any] | None = None
    for feature in report.get("features") or []:
        for scenario in feature.get("scenarios") or []:
            if first is None:
                first = scenario
            if scenario.get("errorMessage") or _failed(scenario.get("serverStatus")) or _failed(
                scenario.get("reportPortalStatus")
            ):
                return scenario
    return first


def _first_failed_step(timeline: dict[str, Any] | None) -> dict[str, Any] | None:
    if not timeline:
        return None
    for attempt in timeline.get("attempts") or []:
        for step in attempt.get("steps") or []:
            if _failed(step.get("status")):
                return {
                    "line": step.get("line"), "keyword": step.get("keyword"),
                    "name": step.get("name"), "status": step.get("status"),
                    "startedAt": step.get("startedAt"), "completedAt": step.get("completedAt"),
                }
    return None


def _failure_classification(
    reason: str | None,
    job_status: str,
    resources: list[dict[str, Any]],
) -> str:
    if not reason and str(job_status or "").upper() in SUCCESS_STATUSES:
        return "无失败"
    text = f"{reason} {job_status}".casefold()
    if any(word in text for word in ("resource", "资源分配", "not available", "allocation")):
        return "资源申请错误"
    if any(word in text for word in ("element", "元素", "navigate", "导航", "not found", "找不到")):
        return "元素/导航不兼容"
    if any(word in text for word in ("outdated", "过时", "step undefined", "未发现模块", "no matching")):
        return "用例脚本过时"
    if any(word in text for word in ("assert", "断言", "expected", "scenario 未全通过")):
        return "业务断言失败"
    if any(word in text for word in ("timeout", "disconnect", "websocket", "client exit", "server-opt")):
        return "平台或基础设施失败"
    if resources and any(str(item.get("kind") or "").upper() == "PHONE" for item in resources):
        return "产品功能失败"
    return "平台或基础设施失败"


def _extension(attachment: dict[str, Any]) -> str:
    kind = str(attachment.get("kind") or "").upper()
    if kind == "XML":
        return ".xml"
    if kind == "VIDEO":
        return ".mp4"
    content_type = str(attachment.get("contentType") or "").partition(";")[0]
    return mimetypes.guess_extension(content_type) or {
        "IMAGE": ".png", "XML": ".xml", "VIDEO": ".mp4"
    }.get(kind, ".bin")


def _inspect_download(path: Path, content_type: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path.resolve()), "bytes": path.stat().st_size}
    if (content_type and "xml" in content_type.casefold()) or path.suffix.casefold() == ".xml":
        try:
            root = ET.parse(path).getroot()
            result.update({
                "inspection": "XML parsed", "rootTag": root.tag,
                "elementCount": sum(1 for _ in root.iter()),
            })
        except ET.ParseError as error:
            result.update({"inspection": "XML parse failed", "error": str(error)})
    else:
        result["inspection"] = "attachment downloaded; use the agent image/file viewer for semantic inspection"
    return result


def _collect_scenario_evidence(
    client: DeviceCloudDiagnosticsClient,
    job_id: int,
    scenario: dict[str, Any] | None,
    output_dir: Path,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[dict[str, Any]],
]:
    if not scenario:
        return None, None, None, []

    timeline = None
    item_uuid = scenario.get("reportPortalItemUuid")
    if scenario.get("id") is not None:
        timeline = client.timeline(job_id, int(scenario["id"]))
        item_uuid = item_uuid or timeline.get("itemUuid")
    if not item_uuid:
        return timeline, None, None, []

    from_time = scenario.get("startedAt")
    to_time = scenario.get("completedAt")
    logs = client.logs(
        job_id, str(item_uuid), from_time=from_time, to_time=to_time, first=100
    )
    evidence = client.evidence(
        job_id, str(item_uuid), from_time=from_time, to_time=to_time
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    downloads = []
    for attachment in evidence.get("attachments") or []:
        content_path = attachment.get("contentPath")
        if not content_path:
            continue
        destination = output_dir / (
            f"job-{job_id}-log-{attachment.get('logId')}{_extension(attachment)}"
        )
        download = client.download_evidence(job_id, str(content_path), destination)
        inspection = _inspect_download(destination, attachment.get("contentType"))
        downloads.append({**attachment, **download, **inspection})
    return timeline, logs, evidence, downloads


def full_diagnosis(
    client: DeviceCloudDiagnosticsClient,
    job_id: int,
    output_dir: Path,
) -> dict[str, Any]:
    # 固定顺序：report → failed step/timeline → failure-window logs → evidence → download/inspect。
    report = client.job_report(job_id)
    scenario = _first_scenario(report)
    timeline, logs, evidence, downloads = _collect_scenario_evidence(
        client, job_id, scenario, output_dir
    )

    failed_step = _first_failed_step(timeline)
    job = report.get("job") or {}
    reason_value = (
        (scenario or {}).get("errorMessage")
        or (failed_step or {}).get("name")
        or job.get("errorMessage")
        or report.get("message")
    )
    reason = str(reason_value) if reason_value else None
    if not reason and str(job.get("status") or "").upper() not in SUCCESS_STATUSES:
        reason = "未返回失败原因"
    return {
        "sop": [
            "job-report", "first-failed-step", "timeline", "failure-window-logs",
            "evidence", "download-and-inspect-screenshot-xml", "failure-classification",
        ],
        "terminal": {
            "planId": job.get("testPlanId"), "jobId": job.get("id") or job_id,
            "resources": job.get("resources") or [],
            "scenarioCount": sum(
                len(feature.get("scenarios") or []) for feature in report.get("features") or []
            ),
            "finalStatus": job.get("status"), "firstFailureReason": reason,
            "failureClassification": _failure_classification(
                reason, str(job.get("status") or ""), job.get("resources") or []
            ),
        },
        "selectedScenario": scenario,
        "firstFailedStep": failed_step,
        "timeline": timeline,
        "logs": logs,
        "evidence": evidence,
        "downloads": downloads,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=sorted(ENV_ENDPOINTS), default="staging-cn")
    parser.add_argument("--endpoint", help="override Device Cloud GraphQL endpoint")
    commands = parser.add_subparsers(dest="command", required=True)

    report = commands.add_parser("job-report")
    report.add_argument("--job-id", type=int, required=True)

    timeline = commands.add_parser("timeline")
    timeline.add_argument("--job-id", type=int, required=True)
    timeline.add_argument("--scenario-id", type=int, required=True)

    for name in ("logs", "evidence"):
        command = commands.add_parser(name)
        command.add_argument("--job-id", type=int, required=True)
        command.add_argument("--item-uuid", required=True)
        command.add_argument("--from", dest="from_time")
        command.add_argument("--to", dest="to_time")
        if name == "logs":
            command.add_argument("--level")
            command.add_argument("--source", choices=["SCRIPT", "PHONE", "SERIAL", "OTHER"])
            command.add_argument("--after")
            command.add_argument("--first", type=int, default=100)

    content = commands.add_parser("evidence-content")
    content.add_argument("--job-id", type=int, required=True)
    content.add_argument("--content-path", required=True)
    content.add_argument("--output", type=Path, required=True)
    content.add_argument("--preview", action="store_true")

    full = commands.add_parser("full", help="按固定 SOP 完成失败诊断和附件检查")
    full.add_argument("--job-id", type=int, required=True)
    full.add_argument("--output-dir", type=Path, default=Path("device-cloud-evidence"))

    cancel_job = commands.add_parser("cancel-job")
    cancel_job.add_argument("--job-id", type=int, required=True)
    cancel_job.add_argument("--timeout", type=int, default=60)

    cancel_plan = commands.add_parser("cancel-plan")
    cancel_plan.add_argument("--plan-id", type=int, required=True)
    cancel_plan.add_argument("--timeout", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    endpoint = args.endpoint or ENV_ENDPOINTS[args.env]
    auth = DeviceCloudAuthClient(server_base_url(endpoint))
    auth.authenticate()
    client = DeviceCloudDiagnosticsClient(endpoint, auth)
    if args.command == "job-report":
        result = client.job_report(args.job_id)
    elif args.command == "timeline":
        result = client.timeline(args.job_id, args.scenario_id)
    elif args.command == "logs":
        result = client.logs(
            args.job_id, args.item_uuid, level=args.level, source=args.source,
            from_time=args.from_time, to_time=args.to_time, after=args.after, first=args.first,
        )
    elif args.command == "evidence":
        result = client.evidence(
            args.job_id, args.item_uuid, from_time=args.from_time, to_time=args.to_time
        )
    elif args.command == "evidence-content":
        result = client.download_evidence(
            args.job_id, args.content_path, args.output, preview=args.preview
        )
    elif args.command == "full":
        result = full_diagnosis(client, args.job_id, args.output_dir)
    elif args.command == "cancel-job":
        result = client.cancel_job(args.job_id, timeout_seconds=args.timeout)
    else:
        result = client.cancel_plan(args.plan_id, timeout_seconds=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DiagnosticError, ValueError) as error:
        raise SystemExit(f"error: {error}") from None
