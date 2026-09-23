# Robot Clusters

## RVC Run Mode (0x0054)
Rev 4 | PICS: RVCRUNM | Base: Mode Base

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | DEPONOFF | OnOff | X (disallowed) |
| 20 | DIRECTMODECH | DirectModeChange | O |

StartUpMode and OnMode are disallowed (X).

**Mode Tags:**
- 0x4000: **Idle** — Device not performing main operations, may seek charger
- 0x4001: **Cleaning** — Device actively running, paused due to error/pause/recharge
- 0x4002: **Mapping** — Device creating space map

**ChangeToModeResponse Status Codes:**
- 0x41: Stuck
- 0x42: DustBinMissing
- 0x43: DustBinFull
- 0x44: WaterTankEmpty
- 0x45: WaterTankMissing
- 0x46: WaterTankLidOpen
- 0x47: MopCleaningPadMissing
- 0x48: BatteryLow

DIRECTMODECH feature allows changing run mode from non-Idle states directly.

---

## RVC Clean Mode (0x0055)
Rev 5 | PICS: RVCCLEANM | Base: Mode Base

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | DEPONOFF | OnOff | X (disallowed) |
| 20 | DIRECTMODECH | DirectModeChange | O |

StartUpMode and OnMode are disallowed (X).

**Mode Tags:**
- 0x4000: **DeepClean** — Device optimizing for improved cleaning
- 0x4001: **Vacuum** — Device's vacuuming feature enabled
- 0x4002: **Mop** — Device's mopping feature enabled
- 0x4003: **VacuumThenMop** — Device will vacuum then mop relevant areas

**ChangeToModeResponse Status Codes:**
- 0x40: CleaningInProgress

---

## RVC Operational State (0x0061)
Rev 3 | PICS: RVCOPSTATE | Base: Operational State

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | Pause | C→S | OperationalCommandResponse | O | M |
| 0x01 | Stop | C→S | — | — | X (disallowed) |
| 0x02 | Start | C→S | — | — | X (disallowed) |
| 0x03 | Resume | C→S | OperationalCommandResponse | O | M |
| 0x80 | GoHome | C→S | OperationalCommandResponse | O | O |

**Operational States (derived):**
| Value | Name | Conf |
|-------|------|------|
| 0x40 | SeekingCharger | M |
| 0x41 | Charging | M |
| 0x42 | Docked | M |
| 0x43 | EmptyingDustBin | O |
| 0x44 | CleaningMop | O |
| 0x45 | FillingWaterTank | O |
| 0x46 | UpdatingMaps | O |

**Error States (derived):**
| Value | Name | Conf |
|-------|------|------|
| 0x40 | FailedToFindChargingDock | M |
| 0x41 | Stuck | M |
| 0x42 | DustBinMissing | M |
| 0x43 | DustBinFull | M |
| 0x44 | WaterTankEmpty | M |
| 0x45 | WaterTankMissing | M |
| 0x46 | WaterTankLidOpen | M |
| 0x47 | MopCleaningPadMissing | M |
| 0x48 | LowBattery | M |
| 0x49 | CannotReachTargetArea | M |
| 0x4A | DirtyWaterTankFull | M |
| 0x4B | DirtyWaterTankMissing | M |
| 0x4C | WheelsJammed | M |
| 0x4D | BrushJammed | M |
| 0x4E | NavigationSensorObscured | M |

---

## Service Area (0x0150)
Rev 2 | PICS: SEAR

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | SELRUN | SelectWhileRunning | O |
| 1 | PROG | ProgressReporting | O |
| 2 | MAPS | Maps | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | SupportedAreas | list[AreaStruct] | max 255 | | R V | M |
| 0x0001 | SupportedMaps | list[MapStruct] | max 255 | | R V | MAPS |
| 0x0002 | SelectedAreas | list[uint32] | all | | R V | M |
| 0x0003 | CurrentArea | uint32 | all | X | R V | O |
| 0x0004 | EstimatedEndTime | epoch-s | all | Q X | R V | [CurrentArea] |
| 0x0005 | Progress | list[ProgressStruct] | max 255 | | R V | PROG |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SelectAreas | C→S | SelectAreasResponse | O | M |
| 0x02 | SkipArea | C→S | SkipAreaResponse | O | [CurrentArea \| Progress] |

**Key Enums:**
- **OperationalStatusEnum:** Pending(0), Operating(1), Skipped(2), Completed(3)
- **SelectAreasStatus:** Success(0), UnsupportedArea(1), InvalidInMode(2), InvalidSet(3)
- **SkipAreaStatus:** Success(0), InvalidAreaList(1), InvalidInMode(2), InvalidSkippedArea(3)

**Data Types:**
- **AreaStruct:** AreaID (uint32), MapID (uint32, X), AreaInfo (AreaInfoStruct)
- **MapStruct:** MapID (uint32), Name (string max 64)
- **ProgressStruct:** AreaID (uint32), Status (OperationalStatusEnum), TotalOperationalTime (elapsed-s, X), EstimatedTime (elapsed-s, X)
