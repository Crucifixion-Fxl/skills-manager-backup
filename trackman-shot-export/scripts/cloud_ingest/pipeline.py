"""P4 (Trackman ingest) and P5 (shots) as plain functions over injected registry / fetcher / submitter /
status / sessions (model.html section 15). No dagster: the dagster job (ingest.py) and the
trackman-shot-export skill both run these."""
import hashlib
import json
import re
import tempfile
import time
from functools import partial
from pathlib import Path

from .adapters.trackman import ADAPTER, parse_report
from .attribution import attribute
from .records import StatusRecord
from .rrd import trackman_rrd
from . import shots as shots_mod

PULLING, TRANSFORMING, REGISTERED, QUARANTINED = "PULLING", "TRANSFORMING", "REGISTERED", "QUARANTINED"
ALGO_VERSION = "cloud-ingest/0.1.0"
_URLISH = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+|\S*tm-short\.me\S*")


def derive_idempotency_key(source_record_id: str, recording_id: str, layer: str, revision: str) -> str:
    """Envelope contract: ik1- + hex(sha256(source_record_id ␟ recording_id ␟ layer ␟ revision))."""
    joined = "\x1f".join((source_record_id, recording_id, layer, revision))
    return "ik1-" + hashlib.sha256(joined.encode()).hexdigest()


def submit_rrd(submitter, write, dataset: str, recording_id: str, layer: str, source_record_id: str) -> str:
    """Write one RRD to a temporary file and submit it; returns the idempotency key.

    RRD bytes are not reproducible (RowIds differ on every write), so the key's revision is the sha256 of
    this very file: a retry or re-run is simply a new upload that REPLACEs the layer, and the gateway
    reclaims the object it replaced. Nothing is kept on local disk between attempts."""
    with tempfile.TemporaryDirectory(prefix="cloud-ingest-") as tmp:
        path = write(Path(tmp) / f"{layer}.rrd")
        with open(path, "rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        key = derive_idempotency_key(source_record_id, recording_id, layer, digest)
        submitter.submit(path, dataset, recording_id, layer, source_record_id, key)
    return key


def safe_error(exc: BaseException, url: str | None = None) -> str:
    message = str(exc).replace(url, "<url>") if url else str(exc)
    return f"{type(exc).__name__}: {_URLISH.sub('<url>', message)}"[:300]


def _fetch(fetcher, url, max_attempts, backoff_s, sleep):
    for attempt in range(1, max_attempts + 1):
        try:
            return fetcher.fetch(url)
        except OSError:
            if attempt == max_attempts:
                raise
            sleep(backoff_s * 2 ** (attempt - 1))


def ingest_experiment(dataset, recording_id, revision, *, registry, fetcher, submitter, status, log,
                      max_attempts=ADAPTER.max_attempts, backoff_s=ADAPTER.backoff_s, sleep=time.sleep,
                      project=""):
    """`project` is the one whose <project>.ingest_registry named the experiment; its status goes to
    <project>.ingest_status. Empty: taken from the registry rows (the status writer resolves it otherwise)."""
    source, layer = ADAPTER.source, ADAPTER.layer
    rows = registry.scan(source=source, dataset=dataset, recording_id=recording_id, project=project or None)
    project = project or next((r.project for r in rows if r.project), "")
    experiment = partial(StatusRecord, source, dataset, recording_id, None, revision=revision, project=project)
    latest = max((r.revision for r in rows), default=0)
    if latest > revision:
        log.info(f"{dataset}/{recording_id}: superseded by revision {latest}; skipping revision {revision}")
        return None
    active = sorted((r for r in rows if r.active and r.url), key=lambda r: r.link_id)
    if not active:
        submitter.delete_layer(dataset, recording_id, layer)
        status.write(experiment(REGISTERED, detail="deleted: no active links"))
        log.info(f"{dataset}/{recording_id}: no active links, layer {layer} deleted")
        return REGISTERED

    parsed, failed = [], 0
    for row in active:
        link = partial(StatusRecord, source, dataset, recording_id, row.link_id, revision=row.revision,
                       project=project)
        status.write(link(PULLING))
        try:
            body = _fetch(fetcher, row.url, max_attempts, backoff_s, sleep)
        except Exception as exc:
            failed += 1
            status.write(link(QUARANTINED, detail=f"pull failed: {safe_error(exc, row.url)}"))
            log.warning(f"link {row.link_id}: pull failed ({safe_error(exc, row.url)})")
            continue

        # No separate raw copy (#47): the session's trackman layer is the only Trackman copy (model.html R6).
        status.write(link(TRANSFORMING))
        try:
            shots = parse_report(body, row.link_id)
        except Exception as exc:
            failed += 1
            status.write(link(QUARANTINED, detail=f"parse failed: {safe_error(exc, row.url)}"))
            log.warning(f"link {row.link_id}: parse failed ({safe_error(exc, row.url)})")
            continue
        kept, excluded = attribute(shots, row.window_start, row.window_end)
        parsed.append((row, kept))
        log.info(f"link {row.link_id}: {len(kept)} shots attributed, {len(excluded)} raw-only")

    shots = [s for _, kept in parsed for s in kept]
    if not shots:
        if parsed and not failed:
            submitter.delete_layer(dataset, recording_id, layer)
            status.write(experiment(REGISTERED, detail="deleted: no shots in window"))
            log.info(f"{dataset}/{recording_id}: no attributable shots, layer {layer} deleted")
            return REGISTERED
        status.write(experiment(QUARANTINED, detail=f"no usable links ({failed} failed); layer unchanged"))
        log.warning(f"{dataset}/{recording_id}: no usable links; layer {layer} unchanged")
        return QUARANTINED

    key = submit_rrd(submitter, partial(trackman_rrd, recording_id, shots), dataset, recording_id, layer,
                     f"{source}:{recording_id}")
    for row, _ in parsed:
        status.write(StatusRecord(source, dataset, recording_id, row.link_id, REGISTERED, row.revision,
                                  idempotency_key=key, project=project))
    status.write(experiment(REGISTERED, idempotency_key=key,
                            detail=f"{len(shots)} shots from {len(parsed)} links, {failed} quarantined"))
    log.info(f"{dataset}/{recording_id}: layer {layer} submitted, {len(shots)} shots")
    return REGISTERED


def derive_shots(dataset, session_id, revision, *, sessions, submitter, status, log,
                 params=shots_mod.Params(), algo_version=ALGO_VERSION, project=""):
    """P5: pair the session's recording and trackman (and shot_review) into the shots layer (model.html
    sections 10 and 15). Source layers are never rewritten; a failure leaves them as they are.
    `project` picks <project>.ingest_status, as in ingest_experiment."""
    record = partial(StatusRecord, shots_mod.SOURCE, dataset, session_id, None, revision=revision, project=project)
    inputs = sessions.load(dataset, session_id)
    if not inputs.has_inputs:
        if shots_mod.LAYER in inputs.layers:
            submitter.delete_layer(dataset, session_id, shots_mod.LAYER)
        status.write(record(REGISTERED, detail=f"deleted: no recording or trackman ({algo_version})"))
        log.info(f"{dataset}/{session_id}: no recording or trackman layer; shots layer removed")
        return None
    status.write(record(TRANSFORMING, detail=algo_version))
    pairing = shots_mod.pair_time_nearest_v1(inputs.lm, inputs.tm, inputs.reviews, params)
    write = partial(shots_mod.shots_rrd, session_id, pairing, algo_version=algo_version, inputs=inputs.layers,
                    orphan_algo_dump=inputs.orphan_algo_dump)
    try:
        key = submit_rrd(submitter, write, dataset, session_id, shots_mod.LAYER, f"{shots_mod.SOURCE}:{session_id}")
    except Exception as exc:
        status.write(record(QUARANTINED, detail=f"shots submit failed: {safe_error(exc)}; source layers unchanged"))
        raise
    counts = pairing.counts()
    status.write(record(REGISTERED, idempotency_key=key,
                        detail=f"{json.dumps(counts, sort_keys=True)} rule={shots_mod.RULE} by {algo_version}"))
    log.info(f"{dataset}/{session_id}: shots layer submitted {counts}")
    return pairing
