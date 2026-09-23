"""The complete model-callable User Research operation registry."""

from __future__ import annotations

from enum import Enum
from typing import Final

from .allowlist import OperationSpec, generate_operation_specs

_GENERATED: Final = generate_operation_specs()
Operation = Enum("Operation", {name: name for name in _GENERATED}, type=str)
OPERATION_SPECS: Final[dict[Operation, OperationSpec]] = {
    Operation(name): spec for name, spec in _GENERATED.items()
}
HOST_ATTACHMENT_OPERATIONS: Final[frozenset[Operation]] = frozenset(
    operation for operation, spec in OPERATION_SPECS.items() if spec.response_mode == "attachment"
)
INTERACTIVE_OPERATIONS: Final[frozenset[Operation]] = frozenset(OPERATION_SPECS) - (
    HOST_ATTACHMENT_OPERATIONS
)
SELF_CONTEXT_OPERATION: Final[Operation] = Operation("get_project_personal_key_context")
PROJECT_OPERATIONS: Final[frozenset[Operation]] = frozenset(
    operation
    for operation, spec in OPERATION_SPECS.items()
    if spec.path.startswith("/api/platform/v3/projects/")
)
