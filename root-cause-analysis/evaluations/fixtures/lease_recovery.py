"""Controlled queue facts for evaluation, not a production recovery tool."""
from dataclasses import dataclass


@dataclass
class Job:
    lease: str = "held"
    queued: bool = False
    output: str | None = None


def release(job, *, expired, scheduler_running, write_ok):
    if not scheduler_running or not expired:
        return "not_invoked"
    if not write_ok:
        return "write_failed"
    job.lease = "released"
    return "released"


def resubmit(job, *, input_available, write_ok):
    if job.lease == "held" or job.queued:
        return "existing_job"
    if not input_available:
        return "input_missing"
    if not write_ok:
        return "write_failed"
    job.queued = True
    return "queued"


def execute(job, *, worker_running, output_write_ok):
    if not worker_running or not job.queued:
        return "not_invoked"
    if not output_write_ok:
        return "write_failed"
    job.output = "result"
    job.queued = False
    return "completed"
