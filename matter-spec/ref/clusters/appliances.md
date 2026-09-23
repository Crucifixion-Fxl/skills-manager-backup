# Appliance Clusters

## Mode Base (Base Cluster)
Rev 2 | PICS: MODB

Base cluster for all derived mode clusters. No standalone cluster ID.

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | DEPONOFF | OnOff | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | SupportedModes | list[ModeOptionStruct] | 2 to 255 | | R V | M |
| 0x0001 | CurrentMode | uint8 | desc | N | R V | M |
| 0x0002 | StartUpMode | uint8 | desc | N X | RW VO | O |
| 0x0003 | OnMode | uint8 | desc | N X | RW VO | DEPONOFF |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | ChangeToMode | C→S | ChangeToModeResponse | O | M |
| 0x01 | ChangeToModeResponse | S→C | — | | M |

**Data Types:**
- **ModeOptionStruct:** Label (string max 64), Mode (uint8), ModeTags (list[ModeTagStruct] max 8)
- **ModeTagStruct:** MfgCode (vendor-id, O), Value (enum16)
- **Common Mode Tags:** Auto(0), Quick(1), Quiet(2), LowNoise(3), LowEnergy(4), Vacation(5), Min(6), Max(7), Night(8), Day(9)
- **Status Codes:** Success(0x00), UnsupportedMode(0x01), GenericFailure(0x02), InvalidInMode(0x03)

**Derived Mode Clusters:**
| ID | Name | PICS | Specific Tags |
|----|------|------|---------------|
| 0x0049 | Oven Mode | OTCCM | — |
| 0x0051 | Laundry Mode | LWM | — |
| 0x0052 | Refrigerator & TCC Mode | TCC | RapidCool, RapidFreeze |
| 0x0054 | RVC Run Mode | RVCRUNM | Idle, Cleaning, Mapping |
| 0x0055 | RVC Clean Mode | RVCCLEANM | DeepClean, Vacuum, Mop, VacuumThenMop |
| 0x0059 | Dishwasher Mode | DISHM | Normal, Heavy, Light |
| 0x005E | Microwave Oven Mode | MWOM | — |
| 0x009D | Energy EVSE Mode | EEVSEM | — |
| 0x009E | Water Heater Mode | WHM | — |
| 0x009F | DEM Mode | DEMM | — |

---

## Mode Select (0x0050)
Rev 2 | PICS: MOD

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | DEPONOFF | OnOff | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | Description | string | max 64 | F | R V | M |
| 0x0001 | StandardNamespace | enum16 | all | F | R V | M |
| 0x0002 | SupportedModes | list[ModeOptionStruct] | max 255 | | R V | M |
| 0x0003 | CurrentMode | uint8 | desc | N | R V | M |
| 0x0004 | StartUpMode | uint8 | desc | N X | RW VO | O |
| 0x0005 | OnMode | uint8 | desc | N X | RW VO | DEPONOFF |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | ChangeToMode | C→S | Y | O | M |

---

## Operational State (0x0060)
Rev 3 | PICS: OPSTATE

Base cluster for derived operational state clusters.

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | PhaseList | list[string] | max 32 | X | R V | M |
| 0x0001 | CurrentPhase | uint8 | all | X | R V | M |
| 0x0002 | CountdownTime | elapsed-s | all | Q X | R V | O |
| 0x0003 | OperationalStateList | list[OperationalStateStruct] | all | | R V | M |
| 0x0004 | OperationalState | OperationalStateEnum | all | | R V | M |
| 0x0005 | OperationalError | ErrorStateStruct | all | | R V | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | Pause | C→S | OperationalCommandResponse | O | O |
| 0x01 | Stop | C→S | OperationalCommandResponse | O | O |
| 0x02 | Start | C→S | OperationalCommandResponse | O | O |
| 0x03 | Resume | C→S | OperationalCommandResponse | O | O |
| 0x04 | OperationalCommandResponse | S→C | — | | M |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | OperationalError | CRITICAL | M |
| 0x01 | OperationCompletion | INFO | O |

**Key Enums:**
- **OperationalStateEnum:** Stopped(0x00), Running(0x01), Paused(0x02), Error(0x03)
- **ErrorStateEnum:** NoError(0x00), UnableToStartOrResume(0x01), CommandInvalidInState(0x02)

---

## Microwave Oven Control (0x005F)
Rev 1 | PICS: MWOCTRL

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | PWRNUM | PowerAsNumber | O |
| 1 | WATTS | PowerInWatts | O |
| 2 | PWRLMTS | PowerNumberLimits | [PWRNUM] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | CookTime | elapsed-s | 1 to MaxCookTime | | R V | M |
| 0x0001 | MaxCookTime | elapsed-s | 1 to 86400 | F | R V | M |
| 0x0002 | PowerSetting | uint8 | MinPower to MaxPower | | R V | PWRNUM |
| 0x0003 | MinPower | uint8 | 1 to 99 | F | R V | PWRLMTS |
| 0x0004 | MaxPower | uint8 | (MinPower+1) to 100 | F | R V | PWRLMTS |
| 0x0005 | PowerStep | uint8 | desc | F | R V | PWRLMTS |
| 0x0006 | SupportedWatts | list[uint16] | 1 to 10 items | F | R V | WATTS |
| 0x0007 | SelectedWattIndex | uint8 | desc | | R V | WATTS |
| 0x0008 | WattRating | uint16 | all | F | R V | O |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SetCookingParameters | C→S | Y | O | M |
| 0x01 | AddMoreTime | C→S | Y | O | M |

---

## Laundry Washer Controls (0x0053)
Rev 2 | PICS: WASHERCTRL

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | SPIN | SpinSpeeds | O |
| 1 | RINSE | Rinses | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | SpinSpeeds | list[string] | max 16 | | R V | SPIN |
| 0x0001 | SpinSpeedCurrent | uint8 | max 15 | X | RW VO | SPIN |
| 0x0002 | NumberOfRinses | NumberOfRinsesEnum | desc | | RW VO | RINSE |
| 0x0003 | SupportedRinses | list[NumberOfRinsesEnum] | max 4 | | R V | RINSE |

**Key Enums:**
- **NumberOfRinsesEnum:** None(0), Normal(1), Extra(2), Max(3)

---

## Laundry Dryer Controls (0x004A)
Rev 1 | PICS: DRYERCTRL

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | SupportedDrynessLevels | list[DrynessLevelEnum] | 1 to 4 items | | R V | M |
| 0x0001 | SelectedDrynessLevel | DrynessLevelEnum | desc | X | RW VO | M |

**Key Enums:**
- **DrynessLevelEnum:** Low(0), Normal(1), Extra(2), Max(3)

---

## Temperature Control (0x0056)
Rev 1 | PICS: TCTL

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | TN | TemperatureNumber | O.a |
| 1 | TL | TemperatureLevel | O.a |
| 2 | STEP | TemperatureStep | [TN] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | TemperatureSetpoint | temperature | MinTemperature to MaxTemperature | | R V | TN |
| 0x0001 | MinTemperature | temperature | all | F | R V | TN |
| 0x0002 | MaxTemperature | temperature | all | F | R V | TN |
| 0x0003 | Step | temperature | 1 to (MaxTemperature-MinTemperature) | F | R V | STEP |
| 0x0004 | SelectedTemperatureLevel | uint8 | max 31 | | R V | TL |
| 0x0005 | SupportedTemperatureLevels | list[string] | max 32 | F | R V | TL |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SetTemperature | C→S | Y | O | M |

---

## Dishwasher Mode (0x0059)
Rev 3 | PICS: DISHM | Base: Mode Base

DEPONOFF feature is disallowed (X). StartUpMode and OnMode are disallowed (X).

**Mode Tags:** Normal(0x4000), Heavy(0x4001), Light(0x4002)

---

## Refrigerator Alarm (0x0057)
Rev 1 | PICS: REFALM | Base: Alarm Base

RESET feature is disallowed (X).

**AlarmBitmap:** DoorOpen (bit 0)
