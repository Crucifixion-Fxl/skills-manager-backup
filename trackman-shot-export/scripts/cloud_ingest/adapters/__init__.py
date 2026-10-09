from dataclasses import dataclass


@dataclass(frozen=True)
class Adapter:
    """The four things a cloud-ingest source declares (docs/cloud-ingest/index.html §7)."""

    source: str
    url_semantics: str
    allowed_hosts: tuple[str, ...]
    parser_version: str
    attribution: str
    layer: str
    timeout_s: float = 60.0
    max_attempts: int = 3
    backoff_s: float = 1.0
