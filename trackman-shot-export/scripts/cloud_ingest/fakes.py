"""In-memory resources for tests and local runs; no network, no Hub, no gateway."""
import hashlib
import tempfile
from collections import Counter
from pathlib import Path

from .records import RegistryRow, StatusRecord, filter_rows


class FakeRegistry:
    """Every project's <project>.ingest_registry in one list; rows carry their project."""

    def __init__(self, rows: list[RegistryRow], project: str = "launch_monitor"):
        self.rows, self.project = rows, project  # `project`: where attach() files a new link

    def scan(self, **filters) -> list[RegistryRow]:
        return filter_rows(self.rows, **filters)

    def attach(self, row: RegistryRow, idempotency_key: str) -> RegistryRow:
        """Upsert by link_key with the next revision of the project's table, like hub_direct.HubRegistry."""
        from dataclasses import replace
        mine = [r.revision for r in self.rows if r.project == self.project]
        row = replace(row, revision=max(mine, default=0) + 1, project=self.project)
        self.rows[:] = [r for r in self.rows if r.link_key != row.link_key] + [row]
        self.keys = getattr(self, "keys", []) + [idempotency_key]
        return row


class FakeFetcher:
    """responses[url]: bytes, an exception to raise, or a list consumed one item per call."""

    def __init__(self, responses: dict):
        self.responses, self.calls = responses, Counter()

    def fetch(self, url: str) -> bytes:
        self.calls[url] += 1
        item = self.responses[url]
        if isinstance(item, list):
            item = item.pop(0) if len(item) > 1 else item[0]
        if isinstance(item, BaseException):
            raise item
        return item


class IdempotencyConflict(Exception):
    pass


class FakeSubmitter:
    """Mimics the gateway: same key with different bytes is a 409 conflict. Keeps a copy of every
    submitted file, since the caller's temporary file is gone once submit returns."""

    def __init__(self, fail_layers: dict[str, int] | None = None):
        self.fail_layers = Counter(fail_layers or {})
        self.attempts, self.submits, self.deletes = [], [], []
        self._by_key = {}
        self._copies = Path(tempfile.mkdtemp(prefix="fake-submitter-"))

    def submit(self, path, dataset, recording_id, layer, source_record_id, idempotency_key):
        data = Path(path).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        copy = self._copies / f"{len(self.attempts)}.rrd"
        copy.write_bytes(data)
        entry = dict(path=str(copy), dataset=dataset, recording_id=recording_id, layer=layer,
                     source_record_id=source_record_id, idempotency_key=idempotency_key, sha256=sha)
        self.attempts.append(entry)
        if self._by_key.setdefault(idempotency_key, sha) != sha:
            raise IdempotencyConflict("idempotency-conflict")
        if self.fail_layers[layer] > 0:
            self.fail_layers[layer] -= 1
            raise ConnectionError("gateway unavailable")
        self.submits.append(entry)
        return {"status": "accepted"}

    def delete_layer(self, dataset, recording_id, layer):
        self.deletes.append((dataset, recording_id, layer))
        return {"status": "deleted"}


class FakeStatusWriter:
    def __init__(self):
        self.records: list[StatusRecord] = []

    def write(self, record: StatusRecord) -> None:
        self.records.append(record)

    def states(self, link_id: str | None) -> list[str]:
        return [r.state for r in self.records if r.link_id == link_id]

    def latest(self, link_id: str | None) -> StatusRecord:
        return [r for r in self.records if r.link_id == link_id][-1]


class FakeSessions:
    """sessions[(dataset, session_id)]: SessionInputs; unknown sessions have no layers."""

    def __init__(self, sessions: dict | None = None):
        self.sessions = sessions or {}

    def load(self, dataset: str, session_id: str):
        from .shots import SessionInputs
        return self.sessions.get((dataset, session_id)) or SessionInputs([], [], [], {}, [])
