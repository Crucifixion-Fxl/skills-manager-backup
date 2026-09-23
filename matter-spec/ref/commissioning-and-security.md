# Matter Commissioning & Security

## Overview

Commissioning is the process of adding a new device to a Matter fabric. It establishes identity, network connectivity, and operational credentials.

## Onboarding Payload

The device provides a payload for the commissioner to discover and pair with it.

### QR Code Fields
- **Version** (3 bits): Format version (initial: 0b000)
- **Vendor ID** (16 bits): Manufacturer identifier
- **Product ID** (16 bits): Product identifier
- **Custom Flow** (2 bits): 0=Standard, 1=User-Intent, 2=Custom (manufacturer-specific)
- **Discovery Capabilities** (8 bits): Bitmask — BLE, DNS-SD, SoftAP, NFC, etc.
- **Discriminator** (12 bits): Unique per device for distinguishing multiple devices
- **Passcode** (27 bits): 8-digit numeric (00000001–99999998), shared secret for PASE
- **TLV Data** (variable): Optional additional information

### Manual Pairing Code
- Human-readable numeric string
- Contains short discriminator (upper 4 bits of full 12-bit discriminator)
- Contains passcode
- Vendor/Product ID optional

## Discovery Methods

| Method | Description | Use Case |
|--------|-------------|----------|
| **BLE** | Bluetooth Low Energy advertising | Thread devices, battery devices |
| **DNS-SD** | IP-based service discovery (mDNS) | Wi-Fi/Ethernet devices |
| **Wi-Fi PAF** | Wi-Fi Public Action Frames | Wi-Fi devices without IP |
| **NFC** | Near Field Communication tap | Physical proximity pairing |

### DNS-SD Discovery Types
- **Commissionable Node Discovery**: Devices in commissioning mode
- **Extended Discovery**: Already-commissioned devices (re-commissioning)
- **Commissioner Discovery**: Devices finding commissioners

## Full Commissioning Flow

```
┌─────────────────────────────────────────────────────┐
│ 1. Discovery: Commissioner finds device (BLE/DNS-SD)│
│ 2. PASE: Establish encrypted session using passcode  │
│ 3. Fail-Safe: Arm 60s timer (prevents stuck devices) │
│ 4. Configure: Time, timezone, regulatory info        │
│ 5. Attestation: Verify device is genuine Matter cert │
│ 6. CSR: Device generates operational key pair         │
│ 7. NOC: Commissioner installs operational certificate │
│ 8. Network: Configure Wi-Fi/Thread credentials        │
│ 9. Connect: Device joins operational network          │
│10. CASE: Open authenticated session on op network     │
│11. Complete: CommissioningComplete command             │
└─────────────────────────────────────────────────────┘
```

### Detailed Steps

**Step 1–2: Discovery → PASE**
- Commissioner scans for device using discriminator from onboarding payload
- **PASE** (Passcode-Authenticated Session Establishment): Uses PBKDF + SPAKE2+ to derive shared secrets from the passcode
- Upon PASE success, device arms 60-second fail-safe timer automatically
- All subsequent messages encrypted with PASE-derived keys
- Commissioner gets implicit Administer privilege

**Step 3–4: Fail-Safe → Configuration**
- Commissioner re-arms fail-safe to desired timeout
- Configures UTC time, timezone, DST offsets
- Sets regulatory information via `SetRegulatoryConfig` command
- Optional: Terms & Conditions acceptance (Enhanced Setup Flow)

**Step 5: Device Attestation**
- Commissioner requests and validates DAC chain (DAC → PAI → PAA)
- Verifies device holds DAC private key (signature challenge)
- Validates Certification Declaration (CD) — proves CSA certification
- Commissioner may warn on uncertified devices (policy-dependent)

**Step 6–7: CSR → NOC Installation**
- Commissioner requests CSR — device generates new operational key pair
- Commissioner creates/obtains Node Operational Certificate (NOC) for the device
- Installs via `AddTrustedRootCertificate` + `AddNOC` commands
- Device receives: Root CA cert, NOC, Fabric ID, Node ID, admin vendor ID
- `UpdateFabricLabel` sets human-readable fabric name

**Step 8–9: Network → Connect**
- Commissioner scans available networks (`ScanNetworks`)
- Configures credentials: `AddOrUpdateWiFiNetwork` or `AddOrUpdateThreadNetwork`
- Triggers `ConnectNetwork` — device joins operational network

**Step 10–11: CASE → Complete**
- Administrator discovers device on operational network (DNS-SD)
- Opens **CASE** (Certificate-Authenticated Session Establishment) — mutual auth using NOC
- Sends `CommissioningComplete` over CASE session
- PASE session terminates; device is fully operational

### Concurrent vs Non-Concurrent
- **Concurrent**: Device maintains both commissioning channel and operational network simultaneously
- **Non-Concurrent**: Device can only use one connection at a time; commissioning channel closes before operational network connects

### Error Handling
- PASE must complete within 60 seconds (PbkdfParamResponse → PAKE3)
- 20 failed commissioning attempts → device exits commissioning mode
- Fail-safe expiration → device reverts all uncommitted changes
- Commissioner can abort by setting ExpiryLengthSeconds = 0

## Device Attestation Chain

```
PAA (Product Attestation Authority)     ← Root CA, maintained by CSA
  └── PAI (Product Attestation Intermediate)  ← Per-vendor/per-product
        └── DAC (Device Attestation Certificate)  ← Unique per device
```

### Certificate Details
- **PAA**: X.509v3, root of trust, in commissioner's trusted set
- **PAI**: X.509v3, signed by PAA, used by manufacturers
- **DAC**: X.509v3, signed by PAI, unique per device
  - Contains VID and PID in subject field (Matter-specific OIDs)
  - Max 600 bytes DER-encoded
  - Associated with immutable private key on device
- **Algorithm**: ECDSA with SHA-256 on secp256r1 (P-256)

### Certification Declaration (CD)
- CMS-encoded signed data (RFC 5652)
- Contains: format version, VID, PID array (1–100 products), device type ID, certificate ID, security level
- Signed by CSA for certified devices
- Proves protocol compliance and certification status
- Embedded in device firmware by manufacturer

## Fabric & Multi-Fabric

### Fabric Concept
A fabric is a logical network under a single Root CA:
- Unique **Fabric ID** (64 bits, scoped to Root CA)
- Each node gets a unique **Node ID** (64 bits, scoped to fabric)
- Contains IPv6 subnets across Wi-Fi, Ethernet, and/or Thread

### Multi-Fabric Support
A device can be commissioned into **multiple fabrics** simultaneously:
- Each fabric has independent Root CA, credentials, ACLs
- Fabric-scoped data is isolated between fabrics
- Each fabric sees only its own entries in fabric-scoped lists

### Adding to Additional Fabric
1. Existing administrator opens commissioning window (`OpenCommissioningWindow`)
2. New commissioner performs full commissioning flow
3. Device receives new NOC for new fabric
4. Retains existing fabric credentials

### CASE Authenticated Tags (CATs)
- Up to three 32-bit optional attributes in NOC
- Enable role-based access control without enumerating individual nodes
- Used for group-level admin permissions

## OTA Software Update

### Architecture
- **OTA Provider** (Device Type 0x0014): Learns about available updates, serves images
- **OTA Requestor** (Device Type 0x0012): Periodically queries for updates

### Update Flow
1. Requestor queries Provider (`QueryImage`)
2. Provider responds with image URI if update available
3. Requestor downloads image (BDX Synchronous/Async, HTTPS, or vendor-specific)
4. Requestor requests permission to apply (`ApplyUpdateRequest`)
5. Provider grants permission → Requestor applies update
6. Requestor notifies Provider of success (`NotifyUpdateApplied`)

### Key Rules
- Only upgrade to numerically newer versions
- Functional rollback = vendor creates higher version with prior functionality
- Discovery: Provisioned provider records + dynamic discovery on operational network
