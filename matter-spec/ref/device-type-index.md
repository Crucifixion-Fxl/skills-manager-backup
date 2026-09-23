# Matter Device Type Index

Device type lookup with mandatory (M) and optional (O) cluster requirements.
Matter specification v1.5.

## Utility Device Types

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0016 | Root Node | Node | Descriptor(M), Access Control(M), Basic Information(M), General Commissioning(M), Network Commissioning(M), General Diagnostics(M), Administrator Commissioning(M), Operational Credentials(M), Group Key Management(M) |
| 0x0011 | Power Source | Utility | Power Source(M), Descriptor(M) |
| 0x0012 | OTA Requestor | Utility | OTA Software Update Requestor(M-Server), OTA Software Update Provider(M-Client) |
| 0x0014 | OTA Provider | Utility | OTA Software Update Provider(M-Server) |
| 0x0013 | Bridged Node | Utility | Descriptor(M), Bridged Device Basic Information(M), Identify(M) |
| 0x050D | Device Energy Management | Utility | Device Energy Management(M) |
| 0x0510 | Electrical Sensor | Utility | Power Topology(M), Electrical Power Measurement(O), Electrical Energy Measurement(O) |
| 0x0019 | Secondary Network Interface | Utility | Network Commissioning(M) |
| 0x0130 | Joint Fabric Administrator | Utility | Access Control(M) |

## Lighting

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0100 | On/Off Light | Simple | Identify(M), Groups(M), On/Off(M), Scenes Management(M), Level Control(O) |
| 0x0101 | Dimmable Light | Simple | Identify(M), Groups(M), On/Off(M), Level Control(M), Scenes Management(M) |
| 0x010C | Color Temperature Light | Simple | Identify(M), Groups(M), On/Off(M), Level Control(M), Color Control(M), Scenes Management(M) |
| 0x010D | Extended Color Light | Simple | Identify(M), Groups(M), On/Off(M), Level Control(M), Color Control(M), Scenes Management(M) |

## Switches & Controls

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0103 | On/Off Light Switch | Simple | Identify(M), On/Off(M-Client) |
| 0x0104 | Dimmer Switch | Simple | Identify(M), On/Off(M-Client), Level Control(M-Client) |
| 0x0105 | Color Dimmer Switch | Simple | Identify(M), On/Off(M-Client), Level Control(M-Client), Color Control(M-Client) |
| 0x000F | Generic Switch | Simple | Identify(M), Switch(M) |
| 0x0840 | Control Bridge | Simple | Identify(M), Descriptor(M) |
| 0x0304 | Pump Controller | Simple | Identify(M), Pump Configuration and Control(M-Client) |

## Smart Plugs & Actuators

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x010A | On/Off Plug-in Unit | Simple | Identify(M), Groups(M), On/Off(M), Scenes Management(M), Level Control(O) |
| 0x010B | Dimmable Plug-In Unit | Simple | Identify(M), Groups(M), On/Off(M), Level Control(M), Scenes Management(M) |
| 0x010F | Mounted On/Off Control | Simple | Identify(M), Groups(M), On/Off(M), Scenes Management(M) |
| 0x0110 | Mounted Dimmable Load Control | Simple | Identify(M), Groups(M), On/Off(M), Level Control(M), Scenes Management(M) |
| 0x0303 | Pump | Simple | Identify(M), On/Off(M), Pump Configuration and Control(M) |
| 0x0042 | Water Valve | Simple | Identify(M), On/Off(M), Valve Configuration and Control(M) |
| 0x0040 | Irrigation System | Simple | Identify(M), On/Off(M), Valve Configuration and Control(M) |

## Sensors

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0015 | Contact Sensor | Simple | Identify(M), Boolean State(M), Boolean State Configuration(O) |
| 0x0106 | Light Sensor | Simple | Identify(M), Illuminance Measurement(M) |
| 0x0107 | Occupancy Sensor | Simple | Identify(M), Occupancy Sensing(M) |
| 0x0302 | Temperature Sensor | Simple | Identify(M), Temperature Measurement(M) |
| 0x0305 | Pressure Sensor | Simple | Identify(M), Pressure Measurement(M) |
| 0x0306 | Flow Sensor | Simple | Identify(M), Flow Measurement(M) |
| 0x0307 | Humidity Sensor | Simple | Identify(M), Relative Humidity Measurement(M) |
| 0x0850 | On/Off Sensor | Simple | Identify(M), Boolean State(M) |
| 0x0076 | Smoke CO Alarm | Simple | Identify(M), Smoke CO Alarm(M), Groups(O), Temperature Measurement(O) |
| 0x002C | Air Quality Sensor | Simple | Identify(M), Air Quality(M) |
| 0x0041 | Water Freeze Detector | Simple | Identify(M), Boolean State(M) |
| 0x0043 | Water Leak Detector | Simple | Identify(M), Boolean State(M) |
| 0x0044 | Rain Sensor | Simple | Identify(M), Boolean State(M) |

## Closures

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x000A | Door Lock | Simple | Identify(M), Door Lock(M) |
| 0x000B | Door Lock Controller | Simple | Identify(M), Door Lock(M-Client) |
| 0x0202 | Window Covering | Simple | Identify(M), Window Covering(M), Groups(O) |
| 0x0203 | Window Covering Controller | Simple | Identify(M), Window Covering(M-Client) |

## HVAC

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0301 | Thermostat | Simple | Identify(M), Thermostat(M), Groups(O), Fan Control(O-Client), Temperature Measurement(O-Client) |
| 0x030A | Thermostat Controller | Simple | Identify(M), Thermostat(M-Client) |
| 0x002B | Fan | Simple | Identify(M), Fan Control(M), On/Off(M), Groups(O) |
| 0x002D | Air Purifier | Simple | Identify(M), Air Quality(M), On/Off(M), Fan Control(O) |
| 0x0309 | Heat Pump | Simple | Identify(M), Thermostat(M) |

## Appliances

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0070 | Refrigerator | Simple | Composed: Temperature Controlled Cabinet(min 1) |
| 0x0071 | Temperature Controlled Cabinet | Simple | Temperature Measurement(M), Refrigerator & TCC Mode(M) |
| 0x0073 | Laundry Washer | Simple | Operational State(M), On/Off(O), Laundry Mode(O), Washer Controls(O) |
| 0x007C | Laundry Dryer | Simple | Operational State(M), On/Off(O), Laundry Dryer Controls(O) |
| 0x0077 | Cook Surface | Simple | Identify(M), On/Off(M), Temperature Control(O) |
| 0x0078 | Cooktop | Simple | Identify(M), On/Off(M) |
| 0x007A | Extractor Hood | Simple | Identify(M), On/Off(M), Fan Control(M) |
| 0x007B | Oven | Simple | Identify(M), On/Off(M), Temperature Control(M), Operational State(M) |
| 0x007E | Microwave Oven | Simple | Identify(M), On/Off(M), Operational State(M) |

## Energy Management

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x050C | EVSE (EV Charger) | Simple | Identify(M), Energy EVSE(M), Power Topology(M) |
| 0x0507 | Water Heater | Simple | Identify(M), On/Off(M), Temperature Control(M) |
| 0x0508 | Solar Power | Simple | Identify(M), Electrical Energy Measurement(M) |
| 0x0514 | Electrical Meter | Simple | Identify(M), Electrical Power Measurement(M), Electrical Energy Measurement(M) |
| 0x0018 | Battery Storage | Simple | Energy Storage(M) |

## Media

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0028 | Basic Video Player | Simple | On/Off(M), Media Playback(M), Channel(O), Keypad Input(O) |
| 0x0023 | Casting Video Player | Simple | On/Off(M), Media Playback(M), Content Launcher(M) |
| 0x0022 | Speaker | Simple | On/Off(M), Audio Output(O) |
| 0x0024 | Content App | Simple | Application Launcher(M), Media Playback(M) |
| 0x0029 | Casting Video Client | Simple | Content Launcher(O-Client) |
| 0x002A | Video Remote Control | Simple | Keypad Input(M-Client), Media Playback(M-Client) |

## Robots

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0074 | Robotic Vacuum Cleaner | Simple | Identify(M), RVC Run Mode(M), RVC Operational State(M), RVC Clean Mode(O), Service Area(O) |

## Cameras & Video

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0142 | Camera | Simple | Camera AV Stream Mgmt(M, VDO+ADO+SNP required), WebRTC Provider(M-Server), WebRTC Requestor(M-Client), WebRTC Provider(O-Client), WebRTC Requestor(O-Server), Push AV Stream Transport(O), Camera AV Settings(O), Zone Management(O), Occupancy Sensing(O), Identify(O) |
| 0x0143 | Video Doorbell | Composed | Camera(M), Doorbell(M) |
| 0x0144 | Floodlight Camera | Composed | Camera(M), On/Off Light+(M, min 1) |
| 0x0145 | Snapshot Camera | Simple | Camera AV Stream Mgmt(M, SNP required, VDO+ADO disallowed), Camera AV Settings(O), Zone Management(O), Occupancy Sensing(O), Identify(O) |
| 0x0146 | Chime | Simple | Chime(M), Identify(O) |
| 0x0147 | Camera Controller | Simple | WebRTC Provider(M-Client), WebRTC Requestor(M-Server), Camera AV Stream Mgmt(O-Client), Camera AV Settings(O-Client), Zone Management(O-Client), Push AV Stream Transport(O-Client), TLS Certificate Mgmt(O-Client), TLS Client Mgmt(O-Client), Identify(O-Client), Power Source(O-Client), Occupancy Sensing(O-Client) |
| 0x0148 | Doorbell | Simple | Identify(M), Switch(M, MomentarySwitch required), Chime(M-Client) — superset of Generic Switch |

## Network Infrastructure

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x0090 | Network Infrastructure Manager | Simple | Identify(M), Network Commissioning(M) |
| 0x0091 | Thread Border Router | Simple | Identify(M), Thread Border Router Management(M) |

## Generic / Other

| ID | Name | Class | Key Clusters |
|----|------|-------|-------------|
| 0x000E | Aggregator | Simple | Identify(M), Descriptor(M) |
| 0x0027 | Mode Select | Simple | Identify(M), Mode Select(M) |

## Notes

- **Class**: Node = node-level utility; Utility = endpoint-level utility; Simple = application endpoint
- **M-Client**: The device implements the client side (initiates commands)
- **Superset**: Some device types extend others (e.g., Dimmable Light ⊃ On/Off Light)
- All application endpoints also require **Descriptor** cluster (M)
- Root Node (Endpoint 0) is required on every Matter device
