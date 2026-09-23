# Measurement & Sensing Clusters

## Temperature Measurement (0x0402)
Rev 4 | PICS: TMP

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MeasuredValue | temperature | MinMeasuredValue to MaxMeasuredValue | P X | R V | M |
| 0x0001 | MinMeasuredValue | temperature | -27315 to 32766 | X | R V | M |
| 0x0002 | MaxMeasuredValue | temperature | min (MinMeasuredValue + 1) | X | R V | M |
| 0x0003 | Tolerance | uint16 | max 2048 | | R V | O |

No commands or events. Temperature in 0.01C units.

---

## Relative Humidity Measurement (0x0405)
Rev 3 | PICS: RH

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MeasuredValue | uint16 | MinMeasuredValue to MaxMeasuredValue | P X | R V | M |
| 0x0001 | MinMeasuredValue | uint16 | max 9999 | X | R V | M |
| 0x0002 | MaxMeasuredValue | uint16 | (MinMeasuredValue + 1) to 10000 | X | R V | M |
| 0x0003 | Tolerance | uint16 | max 2048 | | R V | O |

No commands or events. Value in 0.01% RH units.

---

## Pressure Measurement (0x0403)
Rev 3 | PICS: PRS

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | EXT | Extended | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MeasuredValue | int16 | MinMeasuredValue to MaxMeasuredValue | P X | R V | M |
| 0x0001 | MinMeasuredValue | int16 | max 32766 | X | R V | M |
| 0x0002 | MaxMeasuredValue | int16 | (MinMeasuredValue + 1) to 32767 | X | R V | M |
| 0x0003 | Tolerance | uint16 | max 2048 | | R V | O |
| 0x0010 | ScaledValue | int16 | MinScaledValue to MaxScaledValue | X | R V | EXT |
| 0x0011 | MinScaledValue | int16 | max 32766 | X | R V | EXT |
| 0x0012 | MaxScaledValue | int16 | (MinScaledValue + 1) to 32767 | X | R V | EXT |
| 0x0013 | ScaledTolerance | uint16 | max 2048 | | R V | [EXT] |
| 0x0014 | Scale | int8 | min -127 | | R V | EXT |

No commands or events. MeasuredValue in kPa units.

---

## Flow Measurement (0x0404)
Rev 3 | PICS: FLW

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MeasuredValue | uint16 | MinMeasuredValue to MaxMeasuredValue | P X | R V | M |
| 0x0001 | MinMeasuredValue | uint16 | max 65533 | X | R V | M |
| 0x0002 | MaxMeasuredValue | uint16 | min (MinMeasuredValue + 1) | X | R V | M |
| 0x0003 | Tolerance | uint16 | max 2048 | | R V | O |

No commands or events. Value in 0.1 m3/h units.

---

## Illuminance Measurement (0x0400)
Rev 3 | PICS: ILL

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MeasuredValue | uint16 | 0, MinMeasuredValue to MaxMeasuredValue | P X | R V | M |
| 0x0001 | MinMeasuredValue | uint16 | 1 to 65533 | X | R V | M |
| 0x0002 | MaxMeasuredValue | uint16 | min (MinMeasuredValue + 1) | X | R V | M |
| 0x0003 | Tolerance | uint16 | max 2048 | | R V | O |
| 0x0004 | LightSensorType | LightSensorTypeEnum | all | X | R V | O |

**Key Enums:**
- **LightSensorTypeEnum:** Photodiode(0), CMOS(1)

No commands or events. Value = 10000 * log10(lux) + 1.

---

## Occupancy Sensing (0x0406)
Rev 5 | PICS: OCC

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | OTHER | Other | O.a+ |
| 1 | PIR | PassiveInfrared | O.a+ |
| 2 | US | Ultrasonic | O.a+ |
| 3 | PHY | PhysicalContact | O.a+ |
| 4 | AIR | ActiveInfrared | O.a+ |
| 5 | RAD | Radar | O.a+ |
| 6 | RFS | RFSensing | O.a+ |
| 7 | VIS | Vision | O.a+ |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | Occupancy | OccupancyBitmap | 0 to 1 | P | R V | M |
| 0x0001 | OccupancySensorType | OccupancySensorTypeEnum | desc | F | R V | M, D |
| 0x0002 | OccupancySensorTypeBitmap | OccupancySensorTypeBitmap | 0 to 7 | F | R V | M, D |
| 0x0003 | HoldTime | uint16 | desc | N | RW VM | O |
| 0x0004 | HoldTimeLimits | HoldTimeLimitsStruct | all | F | R V | HoldTime |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | OccupancyChanged | INFO | O |

**Key Enums:**
- **OccupancySensorTypeEnum:** PIR(0), Ultrasonic(1), PIRAndUltrasonic(2), PhysicalContact(3)

---

## Air Quality (0x005B)
Rev 1 | PICS: AIRQUAL

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | FAIR | Fair | O |
| 1 | MOD | Moderate | O |
| 2 | VPOOR | VeryPoor | O |
| 3 | XPOOR | ExtremelyPoor | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | AirQuality | AirQualityEnum | desc | | R V | M |

**Key Enums:**
- **AirQualityEnum:** Unknown(0), Good(1), Fair(2), Moderate(3), Poor(4), VeryPoor(5), ExtremelyPoor(6)

No commands or events.

---

## Concentration Measurement (Base)
Rev 3 | PICS: CONC

Shared base definition for all concentration measurement clusters:

| Cluster ID | Name | PICS |
|-----------|------|------|
| 0x040C | Carbon Monoxide | CMOCONC |
| 0x040D | Carbon Dioxide | CDOCONC |
| 0x0413 | Nitrogen Dioxide | NDOCONC |
| 0x0415 | Ozone | OZCONC |
| 0x042A | PM2.5 | PMICONC |
| 0x042B | Formaldehyde | FLDCONC |
| 0x042C | PM1 | PMHCONC |
| 0x042D | PM10 | PMKCONC |
| 0x042E | TVOC | TVOCCONC |
| 0x042F | Radon | RNCONC |

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | MEA | NumericMeasurement | O.a+ |
| 1 | LEV | LevelIndication | O.a+ |
| 2 | MED | MediumLevel | [LEV] |
| 3 | CRI | CriticalLevel | [LEV] |
| 4 | PEA | PeakMeasurement | [MEA] |
| 5 | AVG | AverageMeasurement | [MEA] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MeasuredValue | single | MinMeasuredValue to MaxMeasuredValue | P X | R V | MEA |
| 0x0001 | MinMeasuredValue | single | all | X | R V | MEA |
| 0x0002 | MaxMeasuredValue | single | min MinMeasuredValue | X | R V | MEA |
| 0x0003 | PeakMeasuredValue | single | MinMeasuredValue to MaxMeasuredValue | P X | R V | PEA |
| 0x0004 | PeakMeasuredValueWindow | elapsed-s | max 604800 | P | R V | PEA |
| 0x0005 | AverageMeasuredValue | single | MinMeasuredValue to MaxMeasuredValue | P X | R V | AVG |
| 0x0006 | AverageMeasuredValueWindow | elapsed-s | max 604800 | P | R V | AVG |
| 0x0007 | Uncertainty | single | all | | R V | [MEA] |
| 0x0008 | MeasurementUnit | MeasurementUnitEnum | all | F | R V | MEA |
| 0x0009 | MeasurementMedium | MeasurementMediumEnum | all | F | R V | M |
| 0x000A | LevelValue | LevelValueEnum | all | | R V | LEV |

**Key Enums:**
- **MeasurementUnitEnum:** PPM(0), PPB(1), PPT(2), MGM3(3), UGM3(4), NGM3(5), PM3(6), BQM3(7)
- **MeasurementMediumEnum:** Air(0), Water(1), Soil(2)
- **LevelValueEnum:** Unknown(0), Low(1), Medium(2), High(3), Critical(4)

No commands or events.
