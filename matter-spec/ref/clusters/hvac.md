# HVAC Clusters

## Thermostat (0x0201)
Rev 9 | PICS: TSTAT

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | HEAT | Heating | AUTO, O.a+ |
| 1 | COOL | Cooling | AUTO, O.a+ |
| 2 | OCC | Occupancy | O |
| 3 | SCH | ScheduleConfiguration | D |
| 4 | SB | Setback | O |
| 5 | AUTO | AutoMode | O |
| 6 | LTNE | LocalTemperatureNotExposed | O |
| 7 | MSCH | MatterScheduleConfiguration | O |
| 8 | PRES | Presets | O |
| 9 | TEVT | Events | P, O |
| 10 | TSUGGEST | ThermostatSuggestions | P, [PRES] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | LocalTemperature | temperature | all | P X | R V | M |
| 0x0001 | OutdoorTemperature | temperature | all | X | R V | O |
| 0x0002 | Occupancy | OccupancyBitmap | all | | R V | OCC |
| 0x0003 | AbsMinHeatSetpointLimit | temperature | all | F | R V | [HEAT] |
| 0x0004 | AbsMaxHeatSetpointLimit | temperature | all | F | R V | [HEAT] |
| 0x0005 | AbsMinCoolSetpointLimit | temperature | all | F | R V | [COOL] |
| 0x0006 | AbsMaxCoolSetpointLimit | temperature | all | F | R V | [COOL] |
| 0x0007 | PICoolingDemand | uint8 | 0 to 100 | P | R V | [COOL] |
| 0x0008 | PIHeatingDemand | uint8 | 0 to 100 | P | R V | [HEAT] |
| 0x0011 | OccupiedCoolingSetpoint | temperature | desc | N S | RW VO | COOL |
| 0x0012 | OccupiedHeatingSetpoint | temperature | desc | N S | RW VO | HEAT |
| 0x0013 | UnoccupiedCoolingSetpoint | temperature | desc | N | RW VO | COOL & OCC |
| 0x0014 | UnoccupiedHeatingSetpoint | temperature | desc | N | RW VO | HEAT & OCC |
| 0x0015 | MinHeatSetpointLimit | temperature | desc | N | RW VO | [HEAT] |
| 0x0016 | MaxHeatSetpointLimit | temperature | desc | N | RW VO | [HEAT] |
| 0x0017 | MinCoolSetpointLimit | temperature | desc | N | RW VO | [COOL] |
| 0x0018 | MaxCoolSetpointLimit | temperature | desc | N | RW VO | [COOL] |
| 0x0019 | MinSetpointDeadBand | int8 | 0 to 127 | N | RW VM | AUTO |
| 0x001A | RemoteSensing | RemoteSensingBitmap | all | N | RW VO | O |
| 0x001B | ControlSequenceOfOperation | ControlSequenceOfOperationEnum | all | N | RW VM | M |
| 0x001C | SystemMode | SystemModeEnum | desc | N | RW VM | M |
| 0x001E | ThermostatRunningMode | ThermostatRunningModeEnum | desc | | R V | [AUTO] |
| 0x0023 | TemperatureSetpointHold | TemperatureSetpointHoldEnum | all | N | RW VM | O |
| 0x0024 | TemperatureSetpointHoldDuration | uint16 | max 1440 | N X | RW VM | O |
| 0x0025 | ThermostatProgrammingOperationMode | ProgrammingOperationModeBitmap | all | P | RW VM | O |
| 0x0029 | ThermostatRunningState | RelayStateBitmap | all | | R V | O |
| 0x004E | ActivePresetHandle | octstr | max 16 | N X | R V | PRES |
| 0x004F | ActiveScheduleHandle | octstr | max 16 | N X | R V | MSCH |
| 0x0050 | Presets | list[PresetStruct] | all | N T | RW VM | PRES |
| 0x0051 | Schedules | list[ScheduleStruct] | all | N T | RW VM | MSCH |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SetpointRaiseLower | C→S | Y | O | M |
| 0x01 | SetWeeklySchedule | C→S | Y | M | SCH |
| 0x02 | GetWeeklySchedule | C→S | GetWeeklyScheduleResponse | O | SCH |
| 0x03 | ClearWeeklySchedule | C→S | Y | M | SCH |
| 0x05 | SetActiveScheduleRequest | C→S | Y | O | MSCH |
| 0x06 | SetActivePresetRequest | C→S | Y | O | PRES |

**Events (TEVT feature):**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | SystemModeChange | INFO | TEVT |
| 0x01 | LocalTemperatureChange | INFO | TEVT & !LTNE |
| 0x02 | OccupancyChange | INFO | TEVT & OCC |
| 0x03 | SetpointChange | INFO | TEVT |
| 0x04 | RunningStateChange | INFO | TEVT |
| 0x05 | RunningModeChange | INFO | TEVT & AUTO |
| 0x06 | ActiveScheduleChange | INFO | TEVT & MSCH |
| 0x07 | ActivePresetChange | INFO | TEVT & PRES |

**Key Enums:**
- **SystemModeEnum:** Off(0), Auto(1), Cool(3), Heat(4), EmergencyHeat(5), Precooling(6), FanOnly(7), Dry(8), Sleep(9)
- **ControlSequenceOfOperationEnum:** CoolingOnly(0), CoolingWithReheat(1), HeatingOnly(2), HeatingWithReheat(3), CoolingAndHeating(4), CoolingAndHeatingWithReheat(5)
- **ThermostatRunningModeEnum:** Off(0), Cool(3), Heat(4)
- **PresetScenarioEnum:** Occupied(0), Unoccupied(1), Sleep(2), Wake(3), Vacation(4), GoingToSleep(5), UserDefined(6)

---

## Fan Control (0x0202)
Rev 6 | PICS: FAN

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | SPD | MultiSpeed | O |
| 1 | AUT | Auto | O |
| 2 | RCK | Rocking | O |
| 3 | WND | Wind | O |
| 4 | STEP | Step | O |
| 5 | DIR | AirflowDirection | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | FanMode | FanModeEnum | desc | N | RW VO | M |
| 0x0001 | FanModeSequence | FanModeSequenceEnum | all | F | R V | M |
| 0x0002 | PercentSetting | percent | max 100 | X | RW VO | M |
| 0x0003 | PercentCurrent | percent | max 100 | Q | R V | M |
| 0x0004 | SpeedMax | uint8 | 1 to 100 | F | R V | SPD |
| 0x0005 | SpeedSetting | uint8 | max SpeedMax | X | RW VO | SPD |
| 0x0006 | SpeedCurrent | uint8 | max SpeedMax | P Q | R V | SPD |
| 0x0007 | RockSupport | RockBitmap | min 1 | F | R V | RCK |
| 0x0008 | RockSetting | RockBitmap | desc | P | RW VO | RCK |
| 0x0009 | WindSupport | WindBitmap | min 1 | F | R V | WND |
| 0x000A | WindSetting | WindBitmap | desc | P | RW VO | WND |
| 0x000B | AirflowDirection | AirflowDirectionEnum | all | P | RW VO | DIR |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | Step | C→S | Y | O | STEP |

**Key Enums:**
- **FanModeEnum:** Off(0), Low(1), Medium(2), High(3), On(4), Auto(5), Smart(6)
- **FanModeSequenceEnum:** OffLowMedHigh(0), OffLowHigh(1), OffLowMedHighAuto(2), OffLowHighAuto(3), OffHighAuto(4), OffHigh(5)
- **AirflowDirectionEnum:** Forward(0), Reverse(1)
- **RockBitmap:** RockLeftRight(0), RockUpDown(1), RockRound(2)
- **WindBitmap:** SleepWind(0), NaturalWind(1)

---

## Pump Configuration and Control (0x0200)
Rev 5 | PICS: PCC

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | PRSCONST | ConstantPressure | O.a+ |
| 1 | PRSCOMP | CompensatedPressure | O.a+ |
| 2 | FLW | ConstantFlow | O.a+ |
| 3 | SPD | ConstantSpeed | O.a+ |
| 4 | TEMP | ConstantTemperature | O.a+ |
| 5 | AUTO | Automatic | O |
| 6 | LOCAL | LocalOperation | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MaxPressure | int16 | all | F X | R V | M |
| 0x0001 | MaxSpeed | uint16 | all | F X | R V | M |
| 0x0002 | MaxFlow | uint16 | all | F X | R V | M |
| 0x0010 | PumpStatus | PumpStatusBitmap | desc | P | R V | O |
| 0x0011 | EffectiveOperationMode | OperationModeEnum | desc | N | R V | M |
| 0x0012 | EffectiveControlMode | ControlModeEnum | desc | N | R V | M |
| 0x0013 | Capacity | int16 | all | P Q X | R V | M |
| 0x0014 | Speed | uint16 | all | Q X | R V | O |
| 0x0015 | LifetimeRunningHours | uint24 | all | N X | RW VM | O |
| 0x0016 | Power | uint24 | all | Q X | R V | O |
| 0x0017 | LifetimeEnergyConsumed | uint32 | all | N X | RW VM | O |
| 0x0020 | OperationMode | OperationModeEnum | desc | N | RW VM | M |
| 0x0021 | ControlMode | ControlModeEnum | desc | N | RW VM | O |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | SupplyVoltageLow | INFO | O |
| 0x01 | SupplyVoltageHigh | INFO | O |
| 0x02 | PowerMissingPhase | INFO | O |
| 0x03 | SystemPressureLow | INFO | O |
| 0x04 | SystemPressureHigh | INFO | O |
| 0x05 | DryRunning | CRITICAL | O |
| 0x06 | MotorTemperatureHigh | INFO | O |
| 0x07 | PumpMotorFatalFailure | CRITICAL | O |
| 0x08 | ElectronicTemperatureHigh | INFO | O |
| 0x09 | PumpBlocked | CRITICAL | O |
| 0x0A | SensorFailure | INFO | O |
| 0x0B | ElectronicNonFatalFailure | INFO | O |
| 0x0C | ElectronicFatalFailure | CRITICAL | O |
| 0x0D | GeneralFault | INFO | O |
| 0x0E | Leakage | INFO | O |
| 0x0F | AirDetection | INFO | O |
| 0x10 | TurbineOperation | INFO | O |

**Key Enums:**
- **OperationModeEnum:** Normal(0), Minimum(1), Maximum(2), Local(3)
- **ControlModeEnum:** ConstantSpeed(0), ConstantPressure(1), ProportionalPressure(2), ConstantFlow(3), ConstantTemperature(5), Automatic(7)
- **PumpStatusBitmap:** DeviceFault(0), SupplyFault(1), SpeedLow(2), SpeedHigh(3), LocalOverride(4), Running(5), RemotePressure(6), RemoteFlow(7), RemoteTemperature(8)

---

## Valve Configuration and Control (0x0081)
Rev 2 | PICS: VALCC

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | TS | TimeSync | desc |
| 1 | LVL | Level | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | OpenDuration | elapsed-s | min 1 | X | R V | M |
| 0x0001 | DefaultOpenDuration | elapsed-s | min 1 | N X | RW VO | M |
| 0x0002 | AutoCloseTime | epoch-us | all | X | R V | TS |
| 0x0003 | RemainingDuration | elapsed-s | all | Q X | R V | M |
| 0x0004 | CurrentState | ValveStateEnum | all | X | R V | M |
| 0x0005 | TargetState | ValveStateEnum | all | X | R V | M |
| 0x0006 | CurrentLevel | percent | all | Q X | R V | LVL |
| 0x0007 | TargetLevel | percent | all | X | R V | LVL |
| 0x0008 | DefaultOpenLevel | percent | 1 to 100 | N | RW VO | [LVL] |
| 0x0009 | ValveFault | ValveFaultBitmap | all | | R V | O |
| 0x000A | LevelStep | uint8 | 1 to 50 | F | R V | [LVL] |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | Open | C→S | Y | O | M |
| 0x01 | Close | C→S | Y | O | M |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | ValveStateChanged | INFO | O |
| 0x01 | ValveFault | INFO | O |

**Key Enums:**
- **ValveStateEnum:** Closed(0), Open(1), Transitioning(2)
- **ValveFaultBitmap:** GeneralFault(0), Blocked(1), Leaking(2), NotConnected(3), ShortCircuit(4), CurrentExceeded(5)

---

## Thermostat User Interface Configuration (0x0204)
Rev 2 | PICS: TSUIC

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | TemperatureDisplayMode | TemperatureDisplayModeEnum | all | | RW VO | M |
| 0x0001 | KeypadLockout | KeypadLockoutEnum | all | | RW VM | M |
| 0x0002 | ScheduleProgrammingVisibility | ScheduleProgrammingVisibilityEnum | all | | RW VM | O |

**Key Enums:**
- **TemperatureDisplayModeEnum:** Celsius(0), Fahrenheit(1)
- **KeypadLockoutEnum:** NoLockout(0), Lockout1(1), Lockout2(2), Lockout3(3), Lockout4(4), Lockout5(5)
- **ScheduleProgrammingVisibilityEnum:** ScheduleProgrammingPermitted(0), ScheduleProgrammingDenied(1)
