"""Resource-closure contracts use real Git objects and real OS symlink traversal."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/native_skill_closure.py'


class NativeSkillClosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Closure Fixture')
        self.entry = 'skills/automation/sample-skill'
        self.required_resource = 'skills/shared/resource-guide/references/required.md'
        self.optional_resource = 'skills/shared/optional-guide/references/optional.md'
        self.write(self.entry + '/SKILL.md', '---\nname: sample-skill\n---\n[required](../../shared/resource-guide/references/required.md)\n[conditional](../../shared/optional-guide/references/optional.md)\n')
        self.write(self.entry + '/references/local.md', '# Local\n')
        self.write(self.required_resource, '# Required resource\n[recursive](child.md)\n')
        self.write('skills/shared/resource-guide/references/child.md', '# Child\n')
        self.write(self.optional_resource, '# Optional resource\n')
        self.commit()
        self.profile = {'schema_version': 1, 'name': 'sample-fixture', 'source_commit': self.sha,
                        'entry_tree': self.entry, 'entry_skill': 'sample-skill',
                        'limits': {'max_files': 32, 'max_total_bytes': 65536, 'max_depth': 8},
                        'dependencies': [
                            {'path': self.required_resource, 'activation': 'mandatory', 'reason': 'Entry-required resource'},
                            {'path': 'skills/shared/resource-guide/references/child.md', 'activation': 'mandatory', 'reason': 'Required transitively'},
                            {'path': self.optional_resource, 'activation': 'optional-mode', 'reason': 'Conditional example'}]}
        self.profile_path = self.root / 'profile.json'
        self.save_profile()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.sha = self.git('rev-parse', 'HEAD')

    def save_profile(self):
        self.profile_path.write_text(json.dumps(self.profile))

    def module(self):
        self.assertTrue(SCRIPT.exists(), 'source-pinned native Skill closure builder is not implemented')
        spec = importlib.util.spec_from_file_location('native_skill_closure', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def build(self, enabled=()):
        module = self.module()
        receipt = module.build(self.repo, self.profile_path, self.root / 'bundle', enabled=enabled)
        return module, receipt

    def cli(self, *arguments, status, exit_code):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, arguments)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, exit_code, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertTrue(result.stdout.strip(), 'CLI must emit one JSON receipt')
        self.assertEqual(len(result.stdout.splitlines()), 1)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], status)
        if status == 'blocked':
            self.assertIsInstance(receipt['reason'], str)
            self.assertTrue(receipt['reason'])
        return receipt

    def rewrite_manifest(self, bundle, change):
        path = bundle / 'closure-manifest.json'
        manifest = json.loads(path.read_text())
        change(manifest)
        raw = (json.dumps(manifest) + '\n').encode()
        path.chmod(0o644); path.write_bytes(raw); path.chmod(0o444)
        return hashlib.sha256(raw).hexdigest()

    def fence_dependency(self, fence):
        self.write(self.entry + '/SKILL.md', fence +
                   '[required](../../shared/resource-guide/references/required.md)\n')
        self.commit(); self.profile['source_commit'] = self.sha; self.save_profile()
        module, receipt = self.build()
        self.assertEqual(receipt['status'], 'built')
        self.assertTrue((self.root / 'bundle' / self.required_resource).is_file())
        self.assertEqual(module.verify(self.root / 'bundle', receipt['manifest_sha256'])['status'], 'verified')

    def test_shorter_inner_fence_does_not_hide_following_dependency(self):
        self.fence_dependency('````python\n```\n````\n')

    def test_different_inner_fence_does_not_hide_following_dependency(self):
        self.fence_dependency('```python\n~~~\n```\n')

    def test_fence_closing_character_length_and_tail_match_opening(self):
        module = self.module()
        examples = [
            '````python\n```\n[example](ignored.md)\n````` \t\n',
            '~~~python\n```\n[example](ignored.md)\n~~~~\n',
            '```python\n``` text\n[example](ignored.md)\n```\n',
            '   ```python\n    ```\n[example](ignored.md)\n  ```\n',
        ]
        for example in examples:
            with self.subTest(example=example):
                self.assertEqual(list(module._links((example + '[body](required.md)\n').encode())), ['required.md'])
        # A backtick in a backtick info string makes this ordinary text,
        # rather than an opening block that can hide following references.
        self.assertEqual(list(module._links(b'```bad`info\n[body](required.md)\n')), ['required.md'])

    def test_unclosed_top_level_fence_remains_literal_to_document_end(self):
        self.assertEqual(list(self.module()._links(b'~~~\n[example](ignored.md)\n')), [])

    def test_recognized_container_fences_fail_closed(self):
        module = self.module()
        for opening in ('> ```', '- ```', '1. ~~~', '> - ```', '    ```', '\t~~~'):
            with self.subTest(opening=opening):
                with self.assertRaisesRegex(module.ClosureError, 'unsupported.*fence'):
                    list(module._links((opening + '\n[body](required.md)\n').encode()))

    def test_cli_build_receipts_cover_success_and_existing_output(self):
        arguments = ('build', '--repo', self.repo, '--profile', self.profile_path,
                     '--output', self.root / 'bundle')
        receipt = self.cli(*arguments, status='built', exit_code=0)
        self.assertEqual(receipt['source_commit'], self.sha)
        self.cli(*arguments, status='blocked', exit_code=1)

    def test_cli_build_invalid_profile_types_emit_structured_blocks(self):
        original = json.loads(json.dumps(self.profile))
        cases = [('name', None), ('name', []), ('name', 1),
                 ('entry_skill', None), ('source_commit', None),
                 ('dependencies', None), ('limits', None)]
        for index, (field, value) in enumerate(cases):
            with self.subTest(field=field, value=value):
                self.profile = {**original, field: value}; self.save_profile()
                output = self.root / ('invalid-' + str(index))
                self.cli('build', '--repo', self.repo, '--profile', self.profile_path,
                         '--output', output, status='blocked', exit_code=1)
                self.assertFalse(output.exists())

    def test_cli_verify_receipts_cover_success_and_invalid_manifest_types(self):
        _, receipt = self.build()
        bundle = self.root / 'bundle'
        self.cli('verify', '--bundle', bundle, '--expected-manifest-sha256',
                 receipt['manifest_sha256'], status='verified', exit_code=0)
        original = (bundle / 'closure-manifest.json').read_bytes()
        cases = [(field, value) for field, value in (
            ('source_commit', None), ('profile_name', []), ('profile_sha256', None),
            ('builder_sha256', {}), ('enabled_conditions', None),
            ('enabled_conditions', [[]]), ('missing_conditions', None),
            ('files', None), ('references', None))]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                digest = self.rewrite_manifest(bundle, lambda m: m.__setitem__(field, value))
                self.cli('verify', '--bundle', bundle, '--expected-manifest-sha256',
                         digest, status='blocked', exit_code=1)
                path = bundle / 'closure-manifest.json'
                path.chmod(0o644); path.write_bytes(original); path.chmod(0o444)
        nested = [('files', 'sha256', None), ('files', 'git_mode', []),
                  ('references', 'from', None), ('references', 'state', []),
                  ('references', 'activation', None)]
        for collection, field, value in nested:
            with self.subTest(collection=collection, field=field):
                digest = self.rewrite_manifest(bundle, lambda m: m[collection][0].__setitem__(field, value))
                self.cli('verify', '--bundle', bundle, '--expected-manifest-sha256',
                         digest, status='blocked', exit_code=1)
                path = bundle / 'closure-manifest.json'
                path.chmod(0o644); path.write_bytes(original); path.chmod(0o444)

    def test_cli_verify_entry_receipts_cover_success_and_wrong_alias(self):
        _, receipt = self.build()
        bundle = self.root / 'bundle'
        alias = self.root / 'discovery/sample-skill'
        alias.parent.mkdir(); alias.symlink_to(bundle / self.entry, target_is_directory=True)
        arguments = ('verify-entry', '--bundle', bundle, '--entry', alias,
                     '--expected-manifest-sha256', receipt['manifest_sha256'])
        self.cli(*arguments, status='verified-entry', exit_code=0)
        alias.unlink(); alias.symlink_to(self.repo / self.entry, target_is_directory=True)
        self.cli(*arguments, status='blocked', exit_code=1)

    def test_build_preserves_topology_and_required_recursive_resources(self):
        module, receipt = self.build()
        bundle = self.root / 'bundle'
        self.assertEqual((bundle / self.entry / 'SKILL.md').read_bytes(), (self.repo / self.entry / 'SKILL.md').read_bytes())
        self.assertEqual((bundle / self.required_resource).read_bytes(), (self.repo / self.required_resource).read_bytes())
        self.assertTrue((bundle / 'skills/shared/resource-guide/references/child.md').is_file())
        self.assertEqual(module.verify(bundle, receipt['manifest_sha256'])['status'], 'verified')
        self.assertIn('optional-mode', receipt['missing_conditions'])

    def test_alias_literal_dotdot_resolves_after_symlink_not_lexical_normpath(self):
        module, receipt = self.build()
        bundle = self.root / 'bundle'
        alias = self.root / 'discovery/sample-skill'
        alias.parent.mkdir()
        alias.symlink_to(bundle / self.entry, target_is_directory=True)
        literal = alias / '../../shared/resource-guide/references/required.md'
        self.assertEqual(literal.read_bytes(), (self.repo / self.required_resource).read_bytes())
        self.assertFalse(Path(os.path.normpath(str(literal))).exists())
        result = module.verify_entry(bundle, alias, receipt['manifest_sha256'])
        self.assertEqual(result['entry'], str(bundle / self.entry / 'SKILL.md'))
        self.assertGreater(result['relative_reference_checks'], 0)

    def test_conditional_resource_requires_explicit_selection_and_preflight(self):
        module, receipt = self.build()
        with self.assertRaisesRegex(module.ClosureError, 'condition'):
            module.verify(self.root / 'bundle', receipt['manifest_sha256'], required_conditions=('optional-mode',))
        other = self.root / 'selected'
        selected = module.build(self.repo, self.profile_path, other, enabled=('optional-mode',))
        self.assertTrue((other / self.optional_resource).is_file())
        self.assertEqual(module.verify(other, selected['manifest_sha256'], required_conditions=('optional-mode',))['missing_conditions'], [])

    def test_enabled_but_unreachable_condition_fails_before_output(self):
        self.write(self.entry + '/SKILL.md',
                   '[required](../../shared/resource-guide/references/required.md)\n')
        self.commit(); self.profile['source_commit'] = self.sha; self.save_profile()
        module = self.module()
        self.assertTrue((self.repo / self.optional_resource).is_file())
        with self.assertRaisesRegex(module.ClosureError, 'selected declaration is not reachable'):
            module.build(self.repo, self.profile_path, self.root / 'bundle', enabled=('optional-mode',))
        self.assertFalse((self.root / 'bundle').exists())

    def test_enabled_condition_requires_every_declared_resource_reachable(self):
        orphan = 'skills/shared/optional-guide/references/orphan.md'
        self.write(orphan, '# Selected but unreachable resource\n')
        self.commit(); self.profile['source_commit'] = self.sha
        self.profile['dependencies'].append(
            {'path': orphan, 'activation': 'optional-mode', 'reason': 'Another resource in the same condition'})
        self.save_profile()
        module = self.module()
        with self.assertRaisesRegex(module.ClosureError, 'selected declaration is not reachable'):
            module.build(self.repo, self.profile_path, self.root / 'bundle', enabled=('optional-mode',))
        self.assertFalse((self.root / 'bundle').exists())

    def test_build_reads_fixed_git_objects_not_dirty_checkout(self):
        self.write(self.required_resource, 'dirty source must not be installed\n')
        _, receipt = self.build()
        self.assertTrue((self.root / 'bundle' / self.required_resource).read_text().startswith('# Required resource'))
        self.assertEqual(receipt['source_commit'], self.sha)

    def test_partial_clone_missing_blob_never_invokes_transport_helper(self):
        # Real filtered Git objects, not a mocked cat-file error. The only
        # transport used to construct the fixture is local file://. Afterward
        # the promisor remote points at a marker-only helper with no network.
        self.git('config', 'uploadpack.allowFilter', 'true')
        partial = self.root / 'partial'
        subprocess.check_call(
            ['/usr/bin/git', '-c', 'protocol.file.allow=always', 'clone', '-q',
             '--filter=blob:none', '--no-local', '--no-checkout',
             self.repo.as_uri(), str(partial)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(list((partial / '.git/objects/pack').glob('*.promisor')),
                        'fixture must have a real promisor pack')
        marker = self.root / 'transport-executed'
        helper = self.root / 'marker-only-helper.sh'
        helper.write_text('#!/bin/sh\n: > "$1"\nexit 1\n')
        subprocess.check_call(
            ['/usr/bin/git', '-C', str(partial), 'remote', 'set-url', 'origin',
             'ext::/bin/sh ' + str(helper) + ' ' + str(marker)])
        subprocess.check_call(
            ['/usr/bin/git', '-C', str(partial), 'config', 'protocol.ext.allow', 'always'])
        module = self.module()
        with self.assertRaises(module.ClosureError):
            module.build(partial, self.profile_path, self.root / 'bundle')
        self.assertFalse(marker.exists(), 'offline build executed a repository-configured transport helper')
        self.assertFalse((self.root / 'bundle').exists())

    def test_missing_required_source_target_fails_before_output(self):
        (self.repo / self.required_resource).unlink()
        self.commit(); self.profile['source_commit'] = self.sha; self.save_profile()
        module = self.module()
        with self.assertRaisesRegex(module.ClosureError, 'missing'):
            module.build(self.repo, self.profile_path, self.root / 'bundle')
        self.assertFalse((self.root / 'bundle').exists())

    def test_undeclared_external_dependency_is_not_automatically_installed(self):
        self.write(self.required_resource, '[hidden](unapproved.md)\n')
        self.write('skills/shared/resource-guide/references/unapproved.md', '# Hidden\n')
        self.commit(); self.profile['source_commit'] = self.sha; self.save_profile()
        module = self.module()
        with self.assertRaisesRegex(module.ClosureError, 'undeclared'):
            module.build(self.repo, self.profile_path, self.root / 'bundle')

    def test_reference_style_local_definition_requires_declared_closure(self):
        self.write(self.entry + '/SKILL.md',
                   '[policy][binding]\n[binding]: ../../shared/resource-guide/references/required.md\n')
        self.commit(); self.profile['source_commit'] = self.sha
        self.profile['dependencies'] = []; self.save_profile()
        module = self.module()
        with self.assertRaisesRegex(module.ClosureError, 'undeclared'):
            module.build(self.repo, self.profile_path, self.root / 'bundle')
        self.assertFalse((self.root / 'bundle').exists())

    def test_reference_style_definition_traverses_declared_recursive_dependency(self):
        self.write(self.entry + '/SKILL.md',
                   '[binding][]\n[binding]: ../../shared/resource-guide/references/required.md\n')
        self.commit(); self.profile['source_commit'] = self.sha; self.save_profile()
        module, receipt = self.build()
        manifest = json.loads((self.root / 'bundle/closure-manifest.json').read_text())
        self.assertTrue(any(e['target'] == self.required_resource for e in manifest['references']))
        self.assertEqual(module.verify(self.root / 'bundle', receipt['manifest_sha256'])['status'], 'verified')

    def test_unsupported_local_reference_definition_syntax_fails_closed(self):
        module = self.module()
        target = '../../shared/resource-guide/references/required.md'
        for index, definition in enumerate(('[binding]:\n  ' + target,
                                             '[binding]: ' + target + ' "title"',
                                             '[binding]: <' + target + '>')):
            with self.subTest(definition=definition):
                self.write(self.entry + '/SKILL.md', '[policy][binding]\n' + definition + '\n')
                self.commit(); self.profile['source_commit'] = self.sha
                self.profile['dependencies'] = []; self.save_profile()
                output = self.root / ('unsupported-reference-' + str(index))
                with self.assertRaises(module.ClosureError):
                    module.build(self.repo, self.profile_path, output)
                self.assertFalse(output.exists())

    def test_source_symlink_is_not_followed(self):
        (self.repo / self.required_resource).unlink()
        (self.repo / self.required_resource).symlink_to('child.md')
        self.commit(); self.profile['source_commit'] = self.sha; self.save_profile()
        module = self.module()
        with self.assertRaisesRegex(module.ClosureError, 'regular'):
            module.build(self.repo, self.profile_path, self.root / 'bundle')

    def test_path_escape_and_command_links_are_rejected(self):
        module = self.module()
        for target in ('../../../../outside.md', 'javascript:alert', 'file:///etc/passwd', '$(touch-pwned)', 'https://example.invalid/$(touch-pwned)'):
            with self.subTest(target=target):
                self.write(self.entry + '/SKILL.md', '[bad](' + target + ')\n')
                self.commit(); self.profile['source_commit'] = self.sha; self.save_profile()
                with self.assertRaises(module.ClosureError):
                    module.build(self.repo, self.profile_path, self.root / ('bad-' + str(len(target))))
        self.assertFalse((self.root / 'pwned').exists())

    def test_source_and_budget_bounds_are_enforced(self):
        module = self.module()
        original_limits = dict(self.profile['limits'])
        for field, value in (('max_files', 2), ('max_total_bytes', 5), ('max_depth', 0)):
            with self.subTest(field=field):
                self.profile['limits'] = dict(original_limits)
                self.profile['limits'][field] = value; self.save_profile()
                with self.assertRaises(module.ClosureError):
                    module.build(self.repo, self.profile_path, self.root / ('bad-' + field))

    def test_verify_requires_independently_trusted_manifest_digest(self):
        module, receipt = self.build()
        with self.assertRaisesRegex(module.ClosureError, 'digest'):
            module.verify(self.root / 'bundle', '0' * 64)
        target = self.root / 'bundle' / self.required_resource
        target.chmod(0o644); target.write_text('changed\n')
        with self.assertRaises(module.ClosureError):
            module.verify(self.root / 'bundle', receipt['manifest_sha256'])

    def test_verify_rejects_extra_file_and_symlink_target(self):
        module, receipt = self.build()
        bundle = self.root / 'bundle'
        (bundle / 'extra').write_text('unexpected')
        with self.assertRaises(module.ClosureError):module.verify(bundle, receipt['manifest_sha256'])
        (bundle / 'extra').unlink()
        target = bundle / self.required_resource
        target.unlink(); target.symlink_to(self.repo / self.required_resource)
        with self.assertRaises(module.ClosureError):module.verify(bundle, receipt['manifest_sha256'])

    def test_existing_output_is_never_overwritten(self):
        module, _ = self.build()
        before = (self.root / 'bundle/closure-manifest.json').read_bytes()
        with self.assertRaises(module.ClosureError):module.build(self.repo, self.profile_path, self.root / 'bundle')
        self.assertEqual((self.root / 'bundle/closure-manifest.json').read_bytes(), before)

    def test_wrong_alias_cannot_supply_entry_authority(self):
        module, receipt = self.build()
        alias = self.root / 'wrong'
        alias.symlink_to(self.repo / self.entry, target_is_directory=True)
        with self.assertRaises(module.ClosureError):module.verify_entry(self.root / 'bundle', alias, receipt['manifest_sha256'])

    def test_legacy_flat_copy_is_intact_but_not_dependency_complete(self):
        module, receipt = self.build()
        flat = self.root / 'old-release/sha'
        shutil.copytree(self.repo / self.entry, flat)
        self.assertEqual((flat / 'SKILL.md').read_bytes(), (self.repo / self.entry / 'SKILL.md').read_bytes())
        self.assertFalse((flat / '../../shared/resource-guide/references/required.md').exists())
        with self.assertRaises(module.ClosureError):module.verify_entry(self.root / 'bundle', flat, receipt['manifest_sha256'])

    def test_exact_project_template_reference_is_explicit_not_source_closure(self):
        module = self.module()
        source = self.entry + '/references/local.md'
        self.write(source, '# Project template\nWrite this in the business repository: [rules](../docs/rules.md).\n')
        self.commit(); self.profile['source_commit'] = self.sha
        self.profile['project_references'] = [{'from': source, 'link': '../docs/rules.md',
                                              'reason': 'Project template, not Skill resource',
                                              'source_evidence': source + ':2'}]
        self.save_profile()
        try:
            receipt = module.build(self.repo, self.profile_path, self.root / 'bundle')
        except module.ClosureError as error:
            self.fail('explicit project-context classification is not supported: ' + str(error))
        self.assertEqual(receipt['external_project_reference_count'], 1)
        manifest = json.loads((self.root / 'bundle/closure-manifest.json').read_text())
        project_edges = [e for e in manifest['references'] if e['state'] == 'external-project-evidence']
        self.assertEqual(len(project_edges), 1)
        self.assertIsNone(project_edges[0]['target'])

    def test_caller_profile_can_select_a_different_skill_entry(self):
        alternate = 'skills/automation/alternate-skill'
        self.write(alternate + '/SKILL.md',
                   '---\nname: alternate-skill\n---\n'
                   '[required](../../shared/resource-guide/references/required.md)\n'
                   '[optional](../../shared/optional-guide/references/optional.md)\n')
        self.commit()
        self.profile.update(source_commit=self.sha, entry_tree=alternate,
                            entry_skill='alternate-skill', name='caller-selected')
        self.save_profile()
        module, receipt = self.build(enabled=('optional-mode',))
        bundle = self.root / 'bundle'
        alias = self.root / 'discovery/alternate-skill'
        alias.parent.mkdir()
        alias.symlink_to(bundle / alternate, target_is_directory=True)
        self.assertEqual(module.verify_entry(
            bundle, alias, receipt['manifest_sha256'])['status'], 'verified-entry')
        self.assertEqual((alias / '../../shared/resource-guide/references/required.md').read_bytes(),
                         (self.repo / self.required_resource).read_bytes())
        self.assertFalse((bundle / self.entry).exists())



if __name__ == '__main__':
    unittest.main()
