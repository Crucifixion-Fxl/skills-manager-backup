# Network Infrastructure Clusters

## Wi-Fi Network Management (0x0451)
Rev 1 | PICS: WIFINM

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | SSID | octstr | 1 to 32 | X | R V | M |
| 0x0001 | PassphraseSurrogate | uint64 | all | | R M | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | NetworkPassphraseRequest | C→S | NetworkPassphraseResponse | A | M |

**Response:** NetworkPassphraseResponse — Passphrase (octstr, max 64)

No events.

---

## Thread Border Router Management (0x0452)
Rev 1 | PICS: TBRM

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | PC | PANChange | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | BorderRouterName | string | 1 to 63 | | R V | M |
| 0x0001 | BorderAgentID | octstr | 16 | | R V | M |
| 0x0002 | ThreadVersion | uint16 | all | F | R V | M |
| 0x0003 | InterfaceEnabled | bool | all | | R V | M |
| 0x0004 | ActiveDatasetTimestamp | uint64 | all | X | R V | M |
| 0x0005 | PendingDatasetTimestamp | uint64 | all | X | R V | O |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | GetActiveDatasetRequest | C→S | DatasetResponse | O | M |
| 0x01 | GetPendingDatasetRequest | C→S | DatasetResponse | O | O |
| 0x03 | SetActiveDatasetRequest | C→S | Y | A | M |
| 0x04 | SetPendingDatasetRequest | C→S | Y | A | PC |

**Response:** DatasetResponse — Dataset (octstr, max 254)

No events.

---

## Thread Network Directory (0x0453)
Rev 1 | PICS: THNETDIR

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | PreferredExtendedPanID | octstr | 8 | X | RW VM | M |
| 0x0001 | ThreadNetworks | list[ThreadNetworkStruct] | max ThreadNetworkTableSize | | R V | M |
| 0x0002 | ThreadNetworkTableSize | uint8 | all | F | R V | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | AddNetwork | C→S | Y | M | M |
| 0x01 | RemoveNetwork | C→S | Y | M | M |
| 0x02 | GetOperationalDataset | C→S | OperationalDatasetResponse | O | M |

**Response:** OperationalDatasetResponse — OperationalDataset (octstr, max 254)

**Data Types:**
- **ThreadNetworkStruct:** ExtendedPanID (octstr 8), NetworkName (string 1-16), Channel (uint16), ActiveTimestamp (uint64)

No events.
