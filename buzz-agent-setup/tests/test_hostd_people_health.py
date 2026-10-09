"""People evidence tolerates health changes, never revocation or incomplete proof."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hostd_onboarding_runtime as fixture
from hostd import console_pause
from hostd.console_operations import ConsoleIntent
from hostd.join_cards import BotCards

runtime = fixture.runtime


@unittest.skipUnless(fixture.fixture.AESGCM is not None, 'requires protected catalog dependency')
class PeopleHealth(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.RuntimeTests.setUp
    bound = fixture.RuntimeTests.bound
    create = fixture.RuntimeTests.create

    def status(self, value):
        self.db.conn.execute("UPDATE binding SET status=? WHERE binding_id='alpha'", (value,))

    def pause(self, binding='alpha'):
        principal, epoch = 'ab' * 32, 'cd' * 32
        reservation = self.db.reserve_console_operation(principal, 'ef' * 32, 'binding', binding, 'pause', execution_epoch=epoch, now=1000)
        row = reservation.record
        self.db.claim_console_operation(row.id, principal, epoch, now=1000)
        console_pause.begin(self.db, ConsoleIntent.from_record(row), '12' * 32, (123, 456, '34' * 32), now=1000)

    async def person(self, service):
        return await service.coordinator._person('cli_agent', runtime.Operator('ou_owner', 'on_owner'), 1000)

    async def test_health_states_and_transitions_keep_fresh_owner_proof(self):
        self.bound(); service = await self.create(); original = service.relay.read
        for before, after in [('active', 'active'), ('degraded', 'degraded'), ('active', 'degraded'), ('degraded', 'active')]:
            with self.subTest(before=before, after=after):
                self.status(before); reads = []
                async def read(kind, *args, **kwargs):
                    answer = await original(kind, *args, **kwargs)
                    reads.append(kind)
                    if kind == 'members': self.status(after)
                    return answer
                service.relay.read = read
                person = await self.person(service)
                self.assertEqual(person.pubkey, self.f.owner)
                self.assertEqual(reads.count('members'), 1)
                self.assertEqual(reads.count('people'), 1)
                self.assertEqual(self.db.join_requests(), [])
        row = self.db.bindings()[0]
        self.assertFalse(runtime._same_binding_authority(dict(row, status='degraded'), dict(row, status='active')))

    async def test_all_sources_fenced_at_capture_cannot_supply_identity(self):
        self.bound(); service = await self.create(); self.pause()
        with mock.patch.object(service.relay, 'read', wraps=service.relay.read) as read:
            with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
        self.assertFalse(any(call.args[0] == 'people' for call in read.call_args_list))
        self.assertEqual(service.last_identity_failure, {'stage': 'source_missing'})

    async def test_existing_fenced_source_does_not_block_another_verified_source(self):
        self.bound(); service = await self.create()
        channel = '00000000-0000-0000-0000-000000000002'
        source = dict(json.loads((self.f.root / 'bound.json').read_text()), channel_id=channel, chat_id='oc_second')
        self.f.write(self.f.root / 'second.json', json.dumps(source))
        self.db.reconcile_bindings([fixture.BindingRecord('beta', channel, 'oc_second', 'cli_agent', str(self.f.root / 'second.json'), str(self.f.cfg), str(self.f.data), self.w.mirror)], now=1000)
        self.pause('beta')
        with mock.patch.object(service.relay, 'read', wraps=service.relay.read) as read:
            self.assertEqual((await self.person(service)).pubkey, self.f.owner)
        source_reads = [call.args for call in read.call_args_list if call.args[0] in ('members', 'people')]
        self.assertEqual(source_reads, [('members', fixture.CHANNEL), ('people', fixture.CHANNEL)])
        self.assertEqual(self.db.join_requests(), [])

    async def test_pause_fence_during_read_blocks_complete_people_proof(self):
        self.bound(); service = await self.create(); original = service.relay.read
        async def read(kind, *args, **kwargs):
            answer = await original(kind, *args, **kwargs)
            if kind == 'people': self.pause()
            return answer
        service.relay.read = read
        with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
        self.assertEqual(service.last_identity_failure, {'stage': 'source_pause'})

    async def test_revoked_or_changed_authority_is_not_health_equivalence(self):
        self.bound(); service = await self.create(); original = service.relay.read
        self.db.conn.execute("INSERT INTO app_profile SELECT 'cli_changed',config_dir,data_dir FROM app_profile WHERE app_id='cli_agent'")
        changes = [('status', 'paused'), ('status', 'conflict'), ('status', 'retired'), ('status', 'pending'),
                   ('claimed_at', 1234), ('sync_app_id', 'cli_changed')]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                before = self.db.bindings()[0]
                async def read(kind, *args, **kwargs):
                    answer = await original(kind, *args, **kwargs)
                    if kind == 'people': self.db.conn.execute(f'UPDATE binding SET {field}=? WHERE binding_id=?', (value, 'alpha'))
                    return answer
                service.relay.read = read
                with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
                self.assertEqual(service.last_identity_failure, {'stage': 'source_authority'})
                self.db.conn.execute(f'UPDATE binding SET {field}=? WHERE binding_id=?', (before[field], 'alpha'))

    async def test_protected_file_change_rejects_and_diagnostic_contains_only_stage(self):
        self.bound(); service = await self.create(); original = service.relay.read
        async def read(kind, *args, **kwargs):
            answer = await original(kind, *args, **kwargs)
            if kind == 'people': self.f.write(self.f.root / 'bound.json', '{"secret-canary":"private-data"}')
            return answer
        service.relay.read = read
        with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
        self.assertEqual(service.diagnostics()['last_identity_failure'], {'stage': 'source_config_changed'})
        self.assertNotIn('private-data', json.dumps(service.diagnostics()))

    async def test_closed_during_last_agent_check_cannot_return_identity(self):
        self.bound(); service = await self.create(); original = service._current; calls = 0
        async def current(record):
            nonlocal calls
            answer = await original(record); calls += 1
            if calls == 3: service.close()  # _fresh has two checks; this is post-people.
            return answer
        service._current = current
        with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
        self.assertEqual(service.last_identity_failure, {'stage': 'runtime_scope'})

    async def test_incomplete_people_roles_and_union_conflict_fail_closed(self):
        self.bound(); service = await self.create(); original = service.relay.read
        for failure, expected in [('members', 'source_roles'), ('people', 'source_people'), ('duplicate', 'source_union_conflict')]:
            with self.subTest(failure=failure):
                async def read(kind, *args, **kwargs):
                    answer = await original(kind, *args, **kwargs)
                    if kind == failure: raise ValueError('private-response-canary')
                    if kind == 'people' and failure == 'duplicate': answer.union_ids['ab' * 32] = 'on_owner'
                    return answer
                service.relay.read = read
                with self.assertRaises(runtime.RuntimeErrorNotice) as error: await self.person(service)
                self.assertEqual(service.last_identity_failure, {'stage': expected})
                self.assertNotIn('private-response-canary', str(error.exception) + json.dumps(service.diagnostics()))
                self.assertEqual(self.db.join_requests(), [])

    async def test_ineligible_source_and_removed_admin_cannot_supply_identity(self):
        self.bound(); service = await self.create()
        for status in ('pending', 'paused', 'conflict', 'retired'):
            with self.subTest(status=status):
                self.status(status)
                with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
                self.assertEqual(service.last_identity_failure, {'stage': 'source_missing'})
        self.status('degraded'); original = service.relay.read
        async def read(kind, *args, **kwargs):
            answer = await original(kind, *args, **kwargs)
            return {self.f.owner: 'member'} if kind == 'members' else answer
        service.relay.read = read
        with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
        self.assertEqual(service.last_identity_failure, {'stage': 'source_roles'})

    async def test_all_sources_must_finish_without_cross_source_union_conflict(self):
        self.bound(); service = await self.create(); original = service.relay.read
        channel = '00000000-0000-0000-0000-000000000002'
        source = dict(json.loads((self.f.root / 'bound.json').read_text()), channel_id=channel, chat_id='oc_second')
        self.f.write(self.f.root / 'second.json', json.dumps(source))
        self.db.reconcile_bindings([fixture.BindingRecord('beta', channel, 'oc_second', 'cli_agent', str(self.f.root / 'second.json'), str(self.f.cfg), str(self.f.data), self.w.mirror)], now=1000)
        for failure in ('incomplete', 'conflicting_pubkey', 'conflicting_union', 'none'):
            with self.subTest(failure=failure):
                visited = []
                async def read(kind, *args, **kwargs):
                    if kind in ('members', 'people') and args[0] == channel:
                        visited.append(kind)
                        if kind == 'members': return {self.f.owner: 'admin'}
                        if failure == 'incomplete': raise ValueError('incomplete second source')
                        if failure == 'conflicting_pubkey': return runtime.PeopleBindings({self.f.owner: 'on_different'})
                        if failure == 'conflicting_union': return runtime.PeopleBindings({'ab' * 32: 'on_owner'})
                        return runtime.PeopleBindings({self.f.owner: 'on_owner'})
                    return await original(kind, *args, **kwargs)
                service.relay.read = read
                if failure == 'none':
                    self.assertEqual((await self.person(service)).pubkey, self.f.owner)
                else:
                    with self.assertRaises(runtime.RuntimeErrorNotice): await self.person(service)
                    expected = 'source_people' if failure == 'incomplete' else 'source_union_conflict'
                    self.assertEqual(service.last_identity_failure, {'stage': expected})
                self.assertEqual(visited, ['members', 'people'])
                self.assertEqual(self.db.join_requests(), [])


class IdentityRefusalCopy(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_identity_copy_differs_from_known_nonowner(self):
        class Bot:
            app_id = 'cli_agent'
            texts = []
            def call(self, what, args):
                self.data = json.loads(args[args.index('--data') + 1])
                self.texts.append(json.loads(self.data['content'])['text'])
                return {'message_id': 'om_feedback', 'chat_id': 'oc_private', 'sender': {'id': self.app_id, 'id_type': 'app_id', 'sender_type': 'app'}}
            def message_view(self, message, identity):
                return {'message_id': message, 'chat_id': 'oc_private', 'sender': {'id': self.app_id, 'id_type': 'app_id', 'sender_type': 'app'}, 'body': {'content': self.data['content']}}
        bot = Bot(); cards = BotCards({'cli_agent': bot})
        row = {'request_id': 'JOIN-12345678', 'callback_app_id': 'cli_agent', 'chat_id': 'oc_group'}
        await cards.refuse(row, 'on_owner', 'evt_identity', 'identity')
        await cards.refuse(row, 'on_owner', 'evt_owner', 'owner')
        self.assertIn('身份核验查询未完成，此次未记录审批', bot.texts[0])
        self.assertNotIn('核验为该 agent 的 owner', bot.texts[0])
        self.assertIn('只有该 agent 的 owner 能决定', bot.texts[1])


if __name__ == '__main__': unittest.main()
