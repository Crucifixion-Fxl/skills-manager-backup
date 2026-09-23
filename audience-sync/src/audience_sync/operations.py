"""Closed operation names bound to the bundled Audience Sync v2 OpenAPI."""

from __future__ import annotations

from enum import Enum
from typing import Final

from .allowlist import OperationSpec, generate_operation_specs, require_operation_inventory

CONTRACT_REVISION = "audience-sync-v2"
CONTRACT_STATE = "published_operations"


class Operation(str, Enum):
    GET_SYNC_CAPABILITIES = "get_sync_capabilities"
    SEARCH_AUDIENCES = "search_audiences"
    CREATE_AUDIENCE = "create_audience"
    SAVE_AUDIENCE = "save_audience"
    PREVIEW_AUDIENCE = "preview_audience"
    MATERIALIZE_AUDIENCE = "materialize_audience"
    GET_MATERIALIZATION = "get_materialization"
    SYNC_AUDIENCE = "sync_audience"
    GET_AUDIENCE_SYNC = "get_audience_sync"


_GENERATED: Final[dict[str, OperationSpec]] = generate_operation_specs()
require_operation_inventory(_GENERATED, frozenset(operation.value for operation in Operation))

OPERATION_SPECS: Final[dict[Operation, OperationSpec]] = {
    operation: _GENERATED[operation.value] for operation in Operation
}
