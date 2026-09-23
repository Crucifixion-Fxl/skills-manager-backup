# Camera & Video Clusters

## Zone Management (0x0550)
Rev 1 | PICS: ZMGMT

Manages motion detection zones on a camera's sensor area. Zones define regions for triggering events/recordings.

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | TWODCZ | TwoDimensionalCartesianZone | O.a+ |
| 1 | PERZONESENS | PerZoneSensitivity | O |
| 2 | UD | UserDefined | O |
| 3 | FOCUS | FocusZones | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MaxUserDefinedZones | uint8 | all | F | R V | UD |
| 0x0001 | MaxZones | uint8 | all | F | R V | M |
| 0x0002 | Zones | list[ZoneInformationStruct] | max MaxZones | N | R V | M |
| 0x0003 | Triggers | list[ZoneTriggerControlStruct] | desc | N | R V | M |
| 0x0004 | SensitivityMax | uint8 | all | F | R V | O |
| 0x0005 | Sensitivity | uint8 | 1 to SensitivityMax | N | RW VO | O |
| 0x0006 | TwoDCartesianMax | uint8 | all | F | R V | TWODCZ |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | CreateTwoDCartesianZone | C→S | CreateTwoDCartesianZoneResponse | M | TWODCZ & UD |
| 0x01 | CreateTwoDCartesianZoneResponse | S→C | N | | TWODCZ & UD |
| 0x02 | UpdateTwoDCartesianZone | C→S | Y | M | TWODCZ & UD |
| 0x03 | RemoveZone | C→S | Y | M | UD |
| 0x04 | CreateOrUpdateTrigger | C→S | Y | M | M |
| 0x05 | RemoveTrigger | C→S | Y | M | M |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | ZoneTriggered | INFO | M |
| 0x01 | ZoneStopped | INFO | M |

**Key Enums:**
- **ZoneTypeEnum:** TwoDCZ(0)
- **ZoneUseEnum:** Motion(0), Privacy(1), Focus(2)
- **ZoneSourceEnum:** System(0), UserDefined(1)
- **ZoneEventTriggeredReasonEnum:** Motion(0)
- **ZoneEventStoppedReasonEnum:** ActionStopped(0), Timeout(1)

**Key Data Types:**
- **TwoDCartesianVertexStruct:** X(uint16), Y(uint16)
- **TwoDCartesianZoneStruct:** Name(string max 16), Use(ZoneUseEnum), Vertices(list[TwoDCartesianVertexStruct] 3-max)
- **ZoneInformationStruct:** ZoneID(uint16), ZoneType(ZoneTypeEnum), ZoneSource(ZoneSourceEnum), TwoDCartesianZone(TwoDCartesianZoneStruct)
- **ZoneTriggerControlStruct:** ZoneID(uint16), Sensitivity(uint8), Enable(bool)

---

## Camera AV Stream Management (0x0551)
Rev 1 | PICS: AVSM

Core cluster for managing audio, video, and snapshot streams on a camera. Handles stream allocation, configuration, privacy, and capture.

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | ADO | Audio | O.a+ |
| 1 | VDO | Video | O.a+ |
| 2 | SNP | Snapshot | O.a+ |
| 3 | PRIV | Privacy | O |
| 4 | SPKR | Speaker | [ADO] |
| 5 | ICTL | ImageControl | [VDO \| SNP] |
| 6 | WMARK | Watermark | [VDO \| SNP] |
| 7 | OSD | OnScreenDisplay | [VDO \| SNP] |
| 8 | STOR | LocalStorage | O |
| 9 | HDR | HighDynamicRange | [VDO \| SNP] |
| 10 | NV | NightVision | [VDO \| SNP] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MaxConcurrentEncoders | uint8 | all | F | R V | VDO \| SNP |
| 0x0001 | MaxEncodedPixelRate | uint32 | all | F | R V | VDO \| SNP |
| 0x0002 | VideoSensorParams | VideoSensorParamsStruct | all | F | R V | VDO |
| 0x0003 | NightVisionUsesInfrared | bool | all | F | R V | NV |
| 0x0004 | MinViewportResolution | VideoResolutionStruct | all | F | R V | VDO |
| 0x0005 | RateDistortionTradeOffPoints | list[RateDistortionTradeOffPointsStruct] | all | F | R V | VDO |
| 0x0006 | MaxContentBufferSize | uint32 | all | F | R V | M |
| 0x0007 | MicrophoneCapabilities | AudioCapabilitiesStruct | all | F | R V | ADO |
| 0x0008 | SpeakerCapabilities | AudioCapabilitiesStruct | all | F | R V | SPKR |
| 0x0009 | TwoWayTalkSupport | TwoWayTalkSupportTypeEnum | all | F | R V | SPKR |
| 0x000A | SnapshotCapabilities | list[SnapshotCapabilitiesStruct] | all | F | R V | SNP |
| 0x000B | MaxNetworkBandwidth | uint32 | all | F | R V | M |
| 0x000C | CurrentFrameRate | uint16 | all | | R V | VDO |
| 0x000D | HDRModeEnabled | bool | all | N | RW M | HDR |
| 0x000E | SupportedStreamUsages | list[StreamUsageEnum] | all | F | R V | M |
| 0x000F | AllocatedVideoStreams | list[VideoStreamStruct] | all | N | R V | VDO |
| 0x0010 | AllocatedAudioStreams | list[AudioStreamStruct] | all | N | R V | ADO |
| 0x0011 | AllocatedSnapshotStreams | list[SnapshotStreamStruct] | all | N | R V | SNP |
| 0x0012 | StreamUsagePriorities | list[StreamUsageEnum] | all | N | R V | M |
| 0x0013 | SoftRecordingPrivacyModeEnabled | bool | all | N | RW VO | PRIV |
| 0x0014 | SoftLivestreamPrivacyModeEnabled | bool | all | N | RW VO | PRIV |
| 0x0015 | HardPrivacyModeOn | bool | all | | R V | O |
| 0x0016 | NightVision | TriStateAutoEnum | all | N | RW M | NV |
| 0x0017 | NightVisionIllum | TriStateAutoEnum | all | N | RW M | [NV] |
| 0x0018 | Viewport | ViewportStruct | all | N | RW M | VDO |
| 0x0019 | SpeakerMuted | bool | all | N | RW M | SPKR |
| 0x001A | SpeakerVolumeLevel | uint8 | SpeakerMinLevel to SpeakerMaxLevel | N | RW M | SPKR |
| 0x001B | SpeakerMaxLevel | uint8 | SpeakerMinLevel to 254 | | R M | SPKR |
| 0x001C | SpeakerMinLevel | uint8 | max SpeakerMaxLevel | | R M | SPKR |
| 0x001D | MicrophoneMuted | bool | all | N | RW M | ADO |
| 0x001E | MicrophoneVolumeLevel | uint8 | MicrophoneMinLevel to MicrophoneMaxLevel | N | RW M | ADO |
| 0x001F | MicrophoneMaxLevel | uint8 | MicrophoneMinLevel to 254 | | R M | ADO |
| 0x0020 | MicrophoneMinLevel | uint8 | max MicrophoneMaxLevel | | R M | ADO |
| 0x0021 | MicrophoneAGCEnabled | bool | all | N | RW M | [ADO] |
| 0x0022 | ImageRotation | uint16 | max 359 | N | RW M | [ICTL].b+ |
| 0x0023 | ImageFlipHorizontal | bool | all | N | RW M | [ICTL].b+ |
| 0x0024 | ImageFlipVertical | bool | all | N | RW M | [ICTL].b+ |
| 0x0025 | LocalVideoRecordingEnabled | bool | all | N | RW M | VDO & STOR |
| 0x0026 | LocalSnapshotRecordingEnabled | bool | all | N | RW M | SNP & STOR |
| 0x0027 | StatusLightEnabled | bool | all | N | RW M | O |
| 0x0028 | StatusLightBrightness | ThreeLevelAutoEnum | all | N | RW M | O |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | AudioStreamAllocate | C→S | AudioStreamAllocateResponse | M | ADO |
| 0x01 | AudioStreamAllocateResponse | S→C | N | | ADO |
| 0x02 | AudioStreamDeallocate | C→S | Y | M | ADO |
| 0x03 | VideoStreamAllocate | C→S | VideoStreamAllocateResponse | M | VDO |
| 0x04 | VideoStreamAllocateResponse | S→C | N | | VDO |
| 0x05 | VideoStreamModify | C→S | Y | M | VDO & (WMARK \| OSD) |
| 0x06 | VideoStreamDeallocate | C→S | Y | M | VDO |
| 0x07 | SnapshotStreamAllocate | C→S | SnapshotStreamAllocateResponse | M | SNP |
| 0x08 | SnapshotStreamAllocateResponse | S→C | N | | SNP |
| 0x09 | SnapshotStreamModify | C→S | Y | M | SNP & (WMARK \| OSD) |
| 0x0A | SnapshotStreamDeallocate | C→S | Y | M | SNP |
| 0x0B | SetStreamPriorities | C→S | Y | A | M |
| 0x0C | CaptureSnapshot | C→S | CaptureSnapshotResponse | O | SNP |
| 0x0D | CaptureSnapshotResponse | S→C | N | | SNP |

**Key Enums (shared across camera clusters, defined in cameras.adoc):**
- **StreamUsageEnum:** Internal(0), Recording(1), Analysis(2), LiveView(3)
- **VideoCodecEnum:** H264(0), HEVC(1), VVC(2), AV1(3)
- **AudioCodecEnum:** OPUS(0), AAC-LC(1)
- **ImageCodecEnum:** JPEG(0)
- **TwoWayTalkSupportTypeEnum:** NotSupported(0), HalfDuplex(1), FullDuplex(2)
- **TriStateAutoEnum:** Off(0), On(1), Auto(2)

**Key Structs:**
- **ViewportStruct:** X1, Y1, X2, Y2 (uint16) — bounding rectangle on sensor
- **VideoSensorParamsStruct:** SensorWidth, SensorHeight, MaxFPS, MaxHDRFPS
- **VideoResolutionStruct:** Width, Height (uint16)
- **VideoStreamStruct:** VideoStreamID, StreamUsage, VideoCodec, MinFrameRate, MaxFrameRate, MinResolution, MaxResolution, MinBitRate, MaxBitRate, KeyFrameInterval, WatermarkEnabled, OSDEnabled, ReferenceCount
- **AudioStreamStruct:** AudioStreamID, StreamUsage, AudioCodec, ChannelCount, SampleRate, BitRate, BitDepth, ReferenceCount
- **SnapshotStreamStruct:** SnapshotStreamID, ImageCodec, FrameRate, MinResolution, MaxResolution, Quality, ReferenceCount, EncodedPixels, HardwareEncoder, WatermarkEnabled, OSDEnabled

---

## Camera AV Settings User-Level Management (0x0552)
Rev 1 | PICS: AVSETTINGS

Provides user-level camera controls: digital and mechanical pan/tilt/zoom, presets.

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | DPTZ | DigitalPTZ | O.a+ |
| 1 | MPAN | MechanicalPan | O.a+ |
| 2 | MTILT | MechanicalTilt | O.a+ |
| 3 | MZOOM | MechanicalZoom | O.a+ |
| 4 | MPRE | MechanicalPresets | [MPAN \| MTILT \| MZOOM] |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | MPTZPosition | MPTZStruct | all | N | R V | MPAN \| MTILT \| MZOOM |
| 0x0001 | MaxPresets | uint8 | all | F | R V | MPRE |
| 0x0002 | MPTZPresets | list[MPTZPresetStruct] | max MaxPresets | N | R V | MPRE |
| 0x0003 | DPTZStreams | list[DPTZStruct] | all | N | R V | DPTZ |
| 0x0004 | ZoomMax | uint8 | min 2 | F | R V | MZOOM |
| 0x0005 | TiltMin | int16 | all | F | R V | MTILT |
| 0x0006 | TiltMax | int16 | min TiltMin | F | R V | MTILT |
| 0x0007 | PanMin | int16 | all | F | R V | MPAN |
| 0x0008 | PanMax | int16 | min PanMin | F | R V | MPAN |
| 0x0009 | MovementState | PhysicalMovementEnum | all | | R V | MPAN \| MTILT \| MZOOM |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | MPTZSetPosition | C→S | Y | O | MPAN \| MTILT \| MZOOM |
| 0x01 | MPTZRelativeMove | C→S | Y | O | MPAN \| MTILT \| MZOOM |
| 0x02 | MPTZMoveToPreset | C→S | Y | O | MPRE |
| 0x03 | MPTZSavePreset | C→S | Y | M | MPRE |
| 0x04 | MPTZRemovePreset | C→S | Y | M | MPRE |
| 0x05 | DPTZSetViewport | C→S | Y | O | DPTZ |
| 0x06 | DPTZRelativeMove | C→S | Y | O | DPTZ |

**Key Enums:**
- **PhysicalMovementEnum:** Idle(0), PanningRight(1), PanningLeft(2), TiltingUp(3), TiltingDown(4), ZoomingIn(5), ZoomingOut(6), PanAndTilt(7), PanAndZoom(8), TiltAndZoom(9), PanTiltAndZoom(10)

**Key Structs:**
- **DPTZStruct:** VideoStreamID(uint16), Viewport(ViewportStruct)
- **MPTZStruct:** Pan(int16), Tilt(int16), Zoom(uint8)
- **MPTZPresetStruct:** PresetID(uint8), Name(string max 16), Position(MPTZStruct)

---

## WebRTC Transport Provider (0x0553)
Rev 1 | PICS: WEBRTCPROV

Camera-side WebRTC signaling cluster. Handles SDP offer/answer exchange and ICE candidate negotiation for live streaming.

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | METADATA | Metadata | P, O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | CurrentSessions | list[WebRTCSessionStruct] | desc | N | R V S | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x01 | SolicitOffer | C→S | SolicitOfferResponse | O F | M |
| 0x02 | SolicitOfferResponse | S→C | N | | M |
| 0x03 | ProvideOffer | C→S | ProvideOfferResponse | O F | M |
| 0x04 | ProvideOfferResponse | S→C | N | | M |
| 0x05 | ProvideAnswer | C→S | Y | O F | M |
| 0x06 | ProvideICECandidates | C→S | Y | O F | M |
| 0x07 | EndSession | C→S | Y | O F | M |

**Key Structs (shared, defined in webrtc.adoc):**
- **WebRTCSessionStruct (Fabric-Scoped):** ID(uint16), PeerNodeID(node-id), PeerEndpointID(endpoint-no), StreamUsage(StreamUsageEnum), VideoStreamID(uint16 nullable), AudioStreamID(uint16 nullable), MetadataEnabled(bool)
- **ICECandidateStruct:** Candidate(string), SDPMid(string nullable), SDPMLineIndex(uint16 nullable)

**SolicitOffer fields:** StreamUsage, VideoStreamID(nullable), AudioStreamID(nullable), ICEServers(list), ICETransportPolicy(string)
**ProvideOffer fields:** WebRTCSessionID, SDP(string), StreamUsage, VideoStreamID(nullable), AudioStreamID(nullable), ICEServers(list), ICETransportPolicy(string)
**ProvideAnswer fields:** WebRTCSessionID, SDP(string)
**ProvideICECandidates fields:** WebRTCSessionID, ICECandidates(list[ICECandidateStruct])
**EndSession fields:** WebRTCSessionID, Reason(WebRTCEndReasonEnum)

**Key Enums:**
- **WebRTCEndReasonEnum:** IceFailed(0), IceTimeout(1), UserHangup(2), PeerHangup(3), ResourceExhausted(4), InternalError(5), Timeout(6), NoStream(7), PrivacyMode(8), InvalidStreamUsage(9), ConnectionFailed(10), InvalidOffer(11), InvalidAnswer(12), ProviderRemoved(13)

---

## WebRTC Transport Requestor (0x0554)
Rev 1 | PICS: WEBRTCREQ

Controller-side WebRTC signaling cluster. Receives offers and answers from the camera, forwards ICE candidates.

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | CurrentSessions | list[WebRTCSessionStruct] | desc | N | R V S | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | Offer | C→S | Y | O F | M |
| 0x01 | Answer | C→S | Y | O F | M |
| 0x02 | ICECandidates | C→S | Y | O F | M |
| 0x03 | End | C→S | Y | O F | M |

**Offer fields:** WebRTCSessionID, SDP(string), ICEServers(list), ICETransportPolicy(string)
**Answer fields:** WebRTCSessionID, SDP(string)
**ICECandidates fields:** WebRTCSessionID, ICECandidates(list[ICECandidateStruct])
**End fields:** WebRTCSessionID, Reason(WebRTCEndReasonEnum)

---

## Push AV Stream Transport (0x0555)
Rev 1 | PICS: PAVST

Manages push-based upload of AV streams to remote servers using TLS and CMAF ingestion. Supports continuous, motion-triggered, and command-triggered recording.

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | PERZONESENS | PerZoneSensitivity | O |
| 1 | METADATA | Metadata | P, O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | SupportedFormats | list[SupportedFormatStruct] | min 1 | F | R V | M |
| 0x0001 | CurrentConnections | list[TransportConfigurationStruct] | desc | N | R V S | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | AllocatePushTransport | C→S | AllocatePushTransportResponse | M F | M |
| 0x01 | AllocatePushTransportResponse | S→C | N | | M |
| 0x02 | DeallocatePushTransport | C→S | Y | M F | M |
| 0x03 | ModifyPushTransport | C→S | Y | M F | M |
| 0x04 | SetTransportStatus | C→S | Y | M F | M |
| 0x05 | ManuallyTriggerTransport | C→S | Y | O F | M |
| 0x06 | FindTransport | C→S | FindTransportResponse | O F | M |
| 0x07 | FindTransportResponse | S→C | N | | M |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | PushTransportBegin | INFO | M |
| 0x01 | PushTransportEnd | INFO | M |

**Key Enums:**
- **TransportTriggerTypeEnum:** Command(0), Motion(1), Continuous(2)
- **TransportStatusEnum:** Active(0), Inactive(1)
- **ContainerFormatEnum:** CMAF(0)
- **IngestMethodsEnum:** CMAFIngest(0)
- **TriggerActivationReasonEnum:** UserInitiated(0), Automation(1), Emergency(2)
- **CMAFInterfaceEnum:** Interface1(0), Interface2DASH(1/P), Interface2HLS(2/P)

**Key Structs:**
- **TransportOptionsStruct:** StreamUsage, VideoStreamID(nullable), AudioStreamID(nullable), TLSEndpointID, URL(string 13-2000), TriggerOptions, IngestMethod, ContainerOptions, ExpiryTime(epoch-s optional)
- **TransportConfigurationStruct (Fabric-Scoped):** ConnectionID(uint16), TransportStatus, TransportOptions
- **TransportTriggerOptionsStruct:** TriggerType, MotionZones(list nullable), MotionSensitivity(uint8 nullable), MotionTimeControl, MaxPreRollLen(uint16)
- **TransportMotionTriggerTimeControlStruct:** InitialDuration(uint16), AugmentationDuration(uint16), MaxDuration(elapsed-s), BlindDuration(uint16)
- **CMAFContainerOptionsStruct:** CMAFInterface, SegmentDuration(uint16 500-65500), ChunkDuration, SessionGroup(uint8), TrackName(string 1-16), CENCKey(octstr 16 P,O), CENCKeyID(octstr 16), MetadataEnabled(bool)

**Status Codes:**
InvalidTLSEndpoint(0x02), InvalidStream(0x03), InvalidURL(0x04), InvalidZone(0x05), InvalidCombination(0x06), InvalidTriggerType(0x07), InvalidTransportStatus(0x08), InvalidOptions(0x09), InvalidStreamUsage(0x0A), InvalidTime(0x0B)

---

## Chime (0x0556)
Rev 1 | PICS: CHIME

Manages a set of pre-installed chime sounds and allows selecting/playing them. Usually paired with a Doorbell.

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | InstalledChimeSounds | list[ChimeSoundStruct] | min 1 | F | R V | M |
| 0x0001 | SelectedChime | uint8 | all | N | RW VO | M |
| 0x0002 | Enabled | bool | all | N | RW VO | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | PlayChimeSound | C→S | Y | O | M |

**Key Structs:**
- **ChimeSoundStruct:** ChimeID(uint8), Name(string max 48)
