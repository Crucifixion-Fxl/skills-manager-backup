"""S7 native delivery notice: real signed sources/Store/Round/BotLarkCli, lower IO fakes only."""
import asyncio
import copy
import concurrent.futures
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
if not (SCRIPTS / "hostd").is_dir():
    WORKTREE = Path(__file__).resolve().parents[2]
    REPO = WORKTREE / "hostd-codex-implementation" / "skills" / "buzz-agent-setup"
    TESTS, SCRIPTS = REPO / "tests", REPO / "scripts"
for path in (TESTS, SCRIPTS, SCRIPTS / "hostd"):
    sys.path.insert(0, str(path))

import test_hostd_delivery_mapping as mapping_fixture
import test_hostd_remote_mapping as public_fixture
import buzz_feishu_group_sync as gs
from hostd import bot_clients as canonical_bot_clients
from hostd import delivery_mapping as canonical_mapping
from hostd import state_store as canonical_state_store
from hostd import store

base = mapping_fixture.base
BotLarkCli = canonical_bot_clients.BotLarkCli
message_card = canonical_mapping.message_card
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule
CHANNEL, CHAT = base.CHANNEL, base.CHAT
FOREIGN_KEY = public_fixture.KEY
FOREIGN_OWNER_KEY = public_fixture.OWNER_KEY
FOREIGN_PUB = public_fixture.PUB
FOREIGN_OWNER = public_fixture.OWNER
FOREIGN_APP = public_fixture.APP
NOTICE_ROOT = "om_s7_notice_root"
PNG_BYTES = base.PNG_PLAIN
PNG_DIGEST = hashlib.sha256(PNG_BYTES).hexdigest()


class DeliveryNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_verified_non_candidates_can_be_excluded_from_hint_queue(self):
        coordinator=self.coordinator()
        top=gs.sign_event(FOREIGN_KEY,9,[['h',CHANNEL]],'top level',self.utc_now)
        human=gs.sign_event(base.OWNER_KEY,9,[['h',CHANNEL],['e',self.root_event['id'],'','reply']],
                            'human reply',self.utc_now)
        self.world.relay_events.extend([top,human])
        roster=max((e for e in self.world.relay_events if e['kind']==39002),key=lambda e:e['created_at'])
        self.advance_clock(1)
        tags=[t for t in roster['tags'] if t[:2]!=['p',base.OWNER_PK]]+[['p',base.OWNER_PK,'','owner']]
        self.world.relay_events.append(gs.sign_event(public_fixture.RELAY_KEY,39002,tags,'',self.utc_now))
        self.assertTrue(await coordinator.exclude_candidate(top['id']))
        self.assertTrue(await coordinator.exclude_candidate(human['id']))
        source=self.source()
        self.assertFalse(await coordinator.exclude_candidate(source['id']))
        source['content']='signature tampered'
        with self.assertRaises(Exception):await coordinator.exclude_candidate(source['id'])
        with self.assertRaises(Exception):await coordinator.exclude_candidate('0'*64)
        self.assertFalse(self.native_notice_posts())

    async def test_sent_reply_with_native_projection_resolves_without_warning(self):
        coordinator = self.coordinator()
        source = self.source()
        await coordinator.observe(source['id'])
        message = self.install_foreign_card(source)
        card = json.loads(message['body']['content'])
        card['config'] = {}
        card['header']['title']['i18n'] = {}
        message['body']['content'] = json.dumps(card, sort_keys=True)
        await coordinator.scan_once()
        self.assertEqual(self.exact_notice_row(source).state, 'resolved_without_notice')
        self.assertFalse(self.native_notice_posts())

    async def test_notice_projection_readback_is_exact_except_server_display_defaults(self):
        coordinator = self.coordinator()
        source = self.source()
        row = await coordinator.observe(source['id'])
        proof = await coordinator._source_proof(source['id'])
        card, version, digest = await coordinator._expected_card(proof, row)
        projected = json.loads(card)
        projected['config'] = {}
        projected['header']['title']['i18n'] = {}
        message = {'message_id':'om_projection', 'chat_id':CHAT, 'root_id':NOTICE_ROOT,
                   'msg_type':'interactive', 'sender':{'sender_type':'app','id_type':'app_id','id':row.sync_app_id},
                   'body':{'content':json.dumps(projected, sort_keys=True)}}
        self.assertTrue(coordinator._native_exact(message, row, card, version, digest))
        for flag in ('update_multi', 'wide_screen_mode'):
            projected['config'][flag] = False
            message['body']['content'] = json.dumps(projected)
            self.assertFalse(coordinator._native_exact(message, row, card, version, digest), flag)
            del projected['config'][flag]
        projected['elements'][0]['text']['content'] = 'tampered'
        message['body']['content'] = json.dumps(projected)
        self.assertFalse(coordinator._native_exact(message, row, card, version, digest))

    def setUp(self):
        self.tmp_context = tempfile.TemporaryDirectory(prefix="hostd-s7-notice-")
        self.addCleanup(self.tmp_context.cleanup)
        self.tmp = Path(self.tmp_context.name)
        # Advance the deterministic wall clock beyond older fixture roster rows;
        # every newly signed policy/roster below is current at the injected time.
        self.utc_now = int(base.NOW.timestamp()) + 10
        self.mono_now = 500.0
        # This is the existing actual MappedHostdRound + Store + BotLarkCli
        # fixture; only the relay/CLI child transport and temp protected files are fake.
        self.env, self.cfg, self.world, legacy_round = mapping_fixture.MappedAssembly.assembly(self)
        self.world.clock = base.NOW.fromtimestamp(self.utc_now, tz=base.NOW.tzinfo)
        self.install_signed_relay_filtering()
        # Reuse only the fixture's protected files and lower transport world.
        # Construct the actual package-qualified Store/client/Round used by the
        # coordinator so type identity and current authority paths match runtime.
        legacy_store = legacy_round.mapping_store
        legacy_store.close()
        clients = canonical_bot_clients.build_clients(
            self.cfg, self.env.base_env, runner=self.world, http=self.world.http_get,
            trusted_relays={"https://relay.test"})
        self.db = store.Store(self.tmp / "hostd.db")
        self.db.reconcile_bindings([store.BindingRecord(
            "test", self.cfg["channel_id"], self.cfg["chat_id"], base.AGENT_APP,
            str(self.env.config), str(self.tmp / "agent-cfg"), str(self.tmp / "agent-data"),
            self.cfg["mirror_pubkey"])], now=self.utc_now)
        adapter = canonical_state_store.StateAdapter(
            self.db, "test", self.env.state_dir,
            profile_id=f"bot:{base.AGENT_APP}:union_id:v1")
        state = adapter.load()
        holder = {}
        def persist():
            adapter.save(holder["run"].state)
        self.round = canonical_mapping.MappedHostdRound(
            self.cfg, clients, state, base.FGS._new_report(), base.NOW, persist,
            reader_namespace=base.AGENT_APP, store=self.db, binding_id="test",
            auth_clock=lambda: base.NOW.fromtimestamp(self.utc_now, tz=base.NOW.tzinfo),
            media_http=self.world.media_upload)
        holder["run"] = self.round
        self.round.verify_identities(); self.round.load_people(); self.round.load_directory(); self.round.verify_desk()
        self.addCleanup(lambda: self.db.close() if self.db.conn is not None else None)
        self.assertIsInstance(self.round, canonical_mapping.MappedHostdRound)
        self.assertIsInstance(self.round.clients.owner, BotLarkCli)
        # Runtime Main gets this relay signer from the protected RuntimeConfig.
        # This focused library fixture injects that explicit pin at the Round
        # boundary; the protected binding config remains validator-canonical.
        # No foreign private env or catalog record is constructed here.
        base.write_owner_only(self.env.config, json.dumps(self.cfg))
        self.round.claim_relay_pubkey = public_fixture.PIN
        self.assertEqual(self.round.claim_relay_pubkey, public_fixture.PIN)
        self.thread_history_complete = True
        native_runner = self.round.clients.owner.runner

        def native_wire_runner(argv, **kwargs):
            result = native_runner(argv, **kwargs)
            # Preserve the real BotLarkCli command and its returned MID. The
            # lower fake's shorthand reply storage is an effect ledger, so
            # project its actual row into the complete native message GET view.
            if (argv[2:4] in (["im", "+messages-reply"], ["im", "+messages-send"])
                    and result.returncode == 0):
                payload = json.loads(result.stdout)
                data = payload.get("data") if isinstance(payload, dict) else None
                mid = data.get("message_id") if isinstance(data, dict) else None
                if type(mid) is str:
                    message = next((item for item in self.world.messages
                                    if item.get("message_id") == mid), None)
                    if message is None:
                        message = next((item for replies in self.world.threads.values()
                                        for item in replies if item.get("message_id") == mid), None)
                    self.assertIsNotNone(message, "successful native reply must have an actual stored MID")
                    command = argv[2:]
                    def arg(name, default=None):
                        try:
                            return command[command.index(name) + 1]
                        except (ValueError, IndexError):
                            return default
                    is_reply = argv[3] == "+messages-reply"
                    parent = arg("--message-id") if is_reply else mid
                    chat_id = CHAT if is_reply else arg("--chat-id")
                    msg_type = arg("--msg-type", "text")
                    if msg_type == "interactive":
                        body_content = arg("--content")
                        self.assertIsNotNone(body_content)
                    else:
                        body_content = json.dumps({"text": arg("--text", "")}, ensure_ascii=False)
                    message.update({
                        "message_id": mid, "chat_id": chat_id, "root_id": parent,
                        "create_time": str(int(self.world.clock.timestamp() * 1000)),
                        "msg_type": msg_type,
                        "sender": {"sender_type": "app", "id_type": "app_id",
                                   "id": self.round.desk_app_id},
                        "body": {"content": body_content}, "deleted": False,
                    })
                    if not any(item.get("message_id") == mid for item in self.world.messages):
                        self.world.messages.append(message)
            elif (argv[2:4] == ["api", "PATCH"] and len(argv) > 4
                  and argv[4].startswith("/open-apis/im/v1/messages/")
                  and result.returncode == 0):
                mid = argv[4].rsplit("/", 1)[1]
                message = next((item for item in self.world.messages
                                if item.get("message_id") == mid), None)
                if message is None:
                    message = next((item for replies in self.world.threads.values()
                                    for item in replies if item.get("message_id") == mid), None)
                self.assertIsNotNone(message, "successful native PATCH must update its actual stored MID")
                self.assertIs(message.get("updated"), True, "successful PATCH must have applied in the lower fake")
                self.assertEqual(message.get("msg_type"), "interactive")
                self.assertIsInstance(message.get("body"), dict)
                self.assertIs(type(message.get("content")), str)
                # The legacy lower fake applies PATCH to its stored content field;
                # expose that same successful effect through native v1's body shape.
                message["body"]["content"] = message["content"]
            return result

        def bounded_history_runner(argv, **kwargs):
            result = native_wire_runner(argv, **kwargs)
            if argv[2:4] == ["im", "+threads-messages-list"] and not self.thread_history_complete:
                payload = json.loads(result.stdout)
                payload["data"]["has_more"] = True
                payload.setdefault("meta", {}).setdefault("pagination", {})["complete"] = False
                return type(result)(argv, result.returncode, json.dumps(payload), result.stderr)
            return result
        self.round.clients.owner.runner = bounded_history_runner
        self.install_public_foreign_directory()
        self.install_actual_mapped_root()

    def install_signed_relay_filtering(self):
        """Extend only the fixture's lower relay result selection, retaining its NIP-98 verifier."""
        world = self.world
        original = world._serve_relay_query

        def query(url, headers, body):
            try:
                status, raw = original(url, headers, body)
            except (KeyError, TypeError, IndexError):
                # The inherited fake authenticates before its simplified filter;
                # malformed stored rows still belong in our whole response so
                # the real parser, not the transport fixture, rejects them.
                world.assert_signed_query(headers, url, body)
                status, raw = 200, b"[]"
            if status != 200:
                return status, raw
            try:
                world.assert_signed_query(headers, url, body)
                filters = json.loads(body)
                # Preserve fixture-controlled malformed HTTP/JSON responses.
                if not isinstance(filters, list) or not isinstance(json.loads(raw), list):
                    return status, raw
            except (AssertionError, TypeError, ValueError):
                return status, raw

            def matches(event, filt):
                if not isinstance(event, dict) or not isinstance(filt, dict):
                    return False
                for name, values in filt.items():
                    if name == "limit":
                        continue
                    if name in ("since", "until"):
                        created = event.get("created_at")
                        if type(created) is not int or (name == "since" and created < values) or (name == "until" and created > values):
                            return False
                    elif name == "kinds" and event.get("kind") not in values:
                        return False
                    elif name == "authors" and event.get("pubkey") not in values:
                        return False
                    elif name == "ids" and event.get("id") not in values:
                        return False
                    elif name.startswith("#"):
                        tags = event.get("tags")
                        if not isinstance(tags, list) or not any(
                                isinstance(tag, list) and len(tag) >= 2
                                and tag[0] == name[1:] and tag[1] in values for tag in tags):
                            return False
                return True

            def order_key(event):
                created = event.get("created_at")
                event_id = event.get("id")
                return (created if type(created) is int else -1,
                        event_id if isinstance(event_id, str) else "")

            selected = {}
            malformed_without_id = []
            for filt in filters:
                rows = [event for event in world.relay_events if matches(event, filt)]
                rows.sort(key=order_key, reverse=True)
                limit = filt.get("limit")
                if type(limit) is int:
                    rows = rows[:limit]
                for event in rows:
                    event_id = event.get("id")
                    if isinstance(event_id, str):
                        selected[event_id] = event
                    else:
                        malformed_without_id.append(event)
            found = sorted(selected.values(), key=order_key, reverse=True)
            found.extend(malformed_without_id)
            # Never verify or strip matching event signatures here. The actual
            # signed reader must reject any malformed or tampered whole packet.
            return 200, json.dumps(found).encode()

        world._serve_relay_query = query

    def install_public_foreign_directory(self):
        # B has no local env/catalog/binding. These are only public signed records
        # fetched through A's relay adapter. The host-A protected cfg stays unchanged.
        profile = gs.sign_event(FOREIGN_KEY, 0,
            [public_fixture.auth(FOREIGN_KEY, FOREIGN_OWNER_KEY)],
            json.dumps({"name": "远端助手"}, separators=(",", ":")), self.utc_now - 20)
        policy = gs.sign_event(FOREIGN_OWNER_KEY, 30177, [["d", FOREIGN_PUB]],
            json.dumps({"feishu": {"app_id": FOREIGN_APP}}, separators=(",", ":")), self.utc_now - 10)
        prior_rosters = [e for e in self.world.relay_events if e.get("kind") == 39002
                         and any(t[:2] == ["d", CHANNEL] for t in e.get("tags", []) if isinstance(t, list))]
        latest_roster = max(prior_rosters, key=lambda event: event.get("created_at", -1), default=None)
        roster_members = {}
        if latest_roster is not None:
            for tag in latest_roster.get("tags", []):
                if (isinstance(tag, list) and len(tag) >= 4 and tag[0] == "p"
                        and isinstance(tag[1], str)):
                    roster_members[tag[1]] = tag
        roster_members[FOREIGN_PUB] = ["p", FOREIGN_PUB, "", "bot"]
        tags = [["d", CHANNEL], *roster_members.values()]
        roster = gs.sign_event(public_fixture.RELAY_KEY, 39002, tags, "", self.utc_now)
        roster_p = [tag for tag in roster["tags"] if isinstance(tag, list) and tag and tag[0] == "p"]
        self.assertEqual(roster["pubkey"], public_fixture.PIN)
        self.assertEqual(roster["created_at"], self.utc_now)
        self.assertEqual([tag for tag in roster["tags"] if isinstance(tag, list) and tag and tag[0] == "d"],
                         [["d", CHANNEL]])
        self.assertEqual(roster_p, tags[1:])
        self.assertEqual(len(roster_p), len({tag[1] for tag in roster_p if len(tag) >= 2}))
        self.assertTrue(any(tag == ["p", FOREIGN_PUB, "", "bot"] for tag in roster_p))
        self.world.relay_events.extend((profile, policy, roster))
        self.world.bots[FOREIGN_APP] = "ou_foreign_s7_bot0000000000000001"
        if hasattr(self.world, "bot_members"):
            self.world.bot_members[FOREIGN_APP] = self.world.bots[FOREIGN_APP]
        self.assertNotIn(FOREIGN_PUB, self.round.cfg["agents"])
        self.assertFalse((self.tmp / "foreign-agent.env").exists())

    def install_actual_mapped_root(self):
        # Build the original topic with the existing real resolver and actual bot GET.
        root = gs.sign_event(base.MIRROR_KEY, 9,
            [["h", CHANNEL], ["feishu", NOTICE_ROOT], ["feishu-root", NOTICE_ROOT],
             ["feishu-author", base.ALICE_PK]], "mapped topic root", self.utc_now - 30)
        self.world.events.append(root)
        self.world.relay_events.append(root)
        self.world.messages.append(base.fmsg(NOTICE_ROOT, base.ALICE_OPEN, "mapped topic root"))
        self.world.resources = getattr(self.world, "resources", {})
        mapping = self.round.resolve_feishu(NOTICE_ROOT)
        self.assertIsNotNone(mapping, "fixture requires actual signed/public/native root mapping")
        self.root_event = root

    def source(self, *, content="foreign body", images=(), created_at=None, root_event=None):
        root_event = root_event or self.root_event
        tags = [["h", CHANNEL], ["e", root_event["id"], "", "root"], ["e", root_event["id"], "", "reply"]]
        for digest, mime, size in images:
            suffix = ".png" if mime == "image/png" else ".jpg"
            tags.append(["imeta", f"url {self.round.clients.relay_url}/media/{digest}{suffix}",
                         f"m {mime}", f"x {digest}", f"size {size}"])
        event = gs.sign_event(FOREIGN_KEY, 9, tags, content,
                              self.utc_now if created_at is None else created_at)
        self.world.events.append(event)
        self.world.relay_events.append(event)
        return event

    def advance_clock(self, seconds):
        self.utc_now += seconds
        self.mono_now += seconds
        self.world.clock = base.NOW.fromtimestamp(self.utc_now, tz=base.NOW.tzinfo)

    def coordinator_class(self):
        # Feature absence must be an assertion RED, never ImportError/AttributeError.
        spec = importlib.util.find_spec("hostd.delivery_notice")
        self.assertIsNotNone(spec, "hostd.delivery_notice absent: genuine tests-first RED")
        module = importlib.import_module("hostd.delivery_notice")
        cls = getattr(module, "DeliveryNoticeCoordinator", None)
        self.assertTrue(callable(cls), "DeliveryNoticeCoordinator absent: genuine tests-first RED")
        for name in ("observe_delivery_notice", "get_delivery_notice", "list_delivery_notices",
                     "reserve_delivery_notice_send", "resolve_delivery_notice_without_notice",
                     "mark_delivery_notice_unknown", "record_delivery_notice_readback",
                     "reserve_delivery_notice_recovery", "record_delivery_notice_recovery_readback"):
            self.assertTrue(callable(getattr(store.Store, name, None)), f"missing Store API {name}: genuine tests-first RED")
        return cls

    def coordinator(self):
        cls = self.coordinator_class()
        # `round` is the actual host-A protected binding Round; no agent/grant/result
        # proof DTO is passed. Existing client assembly owns shared scheduler/HTTP pool.
        coordinator = cls(self.db, "test", self.round,
            utc_clock=lambda: self.utc_now, monotonic_clock=lambda: self.mono_now)
        self.addAsyncCleanup(coordinator.close)
        return coordinator

    def native_writes(self, method, path_suffix):
        if method == "POST":
            return [(argv, kwargs) for argv, kwargs in self.world.bot_calls
                    if self._post_effect_matches(argv, path_suffix)]
        return [(argv, kwargs) for argv, kwargs in self.world.bot_calls
                if argv[2:4] == ["api", method] and len(argv) > 4
                and argv[4].endswith(path_suffix)]

    @staticmethod
    def _post_effect_matches(argv, path_suffix):
        if (argv[2:4] == ["api", "POST"] and len(argv) > 4
                and argv[4].endswith(path_suffix)):
            try:
                body = json.loads(argv[argv.index("--data") + 1])
            except (ValueError, IndexError):
                return False
            return isinstance(body, dict) and body.get("msg_type") == "interactive"
        if argv[2:4] == ["im", "+messages-reply"]:
            try:
                return (path_suffix == "/reply" and "--content" in argv
                        and "--msg-type" in argv
                        and argv[argv.index("--msg-type") + 1] == "interactive")
            except IndexError:
                return False
        if argv[2:4] == ["im", "+messages-send"]:
            try:
                return (path_suffix == "/messages" and "--content" in argv
                        and "--msg-type" in argv
                        and argv[argv.index("--msg-type") + 1] == "interactive")
            except IndexError:
                return False
        return False

    def native_notice_posts(self):
        return [(argv, kwargs) for argv, kwargs in self.world.bot_calls
                if (self._post_effect_matches(argv, "/messages")
                    or self._post_effect_matches(argv, "/reply"))]

    def exact_notice_row(self, source):
        return self.db.get_delivery_notice("test", source["id"])

    def install_foreign_card(self, source, *, message_id="om_foreign_notice_delivery", content=None,
                             root_id=NOTICE_ROOT, app_id=FOREIGN_APP, image_keys=()):
        # Use the real message renderer/footer and actual Card 1.0 native response shape.
        card = json.loads(message_card(source, "远端助手", source["content"] if content is None else content,
                                       self.cfg["people_api"]["base_url"], CHANNEL))
        if image_keys:
            card["elements"][-1:-1] = [{"tag": "img", "img_key": key,
                "alt": {"tag": "plain_text", "content": ""}} for key in image_keys]
        row = {"message_id": message_id, "chat_id": CHAT, "root_id": root_id,
            "thread_id": root_id, "create_time": str(self.utc_now * 1000), "msg_type": "interactive",
            "sender": {"sender_type": "app", "id_type": "app_id", "id": app_id},
            "body": {"content": json.dumps(card, ensure_ascii=False, separators=(",", ":"))}}
        self.world.messages.append(row)
        self.world.threads.setdefault(root_id, []).append(row)
        return row

    def drop_next_native_response(self, method, path_suffix):
        """Lose a response after the real fixture's lower BotLarkCli effect lands."""
        client = self.round.clients.owner
        runner = client.runner
        dropped = {"value": False}
        def matches(argv):
            return (argv[2:4] == ["api", method] and len(argv) > 4
                    and argv[4].endswith(path_suffix)) or (
                    method == "POST" and self._post_effect_matches(argv, path_suffix))
        def lower(argv, **kwargs):
            if not dropped["value"] and matches(argv):
                state = "reserved" if method == "POST" else "recovery_reserved"
                observed = concurrent.futures.Future()
                def read_sql():
                    try:
                        rows = self.db.list_delivery_notices(states=(state,), limit=256)
                        observed.set_result((rows, self.db.conn.in_transaction))
                    except Exception as exc:
                        observed.set_exception(exc)
                self.loop.call_soon_threadsafe(read_sql)
                rows, in_transaction = observed.result(timeout=3)
                self.assertTrue(rows, "durable intent must commit before native mutation")
                self.assertFalse(in_transaction, "SQLite transaction must be closed before native IO")
            result = runner(argv, **kwargs)
            if not dropped["value"] and matches(argv):
                dropped["value"] = True
                return type(result)(argv, 1, "", "synthetic response lost after native effect")
            return result
        client.runner = lower
        return dropped

    def block_next_native_patch_after_effect(self):
        client = self.round.clients.owner
        runner = client.runner
        entered, release = threading.Event(), threading.Event()
        blocked = {"value": False}
        def lower(argv, **kwargs):
            if (not blocked["value"] and argv[2:4] == ["api", "PATCH"]
                    and "/messages/" in argv[4]):
                observed = concurrent.futures.Future()
                def read_sql():
                    try:
                        rows = self.db.list_delivery_notices(states=("recovery_reserved",), limit=256)
                        observed.set_result((rows, self.db.conn.in_transaction))
                    except Exception as exc:
                        observed.set_exception(exc)
                self.loop.call_soon_threadsafe(read_sql)
                rows, in_transaction = observed.result(timeout=3)
                self.assertTrue(rows, "same-message recovery intent must commit before PATCH")
                self.assertFalse(in_transaction, "no SQLite transaction may span native IO")
            result = runner(argv, **kwargs)
            if (not blocked["value"] and argv[2:4] == ["api", "PATCH"]
                    and "/messages/" in argv[4]):
                blocked["value"] = True
                entered.set()
                if not release.wait(10):
                    raise AssertionError("test failed to release native IO rendezvous")
            return result
        client.runner = lower
        return entered, release

    def mutate_after_native_get(self, path_suffix, callback):
        """Change real signed evidence after a completed low-level native GET."""
        client = self.round.clients.owner
        runner = client.runner
        fired = {"value": False}
        def lower(argv, **kwargs):
            result = runner(argv, **kwargs)
            if (not fired["value"] and argv[2:4] == ["api", "GET"]
                    and argv[4].endswith(path_suffix)):
                fired["value"] = True
                callback()
            return result
        client.runner = lower
        return fired

    async def test_a_notices_real_foreign_source_without_b_env_catalog_or_lane(self):
        source = self.source(created_at=self.utc_now - 7200)
        coordinator = self.coordinator()
        first = await coordinator.observe(source["id"])
        self.assertEqual(first.state, "waiting")
        self.assertEqual(first.first_observed_at, self.utc_now)
        self.assertEqual(first.deadline_at, self.utc_now + 60)
        self.assertFalse(self.native_notice_posts())
        self.advance_clock(59)
        await coordinator.scan_once()
        self.assertFalse(self.native_notice_posts())
        self.advance_clock(1)
        await coordinator.scan_once()
        row = self.exact_notice_row(source)
        self.assertEqual(row.state, "noticed")
        self.assertIsNotNone(row.notice_message_id)
        posts = self.native_notice_posts()
        self.assertEqual(len(posts), 1)
        argv = posts[0][0]
        self.assertEqual(argv[argv.index("--profile") + 1], self.round.desk_app_id)
        self.assertEqual(argv[argv.index("--as") + 1], "bot")
        self.assertNotIn(FOREIGN_APP, " ".join(argv))
        card = json.loads(argv[argv.index("--content") + 1])
        visible = json.dumps(card, ensure_ascii=False)
        self.assertIn("远端助手", visible)
        self.assertIn("本话题", visible)
        self.assertIn("复制给 AI", visible)
        self.assertNotIn(FOREIGN_APP, visible)
        self.assertNotIn(FOREIGN_PUB, visible)
        self.assertNotIn("群 GW", visible)
        self.assertNotIn(CHAT, visible)
        self.assertEqual(row.chat_id, CHAT)
        self.assertEqual(row.root_message_id, NOTICE_ROOT)
        await coordinator.close()
        self.db.close()
        self.db = store.Store(self.tmp / "hostd.db")
        self.round.mapping_store = self.db
        restarted = self.coordinator()
        await restarted.observe(source["id"]); await restarted.scan_once()
        self.assertEqual(len(self.native_notice_posts()), 1)

    async def test_no_source_or_stale_public_membership_config_creates_timer(self):
        coordinator = self.coordinator()
        await coordinator.scan_once()
        self.assertEqual(self.db.list_delivery_notices(states=("waiting", "reserved", "unknown", "noticed"), limit=16), ())
        valid = self.source()
        tampered = dict(valid, content=valid["content"] + "tampered")
        self.world.events.remove(valid); self.world.relay_events.remove(valid)
        self.world.events.append(tampered); self.world.relay_events.append(tampered)
        with self.subTest(kind="invalid signed source"):
            await coordinator.observe(tampered["id"])
            self.assertIsNone(self.db.get_delivery_notice("test", tampered["id"]))
        self.world.events.remove(tampered); self.world.relay_events.remove(tampered)
        stale = self.source(content="current policy must be rechecked")
        self.advance_clock(1)
        changed_policy = gs.sign_event(FOREIGN_OWNER_KEY, 30177, [["d", FOREIGN_PUB]],
            json.dumps({"feishu": {"app_id": "cli_wrong_s7_app"}}), self.utc_now)
        self.world.relay_events.append(changed_policy)
        with self.subTest(kind="current owner policy app drift"):
            await coordinator.observe(stale["id"])
            self.assertIsNone(self.db.get_delivery_notice("test", stale["id"]))
        self.advance_clock(1)
        current_policy = gs.sign_event(FOREIGN_OWNER_KEY, 30177, [["d", FOREIGN_PUB]],
            json.dumps({"feishu": {"app_id": FOREIGN_APP}}, separators=(",", ":")), self.utc_now)
        self.world.relay_events.append(current_policy)
        no_native_member = self.source(content="foreign app not in actual native members")
        saved_member = self.world.bots.pop(FOREIGN_APP)
        if hasattr(self.world, "bot_members"):
            self.world.bot_members.pop(FOREIGN_APP, None)
        await coordinator.observe(no_native_member["id"])
        self.assertIsNone(self.db.get_delivery_notice("test", no_native_member["id"]))
        self.world.bots[FOREIGN_APP] = saved_member
        if hasattr(self.world, "bot_members"):
            self.world.bot_members[FOREIGN_APP] = saved_member
        unmapped_root = gs.sign_event(base.MIRROR_KEY, 9,
            [["h", CHANNEL], ["feishu", "om_unmapped_root"], ["feishu-root", "om_unmapped_root"]],
            "unmapped root", self.utc_now)
        no_root = self.source(content="native root has no signed mapping", root_event=unmapped_root)
        await coordinator.observe(no_root["id"])
        self.assertIsNone(self.db.get_delivery_notice("test", no_root["id"]))
        config_source = self.source(content="protected binding config drift")
        saved_config = self.env.config.read_bytes()
        changed = dict(self.cfg, chat_id="oc_changed_s7_target")
        base.write_owner_only(self.env.config, json.dumps(changed))
        try:
            await coordinator.observe(config_source["id"])
            self.assertIsNone(self.db.get_delivery_notice("test", config_source["id"]))
        finally:
            base.write_owner_only(self.env.config, saved_config.decode())
        self.assertFalse(self.native_notice_posts())

    async def test_complete_delivery_before_reserve_resolves_without_notice_and_checks_image_bytes(self):
        source = self.source(images=((PNG_DIGEST, "image/png", len(PNG_BYTES)),))
        coordinator = self.coordinator()
        await coordinator.observe(source["id"])
        self.advance_clock(90)
        # Native delivery really appears after the nominal deadline but before
        # the delayed scanner has reserved a notice.
        actual = self.install_foreign_card(source, image_keys=("img_public_resource_1",))
        self.world.resources[(actual["message_id"], "img_public_resource_1")] = PNG_BYTES
        await coordinator.scan_once()
        row = self.exact_notice_row(source)
        self.assertEqual(row.state, "resolved_without_notice")
        self.assertIsNone(row.notice_message_id)
        self.assertFalse(self.native_notice_posts())
        # Same footer/app with different bytes cannot resolve or permit recovery.
        other = self.source(images=((PNG_DIGEST, "image/png", len(PNG_BYTES)),), content="different source")
        bad = self.install_foreign_card(other, message_id="om_foreign_bad_image", image_keys=("img_bad_resource",))
        self.world.resources[(bad["message_id"], "img_bad_resource")] = b"not-the-signed-image"
        await coordinator.observe(other["id"]); await coordinator.scan_once()
        self.assertNotEqual(self.exact_notice_row(other).state, "resolved_without_notice")
        self.assertEqual(len(self.native_notice_posts()), 0)

    async def test_unknown_notice_post_needs_unique_history_marker_then_exact_mid_get(self):
        pre_effect = self.source(content="crash between notice reservation and POST")
        coordinator = self.coordinator()
        await coordinator.observe(pre_effect["id"])
        pre_row = self.exact_notice_row(pre_effect)
        self.advance_clock(pre_row.deadline_at - self.utc_now)
        self.db.reserve_delivery_notice_send("test", pre_effect["id"],
            expected_state="waiting", now=self.utc_now)
        await coordinator.close()
        self.db.close(); self.db = store.Store(self.tmp / "hostd.db")
        self.round.mapping_store = self.db
        coordinator = self.coordinator()
        await coordinator.scan_once()
        self.assertIn(self.exact_notice_row(pre_effect).state, ("reserved", "unknown"))
        self.assertFalse(self.native_notice_posts(), "restart must not replay a retained pre-effect reservation")

        source = self.source(content="response lost after notice POST")
        await coordinator.observe(source["id"])
        self.advance_clock(60)
        # Drop only the actual CLI reply after the existing lower fake applies POST,
        # so the native effect is real to the fixture but its response is lost.
        dropped = self.drop_next_native_response("POST", "/reply")
        self.loop = asyncio.get_running_loop()
        await coordinator.scan_once()
        self.assertTrue(dropped["value"])
        row = self.exact_notice_row(source)
        self.assertEqual(row.state, "unknown")
        self.assertTrue(row.notice_uuid)
        self.assertTrue(row.notice_content_sha256)
        posts_before = len(self.native_notice_posts())
        native_ids = [m["message_id"] for m in self.world.messages
                      if m.get("sender", {}).get("id") == self.round.desk_app_id
                      and m.get("root_id") == NOTICE_ROOT]
        self.assertEqual(len(native_ids), 1)
        exact_mid = native_ids[0]
        self.thread_history_complete = False
        await coordinator.scan_once()
        self.assertEqual(self.exact_notice_row(source).state, "unknown")
        self.thread_history_complete = True
        duplicate = copy.deepcopy(next(m for m in self.world.messages if m["message_id"] == exact_mid))
        duplicate["message_id"] = "om_duplicate_notice_marker"
        self.world.messages.append(duplicate)
        self.world.threads[NOTICE_ROOT].append(duplicate)
        await coordinator.scan_once()
        self.assertEqual(self.exact_notice_row(source).state, "unknown")
        self.world.messages.remove(duplicate)
        self.world.threads[NOTICE_ROOT].remove(duplicate)
        candidate = next(m for m in self.world.messages if m["message_id"] == exact_mid)
        exact_body = candidate["body"]["content"]
        candidate["body"]["content"] = "{}"
        await coordinator.scan_once()
        self.assertEqual(self.exact_notice_row(source).state, "unknown")
        candidate["body"]["content"] = exact_body
        await coordinator.close()
        self.db.close(); self.db = store.Store(self.tmp / "hostd.db")
        self.round.mapping_store = self.db
        coordinator = self.coordinator()
        await coordinator.scan_once()
        row = self.exact_notice_row(source)
        self.assertEqual(row.state, "noticed")
        self.assertEqual(row.notice_message_id, exact_mid)
        self.assertEqual(len(self.native_notice_posts()), posts_before)
        thread_reads = [x for x in self.world.bot_calls if x[0][2:4] == ["im", "+threads-messages-list"]
                        and NOTICE_ROOT in x[0]]
        self.assertTrue(thread_reads, "candidate search must use complete original-thread history")
        exact_gets = [x for x in self.world.bot_calls if x[0][2:4] == ["api", "GET"]
                      and x[0][4].endswith("/messages/" + exact_mid)]
        self.assertGreaterEqual(len(exact_gets), 1, "history entry must be followed by exact native GET")
        status_message = next(m for m in self.world.messages if m["message_id"] == exact_mid)
        self.assertIn(row.notice_uuid, status_message["body"]["content"])
        self.assertIsNone(self.db.delivery_by_source("test", source["id"], "b2f"))
        dumped = "\n".join(self.db.conn.iterdump())
        self.assertNotIn(source["content"], dumped)
        self.assertNotIn("img_public_resource_1", dumped)

    async def test_latest_valid_text_edit_recovers_same_notice_but_wrong_image_or_root_does_not(self):
        source = self.source(content="original"); coordinator = self.coordinator()
        await coordinator.observe(source["id"])
        self.advance_clock(60); await coordinator.scan_once()
        notice = self.exact_notice_row(source); notice_mid = notice.notice_message_id
        edit = gs.sign_event(FOREIGN_KEY, 40003,
            [["h", CHANNEL], ["e", source["id"]]], "latest signed text", self.utc_now + 1)
        self.world.events.append(edit); self.world.relay_events.append(edit)
        self.advance_clock(1); await coordinator.scan_once()
        delivered = self.install_foreign_card(source, content=edit["content"], message_id="om_latest_edit")
        # Renderer must preserve the original Buzz event mapping while using the
        # complete current signed latest edit as displayed text.
        await coordinator.scan_once()
        self.assertEqual(self.exact_notice_row(source).state, "recovered")
        patches = self.native_writes("PATCH", "/messages/" + notice_mid)
        self.assertEqual(len(patches), 1)
        wrong_root_source = self.source(content="signed image root mismatch",
            images=((PNG_DIGEST, "image/png", len(PNG_BYTES)),))
        wrong_image_source = self.source(content="signed image byte mismatch",
            images=((PNG_DIGEST, "image/png", len(PNG_BYTES)),))
        wrong_body_source = self.source(content="signed native body mismatch")
        await coordinator.observe(wrong_root_source["id"])
        await coordinator.observe(wrong_image_source["id"])
        await coordinator.observe(wrong_body_source["id"])
        self.advance_clock(60); await coordinator.scan_once()
        root_bad = self.install_foreign_card(wrong_root_source, message_id="om_wrong_root_image",
            root_id="om_unrelated_root", image_keys=("img_root_bad",))
        image_bad = self.install_foreign_card(wrong_image_source, message_id="om_wrong_image_bytes",
            image_keys=("img_bytes_bad",))
        self.install_foreign_card(wrong_body_source, message_id="om_wrong_native_body",
            content="forged native body")
        self.world.resources[(root_bad["message_id"], "img_root_bad")] = PNG_BYTES
        self.world.resources[(image_bad["message_id"], "img_bytes_bad")] = b"wrong image bytes"
        await coordinator.scan_once()
        for event in (wrong_root_source, wrong_image_source, wrong_body_source):
            row = self.exact_notice_row(event)
            self.assertEqual(row.state, "noticed")
            self.assertFalse(self.native_writes("PATCH", "/messages/" + row.notice_message_id))

    async def test_lost_recovery_patch_is_get_only_and_cancel_joins_original_io(self):
        first = self.source(content="one")
        second = self.source(content="two")
        third = self.source(content="three")
        coordinator = self.coordinator()
        await coordinator.observe(first["id"]); await coordinator.observe(second["id"]); await coordinator.observe(third["id"])
        self.advance_clock(60); await coordinator.scan_once()
        a, b, c = self.exact_notice_row(first), self.exact_notice_row(second), self.exact_notice_row(third)
        self.assertEqual(len({a.notice_uuid, b.notice_uuid, c.notice_uuid}), 3)
        self.assertEqual(len({a.notice_message_id, b.notice_message_id, c.notice_message_id}), 3)

        # Simulate process loss after committed recovery intent but before PATCH.
        recovery_hash = hashlib.sha256(b"pinned S7 recovery Card1.0").hexdigest()
        self.db.reserve_delivery_notice_recovery("test", second["id"], expected_state="noticed",
            notice_message_id=b.notice_message_id, recovery_version=1,
            recovery_content_sha256=recovery_hash, now=self.utc_now)
        first_delivery = self.install_foreign_card(first, message_id="om_delivered_one")
        def revoke_after_native_get():
            self.advance_clock(1)
            revoked = gs.sign_event(FOREIGN_OWNER_KEY, 30177, [["d", FOREIGN_PUB]],
                json.dumps({"feishu": {"app_id": "cli_revoked_after_get"}}, separators=(",", ":")),
                self.utc_now)
            self.world.relay_events.append(revoked)
        drift = self.mutate_after_native_get("/messages/" + first_delivery["message_id"], revoke_after_native_get)
        await coordinator.scan_once()
        self.assertTrue(drift["value"], "authority drift must occur after the actual GET")
        self.assertEqual(self.exact_notice_row(first).state, "noticed")
        self.assertFalse(self.native_writes("PATCH", "/messages/" + a.notice_message_id))
        # Restore a newer valid owner policy; this keeps the remaining test on a
        # real current source and proves the coordinator can proceed after recovery.
        self.advance_clock(1)
        valid_again = gs.sign_event(FOREIGN_OWNER_KEY, 30177, [["d", FOREIGN_PUB]],
            json.dumps({"feishu": {"app_id": FOREIGN_APP}}, separators=(",", ":")), self.utc_now)
        self.world.relay_events.append(valid_again)
        dropped = self.drop_next_native_response("PATCH", "/messages/" + a.notice_message_id)
        self.loop = asyncio.get_running_loop()
        await coordinator.scan_once()
        self.assertTrue(dropped["value"])
        self.assertEqual(self.exact_notice_row(first).state, "recovery_unknown")
        first_patch_count = len(self.native_writes("PATCH", "/messages/" + a.notice_message_id))
        await coordinator.close()
        self.db.close(); self.db = store.Store(self.tmp / "hostd.db")
        self.round.mapping_store = self.db
        recovered = self.coordinator(); await recovered.scan_once()
        self.assertEqual(self.exact_notice_row(first).state, "recovered")
        self.assertEqual(len(self.native_writes("PATCH", "/messages/" + a.notice_message_id)), first_patch_count)
        gets = [x for x in self.world.bot_calls if x[0][2:4] == ["api", "GET"]
                and x[0][4].endswith("/messages/" + a.notice_message_id)]
        self.assertTrue(gets)
        self.assertEqual(self.exact_notice_row(second).state, "recovery_unknown")
        self.assertFalse(self.native_writes("PATCH", "/messages/" + b.notice_message_id),
            "restart must GET a retained recovery reservation, never replay PATCH")
        # A cancellation at the real native lower-I/O rendezvous must wait for
        # the original worker before coordinator.close and shared client shutdown.
        self.install_foreign_card(third, message_id="om_delivered_three")
        self.loop = asyncio.get_running_loop()
        entered, release = self.block_next_native_patch_after_effect()
        task = asyncio.create_task(recovered.scan_once())
        closing = None
        try:
            arrived = await asyncio.wait_for(asyncio.to_thread(entered.wait, 240), 245)
            self.assertTrue(arrived, "actual native PATCH rendezvous was not reached")
            closing = asyncio.create_task(recovered.close())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if closing is None:
                closing = asyncio.create_task(recovered.close())
            await asyncio.wait_for(closing, 10)
