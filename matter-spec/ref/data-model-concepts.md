# Matter Data Model Concepts

## Hierarchy

```
Fabric
  └── Node (addressable entity, has Operational Node ID)
        └── Endpoint (component within node, addressed by endpoint number)
              └── Cluster (functional interface/service)
                    ├── Attributes (data: read/write/subscribe)
                    ├── Commands (invocable actions: client↔server)
                    └── Events (records of occurrences: prioritized log)
```

### Fabric
A security domain — a set of nodes sharing a root CA trust. Each node can belong to multiple fabrics simultaneously. Each fabric has its own Fabric ID, and nodes have independent credentials per fabric.

### Node
The outermost addressable element. Supports the full Matter protocol stack. Once commissioned, has an Operational Node ID (scoped per fabric). A node may have multiple Node IDs (one per fabric).

### Endpoint
An instance of functionality within a node, addressed by endpoint number (uint16). Each endpoint declares one or more Device Types via the Descriptor cluster.

- **Endpoint 0**: Reserved for Root Node device type — system-level clusters
- **Endpoint 1+**: Application endpoints (lights, sensors, etc.)
- **0xFFFF**: Reserved (invalid)

### Cluster
The lowest independent functional element. Defines both server and client sides:
- **Server**: Holds attribute data, generates events, processes commands
- **Client**: Initiates interactions (reads, writes, invokes)

## Cluster Classification

| Classification | Description | Example |
|---------------|-------------|---------|
| **Application** | Application-level functionality | On/Off, Thermostat, Door Lock |
| **Utility** | Configuration and management | Descriptor, Access Control, Binding |

## Device Type Classification

| Type | Description | Scope | Examples |
|------|------------|-------|---------|
| **Node** | Node-level utility | Node | Root Node (0x0016) |
| **Utility** | Endpoint-level utility | Endpoint | Power Source, OTA Requestor, Bridged Node |
| **Simple** | Persistent, independent local control | Endpoint | On/Off Light, Thermostat, Door Lock |
| **Dynamic** | Intelligent/supervisory services | Endpoint | Aggregator |

### Device Type Composition
A device type may be **composed** — requiring child endpoints with specific device types. Example: Refrigerator requires at least one Temperature Controlled Cabinet child endpoint.

### Primary Device Type
When an endpoint supports multiple device types, one is designated **Primary Device Type** — the manufacturer's chosen main function.

### Device Type Revision
An unsigned integer (starting at 1) tracking the revision of the device type definition.

## Features and FeatureMap

Each cluster MAY define optional features (up to 32 per FeatureMap attribute).

- **FeatureMap** (attribute 0xFFFC, type map32): Each bit = one optional feature
- Bit = 1 → feature supported; Bit = 0 → not supported
- Feature codes (e.g., `HEAT`, `LT`, `TL`) are used in conformance expressions
- Elements tied to a feature become mandatory/optional based on FeatureMap bits
- All undefined bits SHALL be 0

## Global Attributes

Every cluster instance supports these attributes:

| ID | Name | Type | Description |
|----|------|------|-------------|
| 0xFFFD | ClusterRevision | uint16 | Spec revision supported (starts at 1) |
| 0xFFFC | FeatureMap | map32 | Supported optional features bitmap |
| 0xFFFB | AttributeList | list[attrib-id] | IDs of all supported attributes |
| 0xFFF9 | AcceptedCommandList | list[command-id] | Client→server commands supported |
| 0xFFF8 | GeneratedCommandList | list[command-id] | Server→client commands supported |

**Deprecated:** EventList (0xFFFA) — marked as Deprecated (D).

### Global Fields

| ID | Name | Type | Description |
|----|------|------|-------------|
| 0xFE | FabricIndex | fabric-idx | Identifies associated fabric for fabric-scoped data |

## Attribute Qualities

| Quality | Code | Description |
|---------|------|-------------|
| Nullable | X | Value can be NULL (unknown/undefined) |
| Non-volatile | N | Persists across restarts |
| Fixed | F | Read-only, changes only on installation/reset/upgrade |
| Scene | S | Can be part of a scene (uint/bool types ≤ 4 bytes) |
| Changes Omitted | C | Fast-changing data, does not trigger subscription deltas |
| Quieter Reporting | Q | Fluctuating data, some deltas suppressed |
| Reportable | P | Supports reporting configuration |
| Atomic | T | Must be written in atomic write transaction |
| Singleton | I | One cluster instance per node for device type |
| Diagnostics | K | Verbose diagnostics, may skip wildcard expansion |

## Access Control Model

### Privilege Levels (ascending)

| Level | Name | Description |
|-------|------|-------------|
| 1 | **View** | Read access (SHALL NOT alter settings/config data) |
| 3 | **Operate** | Read + Write + Invoke (primary device function) |
| 4 | **Manage** | Operate + modify configuration |
| 5 | **Administer** | Manage + modify ACL itself |

Higher privilege implies all lower privileges. Administer can do everything.

### Default Privileges (when not explicitly specified)
- Read → View
- Write → Operate
- Invoke (request commands) → Operate
- Response commands → no privilege defined

### Access Modifiers
- **F** (Fabric-Scoped): Data associated with specific fabric
- **S** (Fabric-Sensitive): Sensitive data within fabric scope
- **T** (Timed): Requires timed interaction (prevents replay attacks)

## Fabric-Scoped Data

Data exclusively associated with a particular fabric:
- Each entry has a **FabricIndex** field identifying the associated fabric
- **Fabric-filtered reads**: Only entries matching the accessing fabric are visible
- Interactions SHALL NOT modify fabric-scoped data across different fabrics
- Limited to: fabric-scoped struct lists, fabric-sensitive events

Example: Access Control List entries are fabric-scoped — each fabric sees only its own ACL entries.

## MEI (Manufacturer Extensible Identifier)

Used for IDs of: device types, clusters, attributes, fields, events, commands.

Format: 32-bit identifier
- **Standard range** (0x0000_0000–0x0000_FFFF): CSA-defined
- **Manufacturer range** (0xVVVV_0000–0xVVVV_FFFF): Vendor-specific (VVVV = Vendor ID)

Allows vendors to define custom clusters, attributes, and commands within their vendor ID namespace.

## Base Device Type Requirements

Every Matter device SHALL implement the **Root Node** device type on **Endpoint 0**, which requires:

| Cluster | Purpose |
|---------|---------|
| Descriptor | Describes endpoint's device types and cluster lists |
| Access Control | Manages ACL entries |
| Basic Information | Node metadata (vendor, product, SW version) |
| General Commissioning | Commissioning lifecycle management |
| Network Commissioning | Network interface configuration |
| Administrator Commissioning | Multi-admin commissioning windows |
| Operational Credentials | NOC and fabric management |
| Group Key Management | Group key administration |
| General Diagnostics | Boot reason, network interfaces, faults |
