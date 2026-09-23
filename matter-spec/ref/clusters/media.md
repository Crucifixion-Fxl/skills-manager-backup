# Media Clusters

## Media Playback (0x0506)
Rev 2 | PICS: MEDIAPLAYBACK

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | AS | AdvancedSeek | O |
| 1 | VS | VariableSpeed | O |
| 2 | TT | TextTracks | O |
| 3 | AT | AudioTracks | O |
| 4 | AA | AudioAdvance | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | CurrentState | PlaybackStateEnum | all | | R V | M |
| 0x0001 | StartTime | epoch-us | all | X | R V | AS |
| 0x0002 | Duration | uint64 | all | X | R V | AS |
| 0x0003 | SampledPosition | PlaybackPositionStruct | all | X | R V | AS |
| 0x0004 | PlaybackSpeed | single | all | | R V | AS |
| 0x0005 | SeekRangeEnd | uint64 | all | X | R V | AS |
| 0x0006 | SeekRangeStart | uint64 | all | X | R V | AS |
| 0x0007 | ActiveAudioTrack | TrackStruct | all | X | R V | AT |
| 0x0008 | AvailableAudioTracks | list[TrackStruct] | all | X | R V | AT |
| 0x0009 | ActiveTextTrack | TrackStruct | all | X | R V | TT |
| 0x000A | AvailableTextTracks | list[TrackStruct] | all | X | R V | TT |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | Play | C→S | PlaybackResponse | O | M |
| 0x01 | Pause | C→S | PlaybackResponse | O | M |
| 0x02 | Stop | C→S | PlaybackResponse | O | M |
| 0x03 | StartOver | C→S | PlaybackResponse | O | O |
| 0x04 | Previous | C→S | PlaybackResponse | O | O |
| 0x05 | Next | C→S | PlaybackResponse | O | O |
| 0x06 | Rewind | C→S | PlaybackResponse | O | VS |
| 0x07 | FastForward | C→S | PlaybackResponse | O | VS |
| 0x08 | SkipForward | C→S | PlaybackResponse | O | O |
| 0x09 | SkipBackward | C→S | PlaybackResponse | O | O |
| 0x0B | Seek | C→S | PlaybackResponse | O | AS |
| 0x0C | ActivateAudioTrack | C→S | Y | O | AT |
| 0x0D | ActivateTextTrack | C→S | Y | O | TT |
| 0x0E | DeactivateTextTrack | C→S | Y | O | TT |

**Events:**
| ID | Name | Priority | Conf |
|----|------|----------|------|
| 0x00 | StateChanged | INFO | O |

**Key Enums:**
- **PlaybackStateEnum:** Playing(0), Paused(1), NotPlaying(2), Buffering(3)
- **StatusEnum:** Success(0), InvalidState(1), NotAllowed(2), NotActive(3), SpeedOutOfRange(4), SeekOutOfRange(5)

---

## Content Launcher (0x050A)
Rev 2 | PICS: CONTENTLAUNCHER

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | CS | ContentSearch | O |
| 1 | UP | URLPlayback | O |
| 2 | AS | AdvancedSeek | O |
| 3 | TT | TextTracks | O |
| 4 | AT | AudioTracks | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | AcceptHeader | list[string] | all | | R V | UP |
| 0x0001 | SupportedStreamingProtocols | SupportedProtocolsBitmap | all | | RW VO | UP |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | LaunchContent | C→S | LauncherResponse | O | CS |
| 0x01 | LaunchURL | C→S | LauncherResponse | O | UP |

**Key Enums:**
- **StatusEnum:** Success(0), URLNotAvailable(1), AuthFailed(2), TextTrackNotAvailable(3), AudioTrackNotAvailable(4)
- **ParameterEnum:** Actor(0), Channel(1), Character(2), Director(3), Event(4), Franchise(5), Genre(6), League(7), Popularity(8), Provider(9), Sport(10), SportsTeam(11), Type(12), Video(13), Season(14), Episode(15), Any(16)
- **SupportedProtocolsBitmap:** DASH(0), HLS(1)

---

## Keypad Input (0x0509)
Rev 1 | PICS: KEYPADINPUT

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | NV | NavigationKeyCodes | O |
| 1 | LK | LocationKeys | O |
| 2 | NK | NumberKeys | O |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SendKey | C→S | SendKeyResponse | O | M |

**Key Enums:**
- **StatusEnum:** Success(0), UnsupportedKey(1), InvalidKeyInCurrentState(2)
- **CecKeyCodeEnum:** Select(0x00), Up(0x01), Down(0x02), Left(0x03), Right(0x04), RootMenu(0x09), Number0-9(0x20-0x29), Power(0x40), VolumeUp(0x41), VolumeDown(0x42), Mute(0x43), Play(0x44), Stop(0x45), Pause(0x46), Record(0x47), Rewind(0x48), FastForward(0x49), ChannelUp(0x30), ChannelDown(0x31), F1Blue-F5(0x71-0x75), etc.

---

## Channel (0x0504)
Rev 2 | PICS: CHANNEL

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | CL | ChannelList | O |
| 1 | LI | LineupInfo | O |
| 2 | EG | ElectronicGuide | O |
| 3 | RP | RecordProgram | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | ChannelList | list[ChannelInfoStruct] | all | | R V | CL |
| 0x0001 | Lineup | LineupInfoStruct | all | X | R V | LI |
| 0x0002 | CurrentChannel | ChannelInfoStruct | all | X | R V | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | ChangeChannel | C→S | ChangeChannelResponse | O | CL \| LI |
| 0x02 | ChangeChannelByNumber | C→S | Y | O | M |
| 0x03 | SkipChannel | C→S | Y | O | M |
| 0x04 | GetProgramGuide | C→S | ProgramGuideResponse | O | EG |
| 0x06 | RecordProgram | C→S | Y | O | RP |
| 0x07 | CancelRecordProgram | C→S | Y | O | RP |

**Key Enums:**
- **StatusEnum:** Success(0), MultipleMatches(1), NoMatches(2)
- **ChannelTypeEnum:** Satellite(0), Cable(1), Terrestrial(2), OTT(3)
- **LineupInfoTypeEnum:** MSO(0)

**Data Types:**
- **ChannelInfoStruct:** MajorNumber, MinorNumber, Name, CallSign, AffiliateCallSign, Identifier, Type

---

## Audio Output (0x050B)
Rev 1 | PICS: AUDIOOUTPUT

**Features:**
| Bit | Code | Name | Conformance |
|-----|------|------|-------------|
| 0 | NU | NameUpdates | O |

**Attributes:**
| ID | Name | Type | Constraint | Q | Access | Conf |
|----|------|------|-----------|---|--------|------|
| 0x0000 | OutputList | list[OutputInfoStruct] | all | | R V | M |
| 0x0001 | CurrentOutput | uint8 | all | | R V | M |

**Commands:**
| ID | Name | Dir | Response | Access | Conf |
|----|------|-----|----------|--------|------|
| 0x00 | SelectOutput | C→S | Y | O | M |
| 0x01 | RenameOutput | C→S | Y | O | NU |

**Key Enums:**
- **OutputTypeEnum:** HDMI(0), BT(1), Optical(2), Headphone(3), Internal(4), Other(5)
