# Lighting Clusters

## On/Off (0x0006)
Rev 6 | PICS: OO

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | LT | Lighting | [!OFFONLY] |
| 1 | DF | DeadFrontBehavior | [!OFFONLY] |
| 2 | OFFONLY | OffOnly | [!(LT \| DF)] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | OnOff | bool | all | N S | R V | M |
| 0x4000 | GlobalSceneControl | bool | all | | R V | LT |
| 0x4001 | OnTime | uint16 | all | | RW VO | LT |
| 0x4002 | OffWaitTime | uint16 | all | | RW VO | LT |
| 0x4003 | StartUpOnOff | StartUpOnOffEnum | desc | N X | RW VM | LT |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | Off | C→S | Y | O | M |
| 0x01 | On | C→S | Y | O | !OFFONLY |
| 0x02 | Toggle | C→S | Y | O | !OFFONLY |
| 0x40 | OffWithEffect | C→S | Y | O | LT |
| 0x41 | OnWithRecallGlobalScene | C→S | Y | O | LT |
| 0x42 | OnWithTimedOff | C→S | Y | O | LT |

**Key Enums:**
- **StartUpOnOffEnum:** Off(0), On(1), Toggle(2)
- **EffectIdentifierEnum:** DelayedAllOff(0x00), DyingLight(0x01)
- **OnOffControlBitmap:** AcceptOnlyWhenOn (bit 0)

---

## Level Control (0x0008)
Rev 6 | PICS: LVL

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | OO | OnOff | O |
| 1 | LT | Lighting | O |
| 2 | FQ | Frequency | P |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | CurrentLevel | uint8 | MinLevel to MaxLevel | N Q S X | R V | M |
| 0x0001 | RemainingTime | uint16 | all | Q | R V | LT |
| 0x0002 | MinLevel | uint8 | 1 to 254 | | R V | O |
| 0x0003 | MaxLevel | uint8 | MinLevel to 254 | | R V | O |
| 0x0004 | CurrentFrequency | uint16 | MinFrequency to MaxFrequency | P Q S | R V | FQ |
| 0x0005 | MinFrequency | uint16 | all | | R V | FQ |
| 0x0006 | MaxFrequency | uint16 | min MinFrequency | | R V | FQ |
| 0x000F | Options | OptionsBitmap | desc | | RW VO | M |
| 0x0010 | OnOffTransitionTime | uint16 | all | | RW VO | O |
| 0x0011 | OnLevel | uint8 | MinLevel to MaxLevel | X | RW VO | M |
| 0x0012 | OnTransitionTime | uint16 | all | X | RW VO | O |
| 0x0013 | OffTransitionTime | uint16 | all | X | RW VO | O |
| 0x0014 | DefaultMoveRate | uint8 | min 1 | X | RW VO | O |
| 0x4000 | StartUpCurrentLevel | uint8 | desc | N X | RW VM | LT |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | MoveToLevel | C→S | Y | O | M |
| 0x01 | Move | C→S | Y | O | M |
| 0x02 | Step | C→S | Y | O | M |
| 0x03 | Stop | C→S | Y | O | M |
| 0x04 | MoveToLevelWithOnOff | C→S | Y | O | M |
| 0x05 | MoveWithOnOff | C→S | Y | O | M |
| 0x06 | StepWithOnOff | C→S | Y | O | M |
| 0x07 | StopWithOnOff | C→S | Y | O | M |
| 0x08 | MoveToClosestFrequency | C→S | Y | O | FQ |

**Key Enums:**
- **OptionsBitmap:** ExecuteIfOff (bit 0), CoupleColorTempToLevel (bit 1)
- **MoveModeEnum:** Up(0), Down(1)
- **StepModeEnum:** Up(0), Down(1)

---

## Color Control (0x0300)
Rev 8 | PICS: CC

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | HS | HueSaturation | EHUE, O |
| 1 | EHUE | EnhancedHue | CL, O |
| 2 | CL | ColorLoop | O |
| 3 | XY | XY | O |
| 4 | CT | ColorTemperature | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | CurrentHue | uint8 | max 254 | N P Q | R V | HS |
| 0x0001 | CurrentSaturation | uint8 | max 254 | N P Q S | R V | HS |
| 0x0002 | RemainingTime | uint16 | max 65534 | Q | R V | O |
| 0x0003 | CurrentX | uint16 | max 65279 | N P Q S | R V | XY |
| 0x0004 | CurrentY | uint16 | max 65279 | N P Q S | R V | XY |
| 0x0005 | DriftCompensation | DriftCompensationEnum | all | | R V | O |
| 0x0006 | CompensationText | string | max 254 | | R V | O |
| 0x0007 | ColorTemperatureMireds | uint16 | max 65279 | N P Q S | R V | CT |
| 0x0008 | ColorMode | ColorModeEnum | all | N | R V | M |
| 0x000F | Options | OptionsBitmap | desc | | RW VO | M |
| 0x0010 | NumberOfPrimaries | uint8 | max 6 | F X | R V | M |
| 0x4000 | EnhancedCurrentHue | uint16 | all | N Q S | R V | EHUE |
| 0x4001 | EnhancedColorMode | EnhancedColorModeEnum | all | N S | R V | M |
| 0x4002 | ColorLoopActive | uint8 | max 1 | N S | R V | CL |
| 0x4003 | ColorLoopDirection | ColorLoopDirectionEnum | all | N S | R V | CL |
| 0x4004 | ColorLoopTime | uint16 | all | N S | R V | CL |
| 0x4005 | ColorLoopStartEnhancedHue | uint16 | all | | R V | CL |
| 0x4006 | ColorLoopStoredEnhancedHue | uint16 | all | | R V | CL |
| 0x400A | ColorCapabilities | ColorCapabilitiesBitmap | max 0x1F | | R V | M |
| 0x400B | ColorTempPhysicalMinMireds | uint16 | 1 to 65279 | | R V | CT |
| 0x400C | ColorTempPhysicalMaxMireds | uint16 | max 65279 | | R V | CT |
| 0x400D | CoupleColorTempToLevelMinMireds | uint16 | desc | | R V | CT |
| 0x4010 | StartUpColorTemperatureMireds | uint16 | 1 to 65279 | N X | RW VM | CT |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | MoveToHue | C→S | Y | O | HS |
| 0x01 | MoveHue | C→S | Y | O | HS |
| 0x02 | StepHue | C→S | Y | O | HS |
| 0x03 | MoveToSaturation | C→S | Y | O | HS |
| 0x04 | MoveSaturation | C→S | Y | O | HS |
| 0x05 | StepSaturation | C→S | Y | O | HS |
| 0x06 | MoveToHueAndSaturation | C→S | Y | O | HS |
| 0x07 | MoveToColor | C→S | Y | O | XY |
| 0x08 | MoveColor | C→S | Y | O | XY |
| 0x09 | StepColor | C→S | Y | O | XY |
| 0x0A | MoveToColorTemperature | C→S | Y | O | CT |
| 0x40 | EnhancedMoveToHue | C→S | Y | O | EHUE |
| 0x41 | EnhancedMoveHue | C→S | Y | O | EHUE |
| 0x42 | EnhancedStepHue | C→S | Y | O | EHUE |
| 0x43 | EnhancedMoveToHueAndSaturation | C→S | Y | O | EHUE |
| 0x44 | ColorLoopSet | C→S | Y | O | CL |
| 0x47 | StopMoveStep | C→S | Y | O | HS \| XY \| CT |
| 0x4B | MoveColorTemperature | C→S | Y | O | CT |
| 0x4C | StepColorTemperature | C→S | Y | O | CT |

**Key Enums:**
- **ColorModeEnum:** CurrentHueAndCurrentSaturation(0), CurrentXAndCurrentY(1), ColorTemperatureMireds(2)
- **EnhancedColorModeEnum:** CurrentHueAndCurrentSaturation(0), CurrentXAndCurrentY(1), ColorTemperatureMireds(2), EnhancedCurrentHueAndCurrentSaturation(3)
- **DirectionEnum:** Shortest(0), Longest(1), Up(2), Down(3)
- **MoveModeEnum:** Stop(0), Up(1), Down(3)
- **ColorLoopActionEnum:** Deactivate(0), ActivateFromColorLoopStartEnhancedHue(1), ActivateFromEnhancedCurrentHue(2)
- **ColorLoopDirectionEnum:** Decrement(0), Increment(1)
- **ColorCapabilitiesBitmap:** HueSaturation(0), EnhancedHue(1), ColorLoop(2), XY(3), ColorTemperature(4)
