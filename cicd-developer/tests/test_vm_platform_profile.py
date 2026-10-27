"""Black-box contracts for the opt-in EU source profile; no production writes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'validators/check_victoriametrics_scrapes.py'
WRAPPER = ROOT / 'validators/validate.sh'
FIXTURE = ROOT / 'tests/fixtures/eu-prod-vm-stack-profile/baseline.json'
CLUSTER = 'aws-740315635167-eu-prod'
RELATIVE = Path('clusters') / CLUSTER / 'vm-stack-observability'
ORIGIN = 'git@gitlab.addx.ai:DEV/observability-platform.git'
PROFILE = 'eu-prod-vm-stack-v1'


def documents(safe=True):
    docs = json.loads(FIXTURE.read_text())
    if safe:
        next(d for d in docs if d['metadata']['name'] == 'victoria-metrics-core-dns' and d['kind'] == 'VMServiceScrape')['spec']['endpoints'][0].pop('bearerTokenFile')
    return docs


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, text=True, capture_output=True).stdout.strip()


def checkout(tmp_path, docs=None, origin=ORIGIN, relative=RELATIVE):
    root = tmp_path / 'source'
    target = root / relative
    target.mkdir(parents=True)
    (target / 'manifests.yaml').write_text(yaml.safe_dump_all(documents() if docs is None else docs, sort_keys=False))
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Offline Contract Test')
    git(root, 'config', 'user.email', 'offline-contract@example.invalid')
    git(root, 'remote', 'add', 'origin', origin)
    commit(root)
    return root, target


def commit(root):
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Test source snapshot')
    return git(root, 'rev-parse', 'HEAD')


def invoke(root, target, *, wrapper=False, flags=None, environment=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('CI')}
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    env.update(environment or {})
    args = flags if flags is not None else ['--vm-scrape-profile', PROFILE, '--vm-scrape-cluster', CLUSTER, '--vm-scrape-source-revision', git(root, 'rev-parse', 'HEAD')]
    cmd = ['/bin/bash', str(WRAPPER)] if wrapper else [sys.executable, str(CHECKER)]
    return subprocess.run([*cmd, str(target), *args], cwd=root, env=env, text=True, capture_output=True, timeout=60)


class PlatformProfileTests:
    @pytest.mark.parametrize('origin', [ORIGIN, 'https://gitlab.addx.ai/DEV/observability-platform.git', 'ssh://git@gitlab.addx.ai/DEV/observability-platform.git'])
    def test_safe_committed_source_passes(self, tmp_path, origin):
        root, target = checkout(tmp_path, origin=origin)
        result = invoke(root, target)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PASS' in result.stdout

    def test_original_source_retains_only_real_auth_failure(self, tmp_path):
        root, target = checkout(tmp_path, documents(safe=False))
        result = invoke(root, target)
        assert result.returncode == 1, result.stdout + result.stderr
        failures = [line for line in result.stdout.splitlines() if line.startswith('FAIL:')]
        assert len(failures) == 1, result.stdout
        assert 'EUVM-AUTH-001' in failures[0]
        assert 'coredns' in failures[0].lower().replace('-', '')

    def test_generic_original_nineteen_failures_preserved(self, tmp_path):
        root, target = checkout(tmp_path, documents(safe=False))
        result = invoke(root, target, flags=[])
        assert result.returncode == 1
        assert len([line for line in result.stdout.splitlines() if line.startswith('FAIL:')]) == 19

    @pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate', 'non_scrape_spec', 'label', 'metadata', 'unknown', 'selector', 'namespace', 'port', 'path', 'interval', 'token', 'ca', 'server', 'insecure', 'authorization', 'basicAuth', 'oauth2'])
    def test_committed_structural_and_auth_changes_rejected(self, tmp_path, change):
        docs = documents()
        scrape = next(d for d in docs if d['kind'] == 'VMServiceScrape')
        endpoint = scrape['spec']['endpoints'][0]
        if change == 'missing': docs.pop()
        elif change == 'extra': docs.append({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'extra', 'namespace': 'victoria-metrics'}})
        elif change == 'duplicate': docs.append(copy.deepcopy(docs[-1]))
        elif change == 'non_scrape_spec': docs[0]['spec']['externalLabels']['cluster'] = 'us-prod'
        elif change == 'label': scrape['metadata']['labels']['app.kubernetes.io/part-of'] = 'victoria-metrics'
        elif change == 'metadata': scrape['metadata']['annotations'] = {'argocd.argoproj.io/sync-options': 'Prune=false'}
        elif change == 'unknown': scrape['unexpected'] = True
        elif change == 'selector': scrape['spec']['selector'] = {'matchLabels': {'app': 'other'}}
        elif change == 'namespace': scrape['spec']['namespaceSelector'] = {'any': True}
        elif change in ('port', 'path', 'interval'): endpoint[change] = {'port': 'other', 'path': '/metrics', 'interval': '30s'}[change]
        elif change == 'token': endpoint['bearerTokenFile'] = '/tmp/token'
        elif change in ('ca', 'server', 'insecure'): endpoint['tlsConfig'] = {'ca': {'caFile': '/tmp/ca'}, 'server': {'serverName': 'attacker.invalid'}, 'insecure': {'insecureSkipVerify': True}}[change]
        else: endpoint[change] = {'secret': 'caller-supplied'}
        root, target = checkout(tmp_path, docs)
        result = invoke(root, target)
        assert result.returncode != 0, f'{change}: {result.stdout}'

    @pytest.mark.parametrize('change', ['repo', 'host', 'path', 'cluster', 'revision', 'dirty', 'extra_file', 'extra_text', 'symlink', 'no_git', 'ci_path', 'ci_id', 'ci_sha', 'ci_incomplete'])
    def test_source_trust_fail_closed(self, tmp_path, change):
        origin = 'git@gitlab.addx.ai:DEV/other.git' if change == 'repo' else ('https://evil.invalid/DEV/observability-platform.git' if change == 'host' else ORIGIN)
        relative = Path('clusters') / CLUSTER / 'elsewhere' if change == 'path' else RELATIVE
        root, target = checkout(tmp_path, origin=origin, relative=relative)
        sha = git(root, 'rev-parse', 'HEAD')
        flags = ['--vm-scrape-profile', PROFILE, '--vm-scrape-cluster', 'us-prod' if change == 'cluster' else CLUSTER, '--vm-scrape-source-revision', '0' * 40 if change == 'revision' else sha]
        env = {}
        if change == 'dirty': (target / 'manifests.yaml').write_text((target / 'manifests.yaml').read_text() + '\n# uncommitted\n')
        elif change in ('extra_file', 'extra_text'):
            (target / ('extra.yaml' if change == 'extra_file' else 'README.txt')).write_text('apiVersion: v1\nkind: ConfigMap\nmetadata: {name: extra}\n')
            commit(root)
            flags[-1] = git(root, 'rev-parse', 'HEAD')
        elif change == 'symlink':
            manifest = target / 'manifests.yaml'
            external = tmp_path / 'external.yaml'
            external.write_text(manifest.read_text())
            manifest.unlink()
            manifest.symlink_to(external)
            commit(root)
            flags[-1] = git(root, 'rev-parse', 'HEAD')
        elif change == 'no_git':
            import shutil
            shutil.rmtree(root / '.git')
        elif change.startswith('ci_'):
            env = {'CI': 'true', 'CI_PROJECT_PATH': 'DEV/observability-platform', 'CI_PROJECT_ID': '1433', 'CI_COMMIT_SHA': sha}
            if change == 'ci_path': env['CI_PROJECT_PATH'] = 'DEV/other'
            elif change == 'ci_id': env['CI_PROJECT_ID'] = '1146'
            elif change == 'ci_sha': env['CI_COMMIT_SHA'] = '0' * 40
            else: env.pop('CI_PROJECT_ID')
        result = invoke(root, target, flags=flags, environment=env)
        assert result.returncode != 0, f'{change}: {result.stdout}'

    def test_verified_ci_identity_passes(self, tmp_path):
        root, target = checkout(tmp_path)
        result = invoke(root, target, environment={'CI': 'true', 'CI_PROJECT_PATH': 'DEV/observability-platform', 'CI_PROJECT_ID': '1433', 'CI_COMMIT_SHA': git(root, 'rev-parse', 'HEAD')})
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize('suffix', [['--repo-context', 'k8s'], ['--repo-context', 'app'], ['--require-target-match']])
    def test_profile_cannot_mix_other_contexts(self, tmp_path, suffix):
        root, target = checkout(tmp_path)
        flags = ['--vm-scrape-profile', PROFILE, '--vm-scrape-cluster', CLUSTER, '--vm-scrape-source-revision', git(root, 'rev-parse', 'HEAD'), *suffix]
        assert invoke(root, target, flags=flags).returncode != 0

    @pytest.mark.parametrize('flags', [['--vm-scrape-profile', PROFILE], ['--vm-scrape-profile', PROFILE, '--vm-scrape-cluster', CLUSTER], ['--vm-scrape-profile', PROFILE, '--vm-scrape-source-revision', 'a' * 40], ['--vm-scrape-cluster', CLUSTER], ['--vm-scrape-source-revision', 'a' * 40]])
    def test_incomplete_options_rejected(self, tmp_path, flags):
        root, target = checkout(tmp_path)
        assert invoke(root, target, flags=flags).returncode != 0

    @pytest.mark.parametrize('safe', [True, False])
    def test_real_wrapper_forwards_and_executes_all_validators(self, tmp_path, safe):
        root, target = checkout(tmp_path, documents(safe=safe))
        result = invoke(root, target, wrapper=True)
        output = result.stdout + result.stderr
        # Every real validator must run; no replacements or success stubs.
        for name in ['check_vault_paths.py', 'check_eso_pushsecret_bug.py', 'check_argocd_application.py', 'check_deploy_image_host.py', 'check_repo_boundary.py', 'check_s3_bucket_delete_guard.py', 'check_object_bucket.py', 'check_cloudfront_oac.py', 'check_sentry_resource_names.py', 'check_sentry_project_config.py', 'check_victoriametrics_scrapes.py', 'check_one_shot_job.py', 'check_cronjob.py', 'check_db_resource_contracts.py', 'check_namespace_pattern.py', 'check_workload_naming.py']:
            assert f'==> {name}' in output, output
        if safe:
            assert result.returncode == 0, output
            assert 'PASS: all validators succeeded' in output
        else:
            assert result.returncode == 1, output
            assert 'EUVM-AUTH-001' in output
            assert 'PASS: all validators succeeded' not in output

    def test_wrapper_trust_failure_is_not_generic_fallback(self, tmp_path):
        root, target = checkout(tmp_path, origin='git@gitlab.addx.ai:DEV/other.git')
        result = invoke(root, target, wrapper=True)
        assert result.returncode != 0
        assert 'PASS: all validators succeeded' not in result.stdout

    def test_default_wrapper_keeps_generic_failure_on_system_bash(self, tmp_path):
        root, target = checkout(tmp_path, documents(safe=False))
        result = invoke(root, target, wrapper=True, flags=[])
        output = result.stdout + result.stderr
        assert result.returncode == 1, output
        assert 'unbound variable' not in output
        assert output.count('==> check_') == 16, output
        scrape_output = output.split('==> check_victoriametrics_scrapes.py', 1)[1].split('==> check_one_shot_job.py', 1)[0]
        assert len([line for line in scrape_output.splitlines() if line.startswith('FAIL:')]) == 19, scrape_output
        assert 'EUVM-AUTH-001' not in scrape_output
        assert 'PASS: all validators succeeded' not in output
