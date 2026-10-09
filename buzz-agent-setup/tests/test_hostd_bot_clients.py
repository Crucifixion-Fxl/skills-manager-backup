"""Bot-only request contracts and a real Round assembly, with no live credentials."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
for path in (SCRIPTS, SCRIPTS / 'hostd', TESTS):
    sys.path.insert(0, str(path))
import test_buzz_feishu_group_sync as base
import bot_clients as bc
import bot_round as br


def ok(data, *, meta=None):
    payload = {'ok': True, 'identity': 'bot', 'data': data}
    if meta is not None:
        payload['meta'] = meta
    return subprocess.CompletedProcess([], 0, json.dumps(payload), '')


class QueueRunner:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if not self.answers:
            raise AssertionError('unexpected CLI call')
        answer = self.answers.pop(0)
        if callable(answer):
            return answer(argv, kwargs)
        return ok(answer)


class ClientContracts(base.TmpCase):
    def test_native_reads_request_original_sent_card_for_history_and_exact_get(self):
        client, _runner = self.client([])
        calls = []
        def request(method, path, params=None, data=None):
            calls.append((method, path, params))
            return {'ok': True, 'identity': 'bot', 'data': {'items': [], 'has_more': False}}
        client._http_request = request
        client._http_call('history', ['im', '+threads-messages-list', '--thread', 'omt_test', '--page-all'], None)
        client.http_pool = object()
        self.assertIsNone(client.message_view('om_test', 'open_id'))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[2]['card_msg_content_type'] == 'user_card_content' for c in calls))

    def client(self, answers, *, apps=None):
        cfg, data = self.tmp / 'bot-cfg', self.tmp / 'bot-data'
        cfg.mkdir(exist_ok=True, mode=0o700)
        data.mkdir(exist_ok=True, mode=0o700)
        base.write_owner_only(cfg / 'config.json', json.dumps({'apps': apps or [{'appId': base.AGENT_APP, 'name': 'sync-agent'}]}))
        runner = QueueRunner(answers)
        client = bc.BotLarkCli(base.AGENT_APP, cfg, data, base_env={'HOME': str(self.tmp), 'BUZZ_PRIVATE_KEY': 'never-child', 'LARKSUITE_CLI_CONFIG_DIR': '/personal/global'}, runner=runner)
        return client, runner

    def test_bot_member_request_preserves_buckets_full_pagination_and_bot_open_ids(self):
        client, runner = self.client([
            {'users': [{'member_id': base.union_of(base.OWNER_OPEN), 'name': 'Owner'}], 'user_total': 1, 'has_more': False, 'truncations': []},
            {'bots': [{'app_id': base.AGENT_APP, 'member_id': base.AGENT_BOT_MEMBER}], 'bot_total': 1, 'has_more': False, 'truncations': []},
        ])
        listing = client.member_listing(base.CHAT, 'union_id')
        self.assertTrue(listing.complete)
        self.assertEqual(set(listing.users), {base.union_of(base.OWNER_OPEN)})
        self.assertEqual(listing.bots, {base.AGENT_APP: base.AGENT_BOT_MEMBER})
        for argv, options in runner.calls:
            self.assertEqual(argv[:2], [bc.NODE, bc.CLI_ENTRY])
            self.assertEqual(argv[argv.index('--as') + 1], 'bot')
            self.assertEqual(argv[argv.index('--profile') + 1], 'sync-agent')
            self.assertEqual(argv[argv.index('--page-limit') + 1], '0')
            self.assertEqual(options['env']['LARKSUITE_CLI_CONFIG_DIR'], str(self.tmp / 'bot-cfg'))
            self.assertNotIn('BUZZ_PRIVATE_KEY', options['env'])

    def test_truncation_or_inconsistent_total_refuses_member_deletion(self):
        for changes in ({'truncations': [{'member_type': 'user', 'limit': 100}]}, {'user_total': 2}, {'has_more': True}):
            listing = {'users': [{'member_id': base.OWNER_OPEN}], 'bots': [], 'user_total': 1, 'bot_total': 0, 'has_more': False, 'truncations': []}
            listing.update(changes)
            client, runner = self.client([listing])
            with self.assertRaises(base.FGS.GroupSyncError) as caught:
                client.change_members('DELETE', base.CHAT, [base.OWNER_OPEN], 'open_id')
            self.assertIn('怎么解决', str(caught.exception))
            self.assertEqual(len(runner.calls), 1)

    def test_bot_profile_mismatch_is_refused_before_any_command(self):
        client, runner = self.client([], apps=[{'appId': base.OWNER_APP}])
        with self.assertRaises(base.FGS.GroupSyncError):
            client.identity()
        self.assertEqual(runner.calls, [])

    def test_app_id_name_collision_cannot_select_somebody_else(self):
        client, runner = self.client([], apps=[{'appId': base.OWNER_APP, 'name': base.AGENT_APP}, {'appId': base.AGENT_APP}])
        with self.assertRaises(base.FGS.GroupSyncError):
            client.identity()
        self.assertEqual(runner.calls, [])

    def test_applicationless_bot_keeps_complete_roster_and_opaque_slot(self):
        client, _ = self.client([
            {'users': [], 'user_total': 0, 'has_more': False, 'truncations': []},
            {'bots': [{'member_id': base.AGENT_BOT_MEMBER}], 'bot_total': 1, 'has_more': False, 'truncations': []},
        ])
        listing = client.member_listing(base.CHAT, 'union_id')
        self.assertTrue(listing.complete)
        self.assertEqual(listing.bots, {})
        self.assertEqual(listing.opaque_bots, frozenset([base.AGENT_BOT_MEMBER]))

    def test_invalid_application_and_duplicate_opaque_bots_remain_incomplete(self):
        for bots in ([{'member_id': base.AGENT_BOT_MEMBER, 'app_id': 'invalid'}],
                     [{'member_id': base.AGENT_BOT_MEMBER, 'app_id': None}],
                     [{'member_id': base.AGENT_BOT_MEMBER, 'app_id': ''}],
                     [{'member_id': 'invalid'}],
                     [{'member_id': base.AGENT_BOT_MEMBER}] * 2):
            client, _ = self.client([
                {'users': [], 'user_total': 0, 'has_more': False, 'truncations': []},
                {'bots': bots, 'bot_total': len(bots), 'has_more': False, 'truncations': []},
            ])
            self.assertFalse(client.member_listing(base.CHAT, 'union_id').complete)

    def test_opaque_bot_occupies_capacity_and_is_never_a_removal_target(self):
        run = br.HostdRound.__new__(br.HostdRound)
        run.cfg = {'human_membership_sync': 'feishu_to_buzz'}
        run.bot_members = {f'cli_known{i}': f'ou_known{i}' for i in range(14)}
        run._opaque_bot_slots = 1
        plan = base.FGS.MembershipPlan(add_users=('on_human',), remove_users=('on_other',), add_bots=('cli_new',))
        actual = run._membership_plan(plan)
        self.assertEqual(actual.add_bots, ())
        self.assertEqual(actual.blocked_bots, ('cli_new',))
        self.assertEqual(actual.remove_bots, ())
        self.assertEqual(actual.add_users, ())
        self.assertEqual(actual.remove_users, ())

    def test_message_and_thread_pagination_flags_come_back_incomplete(self):
        client, runner = self.client([lambda *_: ok({'messages': [], 'has_more': False}, meta={'pagination': {'complete': False}}), {'messages': [], 'has_more': True}])
        self.assertTrue(client.messages(base.CHAT, base.NOW)[1])
        self.assertTrue(client.thread_messages('om_root')[1])
        for argv, _ in runner.calls:
            self.assertEqual(argv[argv.index('--as') + 1], 'bot')
            self.assertIn('--no-reactions', argv)

    def test_missing_pagination_completeness_is_not_assumed(self):
        client, _ = self.client([{'messages': []}])
        self.assertTrue(client.messages(base.CHAT, base.NOW)[1])

    def test_message_projection_checks_id_chat_and_identity_type(self):
        row = base.fmsg('om_root', base.ALICE_OPEN, 'root')
        client, runner = self.client([{'items': [dict(row, message_id='om_other')]}, {'items': [dict(row, chat_id='oc_elsewhere')]}])
        with self.assertRaises(base.FGS.GroupSyncError):
            client.message_view('om_root', 'union_id')
        with self.assertRaises(base.FGS.GroupSyncError):
            client.root_for_event(base.CHAT, {'message_id': 'om_root'})
        params = json.loads(runner.calls[0][0][runner.calls[0][0].index('--params') + 1])
        self.assertEqual(params['user_id_type'], 'union_id')

    def test_reaction_query_follows_tokens_and_omits_incomplete_message(self):
        item = {'operator': {'operator_type': 'user', 'operator_id': base.union_of(base.ALICE_OPEN)}, 'emoji_type': 'SMILE'}
        client, runner = self.client([
            {'success_msg_reaction_details': [{'message_id': 'om_a', 'message_reaction_items': [item], 'has_more': True, 'page_token': 'next'}, {'message_id': 'om_b', 'message_reaction_items': [], 'has_more': True}], 'fail_msg_reaction_details': []},
            {'success_msg_reaction_details': [{'message_id': 'om_a', 'message_reaction_items': [], 'has_more': False}], 'fail_msg_reaction_details': []},
        ])
        details = client.reaction_details(['om_a', 'om_b'], 'union_id')
        self.assertEqual(set(details), {'om_a'})
        argv = runner.calls[1][0]
        query = json.loads(argv[argv.index('--data') + 1])
        self.assertEqual(query['queries'], [{'message_id': 'om_a', 'page_token': 'next'}])
        self.assertTrue(all('user' not in argv for argv, _ in runner.calls))

    def test_reaction_missing_result_on_later_page_discards_accumulated_rows(self):
        client, _ = self.client([
            {'success_msg_reaction_details': [{'message_id': 'om_a', 'message_reaction_items': [], 'has_more': True, 'page_token': 'next'}]},
            {'success_msg_reaction_details': []},
        ])
        self.assertEqual(client.reaction_details(['om_a'], 'union_id'), {})

    def test_duplicate_reaction_rows_are_not_treated_as_complete(self):
        client, _ = self.client([{'success_msg_reaction_details': [
            {'message_id': 'om_a', 'message_reaction_items': [], 'has_more': False},
            {'message_id': 'om_a', 'message_reaction_items': [], 'has_more': True, 'page_token': 'next'}]}])
        self.assertEqual(client.reaction_details(['om_a'], 'union_id'), {})

    def test_image_download_uses_bot_and_accepts_only_local_file(self):
        slot = self.tmp / 'image'
        slot.mkdir(mode=0o700)
        def download(argv, options):
            (Path(options['cwd']) / 'download.png').write_bytes(b'fake image bytes')
            return ok({'saved_path': '/untrusted/output'})
        client, runner = self.client([download])
        self.assertEqual(client.download_image('om_root', 'img_fake', slot), slot / 'download.png')
        self.assertEqual(runner.calls[0][0][runner.calls[0][0].index('--as') + 1], 'bot')

    def test_user_only_operations_cannot_fall_through_to_legacy_cli(self):
        client, runner = self.client([])
        for call in (client.user_info, lambda: client.search_user('private@example.test'), lambda: client.call('bad', ['api', 'GET', '/open-apis/authen/v1/user_info', '--as', 'user'])):
            with self.assertRaises(base.FGS.GroupSyncError):
                call()
        self.assertEqual(runner.calls, [])

    def test_error_notice_preserves_machine_outcome_without_echoing_server_body(self):
        secret = 'private@example.test credential-canary'
        def rejected(*_):
            payload = {'ok': False, 'error': {'type': 'api', 'subtype': secret, 'code': 41050, 'message': secret}}
            return subprocess.CompletedProcess([], 1, '', json.dumps(payload))
        client, _ = self.client([rejected])
        with self.assertRaises(base.FGS.CliError) as caught:
            client.call('contact lookup', ['api', 'GET', '/open-apis/contact/v3/users/ou_fake'])
        self.assertEqual(caught.exception.code, 41050)
        self.assertTrue(caught.exception.definite)
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn('怎么解决', str(caught.exception))
        self.assertIn('复制给 AI', str(caught.exception))


class BotWorld(base.FakeWorld):
    """Actual bot argv adapter plus legacy Buzz and valid bot sends; no user request translations."""
    def __init__(self, tmp):
        super().__init__(tmp)
        self.bot_calls = []
        self.bot_listing_complete = True
        self.bot_app_mismatch = False
        self.bot_scope_failure = False
        self.bot_open = 'ou_syncappalice000000000000000001'

    def _ingest_raw(self, msg):
        row = copy.deepcopy(msg)
        row['chat_id'] = base.CHAT
        created = str(row.get('create_time', ''))
        if not created.isdigit():
            created = str(int(base.FGS._parse_feishu_time(created).timestamp()) * 1000)
        row['create_time'] = created
        content = row.get('content', '')
        body = ({'image_key': content.removeprefix('[Image: ').removesuffix(']')}
                if row.get('msg_type') == 'image' else {'text': content})
        row.setdefault('body', {'content': json.dumps(body)})
        if row.get('sender', {}).get('sender_type') == 'user': row['sender']['id'] = self.bot_open
        return row

    def __call__(self, argv, **kwargs):
        if argv[0] != bc.NODE:
            return super().__call__(argv, **kwargs)
        self.bot_calls.append((argv, kwargs))
        assert argv[1] == bc.CLI_ENTRY
        args = argv[2:]
        assert args[args.index('--as') + 1] == 'bot'
        if args[:2] == ['api', 'GET'] and args[2] == '/open-apis/application/v6/scopes':
            if self.bot_scope_failure:
                return base.fail(args, 99991672)
            return ok({'scopes': [{'scope_name': scope, 'scope_type': 'tenant', 'grant_status': 1} for scope in bc.READ_SCOPE_GROUPS.keys()]})
        if args[:2] == ['im', '+chat-members-list']:
            kind = args[args.index('--member-types') + 1] if '--member-types' in args else 'all'
            idtype = args[args.index('--member-id-type') + 1] if '--member-id-type' in args else 'open_id'
            out = {'has_more': False, 'truncations': [] if self.bot_listing_complete else [{'limit': 100}]}
            if kind in ('all', 'user', 'user,bot'):
                users = [base.union_of(u) if idtype == 'union_id' else u for u in self.users]
                out.update(users=[{'member_id': u, 'name': 'Human'} for u in users], user_total=len(users))
            if kind in ('all', 'bot', 'user,bot'):
                out.update(bots=[{'app_id': app, 'member_id': mid} for app, mid in self.bots.items()], bot_total=len(self.bots))
            return ok(out)
        if args[:3] == ['api', 'GET', '/open-apis/im/v1/messages']:
            params = json.loads(args[args.index('--params') + 1])
            rows = [self._ingest_raw(m) for m in self.messages]
            rows = [r for r in rows if int(params['start_time'])*1000 <= int(r['create_time']) < (int(params['end_time'])+1)*1000]
            rows.sort(key=lambda r:int(r['create_time']))  # Stable provider order at ties.
            offset = int(params.get('page_token', 0)); size = params['page_size']
            more = offset + size < len(rows)
            return ok({'items': rows[offset:offset+size], 'has_more': more, 'page_token': str(offset+size) if more else ''})
        if args[:2] == ['api', 'GET'] and args[2].startswith('/open-apis/im/v1/messages/'):
            mid = args[2].rsplit('/', 1)[1]
            item = self._message_item(mid, 'open_id')
            params = json.loads(args[args.index('--params') + 1])
            if item is not None:
                item = dict(item, chat_id=base.CHAT)
                sender = dict(item.get('sender') or {})
                if sender.get('sender_type') == 'user':
                    sender['id'] = base.union_of(base.ALICE_OPEN) if params['user_id_type'] == 'union_id' else self.bot_open
                    sender['id_type'] = params['user_id_type']
                    item['sender'] = sender
            if item is not None:
                original = next((m for m in [*self.messages, *(m for rows in self.threads.values() for m in rows)] if m['message_id']==mid), None)
                if original is not None:
                    raw = self._ingest_raw(original)
                    for key in ('create_time','body','deleted','thread_id'):
                        if key in raw: item.setdefault(key, raw[key])
            if item is not None and self.message_get_tamper:
                item = self.message_get_tamper(mid, params['user_id_type'], item)
            return ok({'items': [] if item is None else [item]})
        if args[:2] == ['im', '+chat-messages-list']:
            rows = []
            for item in self.messages:
                row = copy.deepcopy(item)
                row['chat_id'] = base.CHAT
                if row['sender'].get('sender_type') == 'user':
                    row['sender']['id'] = self.bot_open
                rows.append(row)
            return ok({'messages': rows, 'has_more': False})
        if args[:2] == ['im', '+threads-messages-list']:
            root = args[args.index('--thread') + 1]
            return ok({'messages': [dict(item, chat_id=base.CHAT) for item in self.threads.get(root, [])], 'has_more': False})
        if args[:2] == ['api', 'GET'] and args[2] == f'/open-apis/im/v1/chats/{base.CHAT}':
            return ok(dict(self.chat, owner_id=base.union_of(base.OWNER_OPEN), owner_id_type='union_id'))
        if args[:2] == ['im', 'reactions'] and args[2] == 'batch_query':
            queries = json.loads(args[args.index('--data') + 1])['queries']
            return ok({'success_msg_reaction_details': [{'message_id': q['message_id'], 'message_reaction_items': [], 'has_more': False} for q in queries], 'fail_msg_reaction_details': []})
        # Legacy FakeWorld's bot send/edit/react paths remain valid without altering argv identities.
        result = self._lark(args, kwargs['env'], kwargs.get('cwd'))
        if isinstance(result, dict):
            result = subprocess.CompletedProcess(args, 0, json.dumps(result), '')
        payload = json.loads(result.stdout or result.stderr)
        payload['identity'] = 'bot'
        result.stdout = json.dumps(payload) if result.returncode == 0 else ''
        result.stderr = json.dumps(payload) if result.returncode else ''
        return result


class RoundAssembly(base.TmpCase):
    def assembly(self, **overrides):
        env = base.Env(self.tmp, **overrides)
        cfg = base.FGS.load_config(env.config)
        agentcfg = self.tmp / 'agent-cfg' / 'config.json'
        base.write_owner_only(agentcfg, json.dumps({'apps': [{'appId': base.AGENT_APP}]}))
        world = BotWorld(self.tmp)
        world.members = [m for m in world.members if m['pubkey'] != base.CAROL_PK]
        clients = bc.build_clients(cfg, env.base_env, runner=world, http=world.http_get, trusted_relays={'https://relay.test'})
        state = base.FGS.load_state(env.state_dir)
        state.binding = f'{base.CHANNEL}|{base.CHAT}'
        state.floor = state.feishu_since = state.buzz_since = state.react_since = int(base.NOW.timestamp()) - 300
        report = base.FGS._new_report()
        run = br.HostdRound(cfg, clients, state, report, base.NOW, lambda: None, auth_clock=lambda: base.NOW)
        return env, cfg, world, run

    def narrow_scope_assembly(self, scopes, *, grant_status=1, identity='bot'):
        _, _, world, run = self.assembly()
        def native_runner(argv, **kwargs):
            if argv[:4] == [bc.NODE, bc.CLI_ENTRY, 'api', 'GET'] and argv[4] == '/open-apis/application/v6/scopes':
                world.bot_calls.append((argv, kwargs))
                self.assertEqual(argv[argv.index('--as') + 1], 'bot')
                self.assertEqual(argv[argv.index('--profile') + 1], base.AGENT_APP)
                self.assertEqual(kwargs['env']['LARKSUITE_CLI_CONFIG_DIR'], str(self.tmp / 'agent-cfg'))
                self.assertNotIn('BUZZ_PRIVATE_KEY', kwargs['env'])
                payload = {'ok': True, 'identity': identity, 'data': {'scopes': [
                    {'scope_name': scope, **({'scope_type': 'tenant'} if identity == 'bot' else {}),
                     'grant_status': grant_status} for scope in scopes]}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), '')
            return world(argv, **kwargs)
        for client in run.clients.agents.values():
            client.runner = native_runner
        return world, run

    def test_narrow_chat_read_native_grants_allow_actual_round_preflight(self):
        world, run = self.narrow_scope_assembly(['im:chat:read', 'im:chat.members:read', 'im:message:readonly'])
        run.verify_identities()
        self.assertEqual(run.verified_agents, {base.AGENT_PK})
        self.assertEqual(len(world.bot_calls), 1)

    def test_narrow_chat_read_does_not_replace_member_scope(self):
        _, run = self.narrow_scope_assembly(['im:chat:read', 'im:message:readonly'])
        with self.assertRaises(base.FGS.GroupSyncError) as error:
            run.verify_identities()
        self.assertIn('怎么解决', str(error.exception))
        self.assertIn('复制给 AI', str(error.exception))
        self.assertEqual(run.verified_agents, set())

    def test_narrow_chat_read_ungranted_scope_rejected(self):
        _, run = self.narrow_scope_assembly(['im:chat:read', 'im:chat.members:read', 'im:message:readonly'], grant_status=0)
        with self.assertRaises(base.FGS.GroupSyncError):
            run.verify_identities()
        self.assertEqual(run.verified_agents, set())

    def test_narrow_chat_read_wrong_native_identity_or_profile_rejected(self):
        _, run = self.narrow_scope_assembly(['im:chat:read', 'im:chat.members:read', 'im:message:readonly'], identity='user')
        with self.assertRaises(base.FGS.GroupSyncError):
            run.verify_identities()
        self.assertEqual(run.verified_agents, set())
        base.write_owner_only(self.tmp / 'agent-cfg' / 'config.json', json.dumps({'apps': [{'appId': base.OWNER_APP}]}))
        with self.assertRaises(base.FGS.GroupSyncError):
            run.verify_identities()
        self.assertEqual(run.verified_agents, set())

    def test_full_assembly_uses_syncbot_reads_and_verified_union_owner(self):
        env, cfg, world, run = self.assembly()
        before = copy.deepcopy(cfg)
        world.messages = [base.fmsg('om_a', base.ALICE_OPEN, 'hello')]
        run.verify_identities()
        run.load_people()
        run.load_directory()
        run.verify_desk()
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(run.owner_id, base.union_of(base.OWNER_OPEN))
        self.assertIs(run.clients.owner, run.clients.agents[base.AGENT_APP])
        self.assertEqual(run.cfg['owner_app_id'], base.AGENT_APP)
        self.assertEqual(cfg, before)
        self.assertEqual(run.report['to_buzz'], 1)
        self.assertEqual(run.id_to_pubkey[base.union_of(base.ALICE_OPEN)], base.ALICE_PK)
        self.assertTrue(world.bot_calls)
        self.assertTrue(all(argv[argv.index('--as') + 1] == 'bot' for argv, _ in world.bot_calls))
        self.assertNotIn('authen/v1/user_info', json.dumps(world.bot_calls))
        self.assertNotEqual(world.bot_open, base.ALICE_OPEN)

    def test_bot_role_need_not_be_specific_desk_name(self):
        _, _, world, run = self.assembly()
        world.display[base.AGENT_PK] = 'ordinary-test-agent'
        run.verify_identities(); run.load_people(); run.verify_desk()
        self.assertIn(base.AGENT_PK, run.verified_agents)

    def test_buzz_to_feishu_full_assembly_uses_agent_bot_for_human_message(self):
        _, _, world, run = self.assembly()
        world.events = [base.event('ab' * 32, base.ALICE_PK, 'from Buzz')]
        run.verify_identities(); run.load_people(); run.load_directory(); run.verify_desk()
        run.buzz_to_feishu()
        self.assertEqual(run.report['to_feishu'], 1)
        self.assertEqual(run.state.b2f_senders['ab' * 32], base.AGENT_APP)
        self.assertTrue(all(argv[argv.index('--as') + 1] == 'bot' for argv, _ in world.bot_calls))

    def test_migrated_membership_baseline_has_no_deletions(self):
        _, cfg, world, run = self.assembly()
        cfg['identity'] = 'email'
        run.state.feishu_seen['o:old-app:' + base.EXTRA_OPEN] = ''
        run.state.members_synced = 123
        world.users = {base.OWNER_OPEN, base.EXTRA_OPEN}
        migration = br.prepare_union_migration(cfg, run.state, run.clients, authorized=True, now=base.NOW)
        migrated = br.HostdRound(migration.config, run.clients, migration.state, base.FGS._new_report(), base.NOW, lambda: None,
                                reader_namespace=migration.reader_app_id)
        migrated.verify_identities(); migrated.load_people(); migrated.verify_desk()
        migrated.reconcile_members()
        self.assertFalse(any('DELETE' in argv for argv, _ in world.bot_calls))
        self.assertEqual(migrated.report['removed_users'], 0)
        self.assertEqual(migrated.report['removed_bots'], 0)

    def test_real_reconciliation_counts_opaque_slot_after_roster_consumption(self):
        _, _, world, run = self.assembly(membership_sync='buzz_to_feishu')
        world.bots = {base.AGENT_APP: base.AGENT_BOT_MEMBER,
                      **{f'cli_foreign{i}': f'ou_foreign{i}' for i in range(13)}}
        old_runner = run.clients.owner.runner
        opaque = 'ou_opaque0000000000000000000000001'
        def with_opaque(argv, **kwargs):
            result = old_runner(argv, **kwargs)
            if '+chat-members-list' in argv and argv[argv.index('--member-types') + 1] == 'bot':
                payload = json.loads(result.stdout)
                payload['data']['bots'].append({'member_id': opaque})
                payload['data']['bot_total'] += 1
                return subprocess.CompletedProcess(argv, result.returncode, json.dumps(payload), result.stderr)
            return result
        run.clients.owner.runner = with_opaque
        run.verify_identities(); run.load_people(); run.verify_desk()
        run.directory = {base.AGENT2_PK: 'cli_newbot'}
        run.reconcile_members()
        changes = [argv for argv, _ in world.bot_calls if argv[2:4] in (['api', 'POST'], ['api', 'DELETE'])]
        self.assertFalse(any('cli_newbot' in ' '.join(argv) or opaque in ' '.join(argv) for argv in changes))
        self.assertEqual(run.report['blocked_bots'], 1)
        self.assertEqual(run._opaque_bot_slots, 0)

    def test_one_way_initial_baseline_is_recorded_after_complete_success(self):
        _, _, world, run = self.assembly(membership_sync='buzz_to_feishu')
        run.verify_identities(); run.load_people(); run.verify_desk()
        run.reconcile_members()
        self.assertFalse(any('DELETE' in argv for argv, _ in world.bot_calls))
        self.assertEqual(run.state.members_synced, run.now_ts)

    def test_unpaired_union_projection_cannot_borrow_legacy_owner_open_id(self):
        _, _, world, run = self.assembly(feishu_unmapped_senders='skip')
        world.messages = [base.fmsg('om_a', base.ALICE_OPEN, 'hello')]
        def tamper(mid, idtype, item):
            if idtype == 'union_id':
                item['sender'] = dict(item['sender'], id=base.OWNER_OPEN, id_type='open_id')
            return item
        world.message_get_tamper = tamper
        run.verify_identities(); run.load_people(); run.verify_desk()
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(run.report['to_buzz'], 0)
        self.assertNotIn(world.bot_open, run.state.idmap)

    def test_email_mode_explicitly_requires_union_migration(self):
        env = base.Env(self.tmp, identity='email')
        cfg = base.FGS.load_config(env.config)
        with self.assertRaises(base.FGS.GroupSyncError) as caught:
            br.runtime_config(cfg)
        self.assertIn('union_id', str(caught.exception))
        self.assertIn('怎么解决', str(caught.exception))

    def test_missing_owner_union_never_falls_back_to_legacy_open_id(self):
        _, _, world, run = self.assembly()
        world.union_missing.add(base.OWNER_PK)
        run.verify_identities()
        with self.assertRaises(base.FGS.GroupSyncError):
            run.load_people()
        self.assertNotEqual(run.owner_id, base.OWNER_OPEN)

    def test_same_app_id_wrong_profile_or_missing_permissions_fail_preflight(self):
        _, _, world, run = self.assembly()
        world.bot_scope_failure = True
        with self.assertRaises(base.FGS.GroupSyncError):
            run.verify_identities()

    def test_partial_member_listing_performs_no_membership_writes(self):
        _, _, world, run = self.assembly()
        world.bot_listing_complete = False
        run.verify_identities(); run.load_people()
        with self.assertRaises(base.FGS.GroupSyncError):
            run.reconcile_members()
        self.assertFalse(any('POST' in argv or 'DELETE' in argv for argv, _ in world.bot_calls))

    def test_unpaired_cross_app_sender_is_not_matched_using_legacy_id_cache(self):
        _, _, world, run = self.assembly(feishu_unmapped_senders='skip')
        world.messages = [base.fmsg('om_a', base.ALICE_OPEN, 'hello')]
        run.state.idmap[world.bot_open] = base.union_of(base.BOB_OPEN)  # polluted prior app cache
        run.verify_identities(); run.load_people(); run.verify_desk()
        self.assertEqual(run.state.idmap, {})  # first bot-owned namespace must discard legacy unscoped cache
        run.feishu_to_buzz(only_threads=set())
        self.assertEqual(run.report['to_buzz'], 1)
        self.assertIn('[飞书] Alice：hello', [send['content'] for send in world.buzz_sends()])

    def test_known_reply_reaction_event_directly_fetches_and_registers_missing_root(self):
        _, _, world, run = self.assembly()
        world.messages = [base.fmsg('om_oldroot', base.ALICE_OPEN, 'root', thread_id='omt_oldroot')]
        world.threads = {'om_oldroot': [dict(base.fmsg('om_reply', base.ALICE_OPEN, 'reply', thread_id='omt_oldroot'), root_id='om_oldroot')]}
        run.verify_identities(); run.load_people(); run.verify_desk()
        root = run.recover_feishu_event({'type': 'im.message.reaction.created_v1', 'message_id': 'om_reply'})
        self.assertEqual(root['message_id'], 'om_oldroot')
        self.assertIn('om_oldroot', run.state.threads)
        self.assertTrue(any('/open-apis/im/v1/messages/om_oldroot' in argv for argv, _ in world.bot_calls))

    def test_union_migration_is_explicit_read_only_and_preserves_delivery_ledger(self):
        _, cfg, world, run = self.assembly()
        cfg['identity'] = 'email'
        state = run.state
        state.idmap[base.ALICE_OPEN] = base.union_of(base.ALICE_OPEN)
        state.emailmap['legacy-hash'] = base.ALICE_OPEN
        state.feishu_seen['o:old-app:' + base.ALICE_OPEN] = ''
        state.f2r['om_a|' + base.ALICE_OPEN + '|SMILE'] = 'ab' * 32
        state.people_seen[f'o:{cfg["owner_app_id"]}:{base.ALICE_OPEN}'] = f'{base.ALICE_PK}|{int(base.NOW.timestamp())}'
        state.members_synced = 123
        state.b2f['cd' * 32] = 'om_existing'
        before_cfg, before_state = copy.deepcopy(cfg), copy.deepcopy(state)
        with self.assertRaises(base.FGS.GroupSyncError):
            br.prepare_union_migration(cfg, state, run.clients)
        migration = br.prepare_union_migration(cfg, state, run.clients, authorized=True, now=base.NOW)
        self.assertEqual(cfg, before_cfg)
        self.assertEqual(state, before_state)
        self.assertEqual(migration.config['identity'], 'union_id')
        self.assertEqual(migration.config['owner_open_id'], base.OWNER_OPEN)
        self.assertEqual(migration.state.members_synced, 0)
        self.assertEqual(migration.state.idmap, {})
        self.assertEqual(migration.state.f2r, {'om_a|' + base.union_of(base.ALICE_OPEN) + '|SMILE': 'ab' * 32})
        self.assertEqual(migration.reaction_key_mapping, {'om_a|' + base.ALICE_OPEN + '|SMILE':
                                                        'om_a|' + base.union_of(base.ALICE_OPEN) + '|SMILE'})
        self.assertEqual(migration.state.b2f, state.b2f)
        self.assertEqual(migration.reader_app_id, base.AGENT_APP)
        self.assertFalse(any('POST' in argv or 'DELETE' in argv for argv, _ in world.bot_calls))

    def test_failed_union_preflight_does_not_reset_original_state(self):
        _, cfg, world, run = self.assembly()
        cfg['identity'] = 'email'
        run.state.emailmap['legacy-hash'] = base.ALICE_OPEN
        before = copy.deepcopy(run.state)
        world.union_missing.add(base.OWNER_PK)
        with self.assertRaises(base.FGS.GroupSyncError):
            br.prepare_union_migration(cfg, run.state, run.clients, authorized=True, now=base.NOW)
        self.assertEqual(run.state, before)

    def test_pending_member_events_block_identity_migration(self):
        _, cfg, _, run = self.assembly()
        run.state.member_events['pending'] = {'id': 'ab' * 32}
        with self.assertRaises(base.FGS.GroupSyncError):
            br.prepare_union_migration(cfg, run.state, run.clients, authorized=True, now=base.NOW)

    def test_reaction_alias_coalescing_holds_migration_without_losing_cardinality(self):
        _, cfg, _, run = self.assembly()
        union = base.union_of(base.ALICE_OPEN)
        run.state.f2r = {f'om_a|{base.ALICE_OPEN}|SMILE': 'ab' * 32, f'om_a|{union}|SMILE': 'ab' * 32}
        run.state.people_seen[f'o:{cfg["owner_app_id"]}:{base.ALICE_OPEN}'] = f'{base.ALICE_PK}|{int(base.NOW.timestamp())}'
        original = copy.deepcopy(run.state)
        with self.assertRaises(base.FGS.GroupSyncError):
            br.prepare_union_migration(cfg, run.state, run.clients, authorized=True, now=base.NOW)
        self.assertEqual(run.state, original)

    def test_admin_signer_mapping_is_protected_as_existing_semantics_allow(self):
        _, _, world, run = self.assembly()
        for member in world.members:
            if member['pubkey'] == base.OWNER_PK:
                member['role'] = 'admin'
        run.verify_identities(); run.load_people(); run.verify_desk()
        self.assertEqual(run.owner_id, base.union_of(base.OWNER_OPEN))

    def test_migrated_ack_reaction_is_neither_published_nor_withdrawn(self):
        _, cfg, world, run = self.assembly()
        cfg['identity'] = 'email'
        run.state.f2r['om_a|' + base.ALICE_OPEN + '|SMILE'] = 'ab' * 32
        run.state.people_seen[f'o:{cfg["owner_app_id"]}:{base.ALICE_OPEN}'] = f'{base.ALICE_PK}|{int(base.NOW.timestamp())}'
        run.state.rwatch['om_a'] = 'cd' * 32 + '|' + str(int(base.NOW.timestamp()) + 300)
        migration = br.prepare_union_migration(cfg, run.state, run.clients, authorized=True, now=base.NOW)
        migrated = br.HostdRound(migration.config, run.clients, migration.state, base.FGS._new_report(), base.NOW, lambda: None,
                                reader_namespace=migration.reader_app_id)
        migrated.verify_identities(); migrated.load_people(); migrated.verify_desk()
        item = {'operator': {'operator_type': 'user', 'operator_id': base.union_of(base.ALICE_OPEN)}, 'emoji_type': 'SMILE'}
        # Use the real bot reaction adapter with only transport responses scripted.
        original = world.__call__
        def runner(argv, **options):
            if argv[0] == bc.NODE and argv[2:5] == ['im', 'reactions', 'batch_query']:
                return ok({'success_msg_reaction_details': [{'message_id': 'om_a', 'message_reaction_items': [item], 'has_more': False}]})
            return original(argv, **options)
        run.clients.owner.runner = runner
        migrated.feishu_reactions_to_buzz()
        self.assertEqual(migrated.report['reactions_to_buzz'], 0)
        self.assertEqual(migrated.report['reactions_withdrawn_in_buzz'], 0)
        self.assertEqual(list(migrated.state.f2r.values()), ['ab' * 32])

    def test_unknown_or_pending_reaction_identity_holds_migration(self):
        _, cfg, _, run = self.assembly()
        for value in ('ab' * 32, 'pending:123'):
            cfg['identity'] = 'email'
            run.state.f2r['om_a|' + base.ALICE_OPEN + '|SMILE'] = value
            with self.assertRaises(base.FGS.GroupSyncError):
                br.prepare_union_migration(cfg, run.state, run.clients, authorized=True, now=base.NOW)

    def test_legacy_approval_command_does_not_receive_join_authorization_tags(self):
        _, _, _, run = self.assembly()
        self.assertIsNone(run._approval_tags(object(), 'buzz-root'))


if __name__ == '__main__':
    unittest.main()
