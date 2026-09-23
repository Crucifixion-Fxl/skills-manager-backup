# Closure Clusters

## Door Lock (0x0101)
Rev 9 | PICS: DRLK

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | PIN | PINCredential | O |
| 1 | RID | RFIDCredential | O |
| 2 | FGP | FingerCredentials | P, O |
| 4 | WDSCH | WeekDayAccessSchedules | O |
| 5 | DPS | DoorPositionSensor | O |
| 6 | FACE | FaceCredentials | P, O |
| 7 | COTA | CredentialOverTheAirAccess | O |
| 8 | USR | User | ALIRO, [PIN\|RID\|FGP\|FACE] |
| 10 | YDSCH | YearDayAccessSchedules | O |
| 11 | HDSCH | HolidaySchedules | O |
| 12 | UBOLT | Unbolting | O |
| 13 | ALIRO | AliroProvisioning | O |
| 14 | ALBU | AliroBLEUWB | [ALIRO] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | LockState | LockStateEnum | desc | P X | R V | M |
| 0x0001 | LockType | LockTypeEnum | desc | | R V | M |
| 0x0002 | ActuatorEnabled | bool | all | | R V | M |
| 0x0003 | DoorState | DoorStateEnum | desc | P X | R V | DPS |
| 0x0004 | DoorOpenEvents | uint32 | all | | RW VM | [DPS] |
| 0x0005 | DoorClosedEvents | uint32 | all | | RW VM | [DPS] |
| 0x0006 | OpenPeriod | uint16 | all | | RW VM | [DPS] |
| 0x0011 | NumberOfTotalUsersSupported | uint16 | all | F | R V | USR |
| 0x0012 | NumberOfPINUsersSupported | uint16 | all | F | R V | PIN |
| 0x0013 | NumberOfRFIDUsersSupported | uint16 | all | F | R V | RID |
| 0x0014 | NumberOfWeekDaySchedulesSupportedPerUser | uint8 | max 0xFD | F | R V | WDSCH |
| 0x0015 | NumberOfYearDaySchedulesSupportedPerUser | uint8 | max 0xFD | F | R V | YDSCH |
| 0x0016 | NumberOfHolidaySchedulesSupported | uint8 | max 0xFD | F | R V | HDSCH |
| 0x0017 | MaxPINCodeLength | uint8 | all | F | R V | PIN |
| 0x0018 | MinPINCodeLength | uint8 | all | F | R V | PIN |
| 0x0019 | MaxRFIDCodeLength | uint8 | all | F | R V | RID |
| 0x001A | MinRFIDCodeLength | uint8 | all | F | R V | RID |
| 0x001B | CredentialRulesSupport | CredentialRulesBitmap | all | F | R V | USR |
| 0x001C | NumberOfCredentialsSupportedPerUser | uint8 | all | F | R V | USR |
| 0x0021 | Language | string | max 3 | P | R[W] VM | O |
| 0x0022 | LEDSettings | LedSettingEnum | all | P | R[W] VM | O |
| 0x0023 | AutoRelockTime | uint32 | all | P | R[W] VM | O |
| 0x0024 | SoundVolume | SoundVolumeEnum | all | P | R[W] VM | O |
| 0x0025 | OperatingMode | DoorLockOperatingModeEnum | desc | P | R[W] VM | M |
| 0x0026 | SupportedOperatingModes | OperatingModesBitmap | all | F | R V | M |
| 0x0028 | EnableLocalProgramming | bool | all | P | R[W] VA | O |
| 0x0029 | EnableOneTouchLocking | bool | all | P | RW VM | O |
| 0x002B | EnablePrivacyModeButton | bool | all | P | RW VM | O |
| 0x0030 | WrongCodeEntryLimit | uint8 | 1 to 255 | P | R[W] VA | PIN \| RID |
| 0x0031 | UserCodeTemporaryDisableTime | uint8 | 1 to 255 | P | R[W] VA | PIN \| RID |
| 0x0033 | RequirePINforRemoteOperation | bool | all | P | R[W] VA | COTA & PIN |
| 0x0035 | ExpiringUserTimeout | uint16 | 1 to 2880 | P | R[W] VA | [USR] |
| 0x0080 | AliroReaderVerificationKey | octstr | 65 | X | R A | ALIRO |
| 0x0081 | AliroReaderGroupIdentifier | octstr | 16 | X | R A | ALIRO |
| 0x0082 | AliroReaderGroupSubIdentifier | octstr | 16 | F | R A | ALIRO |
| 0x0087 | NumberOfAliroCredentialIssuerKeysSupported | uint16 | all | F | R V | ALIRO |
| 0x0088 | NumberOfAliroEndpointKeysSupported | uint16 | all | F | R V | ALIRO |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | LockDoor | C→S | Y | O T | M |
| 0x01 | UnlockDoor | C→S | Y | O T | M |
| 0x03 | UnlockWithTimeout | C→S | Y | O T | O |
| 0x0B | SetWeekDaySchedule | C→S | Y | A | WDSCH |
| 0x0C | GetWeekDaySchedule | C→S | GetWeekDayScheduleResponse | A | WDSCH |
| 0x0D | ClearWeekDaySchedule | C→S | Y | A | WDSCH |
| 0x0E | SetYearDaySchedule | C→S | Y | A | YDSCH |
| 0x0F | GetYearDaySchedule | C→S | GetYearDayScheduleResponse | A | YDSCH |
| 0x10 | ClearYearDaySchedule | C→S | Y | A | YDSCH |
| 0x11 | SetHolidaySchedule | C→S | Y | A | HDSCH |
| 0x12 | GetHolidaySchedule | C→S | GetHolidayScheduleResponse | A | HDSCH |
| 0x13 | ClearHolidaySchedule | C→S | Y | A | HDSCH |
| 0x1A | SetUser | C→S | Y | A T | USR |
| 0x1B | GetUser | C→S | GetUserResponse | A | USR |
| 0x1D | ClearUser | C→S | Y | A T | USR |
| 0x22 | SetCredential | C→S | SetCredentialResponse | A T | USR |
| 0x24 | GetCredentialStatus | C→S | GetCredentialStatusResponse | A | USR |
| 0x26 | ClearCredential | C→S | Y | A T | USR |
| 0x27 | UnboltDoor | C→S | Y | O T | UBOLT |
| 0x28 | SetAliroReaderConfig | C→S | Y | A T | ALIRO |
| 0x29 | ClearAliroReaderConfig | C→S | Y | A T | ALIRO |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | DoorLockAlarm | CRITICAL | M |
| 0x01 | DoorStateChange | INFO | DPS |
| 0x02 | LockOperation | INFO | M |
| 0x03 | LockOperationError | INFO | M |
| 0x04 | LockUserChange | INFO | USR |

**Key Enums:**
- **LockStateEnum:** Unlatched(0), UnlockedWithTimeout(1), Unlocked(2), LockJammed(3), Locked(4), NotFullyLocked(5)
- **LockTypeEnum:** DeadBolt(0), Magnetic(1), Other(2), Mortise(3), Rim(4), LatchBolt(5), CylindricalLock(6), TubularLock(7), InterconnectedLock(8), DeadLatchLatchBolt(9), Rotating(10), EletromechanicalLock(11), Unknown(255)
- **CredentialTypeEnum:** ProgrammingPIN(0), PIN(1), RFID(2), Fingerprint(3), FingerVein(4), Face(5), AliroCredentialIssuerKey(6), AliroEvictableEndpointKey(7), AliroNonEvictableEndpointKey(8)
- **OperatingModesBitmap:** Normal(0), Vacation(1), Privacy(2), NoRemoteLockUnlock(3), Passage(4) — bit SET = NOT supported
- **CredentialRulesBitmap:** Single(0), Dual(1), Tri(2)

---

## Window Covering (0x0102)
Rev 7 | PICS: WNCV

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | LF | Lift | O.a+ |
| 1 | TL | Tilt | O.a+ |
| 2 | PA_LF | PositionAwareLift | [LF] |
| 4 | PA_TL | PositionAwareTilt | [TL] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | Type | TypeEnum | desc | F | R V | M |
| 0x0005 | NumberOfActuationsLift | uint16 | all | N | R V | [LF] |
| 0x0006 | NumberOfActuationsTilt | uint16 | all | N | R V | [TL] |
| 0x0007 | ConfigStatus | ConfigStatusBitmap | desc | N | R V | M |
| 0x0008 | CurrentPositionLiftPercentage | percent | all | N P X | R V | [LF & PA_LF] |
| 0x0009 | CurrentPositionTiltPercentage | percent | all | N P X | R V | [TL & PA_TL] |
| 0x000A | OperationalStatus | OperationalStatusBitmap | max 63 | P | R V | M |
| 0x000B | TargetPositionLiftPercent100ths | percent100ths | all | P X | R V | LF & PA_LF |
| 0x000C | TargetPositionTiltPercent100ths | percent100ths | all | P X | R V | TL & PA_TL |
| 0x000D | EndProductType | EndProductTypeEnum | desc | F | R V | M |
| 0x000E | CurrentPositionLiftPercent100ths | percent100ths | max 10000 | N P X | R V | LF & PA_LF |
| 0x000F | CurrentPositionTiltPercent100ths | percent100ths | max 10000 | N P X | R V | TL & PA_TL |
| 0x0017 | Mode | ModeBitmap | max 15 | N | RW VM | M |
| 0x001A | SafetyStatus | SafetyStatusBitmap | desc | P | R V | O |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | UpOrOpen | C→S | Y | O | M |
| 0x01 | DownOrClose | C→S | Y | O | M |
| 0x02 | StopMotion | C→S | Y | O | M |
| 0x05 | GoToLiftPercentage | C→S | Y | O | (LF & PA_LF), [LF] |
| 0x08 | GoToTiltPercentage | C→S | Y | O | (TL & PA_TL), [TL] |

**Key Enums:**
- **TypeEnum:** RollerShade(0), RollerShade2Motor(1), RollerShadeExterior(2), RollerShadeExterior2Motor(3), Drapery(4), Awning(5), Shutter(6), TiltBlindTiltOnly(7), TiltBlindLiftAndTilt(8), ProjectorScreen(9), Unknown(255)
- **EndProductTypeEnum:** RollerShade(0), RomanShade(1), BalloonShade(2), WovenWood(3), PleatedShade(4), CellularShade(5), LayeredShade(6), LayeredShade2D(7), SheerShade(8), TiltOnlyInteriorBlind(9), InteriorBlind(10), VerticalBlindStripCurtain(11), InteriorVenetianBlind(12), ExteriorVenetianBlind(13), LateralLeftCurtain(14), LateralRightCurtain(15), CentralCurtain(16), RollerShutter(17), ExteriorVerticalScreen(18), AwningTerracePatio(19), AwningVerticalScreen(20), TiltOnlyPergola(21), SwingingShutter(22), SlidingShutter(23), Unknown(255)
- **ConfigStatusBitmap:** Operational(0), OnlineReserved(1), LiftMovementReversed(2), LiftPositionAware(3), TiltPositionAware(4), LiftEncoderControlled(5), TiltEncoderControlled(6)
- **ModeBitmap:** MotorDirectionReversed(0), CalibrationMode(1), MaintenanceMode(2), LedFeedback(3)
- **SafetyStatusBitmap:** RemoteLockout(0), TamperDetection(1), FailedCommunication(2), PositionFailure(3), ThermalProtection(4), ObstacleDetected(5), Power(6), StopInput(7), MotorJammed(8), HardwareFailure(9), ManualOperation(10), Protection(11)
