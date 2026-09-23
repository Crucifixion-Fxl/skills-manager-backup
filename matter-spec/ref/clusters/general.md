# General Clusters

## Switch (0x003B)
Rev 2 | PICS: SWTCH

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | LS | LatchingSwitch | O.a |
| 1 | MS | MomentarySwitch | O.a |
| 2 | MSR | MomentarySwitchRelease | [MS] |
| 3 | MSL | MomentarySwitchLongPress | [MS & MSR] |
| 4 | MSM | MomentarySwitchMultiPress | [MS & MSR] |
| 5 | AS | ActionSwitch | [MS & MSR & MSM] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | NumberOfPositions | uint8 | min 2 | F | R V | M |
| 0x0001 | CurrentPosition | uint8 | max (NumberOfPositions - 1) | | R V | M |
| 0x0002 | MultiPressMax | uint8 | min 2 | F | R V | MSM |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | SwitchLatched | INFO | LS |
| 0x01 | InitialPress | INFO | MS |
| 0x02 | LongPress | INFO | MSL |
| 0x03 | ShortRelease | INFO | MSR |
| 0x04 | LongRelease | INFO | MSL |
| 0x05 | MultiPressOngoing | INFO | MSM & !AS |
| 0x06 | MultiPressComplete | INFO | MSM |

**Event Payloads:**
- SwitchLatched: NewPosition (uint8)
- InitialPress: NewPosition (uint8)
- LongPress: NewPosition (uint8)
- ShortRelease: PreviousPosition (uint8)
- LongRelease: PreviousPosition (uint8)
- MultiPressOngoing: NewPosition (uint8), CurrentNumberOfPressesCounted (uint8, 2 to MultiPressMax)
- MultiPressComplete: PreviousPosition (uint8), TotalNumberOfPressesCounted (uint8, max MultiPressMax)

No commands. Event-driven cluster.

---

## Scenes Management (0x0062)
Rev 1 | PICS: S

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | SN | SceneNames | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0001 | SceneTableSize | uint16 | all | F | R V | M |
| 0x0002 | FabricSceneInfo | list[SceneInfoStruct] | all | | R V F | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | AddScene | C→S | AddSceneResponse | M | M |
| 0x01 | ViewScene | C→S | ViewSceneResponse | O | M |
| 0x02 | RemoveScene | C→S | RemoveSceneResponse | M | M |
| 0x03 | RemoveAllScenes | C→S | RemoveAllScenesResponse | M | M |
| 0x04 | StoreScene | C→S | StoreSceneResponse | M | M |
| 0x05 | RecallScene | C→S | Y | O | M |
| 0x06 | GetSceneMembership | C→S | GetSceneMembershipResponse | O | M |
| 0x40 | CopyScene | C→S | CopySceneResponse | M | O |

**Data Types:**
- **SceneInfoStruct (fabric-scoped):** SceneCount (uint8), CurrentScene (uint8), CurrentGroup (group-id), SceneValid (bool), RemainingCapacity (uint8, max 253)
- **AttributeValuePairStruct:** AttributeID, ValueUnsigned8/Signed8/16/32/64
- **ExtensionFieldSetStruct:** ClusterID, AttributeValueList (list[AttributeValuePairStruct])
- **CopyModeBitmap:** CopyAllScenes (bit 0)

---

## ICD Management (0x0046)
Rev 3 | PICS: ICDM

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | CIP | CheckInProtocolSupport | LITS, O |
| 1 | UAT | UserActiveModeTrigger | LITS, O |
| 2 | LITS | LongIdleTimeSupport | O |
| 3 | DSLS | DynamicSitLitSupport | [LITS] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | IdleModeDuration | uint32 | 1 to 64800 | F | R V | M |
| 0x0001 | ActiveModeDuration | uint32 | all | F | R V | M |
| 0x0002 | ActiveModeThreshold | uint16 | all | F | R V | M |
| 0x0003 | RegisteredClients | list[MonitoringRegistrationStruct] | all | | R A F | CIP |
| 0x0004 | ICDCounter | uint32 | all | N | R A | CIP |
| 0x0005 | ClientsSupportedPerFabric | uint16 | min 1 | F | R V | CIP |
| 0x0006 | UserActiveModeTriggerHint | UserActiveModeTriggerBitmap | all | F | R V | UAT |
| 0x0007 | UserActiveModeTriggerInstruction | string | max 128 | F | R V | [UAT] |
| 0x0008 | OperatingMode | OperatingModeEnum | all | | R V | LITS |
| 0x0009 | MaximumCheckInBackoff | uint32 | IdleModeDuration to 64800 | F | R V | CIP |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | RegisterClient | C→S | RegisterClientResponse | M F | CIP |
| 0x02 | UnregisterClient | C→S | Y | M F | CIP |
| 0x03 | StayActiveRequest | C→S | StayActiveResponse | O | LITS |

**Key Enums:**
- **OperatingModeEnum:** SIT(0), LIT(1)
- **ClientTypeEnum:** Permanent(0), Ephemeral(1)
- **UserActiveModeTriggerBitmap:** PowerCycle(0), SettingsMenu(1), CustomInstruction(2), DeviceManual(3), ActuateSensor(4), ActuateSensorSeconds(5), ActuateSensorTimes(6), ActuateSensorLightsBlink(7), ResetButton(8), ResetButtonLightsBlink(9), ResetButtonSeconds(10), ResetButtonTimes(11), SetupButton(12), SetupButtonSeconds(13), SetupButtonLightsBlink(14), SetupButtonTimes(15), AppDefinedButton(16)

**Data Types:**
- **MonitoringRegistrationStruct (fabric-scoped):** CheckInNodeID (node-id), MonitoredSubject (subject-id), ClientType (ClientTypeEnum)
