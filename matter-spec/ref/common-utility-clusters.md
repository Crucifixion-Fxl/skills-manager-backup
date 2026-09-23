# Matter Common Utility Clusters

Must-implement or commonly-used clusters for Matter device development.

## Descriptor (0x001D)

**Purpose:** Describes endpoint's device types, cluster lists, and composition.

| Attribute | ID | Type | Q | Description |
|-----------|-----|------|---|-------------|
| DeviceTypeList | 0x0000 | list[DeviceTypeStruct] | F | Device types on this endpoint (min 1) |
| ServerList | 0x0001 | list[cluster-id] | F | Server cluster IDs on endpoint |
| ClientList | 0x0002 | list[cluster-id] | F | Client cluster IDs on endpoint |
| PartsList | 0x0003 | list[endpoint-no] | | Child endpoints for composed device types |
| TagList | 0x0004 | list[SemanticTagStruct] | F | Semantic tags for disambiguation (1-6) |

**DeviceTypeStruct:** { DeviceType: devtype-id, Revision: uint16 (min 1) }

Required on every endpoint. PartsList enables composed device types (e.g., Refrigerator with child Temperature Controlled Cabinet endpoints).

---

## Access Control (0x001F)

**Purpose:** Manages the node's Access Control List (ACL).

| Attribute | ID | Type | Q | Access | Description |
|-----------|-----|------|---|--------|-------------|
| ACL | 0x0000 | list[AccessControlEntryStruct] | N | RW A F | ACL entries |
| Extension | 0x0001 | list[AccessControlExtensionStruct] | N | RW A F | Extension entries (EXTS feature) |
| SubjectsPerAccessControlEntry | 0x0002 | uint16 | F | R V | Max subjects per ACE (min 4) |
| TargetsPerAccessControlEntry | 0x0003 | uint16 | F | R V | Max targets per ACE (min 3) |
| AccessControlEntriesPerFabric | 0x0004 | uint16 | F | R V | Max ACEs per fabric (min 4) |

**Privilege levels:** View(1), Operate(3), Manage(4), Administer(5)
**Auth modes:** PASE, CASE, Group

**AccessControlEntryStruct:** { Privilege, AuthMode, Subjects(nullable list), Targets(nullable list) }
**AccessControlTargetStruct:** { Cluster(nullable), Endpoint(nullable), DeviceType(nullable) }

Node-scoped, mandatory on Endpoint 0.

---

## Basic Information (0x0028)

**Purpose:** Node identity and metadata for commissioning and UI display.

| Attribute | ID | Type | Q | Description |
|-----------|-----|------|---|-------------|
| DataModelRevision | 0x0000 | uint16 | F | Data model revision |
| VendorName | 0x0001 | string | F | Vendor name (max 32) |
| VendorID | 0x0002 | vendor-id | F | Vendor ID |
| ProductName | 0x0003 | string | F | Product name (max 32) |
| ProductID | 0x0004 | uint16 | F | Product ID |
| NodeLabel | 0x0005 | string | N | User-assigned label (max 32, RW) |
| Location | 0x0006 | string | N | 2-char location code (ISO 3166-1, RW) |
| HardwareVersion | 0x0007 | uint16 | F | Hardware version |
| HardwareVersionString | 0x0008 | string | F | Hardware version string (1-64) |
| SoftwareVersion | 0x0009 | uint32 | F | Software version (monotonic increasing) |
| SoftwareVersionString | 0x000A | string | F | Software version string (1-64) |
| ManufacturingDate | 0x000B | string | F,O | Manufacturing date (8-16 chars) |
| PartNumber | 0x000C | string | F,O | Part number (max 32) |
| ProductURL | 0x000D | string | F,O | Product URL (max 256) |
| ProductLabel | 0x000E | string | F,O | Product label (max 64) |
| SerialNumber | 0x000F | string | F,O | Serial number (max 32) |
| UniqueID | 0x0012 | string | F,O | Unique ID (max 32) |
| ProductAppearance | 0x0014 | ProductAppearanceStruct | F,O | Physical appearance (finish + color) |
| CapabilityMinima | 0x0013 | CapabilityMinimaStruct | F | Min CASE sessions/fabric (>=3), min subscriptions/fabric (>=3) |

Node-scoped, mandatory on Endpoint 0. Read-only except NodeLabel and Location.

---

## General Commissioning (0x0030)

**Purpose:** Manages commissioning lifecycle and fail-safe timer.

**Key Commands:**
- `ArmFailSafe`: Arms/extends/disarms fail-safe timer (ExpiryLengthSeconds, Breadcrumb)
- `SetRegulatoryConfig`: Sets regulatory location and country code
- `CommissioningComplete`: Finalizes commissioning

**Key Attributes:**
- `BasicCommissioningInfo`: FailSafeExpiryLengthSeconds, MaxCumulativeFailsafeSeconds
- `SupportsConcurrentConnection`: Can device maintain commissioning + operational connections?
- `Breadcrumb`: Progress checkpoint for commissioner

Node-scoped, mandatory on Endpoint 0.

---

## Network Commissioning (0x0031)

**Purpose:** Configures network interfaces (Wi-Fi, Thread, Ethernet).

**Key Commands:**
- `ScanNetworks`: Discover available networks
- `AddOrUpdateWiFiNetwork`: Add/update Wi-Fi SSID + credentials
- `AddOrUpdateThreadNetwork`: Add/update Thread network dataset
- `RemoveNetwork`: Remove stored network
- `ConnectNetwork`: Join configured network
- `ReorderNetwork`: Change network priority

**Key Attributes:**
- `Networks`: List of configured networks with status
- `ScanMaxTimeSeconds`, `ConnectMaxTimeSeconds`: Timeouts
- `InterfaceEnabled`: Network interface enabled state
- `LastNetworkingStatus`, `LastNetworkID`: Last operation result

One instance per network interface. Node-scoped, mandatory on Endpoint 0.

---

## Administrator Commissioning (0x003C)

**Purpose:** Opens/closes commissioning windows for multi-fabric support.

**Key Commands:**
- `OpenCommissioningWindow`: Opens Enhanced Commissioning Method window (PAKE verifier)
- `OpenBasicCommissioningWindow`: Opens Basic window with device's default passcode (may be deprecated)
- `RevokeCommissioning`: Closes open window

**Key Attributes:**
- `WindowStatus`: Open/Closed status
- `AdminFabricIndex`: Fabric that opened the window
- `AdminVendorId`: Vendor of the admin that opened window

Node-scoped, mandatory on Endpoint 0. ACL-gated (Administer privilege).

---

## Operational Credentials (0x003E)

**Purpose:** Manages Node Operational Certificates and fabric membership.

**Key Commands:**
- `AttestationRequest`: Request device attestation information
- `CertificateChainRequest`: Request DAC/PAI certificates
- `CSRRequest`: Request Certificate Signing Request
- `AddNOC`: Install operational certificate (initial commissioning)
- `UpdateNOC`: Update existing certificate
- `AddTrustedRootCertificate`: Install root CA certificate
- `RemoveFabric`: Remove fabric from device

**Key Attributes:**
- `NOCs`: List of installed NOCs (fabric-scoped, sensitive)
- `Fabrics`: List of fabric descriptors (fabric-scoped)
- `SupportedFabrics`: Maximum number of fabrics supported
- `CommissionedFabrics`: Current number of fabrics
- `TrustedRootCertificates`: Installed root CA certificates
- `CurrentFabricIndex`: Index of accessing fabric

Node-scoped, mandatory on Endpoint 0.

---

## Group Key Management (0x003F)

**Purpose:** Manages group keys used for groupcast messaging.

**Key Commands:**
- `KeySetWrite`: Write a group key set
- `KeySetRead`/`KeySetReadResponse`: Read a group key set
- `KeySetRemove`: Remove a group key set
- `KeySetReadAllIndices`: List all key set IDs

**Key Attributes:**
- `GroupKeyMap`: Maps Group IDs to Group Key Set IDs (fabric-scoped)
- `GroupTable`: All groups the node knows about (fabric-scoped)
- `MaxGroupsPerFabric`, `MaxGroupKeysPerFabric`: Limits

Node-scoped, mandatory on Endpoint 0.

---

## General Diagnostics (0x0033)

**Purpose:** Standardized diagnostics for node health monitoring.

**Key Attributes:**
- `NetworkInterfaces`: List of network interfaces (name, type, IP addresses, MAC)
- `RebootCount`: Number of reboots since manufacture
- `UpTime`: Seconds since last reboot
- `TotalOperationalHours`: Total hours of operation
- `BootReason`: Reason for last reboot (PowerOn, BrownOut, SW/HW Watchdog, SWUpdate, SWReset)
- `ActiveHardwareFaults`, `ActiveRadioFaults`, `ActiveNetworkFaults`: Current faults

**Key Events:**
- `HardwareFaultChange`: Hardware fault state changed
- `RadioFaultChange`: Radio fault state changed
- `NetworkFaultChange`: Network fault state changed
- `BootReason`: Generated on each boot

Node-scoped, mandatory on Endpoint 0.

---

## Identify (0x0003)

**Purpose:** Triggers physical identification (e.g., flashing LED) for installer guidance.

**Key Attributes:**
- `IdentifyTime` (0x0000): Remaining seconds of identification (RW, set to start/stop)
- `IdentifyType` (0x0001): Method (None, LightOutput, VisibleIndicator, AudibleBeep, Display, Actuator)

**Key Commands:**
- `Identify`: Start identification for N seconds
- `TriggerEffect`: Trigger specific effect (Blink, Breathe, Okay, ChannelChange)

Endpoint-scoped. Required by most device types.

---

## Groups (0x0004)

**Purpose:** Manages endpoint membership in groups for groupcast messaging.

**Key Commands:**
- `AddGroup`: Add endpoint to a group (with optional name)
- `ViewGroup`: Get group name
- `GetGroupMembership`: List endpoint's groups
- `RemoveGroup`/`RemoveAllGroups`: Remove from groups
- `AddGroupIfIdentifying`: Add to group during identification

**Key Attributes:**
- `NameSupport`: Whether group names are supported (GN feature)

Endpoint-scoped. Required by device types that support group communication.

---

## Binding (0x001E)

**Purpose:** Defines persistent relationships between this endpoint and other endpoints.

**Key Attributes:**
- `Binding` (0x0000): List of TargetStruct entries (N, RW)

**TargetStruct (fabric-scoped):**
- `Node`: Remote node ID (for unicast)
- `Group`: Group ID (for groupcast, mutually exclusive with Endpoint)
- `Endpoint`: Remote endpoint number (mutually exclusive with Group)
- `Cluster`: Optional cluster filter

Endpoint-scoped. Used by client clusters to direct commands (e.g., switch -> light binding).

---

## Power Source (0x002F)

**Purpose:** Describes a physical power source's configuration and capabilities.

**Features:** WIRED (O.a+), BAT (O.a+), RECHG ([BAT]), REPLC ([BAT])
At least one of WIRED or BAT SHALL be supported.

**Key Attributes (common):**
- `Status`: Active, Standby, Unavailable
- `Order`: Priority order among multiple power sources
- `Description`: Human-readable power source description

**Wired-specific:** WiredAssessedCurrent, WiredNominalVoltage, WiredMaximumCurrent
**Battery-specific:** BatPercentRemaining, BatTimeRemaining, BatChargeLevel (OK/Warning/Critical), BatReplacementNeeded, BatCommonDesignation (AAA, AA, C, D, etc.)
**Rechargeable-specific:** BatTimeToFullCharge, BatFunctionalWhileCharging, BatChargingCurrent

Node-scoped. One instance per physical power source.

---

## OTA Software Update Provider (0x0029) & Requestor (0x002A)

**Provider Commands:** QueryImage -> QueryImageResponse, ApplyUpdateRequest -> ApplyUpdateResponse, NotifyUpdateApplied
**Requestor Attributes:** DefaultOTAProviders, UpdatePossible, UpdateState, UpdateStateProgress
**Requestor Events:** StateTransition, VersionApplied, DownloadError

See `commissioning-and-security.md` for OTA flow details.
