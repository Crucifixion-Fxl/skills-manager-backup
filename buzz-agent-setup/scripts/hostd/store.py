"""Local hostd metadata ledger. No message bodies, credentials, or card tokens."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 19
OVERLAP = 900
JOIN_TTL = 7 * 86400
# Creation FD must close before another local Store can connect to its inode.
_CREATION_LOCK = threading.Lock()
ERROR = ("hostd 本机状态无法安全保存或存在冲突。\n怎么解决：请检查状态目录权限、数据库版本及重复绑定，保留原文件后再重试。"
         "\n复制给 AI：帮我检查 hostd 的本机状态目录、数据库版本和绑定冲突；不要输出凭据、消息正文或个人信息。")


class StoreError(ValueError):
    pass


def ident(value, prefix=None, *, empty=False):
    if empty and value == "":
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", value):
        raise StoreError(ERROR)
    if prefix and not value.startswith(prefix):
        raise StoreError(ERROR)
    return value


def hexid(value, *, empty=False):
    if empty and value == "":
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise StoreError(ERROR)
    return value


def stamp(value):
    if type(value) is not int or not 0 <= value < 2 ** 63:
        raise StoreError(ERROR)
    return value


def localpath(value):
    if not isinstance(value, (str, Path)) or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise StoreError(ERROR)
    return str(value)


@dataclass(frozen=True)
class BindingRecord:
    binding_id: str
    channel_id: str
    chat_id: str
    sync_app_id: str
    config_path: str
    lark_config_dir: str
    lark_data_dir: str
    mirror_pubkey: str = ""
    chat_ref: str = ""
    status: str = "active"

    @classmethod
    def from_binding(cls, binding):
        return cls(binding.name, binding.channel_id, binding.chat_id, binding.sync_app_id,
                   str(binding.config), binding.lark_config_dir, binding.lark_data_dir)


@dataclass(frozen=True)
class Delivery:
    id: str
    status: str
    target_id: str | None
    source_at: int
    attempts: int


@dataclass(frozen=True)
class RestartProcess:
    pid: int
    start: int
    invocation: str

    def __post_init__(self):
        if (stamp(self.pid) == 0 or stamp(self.start) == 0
                or not isinstance(self.invocation, str)
                or not re.fullmatch(r"[0-9a-f]{32}", self.invocation)
                or self.invocation == "0" * 32):
            raise StoreError(ERROR)


@dataclass(frozen=True)
class RestartRecord:
    operation_id: str
    agent_id: str
    scope_hash: str
    protectedfiles_hash: str
    old_process: RestartProcess
    new_process: RestartProcess | None
    state: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class RestartReservation:
    record: RestartRecord
    created: bool


@dataclass(frozen=True)
class OwnHomeAdmissionRecord:
    admission_id: str
    agent_id: str
    snapshot_hash: str
    scope_hash: str
    protectedfiles_hash: str
    catalog_hash: str
    legacy_join_hash: str
    profile_hash: str
    env_before_hash: str
    env_after_hash: str
    agent_spec_hash: str
    prior_channels_hash: str
    proposed_channels_hash: str
    approval_set_hash: str
    old_process: RestartProcess
    state: str
    receipt_hash: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class OwnHomeAdmissionChannel:
    admission_id: str
    ordinal: int
    channel_id: str
    chat_id: str
    chat_ref: str
    source_kind: str
    approval_id: str
    approval_hash: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claimed_at: int
    claim_event_id: str
    policy_event_id: str
    roster_event_id: str
    authorization_hash: str


@dataclass(frozen=True)
class OwnHomeAdmissionRestartLink:
    admission_id: str
    restart_operation_id: str
    scope_hash: str
    created_at: int


@dataclass(frozen=True)
class OwnHomeAdmissionReservation:
    record: OwnHomeAdmissionRecord
    created: bool


@dataclass(frozen=True)
class ConsoleOperation:
    id: str
    principal: str
    request_key_hash: str
    kind: str
    target: str
    action: str
    execution_epoch: str
    status: str
    reason: str
    created_at: int
    updated_at: int
    restart_operation_id: str | None
    receipt_hash: str | None


@dataclass(frozen=True)
class ConsoleReservation:
    record: ConsoleOperation
    created: bool


REMOTE_CAPABILITIES = ("message", "edit", "reaction_add", "reaction_remove")


@dataclass(frozen=True)
class RemoteGrantEvidence:
    """Digest-only input from a trusted verifier, never resolver discovery alone.

    mirror_approval deliberately has no public wire parser here. The caller must
    supply an accepted, verified approval before using that internal boundary.
    """
    agent_id: str
    owner_pubkey: str
    app_id: str
    channel_id: str
    chat_id: str
    chat_ref: str
    relay_origin: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claim_event_id: str
    agent_profile_event_id: str
    agent_policy_event_id: str
    roster_event_id: str
    allowlist_hash: str
    approval_kind: str
    approval_id: str
    approval_hash: str
    capabilities: tuple[str, ...]
    checked_at: int
    valid_until: int
    claimed_at: int


@dataclass(frozen=True)
class RemoteGrantRecord:
    target_id: str
    agent_id: str
    revision: int
    scope_hash: str
    capabilities: tuple[str, ...]
    evidence: RemoteGrantEvidence
    created_at: int


@dataclass(frozen=True)
class RemoteProofRecord:
    """Current verified-read metadata, distinct from immutable original grant."""
    target_id: str
    revision: int
    scope_hash: str
    evidence: RemoteGrantEvidence


@dataclass(frozen=True)
class RemoteDeliveryRecord:
    id: str
    target_id: str
    source_id: str
    action: str
    revision: int
    scope_hash: str
    source_at: int
    content_hash: str
    root_id: str
    status: str
    sender_app_id: str | None
    message_id: str | None
    reaction_id: str
    emoji: str
    receipt_hash: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class RemoteImageUploadRecord:
    target_id: str
    source_id: str
    ordinal: int
    revision: int
    scope_hash: str
    source_at: int
    content_hash: str
    mime_type: str
    byte_size: int
    state: str
    image_key: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class DeliveryNoticeRecord:
    binding_id: str
    source_id: str
    source_author_pubkey: str
    source_app_id: str
    source_created_at: int
    sync_app_id: str
    channel_id: str
    chat_id: str
    root_message_id: str
    first_observed_at: int
    delay_seconds: int
    deadline_at: int
    notice_uuid: str
    notice_version: int
    notice_content_sha256: str
    state: str
    resolved_without_notice_at: int | None
    notice_message_id: str | None
    recovery_version: int | None
    recovery_content_sha256: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class RemoteReservation:
    record: RemoteDeliveryRecord
    created: bool


@dataclass(frozen=True)
class FallbackRequestEvidence:
    subject_pubkey: str
    subject_owner_pubkey: str
    subject_app_id: str
    issuer_app_id: str
    binding_id: str
    channel_id: str
    chat_ref: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claimed_at: int
    catalog_hash: str
    proof_hashes: tuple[str, ...]
    checked_at: int
    valid_until: int


@dataclass(frozen=True)
class FallbackProvenance(FallbackRequestEvidence):
    request_id: str
    scope_hash: str


@dataclass(frozen=True)
class FallbackApprovalDecision:
    request_id: str
    subject_pubkey: str
    subject_owner_pubkey: str
    subject_app_id: str
    issuer_app_id: str
    scope_hash: str
    card_generation: int
    card_message_sha256: str
    decision_event_sha256: str
    decision_at: int


@dataclass(frozen=True)
class FallbackOutwardPin:
    request_id: str
    stage: str
    binding_id: str
    subject_pubkey: str
    subject_owner_pubkey: str
    subject_app_id: str
    issuer_app_id: str
    channel_id: str
    chat_ref: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claimed_at: int
    catalog_hash: str
    request_created_at: int
    request_deadline: int
    card_generation: int
    card_message_sha256: str
    decision_event_sha256: str
    decision_at: int
    signer_pubkey: str
    event_created_at: int
    content_hash: str
    event_id: str
    signature: str
    scope_hash: str
    protectedfiles_hash: str


@dataclass(frozen=True)
class FallbackOutwardRecord:
    pin: FallbackOutwardPin
    state: str
    observed_hash: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class FallbackOutwardReservation:
    record: FallbackOutwardRecord
    created: bool


@dataclass(frozen=True)
class CardApprovalDecision:
    request_id: str
    agent_pubkey: str
    agent_owner_pubkey: str
    app_id: str
    chat_ref: str
    request_created_at: int
    request_deadline: int
    card_generation: int
    card_message_sha256: str
    decision_event_sha256: str
    decision_at: int


@dataclass(frozen=True)
class ApprovalPublicationPin:
    request_id: str
    agent_pubkey: str
    agent_owner_pubkey: str
    app_id: str
    channel_id: str
    chat_ref: str
    mirror_pubkey: str
    mirror_owner_pubkey: str
    claimed_at: int
    request_created_at: int
    request_deadline: int
    card_generation: int
    card_message_sha256: str
    decision_event_sha256: str
    decision_at: int
    binding_id: str
    event_created_at: int
    content_hash: str
    event_id: str
    signature: str
    scope_hash: str
    protectedfiles_hash: str
    plan_hash: str


@dataclass(frozen=True)
class ApprovalPublicationRecord:
    pin: ApprovalPublicationPin
    state: str
    observed_hash: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class ApprovalPublicationReservation:
    record: ApprovalPublicationRecord
    created: bool


_SCHEMA_V1 = """
CREATE TABLE app_profile(app_id TEXT PRIMARY KEY, config_dir TEXT NOT NULL, data_dir TEXT NOT NULL);
CREATE TABLE binding(binding_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, chat_id TEXT NOT NULL,
 sync_app_id TEXT NOT NULL REFERENCES app_profile(app_id), config_path TEXT NOT NULL,
 mirror_pubkey TEXT NOT NULL DEFAULT '', chat_ref TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('pending','active','paused','degraded','conflict','retired')),
 claimed_at INTEGER NOT NULL, heartbeat_at INTEGER NOT NULL);
CREATE UNIQUE INDEX binding_channel ON binding(channel_id) WHERE status != 'retired';
CREATE UNIQUE INDEX binding_chat ON binding(chat_id) WHERE status != 'retired';
CREATE TABLE agent(pubkey TEXT PRIMARY KEY, owner_pubkey TEXT NOT NULL, app_id TEXT UNIQUE,
 config_path TEXT, status TEXT NOT NULL CHECK(status IN ('active','paused','retired')), updated_at INTEGER NOT NULL);
CREATE TABLE delivery(id TEXT PRIMARY KEY, binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 agent_id TEXT REFERENCES agent(pubkey), source_id TEXT NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('f2b','b2f','e2f','r2f','f2r','image','intro')),
 stream TEXT NOT NULL CHECK(stream IN ('feishu','relay','reaction','members')),
 target_id TEXT, root_id TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('pending','acked','failed','unknown','waiting_receipt','skipped','removed','abandoned')),
 source_at INTEGER NOT NULL CHECK(source_at>=0), attempts INTEGER NOT NULL DEFAULT 1,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE UNIQUE INDEX delivery_operation ON delivery(binding_id,direction,source_id,coalesce(agent_id,''));
CREATE TABLE cursor(binding_id TEXT NOT NULL REFERENCES binding(binding_id), agent_id TEXT NOT NULL DEFAULT '',
 stream TEXT NOT NULL CHECK(stream IN ('feishu','relay','reaction','members')), position INTEGER NOT NULL CHECK(position>=0),
 updated_at INTEGER NOT NULL, PRIMARY KEY(binding_id,agent_id,stream));
CREATE TABLE join_request(request_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agent(pubkey),
 owner_pubkey TEXT NOT NULL, callback_app_id TEXT NOT NULL, chat_id TEXT NOT NULL,
 binding_id TEXT REFERENCES binding(binding_id), kind TEXT NOT NULL CHECK(kind IN ('channel','new_binding')),
 status TEXT NOT NULL CHECK(status IN ('requested','approved','applied','done','denied','expired')),
 card_message_id TEXT, card_generation INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL, deadline INTEGER NOT NULL, CHECK(deadline>created_at));
CREATE TABLE join_decision(app_id TEXT NOT NULL, event_id TEXT NOT NULL,
 request_id TEXT NOT NULL REFERENCES join_request(request_id), created_at INTEGER NOT NULL,
 PRIMARY KEY(app_id,event_id));
CREATE TABLE agent_chat(agent_id TEXT NOT NULL REFERENCES agent(pubkey), chat_id TEXT NOT NULL,
 chat_ref TEXT NOT NULL, binding_id TEXT REFERENCES binding(binding_id),
 status TEXT NOT NULL CHECK(status IN ('active','paused','waiting_receipt','degraded','retired')),
 updated_at INTEGER NOT NULL, PRIMARY KEY(agent_id,chat_id));
CREATE TABLE connection(kind TEXT NOT NULL CHECK(kind IN ('feishu','relay','outlet')), identity TEXT NOT NULL,
 binding_id TEXT REFERENCES binding(binding_id), status TEXT NOT NULL
 CHECK(status IN ('connecting','connected','disconnected','backoff','failed','stopped')),
 connected_at INTEGER NOT NULL DEFAULT 0,last_event_at INTEGER NOT NULL DEFAULT 0,
 reconnects INTEGER NOT NULL DEFAULT 0,error_code TEXT NOT NULL DEFAULT ''
 CHECK(error_code IN ('','network','permission','credentials','protocol','callback')),
 updated_at INTEGER NOT NULL,PRIMARY KEY(kind,identity));
CREATE TABLE state_snapshot(binding_id TEXT PRIMARY KEY REFERENCES binding(binding_id), revision INTEGER NOT NULL,
 imported_hash TEXT NOT NULL DEFAULT '', import_path TEXT, state_hash TEXT NOT NULL,
 identity_profile TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL);
CREATE TABLE state_migration(binding_id TEXT NOT NULL REFERENCES state_snapshot(binding_id), old_profile TEXT NOT NULL,
 new_profile TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(binding_id,new_profile));
CREATE TABLE state_scalar(binding_id TEXT NOT NULL REFERENCES state_snapshot(binding_id), field TEXT NOT NULL,
 value_text TEXT, value_int INTEGER, PRIMARY KEY(binding_id,field));
CREATE TABLE state_map(binding_id TEXT NOT NULL REFERENCES state_snapshot(binding_id), field TEXT NOT NULL,
 item_key TEXT NOT NULL, value_text TEXT, value_int INTEGER, PRIMARY KEY(binding_id,field,item_key));
CREATE TABLE state_member_event(binding_id TEXT NOT NULL REFERENCES state_snapshot(binding_id), operation TEXT NOT NULL,
 event_id TEXT NOT NULL,pubkey TEXT NOT NULL,created_at INTEGER NOT NULL,
 kind INTEGER NOT NULL CHECK(kind IN (9000,9001)),tags TEXT NOT NULL,sig TEXT NOT NULL,
 PRIMARY KEY(binding_id,operation));
"""

_JOIN_TRANSPORT_SCHEMA = """
CREATE TABLE join_transport(request_id TEXT PRIMARY KEY REFERENCES join_request(request_id),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), next_due INTEGER NOT NULL DEFAULT 0 CHECK(next_due>=0),
 dm_reserved INTEGER NOT NULL DEFAULT 0 CHECK(dm_reserved IN (0,1)),
 send_generation INTEGER NOT NULL DEFAULT 0 CHECK(send_generation>=0),
 send_status TEXT NOT NULL DEFAULT 'idle' CHECK(send_status IN ('idle','reserved','sent','failed','unknown')),
 lease_until INTEGER NOT NULL DEFAULT 0 CHECK(lease_until>=0),
 pending_message_id TEXT, updated_at INTEGER NOT NULL CHECK(updated_at>=0));
CREATE TABLE join_feedback(app_id TEXT NOT NULL,event_id TEXT NOT NULL,
 request_id TEXT NOT NULL REFERENCES join_request(request_id),union_id TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(reason IN ('owner','identity','card','expired')),
 status TEXT NOT NULL CHECK(status IN ('reserved','sent','unknown')),updated_at INTEGER NOT NULL,
 PRIMARY KEY(app_id,event_id));
CREATE TABLE join_notice(request_id TEXT NOT NULL REFERENCES join_request(request_id),message_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('approved','blocked','done','denied','expired','superseded')),
 status TEXT NOT NULL CHECK(status IN ('pending','reserved','sent','obsolete')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),next_due INTEGER NOT NULL DEFAULT 0 CHECK(next_due>=0),
 lease_until INTEGER NOT NULL DEFAULT 0 CHECK(lease_until>=0),updated_at INTEGER NOT NULL,
 PRIMARY KEY(request_id,message_id));
"""
_SCHEMA_V2 = _SCHEMA_V1 + _JOIN_TRANSPORT_SCHEMA
TARGET_EVENT_TYPES = frozenset(("im.message.receive_v1", "im.message.reaction.created_v1", "im.message.reaction.deleted_v1"))
_UPGRADE_V3_SCHEMA = """
CREATE TABLE event_target(binding_id TEXT NOT NULL REFERENCES binding(binding_id),app_id TEXT NOT NULL,
 message_id TEXT NOT NULL,root_id TEXT NOT NULL,event_type TEXT NOT NULL
 CHECK(event_type IN ('im.message.receive_v1','im.message.reaction.created_v1','im.message.reaction.deleted_v1')),
 first_seen_at INTEGER NOT NULL CHECK(first_seen_at>=0),PRIMARY KEY(binding_id,app_id,message_id,event_type));
CREATE TABLE outlet_receipt(delivery_id TEXT PRIMARY KEY REFERENCES delivery(id),sender_app_id TEXT NOT NULL,
 target_message_id TEXT NOT NULL,reaction_id TEXT NOT NULL DEFAULT '',emoji TEXT NOT NULL DEFAULT ''
 CHECK(emoji IN ('','GLANCE','Typing','DONE','THUMBSUP','OK','THANKS','MUSCLE','CrossMark')),
 CHECK((reaction_id='' AND emoji='') OR (reaction_id!='' AND emoji!='')));
CREATE TABLE effect_plan(request_id TEXT PRIMARY KEY REFERENCES join_request(request_id),channel_id TEXT NOT NULL,
 binding_id TEXT NOT NULL,mirror_pubkey TEXT NOT NULL DEFAULT '',secret_ref TEXT NOT NULL,config_path TEXT NOT NULL,
 created_at INTEGER NOT NULL CHECK(created_at>=0),updated_at INTEGER NOT NULL CHECK(updated_at>=0));
CREATE TABLE effect_step(request_id TEXT NOT NULL REFERENCES effect_plan(request_id),step TEXT NOT NULL
 CHECK(step IN ('channel','mirror','members','claim','config','agent_env','agent_prompt','agent_responsible','runtime','registrar','cleanup')),
 status TEXT NOT NULL CHECK(status IN ('reserved','verified','unknown','waiting_idle','failed')),
 intent_hash TEXT NOT NULL,observed_hash TEXT NOT NULL DEFAULT '',output_id TEXT NOT NULL DEFAULT '',
 lease_until INTEGER NOT NULL CHECK(lease_until>=0),attempts INTEGER NOT NULL CHECK(attempts>0),operation_at INTEGER NOT NULL CHECK(operation_at>=0),updated_at INTEGER NOT NULL,
 PRIMARY KEY(request_id,step));
"""
_SCHEMA_V3 = _SCHEMA_V2 + _UPGRADE_V3_SCHEMA
_UPGRADE_V4_SCHEMA = """
CREATE TABLE restart_operation(operation_id TEXT PRIMARY KEY NOT NULL
 CHECK(length(operation_id) BETWEEN 1 AND 256 AND operation_id GLOB '[A-Za-z0-9]*'
 AND operation_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 agent_id TEXT NOT NULL REFERENCES agent(pubkey),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64 AND scope_hash NOT GLOB '*[^0-9a-f]*'),
 protectedfiles_hash TEXT NOT NULL CHECK(length(protectedfiles_hash)=64 AND protectedfiles_hash NOT GLOB '*[^0-9a-f]*'),
 old_pid INTEGER NOT NULL CHECK(typeof(old_pid)='integer' AND old_pid>0),
 old_start INTEGER NOT NULL CHECK(typeof(old_start)='integer' AND old_start>0),
 old_invocation TEXT NOT NULL CHECK(length(old_invocation)=32 AND old_invocation NOT GLOB '*[^0-9a-f]*'
 AND old_invocation!='00000000000000000000000000000000'),
 new_pid INTEGER,new_start INTEGER,new_invocation TEXT,
 state TEXT NOT NULL CHECK(state IN ('reserved','unknown','acked')),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 CHECK((new_pid IS NULL AND new_start IS NULL AND new_invocation IS NULL)
 OR (new_pid IS NOT NULL AND new_start IS NOT NULL AND new_invocation IS NOT NULL
 AND typeof(new_pid)='integer' AND new_pid>0 AND typeof(new_start)='integer' AND new_start>0
 AND length(new_invocation)=32 AND new_invocation NOT GLOB '*[^0-9a-f]*'
 AND new_invocation!='00000000000000000000000000000000'
 AND new_invocation!=old_invocation AND (new_pid!=old_pid OR new_start!=old_start))),
 CHECK(state!='reserved' OR new_pid IS NULL),CHECK(state!='acked' OR new_pid IS NOT NULL));
CREATE UNIQUE INDEX restart_one_active_agent ON restart_operation(agent_id) WHERE state IN ('reserved','unknown');
"""
_SCHEMA_V4 = _SCHEMA_V3 + _UPGRADE_V4_SCHEMA
_UPGRADE_V5_SCHEMA = """
CREATE TABLE console_operation(id TEXT PRIMARY KEY NOT NULL
 CHECK(length(id)=64 AND id NOT GLOB '*[^0-9a-f]*'),
 principal TEXT NOT NULL CHECK(length(principal)=64 AND principal NOT GLOB '*[^0-9a-f]*'),
 request_key_hash TEXT NOT NULL UNIQUE CHECK(length(request_key_hash)=64 AND request_key_hash NOT GLOB '*[^0-9a-f]*'),
 kind TEXT NOT NULL,binding_id TEXT REFERENCES binding(binding_id),agent_id TEXT REFERENCES agent(pubkey),
 action TEXT NOT NULL,
 execution_epoch TEXT NOT NULL CHECK(length(execution_epoch)=64 AND execution_epoch NOT GLOB '*[^0-9a-f]*'),
 status TEXT NOT NULL CHECK(status IN ('queued','dispatched','unknown','completed','rejected')),
 reason TEXT NOT NULL CHECK(reason IN ('','cancellation','timeout','recovered','scope','proof','missing','busy','driver')),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 restart_operation_id TEXT UNIQUE REFERENCES restart_operation(operation_id),
 receipt_hash TEXT CHECK(receipt_hash IS NULL OR (length(receipt_hash)=64 AND receipt_hash NOT GLOB '*[^0-9a-f]*')),
 CHECK((kind='binding' AND binding_id IS NOT NULL AND agent_id IS NULL AND action IN ('pause','resume','backfill'))
 OR (kind='agent' AND agent_id IS NOT NULL AND binding_id IS NULL AND action='restart')),
 CHECK(restart_operation_id IS NULL OR kind='agent'),
 CHECK((status='completed' AND receipt_hash IS NOT NULL) OR (status!='completed' AND receipt_hash IS NULL)),
 CHECK(status!='completed' OR kind!='agent' OR restart_operation_id IS NOT NULL),
 CHECK(status!='queued' OR restart_operation_id IS NULL),
 CHECK(status!='rejected' OR (restart_operation_id IS NULL AND reason!='')));
CREATE UNIQUE INDEX console_one_pending_binding ON console_operation(binding_id) WHERE status IN ('queued','dispatched','unknown');
CREATE UNIQUE INDEX console_one_pending_agent ON console_operation(agent_id) WHERE status IN ('queued','dispatched','unknown');
"""
_SCHEMA_V5 = _SCHEMA_V4 + _UPGRADE_V5_SCHEMA
_UPGRADE_V6_SCHEMA = """
CREATE TABLE remote_target(target_id TEXT PRIMARY KEY NOT NULL
 CHECK(length(target_id)=64 AND target_id NOT GLOB '*[^0-9a-f]*'),
 agent_id TEXT NOT NULL REFERENCES agent(pubkey),owner_pubkey TEXT NOT NULL,app_id TEXT NOT NULL,
 channel_id TEXT NOT NULL,chat_id TEXT NOT NULL,chat_ref TEXT NOT NULL,relay_origin TEXT NOT NULL,
 current_revision INTEGER NOT NULL CHECK(typeof(current_revision)='integer' AND current_revision>0),
 status TEXT NOT NULL CHECK(status IN ('active','suspended')),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 proof_checked_at INTEGER NOT NULL CHECK(typeof(proof_checked_at)='integer' AND proof_checked_at>=0),
 proof_valid_until INTEGER NOT NULL CHECK(typeof(proof_valid_until)='integer' AND proof_valid_until>proof_checked_at),
 proof_claim_event_id TEXT NOT NULL,proof_agent_profile_event_id TEXT NOT NULL,
 proof_agent_policy_event_id TEXT NOT NULL,proof_roster_event_id TEXT NOT NULL,
 UNIQUE(agent_id,channel_id),UNIQUE(agent_id,chat_id),
 FOREIGN KEY(target_id,current_revision) REFERENCES remote_grant(target_id,revision) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE remote_grant(target_id TEXT NOT NULL REFERENCES remote_target(target_id),
 revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64 AND scope_hash NOT GLOB '*[^0-9a-f]*'),
 mirror_pubkey TEXT NOT NULL,mirror_owner_pubkey TEXT NOT NULL,claim_event_id TEXT NOT NULL,
 agent_profile_event_id TEXT NOT NULL,agent_policy_event_id TEXT NOT NULL,roster_event_id TEXT NOT NULL,
 allowlist_hash TEXT NOT NULL,approval_kind TEXT NOT NULL CHECK(approval_kind IN ('local_card','mirror_approval')),
 approval_id TEXT NOT NULL,approval_hash TEXT NOT NULL,
 capabilities INTEGER NOT NULL CHECK(typeof(capabilities)='integer' AND capabilities BETWEEN 1 AND 15),
 checked_at INTEGER NOT NULL CHECK(typeof(checked_at)='integer' AND checked_at>=0),
 valid_until INTEGER NOT NULL CHECK(typeof(valid_until)='integer' AND valid_until>checked_at),
 claimed_at INTEGER NOT NULL CHECK(typeof(claimed_at)='integer' AND claimed_at>=0 AND claimed_at<=checked_at),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=checked_at),
 PRIMARY KEY(target_id,revision));
CREATE TABLE remote_delivery(id TEXT PRIMARY KEY NOT NULL
 CHECK(length(id)=64 AND id NOT GLOB '*[^0-9a-f]*'),target_id TEXT NOT NULL,
 source_id TEXT NOT NULL CHECK(length(source_id)=64 AND source_id NOT GLOB '*[^0-9a-f]*'),
 action TEXT NOT NULL CHECK(action IN ('message','edit','reaction_add','reaction_remove')),
 revision INTEGER NOT NULL,scope_hash TEXT NOT NULL
 CHECK(length(scope_hash)=64 AND scope_hash NOT GLOB '*[^0-9a-f]*'),
 source_at INTEGER NOT NULL CHECK(typeof(source_at)='integer' AND source_at>=0),
 content_hash TEXT NOT NULL CHECK(length(content_hash)=64 AND content_hash NOT GLOB '*[^0-9a-f]*'),
 root_id TEXT NOT NULL CHECK(root_id='' OR (length(root_id)=64 AND root_id NOT GLOB '*[^0-9a-f]*')),
 status TEXT NOT NULL CHECK(status IN ('reserved','unknown','acked')),
 sender_app_id TEXT,message_id TEXT,reaction_id TEXT NOT NULL DEFAULT '',
 emoji TEXT NOT NULL DEFAULT '' CHECK(emoji IN ('','GLANCE','Typing','DONE','THUMBSUP','OK','THANKS','MUSCLE','CrossMark')),
 receipt_hash TEXT CHECK(receipt_hash IS NULL OR (length(receipt_hash)=64 AND receipt_hash NOT GLOB '*[^0-9a-f]*')),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 UNIQUE(target_id,source_id,action),FOREIGN KEY(target_id,revision) REFERENCES remote_grant(target_id,revision),
 CHECK((status='acked' AND sender_app_id IS NOT NULL AND message_id IS NOT NULL AND receipt_hash IS NOT NULL
 AND ((action IN ('message','edit') AND reaction_id='' AND emoji='')
 OR (action IN ('reaction_add','reaction_remove') AND reaction_id!='' AND emoji!='')))
 OR (status!='acked' AND sender_app_id IS NULL AND message_id IS NULL AND receipt_hash IS NULL AND reaction_id='' AND emoji='')));
CREATE INDEX remote_delivery_pending ON remote_delivery(target_id,created_at,id) WHERE status IN ('reserved','unknown');
CREATE TABLE remote_cursor(target_id TEXT PRIMARY KEY NOT NULL,
 position INTEGER NOT NULL CHECK(typeof(position)='integer' AND position>=0),revision INTEGER NOT NULL,
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
 FOREIGN KEY(target_id,revision) REFERENCES remote_grant(target_id,revision));
"""
_SCHEMA_V6 = _SCHEMA_V5 + _UPGRADE_V6_SCHEMA
_UPGRADE_V7_SCHEMA = """
CREATE TABLE approval_publication(request_id TEXT PRIMARY KEY REFERENCES join_request(request_id),
 agent_pubkey TEXT NOT NULL REFERENCES agent(pubkey),agent_owner_pubkey TEXT NOT NULL,app_id TEXT NOT NULL,
 channel_id TEXT NOT NULL,chat_ref TEXT NOT NULL,mirror_pubkey TEXT NOT NULL,mirror_owner_pubkey TEXT NOT NULL,
 claimed_at INTEGER NOT NULL CHECK(typeof(claimed_at)='integer' AND claimed_at>=0),
 request_created_at INTEGER NOT NULL CHECK(typeof(request_created_at)='integer' AND request_created_at>=0),
 request_deadline INTEGER NOT NULL CHECK(typeof(request_deadline)='integer' AND request_deadline=request_created_at+604800),
 card_generation INTEGER NOT NULL CHECK(typeof(card_generation)='integer' AND card_generation>0),
 card_message_sha256 TEXT NOT NULL,decision_event_sha256 TEXT NOT NULL,
 decision_at INTEGER NOT NULL CHECK(typeof(decision_at)='integer' AND decision_at>=request_created_at AND decision_at<=request_deadline AND decision_at>=claimed_at),
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 event_created_at INTEGER NOT NULL CHECK(typeof(event_created_at)='integer' AND event_created_at>=decision_at),
 content_hash TEXT NOT NULL,event_id TEXT NOT NULL UNIQUE,
 signature TEXT NOT NULL CHECK(length(signature)=128 AND signature NOT GLOB '*[^0-9a-f]*'),
 scope_hash TEXT NOT NULL,protectedfiles_hash TEXT NOT NULL,plan_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','unknown','acked')),
 observed_hash TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=event_created_at-900),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 CHECK((state='acked' AND length(observed_hash)=64 AND observed_hash NOT GLOB '*[^0-9a-f]*') OR (state!='acked' AND observed_hash='')));
"""
_SCHEMA_V7 = _SCHEMA_V6 + _UPGRADE_V7_SCHEMA
_UPGRADE_V8_SCHEMA = """
CREATE TABLE fallback_request(request_id TEXT PRIMARY KEY REFERENCES join_request(request_id),
 subject_pubkey TEXT NOT NULL UNIQUE REFERENCES agent(pubkey),subject_owner_pubkey TEXT NOT NULL,
 subject_app_id TEXT NOT NULL,issuer_app_id TEXT NOT NULL,binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 channel_id TEXT NOT NULL,chat_ref TEXT NOT NULL,mirror_pubkey TEXT NOT NULL,mirror_owner_pubkey TEXT NOT NULL,
 claimed_at INTEGER NOT NULL CHECK(typeof(claimed_at)='integer' AND claimed_at>=0),
 catalog_hash TEXT NOT NULL,proof_hashes TEXT NOT NULL,
 checked_at INTEGER NOT NULL CHECK(typeof(checked_at)='integer' AND checked_at>=0),
 valid_until INTEGER NOT NULL CHECK(typeof(valid_until)='integer' AND valid_until>checked_at AND valid_until<=checked_at+30),
 scope_hash TEXT NOT NULL);
"""
_SCHEMA_V8 = _SCHEMA_V7 + _UPGRADE_V8_SCHEMA
_UPGRADE_V9_SCHEMA = """
CREATE TABLE fallback_outward(request_id TEXT NOT NULL REFERENCES fallback_request(request_id),
 stage TEXT NOT NULL CHECK(stage IN ('member','approval')),
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 subject_pubkey TEXT NOT NULL,subject_owner_pubkey TEXT NOT NULL,subject_app_id TEXT NOT NULL,
 issuer_app_id TEXT NOT NULL,channel_id TEXT NOT NULL,chat_ref TEXT NOT NULL,
 mirror_pubkey TEXT NOT NULL,mirror_owner_pubkey TEXT NOT NULL,
 claimed_at INTEGER NOT NULL CHECK(typeof(claimed_at)='integer' AND claimed_at>=0),catalog_hash TEXT NOT NULL,
 request_created_at INTEGER NOT NULL CHECK(typeof(request_created_at)='integer' AND request_created_at>=0),
 request_deadline INTEGER NOT NULL CHECK(typeof(request_deadline)='integer' AND request_deadline=request_created_at+604800),
 card_generation INTEGER NOT NULL CHECK(typeof(card_generation)='integer' AND card_generation>=1),
 card_message_sha256 TEXT NOT NULL,decision_event_sha256 TEXT NOT NULL,
 decision_at INTEGER NOT NULL CHECK(typeof(decision_at)='integer' AND decision_at>=request_created_at AND decision_at<request_deadline AND decision_at>=claimed_at),
 signer_pubkey TEXT NOT NULL,
 event_created_at INTEGER NOT NULL CHECK(typeof(event_created_at)='integer' AND event_created_at>=decision_at),
 content_hash TEXT NOT NULL,event_id TEXT NOT NULL UNIQUE,
 signature TEXT NOT NULL CHECK(length(signature)=128 AND signature NOT GLOB '*[^0-9a-f]*'),
 scope_hash TEXT NOT NULL,protectedfiles_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','unknown','acked')),
 observed_hash TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=event_created_at-900),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 PRIMARY KEY(request_id,stage),
 CHECK((state='acked' AND length(observed_hash)=64 AND observed_hash NOT GLOB '*[^0-9a-f]*') OR (state!='acked' AND observed_hash='')));
"""
_SCHEMA_V9 = _SCHEMA_V8 + _UPGRADE_V9_SCHEMA
_UPGRADE_V10_SCHEMA = """
CREATE TABLE own_home_admission(
 admission_id TEXT PRIMARY KEY NOT NULL CHECK(length(admission_id)=64 AND admission_id NOT GLOB '*[^0-9a-f]*'),
 agent_id TEXT NOT NULL REFERENCES agent(pubkey) CHECK(length(agent_id)=64 AND agent_id NOT GLOB '*[^0-9a-f]*'),
 snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash)=64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*'),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64 AND scope_hash NOT GLOB '*[^0-9a-f]*'),
 protectedfiles_hash TEXT NOT NULL CHECK(length(protectedfiles_hash)=64 AND protectedfiles_hash NOT GLOB '*[^0-9a-f]*'),
 catalog_hash TEXT NOT NULL CHECK(length(catalog_hash)=64 AND catalog_hash NOT GLOB '*[^0-9a-f]*'),
 legacy_join_hash TEXT NOT NULL CHECK(length(legacy_join_hash)=64 AND legacy_join_hash NOT GLOB '*[^0-9a-f]*'),
 profile_hash TEXT NOT NULL CHECK(length(profile_hash)=64 AND profile_hash NOT GLOB '*[^0-9a-f]*'),
 env_before_hash TEXT NOT NULL CHECK(length(env_before_hash)=64 AND env_before_hash NOT GLOB '*[^0-9a-f]*'),
 env_after_hash TEXT NOT NULL CHECK(length(env_after_hash)=64 AND env_after_hash NOT GLOB '*[^0-9a-f]*'),
 agent_spec_hash TEXT NOT NULL CHECK(length(agent_spec_hash)=64 AND agent_spec_hash NOT GLOB '*[^0-9a-f]*'),
 prior_channels_hash TEXT NOT NULL CHECK(length(prior_channels_hash)=64 AND prior_channels_hash NOT GLOB '*[^0-9a-f]*'),
 proposed_channels_hash TEXT NOT NULL CHECK(length(proposed_channels_hash)=64 AND proposed_channels_hash NOT GLOB '*[^0-9a-f]*'),
 approval_set_hash TEXT NOT NULL CHECK(length(approval_set_hash)=64 AND approval_set_hash NOT GLOB '*[^0-9a-f]*'),
 channel_count INTEGER NOT NULL CHECK(typeof(channel_count)='integer' AND channel_count>=1 AND channel_count<=256),
 channels_sealed INTEGER NOT NULL CHECK(typeof(channels_sealed)='integer' AND channels_sealed IN (0,1)),
 old_pid INTEGER NOT NULL CHECK(typeof(old_pid)='integer' AND old_pid>0),
 old_start INTEGER NOT NULL CHECK(typeof(old_start)='integer' AND old_start>0),
 old_invocation TEXT NOT NULL CHECK(length(old_invocation)=32 AND old_invocation NOT GLOB '*[^0-9a-f]*' AND old_invocation!='00000000000000000000000000000000'),
 state TEXT NOT NULL CHECK(state IN ('reserved','unknown','acked')),
 receipt_hash TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 CHECK((state='acked' AND length(receipt_hash)=64 AND receipt_hash NOT GLOB '*[^0-9a-f]*') OR (state!='acked' AND receipt_hash=''))
);
CREATE UNIQUE INDEX own_home_admission_one_active ON own_home_admission(agent_id) WHERE state IN ('reserved','unknown');
CREATE TABLE own_home_admission_channel(
 admission_id TEXT NOT NULL REFERENCES own_home_admission(admission_id),
 ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>=0 AND ordinal<256),
 channel_id TEXT NOT NULL CHECK(length(channel_id)=36 AND channel_id GLOB '????????-????-????-????-????????????' AND channel_id NOT GLOB '*[^0-9a-f-]*'),
 chat_id TEXT NOT NULL CHECK(length(chat_id)>3 AND length(chat_id)<=256 AND substr(chat_id,1,3)='oc_' AND chat_id NOT GLOB '*[^A-Za-z0-9_]*'),
 chat_ref TEXT NOT NULL CHECK(length(chat_ref)=64 AND chat_ref NOT GLOB '*[^0-9a-f]*'),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('own_approval','local_grant','remote_grant')),
 approval_id TEXT NOT NULL CHECK(length(approval_id)=64 AND approval_id NOT GLOB '*[^0-9a-f]*'),
 approval_hash TEXT NOT NULL CHECK(length(approval_hash)=64 AND approval_hash NOT GLOB '*[^0-9a-f]*'),
 mirror_pubkey TEXT NOT NULL CHECK(length(mirror_pubkey)=64 AND mirror_pubkey NOT GLOB '*[^0-9a-f]*'),
 mirror_owner_pubkey TEXT NOT NULL CHECK(length(mirror_owner_pubkey)=64 AND mirror_owner_pubkey NOT GLOB '*[^0-9a-f]*'),
 claimed_at INTEGER NOT NULL CHECK(typeof(claimed_at)='integer' AND claimed_at>=0),
 claim_event_id TEXT NOT NULL CHECK(length(claim_event_id)=64 AND claim_event_id NOT GLOB '*[^0-9a-f]*'),
 policy_event_id TEXT NOT NULL CHECK(length(policy_event_id)=64 AND policy_event_id NOT GLOB '*[^0-9a-f]*'),
 roster_event_id TEXT NOT NULL CHECK(length(roster_event_id)=64 AND roster_event_id NOT GLOB '*[^0-9a-f]*'),
 authorization_hash TEXT NOT NULL CHECK(length(authorization_hash)=64 AND authorization_hash NOT GLOB '*[^0-9a-f]*'),
 PRIMARY KEY(admission_id,channel_id), UNIQUE(admission_id,ordinal)
);
CREATE TABLE own_home_admission_restart(
 admission_id TEXT PRIMARY KEY NOT NULL REFERENCES own_home_admission(admission_id),
 restart_operation_id TEXT NOT NULL UNIQUE REFERENCES restart_operation(operation_id),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64 AND scope_hash NOT GLOB '*[^0-9a-f]*'),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
);
CREATE TRIGGER own_home_admission_insert_guard BEFORE INSERT ON own_home_admission
WHEN hostd_own_home_writer()!=1 OR NEW.channels_sealed!=0
BEGIN SELECT RAISE(ABORT,'own home admission must use Store reservation'); END;
CREATE TRIGGER own_home_admission_channel_insert_guard BEFORE INSERT ON own_home_admission_channel
WHEN hostd_own_home_writer()!=1 OR NOT EXISTS(SELECT 1 FROM own_home_admission p
 WHERE p.admission_id=NEW.admission_id AND p.channels_sealed=0 AND p.state='reserved')
BEGIN SELECT RAISE(ABORT,'own home channels must use Store reservation'); END;
CREATE TRIGGER own_home_admission_restart_insert_guard BEFORE INSERT ON own_home_admission_restart
WHEN hostd_own_home_restart_witness(NEW.admission_id,NEW.restart_operation_id)!=1
 OR NOT EXISTS(SELECT 1 FROM own_home_admission p JOIN restart_operation r
 ON r.operation_id=NEW.restart_operation_id WHERE p.admission_id=NEW.admission_id
 AND p.state='unknown' AND p.agent_id=r.agent_id AND p.scope_hash=r.scope_hash
 AND p.scope_hash=NEW.scope_hash AND p.protectedfiles_hash=r.protectedfiles_hash
 AND p.old_pid=r.old_pid AND p.old_start=r.old_start AND p.old_invocation=r.old_invocation
 AND r.state='unknown' AND r.new_pid IS NULL)
BEGIN SELECT RAISE(ABORT,'own home restart link lacks original transaction witness'); END;
CREATE TRIGGER own_home_admission_immutable_update BEFORE UPDATE ON own_home_admission
WHEN NOT (
 OLD.admission_id=NEW.admission_id AND OLD.agent_id=NEW.agent_id AND OLD.snapshot_hash=NEW.snapshot_hash
 AND OLD.scope_hash=NEW.scope_hash AND OLD.protectedfiles_hash=NEW.protectedfiles_hash
 AND OLD.catalog_hash=NEW.catalog_hash AND OLD.legacy_join_hash=NEW.legacy_join_hash
 AND OLD.profile_hash=NEW.profile_hash AND OLD.env_before_hash=NEW.env_before_hash
 AND OLD.env_after_hash=NEW.env_after_hash AND OLD.agent_spec_hash=NEW.agent_spec_hash
 AND OLD.prior_channels_hash=NEW.prior_channels_hash AND OLD.proposed_channels_hash=NEW.proposed_channels_hash
 AND OLD.approval_set_hash=NEW.approval_set_hash AND OLD.old_pid=NEW.old_pid
 AND OLD.old_start=NEW.old_start AND OLD.old_invocation=NEW.old_invocation
 AND OLD.created_at=NEW.created_at
 AND NEW.updated_at>=OLD.updated_at
 AND OLD.channel_count=NEW.channel_count
 AND ((OLD.channels_sealed=0 AND NEW.channels_sealed=1 AND OLD.state=NEW.state
       AND OLD.receipt_hash=NEW.receipt_hash AND OLD.updated_at=NEW.updated_at
       AND (SELECT count(*) FROM own_home_admission_channel c
            WHERE c.admission_id=OLD.admission_id)=OLD.channel_count)
   OR (OLD.channels_sealed=1 AND NEW.channels_sealed=1 AND
       ((OLD.state='reserved' AND NEW.state='unknown' AND NEW.receipt_hash=''
         AND EXISTS(SELECT 1 FROM agent a WHERE a.pubkey=OLD.agent_id AND a.status='active'))
        OR (OLD.state='unknown' AND NEW.state='acked' AND length(NEW.receipt_hash)=64
            AND NEW.receipt_hash NOT GLOB '*[^0-9a-f]*'
            AND EXISTS(SELECT 1 FROM own_home_admission_restart l JOIN restart_operation r
                ON r.operation_id=l.restart_operation_id WHERE l.admission_id=OLD.admission_id
                AND l.scope_hash=OLD.scope_hash AND r.agent_id=OLD.agent_id
                AND r.scope_hash=OLD.scope_hash AND r.protectedfiles_hash=OLD.protectedfiles_hash
                AND r.old_pid=OLD.old_pid AND r.old_start=OLD.old_start
                AND r.old_invocation=OLD.old_invocation AND r.state='acked'
                AND r.new_pid IS NOT NULL)))))
)
BEGIN SELECT RAISE(ABORT,'immutable own home admission'); END;
CREATE TRIGGER own_home_admission_immutable_delete BEFORE DELETE ON own_home_admission
BEGIN SELECT RAISE(ABORT,'immutable own home admission'); END;
CREATE TRIGGER own_home_admission_channel_immutable_update BEFORE UPDATE ON own_home_admission_channel
BEGIN SELECT RAISE(ABORT,'immutable own home admission channel'); END;
CREATE TRIGGER own_home_admission_channel_immutable_delete BEFORE DELETE ON own_home_admission_channel
BEGIN SELECT RAISE(ABORT,'immutable own home admission channel'); END;
CREATE TRIGGER own_home_admission_restart_immutable_update BEFORE UPDATE ON own_home_admission_restart
BEGIN SELECT RAISE(ABORT,'immutable own home admission restart'); END;
CREATE TRIGGER own_home_admission_restart_immutable_delete BEFORE DELETE ON own_home_admission_restart
BEGIN SELECT RAISE(ABORT,'immutable own home admission restart'); END;
"""
_SCHEMA_V10 = _SCHEMA_V9 + _UPGRADE_V10_SCHEMA
_UPGRADE_V11_SCHEMA = """
CREATE TABLE remote_image_upload(
 target_id TEXT NOT NULL CHECK(typeof(target_id)='text' AND length(target_id)=64
   AND target_id NOT GLOB '*[^0-9a-f]*'),
 source_id TEXT NOT NULL CHECK(typeof(source_id)='text' AND length(source_id)=64
   AND source_id NOT GLOB '*[^0-9a-f]*'),
 ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal BETWEEN 0 AND 3),
 revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
 scope_hash TEXT NOT NULL CHECK(typeof(scope_hash)='text' AND length(scope_hash)=64
   AND scope_hash NOT GLOB '*[^0-9a-f]*'),
 source_at INTEGER NOT NULL CHECK(typeof(source_at)='integer' AND source_at>=0),
 content_hash TEXT NOT NULL CHECK(typeof(content_hash)='text' AND length(content_hash)=64
   AND content_hash NOT GLOB '*[^0-9a-f]*'),
 mime_type TEXT NOT NULL CHECK(typeof(mime_type)='text' AND mime_type IN ('image/png','image/jpeg')),
 byte_size INTEGER NOT NULL CHECK(typeof(byte_size)='integer' AND byte_size BETWEEN 1 AND 10000000),
 state TEXT NOT NULL CHECK(typeof(state)='text' AND state IN ('unknown','acked')),
 image_key TEXT NOT NULL DEFAULT '' CHECK(typeof(image_key)='text' AND length(image_key)<=256
   AND image_key NOT GLOB '*[^ -~]*'),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 PRIMARY KEY(target_id,source_id,ordinal),
 FOREIGN KEY(target_id,revision) REFERENCES remote_grant(target_id,revision),
 CHECK((state='acked' AND image_key!='') OR state='unknown'));
CREATE TRIGGER remote_image_upload_insert_witness BEFORE INSERT ON remote_image_upload
WHEN hostd_remote_image_witness('reserve',NEW.target_id,NEW.source_id,NEW.ordinal)!=1
 OR NOT EXISTS(SELECT 1 FROM remote_target t JOIN agent a ON a.pubkey=t.agent_id
   JOIN remote_grant g ON g.target_id=t.target_id AND g.revision=t.current_revision
   WHERE t.target_id=NEW.target_id AND t.status='active' AND a.status='active'
   AND t.current_revision=NEW.revision AND g.scope_hash=NEW.scope_hash AND (g.capabilities & 1)!=0)
BEGIN SELECT RAISE(ABORT,'remote image reservation lacks current Store witness'); END;
CREATE TRIGGER remote_image_upload_update_witness BEFORE UPDATE ON remote_image_upload
WHEN NOT (
 OLD.target_id=NEW.target_id AND OLD.source_id=NEW.source_id AND OLD.ordinal=NEW.ordinal
 AND OLD.revision=NEW.revision AND OLD.scope_hash=NEW.scope_hash AND OLD.source_at=NEW.source_at
 AND OLD.content_hash=NEW.content_hash AND OLD.mime_type=NEW.mime_type
 AND OLD.byte_size=NEW.byte_size AND OLD.created_at=NEW.created_at
 AND NEW.updated_at>=OLD.updated_at
 AND ((OLD.state='unknown' AND NEW.state='unknown' AND OLD.image_key=''
       AND NEW.image_key!=''
       AND hostd_remote_image_witness('pin',NEW.target_id,NEW.source_id,NEW.ordinal)=1)
   OR (OLD.state='unknown' AND NEW.state='acked' AND OLD.image_key!=''
       AND NEW.image_key=OLD.image_key
       AND hostd_remote_image_witness('ack',NEW.target_id,NEW.source_id,NEW.ordinal)=1))
 AND EXISTS(SELECT 1 FROM remote_target t JOIN agent a ON a.pubkey=t.agent_id
   JOIN remote_grant g ON g.target_id=t.target_id AND g.revision=t.current_revision
   WHERE t.target_id=NEW.target_id AND t.status='active' AND a.status='active'
   AND t.current_revision=NEW.revision AND g.scope_hash=NEW.scope_hash AND (g.capabilities & 1)!=0)
)
BEGIN SELECT RAISE(ABORT,'immutable remote image journal transition'); END;
CREATE TRIGGER remote_image_upload_immutable_delete BEFORE DELETE ON remote_image_upload
BEGIN SELECT RAISE(ABORT,'immutable remote image journal row'); END;
"""
_SCHEMA_V11 = _SCHEMA_V10 + _UPGRADE_V11_SCHEMA
_UPGRADE_V12_SCHEMA = """
CREATE TABLE delivery_notice(
 binding_id TEXT NOT NULL, source_id TEXT NOT NULL CHECK(typeof(source_id)='text' AND length(source_id) BETWEEN 1 AND 256 AND source_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 source_author_pubkey TEXT NOT NULL CHECK(typeof(source_author_pubkey)='text' AND length(source_author_pubkey)=64 AND source_author_pubkey NOT GLOB '*[^0-9a-f]*'),
 source_app_id TEXT NOT NULL CHECK(typeof(source_app_id)='text' AND length(source_app_id) BETWEEN 1 AND 256 AND source_app_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 source_created_at INTEGER NOT NULL CHECK(typeof(source_created_at)='integer' AND source_created_at>=0),
 sync_app_id TEXT NOT NULL CHECK(typeof(sync_app_id)='text' AND length(sync_app_id) BETWEEN 1 AND 256 AND sync_app_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 channel_id TEXT NOT NULL CHECK(typeof(channel_id)='text' AND length(channel_id) BETWEEN 1 AND 256 AND channel_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 chat_id TEXT NOT NULL CHECK(typeof(chat_id)='text' AND length(chat_id) BETWEEN 1 AND 256 AND chat_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 root_message_id TEXT NOT NULL CHECK(typeof(root_message_id)='text' AND length(root_message_id) BETWEEN 1 AND 256 AND root_message_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 first_observed_at INTEGER NOT NULL CHECK(typeof(first_observed_at)='integer' AND first_observed_at>=0),
 delay_seconds INTEGER NOT NULL CHECK(typeof(delay_seconds)='integer' AND delay_seconds BETWEEN 1 AND 86400),
 deadline_at INTEGER NOT NULL CHECK(typeof(deadline_at)='integer' AND deadline_at>=0),
 notice_uuid TEXT NOT NULL UNIQUE CHECK(typeof(notice_uuid)='text' AND length(notice_uuid) BETWEEN 1 AND 256 AND notice_uuid NOT GLOB '*[^A-Za-z0-9_.:-]*'),
 notice_version INTEGER NOT NULL CHECK(typeof(notice_version)='integer' AND notice_version BETWEEN 1 AND 2147483647),
 notice_content_sha256 TEXT NOT NULL CHECK(typeof(notice_content_sha256)='text' AND length(notice_content_sha256)=64 AND notice_content_sha256 NOT GLOB '*[^0-9a-f]*'),
 state TEXT NOT NULL CHECK(typeof(state)='text' AND state IN ('waiting','resolved_without_notice','reserved','unknown','noticed','recovery_reserved','recovery_unknown','recovered')),
 resolved_without_notice_at INTEGER CHECK(resolved_without_notice_at IS NULL OR (typeof(resolved_without_notice_at)='integer' AND resolved_without_notice_at>=0)),
 notice_message_id TEXT CHECK(notice_message_id IS NULL OR (typeof(notice_message_id)='text' AND length(notice_message_id) BETWEEN 1 AND 256 AND notice_message_id NOT GLOB '*[^A-Za-z0-9_.:-]*')),
 recovery_version INTEGER CHECK(recovery_version IS NULL OR (typeof(recovery_version)='integer' AND recovery_version BETWEEN 1 AND 2147483647)),
 recovery_content_sha256 TEXT CHECK(recovery_content_sha256 IS NULL OR (typeof(recovery_content_sha256)='text' AND length(recovery_content_sha256)=64 AND recovery_content_sha256 NOT GLOB '*[^0-9a-f]*')),
 created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
 updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
 PRIMARY KEY(binding_id,source_id),
 FOREIGN KEY(binding_id) REFERENCES binding(binding_id),
 CHECK(deadline_at=first_observed_at+delay_seconds AND created_at=first_observed_at),
 CHECK((state='waiting' AND resolved_without_notice_at IS NULL AND notice_message_id IS NULL AND recovery_version IS NULL AND recovery_content_sha256 IS NULL) OR
       (state='resolved_without_notice' AND resolved_without_notice_at IS NOT NULL AND notice_message_id IS NULL AND recovery_version IS NULL AND recovery_content_sha256 IS NULL) OR
       (state IN ('reserved','unknown') AND resolved_without_notice_at IS NULL AND notice_message_id IS NULL AND recovery_version IS NULL AND recovery_content_sha256 IS NULL) OR
       (state='noticed' AND resolved_without_notice_at IS NULL AND notice_message_id IS NOT NULL AND recovery_version IS NULL AND recovery_content_sha256 IS NULL) OR
       (state IN ('recovery_reserved','recovery_unknown','recovered') AND resolved_without_notice_at IS NULL AND notice_message_id IS NOT NULL AND recovery_version IS NOT NULL AND recovery_content_sha256 IS NOT NULL))
);
CREATE INDEX delivery_notice_due ON delivery_notice(state,deadline_at,binding_id,source_id);
CREATE TRIGGER delivery_notice_insert_guard BEFORE INSERT ON delivery_notice
WHEN hostd_delivery_notice_witness('observe',NEW.binding_id,NEW.source_id)!=1
 OR NOT EXISTS(SELECT 1 FROM binding b WHERE b.binding_id=NEW.binding_id AND b.status='active'
   AND b.sync_app_id=NEW.sync_app_id AND b.channel_id=NEW.channel_id AND b.chat_id=NEW.chat_id)
BEGIN SELECT RAISE(ABORT,'delivery notice insert lacks Store witness'); END;
CREATE TRIGGER delivery_notice_update_guard BEFORE UPDATE ON delivery_notice
WHEN NOT (
 hostd_delivery_notice_witness('transition',NEW.binding_id,NEW.source_id)=1
 AND OLD.binding_id=NEW.binding_id AND OLD.source_id=NEW.source_id
 AND OLD.source_author_pubkey=NEW.source_author_pubkey AND OLD.source_app_id=NEW.source_app_id
 AND OLD.source_created_at=NEW.source_created_at AND OLD.sync_app_id=NEW.sync_app_id
 AND OLD.channel_id=NEW.channel_id AND OLD.chat_id=NEW.chat_id AND OLD.root_message_id=NEW.root_message_id
 AND OLD.first_observed_at=NEW.first_observed_at AND OLD.delay_seconds=NEW.delay_seconds
 AND OLD.deadline_at=NEW.deadline_at AND OLD.notice_uuid=NEW.notice_uuid
 AND OLD.notice_version=NEW.notice_version AND OLD.notice_content_sha256=NEW.notice_content_sha256
 AND OLD.created_at=NEW.created_at AND NEW.updated_at>=OLD.updated_at
 AND NOT (OLD.notice_message_id IS NOT NULL AND OLD.notice_message_id!=NEW.notice_message_id)
 AND NOT (OLD.recovery_version IS NOT NULL AND (OLD.recovery_version!=NEW.recovery_version OR OLD.recovery_content_sha256!=NEW.recovery_content_sha256))
 AND ((OLD.state='waiting' AND NEW.state='reserved' AND NEW.resolved_without_notice_at IS NULL AND NEW.notice_message_id IS NULL AND NEW.recovery_version IS NULL AND NEW.recovery_content_sha256 IS NULL)
   OR (OLD.state='waiting' AND NEW.state='resolved_without_notice' AND NEW.resolved_without_notice_at IS NOT NULL AND NEW.notice_message_id IS NULL AND NEW.recovery_version IS NULL AND NEW.recovery_content_sha256 IS NULL)
   OR (OLD.state='reserved' AND NEW.state='unknown' AND NEW.resolved_without_notice_at IS NULL AND NEW.notice_message_id IS NULL AND NEW.recovery_version IS NULL AND NEW.recovery_content_sha256 IS NULL)
   OR (OLD.state IN ('reserved','unknown') AND NEW.state='noticed' AND NEW.notice_message_id IS NOT NULL AND NEW.resolved_without_notice_at IS NULL AND NEW.recovery_version IS NULL AND NEW.recovery_content_sha256 IS NULL)
   OR (OLD.state='noticed' AND NEW.state='recovery_reserved' AND NEW.notice_message_id=OLD.notice_message_id AND NEW.recovery_version IS NOT NULL AND NEW.recovery_content_sha256 IS NOT NULL)
   OR (OLD.state='recovery_reserved' AND NEW.state='recovery_unknown' AND NEW.notice_message_id=OLD.notice_message_id AND NEW.recovery_version=OLD.recovery_version AND NEW.recovery_content_sha256=OLD.recovery_content_sha256)
   OR (OLD.state IN ('recovery_reserved','recovery_unknown') AND NEW.state='recovered' AND NEW.notice_message_id=OLD.notice_message_id AND NEW.recovery_version=OLD.recovery_version AND NEW.recovery_content_sha256=OLD.recovery_content_sha256))
)
BEGIN SELECT RAISE(ABORT,'invalid delivery notice transition'); END;
CREATE TRIGGER delivery_notice_delete_guard BEFORE DELETE ON delivery_notice
BEGIN SELECT RAISE(ABORT,'immutable delivery notice row'); END;
"""
_SCHEMA_V12 = _SCHEMA_V11 + _UPGRADE_V12_SCHEMA
_UPGRADE_V13_SCHEMA = """
CREATE TABLE notice_hint(
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 source_id TEXT NOT NULL CHECK(length(source_id)=64 AND source_id NOT GLOB '*[^0-9a-f]*'),
 queued_at INTEGER NOT NULL CHECK(queued_at>=0),
 attempted_at INTEGER NOT NULL DEFAULT 0 CHECK(attempted_at>=0),
 PRIMARY KEY(binding_id,source_id)
);
CREATE INDEX notice_hint_pending ON notice_hint(binding_id,attempted_at,queued_at,source_id);
"""
_SCHEMA_V13 = _SCHEMA_V12 + _UPGRADE_V13_SCHEMA
_UPGRADE_V14_SCHEMA = """
CREATE TABLE reaction_inbox(
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 agent_id TEXT NOT NULL CHECK(length(agent_id)=64 AND agent_id NOT GLOB '*[^0-9a-f]*'),
 source_id TEXT NOT NULL CHECK(length(source_id)=64 AND source_id NOT GLOB '*[^0-9a-f]*'),
 channel_id TEXT NOT NULL CHECK(length(channel_id) BETWEEN 1 AND 256),
 event_json TEXT NOT NULL CHECK(length(CAST(event_json AS BLOB)) BETWEEN 1 AND 2048),
 captured_floor INTEGER NOT NULL CHECK(captured_floor>=0),
 PRIMARY KEY(binding_id,agent_id,source_id)
);
CREATE TRIGGER reaction_inbox_immutable BEFORE UPDATE ON reaction_inbox
BEGIN SELECT RAISE(ABORT,'immutable reaction inbox'); END;
CREATE TRIGGER reaction_inbox_capacity BEFORE INSERT ON reaction_inbox
WHEN NOT EXISTS(SELECT 1 FROM reaction_inbox WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id AND source_id=NEW.source_id)
 AND ((SELECT count(*) FROM reaction_inbox)>=8192
 OR (SELECT count(*) FROM reaction_inbox WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id)>=256)
BEGIN SELECT RAISE(ABORT,'reaction inbox capacity'); END;
CREATE TABLE reaction_foreign(
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 agent_id TEXT NOT NULL CHECK(length(agent_id)=64 AND agent_id NOT GLOB '*[^0-9a-f]*'),
 source_id TEXT NOT NULL CHECK(length(source_id)=64 AND source_id NOT GLOB '*[^0-9a-f]*'),
 channel_id TEXT NOT NULL CHECK(length(channel_id) BETWEEN 1 AND 256),
 target_id TEXT NOT NULL CHECK(length(target_id)=64 AND target_id NOT GLOB '*[^0-9a-f]*'),
 foreign_channel TEXT NOT NULL CHECK(length(foreign_channel) BETWEEN 1 AND 256 AND foreign_channel!=channel_id),
 observed_at INTEGER NOT NULL CHECK(observed_at>=0),
 PRIMARY KEY(binding_id,agent_id,source_id)
);
CREATE TRIGGER reaction_foreign_immutable BEFORE UPDATE ON reaction_foreign
BEGIN SELECT RAISE(ABORT,'immutable foreign reaction proof'); END;
CREATE TRIGGER reaction_foreign_capacity BEFORE INSERT ON reaction_foreign
WHEN NOT EXISTS(SELECT 1 FROM reaction_foreign WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id AND source_id=NEW.source_id)
 AND ((SELECT count(*) FROM reaction_foreign)>=8192
 OR (SELECT count(*) FROM reaction_foreign WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id)>=256)
BEGIN SELECT RAISE(ABORT,'foreign reaction proof capacity'); END;
"""
_SCHEMA_V14 = _SCHEMA_V13 + _UPGRADE_V14_SCHEMA
_UPGRADE_V15_SCHEMA = """
CREATE TABLE console_pause(
 binding_id TEXT PRIMARY KEY NOT NULL REFERENCES binding(binding_id),
 operation_id TEXT NOT NULL UNIQUE REFERENCES console_operation(id),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64 AND scope_hash NOT GLOB '*[^0-9a-f]*'),
 root_pid INTEGER NOT NULL CHECK(root_pid>0),root_start INTEGER NOT NULL CHECK(root_start>0),
 root_nonce TEXT NOT NULL CHECK(length(root_nonce)=64 AND root_nonce NOT GLOB '*[^0-9a-f]*'),
 created_at INTEGER NOT NULL CHECK(created_at>=0),
 state TEXT NOT NULL CHECK(state IN ('stopping','drained')),
 receipt_hash TEXT CHECK(receipt_hash IS NULL OR (length(receipt_hash)=64 AND receipt_hash NOT GLOB '*[^0-9a-f]*')),
 CHECK((state='stopping' AND receipt_hash IS NULL) OR (state='drained' AND receipt_hash IS NOT NULL))
);
CREATE TRIGGER console_pause_update_guard BEFORE UPDATE ON console_pause
WHEN NOT (OLD.binding_id=NEW.binding_id AND OLD.operation_id=NEW.operation_id
 AND OLD.scope_hash=NEW.scope_hash AND OLD.root_pid=NEW.root_pid AND OLD.root_start=NEW.root_start
 AND OLD.root_nonce=NEW.root_nonce AND OLD.created_at=NEW.created_at
 AND OLD.state='stopping' AND NEW.state='drained' AND NEW.receipt_hash IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'invalid pause drain transition'); END;
"""
_SCHEMA_V15 = _SCHEMA_V14 + _UPGRADE_V15_SCHEMA
_UPGRADE_V16_SCHEMA = """
CREATE TABLE outlet_pending(
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 agent_id TEXT NOT NULL REFERENCES agent(pubkey),
 source_id TEXT NOT NULL CHECK(length(source_id)=64 AND source_id NOT GLOB '*[^0-9a-f]*'),
 channel_id TEXT NOT NULL CHECK(length(channel_id) BETWEEN 1 AND 256),
 kind INTEGER NOT NULL CHECK(typeof(kind)='integer' AND kind IN (9,40003,7,5)),
 source_at INTEGER NOT NULL CHECK(typeof(source_at)='integer' AND source_at>=0),
 captured_floor INTEGER NOT NULL CHECK(typeof(captured_floor)='integer' AND captured_floor>=0),
 PRIMARY KEY(binding_id,agent_id,source_id)
);
CREATE TRIGGER outlet_pending_immutable BEFORE UPDATE ON outlet_pending
BEGIN SELECT RAISE(ABORT,'immutable pending outlet source'); END;
CREATE TRIGGER outlet_pending_capacity BEFORE INSERT ON outlet_pending
WHEN NOT EXISTS(SELECT 1 FROM outlet_pending WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id AND source_id=NEW.source_id)
 AND ((SELECT count(*) FROM outlet_pending)>=8192
 OR (SELECT count(*) FROM outlet_pending WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id)>=256)
BEGIN SELECT RAISE(ABORT,'pending outlet capacity'); END;
"""
_SCHEMA_V16 = _SCHEMA_V15 + _UPGRADE_V16_SCHEMA
_UPGRADE_V17_SCHEMA = """
CREATE TABLE outlet_work(
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 agent_id TEXT NOT NULL CHECK(length(agent_id)=64 AND agent_id NOT GLOB '*[^0-9a-f]*'),
 source_id TEXT NOT NULL CHECK(length(source_id)=64 AND source_id NOT GLOB '*[^0-9a-f]*'),
 channel_id TEXT NOT NULL CHECK(length(channel_id) BETWEEN 1 AND 256),
 kind INTEGER NOT NULL CHECK(typeof(kind)='integer' AND kind IN (9,40003,7,5)),
 source_at INTEGER NOT NULL CHECK(typeof(source_at)='integer' AND source_at>=0),
 captured_floor INTEGER NOT NULL CHECK(typeof(captured_floor)='integer' AND captured_floor>=0),
 parent_id TEXT NOT NULL CHECK(parent_id='' OR (length(parent_id)=64 AND parent_id NOT GLOB '*[^0-9a-f]*')),
 attempts INTEGER NOT NULL CHECK(typeof(attempts)='integer' AND attempts>=0),
 retry_at INTEGER NOT NULL CHECK(typeof(retry_at)='integer' AND retry_at>=0),
 PRIMARY KEY(binding_id,agent_id,source_id)
);
CREATE TRIGGER outlet_work_identity BEFORE UPDATE ON outlet_work
WHEN NOT (NEW.binding_id=OLD.binding_id AND NEW.agent_id=OLD.agent_id AND NEW.source_id=OLD.source_id
 AND NEW.channel_id=OLD.channel_id AND NEW.kind=OLD.kind AND NEW.source_at=OLD.source_at
 AND NEW.captured_floor=OLD.captured_floor AND NEW.parent_id=OLD.parent_id
 AND NEW.attempts=OLD.attempts+1 AND NEW.retry_at>=OLD.retry_at)
BEGIN SELECT RAISE(ABORT,'invalid outlet work transition'); END;
CREATE TRIGGER outlet_work_capacity BEFORE INSERT ON outlet_work
WHEN NOT EXISTS(SELECT 1 FROM outlet_work WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id AND source_id=NEW.source_id)
 AND ((SELECT count(*) FROM outlet_work)>=65536
 OR (SELECT count(*) FROM outlet_work WHERE binding_id=NEW.binding_id AND agent_id=NEW.agent_id)>=10000)
BEGIN SELECT RAISE(ABORT,'outlet work capacity'); END;
CREATE TABLE outlet_work_turn(
 binding_id TEXT NOT NULL REFERENCES binding(binding_id),
 agent_id TEXT NOT NULL CHECK(length(agent_id)=64 AND agent_id NOT GLOB '*[^0-9a-f]*'),
 fresh_turns INTEGER NOT NULL CHECK(typeof(fresh_turns)='integer' AND fresh_turns BETWEEN 0 AND 7),
 PRIMARY KEY(binding_id,agent_id)
);
"""
_SCHEMA_V17 = _SCHEMA_V16 + _UPGRADE_V17_SCHEMA
_UPGRADE_V18_SCHEMA = """
CREATE TABLE feishu_scan(
 binding_id TEXT PRIMARY KEY REFERENCES binding(binding_id),
 scope_hash TEXT NOT NULL CHECK(length(scope_hash)=64),
 start_at INTEGER NOT NULL CHECK(start_at>=0), end_at INTEGER NOT NULL CHECK(end_at>=start_at),
 next_token TEXT NOT NULL CHECK(length(next_token)<=4096),
 complete INTEGER NOT NULL CHECK(complete IN (0,1))
);
CREATE TABLE feishu_scan_token(
 binding_id TEXT NOT NULL REFERENCES feishu_scan(binding_id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL CHECK(length(token_hash)=64), PRIMARY KEY(binding_id,token_hash)
);
CREATE TABLE feishu_ingest(
 binding_id TEXT NOT NULL REFERENCES binding(binding_id), chat_id TEXT NOT NULL,
 message_id TEXT NOT NULL, source_ms INTEGER NOT NULL CHECK(source_ms>=0),
 captured_floor INTEGER NOT NULL CHECK(captured_floor>=0),
 capture_seq INTEGER NOT NULL DEFAULT 0 CHECK(capture_seq>=0),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 retry_at INTEGER NOT NULL DEFAULT 0 CHECK(retry_at>=0),
 done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
 waiting_context INTEGER NOT NULL DEFAULT 0 CHECK(waiting_context IN (0,1)),
 PRIMARY KEY(binding_id,message_id)
);
CREATE INDEX feishu_ingest_order ON feishu_ingest(binding_id,capture_seq);
CREATE TRIGGER feishu_ingest_capacity BEFORE INSERT ON feishu_ingest
WHEN NOT EXISTS(SELECT 1 FROM feishu_ingest WHERE binding_id=NEW.binding_id AND message_id=NEW.message_id)
 AND ((SELECT count(*) FROM feishu_ingest)>=65536
 OR (SELECT count(*) FROM feishu_ingest WHERE binding_id=NEW.binding_id AND done=0)>=10000)
BEGIN SELECT RAISE(ABORT,'feishu ingest capacity'); END;
"""
_SCHEMA_V18 = _SCHEMA_V17 + _UPGRADE_V18_SCHEMA
_UPGRADE_V19_SCHEMA = """
CREATE TABLE join_adoption(
 request_id TEXT PRIMARY KEY REFERENCES join_request(request_id),
 original_request TEXT NOT NULL, original_plan TEXT NOT NULL, target_binding TEXT NOT NULL,
 decision_hash TEXT NOT NULL CHECK(length(decision_hash)=64),
 proof_hash TEXT NOT NULL CHECK(length(proof_hash)=64), proof_refs TEXT NOT NULL,
 created_at INTEGER NOT NULL CHECK(created_at>=0)
);
CREATE TRIGGER join_adoption_immutable_insert BEFORE INSERT ON join_adoption
WHEN EXISTS(SELECT 1 FROM join_adoption WHERE request_id=NEW.request_id)
BEGIN SELECT RAISE(ABORT,'immutable join adoption'); END;
CREATE TRIGGER join_adoption_immutable_update BEFORE UPDATE ON join_adoption
BEGIN SELECT RAISE(ABORT,'immutable join adoption'); END;
CREATE TRIGGER join_adoption_immutable_delete BEFORE DELETE ON join_adoption
BEGIN SELECT RAISE(ABORT,'immutable join adoption'); END;
"""
_SCHEMA = _SCHEMA_V18 + _UPGRADE_V19_SCHEMA
CONSOLE_REASONS = frozenset(('cancellation','timeout','recovered','scope','proof','missing','busy','driver'))
CONSOLE_OUTCOMES = {'pause': 'paused', 'resume': 'active', 'backfill': 'backfilled', 'restart': 'restarted'}
EFFECT_STEPS = frozenset(('channel','mirror','members','claim','config','agent_env','agent_prompt','agent_responsible','runtime','registrar','cleanup'))


def _schema_statements(schema):
    """Split complete SQLite statements, including trigger bodies with semicolons."""
    pending = ""
    for line in schema.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                yield pending.strip()
            pending = ""
    if pending.strip():
        raise StoreError(ERROR)


def _schema_sql(schema):
    return {" ".join(sql.rstrip().rstrip(';').split()).lower() for sql in _schema_statements(schema)}


def _directory(path: Path):
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o700, dir_fd=directory)
                except FileExistsError:
                    pass  # another owner-controlled constructor created it; nofollow still applies
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            os.close(directory)
            directory = child
        meta = os.fstat(directory)
        if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise StoreError(ERROR)
        return directory
    except Exception:
        os.close(directory)
        raise StoreError(ERROR) from None


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(localpath(path))
        self._lock = threading.RLock()
        self._seq = 0
        self._rollback_frames = []
        self._restart_provenance = {}  # transient creation/dispatch witnesses for one original outer transaction
        self._own_home_write_depth = 0
        self._remote_image_write_witness = None
        self._delivery_notice_write_witness = None
        self._fallback_live_reservations = {}  # one original live reservation; never reconstructed after reopen
        self.conn = None
        self.dir_fd = None
        try:
            self.dir_fd = _directory(self.path.parent)
            with _CREATION_LOCK:
                try:
                    os.stat(self.path.name, dir_fd=self.dir_fd, follow_symlinks=False)
                except FileNotFoundError:
                    # Closing any descriptor for an existing SQLite inode drops this
                    # process's POSIX locks, including other connections' WAL-era DB
                    # shared locks. Only open a genuinely new inode outside SQLite.
                    try:
                        fd = os.open(self.path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL |
                                     os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                     0o600, dir_fd=self.dir_fd)
                    except FileExistsError:
                        pass  # concurrent creator won; metadata admission below remains mandatory
                    else:
                        os.close(fd)
            self._check_files()
            self.conn = sqlite3.connect(f"/proc/self/fd/{self.dir_fd}/{self.path.name}", timeout=10,
                                        isolation_level=None, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.create_function('hostd_own_home_writer', 0,
                                      lambda: int(self._own_home_write_depth > 0))
            self.conn.create_function('hostd_own_home_restart_witness', 2,
                                      self._own_home_sql_restart_witness)
            self.conn.create_function('hostd_remote_image_witness', 4,
                                      self._remote_image_sql_witness)
            self.conn.create_function('hostd_delivery_notice_witness', 3,
                                      self._delivery_notice_sql_witness)
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA busy_timeout=10000")
            initial_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if initial_version not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, SCHEMA_VERSION):
                raise StoreError(ERROR)
            self.conn.execute("PRAGMA journal_mode=WAL")
            # Current-schema admission only reads a consistent WAL snapshot.
            # Creation/migration and all business writes still reserve the
            # writer lock; readers must not queue behind unrelated bindings.
            with self.transaction(immediate=initial_version != SCHEMA_VERSION):
                version = self.conn.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    if self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                        raise StoreError(ERROR)
                    for sql in _schema_statements(_SCHEMA):
                        self.conn.execute(sql)
                    self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18):
                    migrations = ((1, _SCHEMA_V1, _JOIN_TRANSPORT_SCHEMA),
                                  (2, _SCHEMA_V2, _UPGRADE_V3_SCHEMA),
                                  (3, _SCHEMA_V3, _UPGRADE_V4_SCHEMA),
                                  (4, _SCHEMA_V4, _UPGRADE_V5_SCHEMA),
                                  (5, _SCHEMA_V5, _UPGRADE_V6_SCHEMA),
                                  (6, _SCHEMA_V6, _UPGRADE_V7_SCHEMA),
                                  (7, _SCHEMA_V7, _UPGRADE_V8_SCHEMA),
                                  (8, _SCHEMA_V8, _UPGRADE_V9_SCHEMA),
                                  (9, _SCHEMA_V9, _UPGRADE_V10_SCHEMA),
                                  (10, _SCHEMA_V10, _UPGRADE_V11_SCHEMA),
                                  (11, _SCHEMA_V11, _UPGRADE_V12_SCHEMA),
                                  (12, _SCHEMA_V12, _UPGRADE_V13_SCHEMA),
                                  (13, _SCHEMA_V13, _UPGRADE_V14_SCHEMA),
                                  (14, _SCHEMA_V14, _UPGRADE_V15_SCHEMA),
                                  (15, _SCHEMA_V15, _UPGRADE_V16_SCHEMA),
                                  (16, _SCHEMA_V16, _UPGRADE_V17_SCHEMA),
                                  (17, _SCHEMA_V17, _UPGRADE_V18_SCHEMA),
                                  (18, _SCHEMA_V18, _UPGRADE_V19_SCHEMA))
                    while version < SCHEMA_VERSION:
                        migration = next((item for item in migrations if item[0] == version), None)
                        if migration is None:
                            raise StoreError(ERROR)
                        _, frozen_schema, addition = migration
                        actual = {" ".join(row[0].split()).lower() for row in self.conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")}
                        if actual != _schema_sql(frozen_schema) or self.conn.execute("PRAGMA foreign_key_check").fetchone() or self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            raise StoreError(ERROR)
                        for sql in _schema_statements(addition):
                            self.conn.execute(sql)
                        version += 1
                        self.conn.execute(f"PRAGMA user_version={version}")
                elif version != SCHEMA_VERSION:
                    raise StoreError(ERROR)
                expected_schema = _schema_sql(_SCHEMA)
                actual_schema = {" ".join(row[0].split()).lower() for row in self.conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")}
                if actual_schema != expected_schema or self.conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise StoreError(ERROR)
                if self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise StoreError(ERROR)
            self._check_files()
        except Exception:
            self.close()
            raise StoreError(ERROR) from None

    def _check_files(self):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                meta = os.stat(self.path.name + suffix, dir_fd=self.dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                raise StoreError(ERROR) from None
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600:
                raise StoreError(ERROR)

    @contextlib.contextmanager
    def transaction(self, *, immediate=True):
        with self._lock:
            self._check_files()
            nested = self.conn.in_transaction
            self._seq += 1
            savepoint = f"t{self._seq}"
            callbacks = []
            try:
                self.conn.execute(f"SAVEPOINT {savepoint}" if nested else
                                  "BEGIN IMMEDIATE" if immediate else "BEGIN")
                self._rollback_frames.append(callbacks)
                yield self
                self._check_files()
                self.conn.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
                if nested and len(self._rollback_frames) > 1:
                    self._rollback_frames[-2].extend(callbacks)
            except BaseException as exc:
                if self.conn.in_transaction:
                    self.conn.execute(f"ROLLBACK TO {savepoint}" if nested else "ROLLBACK")
                    if nested:
                        self.conn.execute(f"RELEASE {savepoint}")
                for callback in reversed(callbacks):
                    callback()
                if isinstance(exc, sqlite3.Error):
                    raise StoreError(ERROR) from None
                raise
            finally:
                if self._rollback_frames and self._rollback_frames[-1] is callbacks:
                    self._rollback_frames.pop()
                if not self._rollback_frames:
                    self._restart_provenance.clear()

    def on_rollback(self, callback):
        """Adapter revision tokens follow the enclosing SQLite transaction's rollback."""
        if not self._rollback_frames:
            raise StoreError(ERROR)
        self._rollback_frames[-1].append(callback)

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.dir_fd is not None:
            os.close(self.dir_fd)
            self.dir_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def reconcile_bindings(self, records, *, now):
        stamp(now)
        records = list(records)
        if not all(isinstance(r, BindingRecord) for r in records):
            raise StoreError(ERROR)
        if len({r.binding_id for r in records}) != len(records):
            raise StoreError(ERROR)
        with self.transaction():
            for record in records:
                ident(record.binding_id); ident(record.channel_id); ident(record.chat_id, "oc_"); ident(record.sync_app_id, "cli_")
                localpath(record.config_path); localpath(record.lark_config_dir); localpath(record.lark_data_dir)
                hexid(record.mirror_pubkey, empty=True); hexid(record.chat_ref, empty=True)
                profile = self.conn.execute("SELECT * FROM app_profile WHERE app_id=?", (record.sync_app_id,)).fetchone()
                if profile and (profile["config_dir"], profile["data_dir"]) != (record.lark_config_dir, record.lark_data_dir):
                    raise StoreError(ERROR)
                self.conn.execute("INSERT OR IGNORE INTO app_profile VALUES(?,?,?)",
                                  (record.sync_app_id, record.lark_config_dir, record.lark_data_dir))
                old = self.conn.execute("SELECT channel_id,chat_id FROM binding WHERE binding_id=?", (record.binding_id,)).fetchone()
                if old and tuple(old) != (record.channel_id, record.chat_id):
                    raise StoreError(ERROR)  # an existing ledger cannot silently move to another target
                self.conn.execute("""INSERT INTO binding VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(binding_id)
                  DO UPDATE SET sync_app_id=excluded.sync_app_id,config_path=excluded.config_path,
                  mirror_pubkey=excluded.mirror_pubkey,chat_ref=excluded.chat_ref,heartbeat_at=excluded.heartbeat_at""",
                                  (record.binding_id, record.channel_id, record.chat_id, record.sync_app_id, record.config_path,
                                   record.mirror_pubkey, record.chat_ref, record.status, now, now))

    def bindings(self):
        return [dict(row) for row in self.conn.execute("SELECT b.*,p.config_dir,p.data_dir FROM binding b JOIN app_profile p ON b.sync_app_id=p.app_id ORDER BY binding_id")]

    def register_agent(self, pubkey, *, owner_pubkey, app_id=None, config_path=None, now):
        hexid(pubkey); hexid(owner_pubkey); stamp(now)
        if app_id is not None:
            ident(app_id, "cli_")
        if config_path is not None:
            localpath(config_path)
        with self.transaction():
            old = self.conn.execute("SELECT owner_pubkey,app_id FROM agent WHERE pubkey=?", (pubkey,)).fetchone()
            if old and tuple(old) != (owner_pubkey, app_id):
                raise StoreError(ERROR)
            self.conn.execute("INSERT INTO agent VALUES(?,?,?,?,'active',?) ON CONFLICT(pubkey) DO UPDATE SET config_path=excluded.config_path,updated_at=excluded.updated_at",
                              (pubkey, owner_pubkey, app_id, config_path, now))

    @staticmethod
    def _remote_digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def _remote_scope(cls, evidence):
        # Replacement event IDs, heartbeat readbacks and their finite freshness
        # are proof of authority, not a new authority or a new delivery intent.
        transient = {"checked_at", "valid_until", "claim_event_id", "agent_profile_event_id",
                     "agent_policy_event_id", "roster_event_id"}
        return cls._remote_digest({k: v for k, v in asdict(evidence).items() if k not in transient})

    @staticmethod
    def _remote_evidence(evidence):
        if type(evidence) is not RemoteGrantEvidence:
            raise StoreError(ERROR)
        for name in ("agent_id", "owner_pubkey", "chat_ref", "mirror_pubkey", "mirror_owner_pubkey",
                     "claim_event_id", "agent_profile_event_id", "agent_policy_event_id", "roster_event_id",
                     "allowlist_hash", "approval_hash"):
            hexid(getattr(evidence, name))
        ident(evidence.app_id, "cli_"); ident(evidence.chat_id, "oc_")
        if (not isinstance(evidence.channel_id, str)
                or not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", evidence.channel_id)
                or evidence.chat_ref != hashlib.sha256(("buzz-feishu-chat:v1:" + evidence.chat_id).encode()).hexdigest()):
            raise StoreError(ERROR)
        if (type(evidence.capabilities) is not tuple or not evidence.capabilities
                or tuple(c for c in REMOTE_CAPABILITIES if c in evidence.capabilities) != evidence.capabilities):
            raise StoreError(ERROR)
        stamp(evidence.checked_at); stamp(evidence.valid_until); stamp(evidence.claimed_at)
        if evidence.valid_until <= evidence.checked_at or evidence.claimed_at > evidence.checked_at:
            raise StoreError(ERROR)
        if evidence.approval_kind == "local_card":
            if not isinstance(evidence.approval_id, str) or not re.fullmatch(r"JOIN-[0-9a-f]{8}", evidence.approval_id):
                raise StoreError(ERROR)
        elif evidence.approval_kind == "mirror_approval":
            hexid(evidence.approval_id)
        else:
            raise StoreError(ERROR)
        try:
            origin = evidence.relay_origin
            if not isinstance(origin, str) or len(origin) > 2048 or any(ord(c) < 33 for c in origin):
                raise ValueError
            parsed = urlsplit(origin)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment
                    or parsed.scheme not in ("https", "http")
                    or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
                    or not parsed.hostname.isascii() or parsed.netloc != parsed.netloc.lower()
                    or parsed.port == 0):
                raise ValueError
        except Exception:
            raise StoreError(ERROR) from None
        return evidence

    def _remote_approval(self, evidence):
        if evidence.approval_kind == "mirror_approval":
            # Internal trust seam only. No accepted public approval wire parser
            # exists here; caller discovery evidence is not sufficient authority.
            return True
        row = self.conn.execute("""SELECT j.*,p.channel_id,p.binding_id AS plan_binding,
            p.config_path AS plan_config,b.channel_id AS bound_channel,b.chat_id AS bound_chat,
            b.config_path AS bound_config,b.status AS bound_status,d.event_id
            FROM join_request j JOIN join_decision d ON d.request_id=j.request_id AND d.app_id=j.callback_app_id
            JOIN effect_plan p ON p.request_id=j.request_id
            JOIN binding b ON b.binding_id=p.binding_id AND b.binding_id=j.binding_id
            WHERE j.request_id=?""", (evidence.approval_id,)).fetchall()
        if len(row) != 1:
            return False
        row = row[0]
        return (row["status"] in ("approved", "applied", "done")
                and (row["agent_id"], row["owner_pubkey"], row["callback_app_id"], row["chat_id"])
                    == (evidence.agent_id, evidence.owner_pubkey, evidence.app_id, evidence.chat_id)
                and row["channel_id"] == row["bound_channel"] == evidence.channel_id
                and row["bound_chat"] == evidence.chat_id and row["bound_status"] == "active"
                and row["plan_config"] == row["bound_config"]
                and evidence.approval_hash == self._remote_digest([
                    evidence.approval_id, evidence.owner_pubkey, evidence.app_id, evidence.chat_id, row["event_id"]]))

    def remote_grant(self, target_id, revision=None):
        hexid(target_id)
        if revision is not None and stamp(revision) == 0:
            raise StoreError(ERROR)
        with self._lock:
            self._check_files()
            row = self.conn.execute("""SELECT t.*,g.*,g.created_at AS grant_created_at FROM remote_target t
                JOIN remote_grant g ON g.target_id=t.target_id AND g.revision=coalesce(?,t.current_revision)
                WHERE t.target_id=?""", (revision, target_id)).fetchone()
            if row is None:
                return None
            capabilities = tuple(c for i, c in enumerate(REMOTE_CAPABILITIES) if row["capabilities"] & (1 << i))
            evidence = RemoteGrantEvidence(**{name: row[name] for name in RemoteGrantEvidence.__dataclass_fields__
                                               if name != "capabilities"}, capabilities=capabilities)
            return RemoteGrantRecord(target_id, row["agent_id"], row["revision"], row["scope_hash"],
                                     capabilities, evidence, row["grant_created_at"])

    def _remote_current(self, target_id, revision, scope_hash, now):
        grant = self.remote_grant(target_id)
        row = self.conn.execute("""SELECT t.status,a.status AS agent_status,a.owner_pubkey,a.app_id,
            t.proof_checked_at,t.proof_valid_until FROM remote_target t JOIN agent a ON a.pubkey=t.agent_id
            WHERE t.target_id=?""", (target_id,)).fetchone()
        if (not grant or not row or row["status"] != "active" or row["agent_status"] != "active"
                or (grant.revision, grant.scope_hash) != (revision, scope_hash)
                or (row["owner_pubkey"], row["app_id"]) != (grant.evidence.owner_pubkey, grant.evidence.app_id)
                or not row["proof_checked_at"] <= now < row["proof_valid_until"]
                or not self._remote_approval(grant.evidence)):
            return None
        return grant

    def enqueue_notice_hints(self, binding_id, sources, *, now):
        """Untrusted IDs only. All-or-none bounded admission precedes replay cursor commits."""
        binding_id = self._delivery_notice_id(binding_id)
        sources = tuple(dict.fromkeys(hexid(source) for source in sources))
        if type(now) is not int or now < 0 or len(sources) > 4096:
            raise StoreError(ERROR)
        with self.transaction():
            existing = {row[0] for row in self.conn.execute(
                'SELECT source_id FROM notice_hint WHERE binding_id=?', (binding_id,))}
            if len(existing | set(sources)) > 4096:
                return False
            self.conn.executemany('INSERT OR IGNORE INTO notice_hint(binding_id,source_id,queued_at) VALUES(?,?,?)',
                                  [(binding_id, source, now) for source in sources])
        return True

    def pending_notice_hints(self, binding_id, *, limit=1):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise StoreError(ERROR)
        return [dict(row) for row in self.conn.execute(
            'SELECT * FROM notice_hint WHERE binding_id=? ORDER BY attempted_at,queued_at,source_id LIMIT ?',
            (self._delivery_notice_id(binding_id), limit))]

    def finish_notice_hint(self, binding_id, source_id, *, now, excluded=False):
        """Verified non-candidates or durable notices retire; incomplete reads rotate."""
        binding_id, source_id = self._delivery_notice_id(binding_id), hexid(source_id)
        if type(now) is not int or now < 0 or type(excluded) is not bool:
            raise StoreError(ERROR)
        with self.transaction():
            if excluded or self.conn.execute('SELECT 1 FROM delivery_notice WHERE binding_id=? AND source_id=?',
                                 (binding_id, source_id)).fetchone():
                self.conn.execute('DELETE FROM notice_hint WHERE binding_id=? AND source_id=?',
                                  (binding_id, source_id))
            else:
                self.conn.execute('UPDATE notice_hint SET attempted_at=max(attempted_at+1,?) WHERE binding_id=? AND source_id=?',
                                  (now, binding_id, source_id))

    def _delivery_notice_sql_witness(self, operation, binding_id, source_id):
        return int(self._delivery_notice_write_witness == (operation, binding_id, source_id))

    @contextlib.contextmanager
    def _delivery_notice_witness(self, operation, binding_id, source_id):
        if self._delivery_notice_write_witness is not None or operation not in ('observe', 'transition'):
            raise StoreError(ERROR)
        self._delivery_notice_write_witness = (operation, binding_id, source_id)
        try:
            yield
        finally:
            self._delivery_notice_write_witness = None

    def _delivery_notice_open(self):
        if self.conn is None or self.dir_fd is None:
            raise StoreError(ERROR)
        self._check_files()

    @staticmethod
    def _delivery_notice_record(row):
        return DeliveryNoticeRecord(**dict(row)) if row is not None else None

    @staticmethod
    def _delivery_notice_id(value):
        return ident(value)

    @staticmethod
    def _delivery_notice_version(value):
        if type(value) is not int or not 1 <= value <= 2_147_483_647:
            raise StoreError(ERROR)
        return value

    @staticmethod
    def _delivery_notice_states(values):
        allowed = {'waiting', 'resolved_without_notice', 'reserved', 'unknown', 'noticed',
                   'recovery_reserved', 'recovery_unknown', 'recovered'}
        if (type(values) is not tuple or not values or len(values) > len(allowed)
                or any(type(state) is not str or state not in allowed for state in values)
                or len(set(values)) != len(values)):
            raise StoreError(ERROR)
        return values

    def observe_delivery_notice(self, binding_id, source_id, *, source_author_pubkey, source_app_id,
                                source_created_at, sync_app_id, channel_id, chat_id, root_message_id,
                                first_observed_at, delay_seconds, deadline_at, notice_uuid,
                                notice_version, notice_content_sha256):
        binding_id = self._delivery_notice_id(binding_id)
        source_id = self._delivery_notice_id(source_id)
        source_author_pubkey = hexid(source_author_pubkey)
        source_app_id = self._delivery_notice_id(source_app_id)
        source_created_at = stamp(source_created_at)
        sync_app_id = self._delivery_notice_id(sync_app_id)
        channel_id = self._delivery_notice_id(channel_id)
        chat_id = self._delivery_notice_id(chat_id)
        root_message_id = self._delivery_notice_id(root_message_id)
        first_observed_at = stamp(first_observed_at)
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 86400:
            raise StoreError(ERROR)
        deadline_at = stamp(deadline_at)
        if first_observed_at + delay_seconds >= 2 ** 63 or deadline_at != first_observed_at + delay_seconds:
            raise StoreError(ERROR)
        notice_uuid = self._delivery_notice_id(notice_uuid)
        notice_version = self._delivery_notice_version(notice_version)
        notice_content_sha256 = hexid(notice_content_sha256)
        immutable = (binding_id, source_id, source_author_pubkey, source_app_id, source_created_at,
                     sync_app_id, channel_id, chat_id, root_message_id, first_observed_at,
                     delay_seconds, deadline_at, notice_uuid, notice_version, notice_content_sha256)
        self._delivery_notice_open()
        with self.transaction():
            row = self.conn.execute('SELECT * FROM delivery_notice WHERE binding_id=? AND source_id=?',
                                    (binding_id, source_id)).fetchone()
            if row is not None:
                existing = tuple(row[name] for name in (
                    'binding_id', 'source_id', 'source_author_pubkey', 'source_app_id', 'source_created_at',
                    'sync_app_id', 'channel_id', 'chat_id', 'root_message_id', 'first_observed_at',
                    'delay_seconds', 'deadline_at', 'notice_uuid', 'notice_version', 'notice_content_sha256'))
                if existing != immutable:
                    raise StoreError(ERROR)
                return self._delivery_notice_record(row), False
            binding = self.conn.execute('SELECT channel_id,chat_id,sync_app_id,status FROM binding WHERE binding_id=?',
                                        (binding_id,)).fetchone()
            if (binding is None or (binding['channel_id'], binding['chat_id'], binding['sync_app_id'], binding['status'])
                    != (channel_id, chat_id, sync_app_id, 'active')):
                raise StoreError(ERROR)
            with self._delivery_notice_witness('observe', binding_id, source_id):
                self.conn.execute('''INSERT INTO delivery_notice(
                    binding_id,source_id,source_author_pubkey,source_app_id,source_created_at,
                    sync_app_id,channel_id,chat_id,root_message_id,first_observed_at,delay_seconds,
                    deadline_at,notice_uuid,notice_version,notice_content_sha256,state,
                    resolved_without_notice_at,notice_message_id,recovery_version,recovery_content_sha256,
                    created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting',NULL,NULL,NULL,NULL,?,?)''',
                    (*immutable, first_observed_at, first_observed_at))
            row = self.conn.execute('SELECT * FROM delivery_notice WHERE binding_id=? AND source_id=?',
                                    (binding_id, source_id)).fetchone()
            return self._delivery_notice_record(row), True

    def get_delivery_notice(self, binding_id, source_id):
        binding_id = self._delivery_notice_id(binding_id)
        source_id = self._delivery_notice_id(source_id)
        self._delivery_notice_open()
        with self._lock:
            row = self.conn.execute('SELECT * FROM delivery_notice WHERE binding_id=? AND source_id=?',
                                    (binding_id, source_id)).fetchone()
            return self._delivery_notice_record(row)

    def list_delivery_notices(self, *, states, limit):
        states = self._delivery_notice_states(states)
        if type(limit) is not int or not 1 <= limit <= 256:
            raise StoreError(ERROR)
        self._delivery_notice_open()
        placeholders = ','.join('?' for _ in states)
        with self._lock:
            rows = self.conn.execute(
                f'SELECT * FROM delivery_notice WHERE state IN ({placeholders}) ORDER BY deadline_at,binding_id,source_id LIMIT ?',
                (*states, limit)).fetchall()
            return tuple(self._delivery_notice_record(row) for row in rows)

    def _delivery_notice_transition(self, binding_id, source_id, expected_state, allowed, assignments, values, now):
        binding_id = self._delivery_notice_id(binding_id)
        source_id = self._delivery_notice_id(source_id)
        if type(expected_state) is not str or expected_state not in allowed:
            raise StoreError(ERROR)
        now = stamp(now)
        self._delivery_notice_open()
        with self.transaction():
            row = self.conn.execute('SELECT * FROM delivery_notice WHERE binding_id=? AND source_id=?',
                                    (binding_id, source_id)).fetchone()
            if row is None or row['state'] != expected_state:
                raise StoreError(ERROR)
            with self._delivery_notice_witness('transition', binding_id, source_id):
                updated = self.conn.execute(
                    f'UPDATE delivery_notice SET {assignments},updated_at=max(updated_at,?) WHERE binding_id=? AND source_id=? AND state=?',
                    (*values, now, binding_id, source_id, expected_state)).rowcount
            if updated != 1:
                raise StoreError(ERROR)
            row = self.conn.execute('SELECT * FROM delivery_notice WHERE binding_id=? AND source_id=?',
                                    (binding_id, source_id)).fetchone()
            return self._delivery_notice_record(row)

    def reserve_delivery_notice_send(self, binding_id, source_id, *, expected_state, now):
        now = stamp(now)
        binding_id = self._delivery_notice_id(binding_id)
        source_id = self._delivery_notice_id(source_id)
        row = self.get_delivery_notice(binding_id, source_id)
        if row is None or row.state != 'waiting' or expected_state != 'waiting' or now < row.deadline_at:
            raise StoreError(ERROR)
        return self._delivery_notice_transition(binding_id, source_id, expected_state, ('waiting',),
                                                "state='reserved'", (), now)

    def resolve_delivery_notice_without_notice(self, binding_id, source_id, *, expected_state, observed_delivery_at, now):
        observed_delivery_at = stamp(observed_delivery_at)
        now = stamp(now)
        if observed_delivery_at > now:
            raise StoreError(ERROR)
        return self._delivery_notice_transition(binding_id, source_id, expected_state, ('waiting',),
            "state='resolved_without_notice',resolved_without_notice_at=?", (observed_delivery_at,), now)

    def mark_delivery_notice_unknown(self, binding_id, source_id, *, expected_state, now):
        destination = {'reserved': 'unknown', 'recovery_reserved': 'recovery_unknown'}.get(expected_state)
        if destination is None:
            raise StoreError(ERROR)
        return self._delivery_notice_transition(binding_id, source_id, expected_state, (expected_state,),
                                                'state=?', (destination,), now)

    def record_delivery_notice_readback(self, binding_id, source_id, *, expected_state, notice_message_id,
                                        observed_notice_version, observed_content_sha256, now):
        notice_message_id = self._delivery_notice_id(notice_message_id)
        observed_notice_version = self._delivery_notice_version(observed_notice_version)
        observed_content_sha256 = hexid(observed_content_sha256)
        row = self.get_delivery_notice(binding_id, source_id)
        if (row is None or expected_state not in ('reserved', 'unknown')
                or row.notice_version != observed_notice_version
                or row.notice_content_sha256 != observed_content_sha256
                or (row.notice_message_id is not None and row.notice_message_id != notice_message_id)):
            raise StoreError(ERROR)
        return self._delivery_notice_transition(binding_id, source_id, expected_state, ('reserved', 'unknown'),
            "state='noticed',notice_message_id=?", (notice_message_id,), now)

    def reserve_delivery_notice_recovery(self, binding_id, source_id, *, expected_state, notice_message_id,
                                        recovery_version, recovery_content_sha256, now):
        notice_message_id = self._delivery_notice_id(notice_message_id)
        recovery_version = self._delivery_notice_version(recovery_version)
        recovery_content_sha256 = hexid(recovery_content_sha256)
        row = self.get_delivery_notice(binding_id, source_id)
        if (row is None or row.state != 'noticed' or row.notice_message_id != notice_message_id
                or expected_state != 'noticed' or row.recovery_version is not None):
            raise StoreError(ERROR)
        return self._delivery_notice_transition(binding_id, source_id, expected_state, ('noticed',),
            'state=\'recovery_reserved\',recovery_version=?,recovery_content_sha256=?',
            (recovery_version, recovery_content_sha256), now)

    def record_delivery_notice_recovery_readback(self, binding_id, source_id, *, expected_state,
                                                 notice_message_id, observed_recovery_version,
                                                 observed_content_sha256, now):
        notice_message_id = self._delivery_notice_id(notice_message_id)
        observed_recovery_version = self._delivery_notice_version(observed_recovery_version)
        observed_content_sha256 = hexid(observed_content_sha256)
        row = self.get_delivery_notice(binding_id, source_id)
        if (row is None or expected_state not in ('recovery_reserved', 'recovery_unknown')
                or row.notice_message_id != notice_message_id
                or row.recovery_version != observed_recovery_version
                or row.recovery_content_sha256 != observed_content_sha256):
            raise StoreError(ERROR)
        return self._delivery_notice_transition(binding_id, source_id, expected_state,
            ('recovery_reserved', 'recovery_unknown'), "state='recovered'", (), now)

    def _remote_image_sql_witness(self, operation, target_id, source_id, ordinal):
        return int(self._remote_image_write_witness ==
                   (operation, target_id, source_id, ordinal))

    @contextlib.contextmanager
    def _remote_image_witness(self, operation, target_id, source_id, ordinal):
        if (self._remote_image_write_witness is not None
                or operation not in ('reserve', 'pin', 'ack')):
            raise StoreError(ERROR)
        self._remote_image_write_witness = (operation, target_id, source_id, ordinal)
        try:
            yield
        finally:
            self._remote_image_write_witness = None

    @staticmethod
    def _remote_image_ordinal(value):
        if type(value) is not int or not 0 <= value <= 3:
            raise StoreError(ERROR)
        return value

    @staticmethod
    def _remote_image_hexid(value):
        if type(value) is not str:
            raise StoreError(ERROR)
        return hexid(value)

    @staticmethod
    def _remote_image_size(value):
        if type(value) is not int or not 1 <= value <= 10_000_000:
            raise StoreError(ERROR)
        return value

    @staticmethod
    def _remote_image_key(value):
        if (type(value) is not str or not 1 <= len(value) <= 256
                or not value.isascii() or any(ord(char) < 32 or ord(char) > 126 for char in value)):
            raise StoreError(ERROR)
        return value

    @staticmethod
    def _remote_image_record(row):
        return RemoteImageUploadRecord(**dict(row)) if row is not None else None

    def _remote_image_current(self, target_id, revision, scope_hash, now):
        grant = self._remote_current(target_id, revision, scope_hash, now)
        if not grant or 'message' not in grant.capabilities:
            return None
        return grant

    def remote_image_upload_by_source(self, target_id, source_id, ordinal):
        self._remote_image_hexid(target_id); self._remote_image_hexid(source_id)
        ordinal = self._remote_image_ordinal(ordinal)
        with self._lock:
            self._check_files()
            row = self.conn.execute("""SELECT * FROM remote_image_upload
                WHERE target_id=? AND source_id=? AND ordinal=?""",
                (target_id, source_id, ordinal)).fetchone()
            return self._remote_image_record(row)

    def reserve_remote_image_upload(self, target_id, source_id, ordinal, revision, scope_hash,
                                    source_at, content_hash, mime_type, byte_size, *, now):
        self._remote_image_hexid(target_id); self._remote_image_hexid(source_id)
        ordinal = self._remote_image_ordinal(ordinal)
        if stamp(revision) == 0:
            raise StoreError(ERROR)
        self._remote_image_hexid(scope_hash); stamp(source_at)
        self._remote_image_hexid(content_hash); stamp(now)
        if type(mime_type) is not str or mime_type not in ('image/png', 'image/jpeg'):
            raise StoreError(ERROR)
        byte_size = self._remote_image_size(byte_size)
        immutable = (target_id, source_id, ordinal, revision, scope_hash, source_at,
                     content_hash, mime_type, byte_size)
        with self.transaction():
            if not self._remote_image_current(target_id, revision, scope_hash, now):
                return None
            row = self.conn.execute("""SELECT * FROM remote_image_upload
                WHERE target_id=? AND source_id=? AND ordinal=?""",
                (target_id, source_id, ordinal)).fetchone()
            if row is not None:
                existing = tuple(row[name] for name in (
                    'target_id', 'source_id', 'ordinal', 'revision', 'scope_hash', 'source_at',
                    'content_hash', 'mime_type', 'byte_size'))
                return self._remote_image_record(row) if existing == immutable else None
            with self._remote_image_witness('reserve', target_id, source_id, ordinal):
                self.conn.execute("""INSERT INTO remote_image_upload(
                    target_id,source_id,ordinal,revision,scope_hash,source_at,content_hash,
                    mime_type,byte_size,state,image_key,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,'unknown','',?,?)""",
                    (*immutable, now, now))
            return self.remote_image_upload_by_source(target_id, source_id, ordinal)

    def pin_remote_image_key(self, target_id, source_id, ordinal, revision, scope_hash,
                             image_key, *, now):
        self._remote_image_hexid(target_id); self._remote_image_hexid(source_id)
        ordinal = self._remote_image_ordinal(ordinal)
        if stamp(revision) == 0:
            raise StoreError(ERROR)
        self._remote_image_hexid(scope_hash); image_key = self._remote_image_key(image_key); stamp(now)
        with self.transaction():
            if not self._remote_image_current(target_id, revision, scope_hash, now):
                return False
            row = self.conn.execute("""SELECT * FROM remote_image_upload
                WHERE target_id=? AND source_id=? AND ordinal=?""",
                (target_id, source_id, ordinal)).fetchone()
            if (row is None or (row['revision'], row['scope_hash']) != (revision, scope_hash)
                    or row['state'] != 'unknown'):
                return False
            if row['image_key']:
                return row['image_key'] == image_key
            with self._remote_image_witness('pin', target_id, source_id, ordinal):
                return self.conn.execute("""UPDATE remote_image_upload
                    SET image_key=?,updated_at=max(updated_at,?)
                    WHERE target_id=? AND source_id=? AND ordinal=? AND state='unknown' AND image_key=''""",
                    (image_key, now, target_id, source_id, ordinal)).rowcount == 1

    def ack_remote_image_upload(self, target_id, source_id, ordinal, revision, scope_hash,
                                image_key, content_hash, mime_type, byte_size, *, now):
        self._remote_image_hexid(target_id); self._remote_image_hexid(source_id)
        ordinal = self._remote_image_ordinal(ordinal)
        if stamp(revision) == 0:
            raise StoreError(ERROR)
        self._remote_image_hexid(scope_hash); image_key = self._remote_image_key(image_key)
        self._remote_image_hexid(content_hash); stamp(now)
        if type(mime_type) is not str or mime_type not in ('image/png', 'image/jpeg'):
            raise StoreError(ERROR)
        byte_size = self._remote_image_size(byte_size)
        with self.transaction():
            if not self._remote_image_current(target_id, revision, scope_hash, now):
                return False
            row = self.conn.execute("""SELECT * FROM remote_image_upload
                WHERE target_id=? AND source_id=? AND ordinal=?""",
                (target_id, source_id, ordinal)).fetchone()
            if (row is None or (row['revision'], row['scope_hash']) != (revision, scope_hash)
                    or (row['content_hash'], row['mime_type'], row['byte_size'])
                       != (content_hash, mime_type, byte_size)
                    or row['image_key'] != image_key):
                return False
            if row['state'] == 'acked':
                return True
            if row['state'] != 'unknown' or not row['image_key']:
                return False
            with self._remote_image_witness('ack', target_id, source_id, ordinal):
                return self.conn.execute("""UPDATE remote_image_upload
                    SET state='acked',updated_at=max(updated_at,?)
                    WHERE target_id=? AND source_id=? AND ordinal=? AND state='unknown'""",
                    (now, target_id, source_id, ordinal)).rowcount == 1

    def remote_proof(self, target_id):
        hexid(target_id)
        with self._lock:
            self._check_files()
            original = self.remote_grant(target_id)
            if original is None:
                return None
            row = self.conn.execute("SELECT * FROM remote_target WHERE target_id=?", (target_id,)).fetchone()
            fields = ("checked_at", "valid_until", "claim_event_id", "agent_profile_event_id",
                      "agent_policy_event_id", "roster_event_id")
            current = replace(original.evidence, **{name: row["proof_" + name] for name in fields})
            return RemoteProofRecord(target_id, original.revision, original.scope_hash, current)

    def refresh_remote_proof(self, target_id, evidence, *, revision, scope_hash, now):
        """Only a trusted fresh verifier may renew unchanged original authority.

        No grant, delivery pin, approval or dispatch intent is replaced. The
        current proof can expire independently; its historical copy cannot be
        mistaken for current runtime authorization.
        """
        hexid(target_id); hexid(scope_hash); stamp(revision); stamp(now)
        evidence = self._remote_evidence(evidence)
        with self.transaction():
            order = getattr(self, '_own_home_order', None)
            if (self.active_own_home_admission(evidence.agent_id) is not None
                    or (order is not None and order._busy(evidence.agent_id))):
                return False
            original = self.remote_grant(target_id)
            target = self.conn.execute("SELECT * FROM remote_target WHERE target_id=?", (target_id,)).fetchone()
            agent = self.conn.execute("SELECT * FROM agent WHERE pubkey=?", (evidence.agent_id,)).fetchone()
            if (not original or not target or target["status"] != "active" or not agent
                    or agent["status"] != "active" or (agent["owner_pubkey"], agent["app_id"])
                        != (evidence.owner_pubkey, evidence.app_id)
                    or (original.revision, original.scope_hash) != (revision, scope_hash)
                    or self._remote_scope(evidence) != scope_hash
                    or not evidence.checked_at <= now < evidence.valid_until
                    or evidence.checked_at < target["proof_checked_at"] or now < target["updated_at"]
                    or not self._remote_approval(evidence)):
                return False
            self.conn.execute("""UPDATE remote_target SET proof_checked_at=?,proof_valid_until=?,proof_claim_event_id=?,
                proof_agent_profile_event_id=?,proof_agent_policy_event_id=?,proof_roster_event_id=?,updated_at=?
                WHERE target_id=? AND current_revision=?""", (evidence.checked_at, evidence.valid_until,
                evidence.claim_event_id, evidence.agent_profile_event_id, evidence.agent_policy_event_id,
                evidence.roster_event_id, now, target_id, revision))
            return True

    def activate_remote_grant(self, evidence, *, expected_revision, now):
        """Record trusted approval metadata, not a claim of runtime activation.

        Local card authority is read from real SQL joins. Mirror authority must
        already be verified by an upstream accepted parser, currently absent.
        """
        evidence = self._remote_evidence(evidence)
        stamp(expected_revision); stamp(now)
        if not evidence.checked_at <= now < evidence.valid_until:
            raise StoreError(ERROR)
        target_id = self._remote_digest([evidence.agent_id, evidence.channel_id])
        scope_hash = self._remote_scope(evidence)
        with self.transaction():
            order = getattr(self, '_own_home_order', None)
            if (self.active_own_home_admission(evidence.agent_id) is not None
                    or (order is not None and order._busy(evidence.agent_id))):
                raise StoreError(ERROR)
            agent = self.conn.execute("SELECT * FROM agent WHERE pubkey=?", (evidence.agent_id,)).fetchone()
            if (not agent or agent["status"] != "active" or
                    (agent["owner_pubkey"], agent["app_id"]) != (evidence.owner_pubkey, evidence.app_id)
                    or not self._remote_approval(evidence)):
                raise StoreError(ERROR)
            existing = self.conn.execute("SELECT * FROM remote_target WHERE target_id=?", (target_id,)).fetchone()
            revision = existing["current_revision"] if existing else 0
            if revision != expected_revision:
                raise StoreError(ERROR)
            if existing:
                fields = ("agent_id", "owner_pubkey", "app_id", "channel_id", "chat_id", "chat_ref", "relay_origin")
                if any(existing[name] != getattr(evidence, name) for name in fields):
                    raise StoreError(ERROR)
                prior = self.remote_grant(target_id)
                if prior.scope_hash == scope_hash and existing["status"] == "active":
                    # Same stable authority is not an implicit freshness renewal.
                    # Call refresh_remote_proof explicitly after fresh verification.
                    return prior
                if now < existing["updated_at"] or evidence.checked_at < existing["proof_checked_at"]:
                    raise StoreError(ERROR)
                self.conn.execute("""UPDATE remote_target SET current_revision=?,status='active',updated_at=?,
                    proof_checked_at=?,proof_valid_until=?,proof_claim_event_id=?,proof_agent_profile_event_id=?,
                    proof_agent_policy_event_id=?,proof_roster_event_id=? WHERE target_id=?""",
                    (revision + 1, now, evidence.checked_at, evidence.valid_until, evidence.claim_event_id,
                     evidence.agent_profile_event_id, evidence.agent_policy_event_id, evidence.roster_event_id, target_id))
            else:
                self.conn.execute("INSERT INTO remote_target VALUES(?,?,?,?,?,?,?,?,1,'active',?,?,?,?,?,?,?,?)",
                    (target_id, evidence.agent_id, evidence.owner_pubkey, evidence.app_id, evidence.channel_id,
                     evidence.chat_id, evidence.chat_ref, evidence.relay_origin, now, now,
                     evidence.checked_at, evidence.valid_until, evidence.claim_event_id,
                     evidence.agent_profile_event_id, evidence.agent_policy_event_id, evidence.roster_event_id))
            values = [getattr(evidence, name) for name in (
                "mirror_pubkey", "mirror_owner_pubkey", "claim_event_id", "agent_profile_event_id",
                "agent_policy_event_id", "roster_event_id", "allowlist_hash", "approval_kind", "approval_id", "approval_hash")]
            mask = sum(1 << i for i, c in enumerate(REMOTE_CAPABILITIES) if c in evidence.capabilities)
            self.conn.execute("INSERT INTO remote_grant VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (target_id, revision + 1, scope_hash, *values, mask, evidence.checked_at, evidence.valid_until, evidence.claimed_at, now))
            return self.remote_grant(target_id)

    def suspend_remote_grant(self, target_id, *, expected_revision, now):
        hexid(target_id); stamp(expected_revision); stamp(now)
        with self.transaction():
            return self.conn.execute("""UPDATE remote_target SET status='suspended',updated_at=max(updated_at,?)
                WHERE target_id=? AND current_revision=? AND status='active'""",
                (now, target_id, expected_revision)).rowcount == 1

    @staticmethod
    def _remote_delivery_record(row):
        return RemoteDeliveryRecord(**dict(row)) if row is not None else None

    def remote_delivery(self, delivery_id):
        hexid(delivery_id)
        with self._lock:
            self._check_files()
            return self._remote_delivery_record(self.conn.execute(
                "SELECT * FROM remote_delivery WHERE id=?", (delivery_id,)).fetchone())

    def remote_delivery_by_source(self, target_id, source_id, action):
        hexid(target_id); hexid(source_id)
        if action not in REMOTE_CAPABILITIES:
            raise StoreError(ERROR)
        with self._lock:
            self._check_files()
            return self._remote_delivery_record(self.conn.execute(
                "SELECT * FROM remote_delivery WHERE target_id=? AND source_id=? AND action=?",
                (target_id, source_id, action)).fetchone())

    def reserve_remote_delivery(self, target_id, source_id, action, *, revision, scope_hash,
                                source_at, content_hash, root_id="", now):
        hexid(target_id); hexid(source_id); hexid(scope_hash); hexid(content_hash)
        hexid(root_id, empty=True); stamp(revision); stamp(source_at); stamp(now)
        if action not in REMOTE_CAPABILITIES:
            raise StoreError(ERROR)
        with self.transaction():
            grant = self._remote_current(target_id, revision, scope_hash, now)
            if not grant or action not in grant.capabilities:
                raise StoreError(ERROR)
            existing = self.remote_delivery_by_source(target_id, source_id, action)
            if existing:
                if (existing.source_at, existing.content_hash, existing.root_id) != (source_at, content_hash, root_id):
                    raise StoreError(ERROR)
                # Never re-pin an old unresolved write to a newer grant. Caller
                # sees created=False and must perform readback only, not dispatch.
                return RemoteReservation(existing, False)
            delivery_id = self._remote_digest([target_id, source_id, action])
            self.conn.execute("""INSERT INTO remote_delivery(id,target_id,source_id,action,revision,scope_hash,
                source_at,content_hash,root_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'reserved',?,?)""",
                (delivery_id, target_id, source_id, action, revision, scope_hash, source_at, content_hash, root_id, now, now))
            return RemoteReservation(self.remote_delivery(delivery_id), True)

    def mark_remote_unknown(self, delivery_id, *, revision, scope_hash, now):
        hexid(delivery_id); hexid(scope_hash); stamp(revision); stamp(now)
        with self.transaction():
            row = self.remote_delivery(delivery_id)
            if (not row or (row.revision, row.scope_hash) != (revision, scope_hash)
                    or row.status not in ("reserved", "unknown")
                    or not self._remote_current(row.target_id, revision, scope_hash, now)):
                return False
            self.conn.execute("UPDATE remote_delivery SET status='unknown',updated_at=max(updated_at,?) WHERE id=?",
                              (now, delivery_id))
            return True

    def ack_remote_delivery(self, delivery_id, *, revision, scope_hash, sender_app_id,
                            message_id, reaction_id="", emoji="", receipt_hash, now):
        """CAS metadata after fresh authorization and actual own-app readback.

        The caller owns physical proof. SQL enforces its original grant/app and
        immutable receipt; this never advances a complete-scan cursor itself.
        """
        hexid(delivery_id); hexid(scope_hash); hexid(receipt_hash)
        ident(sender_app_id, "cli_"); ident(message_id, "om_"); stamp(revision); stamp(now)
        if (emoji not in ("", "GLANCE", "Typing", "DONE", "THUMBSUP", "OK", "THANKS", "MUSCLE", "CrossMark")
                or bool(reaction_id) != bool(emoji)
                or not isinstance(reaction_id, str)
                or (reaction_id and not re.fullmatch(r"(?:[A-Za-z0-9_.:-]{1,256}|[A-Za-z0-9_-]{85}[AQgw]==)", reaction_id))):
            raise StoreError(ERROR)
        with self.transaction():
            row = self.remote_delivery(delivery_id)
            if not row or (row.revision, row.scope_hash) != (revision, scope_hash):
                return False
            if (row.action in ("reaction_add", "reaction_remove")) != bool(reaction_id):
                raise StoreError(ERROR)
            grant = self._remote_current(row.target_id, revision, scope_hash, now)
            if not grant or sender_app_id != grant.evidence.app_id or row.action not in grant.capabilities:
                return False
            receipt = (sender_app_id, message_id, reaction_id, emoji, receipt_hash)
            if row.status == "acked":
                return receipt == (row.sender_app_id, row.message_id, row.reaction_id, row.emoji, row.receipt_hash)
            if row.status != "unknown":
                return False
            self.conn.execute("""UPDATE remote_delivery SET status='acked',sender_app_id=?,message_id=?,
                reaction_id=?,emoji=?,receipt_hash=?,updated_at=max(updated_at,?) WHERE id=? AND status='unknown'""",
                (*receipt, now, delivery_id))
            return True

    def pending_remote_deliveries(self, target_id, *, after=None, limit=128):
        hexid(target_id)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise StoreError(ERROR)
        if after is not None:
            if type(after) is not tuple or len(after) != 2:
                raise StoreError(ERROR)
            stamp(after[0]); hexid(after[1])
        with self._lock:
            self._check_files()
            sql = "SELECT * FROM remote_delivery WHERE target_id=? AND status IN ('reserved','unknown')"
            args = [target_id]
            if after is not None:
                sql += " AND (created_at,id)>(?,?)"
                args.extend(after)
            sql += " ORDER BY created_at,id LIMIT ?"
            return [self._remote_delivery_record(r) for r in self.conn.execute(sql, (*args, limit))]

    def remote_replay_since(self, target_id, *, initial_since=0):
        hexid(target_id); stamp(initial_since)
        with self._lock:
            self._check_files()
            cursor = self.conn.execute("SELECT position FROM remote_cursor WHERE target_id=?", (target_id,)).fetchone()
            blocked = self.conn.execute("""SELECT min(source_at) FROM remote_delivery WHERE target_id=?
                AND status IN ('reserved','unknown')""", (target_id,)).fetchone()[0]
            if not cursor:
                # A WSS reservation or ACK does not prove a complete history
                # read. Retain the caller's original reviewed floor; unresolved
                # older work can lower it but must never lift it.
                return initial_since if blocked is None else min(initial_since, max(0, blocked - OVERLAP))
            position = cursor[0]
            if blocked is not None:
                position = min(position, blocked)
            return max(0, position - OVERLAP)

    def commit_remote_cursor(self, target_id, position, *, revision, scope_hash, complete, now):
        hexid(target_id); hexid(scope_hash); stamp(position); stamp(revision); stamp(now)
        if type(complete) is not bool:
            raise StoreError(ERROR)
        with self.transaction():
            if not complete or not self._remote_current(target_id, revision, scope_hash, now):
                return False
            cursor = self.conn.execute("SELECT position FROM remote_cursor WHERE target_id=?", (target_id,)).fetchone()
            if cursor and position < cursor[0]:
                return False
            if self.conn.execute("""SELECT 1 FROM remote_delivery WHERE target_id=?
                    AND status IN ('reserved','unknown') AND source_at<=? LIMIT 1""", (target_id, position)).fetchone():
                return False
            self.conn.execute("""INSERT INTO remote_cursor VALUES(?,?,?,?) ON CONFLICT(target_id)
                DO UPDATE SET position=excluded.position,revision=excluded.revision,updated_at=max(remote_cursor.updated_at,excluded.updated_at)""",
                (target_id, position, revision, now))
            return True

    @staticmethod
    def _restart_record(row):
        if row is None:
            return None
        old = RestartProcess(row["old_pid"], row["old_start"], row["old_invocation"])
        new = (None if row["new_pid"] is None else
               RestartProcess(row["new_pid"], row["new_start"], row["new_invocation"]))
        return RestartRecord(row["operation_id"], row["agent_id"], row["scope_hash"],
                             row["protectedfiles_hash"], old, new, row["state"],
                             row["created_at"], row["updated_at"])

    @staticmethod
    def _restart_keys(operation_id, agent_id, scope_hash, protectedfiles_hash):
        ident(operation_id); hexid(agent_id); hexid(scope_hash); hexid(protectedfiles_hash)

    @staticmethod
    def _restart_process(process):
        if type(process) is not RestartProcess:
            raise StoreError(ERROR)
        return RestartProcess(process.pid, process.start, process.invocation)

    @staticmethod
    def _restart_matches(row, agent_id, scope_hash, protectedfiles_hash):
        return bool(row and (row.agent_id, row.scope_hash, row.protectedfiles_hash)
                    == (agent_id, scope_hash, protectedfiles_hash))

    def restart_record(self, operation_id):
        ident(operation_id)
        with self._lock:
            self._check_files()
            return self._restart_record(self.conn.execute(
                "SELECT * FROM restart_operation WHERE operation_id=?", (operation_id,)).fetchone())

    def active_restart(self, agent_id):
        """Held intents remain visible even after the agent is paused or retired."""
        hexid(agent_id)
        with self._lock:
            self._check_files()
            return self._restart_record(self.conn.execute(
                "SELECT * FROM restart_operation WHERE agent_id=? AND state IN ('reserved','unknown')",
                (agent_id,)).fetchone())

    def pending_restarts(self, *, limit=256):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise StoreError(ERROR)
        with self._lock:
            self._check_files()
            return [self._restart_record(row) for row in self.conn.execute(
                "SELECT * FROM restart_operation WHERE state IN ('reserved','unknown') ORDER BY created_at,operation_id LIMIT ?",
                (limit,))]

    def reserve_restart(self, operation_id, agent_id, scope_hash, protectedfiles_hash, old_process, *, now):
        """Reserve on the owning loop before dispatch; reused intents never authorize another dispatch."""
        self._restart_keys(operation_id, agent_id, scope_hash, protectedfiles_hash)
        old_process = self._restart_process(old_process)
        stamp(now)
        with self.transaction():
            agent = self.conn.execute("SELECT status FROM agent WHERE pubkey=?", (agent_id,)).fetchone()
            if not agent or agent["status"] != "active":
                raise StoreError(ERROR)
            proposed = self.restart_record(operation_id)
            if proposed and (not self._restart_matches(proposed, agent_id, scope_hash, protectedfiles_hash)
                             or proposed.old_process != old_process):
                raise StoreError(ERROR)
            active = self.active_restart(agent_id)
            if active:
                if (not self._restart_matches(active, agent_id, scope_hash, protectedfiles_hash)
                        or active.old_process != old_process):
                    raise StoreError(ERROR)
                return RestartReservation(active, False)
            if proposed:
                return RestartReservation(proposed, False)
            self.conn.execute("""INSERT INTO restart_operation(operation_id,agent_id,scope_hash,protectedfiles_hash,
                old_pid,old_start,old_invocation,state,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'reserved',?,?)""",
                (operation_id, agent_id, scope_hash, protectedfiles_hash, old_process.pid,
                 old_process.start, old_process.invocation, now, now))
            witness = (self._rollback_frames[0], False)
            self._restart_provenance[operation_id] = witness
            def forget_creation():
                if self._restart_provenance.get(operation_id) is witness:
                    self._restart_provenance.pop(operation_id, None)
            self.on_rollback(forget_creation)
            return RestartReservation(self.restart_record(operation_id), True)

    def mark_restart_unknown(self, operation_id, agent_id, scope_hash, protectedfiles_hash, *, now):
        """Persist uncertainty BEFORE external dispatch; no lease or automatic expiry permits replay."""
        self._restart_keys(operation_id, agent_id, scope_hash, protectedfiles_hash); stamp(now)
        with self.transaction():
            row = self.restart_record(operation_id)
            agent = self.conn.execute("SELECT status FROM agent WHERE pubkey=?", (agent_id,)).fetchone()
            if (not self._restart_matches(row, agent_id, scope_hash, protectedfiles_hash)
                    or row.state not in ("reserved", "unknown") or not agent or agent["status"] != "active"):
                return False
            if row.state == "reserved":
                self.conn.execute("UPDATE restart_operation SET state='unknown',updated_at=? WHERE operation_id=? AND state='reserved'",
                                  (max(now, row.updated_at), operation_id))
                before = self._restart_provenance.get(operation_id)
                if before is not None and before[0] is self._rollback_frames[0]:
                    witness = (before[0], True)
                    self._restart_provenance[operation_id] = witness
                    def restore_dispatch():
                        if self._restart_provenance.get(operation_id) is witness:
                            self._restart_provenance[operation_id] = before
                    self.on_rollback(restore_dispatch)
            return True

    def pin_restart(self, operation_id, agent_id, scope_hash, protectedfiles_hash, new_process, *, now):
        """Only the original live dispatch may pin its first observed replacement. Reopen cannot establish provenance."""
        self._restart_keys(operation_id, agent_id, scope_hash, protectedfiles_hash); stamp(now)
        new_process = self._restart_process(new_process)
        with self.transaction():
            row = self.restart_record(operation_id)
            if not self._restart_matches(row, agent_id, scope_hash, protectedfiles_hash) or row.state != "unknown":
                return False
            if row.new_process is not None:
                return row.new_process == new_process
            if (row.old_process.invocation == new_process.invocation
                    or (row.old_process.pid, row.old_process.start) == (new_process.pid, new_process.start)):
                return False
            changed = self.conn.execute("""UPDATE restart_operation SET new_pid=?,new_start=?,new_invocation=?,updated_at=?
                WHERE operation_id=? AND state='unknown' AND new_pid IS NULL""",
                (new_process.pid, new_process.start, new_process.invocation,
                 max(now, row.updated_at), operation_id)).rowcount
            return changed == 1

    def ack_restart(self, operation_id, agent_id, scope_hash, protectedfiles_hash, pinned_process, *, now):
        """Caller must finish verified readback and reap lock cleanup before this final synchronous ACK."""
        self._restart_keys(operation_id, agent_id, scope_hash, protectedfiles_hash); stamp(now)
        pinned_process = self._restart_process(pinned_process)
        with self.transaction():
            row = self.restart_record(operation_id)
            if (not self._restart_matches(row, agent_id, scope_hash, protectedfiles_hash)
                    or row.state not in ("unknown", "acked") or row.new_process != pinned_process):
                return False
            if row.state == "unknown":
                self.conn.execute("UPDATE restart_operation SET state='acked',updated_at=? WHERE operation_id=? AND state='unknown'",
                                  (max(now, row.updated_at), operation_id))
            return True

    @staticmethod
    def _own_home_admission_record(row):
        if row is None:
            return None
        process = RestartProcess(row['old_pid'], row['old_start'], row['old_invocation'])
        return OwnHomeAdmissionRecord(*(row[name] for name in (
            'admission_id', 'agent_id', 'snapshot_hash', 'scope_hash', 'protectedfiles_hash',
            'catalog_hash', 'legacy_join_hash', 'profile_hash', 'env_before_hash',
            'env_after_hash', 'agent_spec_hash', 'prior_channels_hash',
            'proposed_channels_hash', 'approval_set_hash')), process, row['state'],
            row['receipt_hash'], row['created_at'], row['updated_at'])

    @staticmethod
    def _own_home_channel_rows(channels):
        names = frozenset(('channel_id', 'chat_id', 'chat_ref', 'source_kind',
            'approval_id', 'approval_hash', 'mirror_pubkey', 'mirror_owner_pubkey',
            'claimed_at', 'claim_event_id', 'policy_event_id', 'roster_event_id',
            'authorization_hash'))
        if type(channels) not in (tuple, list) or not 1 <= len(channels) <= 256:
            raise StoreError(ERROR)
        normalized = []
        for row in channels:
            if type(row) is not dict or frozenset(row) != names:
                raise StoreError(ERROR)
            channel_id = row['channel_id']
            if (not isinstance(channel_id, str) or not re.fullmatch(
                    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', channel_id)):
                raise StoreError(ERROR)
            chat_id = ident(row['chat_id'], 'oc_')
            chat_ref = hexid(row['chat_ref'])
            if chat_ref != hashlib.sha256(('buzz-feishu-chat:v1:' + chat_id).encode()).hexdigest():
                raise StoreError(ERROR)
            source_kind = row['source_kind']
            if not isinstance(source_kind, str) or source_kind not in ('own_approval', 'local_grant', 'remote_grant'):
                raise StoreError(ERROR)
            for field in ('approval_id', 'approval_hash', 'mirror_pubkey', 'mirror_owner_pubkey',
                          'claim_event_id', 'policy_event_id', 'roster_event_id', 'authorization_hash'):
                hexid(row[field])
            claimed_at = stamp(row['claimed_at'])
            normalized.append({
                'channel_id': channel_id, 'chat_id': chat_id, 'chat_ref': chat_ref,
                'source_kind': source_kind, 'approval_id': row['approval_id'],
                'approval_hash': row['approval_hash'], 'mirror_pubkey': row['mirror_pubkey'],
                'mirror_owner_pubkey': row['mirror_owner_pubkey'], 'claimed_at': claimed_at,
                'claim_event_id': row['claim_event_id'], 'policy_event_id': row['policy_event_id'],
                'roster_event_id': row['roster_event_id'], 'authorization_hash': row['authorization_hash']})
        normalized.sort(key=lambda item: item['channel_id'])
        ids = [row['channel_id'] for row in normalized]
        if len(ids) != len(set(ids)):
            raise StoreError(ERROR)
        return tuple(normalized)

    @staticmethod
    def _own_home_channels_digest(channels):
        ids = sorted(row['channel_id'] for row in channels)
        encoded = json.dumps(ids, separators=(',', ':')).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _own_home_channel_record(row):
        return OwnHomeAdmissionChannel(*(row[name] for name in (
            'admission_id', 'ordinal', 'channel_id', 'chat_id', 'chat_ref', 'source_kind',
            'approval_id', 'approval_hash', 'mirror_pubkey', 'mirror_owner_pubkey',
            'claimed_at', 'claim_event_id', 'policy_event_id', 'roster_event_id', 'authorization_hash')))

    @staticmethod
    def _own_home_restart_link(row):
        if row is None:
            return None
        return OwnHomeAdmissionRestartLink(row['admission_id'], row['restart_operation_id'],
                                           row['scope_hash'], row['created_at'])

    def _own_home_sql_restart_witness(self, admission_id, operation_id):
        if not isinstance(admission_id, str) or not isinstance(operation_id, str):
            return 0
        witness = self._restart_provenance.get(operation_id)
        return int(bool(witness and self._rollback_frames
            and witness[0] is self._rollback_frames[0] and witness[1] is True))

    def own_home_admission(self, admission_id):
        hexid(admission_id)
        with self._lock:
            self._check_files()
            return self._own_home_admission_record(self.conn.execute(
                'SELECT * FROM own_home_admission WHERE admission_id=?', (admission_id,)).fetchone())

    def active_own_home_admission(self, agent_id):
        hexid(agent_id)
        with self._lock:
            self._check_files()
            return self._own_home_admission_record(self.conn.execute(
                "SELECT * FROM own_home_admission WHERE agent_id=? AND state IN ('reserved','unknown')",
                (agent_id,)).fetchone())

    def own_home_admission_channels(self, admission_id):
        hexid(admission_id)
        with self._lock:
            self._check_files()
            rows = self.conn.execute('SELECT * FROM own_home_admission_channel '
                'WHERE admission_id=? ORDER BY ordinal', (admission_id,)).fetchall()
            return tuple(self._own_home_channel_record(row) for row in rows)

    def own_home_admission_restart(self, admission_id):
        hexid(admission_id)
        with self._lock:
            self._check_files()
            return self._own_home_restart_link(self.conn.execute(
                'SELECT * FROM own_home_admission_restart WHERE admission_id=?',
                (admission_id,)).fetchone())

    def reserve_own_home_admission(self, *, admission_id, agent_id, snapshot_hash, scope_hash,
            protectedfiles_hash, catalog_hash, legacy_join_hash, profile_hash, env_before_hash,
            env_after_hash, agent_spec_hash, prior_channels_hash, proposed_channels_hash,
            approval_set_hash, old_process, channels, now):
        """Persist a bounded metadata snapshot; it is not source approval or admission authority."""
        for value in (admission_id, agent_id, snapshot_hash, scope_hash, protectedfiles_hash,
                      catalog_hash, legacy_join_hash, profile_hash, env_before_hash, env_after_hash,
                      agent_spec_hash, prior_channels_hash, proposed_channels_hash, approval_set_hash):
            hexid(value)
        old_process = self._restart_process(old_process)
        stamp(now)
        channels = self._own_home_channel_rows(channels)
        if self._own_home_channels_digest(channels) != proposed_channels_hash:
            raise StoreError(ERROR)
        immutable = (admission_id, agent_id, snapshot_hash, scope_hash, protectedfiles_hash,
            catalog_hash, legacy_join_hash, profile_hash, env_before_hash, env_after_hash,
            agent_spec_hash, prior_channels_hash, proposed_channels_hash, approval_set_hash,
            old_process)
        with self.transaction():
            agent = self.conn.execute('SELECT status FROM agent WHERE pubkey=?', (agent_id,)).fetchone()
            if not agent or agent['status'] != 'active':
                raise StoreError(ERROR)
            previous = self.own_home_admission(admission_id)
            if previous is not None:
                prior_immutable = (previous.admission_id, previous.agent_id, previous.snapshot_hash,
                    previous.scope_hash, previous.protectedfiles_hash, previous.catalog_hash,
                    previous.legacy_join_hash, previous.profile_hash, previous.env_before_hash,
                    previous.env_after_hash, previous.agent_spec_hash, previous.prior_channels_hash,
                    previous.proposed_channels_hash, previous.approval_set_hash, previous.old_process)
                if prior_immutable != immutable:
                    raise StoreError(ERROR)
                expected_rows = tuple(channels)
                actual_rows = tuple({key: getattr(row, key) for key in (
                    'channel_id', 'chat_id', 'chat_ref', 'source_kind', 'approval_id', 'approval_hash',
                    'mirror_pubkey', 'mirror_owner_pubkey', 'claimed_at', 'claim_event_id',
                    'policy_event_id', 'roster_event_id', 'authorization_hash')}
                    for row in self.own_home_admission_channels(admission_id))
                if actual_rows != expected_rows:
                    raise StoreError(ERROR)
                return OwnHomeAdmissionReservation(previous, False)
            if self.active_own_home_admission(agent_id) is not None:
                raise StoreError(ERROR)
            self._own_home_write_depth += 1
            try:
                self.conn.execute('''INSERT INTO own_home_admission(
                admission_id,agent_id,snapshot_hash,scope_hash,protectedfiles_hash,catalog_hash,
                legacy_join_hash,profile_hash,env_before_hash,env_after_hash,agent_spec_hash,
                prior_channels_hash,proposed_channels_hash,approval_set_hash,channel_count,
                channels_sealed,old_pid,old_start,old_invocation,state,receipt_hash,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,'reserved','',?,?)''',
                (admission_id, agent_id, snapshot_hash, scope_hash, protectedfiles_hash,
                 catalog_hash, legacy_join_hash, profile_hash, env_before_hash, env_after_hash,
                 agent_spec_hash, prior_channels_hash, proposed_channels_hash, approval_set_hash,
                 len(channels), old_process.pid, old_process.start, old_process.invocation, now, now))
                for ordinal, row in enumerate(channels):
                    self.conn.execute('''INSERT INTO own_home_admission_channel(
                    admission_id,ordinal,channel_id,chat_id,chat_ref,source_kind,approval_id,
                    approval_hash,mirror_pubkey,mirror_owner_pubkey,claimed_at,claim_event_id,
                    policy_event_id,roster_event_id,authorization_hash)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (admission_id, ordinal, row['channel_id'], row['chat_id'], row['chat_ref'],
                         row['source_kind'], row['approval_id'], row['approval_hash'], row['mirror_pubkey'],
                         row['mirror_owner_pubkey'], row['claimed_at'], row['claim_event_id'],
                         row['policy_event_id'], row['roster_event_id'], row['authorization_hash']))
                self.conn.execute('UPDATE own_home_admission SET channels_sealed=1 WHERE admission_id=?',
                                  (admission_id,))
            finally:
                self._own_home_write_depth -= 1
            record = self.own_home_admission(admission_id)
            return OwnHomeAdmissionReservation(record, True)

    def mark_own_home_admission_unknown(self, admission_id, snapshot_hash, *, now):
        hexid(admission_id); hexid(snapshot_hash); stamp(now)
        with self.transaction():
            record = self.own_home_admission(admission_id)
            agent = (self.conn.execute('SELECT status FROM agent WHERE pubkey=?',
                                      (record.agent_id,)).fetchone() if record else None)
            if (not record or record.state != 'reserved' or record.snapshot_hash != snapshot_hash
                    or now < record.updated_at or not agent or agent['status'] != 'active'):
                return False
            changed = self.conn.execute("UPDATE own_home_admission SET state='unknown',updated_at=? "
                "WHERE admission_id=? AND state='reserved' AND snapshot_hash=?",
                (max(now, record.updated_at), admission_id, snapshot_hash)).rowcount
            return changed == 1

    def link_own_home_admission_restart(self, admission_id, restart_operation_id, *, scope_hash, now):
        """Link only a restart intent reserved and marked UNKNOWN in this outer transaction."""
        hexid(admission_id); ident(restart_operation_id); hexid(scope_hash); stamp(now)
        if not self.conn.in_transaction or not self._rollback_frames:
            raise StoreError(ERROR)
        witness = self._restart_provenance.get(restart_operation_id)
        if not witness or witness[0] is not self._rollback_frames[0] or witness[1] is not True:
            return False
        with self.transaction():
            record = self.own_home_admission(admission_id)
            restart = self.restart_record(restart_operation_id)
            existing = self.own_home_admission_restart(admission_id)
            agent = (self.conn.execute('SELECT status FROM agent WHERE pubkey=?',
                                      (record.agent_id,)).fetchone() if record else None)
            if (existing or not record or record.state != 'unknown' or not restart
                    or not self._restart_matches(restart, record.agent_id, scope_hash,
                                                  record.protectedfiles_hash)
                    or restart.scope_hash != record.scope_hash or restart.state != 'unknown'
                    or restart.new_process is not None or restart.old_process != record.old_process
                    or now < record.updated_at or now < restart.updated_at
                    or not agent or agent['status'] != 'active'):
                return False
            self.conn.execute('INSERT INTO own_home_admission_restart(admission_id,'
                'restart_operation_id,scope_hash,created_at) VALUES(?,?,?,?)',
                (admission_id, restart_operation_id, scope_hash, now))
            return True

    def ack_own_home_admission(self, admission_id, *, snapshot_hash, restart_operation_id,
                               receipt_hash, now):
        hexid(admission_id); hexid(snapshot_hash); ident(restart_operation_id)
        hexid(receipt_hash); stamp(now)
        with self.transaction():
            record = self.own_home_admission(admission_id)
            link = self.own_home_admission_restart(admission_id)
            if (not record or record.snapshot_hash != snapshot_hash or not link
                    or link.restart_operation_id != restart_operation_id
                    or link.scope_hash != record.scope_hash or now < record.updated_at):
                return False
            if record.state == 'acked':
                return record.receipt_hash == receipt_hash
            if record.state != 'unknown':
                return False
            restart = self.restart_record(restart_operation_id)
            agent = self.conn.execute('SELECT status FROM agent WHERE pubkey=?',
                                      (record.agent_id,)).fetchone()
            if (not restart or restart.state != 'acked' or restart.new_process is None
                    or not self._restart_matches(restart, record.agent_id, record.scope_hash,
                                                  record.protectedfiles_hash)
                    or restart.old_process != record.old_process or now < restart.updated_at
                    or not agent or agent['status'] != 'active'):
                return False
            changed = self.conn.execute("UPDATE own_home_admission SET state='acked',receipt_hash=?,updated_at=? "
                "WHERE admission_id=? AND state='unknown' AND snapshot_hash=?",
                (receipt_hash, max(now, record.updated_at), admission_id, snapshot_hash)).rowcount
            return changed == 1

    @staticmethod
    def _console_record(row):
        if row is None:
            return None
        return ConsoleOperation(row["id"], row["principal"], row["request_key_hash"], row["kind"],
                                row["binding_id"] if row["kind"] == "binding" else row["agent_id"],
                                row["action"], row["execution_epoch"], row["status"], row["reason"],
                                row["created_at"], row["updated_at"], row["restart_operation_id"], row["receipt_hash"])

    def _console_get(self, operation_id):
        return self._console_record(self.conn.execute("SELECT * FROM console_operation WHERE id=?",
                                                      (operation_id,)).fetchone())

    @staticmethod
    def _console_scope(kind, target, action):
        if kind == "binding" and action in ("pause", "resume", "backfill"):
            ident(target)
        elif kind == "agent" and action == "restart":
            hexid(target)
        else:
            raise StoreError(ERROR)

    def _console_target_current(self, kind, target):
        if kind == "binding":
            row = self.conn.execute("SELECT status FROM binding WHERE binding_id=?", (target,)).fetchone()
            return bool(row and row["status"] != "retired")
        row = self.conn.execute("SELECT status FROM agent WHERE pubkey=?", (target,)).fetchone()
        return bool(row and row["status"] == "active")

    def console_operation(self, operation_id, principal):
        hexid(operation_id); hexid(principal)
        with self._lock:
            self._check_files()
            row = self._console_get(operation_id)
            return row if row and row.principal == principal else None

    def console_request(self, principal, request_key_hash):
        """Exact authorized retry lookup, independent of UI bounds or current target availability."""
        hexid(principal); hexid(request_key_hash)
        with self._lock:
            self._check_files()
            return self._console_record(self.conn.execute(
                "SELECT * FROM console_operation WHERE principal=? AND request_key_hash=?",
                (principal, request_key_hash)).fetchone())

    def console_operations(self, principal, *, limit=128):
        hexid(principal)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise StoreError(ERROR)
        with self._lock:
            self._check_files()
            return [self._console_record(row) for row in self.conn.execute(
                "SELECT * FROM console_operation WHERE principal=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (principal, limit))]

    def console_pending_operations(self, principal, *, after=None, limit=128):
        """Recovery pages use immutable creation ordering, independently of bounded terminal UI history."""
        hexid(principal)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise StoreError(ERROR)
        params = [principal]
        condition = ""
        if after is not None:
            if type(after) is not tuple or len(after) != 2:
                raise StoreError(ERROR)
            created_at, operation_id = after
            stamp(created_at); hexid(operation_id)
            condition = " AND (created_at>? OR (created_at=? AND id>?))"
            params.extend((created_at, created_at, operation_id))
        params.append(limit)
        with self._lock:
            self._check_files()
            return [self._console_record(row) for row in self.conn.execute(
                "SELECT * FROM console_operation WHERE principal=? AND status IN ('queued','dispatched','unknown')"
                + condition + " ORDER BY created_at,id LIMIT ?", params)]

    def reserve_console_operation(self, principal, request_key_hash, kind, target, action, *, execution_epoch, now):
        """Authenticated caller derives principal/key hashes; reused keys never dispatch, even after restart."""
        hexid(principal); hexid(request_key_hash); hexid(execution_epoch); stamp(now)
        self._console_scope(kind, target, action)
        with self.transaction():
            row = self._console_record(self.conn.execute(
                "SELECT * FROM console_operation WHERE request_key_hash=?", (request_key_hash,)).fetchone())
            if row:
                if (row.principal, row.kind, row.target, row.action) != (principal, kind, target, action):
                    raise StoreError(ERROR)
                return ConsoleReservation(row, False)
            if not self._console_target_current(kind, target):
                raise StoreError(ERROR)
            operation_id = secrets.token_hex(32)
            self.conn.execute("""INSERT INTO console_operation(id,principal,request_key_hash,kind,binding_id,agent_id,
                action,execution_epoch,status,reason,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,'queued','',?,?)""",
                (operation_id, principal, request_key_hash, kind, target if kind == "binding" else None,
                 target if kind == "agent" else None, action, execution_epoch, now, now))
            return ConsoleReservation(self._console_get(operation_id), True)

    def claim_console_operation(self, operation_id, principal, execution_epoch, *, now):
        """Caller also requires a newly-created live intent; SQL cannot prove current external authorization."""
        hexid(operation_id); hexid(principal); hexid(execution_epoch); stamp(now)
        with self.transaction():
            row = self._console_get(operation_id)
            if (not row or (row.principal, row.execution_epoch, row.status) != (principal, execution_epoch, "queued")
                    or not self._console_target_current(row.kind, row.target)):
                return False
            self.conn.execute("UPDATE console_operation SET status='dispatched',updated_at=? WHERE id=? AND status='queued'",
                              (max(now, row.updated_at), operation_id))
            return True

    def reject_console_operation(self, operation_id, principal, execution_epoch, *, reason, now):
        hexid(operation_id); hexid(principal); hexid(execution_epoch); stamp(now)
        if not isinstance(reason, str) or reason not in CONSOLE_REASONS:
            raise StoreError(ERROR)
        with self.transaction():
            row = self._console_get(operation_id)
            if not row or (row.principal, row.execution_epoch) != (principal, execution_epoch):
                return False
            if row.status == "rejected":
                return row.reason == reason
            if row.status != "queued":
                return False
            self.conn.execute("UPDATE console_operation SET status='rejected',reason=?,updated_at=? WHERE id=? AND status='queued'",
                              (reason, max(now, row.updated_at), operation_id))
            return True

    def hold_console_operation(self, operation_id, *, reason, now):
        hexid(operation_id); stamp(now)
        if not isinstance(reason, str) or reason not in CONSOLE_REASONS:
            raise StoreError(ERROR)
        with self.transaction():
            row = self._console_get(operation_id)
            if not row or row.status not in ("queued", "dispatched", "unknown"):
                return False
            self.conn.execute("UPDATE console_operation SET status='unknown',reason=?,updated_at=? WHERE id=?",
                              (reason, max(now, row.updated_at), operation_id))
            return True

    def link_console_restart(self, operation_id, restart_operation_id, *, scope_hash, protectedfiles_hash, now):
        """Link the newly reserved/marked domain intent in its ORIGINAL caller transaction, before external IO."""
        hexid(operation_id); ident(restart_operation_id); hexid(scope_hash); hexid(protectedfiles_hash); stamp(now)
        if not self.conn.in_transaction or not self._rollback_frames:
            raise StoreError(ERROR)
        with self.transaction():
            row = self._console_get(operation_id)
            restart = self.restart_record(restart_operation_id)
            if (not row or row.kind != "agent" or not restart
                    or not self._restart_matches(restart, row.target, scope_hash, protectedfiles_hash)):
                return False
            if row.restart_operation_id is not None:
                return row.restart_operation_id == restart_operation_id
            if row.status not in ("dispatched", "unknown") or restart.state != "unknown":
                return False
            witness = self._restart_provenance.get(restart_operation_id)
            if not witness or witness[0] is not self._rollback_frames[0] or not witness[1]:
                raise StoreError(ERROR)
            self.conn.execute("UPDATE console_operation SET restart_operation_id=?,updated_at=? WHERE id=? AND restart_operation_id IS NULL",
                              (restart_operation_id, max(now, row.updated_at), operation_id))
            return True

    def finish_console_operation(self, operation_id, *, expected, observed, receipt_hash, now):
        """A trusted actual driver supplies the receipt; SQLite validates metadata CAS, not external effects."""
        hexid(operation_id); hexid(receipt_hash); stamp(now)
        if expected not in ("dispatched", "unknown") or observed not in tuple(CONSOLE_OUTCOMES.values()):
            raise StoreError(ERROR)
        with self.transaction():
            row = self._console_get(operation_id)
            if not row or CONSOLE_OUTCOMES[row.action] != observed:
                return False
            if row.status == "completed":
                return row.receipt_hash == receipt_hash
            if row.status != expected:
                return False
            if row.action == "restart":
                restart = self.restart_record(row.restart_operation_id) if row.restart_operation_id else None
                if not restart or restart.agent_id != row.target or restart.state != "acked":
                    return False
            self.conn.execute("UPDATE console_operation SET status='completed',reason='',receipt_hash=?,updated_at=? WHERE id=? AND status=?",
                              (receipt_hash, max(now, row.updated_at), operation_id, expected))
            return True

    def reconcile_console_restart(self, operation_id, *, now):
        """Resolve only an associated actual domain ACK. This never invokes a restart or infers round completion."""
        hexid(operation_id); stamp(now)
        with self.transaction():
            row = self._console_get(operation_id)
            if not row or row.action != "restart" or not row.restart_operation_id:
                return False
            if row.status == "completed":
                return True
            if row.status not in ("dispatched", "unknown"):
                return False
            restart = self.restart_record(row.restart_operation_id)
            if not restart or restart.agent_id != row.target or restart.state != "acked" or restart.new_process is None:
                return False
            values = ("hostd-console-restart-receipt:v1", restart.operation_id, restart.agent_id, restart.scope_hash,
                      restart.protectedfiles_hash, str(restart.old_process.pid), str(restart.old_process.start),
                      restart.old_process.invocation, str(restart.new_process.pid), str(restart.new_process.start),
                      restart.new_process.invocation, str(restart.created_at), str(restart.updated_at))
            receipt = hashlib.sha256("\0".join(values).encode()).hexdigest()
            return self.finish_console_operation(operation_id, expected=row.status, observed="restarted",
                                                 receipt_hash=receipt, now=now)

    def reserve_delivery(self, binding_id, source_id, direction, *, agent_id="", source_at=0, now,
                         content_hash="", root_id="", stream=None):
        ident(binding_id); stamp(source_at); stamp(now); hexid(content_hash, empty=True); ident(root_id, empty=True)
        if direction == "f2r":
            parts = source_id.split("|") if isinstance(source_id, str) else []
            if len(parts) != 3 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", parts[2]):
                raise StoreError(ERROR)
            ident(parts[0], "om_"); ident(parts[1])
            if not parts[1].startswith(("ou_", "on_")):
                raise StoreError(ERROR)
        elif direction == "f2b":
            ident(source_id, "om_")
        elif direction in ("b2f", "e2f", "r2f", "intro"):
            hexid(source_id)
        elif direction == "image":
            if not re.fullmatch(r"[0-9a-f]{64}:(?:[0-9]+|thread|over)", source_id):
                raise StoreError(ERROR)
        else:
            raise StoreError(ERROR)
        hexid(agent_id, empty=True)
        stream = stream or ("feishu" if direction == "f2b" else "reaction" if direction in ("r2f", "f2r") else "relay")
        digest = hashlib.sha256(f"{binding_id}|{agent_id}|{direction}|{source_id}".encode()).hexdigest()
        with self.transaction():
            self.conn.execute("""INSERT OR IGNORE INTO delivery(id,binding_id,agent_id,source_id,direction,stream,
             root_id,content_hash,status,source_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                              (digest, binding_id, agent_id or None, source_id, direction, stream, root_id, content_hash, source_at, now, now))
            row = self.conn.execute("SELECT * FROM delivery WHERE id=?", (digest,)).fetchone()
            if row is None or (content_hash and row["content_hash"] != content_hash) or row["stream"] != stream:
                raise StoreError(ERROR)
            if row["source_at"] == 0 and source_at:
                self.conn.execute("UPDATE delivery SET source_at=? WHERE id=?", (source_at, digest))
                row = self.conn.execute("SELECT * FROM delivery WHERE id=?", (digest,)).fetchone()
            return Delivery(row["id"], row["status"], row["target_id"], row["source_at"], row["attempts"])

    def ack_delivery(self, delivery_id, target_id, *, now, advance_cursor=True):
        hexid(delivery_id); ident(target_id); stamp(now)
        if type(advance_cursor) is not bool: raise StoreError(ERROR)
        with self.transaction():
            row = self.conn.execute("SELECT * FROM delivery WHERE id=?", (delivery_id,)).fetchone()
            if row is None or (row["status"] == "acked" and row["target_id"] != target_id):
                raise StoreError(ERROR)
            if row["direction"] in ("f2b", "f2r"):
                hexid(target_id)
                if row["direction"] == "f2b" and row["content_hash"] and row["content_hash"] != target_id:
                    raise StoreError(ERROR)
            else:
                ident(target_id, "om_")
            self.conn.execute("UPDATE delivery SET status='acked',target_id=?,updated_at=? WHERE id=?", (target_id, now, delivery_id))
            if advance_cursor:
                self._advance_cursor(row["binding_id"], row["agent_id"] or "", row["stream"], now)

    def fail_delivery(self, delivery_id, *, unknown=False, now):
        hexid(delivery_id); stamp(now)
        if type(unknown) is not bool:
            raise StoreError(ERROR)
        with self.transaction():
            self.conn.execute("UPDATE delivery SET status=?,updated_at=? WHERE id=? AND status!='acked'",
                              ("unknown" if unknown else "failed", now, delivery_id))

    def retry_delivery(self, delivery_id, *, now):
        """A definite refusal may retry with the same ID; an unknown outcome cannot."""
        hexid(delivery_id); stamp(now)
        with self.transaction():
            result = self.conn.execute("UPDATE delivery SET status='pending',attempts=attempts+1,updated_at=? WHERE id=? AND status='failed'", (now, delivery_id))
            return result.rowcount == 1

    def seal_delivery_content(self, delivery_id, event_id, *, now):
        """Pin one signed F2B event ID before publishing; edited source cannot become a retry."""
        hexid(delivery_id); hexid(event_id); stamp(now)
        with self.transaction():
            row = self.conn.execute("SELECT direction,content_hash,target_id FROM delivery WHERE id=?", (delivery_id,)).fetchone()
            if (row is None or row['direction'] != 'f2b' or row['content_hash'] not in ('', event_id)
                    or row['target_id'] not in (None, event_id)):
                raise StoreError(ERROR)
            self.conn.execute("UPDATE delivery SET content_hash=?,updated_at=? WHERE id=?", (event_id, now, delivery_id))

    def settle_delivery(self, delivery_id, *, outcome, now):
        """Terminal non-delivery or removed reaction; retain any prior ACK target."""
        hexid(delivery_id); stamp(now)
        if outcome not in {"skipped", "removed", "abandoned"}:
            raise StoreError(ERROR)
        with self.transaction():
            if outcome != "removed" and self.conn.execute("SELECT 1 FROM delivery WHERE id=? AND status='acked'", (delivery_id,)).fetchone():
                raise StoreError(ERROR)
            self.conn.execute("UPDATE delivery SET status=?,updated_at=? WHERE id=?", (outcome, now, delivery_id))

    def _advance_cursor(self, binding_id, agent_id, stream, now, candidate=None):
        ack = self.conn.execute("SELECT max(source_at) FROM delivery WHERE binding_id=? AND agent_id IS ? AND stream=? AND status='acked'",
                                (binding_id, agent_id or None, stream)).fetchone()[0] or 0
        blocked = self.conn.execute("SELECT min(source_at) FROM delivery WHERE binding_id=? AND agent_id IS ? AND stream=? AND status IN ('pending','failed','unknown','waiting_receipt')",
                                    (binding_id, agent_id or None, stream)).fetchone()[0]
        position = ack if candidate is None else stamp(candidate)
        if blocked is not None:
            position = min(position, max(0, blocked - 1))
        self.conn.execute("""INSERT INTO cursor VALUES(?,?,?,?,?) ON CONFLICT(binding_id,agent_id,stream)
          DO UPDATE SET position=max(cursor.position,excluded.position),updated_at=excluded.updated_at""",
                          (binding_id, agent_id, stream, position, now))

    def cursor_position(self, binding_id, stream, *, agent_id=""):
        row = self.conn.execute("SELECT position FROM cursor WHERE binding_id=? AND agent_id=? AND stream=?", (binding_id, agent_id, stream)).fetchone()
        return row[0] if row else 0

    def replay_since(self, binding_id, stream, *, agent_id=""):
        position = self.cursor_position(binding_id, stream, agent_id=agent_id)
        blocked = self.conn.execute("SELECT min(source_at) FROM delivery WHERE binding_id=? AND agent_id IS ? AND stream=? AND status IN ('pending','failed','unknown','waiting_receipt')", (binding_id, agent_id or None, stream)).fetchone()[0]
        if blocked is not None:
            position = min(position, blocked)
        return max(0, position - OVERLAP)

    def has_replay_state(self, binding_id, stream, *, agent_id=""):
        """Whether durable replay overrides a brand-new outlet's initial baseline."""
        ident(binding_id); hexid(agent_id, empty=True)
        if stream not in ('feishu','relay','reaction','members'): raise StoreError(ERROR)
        return bool(self.conn.execute("SELECT 1 FROM cursor WHERE binding_id=? AND agent_id=? AND stream=?", (binding_id, agent_id, stream)).fetchone()
                    or self.conn.execute("SELECT 1 FROM delivery WHERE binding_id=? AND agent_id IS ? AND stream=? AND status IN ('pending','failed','unknown','waiting_receipt') LIMIT 1", (binding_id, agent_id or None, stream)).fetchone())

    def create_join(self, request_id, agent_id, owner_pubkey, app_id, chat_id, *, kind, binding_id=None, now):
        if not isinstance(request_id, str) or not re.fullmatch(r"JOIN-[0-9a-f]{8}", request_id):
            raise StoreError(ERROR)
        hexid(agent_id); hexid(owner_pubkey); ident(app_id, "cli_"); ident(chat_id, "oc_"); stamp(now)
        if binding_id is not None:
            ident(binding_id)
        with self.transaction():
            agent = self.conn.execute("SELECT owner_pubkey FROM agent WHERE pubkey=?", (agent_id,)).fetchone()
            if not agent or agent[0] != owner_pubkey:
                raise StoreError(ERROR)
            self.conn.execute("""INSERT OR IGNORE INTO join_request(request_id,agent_id,owner_pubkey,callback_app_id,
             chat_id,binding_id,kind,status,created_at,updated_at,deadline) VALUES(?,?,?,?,?,?,?,'requested',?,?,?)""",
                              (request_id, agent_id, owner_pubkey, app_id, chat_id, binding_id, kind, now, now, now + JOIN_TTL))
            row = self.join_request(request_id)
            if (row["agent_id"], row["owner_pubkey"], row["callback_app_id"], row["chat_id"], row["kind"], row["binding_id"]) != (agent_id, owner_pubkey, app_id, chat_id, kind, binding_id):
                raise StoreError(ERROR)
            return row

    def join_request(self, request_id):
        row = self.conn.execute("SELECT * FROM join_request WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def rotate_card(self, request_id, card_message_id, *, now):
        ident(card_message_id, "om_"); stamp(now)
        with self.transaction():
            row = self.join_request(request_id)
            if not row or row["status"] != "requested" or now >= row["deadline"]:
                raise StoreError(ERROR)
            self.conn.execute("UPDATE join_request SET card_message_id=?,card_generation=card_generation+1,updated_at=? WHERE request_id=?", (card_message_id, now, request_id))
            return row["card_generation"] + 1

    def join_requests(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM join_request ORDER BY created_at,request_id")]

    def delivery_record(self, delivery_id):
        """Public retry metadata only; created_at is immutable across resend attempts."""
        hexid(delivery_id)
        row = self.conn.execute("SELECT id,status,target_id,source_at,created_at,updated_at,attempts,root_id,content_hash FROM delivery WHERE id=?", (delivery_id,)).fetchone()
        return dict(row) if row else None

    def delivery_by_source(self, binding_id, source_id, direction, *, agent_id=""):
        """Find committed retry metadata without reserving a missing source."""
        ident(binding_id); hexid(agent_id, empty=True)
        if direction in ("b2f", "e2f", "r2f", "intro"):
            hexid(source_id)
        elif direction == "f2b":
            ident(source_id, "om_")
        elif direction == "f2r":
            parts = source_id.split("|") if isinstance(source_id, str) else []
            if len(parts) != 3 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", parts[2]):
                raise StoreError(ERROR)
            ident(parts[0], "om_"); ident(parts[1])
            if not parts[1].startswith(("ou_", "on_")):
                raise StoreError(ERROR)
        elif direction == "image":
            if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{64}:(?:[0-9]+|thread|over)", source_id):
                raise StoreError(ERROR)
        else:
            raise StoreError(ERROR)
        row = self.conn.execute("""SELECT id,status,target_id,source_at,created_at,updated_at,attempts,root_id,content_hash
         FROM delivery WHERE binding_id=? AND source_id=? AND direction=? AND coalesce(agent_id,'')=?""",
                                (binding_id, source_id, direction, agent_id)).fetchone()
        return dict(row) if row else None

    def ensure_effect_plan(self, request_id, channel_id, binding_id, secret_ref, config_path, *, now):
        ident(request_id); ident(binding_id); localpath(secret_ref); localpath(config_path); stamp(now)
        if not isinstance(channel_id, str) or not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", channel_id):
            raise StoreError(ERROR)
        values = (channel_id, binding_id, str(secret_ref), str(config_path))
        with self.transaction():
            row = self.effect_plan(request_id)
            if row and tuple(row[k] for k in ('channel_id','binding_id','secret_ref','config_path')) != values:
                raise StoreError(ERROR)
            self.conn.execute("INSERT OR IGNORE INTO effect_plan(request_id,channel_id,binding_id,secret_ref,config_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                              (request_id, *values, now, now))
        return self.effect_plan(request_id)

    def effect_plan(self, request_id):
        ident(request_id)
        row = self.conn.execute("SELECT * FROM effect_plan WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def join_adoption(self, request_id):
        ident(request_id)
        row = self.conn.execute('SELECT * FROM join_adoption WHERE request_id=?', (request_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def adoption_binding(binding):
        # Heartbeats are liveness, not a topology change. Every authority field,
        # including active status and original claimed_at, stays pinned.
        return {key: value for key, value in binding.items() if key != 'heartbeat_at'}

    def adopt_join_binding(self, request, plan, target, decision, *, secret_ref, proof_refs, now):
        """CAS an untouched approved plan after caller's fresh read-only proof.

        This does not grant membership, issue approval or dispatch effects. Original
        plan/card metadata stays immutable; UNKNOWN and any previous effect refuse.
        """
        ident(request['request_id']); localpath(secret_ref); stamp(now)
        if (not isinstance(proof_refs, dict) or set(proof_refs) != {'checked_at', 'events', 'files_hash', 'config_hash', 'preparation_hash'}
            or type(proof_refs['checked_at']) is not int or not 0 <= now - proof_refs['checked_at'] <= 300
            or not isinstance(proof_refs['events'], list) or not 1 <= len(proof_refs['events']) <= 1024):
            raise StoreError(ERROR)
        for value in proof_refs['events'] + [proof_refs[k] for k in ('files_hash', 'config_hash', 'preparation_hash')]: hexid(value)
        with self.transaction():
            current = self.join_request(request['request_id'])
            saved = self.effect_plan(request['request_id'])
            actual_decision = self.card_approval_decision(request['request_id'])
            bindings = [b for b in self.bindings() if b['chat_id'] == request['chat_id'] and b['status'] != 'retired']
            agent = self.conn.execute('SELECT * FROM agent WHERE pubkey=?', (request['agent_id'],)).fetchone()
            if (current != request or saved != plan or not actual_decision or actual_decision != decision
                or current['status'] != 'approved' or current['kind'] != 'new_binding' or current['binding_id'] is not None
                or plan['binding_id'] != current['request_id'] or plan['mirror_pubkey']
                or not agent or agent['status'] != 'active' or agent['config_path'] not in (None, secret_ref)
                or agent['owner_pubkey'] != current['owner_pubkey'] or agent['app_id'] != current['callback_app_id']
                or len(bindings) != 1 or bindings[0]['status'] != 'active'
                or self.adoption_binding(bindings[0]) != self.adoption_binding(target)
                or target['binding_id'] == plan['binding_id']
                or self.conn.execute('SELECT 1 FROM console_pause WHERE binding_id=?', (target['binding_id'],)).fetchone()
                or self.effect_steps(current['request_id']) or self.join_adoption(current['request_id'])
                or self.conn.execute('SELECT 1 FROM binding WHERE binding_id=?', (plan['binding_id'],)).fetchone()
                or self.conn.execute('SELECT 1 FROM agent_chat WHERE agent_id=? AND chat_id=?',
                                     (current['agent_id'], current['chat_id'])).fetchone()
                or self.conn.execute('SELECT 1 FROM approval_publication WHERE request_id=?', (current['request_id'],)).fetchone()):
                raise StoreError(ERROR)
            dump = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'))
            self.conn.execute('INSERT INTO join_adoption VALUES(?,?,?,?,?,?,?,?)',
                (current['request_id'], dump(current), dump(plan), dump(self.adoption_binding(target)),
                 self._approval_digest(asdict(actual_decision)), self._approval_digest(proof_refs), dump(proof_refs), now))
            self.conn.execute("UPDATE join_request SET kind='channel',binding_id=?,updated_at=? WHERE request_id=?",
                              (target['binding_id'], now, current['request_id']))
            self.conn.execute('UPDATE effect_plan SET channel_id=?,binding_id=?,secret_ref=?,config_path=?,updated_at=? WHERE request_id=?',
                (target['channel_id'], target['binding_id'], secret_ref, target['config_path'], now, current['request_id']))
        return self.join_request(request['request_id']), self.effect_plan(request['request_id'])

    def set_effect_mirror(self, request_id, mirror_pubkey, *, now):
        ident(request_id); hexid(mirror_pubkey); stamp(now)
        with self.transaction():
            row = self.effect_plan(request_id)
            if not row or row['mirror_pubkey'] not in ('', mirror_pubkey):
                raise StoreError(ERROR)
            self.conn.execute("UPDATE effect_plan SET mirror_pubkey=?,updated_at=? WHERE request_id=?", (mirror_pubkey, now, request_id))

    def activate_effect_binding(self, request_id, *, now):
        """Caller already verified registrar, claim, roster and runtime independently."""
        ident(request_id); stamp(now)
        with self.transaction():
            row = self.conn.execute("""SELECT b.binding_id,j.agent_id,j.chat_id FROM effect_plan p
             JOIN join_request j ON j.request_id=p.request_id JOIN binding b ON b.binding_id=p.binding_id
             JOIN effect_step s ON s.request_id=p.request_id AND s.step='registrar' AND s.status='verified'
             WHERE p.request_id=? AND b.channel_id=p.channel_id AND b.chat_id=j.chat_id
             AND b.sync_app_id=j.callback_app_id AND b.mirror_pubkey=p.mirror_pubkey AND b.config_path=p.config_path
             AND b.status IN ('pending','active') AND j.status='approved'""", (request_id,)).fetchone()
            if not row: raise StoreError(ERROR)
            self.conn.execute("UPDATE binding SET status='active',heartbeat_at=? WHERE binding_id=?", (now,row['binding_id']))
            self.record_agent_chat(row['agent_id'],row['chat_id'], self.conn.execute("SELECT chat_ref FROM binding WHERE binding_id=?",(row['binding_id'],)).fetchone()[0],binding_id=row['binding_id'],status='active',now=now)

    def effect_steps(self, request_id):
        ident(request_id)
        return [dict(row) for row in self.conn.execute("SELECT * FROM effect_step WHERE request_id=? ORDER BY step", (request_id,))]

    def reserve_effect_step(self, request_id, step, intent_hash, *, now, operation_at=None):
        ident(request_id); hexid(intent_hash); stamp(now)
        if operation_at is not None: stamp(operation_at)
        if step not in EFFECT_STEPS:
            raise StoreError(ERROR)
        with self.transaction():
            row = self.conn.execute("SELECT * FROM effect_step WHERE request_id=? AND step=?", (request_id, step)).fetchone()
            if row:
                if row['intent_hash'] != intent_hash or (operation_at is not None and row['operation_at'] != operation_at):
                    raise StoreError(ERROR)
                if row['status'] == 'verified' or row['lease_until'] > now:
                    return False
                self.conn.execute("UPDATE effect_step SET status='reserved',lease_until=?,attempts=attempts+1,updated_at=? WHERE request_id=? AND step=?", (now + 60, now, request_id, step))
            else:
                self.conn.execute("INSERT INTO effect_step VALUES(?,?,'reserved',?,'','',?,1,?,?)", (request_id, step, intent_hash, now + 60, now if operation_at is None else operation_at, now))
            return True

    def finish_effect_step(self, request_id, step, intent_hash, observed_hash, output_id='', *, now):
        ident(request_id); hexid(intent_hash); hexid(observed_hash); ident(output_id, empty=True); stamp(now)
        if step not in EFFECT_STEPS:
            raise StoreError(ERROR)
        with self.transaction():
            changed = self.conn.execute("UPDATE effect_step SET status='verified',observed_hash=?,output_id=?,lease_until=0,updated_at=? WHERE request_id=? AND step=? AND intent_hash=?",
                                        (observed_hash, output_id, now, request_id, step, intent_hash)).rowcount
            if not changed:
                raise StoreError(ERROR)

    def defer_effect_step(self, request_id, step, *, status, now):
        ident(request_id); stamp(now)
        if step not in EFFECT_STEPS or status not in ('unknown','waiting_idle','failed'):
            raise StoreError(ERROR)
        with self.transaction():
            if not self.conn.execute("UPDATE effect_step SET status=?,lease_until=0,updated_at=? WHERE request_id=? AND step=? AND status!='verified'", (status, now, request_id, step)).rowcount:
                raise StoreError(ERROR)

    def outlet_receipt(self, delivery_id):
        hexid(delivery_id)
        row = self.conn.execute("SELECT * FROM outlet_receipt WHERE delivery_id=?", (delivery_id,)).fetchone()
        return dict(row) if row else None

    def ack_outlet_delivery(self, delivery_id, target_message_id, sender_app_id, *, reaction_id="", emoji="", now, advance_cursor=True):
        hexid(delivery_id); ident(target_message_id, "om_"); ident(sender_app_id, "cli_"); stamp(now)
        if reaction_id:
            if not isinstance(reaction_id, str) or not re.fullmatch(r"(?:[A-Za-z0-9_.:-]{1,256}|[A-Za-z0-9_-]{85}[AQgw]==)", reaction_id):
                raise StoreError(ERROR)
        if emoji not in ("", "GLANCE", "Typing", "DONE", "THUMBSUP", "OK", "THANKS", "MUSCLE", "CrossMark") or bool(reaction_id) != bool(emoji):
            raise StoreError(ERROR)
        with self.transaction():
            row = self.conn.execute("""SELECT d.direction,d.agent_id,b.chat_id FROM delivery d
             JOIN binding b ON b.binding_id=d.binding_id JOIN agent a ON a.pubkey=d.agent_id
             JOIN agent_chat ac ON ac.agent_id=d.agent_id AND ac.binding_id=d.binding_id AND ac.chat_id=b.chat_id
             WHERE d.id=? AND a.app_id=? AND a.status='active' AND ac.status='active' AND b.status!='retired'""", (delivery_id, sender_app_id)).fetchone()
            if not row or row["direction"] not in ("b2f", "e2f", "r2f") or (row["direction"] == "r2f") != bool(reaction_id):
                raise StoreError(ERROR)
            existing = self.outlet_receipt(delivery_id)
            values = (delivery_id, sender_app_id, target_message_id, reaction_id, emoji)
            if existing and tuple(existing.values()) != values:
                raise StoreError(ERROR)
            self.conn.execute("INSERT OR IGNORE INTO outlet_receipt VALUES(?,?,?,?,?)", values)
            self.ack_delivery(delivery_id, target_message_id, now=now, advance_cursor=advance_cursor)
            return existing is None

    def enqueue_target(self, binding_id, app_id, message_id, root_id, event_type, *, now):
        ident(binding_id); ident(app_id, "cli_"); ident(message_id, "om_"); ident(root_id, "om_", empty=True); stamp(now)
        if event_type not in TARGET_EVENT_TYPES:
            raise StoreError(ERROR)
        with self.transaction():
            binding = self.conn.execute("SELECT sync_app_id,status FROM binding WHERE binding_id=?", (binding_id,)).fetchone()
            if not binding or binding["sync_app_id"] != app_id or binding["status"] == "retired":
                raise StoreError(ERROR)
            prior = self.conn.execute("SELECT root_id FROM event_target WHERE binding_id=? AND app_id=? AND message_id=? AND event_type=?", (binding_id, app_id, message_id, event_type)).fetchone()
            if prior and prior["root_id"] != root_id:
                raise StoreError(ERROR)
            return self.conn.execute("INSERT OR IGNORE INTO event_target VALUES(?,?,?,?,?,?)", (binding_id, app_id, message_id, root_id, event_type, now)).rowcount == 1

    def pending_targets(self, binding_id, *, limit=256):
        ident(binding_id)
        if type(limit) is not int or not 1 <= limit <= 256:
            raise StoreError(ERROR)
        return [dict(row) for row in self.conn.execute("SELECT * FROM event_target WHERE binding_id=? ORDER BY first_seen_at,app_id,message_id,event_type LIMIT ?", (binding_id, limit))]

    def ack_target(self, binding_id, app_id, message_id, event_type):
        ident(binding_id); ident(app_id, "cli_"); ident(message_id, "om_")
        if event_type not in TARGET_EVENT_TYPES:
            raise StoreError(ERROR)
        with self.transaction():
            return self.conn.execute("DELETE FROM event_target WHERE binding_id=? AND app_id=? AND message_id=? AND event_type=?", (binding_id, app_id, message_id, event_type)).rowcount == 1

    def join_transport(self, request_id):
        ident(request_id)
        row = self.conn.execute("SELECT * FROM join_transport WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def reserve_join_card(self, request_id, *, now):
        """One writer per minute; ambiguous attempts reuse the original generation/UUID."""
        ident(request_id); stamp(now)
        with self.transaction():
            request = self.join_request(request_id)
            if not request or request["status"] != "requested" or now >= request["deadline"]:
                return None
            self.conn.execute("INSERT OR IGNORE INTO join_transport(request_id,updated_at) VALUES(?,?)", (request_id, now))
            row = self.join_transport(request_id)
            if now < row["next_due"] or now < row["lease_until"] or now < row["updated_at"]:
                return None
            generation = row["send_generation"] if row["send_status"] in ("reserved", "unknown", "failed") else request["card_generation"] + 1
            attempts = row["attempts"] + 1
            delay = min(86400, 300 * (2 ** min(attempts - 1, 9)))
            self.conn.execute("""UPDATE join_transport SET attempts=?,next_due=?,send_generation=?,
              send_status='reserved',lease_until=?,pending_message_id=NULL,updated_at=? WHERE request_id=?""",
                              (attempts, now + delay, generation, now + 60, now, request_id))
            return generation

    def finish_join_card(self, request_id, generation, message_id, *, now):
        ident(request_id); stamp(generation); ident(message_id, "om_"); stamp(now)
        with self.transaction():
            request, row = self.join_request(request_id), self.join_transport(request_id)
            if not request or not row or request["status"] != "requested" or now >= request["deadline"] or now < row["updated_at"]:
                return False
            if row["send_status"] != "reserved" or row["send_generation"] != generation or generation != request["card_generation"] + 1:
                return False
            self.conn.execute("UPDATE join_request SET card_message_id=?,card_generation=?,updated_at=? WHERE request_id=?", (message_id, generation, now, request_id))
            self.conn.execute("UPDATE join_transport SET send_status='sent',lease_until=0,pending_message_id=?,updated_at=? WHERE request_id=?", (message_id, now, request_id))
            if request["card_message_id"]:
                self.conn.execute("""INSERT INTO join_notice(request_id,message_id,state,status,updated_at)
                 VALUES(?,?,'superseded','pending',?) ON CONFLICT(request_id,message_id) DO UPDATE SET
                 state='superseded',status='pending',next_due=0,lease_until=0,updated_at=excluded.updated_at""", (request_id, request["card_message_id"], now))
            return True

    def fail_join_card(self, request_id, generation, *, definite, now):
        ident(request_id); stamp(generation); stamp(now)
        if type(definite) is not bool:
            raise StoreError(ERROR)
        with self.transaction():
            row = self.join_transport(request_id)
            if not row or row["send_status"] != "reserved" or row["send_generation"] != generation or now < row["updated_at"]:
                return False
            self.conn.execute("UPDATE join_transport SET send_status=?,lease_until=0,updated_at=? WHERE request_id=?", ("failed" if definite else "unknown", now, request_id))
            return True

    def reserve_join_dm(self, request_id, *, now):
        ident(request_id); stamp(now)
        with self.transaction():
            request = self.join_request(request_id)
            if not request or request["status"] != "requested" or now >= request["deadline"]:
                return False
            self.conn.execute("INSERT OR IGNORE INTO join_transport(request_id,updated_at) VALUES(?,?)", (request_id, now))
            return self.conn.execute("UPDATE join_transport SET dm_reserved=1,updated_at=? WHERE request_id=? AND dm_reserved=0 AND updated_at<=?", (now, request_id, now)).rowcount == 1

    def reserve_join_feedback(self, request_id, app_id, event_id, union_id, *, reason, now):
        ident(request_id); ident(app_id, "cli_"); ident(event_id); ident(union_id, "on_"); stamp(now)
        if reason not in ("owner", "identity", "card", "expired"):
            raise StoreError(ERROR)
        with self.transaction():
            row = self.join_request(request_id)
            if not row or row["callback_app_id"] != app_id:
                return False
            return self.conn.execute("INSERT OR IGNORE INTO join_feedback VALUES(?,?,?,?,?,'reserved',?)", (app_id, event_id, request_id, union_id, reason, now)).rowcount == 1

    def finish_join_feedback(self, app_id, event_id, *, sent, now):
        ident(app_id, "cli_"); ident(event_id); stamp(now)
        if type(sent) is not bool:
            raise StoreError(ERROR)
        with self.transaction():
            return self.conn.execute("UPDATE join_feedback SET status=?,updated_at=? WHERE app_id=? AND event_id=? AND status='reserved' AND updated_at<=?", ("sent" if sent else "unknown", now, app_id, event_id, now)).rowcount == 1

    def queue_join_notice(self, request_id, message_id, state, *, now):
        ident(request_id); ident(message_id, "om_"); stamp(now)
        expected = {"approved": ("approved",), "blocked": ("approved", "applied"), "done": ("done",), "denied": ("denied",), "expired": ("expired",)}
        if state not in expected:
            raise StoreError(ERROR)
        with self.transaction():
            row = self.join_request(request_id)
            if not row or row["card_message_id"] != message_id or row["status"] not in expected[state] or now < row["updated_at"]:
                return False
            self.conn.execute("""INSERT INTO join_notice(request_id,message_id,state,status,updated_at)
             VALUES(?,?,?,'pending',?) ON CONFLICT(request_id,message_id) DO UPDATE SET state=excluded.state,
             status='pending',next_due=0,lease_until=0,updated_at=excluded.updated_at
             WHERE join_notice.state!=excluded.state AND join_notice.updated_at<=excluded.updated_at""", (request_id, message_id, state, now))
            return True

    def join_notices(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM join_notice WHERE status IN ('pending','reserved') ORDER BY updated_at")]

    def reserve_join_notice(self, request_id, message_id, *, now):
        ident(request_id); ident(message_id, "om_"); stamp(now)
        with self.transaction():
            row = self.conn.execute("SELECT * FROM join_notice WHERE request_id=? AND message_id=?", (request_id, message_id)).fetchone()
            if not row or row["status"] not in ("pending", "reserved") or now < max(row["next_due"], row["lease_until"], row["updated_at"]):
                return None
            attempts = row["attempts"] + 1
            self.conn.execute("UPDATE join_notice SET status='reserved',attempts=?,next_due=?,lease_until=?,updated_at=? WHERE request_id=? AND message_id=?", (attempts, now + min(86400, 300 * (2 ** min(attempts - 1, 9))), now + 60, now, request_id, message_id))
            return row["state"]

    def finish_join_notice(self, request_id, message_id, state, *, sent, now):
        ident(request_id); ident(message_id, "om_"); stamp(now)
        if type(sent) is not bool:
            raise StoreError(ERROR)
        with self.transaction():
            return self.conn.execute("UPDATE join_notice SET status=?,lease_until=0,updated_at=? WHERE request_id=? AND message_id=? AND state=? AND status='reserved' AND updated_at<=?", ("sent" if sent else "pending", now, request_id, message_id, state, now)).rowcount == 1

    def decide_join(self, request_id, event_id, owner_pubkey, app_id, card_message_id, generation, *, approved, now):
        if self.fallback_provenance(request_id) is not None:
            return False
        ident(event_id); hexid(owner_pubkey); ident(app_id, "cli_"); ident(card_message_id, "om_"); stamp(generation); stamp(now)
        if type(approved) is not bool:
            raise StoreError(ERROR)
        with self.transaction():
            row = self.join_request(request_id)
            if not row or row["status"] != "requested":
                return False
            if now >= row["deadline"]:
                self.conn.execute("UPDATE join_request SET status='expired',updated_at=? WHERE request_id=?", (now, request_id))
                return False
            if (row["owner_pubkey"], row["callback_app_id"], row["card_message_id"], row["card_generation"]) != (owner_pubkey, app_id, card_message_id, generation):
                return False
            if self.conn.execute("SELECT 1 FROM join_decision WHERE app_id=? AND event_id=?", (app_id, event_id)).fetchone():
                return False
            if self.conn.execute("SELECT 1 FROM join_feedback WHERE app_id=? AND event_id=?", (app_id, event_id)).fetchone():
                return False
            self.conn.execute("INSERT INTO join_decision VALUES(?,?,?,?)", (app_id, event_id, request_id, now))
            self.conn.execute("UPDATE join_request SET status=?,updated_at=? WHERE request_id=? AND status='requested'", ("approved" if approved else "denied", now, request_id))
            return True

    def advance_join(self, request_id, *, expected, target, now):
        if self.fallback_provenance(request_id) is not None:
            return False
        stamp(now)
        if (expected, target) not in (("approved", "applied"), ("applied", "done"), ("requested", "expired")):
            return False
        with self.transaction():
            row = self.join_request(request_id)
            if not row or row["status"] != expected or (target == "expired" and now < row["deadline"]):
                return False
            self.conn.execute("UPDATE join_request SET status=?,updated_at=? WHERE request_id=? AND status=?", (target, now, request_id, expected))
            return True

    def record_agent_chat(self, agent_id, chat_id, chat_ref, *, binding_id=None, status="active", now):
        hexid(agent_id); ident(chat_id, "oc_"); hexid(chat_ref); stamp(now)
        with self.transaction():
            if binding_id is not None:
                row = self.conn.execute("SELECT chat_id,chat_ref FROM binding WHERE binding_id=?", (binding_id,)).fetchone()
                if not row or row["chat_id"] != chat_id or (row["chat_ref"] and row["chat_ref"] != chat_ref):
                    raise StoreError(ERROR)
            self.conn.execute("INSERT INTO agent_chat VALUES(?,?,?,?,?,?) ON CONFLICT(agent_id,chat_id) DO UPDATE SET chat_ref=excluded.chat_ref,binding_id=excluded.binding_id,status=excluded.status,updated_at=excluded.updated_at", (agent_id, chat_id, chat_ref, binding_id, status, now))

    def record_connection(self, kind, identity, *, binding_id=None, status, error_code="", now):
        ident(identity); stamp(now)
        with self.transaction():
            old = self.conn.execute("SELECT * FROM connection WHERE kind=? AND identity=?", (kind, identity)).fetchone()
            reconnects = (old["reconnects"] if old else 0) + int(status == "connected" and old is not None and old["status"] != "connected")
            connected = now if status == "connected" else old["connected_at"] if old else 0
            self.conn.execute("INSERT INTO connection VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,identity) DO UPDATE SET binding_id=excluded.binding_id,status=excluded.status,connected_at=excluded.connected_at,reconnects=excluded.reconnects,error_code=excluded.error_code,updated_at=excluded.updated_at", (kind, identity, binding_id, status, connected, old["last_event_at"] if old else 0, reconnects, error_code, now))

    @staticmethod
    def _approval_digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

    def card_approval_decision(self, request_id):
        """First current approved owner card facts, never a supplied callback DTO."""
        ident(request_id)
        request = self.join_request(request_id)
        decisions = self.conn.execute('SELECT * FROM join_decision WHERE request_id=?', (request_id,)).fetchall()
        if not request or request['status'] not in ('approved', 'applied', 'done') or len(decisions) != 1:
            return None
        agent = self.conn.execute('SELECT * FROM agent WHERE pubkey=?', (request['agent_id'],)).fetchone()
        decision = decisions[0]
        if (not agent or agent['status'] != 'active'
                or (agent['owner_pubkey'], agent['app_id']) != (request['owner_pubkey'], request['callback_app_id'])
                or decision['app_id'] != request['callback_app_id']):
            return None
        try:
            hexid(request['agent_id']); hexid(request['owner_pubkey']); ident(request['callback_app_id'], 'cli_')
            ident(request['card_message_id'], 'om_'); ident(decision['event_id']); ident(request['chat_id'], 'oc_')
            for field in ('created_at', 'deadline', 'card_generation'): stamp(request[field])
            stamp(decision['created_at'])
            if (request['card_generation'] < 1 or request['deadline'] != request['created_at'] + JOIN_TTL
                    or not request['created_at'] <= decision['created_at'] < request['deadline']):
                return None
            return CardApprovalDecision(request_id, request['agent_id'], request['owner_pubkey'], request['callback_app_id'],
                hashlib.sha256(('buzz-feishu-chat:v1:' + request['chat_id']).encode()).hexdigest(),
                request['created_at'], request['deadline'], request['card_generation'],
                hashlib.sha256(request['card_message_id'].encode()).hexdigest(),
                hashlib.sha256(decision['event_id'].encode()).hexdigest(), decision['created_at'])
        except Exception:
            return None

    @staticmethod
    def _approval_body(pin):
        keys = ('request_id', 'agent_pubkey', 'agent_owner_pubkey', 'app_id', 'channel_id', 'chat_ref',
                'mirror_pubkey', 'mirror_owner_pubkey', 'claimed_at', 'request_created_at', 'request_deadline',
                'card_generation', 'card_message_sha256', 'decision_event_sha256', 'decision_at')
        return dict(version=1, decision='approve', **{key: getattr(pin, key) for key in keys})

    @classmethod
    def _approval_event(cls, pin):
        """Reconstruct the pinned public event, without re-signing or body storage."""
        content = json.dumps(cls._approval_body(pin), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        tags = [['t', 'hostd-card-approval-v1'], ['h', pin.channel_id], ['p', pin.agent_pubkey],
                ['d', 'hostd-card-approval-v1:' + pin.content_hash]]
        return dict(id=pin.event_id, pubkey=pin.mirror_pubkey, kind=30078, tags=tags,
                    content=content, created_at=pin.event_created_at, sig=pin.signature)

    @classmethod
    def _approval_pin(cls, pin):
        if type(pin) is not ApprovalPublicationPin:
            raise StoreError(ERROR)
        ident(pin.request_id); ident(pin.binding_id); ident(pin.app_id, 'cli_')
        if not re.fullmatch('JOIN-[0-9a-f]{8}', pin.request_id) or not isinstance(pin.channel_id, str) or not re.fullmatch(
                '[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', pin.channel_id): raise StoreError(ERROR)
        for key in ('agent_pubkey', 'agent_owner_pubkey', 'chat_ref', 'mirror_pubkey', 'mirror_owner_pubkey',
                    'card_message_sha256', 'decision_event_sha256', 'content_hash', 'event_id',
                    'scope_hash', 'protectedfiles_hash', 'plan_hash'): hexid(getattr(pin, key))
        for key in ('claimed_at', 'request_created_at', 'request_deadline', 'card_generation', 'decision_at',
                    'event_created_at'): stamp(getattr(pin, key))
        if (not isinstance(pin.signature, str) or not re.fullmatch('[0-9a-f]{128}', pin.signature)
                or pin.card_generation < 1 or pin.request_deadline != pin.request_created_at + JOIN_TTL
                or not pin.request_created_at <= pin.decision_at < pin.request_deadline
                or not pin.claimed_at <= pin.decision_at <= pin.event_created_at
                or pin.agent_pubkey == pin.agent_owner_pubkey or pin.mirror_pubkey == pin.mirror_owner_pubkey):
            raise StoreError(ERROR)
        scope = {key: getattr(pin, key) for key in ('binding_id', 'agent_pubkey', 'agent_owner_pubkey', 'app_id',
                 'channel_id', 'chat_ref', 'mirror_pubkey', 'mirror_owner_pubkey', 'claimed_at')}
        event = cls._approval_event(pin)
        serial = [0, pin.mirror_pubkey, pin.event_created_at, 30078, event['tags'], event['content']]
        if (cls._approval_digest(scope) != pin.scope_hash
                or hashlib.sha256(event['content'].encode()).hexdigest() != pin.content_hash
                or hashlib.sha256(json.dumps(serial, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() != pin.event_id):
            raise StoreError(ERROR)
        return pin

    def _approval_current(self, pin):
        decision = self.card_approval_decision(pin.request_id)
        plan = self.effect_plan(pin.request_id)
        request = self.join_request(pin.request_id)
        binding = self.conn.execute('SELECT * FROM binding WHERE binding_id=?', (pin.binding_id,)).fetchone()
        agent = self.conn.execute('SELECT config_path FROM agent WHERE pubkey=?', (pin.agent_pubkey,)).fetchone()
        # An existing-channel plan reserves the approved local Agent env, not
        # the already-owned mirror. Only a new-binding plan reserves mirror.env.
        plan_scope = bool(plan and request and agent and agent['config_path'] and (
            (request['kind'] == 'channel' and plan['mirror_pubkey'] == ''
             and plan['secret_ref'] == agent['config_path'] and request['binding_id'] == pin.binding_id)
            or (request['kind'] == 'new_binding' and plan['mirror_pubkey'] == pin.mirror_pubkey
                and plan['binding_id'] == pin.request_id)))
        return bool(decision and plan_scope and binding
            and all(getattr(decision, key) == getattr(pin, key) for key in CardApprovalDecision.__dataclass_fields__)
            and binding['status'] in ('pending', 'active')
            and (binding['channel_id'], binding['mirror_pubkey'], binding['chat_id']) ==
                (pin.channel_id, pin.mirror_pubkey, request['chat_id'])
            and (not binding['chat_ref'] or binding['chat_ref'] == pin.chat_ref)
            and (plan['channel_id'], plan['binding_id'], plan['config_path']) ==
                (pin.channel_id, pin.binding_id, binding['config_path'])
            and self._approval_digest(plan) == pin.plan_hash)

    def approval_publication(self, request_id):
        ident(request_id)
        row = self.conn.execute('SELECT * FROM approval_publication WHERE request_id=?', (request_id,)).fetchone()
        if not row: return None
        pin = ApprovalPublicationPin(**{key: row[key] for key in ApprovalPublicationPin.__dataclass_fields__})
        self._approval_pin(pin)
        return ApprovalPublicationRecord(pin, row['state'], row['observed_hash'], row['created_at'], row['updated_at'])

    def reserve_approval_publication(self, pin, *, now):
        """One immutable public event. Existing rows never lease or re-pin."""
        pin = self._approval_pin(pin); stamp(now)
        with self.transaction():
            if pin.event_created_at > now + 900 or not self._approval_current(pin): raise StoreError(ERROR)
            previous = self.approval_publication(pin.request_id)
            if previous:
                if previous.pin != pin or now < previous.created_at: raise StoreError(ERROR)
                return ApprovalPublicationReservation(previous, False)
            keys = tuple(ApprovalPublicationPin.__dataclass_fields__)
            self.conn.execute(f'INSERT INTO approval_publication({",".join(keys)},state,created_at,updated_at) '
                              f'VALUES({",".join("?" for _ in keys)},\'reserved\',?,?)',
                              tuple(getattr(pin, key) for key in keys) + (now, now))
            return ApprovalPublicationReservation(self.approval_publication(pin.request_id), True)

    def mark_approval_unknown(self, request_id, event_id, *, now):
        ident(request_id); hexid(event_id); stamp(now)
        with self.transaction():
            record = self.approval_publication(request_id)
            if (not record or record.state != 'reserved' or record.pin.event_id != event_id
                    or now < record.updated_at or not self._approval_current(record.pin)): return False
            self.conn.execute("UPDATE approval_publication SET state='unknown',updated_at=? WHERE request_id=?", (now, request_id))
            return True

    def ack_approval_publication(self, request_id, event_id, observed_hash, *, now):
        """Internal trusted caller must first prove exact signed GET/current scope."""
        ident(request_id); hexid(event_id); hexid(observed_hash); stamp(now)
        with self.transaction():
            record = self.approval_publication(request_id)
            if (not record or record.pin.event_id != event_id or now < record.updated_at
                    or not self._approval_current(record.pin)): return False
            if record.state == 'acked': return record.observed_hash == observed_hash
            if record.state != 'unknown': return False
            self.conn.execute("UPDATE approval_publication SET state='acked',observed_hash=?,updated_at=? WHERE request_id=?",
                              (observed_hash, now, request_id))
            return True
    @staticmethod
    def _fallback_scope(evidence):
        return {name: getattr(evidence, name) for name in (
            'subject_pubkey','subject_owner_pubkey','subject_app_id','issuer_app_id','binding_id',
            'channel_id','chat_ref','mirror_pubkey','mirror_owner_pubkey','claimed_at','catalog_hash')}

    def _fallback_evidence(self, evidence, now):
        if type(evidence) is not FallbackRequestEvidence:
            raise StoreError(ERROR)
        stamp(now); stamp(evidence.checked_at); stamp(evidence.valid_until); stamp(evidence.claimed_at)
        for name in ('subject_pubkey','subject_owner_pubkey','chat_ref','mirror_pubkey','mirror_owner_pubkey','catalog_hash'):
            hexid(getattr(evidence, name))
        ident(evidence.subject_app_id, 'cli_'); ident(evidence.issuer_app_id, 'cli_'); ident(evidence.binding_id)
        if (not isinstance(evidence.channel_id, str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', evidence.channel_id)
                or evidence.subject_app_id == evidence.issuer_app_id
                or not evidence.claimed_at <= evidence.checked_at <= now < evidence.valid_until <= evidence.checked_at + 30
                or type(evidence.proof_hashes) is not tuple or not 1 <= len(evidence.proof_hashes) <= 256):
            raise StoreError(ERROR)
        for value in evidence.proof_hashes: hexid(value)
        row = self.conn.execute('SELECT * FROM binding WHERE binding_id=?', (evidence.binding_id,)).fetchone()
        if (not row or row['status'] != 'active' or
                (row['sync_app_id'],row['channel_id'],row['mirror_pubkey']) !=
                (evidence.issuer_app_id,evidence.channel_id,evidence.mirror_pubkey)
                or hashlib.sha256(('buzz-feishu-chat:v1:'+row['chat_id']).encode()).hexdigest() != evidence.chat_ref
                or row['chat_ref'] not in ('', evidence.chat_ref)):
            raise StoreError(ERROR)
        return row

    def fallback_provenance(self, request_id):
        ident(request_id)
        row = self.conn.execute('SELECT * FROM fallback_request WHERE request_id=?', (request_id,)).fetchone()
        if not row: return None
        values = dict(row); values['proof_hashes'] = tuple(json.loads(values['proof_hashes']))
        return FallbackProvenance(**values)

    def _fallback_current(self, request_id, evidence, now):
        binding = self._fallback_evidence(evidence, now)
        marker, row = self.fallback_provenance(request_id), self.join_request(request_id)
        if not marker or not row or self._fallback_scope(marker) != self._fallback_scope(evidence):
            raise StoreError(ERROR)
        agent = self.conn.execute('SELECT * FROM agent WHERE pubkey=?', (marker.subject_pubkey,)).fetchone()
        if (not agent or (agent['owner_pubkey'],agent['app_id'],agent['config_path'],agent['status']) !=
                (marker.subject_owner_pubkey,marker.subject_app_id,None,'paused') or
                (row['agent_id'],row['owner_pubkey'],row['callback_app_id'],row['binding_id'],row['chat_id'],row['kind']) !=
                (marker.subject_pubkey,marker.subject_owner_pubkey,marker.issuer_app_id,marker.binding_id,binding['chat_id'],'channel')):
            raise StoreError(ERROR)
        return row, marker

    def reserve_fallback_join(self, request_id, evidence, *, now):
        if not isinstance(request_id, str) or not re.fullmatch(r'JOIN-[0-9a-f]{8}', request_id):
            raise StoreError(ERROR)
        with self.transaction():
            binding = self._fallback_evidence(evidence, now)
            prior = self.conn.execute('SELECT request_id FROM fallback_request WHERE subject_pubkey=?', (evidence.subject_pubkey,)).fetchone()
            if prior:
                row, _ = self._fallback_current(prior[0], evidence, now)
                if row['status'] not in ('requested','approved') or now >= row['deadline']:
                    raise StoreError(ERROR)
                return row
            # Never adopt or rewrite an unmarked row, including paused NULL metadata.
            if self.conn.execute('SELECT 1 FROM agent WHERE pubkey=?', (evidence.subject_pubkey,)).fetchone() or self.join_request(request_id):
                raise StoreError(ERROR)
            self.conn.execute('INSERT INTO agent VALUES(?,?,?,?,?,?)',
                (evidence.subject_pubkey,evidence.subject_owner_pubkey,evidence.subject_app_id,None,'paused',now))
            row = self.create_join(request_id,evidence.subject_pubkey,evidence.subject_owner_pubkey,evidence.issuer_app_id,
                binding['chat_id'],kind='channel',binding_id=evidence.binding_id,now=now)
            values = asdict(evidence); values['proof_hashes'] = json.dumps(evidence.proof_hashes,separators=(',',':'))
            values.update(request_id=request_id,scope_hash=self._approval_digest(self._fallback_scope(evidence)))
            self.conn.execute('INSERT INTO fallback_request('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
            return row

    def reserve_fallback_card(self, request_id, evidence, *, now):
        """UNKNOWN commits before first physical POST. Existing attempts are GET-only."""
        with self.transaction():
            row, _ = self._fallback_current(request_id,evidence,now)
            if row['status'] != 'requested' or now >= row['deadline'] or row['card_message_id']:
                return None
            if self.join_transport(request_id) is not None: return None
            self.conn.execute("INSERT INTO join_transport(request_id,attempts,send_generation,send_status,updated_at) VALUES(?,1,1,'unknown',?)",(request_id,now))
            return 1

    def finish_fallback_card(self, request_id, generation, message_id, evidence, *, now):
        stamp(generation); ident(message_id,'om_')
        with self.transaction():
            row, _ = self._fallback_current(request_id,evidence,now)
            transport = self.join_transport(request_id)
            if (row['status'] != 'requested' or now >= row['deadline'] or not transport
                    or transport['send_status'] != 'unknown' or transport['send_generation'] != generation
                    or row['card_generation'] != 0 or generation != 1 or now < transport['updated_at']): return False
            self.conn.execute('UPDATE join_request SET card_message_id=?,card_generation=?,updated_at=? WHERE request_id=?',(message_id,generation,now,request_id))
            self.conn.execute("UPDATE join_transport SET send_status='sent',pending_message_id=?,updated_at=? WHERE request_id=?",(message_id,now,request_id))
            return True

    def decide_fallback_join(self, request_id,event_id,owner_pubkey,issuer_app_id,message_id,generation,evidence,*,approved,now):
        ident(event_id); hexid(owner_pubkey); ident(issuer_app_id,'cli_'); ident(message_id,'om_'); stamp(generation)
        if type(approved) is not bool: raise StoreError(ERROR)
        with self.transaction():
            row, marker = self._fallback_current(request_id,evidence,now)
            if (row['status'] != 'requested' or now >= row['deadline'] or now < row['updated_at'] or
                    (owner_pubkey,issuer_app_id,message_id,generation) !=
                    (marker.subject_owner_pubkey,marker.issuer_app_id,row['card_message_id'],row['card_generation']) or generation < 1): return False
            if self.conn.execute('SELECT 1 FROM join_decision WHERE app_id=? AND event_id=?',(issuer_app_id,event_id)).fetchone() or self.conn.execute('SELECT 1 FROM join_feedback WHERE app_id=? AND event_id=?',(issuer_app_id,event_id)).fetchone(): return False
            self.conn.execute('INSERT INTO join_decision VALUES(?,?,?,?)',(issuer_app_id,event_id,request_id,now))
            self.conn.execute('UPDATE join_request SET status=?,updated_at=? WHERE request_id=?',('approved' if approved else 'denied',now,request_id))
            return True

    def fallback_approval_decision(self, request_id):
        marker, row = self.fallback_provenance(request_id), self.join_request(request_id)
        if not marker or not row or row['status'] != 'approved': return None
        decision = self.conn.execute('SELECT * FROM join_decision WHERE request_id=? ORDER BY created_at,app_id,event_id',(request_id,)).fetchall()
        if len(decision) != 1 or decision[0]['app_id'] != marker.issuer_app_id or not row['card_message_id'] or row['card_generation'] < 1: return None
        return FallbackApprovalDecision(request_id,marker.subject_pubkey,marker.subject_owner_pubkey,marker.subject_app_id,
            marker.issuer_app_id,marker.scope_hash,row['card_generation'],hashlib.sha256(row['card_message_id'].encode()).hexdigest(),
            hashlib.sha256(decision[0]['event_id'].encode()).hexdigest(),decision[0]['created_at'])

    def _fallback_outward_facts(self, request_id):
        """Exact current SQL provenance; never foreign local-runtime authority."""
        ident(request_id)
        marker, request = self.fallback_provenance(request_id), self.join_request(request_id)
        decision = self.fallback_approval_decision(request_id)
        if not marker or not request or not decision:
            raise StoreError(ERROR)
        binding = self.conn.execute('SELECT * FROM binding WHERE binding_id=?', (marker.binding_id,)).fetchone()
        agent = self.conn.execute('SELECT * FROM agent WHERE pubkey=?', (marker.subject_pubkey,)).fetchone()
        if (not binding or binding['status'] != 'active' or not agent
                or (agent['owner_pubkey'],agent['app_id'],agent['config_path'],agent['status']) !=
                   (marker.subject_owner_pubkey,marker.subject_app_id,None,'paused')
                or (request['kind'],request['agent_id'],request['owner_pubkey'],request['callback_app_id'],
                    request['binding_id'],request['chat_id']) !=
                   ('channel',marker.subject_pubkey,marker.subject_owner_pubkey,marker.issuer_app_id,
                    marker.binding_id,binding['chat_id'])
                or (binding['channel_id'],binding['sync_app_id'],binding['mirror_pubkey']) !=
                   (marker.channel_id,marker.issuer_app_id,marker.mirror_pubkey)
                or hashlib.sha256(('buzz-feishu-chat:v1:'+binding['chat_id']).encode()).hexdigest() != marker.chat_ref
                or binding['chat_ref'] not in ('',marker.chat_ref)
                or self._approval_digest(self._fallback_scope(marker)) != marker.scope_hash):
            raise StoreError(ERROR)
        values = {name:getattr(marker,name) for name in ('request_id','binding_id','subject_pubkey',
            'subject_owner_pubkey','subject_app_id','issuer_app_id','channel_id','chat_ref','mirror_pubkey',
            'mirror_owner_pubkey','claimed_at','catalog_hash','scope_hash')}
        values.update(request_created_at=request['created_at'],request_deadline=request['deadline'],
            card_generation=decision.card_generation,card_message_sha256=decision.card_message_sha256,
            decision_event_sha256=decision.decision_event_sha256,decision_at=decision.decision_at)
        return values

    @staticmethod
    def _fallback_outward_body(pin):
        return dict(version=1,decision='approve',request_id=pin.request_id,
            agent_pubkey=pin.subject_pubkey,agent_owner_pubkey=pin.subject_owner_pubkey,app_id=pin.subject_app_id,
            channel_id=pin.channel_id,chat_ref=pin.chat_ref,mirror_pubkey=pin.mirror_pubkey,
            mirror_owner_pubkey=pin.mirror_owner_pubkey,claimed_at=pin.claimed_at,
            request_created_at=pin.request_created_at,request_deadline=pin.request_deadline,
            card_generation=pin.card_generation,card_message_sha256=pin.card_message_sha256,
            decision_event_sha256=pin.decision_event_sha256,decision_at=pin.decision_at)

    @classmethod
    def _fallback_outward_event(cls, pin):
        """Reconstruct original public bytes/signature; never sign or store a body."""
        if pin.stage == 'member':
            kind,content,tags = 9000,'',[['h',pin.channel_id],['p',pin.subject_pubkey],['role','bot']]
        elif pin.stage == 'approval':
            kind = 30078
            content = json.dumps(cls._fallback_outward_body(pin),sort_keys=True,separators=(',',':'),ensure_ascii=False)
            tags = [['t','hostd-card-approval-v1'],['h',pin.channel_id],['p',pin.subject_pubkey],
                    ['d','hostd-card-approval-v1:'+pin.content_hash]]
        else: raise StoreError(ERROR)
        return dict(id=pin.event_id,pubkey=pin.signer_pubkey,created_at=pin.event_created_at,
            kind=kind,tags=tags,content=content,sig=pin.signature)

    @classmethod
    def _fallback_outward_pin(cls, pin, now):
        from . import remote_approval as approval
        if type(pin) is not FallbackOutwardPin: raise StoreError(ERROR)
        ident(pin.request_id); ident(pin.binding_id); ident(pin.subject_app_id,'cli_'); ident(pin.issuer_app_id,'cli_')
        for name in ('subject_pubkey','subject_owner_pubkey','chat_ref','mirror_pubkey','mirror_owner_pubkey',
                'catalog_hash','card_message_sha256','decision_event_sha256','signer_pubkey','content_hash',
                'event_id','scope_hash','protectedfiles_hash'):
            hexid(getattr(pin,name))
        for name in ('claimed_at','request_created_at','request_deadline','card_generation','decision_at','event_created_at'):
            stamp(getattr(pin,name))
        stamp(now)
        if (not re.fullmatch('JOIN-[0-9a-f]{8}',pin.request_id) or pin.stage not in ('member','approval')
                or not isinstance(pin.channel_id,str) or not re.fullmatch('[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',pin.channel_id)
                or pin.subject_app_id == pin.issuer_app_id or pin.subject_pubkey == pin.subject_owner_pubkey
                or pin.mirror_pubkey == pin.mirror_owner_pubkey or pin.card_generation < 1
                or pin.request_deadline != pin.request_created_at+JOIN_TTL
                or not pin.request_created_at <= pin.decision_at < pin.request_deadline
                or not pin.claimed_at <= pin.decision_at <= pin.event_created_at
                or pin.signer_pubkey != (pin.mirror_owner_pubkey if pin.stage=='member' else pin.mirror_pubkey)):
            raise StoreError(ERROR)
        event = cls._fallback_outward_event(pin)
        try:
            approval._event(event,9000 if pin.stage=='member' else 30078,now)
            if pin.stage == 'approval': approval.decode(event,now=now)
        except Exception: raise StoreError(ERROR) from None
        if hashlib.sha256(event['content'].encode()).hexdigest() != pin.content_hash:
            raise StoreError(ERROR)
        return pin

    def _fallback_outward_current(self, pin):
        try:
            facts = self._fallback_outward_facts(pin.request_id)
            return all(getattr(pin,name)==value for name,value in facts.items())
        except Exception: return False

    def fallback_outward(self, request_id, stage):
        ident(request_id)
        if stage not in ('member','approval'): raise StoreError(ERROR)
        row = self.conn.execute('SELECT * FROM fallback_outward WHERE request_id=? AND stage=?',(request_id,stage)).fetchone()
        if not row: return None
        pin = FallbackOutwardPin(**{name:row[name] for name in FallbackOutwardPin.__dataclass_fields__})
        self._fallback_outward_pin(pin,pin.event_created_at)
        return FallbackOutwardRecord(pin,row['state'],row['observed_hash'],row['created_at'],row['updated_at'])

    def reserve_fallback_outward(self, request_id, stage, event, scope_hash, protectedfiles_hash, *, now):
        """Internal producer seam; complete current public/native proofs remain required."""
        from . import remote_approval as approval
        stamp(now); hexid(scope_hash); hexid(protectedfiles_hash)
        if stage not in ('member','approval') or not isinstance(event,dict): raise StoreError(ERROR)
        try: approval._event(event,9000 if stage=='member' else 30078,now)
        except Exception: raise StoreError(ERROR) from None
        with self.transaction():
            facts = self._fallback_outward_facts(request_id)
            if scope_hash != facts['scope_hash']: raise StoreError(ERROR)
            pin = FallbackOutwardPin(**facts,stage=stage,signer_pubkey=event['pubkey'],
                event_created_at=event['created_at'],content_hash=hashlib.sha256(event['content'].encode()).hexdigest(),
                event_id=event['id'],signature=event['sig'],protectedfiles_hash=protectedfiles_hash)
            self._fallback_outward_pin(pin,now)
            if self._fallback_outward_event(pin) != event: raise StoreError(ERROR)
            previous = self.fallback_outward(request_id,stage)
            if previous:
                if previous.pin != pin or now < previous.updated_at: raise StoreError(ERROR)
                return FallbackOutwardReservation(previous,False)
            keys = tuple(FallbackOutwardPin.__dataclass_fields__)
            self.conn.execute(f'INSERT INTO fallback_outward({",".join(keys)},state,created_at,updated_at) '
                f'VALUES({",".join("?" for _ in keys)},\'reserved\',?,?)',tuple(getattr(pin,key) for key in keys)+(now,now))
            token = (request_id,stage)
            self._fallback_live_reservations[token] = pin.event_id
            self._rollback_frames[-1].append(lambda:self._fallback_live_reservations.pop(token,None))
            return FallbackOutwardReservation(self.fallback_outward(request_id,stage),True)

    def mark_fallback_outward_unknown(self, request_id, stage, event_id, *, now):
        ident(request_id); hexid(event_id); stamp(now)
        with self.transaction():
            record = self.fallback_outward(request_id,stage)
            if (not record or record.state!='reserved' or record.pin.event_id!=event_id
                    or self._fallback_live_reservations.get((request_id,stage))!=event_id
                    or now<record.updated_at or not self._fallback_outward_current(record.pin)): return False
            self.conn.execute("UPDATE fallback_outward SET state='unknown',updated_at=? WHERE request_id=? AND stage=?",(now,request_id,stage))
            self._fallback_live_reservations.pop((request_id,stage),None)
            return True

    def ack_fallback_outward(self, request_id, stage, event_id, observed_hash, *, now):
        """Trusted internal caller proves original signed GET and fresh native/public scope."""
        ident(request_id); hexid(event_id); hexid(observed_hash); stamp(now)
        with self.transaction():
            record = self.fallback_outward(request_id,stage)
            if (not record or record.pin.event_id!=event_id or now<record.updated_at
                    or observed_hash!=self._approval_digest(self._fallback_outward_event(record.pin))
                    or not self._fallback_outward_current(record.pin)): return False
            if record.state=='acked': return record.observed_hash==observed_hash
            if record.state!='unknown': return False
            self.conn.execute("UPDATE fallback_outward SET state='acked',observed_hash=?,updated_at=? WHERE request_id=? AND stage=?",
                (observed_hash,now,request_id,stage))
            return True
