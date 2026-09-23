# Matter Conformance Notation

The conformance system defines whether a cluster element (attribute, command, event, feature) is mandatory, optional, or conditional. This is the single most confusing aspect of the spec for new developers.

## Basic Conformance Codes

| Code | Name | Meaning |
|------|------|---------|
| **M** | Mandatory | SHALL be supported. Part of the base feature set. |
| **O** | Optional | MAY be supported. No dependencies beyond the mandatory set. |
| **P** | Provisional | Provisional — subject to change in future revisions. |
| **D** | Deprecated | Legacy item. MAY occur in older implementations. |
| **X** | Disallowed | SHALL NOT be supported. Used in cluster derivation. |

## Feature Code Operands

Feature codes from the cluster's FeatureMap act as boolean operands:
- A feature code evaluates to **TRUE** if the feature is supported, **FALSE** otherwise.
- Feature codes are short uppercase names defined in each cluster's FeatureMap table.

Example: In Thermostat cluster, `HEAT` = Heating feature, `COOL` = Cooling feature.

## Boolean Operators

| Operator | Meaning | Example |
|----------|---------|---------|
| `&` | AND | `HEAT & COOL` — both Heating and Cooling supported |
| `\|` | OR | `HEAT \| COOL` — either Heating or Cooling supported |
| `^` | XOR | `HEAT ^ COOL` — exactly one of them supported |
| `!` | NOT | `!HEAT` — Heating NOT supported |

**Precedence** (highest to lowest): `!` > `&` > `^` > `|`

## Conditional Conformance Expressions

**Mandatory if condition:**
```
AB        → SHALL be supported if feature/element AB is supported
AB & CD   → SHALL be supported if both AB and CD are supported
AB | CD   → SHALL be supported if either AB or CD is supported
!AB       → SHALL be supported if AB is NOT supported
```

**Optional if condition (bracket notation):**
```
[AB]      → MAY be supported ONLY IF feature AB is supported
[AB & CD] → MAY be supported ONLY IF both AB and CD are supported
```

Brackets `[]` serve dual purpose: grouping (like parentheses) AND indicating optionality.

## Otherwise Lists

Comma-separated entries create "otherwise" chains, evaluated left to right:

```
AB, O     → Mandatory if AB is supported; otherwise Optional
AB, CD, O → Mandatory if AB; otherwise Mandatory if CD; otherwise Optional
AB, X     → Mandatory if AB; otherwise Disallowed
[AB], O   → Optional if AB; otherwise Optional (always optional, but only meaningful with AB)
AB, [CD]  → Mandatory if AB; otherwise Optional only if CD; otherwise Disallowed
```

## Value Conformance

Conformance can depend on attribute/field values:

| Expression | Meaning |
|------------|---------|
| `EF == x` | TRUE if element EF equals value x |
| `EF != x` | TRUE if element EF does not equal value x |
| `x <= EF & EF <= y` | TRUE if EF is within range [x, y] inclusive |

Example: `LevelControl == 1, O` → Mandatory if LevelControl equals 1, otherwise Optional.

## Choice Conformance

Defines mutually exclusive or minimum-selection groups using suffix notation:

| Notation | Meaning |
|----------|---------|
| `O.a` | Optional, belongs to choice group "a" |
| `O.a+` | At least one element from group "a" SHALL be supported |
| `O.a1+` | If any from group "a" is supported, at least 1 SHALL be |
| `O.a2+` | If any from group "a" is supported, at least 2 SHALL be |

Example — Power Source features:
```
WIRED   O.a+    ─┐
BAT     O.a+    ─┤ At least one of WIRED or BAT SHALL be supported
                 ─┘
```

## Constraint Notation

Defines valid ranges for attribute values:

| Constraint | Meaning |
|------------|---------|
| `min 0` | Minimum value is 0 |
| `max 100` | Maximum value is 100 |
| `0 to 100` | Value range 0–100 inclusive |
| `all` | All values of the data type are valid |
| `desc` | See attribute description for constraints |
| `max 254` | For list attributes, maximum number of entries |

## Quality Notation

Quality codes appear in the Quality (Q) column:

| Code | Name | Meaning |
|------|------|---------|
| **X** | Nullable | Value can be NULL |
| **N** | Non-volatile | Persists across restarts |
| **F** | Fixed | Read-only, changes only on installation/reset |
| **S** | Scene | Can be set via Scenes cluster |
| **P** | Reportable | Supports reporting configuration |
| **Q** | Quieter Reporting | Fluctuating data, some deltas skip reporting |
| **C** | Changes Omitted | Fast-changing, does not trigger subscription deltas |
| **T** | Atomic | Requires atomic write transaction |
| **I** | Singleton | One cluster instance per node for this device type |
| **K** | Diagnostics | Verbose cluster, may be omitted from wildcard reads |

## Access Notation

Access codes appear in the Access column:

| Code | Meaning |
|------|---------|
| **R** | Read access |
| **W** | Write access |
| **R[W]** | Read always, Write optionally |
| **V** | View privilege required |
| **O** | Operate privilege required |
| **M** | Manage privilege required |
| **A** | Administer privilege required |
| **F** | Fabric-scoped access |
| **S** | Fabric-sensitive access |
| **T** | Timed interaction required |

Example: `R V` = Readable with View privilege. `RW O T` = Read/Write with Operate privilege, Timed interaction required.

## Conformance Inheritance

If an element has no explicit conformance (blank column), it inherits from the next highest element in the hierarchy:
- Struct field → inherits from parent attribute
- Attribute → inherits from cluster
- Cluster → inherits from device type

## Common Patterns

```
M                    → Always mandatory
O                    → Always optional
HEAT, O              → Mandatory with HEAT feature, otherwise optional
[HEAT]               → Optional, only allowed with HEAT feature
HEAT & COOL          → Mandatory when both features present
HEAT | COOL, O       → Mandatory if either feature, otherwise optional
!LT, X              → Disallowed unless LT feature is absent (mandatory if no LT)
O.a+                 → Part of a choice set, at least one required
```
