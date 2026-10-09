"""Private tests-first proposal: native tenant/user scope-type separation.

Only native HTTPS responses are synthetic. The actual CLI adapter, protected
catalog, signed source, own reader, proof verifier and callers stay real.
"""
import importlib
import importlib.util
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BASELINE = TESTS.parent
if not (BASELINE / 'scripts').is_dir():
    BASELINE = BASELINE / 'baseline'
sys.path.insert(0, str(BASELINE / 'scripts'))
sys.path.insert(0, str(BASELINE / 'tests'))

import test_hostd_remote_proofs as proof_fixture


READ_NAMES = frozenset(proof_fixture.READ_SCOPE_GROUPS)
MESSAGE_WRITE = 'im:message:send'
PROOF_NAMES = READ_NAMES | {MESSAGE_WRITE}


def require_native_scopes(testcase):
    spec = importlib.util.find_spec('hostd.native_scopes')
    testcase.assertIsNotNone(spec, 'missing common strict native scope parser: genuine feature RED')
    return importlib.import_module('hostd.native_scopes')


def payload(rows, *, identity='bot', meta=None, **data_extra):
    data = {'scopes': list(rows), **data_extra}
    envelope = {'ok': True, 'identity': identity, 'data': data}
    if meta is not None:
        envelope['meta'] = meta
    return envelope


def row(name, scope_type='tenant', grant_status=1):
    return {'scope_name': name, 'scope_type': scope_type, 'grant_status': grant_status}


class TypedScopeWorld(proof_fixture.World):
    """Real existing proof fixture with typed rows at its native HTTP seam."""

    def __init__(self, case):
        super().__init__(case)
        rows = [row(name) for name in sorted(PROOF_NAMES)]
        rows.extend(row(name, 'user', 1) for name in sorted(PROOF_NAMES))
        self.native_payload = payload(rows)

    def request(self, *args, **kwargs):
        if args[4] == '/open-apis/application/v6/scopes':
            self.api_calls.append((args[3], args[4], kwargs.get('params'), kwargs.get('data')))
            return self.native_payload
        return super().request(*args, **kwargs)


class NativeScopeTypeTests(unittest.IsolatedAsyncioTestCase):
    def native_scopes_module(self):
        # Parser-specific cases report genuine feature RED. Existing consumer
        # cases deliberately run without this guard to expose current behavior.
        return require_native_scopes(self)

    async def test_parser_accepts_same_name_across_types_but_only_tenant_grants(self):
        parser = self.native_scopes_module().parse_bot_scope_envelope
        same = next(iter(sorted(READ_NAMES)))
        names = parser(payload([row(name) for name in sorted(READ_NAMES)]
                               + [row(same, 'user', 1)]))
        self.assertEqual(names, READ_NAMES)
        self.assertEqual(parser(payload([row(name) for name in sorted(READ_NAMES)],
                                       has_more=False, page_token='')),
                         READ_NAMES)

    async def test_parser_rejects_same_pair_and_invalid_or_missing_types_atomically(self):
        parser = self.native_scopes_module().parse_bot_scope_envelope
        name = next(iter(sorted(READ_NAMES)))
        valid = [row(scope) for scope in sorted(READ_NAMES)]
        duplicate_pair = [dict(item) for item in valid]
        duplicate_pair.append(row(name, 'tenant', 0))
        # Replace one valid row for each malformed-envelope case; none may
        # yield a partial set that could satisfy the READ alternatives.
        malformed = []
        missing = [dict(item) for item in valid]
        missing[0].pop('scope_type')
        malformed.append(payload(missing))
        unknown = [dict(item) for item in valid]
        unknown[0]['scope_type'] = 'application'
        malformed.append(payload(unknown))
        wrong_type = [dict(item) for item in valid]
        wrong_type[0]['scope_type'] = 1
        malformed.append(payload(wrong_type))
        boolean = [dict(item) for item in valid]
        boolean[0]['grant_status'] = True
        malformed.append(payload(boolean))
        out_of_range = [dict(item) for item in valid]
        out_of_range[0]['grant_status'] = 2
        malformed.append(payload(out_of_range))
        negative = [dict(item) for item in valid]
        negative[0]['grant_status'] = -1
        malformed.append(payload(negative))
        malformed.extend((payload(duplicate_pair), payload(valid, identity='user')))
        control = [dict(item) for item in valid]
        control[0]['scope_name'] = 'bad\nname'
        malformed.append(payload(control))
        oversized = [dict(item) for item in valid]
        oversized[0]['scope_name'] = 'x' * 257
        malformed.append(payload(oversized))
        malformed.append(payload(valid, has_more=True, page_token='next'))
        malformed.append(payload(valid, has_more=False, page_token='next'))
        malformed.append(payload(valid, meta={'pagination': {'complete': False}}))
        malformed.append(payload(valid + [None]))
        malformed.extend((None, []))
        for envelope in malformed:
            with self.subTest(envelope='invalid native scopes'):
                with self.assertRaises((ValueError, RuntimeError)):
                    parser(envelope)

    async def test_parser_enforces_the_4096_row_bound(self):
        parser = self.native_scopes_module().parse_bot_scope_envelope
        rows = [row(f'scope:{index:04d}') for index in range(4096)]
        self.assertEqual(len(parser(payload(rows))), 4096)
        with self.assertRaises((ValueError, RuntimeError)):
            parser(payload(rows + [row('scope:overflow')]))

    async def test_control_actual_remote_proof_accepts_narrow_tenant_read_and_message_write(self):
        world = TypedScopeWorld(self)
        world.native_payload = payload(row(name) for name in sorted(PROOF_NAMES))
        result = await world.consumer().verify(world.target)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.authorization.evidence.capabilities, ('message',))
        world.no_writes()

    async def test_actual_remote_proof_uses_tenant_rows_and_preserves_exact_capability(self):
        world = TypedScopeWorld(self)
        result = await world.consumer().verify(world.target)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.authorization.evidence.capabilities, ('message',))
        world.no_writes()

    async def test_actual_remote_proof_rejects_user_only_read_grants(self):
        world = TypedScopeWorld(self)
        world.native_payload = payload(
            [row(name, 'user', 1) for name in sorted(PROOF_NAMES)])
        result = await world.consumer().verify(world.target)
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.authorization)
        world.no_writes()

    async def test_control_actual_own_admission_discovery_accepts_tenant_read_rows(self):
        import test_hostd_own_admission_discovery as admission_fixture

        class TenantAdmissionWorld(admission_fixture.AdmissionWorld):
            def __init__(self, case):
                super().__init__(case)
                self.native_payload = payload(row(name) for name in sorted(READ_NAMES))

            def request(self, *args, **kwargs):
                if args[4] == '/open-apis/application/v6/scopes':
                    self.api_calls.append((args[3], args[4], kwargs.get('params'), kwargs.get('data')))
                    return self.native_payload
                return super().request(*args, **kwargs)

        world = TenantAdmissionWorld(self)
        result = await world.adapter().discover()
        self.assertEqual(result.status, 'discovered')
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.readback()['sending_ready'])
        world.no_writes()

    async def test_actual_own_admission_discovery_uses_tenant_read_rows(self):
        import test_hostd_own_admission_discovery as admission_fixture

        class TypedAdmissionWorld(admission_fixture.AdmissionWorld):
            def __init__(self, case):
                super().__init__(case)
                rows = [row(name) for name in sorted(READ_NAMES)]
                rows.extend(row(name, 'user', 1) for name in sorted(READ_NAMES))
                self.native_payload = payload(rows)

            def request(self, *args, **kwargs):
                if args[4] == '/open-apis/application/v6/scopes':
                    self.api_calls.append((args[3], args[4], kwargs.get('params'), kwargs.get('data')))
                    return self.native_payload
                return super().request(*args, **kwargs)

        world = TypedAdmissionWorld(self)
        original = {path: __import__('hashlib').sha256(path.read_bytes()).hexdigest()
                    for path in world.root.rglob('*') if path.is_file()}
        result = await world.adapter().discover()
        self.assertEqual(result.status, 'discovered')
        self.assertFalse(result.readback()['sending_ready'])
        world.no_mutation(original)

    async def test_actual_own_admission_rejects_user_only_read_rows(self):
        import test_hostd_own_admission_discovery as admission_fixture

        class TypedAdmissionWorld(admission_fixture.AdmissionWorld):
            def __init__(self, case):
                super().__init__(case)
                self.native_payload = payload(
                    [row(name, 'user', 1) for name in sorted(READ_NAMES)])

            def request(self, *args, **kwargs):
                if args[4] == '/open-apis/application/v6/scopes':
                    self.api_calls.append((args[3], args[4], kwargs.get('params'), kwargs.get('data')))
                    return self.native_payload
                return super().request(*args, **kwargs)

        world = TypedAdmissionWorld(self)
        result = await world.adapter().discover()
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.candidates, ())
        world.no_writes()

    async def test_dispatch_selector_uses_only_tenant_write_rows(self):
        # Reuse the established optional-websocket guard, which installs only
        # a dependency import placeholder and leaves the real dispatch module.
        import test_hostd_remote_dispatch as dispatch_fixture
        from hostd.remote_proofs import WRITE_SCOPE_GROUPS
        remote_dispatch = importlib.import_module('hostd.remote_dispatch')

        world = TypedScopeWorld(self)
        message_name = MESSAGE_WRITE
        self.assertIn(message_name, WRITE_SCOPE_GROUPS['message'])
        all_read = [row(name) for name in sorted(READ_NAMES)]
        all_write = sorted(set().union(*WRITE_SCOPE_GROUPS.values()))
        world.native_payload = payload(all_read + [row(message_name)]
            + [row(name, 'user', 1) for name in all_write if name != message_name])
        selected = remote_dispatch._native_write_capabilities(
            world.actual_record, world.bot, proof_fixture.CHAT)
        self.assertEqual(selected, ('message',))

    async def test_inherited_bot_scopes_returns_only_native_tenant_grants(self):
        world = TypedScopeWorld(self)
        world.native_payload = payload([row(name) for name in sorted(READ_NAMES)]
                                       + [row(name, 'user', 1) for name in sorted(READ_NAMES)])
        self.assertEqual(world.bot.bot_scopes(), READ_NAMES)
        world.native_payload = payload([row(name, 'user', 1) for name in sorted(READ_NAMES)])
        self.assertEqual(world.bot.bot_scopes(), set())
        world.native_payload = payload([row(name) for name in sorted(READ_NAMES)], identity='user')
        with self.assertRaises(proof_fixture.gs.GroupSyncError):
            world.bot.bot_scopes()

    async def test_fallback_member_gate_rejects_user_only_native_read_rows(self):
        from unittest import mock
        import test_hostd_fallback_discovery as fallback_fixture

        class TypedNativeBotHTTPS(fallback_fixture.NativeBotHTTPS):
            def __init__(self, testcase):
                super().__init__(testcase)
                self.scope_rows = ([row(name, 'tenant', 1) for name in sorted(READ_NAMES)]
                                   + [row(name, 'user', 1) for name in sorted(READ_NAMES)])

            class Connection(fallback_fixture.NativeBotHTTPS.Connection):
                def request(self, method, path, body=None, headers=None):
                    super().request(method, path, body=body, headers=headers)
                    if path == '/open-apis/application/v6/scopes':
                        # Raw Feishu API protocol; BotLarkCli builds/verifies
                        # the full identity envelope above this HTTP seam.
                        self.response = fallback_fixture.Response({'code': 0,
                            'data': {'scopes': self.server.scope_rows}})

        class SynchronousFallbackHarness(fallback_fixture.base.TmpCase):
            assembly = fallback_fixture.FallbackDiscoveryTests.assembly
            seed = fallback_fixture.FallbackDiscoveryTests.seed

            def setup_discovery(self):
                return fallback_fixture.FallbackDiscoveryTests.setup_discovery(self)

            def instance(self):
                return fallback_fixture.FallbackDiscoveryTests.instance(self)

        case = SynchronousFallbackHarness()
        case.setUp()
        try:
            with mock.patch.object(fallback_fixture, 'NativeBotHTTPS', TypedNativeBotHTTPS):
                case.setup_discovery()
            discoverer = case.instance()
            sql = discoverer._sql(fallback_fixture.BINDING)
            mixed = discoverer._bot_members(sql)
            self.assertEqual(set(mixed), {fallback_fixture.base.AGENT_APP,
                                          fallback_fixture.SUBJECT_APP})
            self.assertEqual(set(case.client.bot_scopes()), set(READ_NAMES))
            case.https.scope_rows = [row(name, 'user', 1) for name in sorted(READ_NAMES)]
            with case.assertRaises(ValueError):
                discoverer._bot_members(sql)
            self.assertEqual(case.posts, [])
        finally:
            try:
                self.assertTrue(case.doCleanups(), 'nested fallback cleanup must succeed')
            finally:
                case.tearDown()


if __name__ == '__main__':
    unittest.main()
