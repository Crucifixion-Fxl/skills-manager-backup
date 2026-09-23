# Matter Interaction Model

The Interaction Model (IM) defines how nodes communicate over the data model.

## Core Concepts

- **Interaction**: A complete sequence of transactions (e.g., a subscription lifecycle)
- **Transaction**: A sequence of actions (e.g., request + response)
- **Action**: A single logical message from source to destination(s)

## Path Concept

A path identifies one or more elements in the data model:

```
Path = Target + Cluster + Element
Target = Node + Endpoint  OR  Group
Element = Attribute | Event | Command
```

### Path Types

| Type | Description |
|------|-------------|
| **Concrete** | No wildcards — points to exactly one element |
| **Wildcard** | Wildcard endpoint and/or cluster — expands to multiple elements |
| **Group** | Uses Group ID instead of Node+Endpoint — expands to group members |

### Attribute Path Nesting
Attribute paths can reference nested data:
```
Endpoint → Cluster → Attribute → Struct Field → List Entry
```

### Wildcard Path Flags (Read/Subscribe)

| Bit | Flag | Effect |
|-----|------|--------|
| 0 | WildcardSkipRootNode | Skip Endpoint 0 |
| 1 | WildcardSkipGlobalAttributes | Skip global attribute lists |
| 2 | WildcardSkipAttributeList | Skip AttributeList (0xFFFB) |
| 4 | WildcardSkipCommandLists | Skip AcceptedCommandList, GeneratedCommandList |
| 5 | WildcardSkipCustomElements | Skip manufacturer-specific (MEI) clusters/attributes |
| 6 | WildcardSkipFixedAttributes | Skip Fixed (F) quality attributes |
| 7 | WildcardSkipChangesOmittedAttributes | Skip Changes Omitted (C) quality attributes |
| 8 | WildcardSkipDiagnosticsClusters | Skip Diagnostics (K) quality clusters |

## Interaction Types

### 1. Read Interaction

Request attribute and/or event data. Simplest interaction.

```
Client ──── Read Request ────► Server
Client ◄─── Report Data ───── Server
```

- Supports concrete, wildcard, and group paths (groups not for events)
- DataVersionFilters: Skip unchanged clusters for efficiency

### 2. Subscribe Interaction

Establish continuous subscription to attribute/event changes.

**Initiation (Subscribe Transaction):**
```
Client ──── Subscribe Request ──► Server    (paths, min/max intervals)
Client ◄─── Report Data (priming) ── Server (initial data snapshot)
Client ──── Status Response ────► Server    (acknowledge priming)
Client ◄─── Subscribe Response ─── Server   (SubscriptionId, final MaxInterval)
```

**Ongoing (Report Transactions):**
```
Client ◄─── Report Data ─────── Server     (changed attributes/events)
Client ──── Status Response ──► Server      (acknowledge, if not suppressed)
```

**Interval Parameters:**
- **MinIntervalFloor** (subscriber): Lower bound for reporting interval (seconds)
- **MaxIntervalCeiling** (subscriber): Upper bound for max interval (seconds)
- **MaxInterval** (publisher): Final negotiated max interval (in Subscribe Response)

**Keep-Alive:** Publisher MUST send Report Data every MaxInterval (can be empty). If subscriber receives nothing within MaxInterval, subscription is considered lost.

**Event Delivery:**
- Queued and buffered, delivered in order
- **IsUrgent = TRUE**: Triggers immediate Report Data (subject to MinInterval)
- **IsUrgent = FALSE**: Delivered opportunistically or at MaxInterval

### 3. Write Interaction

Modify attribute values.

**Untimed Write:**
```
Client ──── Write Request ────► Server
Client ◄─── Write Response ──── Server    (per-attribute status)
```

**Timed Write (for security-sensitive attributes):**
```
Client ──── Timed Request ────► Server    (declares timeout in ms)
Client ◄─── Status Response ─── Server    (timeout clock starts)
Client ──── Write Request ────► Server    (must arrive before timeout)
Client ◄─── Write Response ──── Server
```

- Groupcast writes: SuppressResponse = TRUE (no response expected)
- DataVersion field: Optimistic concurrency — write rejected if version mismatch

### 4. Invoke Interaction

Execute cluster commands.

**Untimed Invoke:**
```
Client ──── Invoke Request ───► Server    (CommandDataIB with command + args)
Client ◄─── Invoke Response ─── Server    (CommandDataIB or CommandStatusIB)
```

**Timed Invoke (for security-sensitive commands):**
```
Client ──── Timed Request ────► Server    (declares timeout in ms)
Client ◄─── Status Response ─── Server    (timeout clock starts)
Client ──── Invoke Request ───► Server    (must arrive before timeout)
Client ◄─── Invoke Response ─── Server
```

- Groupcast invoke: Single CommandDataIB only, no response expected

## Timed Interactions

**Purpose:** Prevent replay attacks and ensure time-bounded execution for security-sensitive operations (e.g., door lock, garage door).

**Mechanism:**
1. Client sends Timed Request with timeout (uint16, milliseconds, max ~65.5s)
2. Server responds with Status Response — timeout clock starts
3. Client sends Write/Invoke Request within timeout window
4. If request doesn't arrive in time → TIMEOUT (0x94) status

**Rules:**
- Timed transactions are **unicast only** (no groupcast)
- Server SHALL support timed interaction for ALL writeable attributes and ALL commands
- Some attributes/commands REQUIRE timed interaction (marked with `T` in Access column)
- TimedRequest field mismatch → TIMED_REQUEST_MISMATCH (0xC9)

## Group Communication

- Group paths expand to all endpoints that are members of the group
- Supported for Write and Invoke (not Read or Subscribe)
- No responses in groupcast mode (SuppressResponse = TRUE)
- Timed transactions cannot use group paths

## Data Versioning

Each cluster instance has a **data version** (uint32):
- Included in Report Data responses
- Used in DataVersionFilters for Read/Subscribe to skip unchanged data
- Used in Write Request for optimistic concurrency control
- Version mismatch → DATA_VERSION_MISMATCH (0x92) — write rejected
