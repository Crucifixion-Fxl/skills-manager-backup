# Matter Data Types

## Base Data Types

### Discrete Types

| Type | Size | Range | Description |
|------|------|-------|-------------|
| bool | 1B | FALSE(0), TRUE(1) | Boolean |
| map8 | 1B | 8 bits | 8-bit bitmap |
| map16 | 2B | 16 bits | 16-bit bitmap |
| map32 | 4B | 32 bits | 32-bit bitmap (FeatureMap) |
| map64 | 8B | 64 bits | 64-bit bitmap |

### Unsigned Integers

| Type | Size | Non-nullable Range | Nullable Range |
|------|------|-------------------|----------------|
| uint8 | 1B | 0–255 | 0–254 |
| uint16 | 2B | 0–65535 | 0–65534 |
| uint24 | 3B | 0–16777215 | 0–16777214 |
| uint32 | 4B | 0–4294967295 | 0–4294967294 |
| uint48 | 6B | 0–2^48-1 | 0–2^48-2 |
| uint64 | 8B | 0–2^64-1 | 0–2^64-2 |

### Signed Integers

| Type | Size | Non-nullable Range | Nullable Range |
|------|------|-------------------|----------------|
| int8 | 1B | -128–127 | -127–127 |
| int16 | 2B | -32768–32767 | -32767–32767 |
| int32 | 4B | -2^31–2^31-1 | -(2^31-1)–2^31-1 |
| int64 | 8B | -2^63–2^63-1 | -(2^63-1)–2^63-1 |

### Floating Point

| Type | Size | Description |
|------|------|-------------|
| single | 4B | IEEE 754 single-precision |
| double | 8B | IEEE 754 double-precision |

### Composite Types

| Type | Description |
|------|-------------|
| octstr | Octet string (byte array), 0–65534 bytes |
| string | UTF-8 character string, 0–65534 bytes |
| list | Collection of same-type entries, 0–65534 entries |
| struct | Ordered sequence of named fields (field ID = uint, starting at 0) |

## Derived Types — Enumerations

| Type | Base | Size | Description |
|------|------|------|-------------|
| enum8 | uint8 | 1B | 8-bit enumeration (up to 256 values) |
| enum16 | uint16 | 2B | 16-bit enumeration (up to 65536 values) |
| priority | enum8 | 1B | DEBUG(0), INFO(1), CRITICAL(2) |
| status | enum8 | 1B | Success/error status codes |

## Derived Types — Identifiers

| Type | Base | Size | Description |
|------|------|------|-------------|
| node-id | uint64 | 8B | Node identifier (fabric-scoped) |
| group-id | uint16 | 2B | Group identifier (fabric-scoped) |
| endpoint-no | uint16 | 2B | Endpoint number (0xFFFF invalid) |
| cluster-id | uint32 | 4B | Cluster identifier (MEI format) |
| attrib-id | uint32 | 4B | Attribute identifier (MEI format) |
| command-id | uint32 | 4B | Command identifier (MEI format) |
| event-id | uint32 | 4B | Event identifier (MEI format) |
| field-id | uint32 | 4B | Struct field identifier (MEI format) |
| vendor-id | uint16 | 2B | Vendor identifier |
| devtype-id | uint32 | 4B | Device type identifier (MEI format) |
| fabric-id | uint64 | 8B | Fabric identifier |
| fabric-idx | uint8 | 1B | Fabric index on node (0/NULL = no fabric) |
| subject-id | uint64 | 8B | Source identity (PASE/CASE/Group) |
| action-id | uint8 | 1B | Interaction Model action |
| trans-id | uint32 | 4B | Transaction identifier |
| entry-idx | uint16 | 2B | List entry index |
| data-ver | uint32 | 4B | Cluster data version |
| event-no | uint64 | 8B | Event instance number |

## Derived Types — Time

| Type | Base | Size | Description |
|------|------|------|-------------|
| epoch-s | uint32 | 4B | Seconds since 2000-01-01 00:00:00 UTC |
| epoch-us | uint64 | 8B | Microseconds since 2000-01-01 00:00:00 UTC |
| posix-ms | uint64 | 8B | Milliseconds since 1970-01-01 00:00:00 UTC |
| systime-us | uint64 | 8B | Microseconds since boot |
| systime-ms | uint64 | 8B | Milliseconds since boot |
| elapsed-s | uint32 | 4B | Elapsed seconds for an operation |

**Note:** Matter epoch is **2000-01-01 00:00:00 UTC** (not Unix 1970 epoch), except for posix-ms.

## Derived Types — Physical Quantities

| Type | Base | Size | Unit | Resolution |
|------|------|------|------|------------|
| temperature | int16 | 2B | °C | 0.01°C (value = temp × 100) |
| power-mW | int64 | 8B | mW | 1 mW |
| amperage-mA | int64 | 8B | mA | 1 mA |
| voltage-mV | int64 | 8B | mV | 1 mV |
| energy-mWh | int64 | 8B | mWh | 1 mWh |

## Derived Types — Special

| Type | Base | Size | Description |
|------|------|------|-------------|
| percent | uint8 | 1B | 0–100 (1% resolution) |
| percent100ths | uint16 | 2B | 0–10000 (0.01% resolution) |
| ipv4adr | octstr | 4B | IPv4 address (network byte order) |
| ipv6adr | octstr | 16B | IPv6 address (network byte order) |
| hwadr | octstr | 6/8B | MAC-48 or EUI-64 (big-endian) |
| semtag | struct | 4B+ | Semantic tag { MfgCode, NamespaceID, Tag, Label } |
| namespace | enum8 | 1B | Semantic tag namespace |
| tag | enum8 | 1B | Semantic tag value |

## TLV Encoding

Matter uses TLV (Tag-Length-Value) for all wire encoding:
- **Tags**: Context-specific (1 byte), implicit profile (2 bytes), or fully-qualified (6 bytes)
- **Types**: Signed/unsigned integers (1/2/4/8 bytes), bool, float, double, UTF-8 string, byte string, null, struct, array, list
- **Encoding**: Little-endian for integers
- **Compact**: No padding, variable-length integers
- **Self-describing**: Type info embedded in each element

### Common TLV Patterns
- Struct fields identified by context tag (field ID)
- Arrays contain homogeneous anonymous elements
- Lists contain heterogeneous elements with tags
- NULL value represents absent/unknown data (for nullable fields)
