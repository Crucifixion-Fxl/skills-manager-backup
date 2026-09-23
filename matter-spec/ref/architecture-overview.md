# Matter Architecture Overview

## What is Matter?

Matter is an application-layer protocol for smart home devices, developed by the Connectivity Standards Alliance (CSA). It enables interoperability across manufacturers and ecosystems (Apple Home, Google Home, Amazon Alexa, Samsung SmartThings, etc.).

**Key properties:**
- Application-layer protocol (not a radio protocol)
- Runs over IPv6
- Transport: Wi-Fi, Thread, Ethernet (operational); BLE (commissioning only)
- Local-first: Works without cloud, enables local control
- Multi-admin: One device can be controlled by multiple ecosystems simultaneously
- Open standard with mandatory certification

## Protocol Stack

```
┌─────────────────────────────────────┐
│        Application Layer            │  ← Clusters, Device Types
├─────────────────────────────────────┤
│        Data Model                   │  ← Attributes, Commands, Events
├─────────────────────────────────────┤
│        Interaction Model            │  ← Read, Write, Subscribe, Invoke
├─────────────────────────────────────┤
│        Action Framing               │  ← TLV encoding
├─────────────────────────────────────┤
│        Security / Message Layer     │  ← Encryption (AES-CCM), sessions
├─────────────────────────────────────┤
│     Message Reliability Protocol    │  ← MRP (acknowledgments, retries)
├─────────────────────────────────────┤
│        UDP / TCP                    │  ← Transport
├─────────────────────────────────────┤
│        IPv6                         │  ← Network
├─────────────────────────────────────┤
│   Wi-Fi / Thread / Ethernet / BLE  │  ← Physical / Link layer
└─────────────────────────────────────┘
```

## Spec Structure (3 Books)

### Book 1: Core Specification
The protocol fundamentals:

| Chapter | Topic |
|---------|-------|
| Ch 1 | Introduction, Terminology |
| Ch 2 | Architecture |
| Ch 3 | Cryptographic Primitives |
| Ch 4 | Secure Channel (PASE, CASE, Group Sessions) |
| Ch 5 | Commissioning |
| Ch 6 | Device Attestation |
| Ch 7 | Data Model |
| Ch 8 | Interaction Model |
| Ch 9 | System Model (Discovery, Multi-Admin) |
| Ch 10 | Interaction Model Encoding (TLV) |
| Ch 11 | Transport Layer (MRP, TCP) |
| Ch 12 | Bluetooth Transport Protocol |
| Ch 13 | Multi-admin & Fabric |
| Ch 14 | Device Management (Diagnostics, OTA, etc.) |

### Book 2: Application Clusters
Defines all cluster specifications — the functional building blocks:
- Utility clusters (Descriptor, ACL, Basic Information, etc.)
- Application clusters organized by domain (Lighting, HVAC, Closures, Measurement, etc.)

### Book 3: Device Type Library
Defines device type requirements — which clusters are mandatory/optional for each device type:
- Organized by category (Lighting, Sensors, HVAC, etc.)
- Each device type specifies required server/client clusters

## Network Topology

### Fabric
A logical overlay network under a single Root CA. Multiple fabrics can share the same physical network. A device can belong to multiple fabrics.

### Node Types
- **Commissioner**: Performs commissioning (adds devices to fabric)
- **Controller**: Controls devices (sends commands)
- **End Device**: The device itself (light, sensor, lock, etc.)
- **Bridge**: Bridges non-Matter devices into Matter fabric
- **OTA Provider**: Serves firmware updates

### Network Transports

| Transport | Use | Typical Devices |
|-----------|-----|-----------------|
| **Wi-Fi** | High bandwidth, always-on | Cameras, hubs, video players |
| **Thread** | Low power mesh, sleepy devices | Sensors, locks, switches |
| **Ethernet** | Wired, high reliability | Hubs, bridges, fixed appliances |
| **BLE** | Commissioning only | Initial device setup |

### Thread Specifics
- Thread is an IPv6 mesh network (IEEE 802.15.4)
- **Thread Border Router (TBR)**: Bridges Thread mesh to Wi-Fi/Ethernet
- **Sleepy End Device (SED)**: Battery device, polls for messages
- **Intermittently Connected Device (ICD)**: Extended sleep with ICD Management cluster

## Security Model

### Session Types
- **PASE Session**: Passcode-based (commissioning only, uses SPAKE2+)
- **CASE Session**: Certificate-based (operational, mutual authentication)
- **Group Session**: Group key-based (groupcast, derived from epoch keys)

### Encryption
- AES-128-CCM for message encryption
- ECDSA P-256 for signatures
- HKDF-SHA-256 for key derivation

### Access Control
Four privilege levels: View < Operate < Manage < Administer
Controlled by ACL entries (per-fabric, installed during commissioning).

## Key Concepts for Developers

### Endpoint 0 (Root Node)
Every device has Endpoint 0 with system-level clusters:
- Basic Information, Descriptor, Access Control
- Commissioning clusters (General, Network, Administrator, Operational Credentials)
- Group Key Management, General Diagnostics

### Application Endpoints (1+)
Each represents a device function:
- Endpoint 1: On/Off Light
- Endpoint 2: Temperature Sensor
- etc.

### Cluster Server vs Client
- **Server**: Holds state, processes commands (e.g., Light endpoint has On/Off server)
- **Client**: Initiates interactions (e.g., Switch endpoint has On/Off client)
- Binding connects client to server (switch → light)

### TLV (Tag-Length-Value)
Matter uses TLV encoding for all data serialization:
- Compact binary format
- Self-describing tags
- Supports: integers, floats, strings, byte strings, booleans, null, arrays, structs
