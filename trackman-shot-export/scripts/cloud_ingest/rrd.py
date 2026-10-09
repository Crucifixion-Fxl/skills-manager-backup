import math
from pathlib import Path
from typing import Iterable

import rerun as rr

from .adapters.trackman import Shot
from .attribution import parse_time

TIMELINE = "event_time"
SPACES = (("Measurement", "raw"), ("NormalizedMeasurement", "normalized"))


def _stream(application_id: str, recording_id: str, path: Path) -> rr.RecordingStream:
    rec = rr.RecordingStream(application_id, recording_id=recording_id)
    rec.save(path)
    rr.set_log_time_enabled(False, recording=rec)
    rr.set_log_tick_enabled(False, recording=rec)
    return rec


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trackman_rrd(experiment_id: str, shots: Iterable[Shot], path: str | Path) -> Path:
    """Per-shot numeric metrics and ball trajectories on the event_time timeline; API values unconverted."""
    path = Path(path)
    rec = _stream("trackman", experiment_id, path)
    for shot in sorted(shots, key=lambda s: (parse_time(s.time), s.link_id, s.shot_key)):
        rec.set_time(TIMELINE, timestamp=parse_time(shot.time))
        base = f"trackman/{rr.escape_entity_path_part(shot.link_id)}"
        rec.log(f"{base}/shot", rr.AnyValues(
            stroke_id=str(shot.stroke_id), shot_key=shot.shot_key,
            group_id=None if shot.group_id is None else str(shot.group_id),
            club=None if shot.club is None else str(shot.club)))
        for variant, space in SPACES:
            for name, value in (shot.measurements.get(variant) or {}).items():
                if _is_number(value):
                    rec.log(f"{base}/{space}/{rr.escape_entity_path_part(name)}", rr.Scalars(value))
            ball = shot.ball_trajectory(variant)
            if ball:
                # Points3D is float32 for the viewer; X/Y/Z keep the exact API values.
                xyz = {axis: [float(p[axis]) for p in ball] for axis in ("X", "Y", "Z")}
                rec.log(f"{base}/{space}/ball_trajectory",
                        rr.Points3D(list(zip(xyz["X"], xyz["Y"], xyz["Z"], strict=True))), rr.AnyValues(**xyz))
    rec.disconnect()
    return path
