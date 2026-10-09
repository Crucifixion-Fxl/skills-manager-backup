import json
import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('scan', Path(__file__).parents[1] / 'scripts/scan.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

class InventoryTests(unittest.TestCase):
    def test_skill_symlink_is_rejected_before_any_source_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root / 'external.md'
            external.write_text('must not be opened')
            skill = root / 'skills/quality/example/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.symlink_to(external)
            with patch.object(Path, 'read_text', side_effect=AssertionError('source opened before guard')):
                with self.assertRaisesRegex(ValueError, 'non-repository source'):
                    scan.scan(root)

    def test_dependency_cache_and_symlink_directories_are_not_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'skills/customer-care/customer-care/SKILL.md'
            source.parent.mkdir(parents=True)
            source.write_text('---\nname: customer-care\n---\n')
            for ignored in ('node_modules', '.venv', '.git', '__pycache__'):
                other = source.parent / ignored / 'borrowed/SKILL.md'
                other.parent.mkdir(parents=True)
                other.write_text(source.read_text())
            (root / 'skills/external').symlink_to(source.parent, target_is_directory=True)
            inventory = scan.scan(root)
            self.assertEqual(inventory['totals']['skills'], 1)
            self.assertEqual(inventory['records'][0]['skill_path'], 'skills/customer-care/customer-care/SKILL.md')

    def test_catalog_resolves_classified_owner_and_rejects_duplicate_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner = root / 'skills/observability/one/SKILL.md'
            owner.parent.mkdir(parents=True)
            owner.write_text('---\nname: one\n---\n认证 https://service.example/api\n')
            registry = {'tools': [{'id': 'one', 'owner': 'one', 'reference': str(owner.relative_to(root))}]}
            result = scan.catalog(root, scan.scan(root), registry)
            self.assertEqual(result['tools'][0]['owner'], 'one')
            self.assertEqual(result['tools'][0]['domainCandidates'], ['service.example'])
            duplicate = root / 'skills/development/one/SKILL.md'
            duplicate.parent.mkdir(parents=True)
            duplicate.write_text(owner.read_text())
            with self.assertRaisesRegex(ValueError, 'duplicate Skill name'):
                scan.catalog(root, scan.scan(root), registry)

    def test_source_changes_invalidate_digest_and_unclassified_sources_remain_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / 'skills/one/SKILL.md'
            first.parent.mkdir(parents=True)
            first.write_text('---\nname: one\n---\n认证 https://service.example/api\n')
            other = root / 'skills/two/SKILL.md'
            other.parent.mkdir()
            other.write_text('---\nname: two\n---\nhttps://docs.example/readme\n')
            dep = root / 'skills/one/node_modules/dependency/SKILL.md'
            dep.parent.mkdir(parents=True)
            dep.write_text('must not scan installed plugins')
            registry = {'tools': [{'id': 'one', 'owner': 'one', 'reference': 'skills/one/SKILL.md'}]}
            result = scan.scan(root)
            self.assertEqual(result['totals']['skills'], 2)
            self.assertEqual(result['domain_evidence']['service.example'][0]['line'], 4)
            before = scan.catalog(root, result, registry)
            self.assertEqual(before['unclassifiedReferences'][0]['domainCandidates'], ['docs.example'])
            first.write_text(first.read_text() + '新增功能\n')
            after = scan.catalog(root, scan.scan(root), registry)
            self.assertNotEqual(before['tools'][0]['sourceDigest'], after['tools'][0]['sourceDigest'])

    def test_registered_auth_profile_changes_invalidate_digest_and_status_comes_from_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner = root / 'skills/one'
            (owner / 'references').mkdir(parents=True)
            (owner / 'SKILL.md').write_text('---\nname: one\n---\n')
            profile = owner / 'references/auth-profile.json'
            profile.write_text(json.dumps({'schemaVersion': 1, 'tool': 'one', 'owner': 'one', 'provider': 'delegated', 'runtimeVerification': 'pending'}))
            registry = {'tools': [{'id': 'one', 'owner': 'one', 'reference': 'skills/one/SKILL.md', 'accessProfile': 'skills/one/references/auth-profile.json', 'runtimeVerification': 'stale'}]}
            before = scan.catalog(root, scan.scan(root), registry)
            self.assertEqual(before['tools'][0]['runtimeVerification'], 'pending')
            profile.write_text(json.dumps({'schemaVersion': 1, 'tool': 'one', 'owner': 'one', 'provider': 'bearer-env', 'origin': 'https://other.example', 'runtimeVerification': 'read-only-pilot-verified'}))
            after = scan.catalog(root, scan.scan(root), registry)
            self.assertNotEqual(before['tools'][0]['sourceDigest'], after['tools'][0]['sourceDigest'])
            self.assertEqual(after['tools'][0]['runtimeVerification'], 'read-only-pilot-verified')

    def test_source_capability_changes_invalidate_owner_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner = root / 'skills/one'
            (owner / 'references').mkdir(parents=True)
            (owner / 'SKILL.md').write_text('---\nname: one\n---\n')
            capability = owner / 'references/source-capabilities.json'
            capability.write_text('{"routes": ["/first"]}')
            registry = {'tools': [{'id': 'one', 'owner': 'one', 'reference': 'skills/one/SKILL.md'}]}
            before = scan.catalog(root, scan.scan(root), registry)
            capability.write_text('{"routes": ["/second"]}')
            after = scan.catalog(root, scan.scan(root), registry)
            self.assertNotEqual(before['tools'][0]['sourceDigest'], after['tools'][0]['sourceDigest'])

    def test_missing_owner_or_contract_fails_instead_of_claiming_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                scan.catalog(root, scan.scan(root), {'tools': [{'id': 'missing', 'owner': 'missing', 'reference': 'absent.md'}]})

    def test_discovery_json_changes_digest_without_generated_inventory_recursion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner = root / 'skills/one'
            refs = owner / 'references'
            refs.mkdir(parents=True)
            (owner / 'SKILL.md').write_text('---\nname: one\n---\n')
            registry = {'tools': [{'id': 'one', 'owner': 'one', 'reference': 'skills/one/SKILL.md'}]}
            for filename in ('auth-discovery.json', 'source-discovery.json', 'source-api-catalog.json'):
                path = refs / filename
                path.write_text('{"origin":"https://first.addx.live"}')
                before = scan.catalog(root, scan.scan(root), registry)
                path.write_text('{"origin":"https://second.addx.live"}')
                after = scan.catalog(root, scan.scan(root), registry)
                self.assertNotEqual(before['tools'][0]['sourceDigest'], after['tools'][0]['sourceDigest'])
            baseline = scan.scan(root)
            (refs / 'inventory.json').write_text('{"recursive":"https://fake.addx.live"}')
            (refs / 'tools.json').write_text('{"recursive":"https://fake.addx.live"}')
            self.assertEqual(baseline['records'], scan.scan(root)['records'])
            self.assertNotIn('fake.addx.live', scan.scan(root)['domain_evidence'])

    def test_shared_owner_contract_changes_invalidate_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            refs = root / 'skills/one/references'
            refs.mkdir(parents=True)
            (refs.parent / 'SKILL.md').write_text('---\nname: one\n---\n')
            shared = root / 'skills/two/references/shared.md'
            shared.parent.mkdir(parents=True)
            shared.write_text('auth v1 https://first.addx.live')
            (refs / 'auth-discovery.json').write_text(json.dumps({'contract': '../../two/references/shared.md'}))
            registry = {'tools': [{'id': 'one', 'owner': 'one', 'reference': 'skills/one/SKILL.md'}]}
            before = scan.catalog(root, scan.scan(root), registry)
            shared.write_text('auth v2 wss://second.addx.live')
            after = scan.catalog(root, scan.scan(root), registry)
            self.assertNotEqual(before['tools'][0]['sourceDigest'], after['tools'][0]['sourceDigest'])
            self.assertIn('second.addx.live', after['tools'][0]['domainCandidates'])

    def test_timestamp_is_current_and_domain_triage_does_not_promote_examples(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            result = scan.scan(Path(tmp))
        stamp = datetime.fromisoformat(result['generated_on'])
        self.assertLess(abs((datetime.now(timezone.utc) - stamp).total_seconds()), 10)
        self.assertEqual(scan.classify_domain('graylog.internal.example.com', 'AGENTS.md', ''), 'placeholder')
        self.assertEqual(scan.classify_domain('metabase.addx.live', 'common-apps.md', ''), 'deployment-example-candidate')
        self.assertEqual(scan.classify_domain('gitlab.addx.ai', 'SKILL.md', ''), 'source-or-reference')
        self.assertEqual(scan.classify_domain('docs.vendor.com', 'SKILL.md', ''), 'official-reference-candidate')

    def test_explicit_triage_requires_sources_and_disposition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'AGENTS.md'
            source.write_text('Graylog has placeholder URL')
            entry = {'id': 'graylog', 'disposition': 'placeholder-no-instance', 'reason': 'explicit placeholder', 'sources': ['AGENTS.md']}
            result = scan.catalog(root, scan.scan(root), {'tools': [], 'platformTriage': [entry]})
            self.assertEqual(result['platformTriage'], [entry])
            for mutation in ({'sources': []}, {'disposition': 'supported'}, {'reason': ''}, {'sources': ['absent.md']}):
                with self.assertRaises(ValueError):
                    scan.catalog(root, scan.scan(root), {'tools': [], 'platformTriage': [dict(entry, **mutation)]})

    def test_repository_profiles_and_discovery_are_synchronized(self):
        root = Path(__file__).resolve().parents[4]
        registry = json.loads((root / 'skills/agent-harness/platform-onboarding/references/registry.json').read_text())
        ids = [item['id'] for item in registry['tools']]
        self.assertEqual(len(ids), len(set(ids)))
        result = scan.catalog(root, scan.scan(root), registry)
        self.assertGreater(len(result['tools']), 49)
        for item in registry['tools']:
            self.assertNotIn(item['owner'], {'remote-web-session', 'tracker-manager'})
            for field in ('accessProfile', 'authDiscovery', 'sourceDiscovery'):
                if item.get(field):
                    document = json.loads((root / item[field]).read_text())
                    self.assertEqual(document['tool'], item['id'])
                    self.assertEqual(document['owner'], item['owner'])
            if item.get('authDiscovery'):
                discovery = json.loads((root / item['authDiscovery']).read_text())
                contract = discovery.get('contract')
                if contract:
                    self.assertTrue(((root / item['authDiscovery']).parent / contract).is_file())

if __name__ == '__main__':
    unittest.main()

class OwnerSSOTTests(unittest.TestCase):
    def test_duplicate_platform_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('one','two'):
                p=root/'skills'/name/'SKILL.md';p.parent.mkdir(parents=True);p.write_text('---\nname: '+name+'\n---\n')
            registry={'tools':[{'id':'platform','owner':n,'reference':f'skills/{n}/SKILL.md'} for n in ('one','two')]}
            with self.assertRaisesRegex(ValueError,'duplicate platform'):
                scan.catalog(root,scan.scan(root),registry)

    def test_duplicate_auth_copy_in_business_consumer_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('owner','consumer'):
                refs=root/'skills'/name/'references';refs.mkdir(parents=True)
                (refs.parent/'SKILL.md').write_text('---\nname: '+name+'\n---\n')
                (refs/'auth-profile.json').write_text(json.dumps({'tool':'platform','owner':name,'provider':'delegated'}))
            registry={'tools':[{'id':'platform','owner':'owner','reference':'skills/owner/SKILL.md','accessProfile':'skills/owner/references/auth-profile.json','businessConsumers':['consumer']}]}
            with self.assertRaisesRegex(ValueError,'duplicate authentication SSOT'):
                scan.catalog(root,scan.scan(root),registry)

    def test_declared_consumer_has_a_single_owner_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('owner','consumer'):
                p=root/'skills'/name/'SKILL.md';p.parent.mkdir(parents=True);p.write_text('---\nname: '+name+'\n---\n登录 https://service.example\n')
            registry={'tools':[{'id':'platform','owner':'owner','reference':'skills/owner/SKILL.md','businessConsumers':['consumer']}]}
            catalog=scan.catalog(root,scan.scan(root),registry)
            self.assertEqual(catalog['tools'][0]['businessConsumers'],['consumer'])
            self.assertFalse(catalog['unclassifiedReferences'])
