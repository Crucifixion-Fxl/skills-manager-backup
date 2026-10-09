"""Round-compatible metadata snapshots in SQL, imported once from validated fd bytes."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
try:
    from .store import Store, StoreError, ERROR, ident, hexid, stamp, localpath, _directory
    from .safety import read_owned
except ImportError:
    from store import Store, StoreError, ERROR, ident, hexid, stamp, localpath, _directory
    from safety import read_owned

INTS = frozenset("floor buzz_since feishu_since buzz_floor feishu_floor react_since members_synced member_event_seq".split())
BOOLS = frozenset("member_notice_active agent_intros_initialized".split())
TEXT = frozenset("binding member_event_stream member_notice_event member_notice_feishu member_notice_sender".split())
STRMAPS = frozenset("b2f b2f_modes b2f_senders e2f f2b r2f idmap emailmap images feishu_seen buzz_seen member_event_blocks people_seen agent_intros agent_intro_senders agent_intro_formats rwatch f2r claim_notes".split())
INTMAPS = frozenset("attempts threads polled tried unresolved f_unresolved edit_unresolved img_unresolved member_notes claim_takeovers".split())
FIELDS = INTS | BOOLS | TEXT | STRMAPS | INTMAPS | {"member_events", "member_notice_content"}


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


H = r"[0-9a-f]{64}"
M = r"om_[0-9A-Za-z_]+"
A = r"cli_[0-9A-Za-z_]+"
U = r"(?:ou|on)_[0-9A-Za-z_]+"
EMOJI = r"[A-Za-z][A-Za-z0-9_]{0,63}"
MARK = r"(?:pending|retry):[0-9]{1,12}"
TERMINAL = r"(?:failed|unknown|skipped|removed)"
# Observed Feishu reaction IDs encode 64 opaque bytes as canonical URL-safe
# base64. Padding and pad bits are constrained; this is metadata, never text.
REACTION_ID = r"(?:[A-Za-z0-9_.:-]+|[A-Za-z0-9_-]{85}[AQgw]==)"
IMAGE = H + r":(?:[0-9]+|thread|over)"


def _key(field, key):
    if field in {"b2f", "b2f_modes", "b2f_senders", "e2f", "r2f", "unresolved", "edit_unresolved", "buzz_seen", "agent_intros", "agent_intro_senders", "agent_intro_formats", "emailmap"}:
        return _matches(H, key)
    if field in {"f2b", "threads", "polled", "tried", "f_unresolved", "rwatch"}:
        return _matches(M, key)
    if field in {"images", "img_unresolved"}:
        return _matches(IMAGE, key)
    if field == "idmap":
        return _matches(r"ou_[0-9A-Za-z_]+", key)
    if field in {"people_seen", "feishu_seen"}:
        return isinstance(key, str) and gs._member_source_key_ok(key)
    if field == "member_event_blocks":
        return gs._parse_member_event_key(key) is not None
    if field == "f2r":
        return _matches(M + r"\|" + U + r"\|" + EMOJI, key)
    if field == "member_notes":
        reason, _, who = key.partition(":")
        return ((reason in {"unresolved", "refused"} and gs._member_source_key_ok(who))
                or (reason == "protected" and _matches(H, who)))
    if field == "claim_notes":
        return _matches(r"(?:buzz|feishu):[0-9a-f]{40}", key)
    if field == "claim_takeovers":
        return _matches(r"[0-9a-f]{40}", key)
    if field == "attempts":
        direction, _, item = key.partition(":")
        targets = {"b2f": "b2f", "f2b": "f2b", "e2f": "e2f", "r2f": "r2f", "r2f-del": "r2f",
                   "f2r": "f2r", "b2f-img": "images", "b2f-thread": "b2f", "intro": "agent_intros"}
        return direction in targets and _key(targets[direction], item)
    return False


def _value(field, value):
    if field in INTMAPS:
        stamp(value)
        return True
    patterns = {
        "b2f": f"(?:{M}|{TERMINAL}|{MARK}(?::(?:-|{M})(?:,(?:card|text))?)?)",
        "f2b": f"(?:{H}|{TERMINAL}|{MARK})",
        "e2f": f"(?:{M}|{TERMINAL}|{MARK}(?::{H}\\|{M}\\|(?:card|text))?)",
        "b2f_modes": r"(?:card|text)", "b2f_senders": A, "agent_intro_senders": A,
        "agent_intro_formats": r"(?:text|card_v1)",
        "r2f": f"(?:{TERMINAL}|{H}\\|{M}\\|{EMOJI}\\|{REACTION_ID}(?:\\|desk:{A})?)",
        "idmap": r"on_[0-9A-Za-z_]+", "emailmap": r"(?:ou_[0-9A-Za-z_]+|miss:[0-9]{1,12})",
        "images": f"(?:-|{M}|{TERMINAL}|{MARK}(?::(?:-|{M}))?)", "feishu_seen": "",
        "people_seen": H + r"\|[0-9]{1,12}", "rwatch": H + r"\|[0-9]{1,12}",
        "f2r": f"(?:{H}|{TERMINAL}|{MARK})", "agent_intros": f"(?:baseline|{M}|{TERMINAL}|{MARK})",
        "member_event_blocks": "retry_refused", "claim_notes": r"(?:sent|failed|retry:[0-9]{1,12})",
    }
    if field == "buzz_seen":
        return value == "" or gs._member_source_key_ok(value)
    return field in patterns and _matches(patterns[field], value)


def _validated(state, binding):
    if not isinstance(state, gs.State) or set(gs.State.__dataclass_fields__) != FIELDS:
        raise StoreError(ERROR)
    # Existing migration/validation remains the source of the exact signed retry contract.
    data = dataclasses.asdict(state)
    try:
        state = gs.state_from_data(data)
    except Exception:
        raise StoreError(ERROR) from None
    expected = binding["channel_id"] + "|" + binding["chat_id"]
    if state.binding not in ("", expected):
        raise StoreError(ERROR)
    for field in INTS:
        stamp(getattr(state, field))
    for field in TEXT - {"binding"}:
        value = getattr(state, field)
        if value:
            if field in {"member_event_stream", "member_notice_event"}:
                hexid(value)
            else:
                ident(value, "om_" if field == "member_notice_feishu" else "cli_")
    for field in STRMAPS | INTMAPS:
        for key, value in getattr(state, field).items():
            if not _key(field, key) or not _value(field, value):
                raise StoreError(ERROR)
    state.member_notice_content = ""  # reconstructed from current domain state; never persist the text
    return state


def _hash(state):
    return hashlib.sha256(json.dumps(dataclasses.asdict(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _matches_snapshot_hash(state, expected):
    if _hash(state) == expected:
        return True
    # Before introduction cards, this one field did not exist in the snapshot
    # digest. Accept only its exact absent/empty predecessor; never ignore a
    # persisted format or any other state field. The next normal save upgrades
    # the digest without replaying or rewriting delivery responsibility.
    if state.agent_intro_formats:
        return False
    previous = dataclasses.asdict(state)
    del previous['agent_intro_formats']
    return hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest() == expected


class StateAdapter:
    def __init__(self, store: Store, binding_id: str, legacy_dir: str | Path, *, profile_id=""):
        self.store = store
        self.binding_id = ident(binding_id)
        self.legacy_dir = Path(localpath(legacy_dir))
        ident(profile_id, empty=True)
        self.initial_profile = profile_id
        self.revision = None

    @property
    def identity_profile(self):
        row = self.store.conn.execute("SELECT identity_profile FROM state_snapshot WHERE binding_id=?", (self.binding_id,)).fetchone()
        return row[0] if row else ""

    def metadata(self):
        row = self.store.conn.execute("SELECT revision,imported_hash,import_path,identity_profile,updated_at FROM state_snapshot WHERE binding_id=?", (self.binding_id,)).fetchone()
        result = dict(row) if row else {"revision": None, "identity_profile": ""}
        match = re.fullmatch(r"bot:(cli_[A-Za-z0-9_]+):union_id:v1", result["identity_profile"])
        result["reader_app_id"] = match[1] if match else None
        return result

    def _binding(self):
        row = self.store.conn.execute("SELECT * FROM binding WHERE binding_id=?", (self.binding_id,)).fetchone()
        if row is None:
            raise StoreError(ERROR)
        return dict(row)

    def _legacy_bytes(self):
        # Walk directories without following links, including the absent-file case.
        directory = os.open(self.legacy_dir.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for part in self.legacy_dir.parts[1:]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                except FileNotFoundError:
                    return None
                os.close(directory); directory = child
            try:
                os.stat(gs.STATE_FILE, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return None
        finally:
            os.close(directory)
        return read_owned(self.legacy_dir / gs.STATE_FILE, max_bytes=32 * 1024 * 1024)

    def load(self):
        previous_revision = self.revision
        try:
            with self.store.transaction():
                binding = self._binding()
                row = self.store.conn.execute("SELECT * FROM state_snapshot WHERE binding_id=?", (self.binding_id,)).fetchone()
                if row is None:
                    raw = self._legacy_bytes()
                    state = gs.state_from_data(json.loads(raw)) if raw is not None else gs.State()
                    state = _validated(state, binding)
                    self.revision = 0
                    self._save(state, int(time.time()), imported_hash=hashlib.sha256(raw).hexdigest() if raw is not None else "",
                               import_path=str(self.legacy_dir / gs.STATE_FILE) if raw is not None else None,
                               identity_profile=self.initial_profile)
                    return state
                self.revision = row["revision"]
                data = dataclasses.asdict(gs.State())
                scalars = list(self.store.conn.execute("SELECT * FROM state_scalar WHERE binding_id=?", (self.binding_id,)))
                if {r["field"] for r in scalars} != INTS | BOOLS | TEXT:
                    raise StoreError(ERROR)
                for item in scalars:
                    field = item["field"]
                    data[field] = (bool(item["value_int"]) if field in BOOLS else item["value_int"] if field in INTS else item["value_text"])
                for item in self.store.conn.execute("SELECT * FROM state_map WHERE binding_id=? ORDER BY rowid", (self.binding_id,)):
                    field = item["field"]
                    if field not in STRMAPS | INTMAPS:
                        raise StoreError(ERROR)
                    data[field][item["item_key"]] = item["value_int"] if field in INTMAPS else item["value_text"]
                for item in self.store.conn.execute("SELECT * FROM state_member_event WHERE binding_id=?", (self.binding_id,)):
                    data["member_events"][item["operation"]] = {"id": item["event_id"], "pubkey": item["pubkey"],
                      "created_at": item["created_at"], "kind": item["kind"], "tags": json.loads(item["tags"]),
                      "content": "", "sig": item["sig"]}
                state = _validated(gs.state_from_data(data), binding)
                if not _matches_snapshot_hash(state, row["state_hash"]):
                    raise StoreError(ERROR)
                return state
        except Exception:
            self.revision = previous_revision
            raise StoreError(ERROR) from None

    def save(self, state, *, now=None):
        try:
            now = int(time.time()) if now is None else stamp(now)
            state = _validated(state, self._binding())
            with self.store.transaction():
                self._save(state, now)
        except Exception:
            raise StoreError(ERROR) from None

    def _save(self, state, now, *, imported_hash=None, import_path=None, identity_profile=None):
        row = self.store.conn.execute("SELECT * FROM state_snapshot WHERE binding_id=?", (self.binding_id,)).fetchone()
        if self.revision is None or (row and row["revision"] != self.revision) or (not row and self.revision != 0):
            raise StoreError(ERROR)
        revision = self.revision + 1
        previous_revision = self.revision
        self.store.on_rollback(lambda: setattr(self, "revision", previous_revision))
        self._deliveries(state, now)
        for field, stream in (("buzz_since", "relay"), ("feishu_since", "feishu"), ("react_since", "reaction")):
            self.store._advance_cursor(self.binding_id, "", stream, now, candidate=getattr(state, field))
            setattr(state, field, self.store.cursor_position(self.binding_id, stream))
        digest = _hash(state)
        if row is None:
            self.store.conn.execute("INSERT INTO state_snapshot VALUES(?,?,?,?,?,?,?)",
                                    (self.binding_id, revision, imported_hash or "", import_path, digest, identity_profile or "", now))
        else:
            self.store.conn.execute("UPDATE state_snapshot SET revision=?,state_hash=?,updated_at=? WHERE binding_id=? AND revision=?", (revision, digest, now, self.binding_id, self.revision))
        for table in ("state_scalar", "state_map", "state_member_event"):
            self.store.conn.execute(f"DELETE FROM {table} WHERE binding_id=?", (self.binding_id,))
        for field in sorted(INTS | BOOLS | TEXT):
            value = getattr(state, field)
            self.store.conn.execute("INSERT INTO state_scalar VALUES(?,?,?,?)",
                                    (self.binding_id, field, value if field in TEXT else None, int(value) if field in INTS | BOOLS else None))
        for field in sorted(STRMAPS | INTMAPS):
            for key, value in getattr(state, field).items():
                self.store.conn.execute("INSERT INTO state_map VALUES(?,?,?,?,?)", (self.binding_id, field, key, value if field in STRMAPS else None, value if field in INTMAPS else None))
        for operation, event in state.member_events.items():
            self.store.conn.execute("INSERT INTO state_member_event VALUES(?,?,?,?,?,?,?,?)", (self.binding_id, operation,
                event["id"], event["pubkey"], event["created_at"], event["kind"], json.dumps(event["tags"], separators=(",", ":")), event["sig"]))
        self.revision = revision

    def _deliveries(self, state, now):
        for direction, field, unresolved in (("b2f", "b2f", "unresolved"), ("f2b", "f2b", "f_unresolved"),
            ("e2f", "e2f", "edit_unresolved"), ("r2f", "r2f", None), ("f2r", "f2r", None),
            ("image", "images", "img_unresolved"), ("intro", "agent_intros", None)):
            for source, value in getattr(state, field).items():
                if value == "baseline" or (direction == "image" and source.endswith((":thread", ":over"))):
                    continue
                source_at = getattr(state, unresolved).get(source, 0) if unresolved else 0
                if not source_at and (gs._is_pending(value) or gs._is_retry(value)):
                    source_at = int(value.split(":", 2)[1])
                op = self.store.reserve_delivery(self.binding_id, source, direction, source_at=source_at, now=now)
                if gs._is_pending(value):
                    if op.status in {"acked", "unknown"}:
                        raise StoreError(ERROR)
                    if op.status == "failed":
                        self.store.retry_delivery(op.id, now=now)
                elif gs._is_retry(value) or value == gs.UNKNOWN:
                    self.store.fail_delivery(op.id, unknown=value == gs.UNKNOWN, now=now)
                elif value in {gs.FAILED, gs.SKIPPED, gs.REMOVED}:
                    self.store.settle_delivery(op.id, outcome={gs.FAILED: "abandoned", gs.SKIPPED: "skipped", gs.REMOVED: "removed"}[value], now=now)
                else:
                    target = value.split("|")[1] if direction == "r2f" else value
                    # Aggregate read cursors require the worker's whole-phase
                    # completeness proof. A single delivered message is not a
                    # proof that all earlier pages or threads were read.
                    self.store.ack_delivery(op.id, target, now=now, advance_cursor=False)

    def migrate_identity(self, profile_id, *, candidate=None, reaction_key_mapping=(), now):
        """Apply only an upstream-verified candidate. Changed authors require its exact proof mapping.

        This storage layer validates preservation; the bot preflight verifies signed people
        and app-scoped author evidence. It must supply a complete, one-to-one mapping.
        """
        ident(profile_id); stamp(now)
        with self.store.transaction():
            state = self.load()
            old = self.identity_profile
            if old == profile_id:
                return False
            if candidate is None:
                for field in ("idmap", "emailmap", "feishu_seen", "buzz_seen", "people_seen", "member_notes"):
                    setattr(state, field, {})
                state.members_synced = 0
            else:
                candidate = _validated(candidate, self._binding())
                projections = {"idmap", "emailmap", "feishu_seen", "buzz_seen", "people_seen", "member_notes", "members_synced", "f2r"}
                if any(getattr(state, field) != getattr(candidate, field) for field in FIELDS - projections - {"member_notice_content"}):
                    raise StoreError(ERROR)
                old_reactions = Counter((key.split("|")[0], key.split("|")[2], value) for key, value in state.f2r.items())
                new_reactions = Counter((key.split("|")[0], key.split("|")[2], value) for key, value in candidate.f2r.items())
                if old_reactions != new_reactions:
                    raise StoreError(ERROR)
                if state.f2r != candidate.f2r:
                    if (not isinstance(reaction_key_mapping, tuple)
                            or not all(isinstance(pair, tuple) and len(pair) == 2 for pair in reaction_key_mapping)):
                        raise StoreError(ERROR)
                    mapping = dict(reaction_key_mapping)
                    if (len(mapping) != len(reaction_key_mapping) or set(mapping) != set(state.f2r)
                            or len(set(mapping.values())) != len(mapping) or set(mapping.values()) != set(candidate.f2r)):
                        raise StoreError(ERROR)
                    for before, after in mapping.items():
                        if (not _key("f2r", before) or not _key("f2r", after)
                                or before.split("|")[::2] != after.split("|")[::2]
                                or state.f2r[before] != candidate.f2r[after]):
                            raise StoreError(ERROR)
                state = candidate
            self.save(state, now=now)
            self.store.conn.execute("INSERT INTO state_migration VALUES(?,?,?,?)", (self.binding_id, old, profile_id, now))
            self.store.conn.execute("UPDATE state_snapshot SET identity_profile=? WHERE binding_id=?", (profile_id, self.binding_id))
            return True

    def export_legacy(self, target_dir):
        state = self.load()
        directory = None
        try:
            directory = _directory(Path(localpath(target_dir)))
            gs.save_state(Path(f"/proc/self/fd/{directory}"), state)
        except Exception:
            raise StoreError(ERROR) from None
        finally:
            if directory is not None:
                os.close(directory)
