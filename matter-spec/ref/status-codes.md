# Matter Interaction Model Status Codes

Global status codes used in all Matter interactions.

## Status Code Table

| Hex | Name | Description |
|-----|------|-------------|
| 0x00 | SUCCESS | Operation successful |
| 0x01 | FAILURE | Operation not successful (generic) |
| 0x7D | INVALID_SUBSCRIPTION | Subscription ID is not active |
| 0x7E | UNSUPPORTED_ACCESS | Sender lacks authorization/access |
| 0x7F | UNSUPPORTED_ENDPOINT | Endpoint not supported on node |
| 0x80 | INVALID_ACTION | Malformed action, missing/invalid fields |
| 0x81 | UNSUPPORTED_COMMAND | Command ID not supported on cluster instance |
| 0x85 | INVALID_COMMAND | Command malformed, missing/invalid fields |
| 0x86 | UNSUPPORTED_ATTRIBUTE | Attribute/field/list entry does not exist |
| 0x87 | CONSTRAINT_ERROR | Value out of range or reserved |
| 0x88 | UNSUPPORTED_WRITE | Attempt to write a read-only attribute |
| 0x89 | RESOURCE_EXHAUSTED | Insufficient resources |
| 0x8B | NOT_FOUND | Data field or entry not found |
| 0x8C | UNREPORTABLE_ATTRIBUTE | Reports cannot be issued for this attribute |
| 0x8D | INVALID_DATA_TYPE | Undefined or invalid data type |
| 0x8F | UNSUPPORTED_READ | Attempt to read a write-only attribute |
| 0x92 | DATA_VERSION_MISMATCH | Cluster data version doesn't match request |
| 0x94 | TIMEOUT | Transaction timed out |
| 0x9B | UNSUPPORTED_NODE | Node ID not supported |
| 0x9C | BUSY | Receiver busy processing another action |
| 0x9D | ACCESS_RESTRICTED | Permitted by ACL but restricted by ARL |
| 0xC3 | UNSUPPORTED_CLUSTER | Cluster not supported on endpoint |
| 0xC5 | NO_UPSTREAM_SUBSCRIPTION | Proxy lacks upstream subscription |
| 0xC6 | NEEDS_TIMED_INTERACTION | Attribute/command requires timed interaction |
| 0xC7 | UNSUPPORTED_EVENT | Event ID not supported on cluster |
| 0xC8 | PATHS_EXHAUSTED | Too many paths in request |
| 0xC9 | TIMED_REQUEST_MISMATCH | TimedRequest field doesn't match context |
| 0xCA | FAILSAFE_REQUIRED | Fail-safe context required but not active |
| 0xCB | INVALID_IN_STATE | Cannot handle request in current state |
| 0xCC | NO_COMMAND_RESPONSE | CommandDataIB missing response |
| 0xCD | TERMS_AND_CONDITIONS_CHANGED | Node requires updated TC acceptance |
| 0xCE | MAINTENANCE_REQUIRED | User must visit maintenance URL |
| 0xCF | DYNAMIC_CONSTRAINT_ERROR | Runtime validation rejected value |
| 0xD0 | ALREADY_EXISTS | Entity/identifier already exists |
| 0xD1 | INVALID_TRANSPORT_TYPE | Transport type not valid for element |

## Common Error Scenarios

| Scenario | Status Code |
|----------|-------------|
| Read nonexistent attribute | UNSUPPORTED_ATTRIBUTE (0x86) |
| Write to read-only attribute | UNSUPPORTED_WRITE (0x88) |
| Write value out of range | CONSTRAINT_ERROR (0x87) |
| Send command to wrong cluster | UNSUPPORTED_CLUSTER (0xC3) |
| Send unknown command | UNSUPPORTED_COMMAND (0x81) |
| Timed write without Timed Request | NEEDS_TIMED_INTERACTION (0xC6) |
| Timed Request timeout expired | TIMEOUT (0x94) |
| ACL denies access | UNSUPPORTED_ACCESS (0x7E) |
| Write with wrong data version | DATA_VERSION_MISMATCH (0x92) |
| Door lock command without timed invoke | NEEDS_TIMED_INTERACTION (0xC6) |
| Commissioning without fail-safe | FAILSAFE_REQUIRED (0xCA) |

## Obsolete Names

Some status codes have old names still seen in legacy code:

| Current Name | Obsolete Name |
|-------------|---------------|
| UNSUPPORTED_ACCESS | NOT_AUTHORIZED |
| UNSUPPORTED_COMMAND | UNSUP_COMMAND |
| INVALID_COMMAND | INVALID_FIELD |
| CONSTRAINT_ERROR | INVALID_VALUE |
| UNSUPPORTED_WRITE | READ_ONLY |
| RESOURCE_EXHAUSTED | INSUFFICIENT_SPACE |
