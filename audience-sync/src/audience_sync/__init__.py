"""Fixed-operation client for the Audience Sync Personal Agent Skill."""

from .client import AudienceSyncClient, AudienceSyncConfig, SafeApiError
from .operations import CONTRACT_REVISION, CONTRACT_STATE, OPERATION_SPECS, Operation
from .project_operations import (
    PERSONAL_KEY_OPERATION_SPECS,
    PROJECT_OPERATION_SPECS,
    PersonalKeyOperation,
    ProjectOperation,
)

__all__ = [
    "AudienceSyncClient",
    "AudienceSyncConfig",
    "CONTRACT_REVISION",
    "CONTRACT_STATE",
    "OPERATION_SPECS",
    "Operation",
    "SafeApiError",
    "PROJECT_OPERATION_SPECS",
    "ProjectOperation",
    "PersonalKeyOperation",
    "PERSONAL_KEY_OPERATION_SPECS",
]
