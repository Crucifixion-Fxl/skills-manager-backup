#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fixed read-only platform queries, timestamp helpers and redaction."""
from __future__ import annotations
import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request

JOBS = """query TriageJobs($page:Int!,$size:Int!) {
 jobs(page:$page,pageSize:$size) { total current size records {
 id status createdAt updatedAt completedAt testPlanId
 }}
}"""
JOB = """query TriageJob($id:ID!) { job(id:$id) {
 id name status createdAt updatedAt startedAt completedAt testPlanId jobType
  errorMessage reportPortalUrl reportPortalUuid metadata
  features { id name filename status scenarios {
   id name line status errorMessage metadata tags reportPortalUuid
   steps { name line status errorMessage keyword text }
  }}
 }}"""
PLAN = """query TriagePlan($id:ID!) { testPlan(id:$id) {
 id name planType status createdAt beginAt endAt metadata
}}"""
REPORT = """query TriageReport($job:ID!) { jobDiagnosticReport(jobId:$job) {
 state message job { application { platform packageName version commit environment builtAt }
 resources { kind name serialNumber platform cabinetName } }
 reportPortal { launchId launchUuid status url }
 features { id filename scenarios {
  id name reportPortalItemUuid reportPortalItemId serverStatus reportPortalStatus
 }}
}}"""
LOGS = """query TriageLogs($job:ID!,$item:String!,$after:String,$size:Int!) {
 jobDiagnosticLogs(jobId:$job,itemUuid:$item,after:$after,first:$size) {
  entries { id time level source message hasAttachment contentType }
  endCursor hasNextPage totalCount truncated
 }}"""
EVIDENCE = """query TriageEvidence($job:ID!,$item:String!,$from:DateTime!,$to:DateTime!) {
 jobDiagnosticEvidence(jobId:$job,itemUuid:$item,from:$from,to:$to) {
  state message truncated attachments { logId time level message contentType kind contentPath }
 }}"""
TIMELINE = """query TriageTimeline($job:ID!,$scenario:ID!) {
 jobDiagnosticTimeline(jobId:$job,scenarioId:$scenario) {
  state terminal truncated diagnosticsMode degradationReasons warnings attempts {
   attemptId attemptIndex status startedAt completedAt steps {
    attemptId parentAttemptId scope line keyword name status startedAt completedAt
   }
  }
 }}"""
FAILED = {"failed", "error", "broken", "undefined"}
SAFE_METADATA = {"mode", "executionMode", "deviumAiMode", "executionLocation",
                 "purpose", "phase", "testsRepo", "testsRef", "clientVersion",
                 "pluginVersion", "attemptId", "diagnosticsMode"}
SECRET_FIELD = re.compile(r"(?i)(token|password|secret|api[_-]?key|authorization|cookie)")
SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+)[^\s\"<>]+|"
    r"((?:token|password|secret|api[_-]?key|authorization)\s*[=:]\s*[\"']?)[^\s\"',}]+|"
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b|"
    r"\bdcpat_[A-Za-z0-9_-]+\b"
)

class CollectionError(RuntimeError):
    pass

def redact(value):
    if isinstance(value, str):
        return SECRET_TEXT.sub(lambda m: (m.group(1) or m.group(2) or "") + "[REDACTED]", value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_FIELD.search(k) else redact(v) for k, v in value.items()}
    return value

def safe_metadata(value):
    """Never expose metadata.env; known mode fields are observations, not conclusions."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}
    result = {k: redact(v) for k, v in value.items()
              if k in SAFE_METADATA and isinstance(v, (str, int, bool, type(None)))}
    if isinstance(value.get("execution"), dict):
        result["execution"] = safe_metadata(value["execution"])
    return result

def timestamp(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)

def iso(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")

def in_window(job, start, end):
    # Job created earlier can fail this week. Do not filter solely by Plan.createdAt.
    time = timestamp(job.get("completedAt") or job.get("updatedAt") or job.get("createdAt"))
    return time is not None and start <= time < end

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError("redirect refused; verify configured endpoint")

class Platform:
    def __init__(self, endpoint, token, timeout=30):
        url = urllib.parse.urlparse(endpoint)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise CollectionError("configure an HTTPS platform GraphQL endpoint")
        if not token:
            raise CollectionError("DEVICE_CLOUD_READ_TOKEN is missing")
        self.endpoint, self.token, self.timeout = endpoint, token, timeout
        self.opener = urllib.request.build_opener(NoRedirect)

    def query(self, query, variables):
        if not query.lstrip().startswith("query "):
            raise CollectionError("collector accepts only fixed GraphQL queries")
        req = urllib.request.Request(self.endpoint,
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                value = json.load(response)
        except urllib.error.HTTPError as exc:
            raise CollectionError(f"platform HTTP {exc.code}") from None
        except (urllib.error.URLError, ValueError, TimeoutError):
            raise CollectionError("platform unavailable or invalid response") from None
        if value.get("errors"):
            # Do not print raw server errors: they can contain token-bearing inputs.
            raise CollectionError("GraphQL query rejected; inspect authorized schema/scopes")
        if not isinstance(value.get("data"), dict):
            raise CollectionError("platform returned no data")
        return value["data"]


if __name__ == "__main__":
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
