"""Registry rows and ingest status records (model.html section 11). No dagster: shared with the skill."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .attribution import parse_time

# Every project has its own pair of tables, `<project>.ingest_registry` and `<project>.ingest_status` (#50):
# the prefix is the full project name, and the tables belong to that project like its datasets do.
REGISTRY_TABLE = "ingest_registry"
STATUS_TABLE = "ingest_status"
_INDEX = {"rerun:is_table_index": "true"}


def registry_table(project: str) -> str:
    if not project:
        raise ValueError("a registry table needs its project")
    return f"{project}.{REGISTRY_TABLE}"


def status_table(project: str) -> str:
    if not project:
        raise ValueError("a status table needs its project")
    return f"{project}.{STATUS_TABLE}"


def table_project(name: str, suffix: str = REGISTRY_TABLE) -> str | None:
    """`launch_monitor` for `launch_monitor.ingest_registry`; None for any other name."""
    project, dot, rest = name.rpartition(".")
    return project if dot and project and rest == suffix else None


@dataclass(frozen=True)
class RegistryRow:
    source: str
    dataset: str
    recording_id: str
    link_id: str
    revision: int
    active: bool
    url: str | None = field(default=None, repr=False)
    window_start: datetime | None = None
    window_end: datetime | None = None
    project: str = ""  # the project whose registry table the row is in; not a column

    @property
    def link_key(self) -> str:
        return f"{self.source}:{self.dataset}:{self.recording_id}:{self.link_id}"


@dataclass(frozen=True)
class StatusRecord:
    source: str
    dataset: str
    recording_id: str
    link_id: str | None  # None: the experiment's layer as a whole
    state: str  # PULLING | TRANSFORMING | REGISTERED | QUARANTINED
    revision: int
    detail: str = ""
    idempotency_key: str = ""
    project: str = ""  # picks the table, <project>.ingest_status; not a column

    @property
    def status_key(self) -> str:
        return f"{self.source}:{self.dataset}:{self.recording_id}:{self.link_id or '*'}"


def filter_rows(rows, *, min_revision=0, source=None, dataset=None, recording_id=None,
                project=None) -> list[RegistryRow]:
    return sorted((r for r in rows if r.revision > min_revision
                   and (project is None or r.project == project)
                   and (source is None or r.source == source)
                   and (dataset is None or r.dataset == dataset)
                   and (recording_id is None or r.recording_id == recording_id)),
                  key=lambda r: r.revision)


def as_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return parse_time(value)


def registry_rows(records: list[dict], project: str = "") -> list[RegistryRow]:
    """<project>.ingest_registry table rows (dicts) as RegistryRow."""
    return [RegistryRow(source=r["source"], dataset=r["dataset"], recording_id=r["recording_id"],
                        link_id=r["link_id"], revision=int(r["revision"]), active=bool(r["active"]),
                        url=r.get("url") or None, window_start=as_time(r.get("window_start")),
                        window_end=as_time(r.get("window_end")), project=project) for r in records]


def registry_schema():
    """Same columns and types as collect-gateway's registrySchema() (internal/hub/rerun.go)."""
    import pyarrow as pa
    ts = pa.timestamp("ms", tz="UTC")
    return pa.schema([
        pa.field("link_key", pa.string(), nullable=False, metadata=_INDEX),
        pa.field("source", pa.string()), pa.field("dataset", pa.string()), pa.field("recording_id", pa.string()),
        pa.field("link_id", pa.string()), pa.field("url", pa.string()), pa.field("window_start", ts),
        pa.field("window_end", ts), pa.field("active", pa.bool_()), pa.field("revision", pa.int64()),
        pa.field("idempotency_key", pa.string()), pa.field("updated_at", ts)])


def registry_values(row: RegistryRow, idempotency_key: str) -> dict:
    return dict(link_key=row.link_key, source=row.source, dataset=row.dataset, recording_id=row.recording_id,
                link_id=row.link_id, url=row.url, window_start=row.window_start, window_end=row.window_end,
                active=row.active, revision=row.revision, idempotency_key=idempotency_key,
                updated_at=datetime.now(timezone.utc))


def status_schema():
    import pyarrow as pa
    return pa.schema([
        pa.field("status_key", pa.string(), nullable=False, metadata=_INDEX),
        pa.field("source", pa.string()), pa.field("dataset", pa.string()),
        pa.field("recording_id", pa.string()), pa.field("link_id", pa.string()),
        pa.field("state", pa.string()), pa.field("revision", pa.int64()),
        pa.field("detail", pa.string()), pa.field("idempotency_key", pa.string()),
        pa.field("updated_at", pa.timestamp("us", tz="UTC")),
    ])


def status_values(record: StatusRecord) -> dict:
    return dict(status_key=record.status_key, source=record.source, dataset=record.dataset,
                recording_id=record.recording_id, link_id=record.link_id, state=record.state,
                revision=record.revision, detail=record.detail, idempotency_key=record.idempotency_key,
                updated_at=datetime.now(timezone.utc))
