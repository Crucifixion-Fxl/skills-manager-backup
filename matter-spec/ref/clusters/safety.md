# Safety Clusters

## Smoke/CO Alarm (0x005C)
Rev 1 | PICS: SMOKECO

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | SMOKE | SmokeAlarm | O.a+ |
| 1 | CO | COAlarm | O.a+ |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | ExpressedState | ExpressedStateEnum | desc | | R V | M |
| 0x0001 | SmokeState | AlarmStateEnum | all | | R V | SMOKE |
| 0x0002 | COState | AlarmStateEnum | all | | R V | CO |
| 0x0003 | BatteryAlert | AlarmStateEnum | all | | R V | M |
| 0x0004 | DeviceMuted | MuteStateEnum | all | | R V | O |
| 0x0005 | TestInProgress | bool | all | | R V | M |
| 0x0006 | HardwareFaultAlert | bool | all | | R V | M |
| 0x0007 | EndOfServiceAlert | EndOfServiceEnum | all | | R V | M |
| 0x0008 | InterconnectSmokeAlarm | AlarmStateEnum | all | | R V | O |
| 0x0009 | InterconnectCOAlarm | AlarmStateEnum | all | | R V | O |
| 0x000A | ContaminationState | ContaminationStateEnum | all | | R V | [SMOKE] |
| 0x000B | SmokeSensitivityLevel | SensitivityEnum | all | | RW VM | [SMOKE] |
| 0x000C | ExpiryDate | epoch-s | all | | R V | O |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SelfTestRequest | C→S | Y | O | M |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | SmokeAlarm | CRITICAL | SMOKE |
| 0x01 | COAlarm | CRITICAL | CO |
| 0x02 | LowBattery | INFO | M |
| 0x03 | HardwareFault | INFO | M |
| 0x04 | EndOfService | INFO | M |
| 0x05 | SelfTestComplete | INFO | M |
| 0x06 | AlarmMuted | INFO | O |
| 0x07 | MuteEnded | INFO | O |
| 0x08 | InterconnectSmokeAlarm | CRITICAL | [SMOKE] |
| 0x09 | InterconnectCOAlarm | CRITICAL | [CO] |
| 0x0A | AllClear | INFO | M |

**Key Enums:**
- **AlarmStateEnum:** Normal(0), Warning(1), Critical(2)
- **ExpressedStateEnum:** Normal(0), SmokeAlarm(1), COAlarm(2), BatteryAlert(3), Testing(4), HardwareFault(5), EndOfService(6), InterconnectSmoke(7), InterconnectCO(8)
- **SensitivityEnum:** High(0), Standard(1), Low(2)
- **MuteStateEnum:** NotMuted(0), Muted(1)
- **EndOfServiceEnum:** Normal(0), Expired(1)
- **ContaminationStateEnum:** Normal(0), Low(1), Warning(2), Critical(3)

---

## Boolean State (0x0045)
Rev 1 | PICS: BOOL

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | StateValue | bool | all | | R V | M |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | StateChange | INFO | O |

No commands. Simple binary sensor (e.g., contact/water leak).

---

## Boolean State Configuration (0x0080)
Rev 1 | PICS: BOOLCFG

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | VIS | Visual | O |
| 1 | AUD | Audible | O |
| 2 | SPRS | AlarmSuppress | [VIS \| AUD] |
| 3 | SENSLVL | SensitivityLevel | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | CurrentSensitivityLevel | uint8 | desc | | RW VO | SENSLVL |
| 0x0001 | SupportedSensitivityLevels | uint8 | all | | R V | SENSLVL |
| 0x0002 | DefaultSensitivityLevel | uint8 | all | | R V | [SENSLVL] |
| 0x0003 | AlarmsActive | AlarmModeBitmap | all | | R V | VIS \| AUD |
| 0x0004 | AlarmsSuppressed | AlarmModeBitmap | all | | R V | SPRS |
| 0x0005 | AlarmsEnabled | AlarmModeBitmap | all | | R V | [VIS \| AUD] |
| 0x0006 | AlarmsSupported | AlarmModeBitmap | all | | R V | VIS \| AUD |
| 0x0007 | SensorFault | SensorFaultBitmap | all | | R V | O |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SuppressAlarm | C→S | Y | O | SPRS |
| 0x01 | EnableDisableAlarm | C→S | Y | O | VIS \| AUD |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | AlarmsStateChanged | INFO | VIS \| AUD |
| 0x01 | SensorFault | INFO | O |

**Key Enums:**
- **AlarmModeBitmap:** Visual(0), Audible(1)
- **SensorFaultBitmap:** GeneralFault(0)
