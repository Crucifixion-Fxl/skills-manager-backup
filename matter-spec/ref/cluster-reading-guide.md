# How to Read a Matter Cluster Specification

Every cluster spec document follows a standard structure. Here's how to read one.

## Document Sections

### 1. Revision History
Table of revisions. The highest revision number = the `ClusterRevision` global attribute value.

### 2. Classification
- **Hierarchy**: Base (standalone) or Derived (extends a base cluster)
- **Role**: Application or Utility
- **Scope**: Endpoint or Node
- **PICS Code**: Short identifier used in test plans (e.g., `OO` for On/Off)

### 3. Cluster ID
Hexadecimal identifier (e.g., `0x0006` for On/Off). Used in paths and discovery.

### 4. Features Table
Optional features in the FeatureMap:

| Bit | Code | Name | Conformance | Description |
|-----|------|------|-------------|-------------|
| 0 | LT | Lighting | O | Lighting behavior support |

- **Bit**: Position in FeatureMap (map32)
- **Code**: Short name used in conformance expressions throughout the document
- **Conformance**: M/O/P/D/X — whether the feature itself is mandatory or optional

### 5. Data Types Section
Custom types used by the cluster:
- **Enums**: Named value sets (e.g., `enum8` with specific values)
- **Bitmaps**: Named bit fields (e.g., `map8` with specific bits)
- **Structs**: Composite types with named fields

### 6. Attributes Table

| ID | Name | Type | Constraint | Quality | Fallback | Access | Conformance |
|----|------|------|-----------|---------|----------|--------|-------------|

**Column meanings:**
- **ID**: Attribute ID (hex)
- **Type**: Data type (uint8, bool, list[...], custom struct, etc.)
- **Constraint**: Valid range (e.g., `0 to 254`, `max 100`, `desc`)
- **Quality**: N=Non-volatile, F=Fixed, X=Nullable, S=Scene, C=ChangesOmitted, Q=Quieter, T=Atomic
- **Fallback**: Default value when attribute is not explicitly set
- **Access**: R=Read, W=Write, V/O/M/A=privilege level, F=Fabric-scoped, T=Timed
- **Conformance**: M/O/P/D/X or conditional expression (see conformance-notation.md)

### 7. Commands Table

| ID | Name | Direction | Response | Access | Quality | Conformance |
|----|------|-----------|----------|--------|---------|-------------|

- **Direction**: client⇒server or server⇒client
- **Response**: Name of response command (or "Y" for default status response)
- Each command has a separate section listing its fields/parameters

### 8. Events Table

| ID | Name | Priority | Quality | Access | Conformance |
|----|------|----------|---------|--------|-------------|

- **Priority**: DEBUG, INFO, or CRITICAL
- Events have fields listed in a sub-table

## Reading Tips

1. **Start with Features**: Check the FeatureMap to understand which optional capabilities exist
2. **Follow Conformance**: An attribute marked `HEAT` means it only exists when the HEAT feature is supported
3. **Check Quality column**: `X` (nullable) means you must handle NULL values; `N` means you must persist the value
4. **Access determines privilege**: `RW O` means the attribute needs at least Operate privilege to write
5. **Fallback ≠ Reset value**: Fallback is the value used when no other value is available
6. **Derived clusters**: If derived from a base (e.g., OPSTATE), the base defines the common structure; the derived cluster adds/overrides specific elements
7. **Server vs Client**: Most device implementations only need the server side (holding data, processing commands). The client side is for controllers/switches.

## Example: Reading On/Off Cluster (0x0006)

```
Features:
  Bit 0: LT (Lighting) - O
  Bit 1: DF (DeadFrontBehavior) - O
  Bit 2: OO (OffOnly) - O

Attributes:
  0x0000: OnOff        bool   R V  M     ← Always mandatory, readable
  0x4000: GlobalSceneControl bool R V LT  ← Only with Lighting feature
  0x4001: OnTime       uint16 R V LT     ← Only with Lighting feature
  0x4002: OffWaitTime  uint16 R V LT     ← Only with Lighting feature
  0x4003: StartUpOnOff enum8  RW M LT    ← Writable, Manage privilege, Lighting only

Commands:
  0x00: Off            client⇒server M   ← Always mandatory
  0x01: On             client⇒server M   ← Always mandatory
  0x02: Toggle         client⇒server M   ← Always mandatory
  0x40: OffWithEffect  client⇒server LT  ← Only with Lighting feature
  0x41: OnWithRecallGlobalScene client⇒server LT
  0x42: OnWithTimedOff client⇒server LT
```
