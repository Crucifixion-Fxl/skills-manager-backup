"""The derived `shots` layer: LM <-> Trackman pairing by time (model.html section 10, rule time_nearest_v1).

Pure pairing, the RRD writer, and turning Hub dataframes into pairing inputs. No dagster here: the
dagster job (ingest.py) and the developer CLI (cli.py) both call this module.
"""
import hashlib
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import rerun as rr

LAYER = "shots"
SOURCE = "lm_shots"
RULE = "time_nearest_v1"
MATCHED, LM_ONLY, TRACKMAN_ONLY, AMBIGUOUS, EXCLUDED = (
    "matched", "lm_only", "trackman_only", "ambiguous", "excluded")
STATUSES = (MATCHED, LM_ONLY, TRACKMAN_ONLY, AMBIGUOUS, EXCLUDED)
MIN_PAIRS = 3  # fewer pairs than this in a boot group: quality=coarse, offset_ms=0


@dataclass(frozen=True)
class Params:
    """Suggested initial values (model.html section 10); written into /shots/summary.params_json."""

    B_s: float = 60.0  # boot grouping: adjacent boot_wallclock_ms closer than this are one boot
    W_s: float = 30.0  # offset estimation: nearest Trackman stroke within +-W of each LM record
    T_s: float = 2.0  # pairing: |residual| <= T is matched


@dataclass(frozen=True)
class LmRecord:
    lm_shot_id: str
    event_ns: int  # impact on event_time
    mono_ns: int | None  # impact on device_mono_ns
    boot_wallclock_ms: int
    rejected: bool = False
    calibration_missing: bool = False


@dataclass(frozen=True)
class TmStroke:
    link_id: str
    shot_key: str
    stroke_id: str
    event_ns: int


@dataclass(frozen=True)
class Review:
    decision: str  # pair | unpair | exclude
    lm_shot_id: str | None = None
    tm_link_id: str | None = None
    tm_shot_key: str | None = None


@dataclass
class ShotRow:
    status: str
    match_method: str
    event_ns: int
    mono_ns: int | None = None
    lm: LmRecord | None = None
    tm: TmStroke | None = None
    dt_ms: float | None = None
    candidates: list[str] = field(default_factory=list)
    shot_index: int = 0

    @property
    def shot_id(self) -> str:
        return f"lm:{self.lm.lm_shot_id}" if self.lm else f"tm:{self.tm.link_id}:{self.tm.shot_key}"

    def fields(self, revision: str) -> dict:
        lm, tm = self.lm, self.tm
        return dict(
            shot_id=self.shot_id, shot_index=self.shot_index, status=self.status,
            lm_shot_id=lm and lm.lm_shot_id, lm_event_time_ms=lm and lm.event_ns // 1_000_000,
            lm_rejected=bool(lm and lm.rejected),
            tm_link_id=tm and tm.link_id, tm_shot_key=tm and tm.shot_key, tm_stroke_id=tm and tm.stroke_id,
            tm_event_time_ms=tm and tm.event_ns // 1_000_000, dt_ms=self.dt_ms,
            candidates=self.candidates or None, match_method=self.match_method,
            calibration_missing=bool(lm and lm.calibration_missing), revision=revision)

    def summary_line(self) -> str:
        dt = "" if self.dt_ms is None else f" · dt={self.dt_ms / 1000:+.1f} s"
        return f"#{self.shot_index} {self.status}{dt}"


@dataclass
class ClockMap:
    boot_wallclock_ms: int
    offset_ms: float
    residual_ms: float | None
    n_pairs: int
    quality: str  # estimated | coarse
    event_ns: int  # the group's first LM record
    anchor: str = "ulid"


@dataclass
class Pairing:
    rows: list[ShotRow]
    clock_maps: list[ClockMap]
    params: Params

    def counts(self) -> dict[str, int]:
        return {s: sum(r.status == s for r in self.rows) for s in STATUSES}

    def revision(self) -> str:
        """Content hash of the pairing result, so the same inputs and params give the same revision."""
        payload = json.dumps([[r.status, r.match_method, r.shot_id, r.tm and r.tm.shot_key, r.dt_ms]
                              for r in self.rows] + [asdict(self.params)], sort_keys=True, default=str)
        return "shots-" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _groups(lm: list[LmRecord], b_ms: float) -> list[list[LmRecord]]:
    """Boot groups: sorted by boot_wallclock_ms, adjacent records closer than B are one boot."""
    groups: list[list[LmRecord]] = []
    for rec in sorted(lm, key=lambda r: (r.boot_wallclock_ms, r.event_ns)):
        if groups and rec.boot_wallclock_ms - groups[-1][-1].boot_wallclock_ms < b_ms:
            groups[-1].append(rec)
        else:
            groups.append([rec])
    return groups


def _ms(ns: int) -> float:
    return ns / 1_000_000


def pair_time_nearest_v1(lm: list[LmRecord], tm: list[TmStroke], reviews: list[Review],
                         params: Params = Params()) -> Pairing:
    """model.html section 10, rule time_nearest_v1, step by step."""
    tm_by_key = {(s.link_id, s.shot_key): s for s in tm}
    lm_by_id = {r.lm_shot_id: r for r in lm}
    rows: list[ShotRow] = []
    fixed_lm: set[str] = set()
    fixed_tm: set[tuple[str, str]] = set()
    forbidden: set[tuple[str, tuple[str, str]]] = set()

    # 1. Human conclusions from shot_review are fixed first and take no part in the later steps.
    pending_pairs = []
    for rv in reviews:
        tm_key = (rv.tm_link_id, rv.tm_shot_key)
        rec, stroke = lm_by_id.get(rv.lm_shot_id), tm_by_key.get(tm_key)
        if rv.decision == "unpair" and rec and stroke:
            forbidden.add((rec.lm_shot_id, tm_key))
        elif rv.decision == "pair" and rec and stroke and rec.lm_shot_id not in fixed_lm and tm_key not in fixed_tm:
            fixed_lm.add(rec.lm_shot_id)
            fixed_tm.add(tm_key)
            pending_pairs.append((rec, stroke))
        elif rv.decision == "exclude":
            if rec and rec.lm_shot_id not in fixed_lm:
                fixed_lm.add(rec.lm_shot_id)
                rows.append(ShotRow(EXCLUDED, "review", rec.event_ns, rec.mono_ns, lm=rec))
            if stroke and tm_key not in fixed_tm:
                fixed_tm.add(tm_key)
                rows.append(ShotRow(EXCLUDED, "review", stroke.event_ns, tm=stroke))

    free_lm = [r for r in lm if r.lm_shot_id not in fixed_lm]
    free_tm = [s for s in tm if (s.link_id, s.shot_key) not in fixed_tm]

    # 2-3. Boot groups (all LM records, so a reviewed record still anchors its group) and one clock offset
    # per group: median of (tm - lm) over each free LM record's nearest free Trackman stroke within +-W.
    w_ms, t_ms = params.W_s * 1000, params.T_s * 1000
    offset_of: dict[str, float] = {}
    clock_maps = []
    for group in _groups(lm, params.B_s * 1000):
        diffs = []
        for rec in group:
            if rec.lm_shot_id in fixed_lm:
                continue
            near = [_ms(s.event_ns - rec.event_ns) for s in free_tm if abs(_ms(s.event_ns - rec.event_ns)) <= w_ms]
            if near:
                diffs.append(min(near, key=abs))
        if len(diffs) >= MIN_PAIRS:
            offset = float(statistics.median(diffs))
            residual = float(statistics.median(abs(d - offset) for d in diffs))
            quality = "estimated"
        else:
            offset, residual, quality = 0.0, None, "coarse"
        first = min(group, key=lambda r: r.event_ns)
        clock_maps.append(ClockMap(group[0].boot_wallclock_ms, offset, residual, len(diffs), quality, first.event_ns))
        for rec in group:
            offset_of[rec.lm_shot_id] = offset

    for rec, stroke in pending_pairs:
        dt = _ms(stroke.event_ns - rec.event_ns) - offset_of[rec.lm_shot_id]
        rows.append(ShotRow(MATCHED, "review", rec.event_ns, rec.mono_ns, lm=rec, tm=stroke, dt_ms=dt))

    # 4. After removing the offset, greedy one-to-one by |residual| <= T. An LM record with >= 2 candidates
    # within T whose two nearest differ by less than T/2 is ambiguous and claims nothing.
    candidates: dict[str, list[tuple[float, TmStroke]]] = {}
    for rec in free_lm:
        found = []
        for s in free_tm:
            key = (s.link_id, s.shot_key)
            residual = _ms(s.event_ns - rec.event_ns) - offset_of[rec.lm_shot_id]
            if abs(residual) <= t_ms and (rec.lm_shot_id, key) not in forbidden:
                found.append((residual, s))
        candidates[rec.lm_shot_id] = sorted(found, key=lambda x: (abs(x[0]), x[1].event_ns, x[1].shot_key))
    ambiguous = {lid for lid, found in candidates.items()
                 if len(found) >= 2 and abs(found[1][0]) - abs(found[0][0]) < t_ms / 2}
    edges = sorted(((abs(res), rec.event_ns, rec.lm_shot_id, s.event_ns, s.link_id, s.shot_key, res, rec, s)
                    for rec in free_lm if rec.lm_shot_id not in ambiguous
                    for res, s in candidates[rec.lm_shot_id]), key=lambda e: e[:6])
    used_lm, used_tm = set(), set()
    for *_, res, rec, s in edges:
        key = (s.link_id, s.shot_key)
        if rec.lm_shot_id in used_lm or key in used_tm:
            continue
        used_lm.add(rec.lm_shot_id)
        used_tm.add(key)
        rows.append(ShotRow(MATCHED, RULE, rec.event_ns, rec.mono_ns, lm=rec, tm=s, dt_ms=res))

    # 5. Everything left over.
    for rec in free_lm:
        if rec.lm_shot_id in ambiguous:
            rows.append(ShotRow(AMBIGUOUS, RULE, rec.event_ns, rec.mono_ns, lm=rec,
                                candidates=[s.shot_key for _, s in candidates[rec.lm_shot_id]]))
        elif rec.lm_shot_id not in used_lm:
            rows.append(ShotRow(LM_ONLY, "none", rec.event_ns, rec.mono_ns, lm=rec))
    for s in free_tm:
        if (s.link_id, s.shot_key) not in used_tm:
            rows.append(ShotRow(TRACKMAN_ONLY, "none", s.event_ns, tm=s))

    rows.sort(key=lambda r: (r.event_ns, r.shot_id))
    for index, r in enumerate(rows, 1):
        r.shot_index = index
    return Pairing(rows, sorted(clock_maps, key=lambda c: c.event_ns), params)


# ---- RRD --------------------------------------------------------------------------------------

def _values(**fields) -> rr.AnyValues:
    """Missing values are not written (model.html section 6: read back as null, never 0 or "")."""
    return rr.AnyValues(**{k: v for k, v in fields.items() if v is not None})


def shots_rrd(session_id: str, pairing: Pairing, path: str | Path, *, algo_version: str,
              inputs: dict[str, str | None] | None = None, orphan_algo_dump: list[str] | None = None) -> Path:
    path = Path(path)
    rec = rr.RecordingStream("launch_monitor", recording_id=session_id)
    rec.save(path)
    rr.set_log_time_enabled(False, recording=rec)
    rr.set_log_tick_enabled(False, recording=rec)
    revision = pairing.revision()
    for row in pairing.rows:
        rec.reset_time()
        rec.set_time("event_time", timestamp=np.datetime64(row.event_ns, "ns"))
        if row.mono_ns is not None:
            rec.set_time("device_mono_ns", duration=np.timedelta64(row.mono_ns, "ns"))
        rec.log("shots", _values(**row.fields(revision)), rr.TextLog(row.summary_line()))
    for cm in pairing.clock_maps:
        rec.reset_time()
        rec.set_time("event_time", timestamp=np.datetime64(cm.event_ns, "ns"))
        rec.log("shots/clock_map", _values(boot_wallclock_ms=cm.boot_wallclock_ms, offset_ms=cm.offset_ms,
                                           residual_ms=cm.residual_ms, n_pairs=cm.n_pairs, anchor=cm.anchor,
                                           quality=cm.quality))
    counts, inputs = pairing.counts(), inputs or {}
    rec.reset_time()
    rec.log("shots/summary", _values(
        revision=revision, algo_version=algo_version,
        params_json=json.dumps({"rule": RULE, **asdict(pairing.params)}, sort_keys=True),
        input_recording=inputs.get("recording"), input_trackman=inputs.get("trackman"),
        input_review=inputs.get("shot_review"),
        count_matched=counts[MATCHED], count_lm_only=counts[LM_ONLY], count_trackman_only=counts[TRACKMAN_ONLY],
        count_ambiguous=counts[AMBIGUOUS], orphan_algo_dump=orphan_algo_dump or None), static=True)
    rec.disconnect()
    return path


# ---- inputs from Hub dataframes ----------------------------------------------------------------

@dataclass
class SessionInputs:
    lm: list[LmRecord]
    tm: list[TmStroke]
    reviews: list[Review]
    layers: dict[str, str]  # layer name -> object URI of what is registered now
    orphan_algo_dump: list[str]
    properties: dict[str, object] = field(default_factory=dict)

    @property
    def has_inputs(self) -> bool:
        return "recording" in self.layers or "trackman" in self.layers


def _scalar(cell):
    """Reader cells are lists (one component batch per row); take the single value, or None."""
    if cell is None:
        return None
    if isinstance(cell, list):
        return cell[0] if cell else None
    return cell


def _int_column(table, name: str) -> list:
    """event_time / device_mono_ns as integer nanoseconds (to_pylist on the temporal type drops them)."""
    if name not in table.schema.names:
        return [None] * table.num_rows
    return table.column(name).cast(pa.int64()).to_pylist()


def _entity_rows(table, entity: str, key: str):
    """(row dict of this entity's components, event_ns, mono_ns) for every row where `entity:key` is set."""
    prefix = f"{entity}:"
    names = [n for n in table.schema.names if n.startswith(prefix)]
    if f"{entity}:{key}" not in names:
        return
    cols = {n[len(prefix):]: table.column(n).to_pylist() for n in names}
    times, monos = _int_column(table, "event_time"), _int_column(table, "device_mono_ns")
    for i in range(table.num_rows):
        if _scalar(cols[key][i]) is None:
            continue
        yield {k: _scalar(v[i]) for k, v in cols.items()}, times[i], monos[i]


def inputs_from_tables(timed, static, layers: dict[str, str]) -> SessionInputs:
    """Build pairing inputs from one segment's reader output: `timed` on event_time, `static` with index=None.

    `timed` must cover /lm/record, /lm/events/impact, /lm/provenance, /lm/algo/provenance, /trackman/**
    and /shot_review; `static` covers /lm/calibration/** (model.html sections 6-10)."""
    calibration = set()
    if static is not None:
        for name in static.schema.names:
            if name.startswith("/lm/calibration/") and name.endswith(":sha256"):
                calibration |= {_scalar(v) for v in static.column(name).to_pylist()} - {None}

    rejected = {r["lm_shot_id"] for r, _, _ in _entity_rows(timed, "/lm/events/impact", "lm_shot_id")
                if r.get("reject_reason")}
    cal_sha = {r["lm_shot_id"]: r.get("calibration_sha256")
               for r, _, _ in _entity_rows(timed, "/lm/provenance", "lm_shot_id")}
    lm = []
    for r, event_ns, mono_ns in _entity_rows(timed, "/lm/record", "lm_shot_id"):
        lid = r["lm_shot_id"]
        lm.append(LmRecord(lid, event_ns, mono_ns, int(r["boot_wallclock_ms"]), rejected=lid in rejected,
                           calibration_missing=cal_sha.get(lid) not in calibration))
    tm = []
    shot_entities = sorted({n.rsplit(":", 1)[0] for n in timed.schema.names
                            if n.startswith("/trackman/") and n.endswith("/shot:shot_key")})
    for entity in shot_entities:
        link_id = link_id_of(entity.split("/")[2])
        for r, event_ns, _ in _entity_rows(timed, entity, "shot_key"):
            tm.append(TmStroke(link_id, r["shot_key"], str(r.get("stroke_id")), event_ns))
    reviews = [Review(r["decision"], r.get("lm_shot_id"), r.get("tm_link_id"), r.get("tm_shot_key"))
               for r, _, _ in _entity_rows(timed, "/shot_review", "decision")]
    recorded = {x.lm_shot_id for x in lm}
    orphans = sorted({r["lm_shot_id"] for r, _, _ in _entity_rows(timed, "/lm/algo/provenance", "lm_shot_id")}
                     - recorded)
    return SessionInputs(lm, tm, reviews, layers, orphans)


TIMED_CONTENTS = ["/lm/record", "/lm/events/impact", "/lm/provenance", "/lm/algo/provenance", "/trackman/**",
                  "/shot_review"]
STATIC_CONTENTS = ["/lm/calibration/**"]


def _first(cell):
    return cell[0] if isinstance(cell, list) and cell else (None if isinstance(cell, list) else cell)


def load_session(client, dataset: str, session_id: str) -> SessionInputs:
    """One session Segment's layers, properties and pairing inputs, read with a rerun CatalogClient.

    Unknown dataset or segment: no layers (not an error). Needs read permission on the dataset."""
    from rerun_bindings import NotFoundError

    try:
        ds = client.get_dataset(dataset)
    except (NotFoundError, LookupError, ValueError):
        return SessionInputs([], [], [], {}, [])
    segments = ds.segment_table().to_arrow_table().to_pylist()
    row = next((r for r in segments if r["rerun_segment_id"] == session_id), None)
    if row is None:
        return SessionInputs([], [], [], {}, [])
    layers = dict(zip(row["rerun_layer_names"], row["rerun_storage_urls"], strict=True))
    properties = {k[len("property:"):]: _first(v) for k, v in row.items() if k.startswith("property:")}
    segment = ds.filter_segments([session_id])
    timed = segment.filter_contents(TIMED_CONTENTS).reader(index="event_time").to_arrow_table()
    static = segment.filter_contents(STATIC_CONTENTS).reader(index=None).to_arrow_table()
    inputs = inputs_from_tables(timed, static, layers)
    inputs.properties = properties
    return inputs


def link_id_of(entity_part: str) -> str:
    """Inverse of rr.escape_entity_path_part for the ids we write (ULIDs and plain tokens pass through)."""
    return entity_part.replace("\\", "")
