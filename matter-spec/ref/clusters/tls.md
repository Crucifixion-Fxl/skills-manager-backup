# TLS Infrastructure Clusters

## TLS Certificate Management (0x0801)
Rev 1 | PICS: TLSCERT

Manages TLS root CA certificates and client certificates on a node. Used by Camera Controller for push transport authentication.

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MaxRootCertificates | uint8 | 5 to 254 | F | R V | M |
| 0x0001 | ProvisionedRootCertificates | list[TLSCertStruct] | max MaxRootCertificates | N | R V | M |
| 0x0002 | MaxClientCertificates | uint8 | 2 to 254 | F | R V | M |
| 0x0003 | ProvisionedClientCertificates | list[TLSClientCertificateDetailStruct] | max MaxClientCertificates | N | R V | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | ProvisionRootCertificate | C→S | ProvisionRootCertificateResponse | A F | M |
| 0x01 | ProvisionRootCertificateResponse | S→C | N | | M |
| 0x02 | FindRootCertificate | C→S | FindRootCertificateResponse | O F | M |
| 0x03 | FindRootCertificateResponse | S→C | N | | M |
| 0x04 | LookupRootCertificate | C→S | LookupRootCertificateResponse | O F | M |
| 0x05 | LookupRootCertificateResponse | S→C | N | | M |
| 0x06 | RemoveRootCertificate | C→S | Y | A F | M |
| 0x07 | ClientCSR | C→S | ClientCSRResponse | A F | M |
| 0x08 | ClientCSRResponse | S→C | N | | M |
| 0x09 | ProvisionClientCertificate | C→S | Y | A F | M |
| 0x0A | FindClientCertificate | C→S | FindClientCertificateResponse | O F | M |
| 0x0B | FindClientCertificateResponse | S→C | N | | M |
| 0x0C | LookupClientCertificate | C→S | LookupClientCertificateResponse | O F | M |
| 0x0D | LookupClientCertificateResponse | S→C | N | | M |
| 0x0E | RemoveClientCertificate | C→S | Y | A F | M |

**Key Data Types:**
- **TLSCAID:** uint16 (0-65534) — CA certificate identifier
- **TLSCCDID:** uint16 (0-65534) — Client certificate detail identifier
- **TLSCertStruct (Fabric-Scoped):** CAID(TLSCAID), Certificate(octstr max 3000, O)
- **TLSClientCertificateDetailStruct (Fabric-Scoped):** CCDID(TLSCCDID), ClientCertificate(octstr max 3000, nullable, O), IntermediateCertificates(list[octstr] max 10, O)

---

## TLS Client Management (0x0802)
Rev 1 | PICS: TLSCLIENT

Manages TLS endpoint connections (hostname + port + certificates). Required by Push AV Stream Transport for secure upload.

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MaxProvisioned | uint8 | 5 to 254 | F | R V | M |
| 0x0001 | ProvisionedEndpoints | list[TLSEndpointStruct] | desc | N | R V | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | ProvisionEndpoint | C→S | ProvisionEndpointResponse | A F | M |
| 0x01 | ProvisionEndpointResponse | S→C | N | | M |
| 0x02 | FindEndpoint | C→S | FindEndpointResponse | O F | M |
| 0x03 | FindEndpointResponse | S→C | N | | M |
| 0x04 | RemoveEndpoint | C→S | N | A F | M |

**Key Data Types:**
- **TLSEndpointID:** uint16 (0-65534) — endpoint identifier
- **TLSEndpointStruct (Fabric-Scoped):** EndpointID(TLSEndpointID), Hostname(octstr 4-253), Port(uint16 1-65535), CAID(TLSCAID), CCDID(TLSCCDID nullable), ReferenceCount(uint8)

**Status Codes:**
EndpointAlreadyInstalled(0x02), RootCertificateNotFound(0x03), ClientCertificateNotFound(0x04), EndpointInUse(0x05), InvalidTime(0x06)
