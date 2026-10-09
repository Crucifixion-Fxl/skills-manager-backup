"""Hub-direct resources: run P3-P5 straight against rerun hub, without collect-gateway or collect-sdk (#48).

For people importing on their own machine (the trackman-shot-export skill): one Hub read-write token,
rerun-sdk + pyarrow only. Layers go up through the Hub's HTTP upload (`POST /hub/v1/upload` with
X-Layer / X-On-Duplicate: replace); the session's project's two tables, <project>.ingest_registry and
<project>.ingest_status (#50), are read and upserted with the Catalog SDK; GET /hub/v1/datasets/<name> says
which project that is.
Same interfaces as the dagster resources, so pipeline.py runs unchanged. No dagster here.
"""
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import replace
from functools import partial
from pathlib import Path
from urllib.parse import quote

from .adapters.trackman import ADAPTER, MAX_RESPONSE_BYTES, fetch_report, urllib_transport
from .records import (RegistryRow, StatusRecord, filter_rows, registry_rows, registry_schema, registry_table,
                      registry_values, status_schema, status_table, status_values, table_project)
from .shots import SessionInputs, load_session


MAX_REPLY_BYTES = 1024 * 1024  # an upload reply is a small JSON status


class HubError(Exception):
    pass


def _table(client, name: str, schema):
    """The table, created on first use. <project>.ingest_registry and <project>.ingest_status belong to their
    project (#50): the Hub files a new table under the project its name starts with, so a token that may create
    in that project (or owns the table) opens it."""
    from rerun_bindings import NotFoundError

    try:
        return client.get_table(name)
    except (NotFoundError, LookupError, ValueError):
        try:
            return client.create_table(name, schema)
        except RuntimeError as exc:
            raise HubError(f"table {name} does not exist and this token cannot create it "
                           f"({str(exc).splitlines()[0][:200]}); ask the project's owner to open it") from None


def _rows(client, name: str) -> list[dict]:
    from rerun_bindings import NotFoundError

    try:
        table = client.get_table(name)
    except (NotFoundError, LookupError, ValueError):
        return []
    return table.to_arrow_reader().read_all().to_pylist()


class DatasetProjects:
    """dataset -> project through the Hub's GET /hub/v1/datasets/<name>; a dataset never changes project."""

    def __init__(self, http_url: str, token: str, timeout: float = 30.0):
        self.http_url, self.token, self.timeout = http_url.rstrip("/"), token, timeout
        self.known: dict[str, str] = {}

    def __call__(self, dataset: str) -> str:
        if dataset not in self.known:
            request = urllib.request.Request(f"{self.http_url}/hub/v1/datasets/{quote(dataset, safe='')}",
                                             headers={"Authorization": f"Bearer {self.token}"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read(MAX_REPLY_BYTES).decode() or "{}")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise HubError(f"dataset {dataset} is not in the Hub, or this token cannot read it") from None
                detail = exc.read().decode(errors="replace")[:300]
                raise HubError(f"dataset {dataset}: HTTP {exc.code} {detail}") from None
            if not body.get("project"):
                raise HubError(f"dataset {dataset} has no project in the Hub")
            self.known[dataset] = body["project"]
        return self.known[dataset]


class HubRegistry:
    """<project>.ingest_registry read and written directly (the gateway is its other writer), in the project of
    the dataset. Revision = that table's max + 1."""

    def __init__(self, client, project_of):
        self.client, self.project_of = client, project_of

    def scan(self, *, project: str | None = None, **filters) -> list[RegistryRow]:
        if project is None and filters.get("dataset"):
            project = self.project_of(filters["dataset"])
        tables = ([registry_table(project)] if project
                  else sorted(n for n in self.client.table_names() if table_project(n)))
        rows = [row for name in tables for row in registry_rows(_rows(self.client, name), project=table_project(name))]
        return filter_rows(rows, **filters)

    def attach(self, row: RegistryRow, idempotency_key: str) -> RegistryRow:
        """Upsert one link with the next revision of its project's table; returns the row as written."""
        import pyarrow as pa

        project = self.project_of(row.dataset)
        name = registry_table(project)
        revision = max((int(r["revision"]) for r in _rows(self.client, name)), default=0) + 1
        row = replace(row, revision=revision, project=project)
        _table(self.client, name, registry_schema()).upsert(
            pa.RecordBatch.from_pylist([registry_values(row, idempotency_key)], schema=registry_schema()))
        return row


class HubStatus:
    """<project>.ingest_status of the record's project (the dataset's, when the record does not say)."""

    def __init__(self, client, project_of):
        self.client, self.project_of = client, project_of

    def write(self, record: StatusRecord) -> None:
        import pyarrow as pa

        name = status_table(record.project or self.project_of(record.dataset))
        _table(self.client, name, status_schema()).upsert(
            pa.RecordBatch.from_pylist([status_values(record)], schema=status_schema()))


class HubSessions:
    def __init__(self, client):
        self.client = client

    def load(self, dataset: str, session_id: str) -> SessionInputs:
        return load_session(self.client, dataset, session_id)


class HubUploadSubmitter:
    """Layer RRDs through the Hub's HTTP upload, REPLACEing the layer. Object key is content-addressed:
    <recording_id>/<layer>/<sha256>.rrd under the dataset, so a re-upload of the same bytes is a no-op.
    Only into the existing session dataset: this never creates a dataset, so it names no project."""

    def __init__(self, client, http_url: str, token: str, timeout: float = 300.0):
        self.client, self.http_url, self.token, self.timeout = client, http_url.rstrip("/"), token, timeout

    def submit(self, path, dataset, recording_id, layer, source_record_id, idempotency_key):
        body = Path(path).read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        headers = {
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/octet-stream",
            "X-Dataset": dataset, "X-Object-Key": quote(f"{recording_id}/{layer}/{digest}.rrd"),
            "X-Sha256": digest, "X-Source": "trackman", "X-Submission-Id": idempotency_key,
            "X-Session-Id": recording_id, "X-Layer": layer, "X-On-Duplicate": "replace",
        }
        request = urllib.request.Request(f"{self.http_url}/hub/v1/upload", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(MAX_REPLY_BYTES).decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise HubError(f"upload {dataset}/{recording_id}/{layer}: HTTP {exc.code} {detail}") from None
        if result.get("status") != "INDEXED":
            raise HubError(f"upload {dataset}/{recording_id}/{layer}: {result.get('status')} {result.get('error', '')}")
        return result

    def delete_layer(self, dataset, recording_id, layer):
        from rerun_bindings import NotFoundError

        try:
            ds = self.client.get_dataset(dataset)
        except (NotFoundError, LookupError, ValueError):
            return {"status": "absent"}
        ds.unregister(segments_to_drop=[recording_id], layers_to_drop=[layer]).wait()
        return {"status": "deleted"}


class HttpFetcher:
    def __init__(self, timeout_s: float = ADAPTER.timeout_s, max_bytes: int = MAX_RESPONSE_BYTES):
        self.timeout_s, self.max_bytes = timeout_s, max_bytes

    def fetch(self, url: str) -> bytes:
        return fetch_report(url, partial(urllib_transport, max_bytes=self.max_bytes), self.timeout_s)


def resources(grpc_url: str, http_url: str, token: str) -> dict:
    """Everything pipeline.py and cli.py need, over one Hub read-write token."""
    from rerun.catalog import CatalogClient

    client = CatalogClient(grpc_url, token=token)
    project_of = DatasetProjects(http_url, token)
    return dict(registry=HubRegistry(client, project_of), status=HubStatus(client, project_of),
                sessions=HubSessions(client),
                submitter=HubUploadSubmitter(client, http_url, token), fetcher=HttpFetcher(),
                client=client)
