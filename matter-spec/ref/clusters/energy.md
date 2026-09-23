# Energy Clusters

## Electrical Power Measurement (0x0090)
Rev 3 | PICS: EPM

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | DIRC | DirectCurrent | O.a+ |
| 1 | ALTC | AlternatingCurrent | O.a+ |
| 2 | POLY | PolyphasePower | [ALTC] |
| 3 | HARM | Harmonics | [ALTC] |
| 4 | PWRQ | PowerQuality | [ALTC] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | PowerMode | PowerModeEnum | all | | R V | M |
| 0x0001 | NumberOfMeasurementTypes | uint8 | all | | R V | M |
| 0x0002 | Accuracy | list[MeasurementAccuracyStruct] | all | | R V | M |
| 0x0003 | Ranges | list[MeasurementRangeStruct] | all | | R V | O |
| 0x0004 | Voltage | voltage-mV | all | Q X | R V | O |
| 0x0005 | ActiveCurrent | amperage-mA | all | Q X | R V | O |
| 0x0006 | ReactiveCurrent | amperage-mA | all | Q X | R V | [ALTC] |
| 0x0007 | ApparentCurrent | amperage-mA | all | Q X | R V | [ALTC] |
| 0x0008 | ActivePower | power-mW | all | Q X | R V | M |
| 0x0009 | ReactivePower | power-mVAR | all | Q X | R V | [ALTC] |
| 0x000A | ApparentPower | power-mVA | all | Q X | R V | [ALTC] |
| 0x000B | RMSVoltage | voltage-mV | all | Q X | R V | [ALTC] |
| 0x000C | RMSCurrent | amperage-mA | all | Q X | R V | [ALTC] |
| 0x000D | RMSPower | power-mW | all | Q X | R V | [ALTC] |
| 0x000E | Frequency | int64 | 0 to 1000000 | Q X | R V | [ALTC] |
| 0x000F | HarmonicCurrents | list[HarmonicMeasurementStruct] | max 25 | Q X | R V | HARM |
| 0x0010 | HarmonicPhases | list[HarmonicMeasurementStruct] | max 25 | Q X | R V | PWRQ |
| 0x0011 | PowerFactor | int64 | -10000 to 10000 | Q X | R V | [ALTC] |
| 0x0012 | NeutralCurrent | amperage-mA | all | Q X | R V | [POLY] |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | MeasurementPeriodRanges | INFO | O |

**Key Enums:**
- **PowerModeEnum:** Unknown(0), DC(1), AC(2)

---

## Electrical Energy Measurement (0x0091)
Rev 2 | PICS: EEM

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | IMPE | ImportedEnergy | O.a+ |
| 1 | EXPE | ExportedEnergy | O.a+ |
| 2 | CUME | CumulativeEnergy | O.b+ |
| 3 | PERE | PeriodicEnergy | O.b+ |
| 4 | APPE | ApparentEnergy | P, O |
| 5 | REAE | ReactiveEnergy | P, O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | Accuracy | MeasurementAccuracyStruct | all | | R V | M |
| 0x0001 | CumulativeEnergyImported | EnergyMeasurementStruct | all | Q X | R V | IMPE & CUME |
| 0x0002 | CumulativeEnergyExported | EnergyMeasurementStruct | all | Q X | R V | EXPE & CUME |
| 0x0003 | PeriodicEnergyImported | EnergyMeasurementStruct | all | Q X | R V | IMPE & PERE |
| 0x0004 | PeriodicEnergyExported | EnergyMeasurementStruct | all | Q X | R V | EXPE & PERE |
| 0x0005 | CumulativeEnergyReset | CumulativeEnergyResetStruct | all | Q X | R V | [CUME] |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | CumulativeEnergyMeasured | INFO | CUME |
| 0x01 | PeriodicEnergyMeasured | INFO | PERE |

**Data Types:**
- **EnergyMeasurementStruct:** Energy (energy-mWh), StartTimestamp, EndTimestamp, StartSystime, EndSystime, ApparentEnergy (P, APPE), ReactiveEnergy (P, REAE)

---

## Energy EVSE (0x0099)
Rev 4 | PICS: EEVSE

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | PREF | ChargingPreferences | M |
| 1 | SOC | SoCReporting | O |
| 2 | PNC | PlugAndCharge | O |
| 3 | RFID | RFID | O |
| 4 | V2X | V2X | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | State | StateEnum | all | X | R V | M |
| 0x0001 | SupplyState | SupplyStateEnum | all | | R V | M |
| 0x0002 | FaultState | FaultStateEnum | all | | R V | M |
| 0x0003 | ChargingEnabledUntil | epoch-s | all | X | R V | M |
| 0x0004 | DischargingEnabledUntil | epoch-s | all | X | R V | V2X |
| 0x0005 | CircuitCapacity | amperage-mA | min 0 | | R V | M |
| 0x0006 | MinimumChargeCurrent | amperage-mA | min 0 | | R V | M |
| 0x0007 | MaximumChargeCurrent | amperage-mA | min 0 | | R V | M |
| 0x0008 | MaximumDischargeCurrent | amperage-mA | min 0 | | R V | V2X |
| 0x0009 | UserMaximumChargeCurrent | amperage-mA | desc | | RW VO | O |
| 0x000A | RandomizationDelayWindow | elapsed-s | max 86400 | | RW VO | O |
| 0x0023 | NextChargeStartTime | epoch-s | all | X | R V | PREF |
| 0x0024 | NextChargeTargetTime | epoch-s | all | X | R V | PREF |
| 0x0025 | NextChargeRequiredEnergy | energy-mWh | min 0 | X | R V | PREF |
| 0x0026 | NextChargeTargetSoC | percent | all | X | R V | PREF |
| 0x0027 | ApproximateEVEfficiency | uint16 | all | X | RW VO | [PREF] |
| 0x0030 | StateOfCharge | percent | all | X | R V | SOC |
| 0x0031 | BatteryCapacity | energy-mWh | min 0 | X | R V | SOC |
| 0x0032 | VehicleID | string | max 32 | X | R V | PNC |
| 0x0040 | SessionID | uint32 | all | X | R V | M |
| 0x0041 | SessionDuration | elapsed-s | all | Q | R V | M |
| 0x0042 | SessionEnergyCharged | energy-mWh | min 0 | Q | R V | M |
| 0x0043 | SessionEnergyDischarged | energy-mWh | min 0 | Q | R V | V2X |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x01 | Disable | C→S | Y | O | M |
| 0x02 | EnableCharging | C→S | Y | O | M |
| 0x03 | EnableDischarging | C→S | Y | O | V2X |
| 0x04 | StartDiagnostics | C→S | Y | O | O |
| 0x05 | SetTargets | C→S | Y | O | PREF |
| 0x06 | GetTargets | C→S | GetTargetsResponse | O | PREF |
| 0x07 | ClearTargets | C→S | Y | O | PREF |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | EVConnected | INFO | M |
| 0x01 | EVNotDetected | INFO | M |
| 0x02 | EnergyTransferStarted | INFO | M |
| 0x03 | EnergyTransferStopped | INFO | M |
| 0x04 | Fault | CRITICAL | M |
| 0x05 | RFID | INFO | [RFID] |

**Key Enums:**
- **StateEnum:** NotPluggedIn(0), PluggedInNoDemand(1), PluggedInDemand(2), PluggedInCharging(3), PluggedInDischarging(4/V2X), SessionEnding(5), Fault(6)
- **SupplyStateEnum:** Disabled(0), ChargingEnabled(1), DischargingEnabled(2/[V2X]), DisabledError(3), DisabledDiagnostics(4), Enabled(5/[V2X])
- **FaultStateEnum:** NoError(0), GroundFault(1), OverTemperature(2), WrongVoltage(3), etc.

---

## Device Energy Management (0x0098)
Rev 4 | PICS: DEM

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | PA | PowerAdjustment | O |
| 1 | PFR | PowerForecastReporting | O |
| 2 | SFR | StateForecastReporting | O |
| 3 | STA | StartTimeAdjustment | O |
| 4 | PAU | Pausable | O |
| 5 | FA | ForecastAdjustment | O |
| 6 | CON | ConstraintBasedAdjustment | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | ESAType | ESATypeEnum | all | | R V | M |
| 0x0001 | ESACanGenerate | bool | all | | R V | M |
| 0x0002 | ESAState | ESAStateEnum | all | | R V | M |
| 0x0003 | AbsMinPower | power-mW | all | | R V | M |
| 0x0004 | AbsMaxPower | power-mW | min AbsMinPower | | R V | M |
| 0x0005 | PowerAdjustmentCapability | PowerAdjustCapabilityStruct | all | Q X | R V | PA |
| 0x0006 | Forecast | ForecastStruct | all | Q X | R V | PFR \| SFR |
| 0x0007 | OptOutState | OptOutStateEnum | all | | R V | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | PowerAdjustRequest | C→S | Y | O | PA |
| 0x01 | CancelPowerAdjustRequest | C→S | Y | O | PA |
| 0x02 | StartTimeAdjustRequest | C→S | Y | O | STA |
| 0x03 | PauseRequest | C→S | Y | O | PAU |
| 0x04 | ResumeRequest | C→S | Y | O | PAU |
| 0x05 | ModifyForecastRequest | C→S | Y | O | FA |
| 0x06 | RequestConstraintBasedForecast | C→S | Y | O | CON |
| 0x07 | CancelRequest | C→S | Y | O | STA \| FA \| CON |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | PowerAdjustStart | INFO | PA |
| 0x01 | PowerAdjustEnd | INFO | PA |
| 0x02 | Paused | INFO | PAU |
| 0x03 | Resumed | INFO | PAU |

**Key Enums:**
- **ESATypeEnum:** Unspecified(0), EVSE(1), SpaceHeating(2), WaterHeating(3), SpaceCooling(4), SpaceHeatingCooling(5), BatteryStorage(6), SolarPV(7), FridgeFreezer(8), WashingMachine(9), Dishwasher(10), Cooking(11), HomeWaterPump(12), IrrigationWaterPump(13), PoolPump(14), Other(255)
- **ESAStateEnum:** Offline(0), Online(1), Fault(2), NotFlexible(3), Paused(4), PowerAdjustActive(5)
- **OptOutStateEnum:** NoOptOut(0), LocalOptOut(1), GridOptOut(2), OptOut(3)

---

## Power Topology (0x009C)
Rev 1 | PICS: PWRTL

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | NODE | NodeTopology | O.a |
| 1 | TREE | TreeTopology | O.a |
| 2 | SET | SetTopology | O.a |
| 3 | DYPF | DynamicPowerFlow | [SET] |
| 4 | CIRC | ElectricalCircuit | P, O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | AvailableEndpoints | list[endpoint-no] | max 20 | | R V | SET |
| 0x0001 | ActiveEndpoints | list[endpoint-no] | max 20 | | R V | DYPF |

No commands or events.
