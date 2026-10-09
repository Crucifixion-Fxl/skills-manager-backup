"""Trackman adapter. Fetch and split logic shared with the trackman-shot-export skill (engineering/skills), which ships this file via scripts/sync_trackman_skill.py."""
import json
import math
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from . import Adapter

PARSER_VERSION = "trackman-parse.v1"
SHARE_HOST = "tm-short.me"
REPORT_HOST = "web-dynamic-reports.trackmangolf.com"
API_HOST = "golf-player-activities.trackmangolf.com"
API = f"https://{API_HOST}/api/reports/"
VARIANTS = ("Measurement", "NormalizedMeasurement")
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

ADAPTER = Adapter(
    source="trackman",
    url_semantics="tm-short.me share link or TrackMan dynamic report URL; the link is a quasi-credential",
    allowed_hosts=(SHARE_HOST, REPORT_HOST, API_HOST),
    parser_version=PARSER_VERSION,
    attribution="explicit report_link to one experiment; keep strokes whose Time is in [window_start, window_end)",
    layer="trackman",
)

Transport = Callable[[str, dict | None, float], tuple[str, bytes]]


class AllowlistRedirectHandler(HTTPRedirectHandler):
    hosts = (SHARE_HOST, REPORT_HOST)

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self.hosts:
            raise ValueError("Redirect left the allowed TrackMan hosts")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urllib_transport(url: str, payload: dict | None, timeout: float,
                     max_bytes: int = MAX_RESPONSE_BYTES) -> tuple[str, bytes]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    req = Request(url, data=data, headers={"User-Agent": "cloud-ingest/0.1",
                                          "Content-Type": "application/json"})
    with build_opener(AllowlistRedirectHandler()).open(req, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("Response exceeds size limit")
        return response.geturl(), body


def fetch_report(url: str, transport: Transport, timeout: float) -> bytes:
    """Resolve a share link and return the raw report API response bytes."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in (SHARE_HOST, REPORT_HOST):
        raise ValueError("Expected HTTPS tm-short.me or TrackMan dynamic report URL")
    resolved, _ = transport(url, None, timeout)
    parsed = urlparse(resolved)
    if parsed.scheme != "https" or parsed.hostname != REPORT_HOST:
        raise ValueError("Share link did not resolve to the supported TrackMan report site")
    query = parse_qs(parsed.query)
    if len(query.get("a", [])) == 1:
        identifier, field, route = query["a"][0], "ActivityId", "getactivityreport"
    elif len(query.get("r", [])) == 1:
        identifier, field, route = query["r"][0], "ReportId", "getreport"
    else:
        raise ValueError("Expected one activity a= or report r= identifier")
    UUID(identifier)
    payload: dict[str, Any] = {field: identifier}
    for key, outkey in (("nd_altitude", "Altitude"), ("nd_temperature", "Temperature")):
        if key in query:
            value = float(query[key][0])
            if not math.isfinite(value):
                raise ValueError(f"Invalid {key}")
            unit = query.get(key + "Unit", ["Meters" if key == "nd_altitude" else "Celsius"])[0]
            if key == "nd_altitude":
                if unit not in ("Meters", "Feet"):
                    raise ValueError("Unknown altitude unit")
                value = value * 0.3048 if unit == "Feet" else value
            else:
                if unit not in ("Celsius", "Fahrenheit"):
                    raise ValueError("Unknown temperature unit")
                value = (value - 32) * 5 / 9 if unit == "Fahrenheit" else value
            payload[outkey] = value
    if "nd_ballType" in query:
        payload["BallType"] = query["nd_ballType"][0]
    _, body = transport(API + route, payload, timeout)
    return body


@dataclass(frozen=True)
class Shot:
    link_id: str
    shot_key: str
    group_index: int
    group_id: Any
    shot_index: int
    stroke_id: str
    time: str | None
    club: Any
    measurements: dict
    trajectories: dict

    def ball_trajectory(self, variant: str) -> list | None:
        series = self.trajectories.get(variant)
        return None if series is None else series.get("BallTrajectory")


def _valid_point(point: Any) -> bool:
    return isinstance(point, dict) and all(
        isinstance(point.get(axis), (float, int)) and not isinstance(point[axis], bool)
        and math.isfinite(point[axis]) for axis in ("X", "Y", "Z"))


def parse_report(raw: bytes, link_id: str) -> list[Shot]:
    """Split one raw report into shots; same validation as the skill's split_reports."""
    report = json.loads(raw)
    if not isinstance(report, dict) or not isinstance(report.get("StrokeGroups"), list):
        raise ValueError("Unsupported response: expected object with StrokeGroups array")
    shots, seen = [], set()
    for group_index, group in enumerate(report["StrokeGroups"], 1):
        if not isinstance(group, dict) or not isinstance(group.get("Strokes"), list):
            raise ValueError("Unsupported group: expected Strokes array")
        for shot_index, stroke in enumerate(group["Strokes"], 1):
            if not isinstance(stroke, dict) or not stroke.get("Id"):
                raise ValueError("Missing stroke Id; refusing to invent identity")
            shot_key = f"1:{group_index}:{stroke['Id']}"
            if shot_key in seen:
                raise ValueError(f"Duplicate stroke within group: {shot_key}")
            seen.add(shot_key)
            unexpected = [k for k, v in stroke.items() if k not in VARIANTS and
                          isinstance(v, dict) and any("Trajectory" in x for x in v)]
            if unexpected:
                raise ValueError(f"Unsupported measurement variant: {unexpected}")
            measurements, trajectories = {}, {}
            for variant in VARIANTS:
                measurement = stroke.get(variant)
                if measurement is None:
                    measurements[variant] = trajectories[variant] = None
                    continue
                if not isinstance(measurement, dict):
                    raise ValueError(f"{shot_key}: {variant} is not an object")
                fields, series = {}, {}
                for name, value in measurement.items():
                    if "Trajectory" not in name:
                        fields[name] = value
                        continue
                    if value is not None and not isinstance(value, list):
                        raise ValueError(f"{shot_key}: unsupported trajectory structure {name}")
                    if value and not all(_valid_point(p) for p in value):
                        raise ValueError(f"{shot_key}: invalid XYZ point in {name}")
                    series[name] = value
                measurements[variant], trajectories[variant] = fields, series
            shots.append(Shot(link_id=link_id, shot_key=shot_key, group_index=group_index,
                              group_id=group.get("Id"), shot_index=shot_index, stroke_id=stroke["Id"],
                              time=stroke.get("Time"), club=stroke.get("Club", group.get("Club")),
                              measurements=measurements, trajectories=trajectories))
    if not shots:
        raise ValueError("Report has no strokes")
    return shots
