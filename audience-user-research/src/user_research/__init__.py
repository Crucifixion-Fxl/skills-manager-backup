"""Host-neutral fixed-operation Audience Personal API client."""

from .client import AudienceClient, AudienceClientConfig, SafeApiError
from .operations import HOST_ATTACHMENT_OPERATIONS, INTERACTIVE_OPERATIONS, Operation

__all__ = [
    "INTERACTIVE_OPERATIONS",
    "HOST_ATTACHMENT_OPERATIONS",
    "AudienceClient",
    "AudienceClientConfig",
    "Operation",
    "SafeApiError",
]
