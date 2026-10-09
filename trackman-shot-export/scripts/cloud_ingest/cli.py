"""Import TrackMan share links onto a session in rerun hub and pair them with the LM shots, from your own machine.

    python -m cloud_ingest.cli {preview,import,shots} --dataset D --session S

The same P3 -> P4 -> P5 as the dagster job (pipeline.py, shots.py), run straight against the Hub (#48):
no collect-gateway, no collect-sdk, no dagster; rerun-sdk + pyarrow only. The trackman-shot-export skill
ships this module unchanged. Share links are quasi-credentials: read from the environment variable
TRACKMAN_URLS (whitespace-separated) or stdin, never from argv, never printed.
Hub: RERUN_HUB_URL (rerun+http://host:port, gRPC), RERUN_HUB_HTTP_URL (http(s)://host:port, the HTTP API)
and RERUN_HUB_TOKEN (read-write on the session dataset, and on the session project's
<project>.ingest_registry and <project>.ingest_status; owner of that project covers all three).
"""
import argparse
import json
import os
import secrets
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from . import shots as S
from .adapters.trackman import ADAPTER, parse_report
from .attribution import attribute, parse_time
from .pipeline import REGISTERED, derive_shots, ingest_experiment, safe_error
from .records import RegistryRow

EXECUTOR = "skill:trackman-shot-export"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class CliError(Exception):
    pass


class Log:
    """dagster-log-shaped logger on stderr; ingest messages already have URLs replaced with <url>."""

    def __init__(self, stream=sys.stderr):
        self.stream = stream

    def info(self, msg):
        print(f"[info] {msg}", file=self.stream)

    def warning(self, msg):
        print(f"[warn] {msg}", file=self.stream)


def new_ulid(now_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ms << 80) | secrets.randbits(80)
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def read_urls(from_stdin: bool, stdin=sys.stdin, environ=os.environ) -> list[str]:
    text = stdin.read() if from_stdin else environ.get("TRACKMAN_URLS", "")
    urls = list(dict.fromkeys(text.split()))
    if not urls:
        raise CliError("no share link: set TRACKMAN_URLS (whitespace-separated) or pipe links with --urls-stdin")
    return urls


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise CliError(f"environment variable {name} is not set")
    return value


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_window(args, inputs: S.SessionInputs) -> tuple[datetime, datetime, str]:
    """Explicit > session properties started_at/ended_at > recording event_time range +- pad (model.html section 11).

    Never an empty or reversed window: it would keep no strokes, and import would then delete the trackman layer."""
    start, end, how = _window(args, inputs)
    if not start < end:
        raise CliError(f"window start {_iso(start)} is not before end {_iso(end)} ({how}); fix --window-start/--window-end")
    return start, end, how


def _window(args, inputs: S.SessionInputs) -> tuple[datetime, datetime, str]:
    if args.window_start or args.window_end:
        start, end = parse_time(args.window_start), parse_time(args.window_end)
        if start is None or end is None:
            raise CliError("--window-start/--window-end must both be ISO-8601 with a timezone")
        return start, end, "explicit"
    start, end = (parse_time(inputs.properties.get(f"session:{k}")) for k in ("started_at", "ended_at"))
    if start and end:
        return start, end, "session properties"
    if inputs.lm:
        pad = timedelta(seconds=args.pad_s)
        first = min(r.event_ns for r in inputs.lm)
        last = max(r.event_ns for r in inputs.lm)
        to_dt = lambda ns: datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)  # noqa: E731
        return to_dt(first) - pad, to_dt(last) + pad + timedelta(milliseconds=1), f"recording event_time +- {args.pad_s:g}s"
    raise CliError("session has no started_at/ended_at and no recording: pass --window-start and --window-end")


def _resources():
    from . import hub_direct
    return hub_direct.resources(_env("RERUN_HUB_URL"), _env("RERUN_HUB_HTTP_URL"), _env("RERUN_HUB_TOKEN"))


def _pairing_report(pairing: S.Pairing) -> dict:
    return {"counts": pairing.counts(), "params": asdict(pairing.params), "revision": pairing.revision(),
            "clock_map": [{"boot_wallclock_ms": c.boot_wallclock_ms, "offset_ms": c.offset_ms,
                           "residual_ms": c.residual_ms, "n_pairs": c.n_pairs, "quality": c.quality}
                          for c in pairing.clock_maps],
            "shots": [{k: v for k, v in r.fields(pairing.revision()).items()
                       if k in ("shot_index", "status", "lm_shot_id", "tm_link_id", "tm_shot_key", "dt_ms",
                                "match_method", "candidates", "lm_rejected", "calibration_missing")}
                      for r in pairing.rows]}


def _params(args) -> S.Params:
    return S.Params(B_s=args.B_s, W_s=args.W_s, T_s=args.T_s)


def cmd_preview(args, res) -> dict:
    """Read-only: pull and parse the links, apply the window, pair against the session's LM records in memory."""
    inputs = res["sessions"].load(args.dataset, args.session)
    start, end, how = resolve_window(args, inputs)
    links, strokes = [], []
    for i, url in enumerate(read_urls(args.urls_stdin), 1):
        link_id = f"preview-{i}"
        try:
            parsed = parse_report(res["fetcher"].fetch(url), link_id)
        except Exception as exc:
            links.append({"link": i, "error": safe_error(exc, url)})
            continue
        kept, excluded = attribute(parsed, start, end)
        no_time = sum(parse_time(s.time) is None for s in excluded)
        links.append({"link": i, "strokes": len(parsed), "in_window": len(kept),
                      "outside_window": len(excluded) - no_time, "no_timezone_or_time": no_time})
        strokes += [S.TmStroke(link_id, s.shot_key, str(s.stroke_id), int(parse_time(s.time).timestamp() * 1e9))
                    for s in kept]
    pairing = S.pair_time_nearest_v1(inputs.lm, strokes, inputs.reviews, _params(args))
    return {"mode": "preview (nothing written)", "dataset": args.dataset, "session": args.session,
            "window": {"start": _iso(start), "end": _iso(end), "from": how}, "lm_records": len(inputs.lm),
            "links": links, **_pairing_report(pairing)}


def cmd_import(args, res) -> dict:
    """P3 register every link, P4 ingest them here, P5 derive shots here (same code as the dagster job)."""
    inputs = res["sessions"].load(args.dataset, args.session)
    start, end, how = resolve_window(args, inputs)
    urls = read_urls(args.urls_stdin)
    existing = {r.url: r.link_id for r in res["registry"].scan(source=ADAPTER.source, dataset=args.dataset,
                                                                 recording_id=args.session) if r.active and r.url}
    link_ids, revision = [], 0
    for url in urls:  # P3: attach each link to the session (same row the gateway's control envelope writes)
        link_id = existing.get(url) or new_ulid()
        link_ids.append(link_id)
        row = RegistryRow(ADAPTER.source, args.dataset, args.session, link_id, 0, True, url=url,
                          window_start=start, window_end=end)
        revision = res["registry"].attach(row, idempotency_key=f"{args.executor}:{new_ulid()}").revision
    log, submitter = Log(), res["submitter"]
    state = ingest_experiment(args.dataset, args.session, revision, registry=res["registry"], fetcher=res["fetcher"],
                              submitter=submitter, status=res["status"], log=log)
    out = {"mode": "import", "dataset": args.dataset, "session": args.session,
           "window": {"start": _iso(start), "end": _iso(end), "from": how}, "registry_revision": revision,
           "link_ids": link_ids, "trackman_state": state}
    if state != REGISTERED:
        out["shots"] = "not derived: trackman layer not registered (see <project>.ingest_status)"
        return out
    pairing = derive_shots(args.dataset, args.session, revision, sessions=res["sessions"], submitter=submitter,
                           status=res["status"], log=log, params=_params(args), algo_version=args.executor)
    return {**out, **(_pairing_report(pairing) if pairing else {"shots": "no inputs"})}


def cmd_shots(args, res) -> dict:
    """P5 only: re-derive the shots layer from what the Hub has now."""
    pairing = derive_shots(args.dataset, args.session, 0, sessions=res["sessions"],
                           submitter=res["submitter"], status=res["status"], log=Log(),
                           params=_params(args), algo_version=args.executor)
    return {"mode": "shots", "dataset": args.dataset, "session": args.session,
            **(_pairing_report(pairing) if pairing else {"shots": "no recording or trackman layer"})}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cloud_ingest.cli", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_ in (("preview", cmd_preview, "read-only dry run: window check and pairing preview"),
                            ("import", cmd_import, "register links, ingest trackman, derive shots"),
                            ("shots", cmd_shots, "re-derive the shots layer only")):
        c = sub.add_parser(name, help=help_)
        c.set_defaults(func=fn)
        c.add_argument("--dataset", required=True, help="session Dataset")
        c.add_argument("--session", required=True, help="session_id = recording_id = segment_id")
        c.add_argument("--executor", default=EXECUTOR, help="written to /shots/summary.algo_version")
        c.add_argument("--B-s", dest="B_s", type=float, default=S.Params.B_s)
        c.add_argument("--W-s", dest="W_s", type=float, default=S.Params.W_s)
        c.add_argument("--T-s", dest="T_s", type=float, default=S.Params.T_s)
        if name != "shots":
            c.add_argument("--urls-stdin", action="store_true", help="read share links from stdin, not TRACKMAN_URLS")
            c.add_argument("--window-start", help="ISO-8601 with timezone; overrides the session's own window")
            c.add_argument("--window-end")
            c.add_argument("--pad-s", type=float, default=S.Params.W_s,
                           help="padding when the window falls back to the recording's event_time range")
    return p


def main(argv=None, resources=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        out = args.func(args, resources or _resources())
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # Hub / network errors: one line, share links already scrubbed by safe_error
        print(f"error: {safe_error(exc)}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
