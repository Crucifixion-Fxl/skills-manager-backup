# Matter Cluster Index

Complete cluster lookup table for Matter specification v1.5.
Only includes clusters with certification state **C** (Certified) or **P** (Provisional).

> **Cluster Details:** For full attribute/command/event tables, see `ref/clusters/*.md` files organized by category (lighting, hvac, measurement, closures, appliances, energy, media, robots, safety, general, network, camera, tls).

## General

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0003 | Identify | I | — | Utility | Endpoint | C |
| 0x0004 | Groups | G | — | Utility | Endpoint | C |
| 0x0006 | On/Off | OO | — | Application | Endpoint | C |
| 0x0045 | Boolean State | BOOL | — | Application | Endpoint | C |
| 0x0050 | Mode Select | MODE | — | Application | Endpoint | C |
| 0x0060 | Operational State | OPSTATE | — | Application | Endpoint | C |
| 0x0062 | Scenes Management | S | — | Application | Endpoint | C |
| 0x0150 | Service Area | SEAR | — | Application | Endpoint | C |
| 0x0503 | Wake On LAN | WOLAN | — | Application | Endpoint | C |
| 0x0508 | Low Power | MDLPWR | — | Application | Endpoint | C |

## System / Utility

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x001D | Descriptor | DESC | — | Utility | Endpoint | C |
| 0x001E | Binding | BIND | — | Utility | Endpoint | C |
| 0x001F | Access Control | ACL | — | Utility | Node | C |
| 0x0025 | Actions | ACT | — | Utility | Endpoint | C |
| 0x0039 | Bridged Device Basic Information | BRBINFO | BINFO | Utility | Endpoint | C |
| 0x0040 | Fixed Label | FLABEL | LABEL | Utility | Endpoint | C |
| 0x0041 | User Label | ULABEL | LABEL | Utility | Endpoint | C |
| 0x0046 | ICD Management | ICDM | — | Utility | Node | C |
| 0x0750 | Ecosystem Information | ECOINFO | — | Utility | Endpoint | C |

## Service & Device Management

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0028 | Basic Information | BINFO | — | Utility | Node | C |
| 0x0029 | OTA Software Update Provider | OTAS | — | Utility | Node | C |
| 0x002A | OTA Software Update Requestor | OTAC | — | Utility | Node | C |
| 0x002B | Localization Configuration | LCFG | — | Utility | Node | C |
| 0x002C | Time Format Localization | LTIME | — | Utility | Node | C |
| 0x002D | Unit Localization | LUNIT | — | Utility | Node | C |
| 0x002E | Power Source Configuration | PSCFG | — | Utility | Node | C |
| 0x002F | Power Source | PS | — | Utility | Node | C |
| 0x0030 | General Commissioning | CGEN | — | Utility | Node | C |
| 0x0031 | Network Commissioning | CNET | — | Utility | Node | C |
| 0x0032 | Diagnostics Log | DGLOG | — | Utility | Node | C |
| 0x0033 | General Diagnostics | DGGEN | — | Utility | Node | C |
| 0x0034 | Software Diagnostics | DGSW | — | Utility | Node | C |
| 0x0035 | Thread Network Diagnostics | DGTHREAD | — | Utility | Node | C |
| 0x0036 | WiFi Network Diagnostics | DGWIFI | — | Utility | Node | C |
| 0x0037 | Ethernet Network Diagnostics | DGETHER | — | Utility | Node | C |
| 0x0038 | Time Synchronization | TIMESYNC | — | Utility | Node | C |
| 0x003C | Administrator Commissioning | CADMIN | — | Utility | Node | C |
| 0x003E | Operational Credentials | OPCREDS | — | Utility | Node | C |
| 0x003F | Group Key Management | GRPKEY | — | Utility | Node | C |
| 0x0751 | Commissioner Control | CCTRL | — | Utility | Node | C |
| 0x0752 | Joint Fabric Datastore | JFDS | — | Utility | Node | P |
| 0x0753 | Joint Fabric Administrator | JFPKI | — | Utility | Node | P |

## Lighting

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0008 | Level Control | LVL | — | Application | Endpoint | C |
| 0x0300 | Color Control | CC | — | Application | Endpoint | C |

## Closures

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0101 | Door Lock | DRLK | — | Application | Endpoint | C |
| 0x0102 | Window Covering | WNCV | — | Application | Endpoint | C |

## HVAC

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0080 | Boolean State Configuration | BOOLCFG | — | Application | Endpoint | C |
| 0x0081 | Valve Configuration and Control | VCC | — | Application | Endpoint | C |
| 0x0200 | Pump Configuration and Control | PCC | — | Application | Endpoint | C |
| 0x0201 | Thermostat | TSTAT | — | Application | Endpoint | C |
| 0x0202 | Fan Control | FAN | — | Application | Endpoint | C |
| 0x0204 | Thermostat User Interface Configuration | TSUIC | — | Application | Endpoint | C |

## Measurement & Sensing

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x005B | Air Quality | AIRQUAL | — | Application | Endpoint | C |
| 0x005C | Smoke/CO Alarm | SMOKECO | — | Application | Endpoint | C |
| 0x005D | Dishwasher Alarm | DISHALM | — | Application | Endpoint | C |
| 0x0071 | HEPA Filter Monitoring | HEPAFREPMON | REPM | Application | Endpoint | C |
| 0x0072 | Activated Carbon Filter Monitoring | ACFREPMON | REPM | Application | Endpoint | C |
| 0x0079 | Water Tank Monitoring | — | REPM | Application | Endpoint | C |
| 0x0400 | Illuminance Measurement | ILL | — | Application | Endpoint | C |
| 0x0402 | Temperature Measurement | TMP | — | Application | Endpoint | C |
| 0x0403 | Pressure Measurement | PRS | — | Application | Endpoint | C |
| 0x0404 | Flow Measurement | FLW | — | Application | Endpoint | C |
| 0x0405 | Relative Humidity Measurement | RH | — | Application | Endpoint | C |
| 0x0406 | Occupancy Sensing | OCC | — | Application | Endpoint | C |
| 0x040C | Carbon Monoxide Concentration | CMOCONC | CONC | Application | Endpoint | C |
| 0x040D | Carbon Dioxide Concentration | CDOCONC | CONC | Application | Endpoint | C |
| 0x0413 | Nitrogen Dioxide Concentration | NDOCONC | CONC | Application | Endpoint | C |
| 0x0415 | Ozone Concentration | OZCONC | CONC | Application | Endpoint | C |
| 0x042A | PM2.5 Concentration | PMICONC | CONC | Application | Endpoint | C |
| 0x042B | Formaldehyde Concentration | FLDCONC | CONC | Application | Endpoint | C |
| 0x042C | PM1 Concentration | PMHCONC | CONC | Application | Endpoint | C |
| 0x042D | PM10 Concentration | PMKCONC | CONC | Application | Endpoint | C |
| 0x042E | TVOC Concentration | TVOCCONC | CONC | Application | Endpoint | C |
| 0x042F | Radon Concentration | RNCONC | CONC | Application | Endpoint | C |

## Home Appliances

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0048 | Oven Cavity Operational State | OVENOPSTATE | OPSTATE | Application | Endpoint | C |
| 0x0049 | Oven Mode | OTCCM | MODB | Application | Endpoint | C |
| 0x004A | Laundry Dryer Controls | DRYERCTRL | — | Application | Endpoint | C |
| 0x0051 | Laundry Mode | LWM | MODB | Application | Endpoint | C |
| 0x0052 | Refrigerator & TCC Mode | TCC | MODB | Application | Endpoint | C |
| 0x0053 | Washer Controls | WASHCTRL | — | Application | Endpoint | C |
| 0x0054 | RVC Run Mode | RVCRUN | MODB | Application | Endpoint | C |
| 0x0055 | RVC Clean Mode | RVCCLEAN | MODB | Application | Endpoint | C |
| 0x0056 | Temperature Control | TCTL | — | Application | Endpoint | C |
| 0x0057 | Refrigerator Alarm | REFALM | ALMBASE | Application | Endpoint | C |
| 0x0058 | Dishwasher Control | DISHCTRL | — | Application | Endpoint | C |
| 0x0059 | Dishwasher Mode | DISHMS | MODB | Application | Endpoint | C |
| 0x005A | Dishwasher Operational State | DISHOPSTATE | OPSTATE | Application | Endpoint | C |
| 0x005E | Microwave Oven Mode | MWOM | MODB | Application | Endpoint | C |
| 0x005F | Microwave Oven Control | MWOCTRL | — | Application | Endpoint | C |

## Energy Management

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0090 | Electrical Power Measurement | POWMNT | — | Application | Endpoint | C |
| 0x0091 | Electrical Energy Measurement | ELECMNT | — | Application | Endpoint | C |
| 0x0094 | Water Heater Management | EWATERHTR | — | Application | Endpoint | C |
| 0x0097 | Messages | MESS | — | Application | Endpoint | C |
| 0x0098 | Device Energy Management | DEM | — | Application | Endpoint | C |
| 0x0099 | Energy EVSE | EEVSE | — | Application | Endpoint | C |
| 0x009B | Energy Preferences | EPREF | — | Application | Endpoint | C |
| 0x009C | Power Topology | PWRTL | — | Application | Endpoint | C |
| 0x009D | Energy EVSE Mode | EEVSEM | MODB | Application | Endpoint | C |
| 0x009E | Water Heater Mode | WHM | MODB | Application | Endpoint | C |
| 0x009F | Device Energy Management Mode | DEMM | MODB | Application | Endpoint | C |
| 0x0B07 | Commodity Metering | COMMTR | — | Application | Endpoint | P |

## Robots

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0061 | RVC Operational State | RVCOPSTATE | OPSTATE | Application | Endpoint | C |

## Network Infrastructure

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0451 | Wi-Fi Network Management | WIFINM | — | Application | Endpoint | C |
| 0x0452 | Thread Border Router Management | TBRM | — | Application | Endpoint | C |
| 0x0453 | Thread Network Directory | THNETDIR | — | Application | Endpoint | C |

## Cameras & Video

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0550 | Zone Management | ZMGMT | — | Application | Endpoint | C |
| 0x0551 | Camera AV Stream Management | AVSM | — | Application | Endpoint | C |
| 0x0552 | Camera AV Settings User-Level Management | AVSETTINGS | — | Application | Endpoint | C |
| 0x0553 | WebRTC Transport Provider | WEBRTCPROV | — | Application | Endpoint | C |
| 0x0554 | WebRTC Transport Requestor | WEBRTCREQ | — | Application | Endpoint | C |
| 0x0555 | Push AV Stream Transport | PAVST | — | Application | Endpoint | C |
| 0x0556 | Chime | CHIME | — | Application | Endpoint | C |

## TLS Infrastructure

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0801 | TLS Certificate Management | TLSCERT | — | Utility | Node | C |
| 0x0802 | TLS Client Management | TLSCLIENT | — | Utility | Node | C |

## Media

| ID | Name | PICS | Base | Class | Scope | State |
|----|------|------|------|-------|-------|-------|
| 0x0504 | TV Channel | TVCHAN | — | Application | Endpoint | C |
| 0x0505 | Target Navigator | TARNAV | — | Application | Endpoint | C |
| 0x0506 | Media Playback | MDPBACK | — | Application | Endpoint | C |
| 0x0507 | Media Input | MDINPUT | — | Application | Endpoint | C |
| 0x0509 | Keypad Input | MDKEYP | — | Application | Endpoint | C |
| 0x050A | Content Launcher | MDCONT | — | Application | Endpoint | C |
| 0x050B | Audio Output | AUDOUT | — | Application | Endpoint | C |
| 0x050C | Application Launcher | APPRUN | — | Application | Endpoint | C |
| 0x050D | Application Basic | APPBAS | — | Application | Endpoint | C |
| 0x050E | Account Login | ALOGIN | — | Application | Endpoint | C |
| 0x050F | Content Control | CONCON | — | Application | Endpoint | C |
| 0x0510 | Content App Observer | APPOBSERVER | — | Application | Endpoint | C |

## Summary

- **Certified (C):** ~127 clusters
- **Provisional (P):** 3 clusters (Joint Fabric Datastore, Joint Fabric Administrator, Commodity Metering)

## Base Clusters (Derived From)

Several clusters are derived from base cluster definitions:

| Base Code | Base Cluster | Derived Clusters |
|-----------|-------------|-----------------|
| OPSTATE | Operational State | Oven Cavity Op State, Dishwasher Op State, RVC Op State |
| MODB | Mode Base | Oven Mode, Laundry Mode, RVC Run/Clean Mode, Dishwasher Mode, Microwave Oven Mode, Energy EVSE Mode, Water Heater Mode, DEM Mode, Refrigerator & TCC Mode |
| CONC | Concentration Measurement | CO, CO2, NO2, O3, PM2.5, PM1, PM10, TVOC, Formaldehyde, Radon |
| REPM | Resource Monitoring | HEPA Filter, Activated Carbon Filter, Water Tank |
| ALMBASE | Alarm Base | Refrigerator Alarm |
| LABEL | Label | Fixed Label, User Label |
| BINFO | Basic Information | Bridged Device Basic Information |
