"""The us-prod qdrant monitoring key exception is one exact file, never a waiver."""
import copy
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL / 'validators/check_repo_boundary.py'
FIXTURE = SKILL / 'references/approved-exceptions/us-prod-qdrant-monitoring-key.yaml'
RELATIVE = Path('clusters/aws-302571458622-us-prod/victoria-metrics-config/qdrant-monitoring-key.yaml')
APPROVED = FIXTURE.read_bytes()


def check(root):
    return subprocess.run([sys.executable, str(VALIDATOR), str(root), '--repo-context', 'k8s'],
                          capture_output=True, text=True, check=False)


def put(root, data=APPROVED, relative=RELATIVE):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_regression_is_in_actual_ci_entrypoint():
    repository = next(parent for parent in SKILL.parents
                      if (parent / '.gitlab/ci/cicd-developer-offline-e2e.yml').is_file())
    job = yaml.safe_load((repository / '.gitlab/ci/cicd-developer-offline-e2e.yml').read_text())[
        'cicd-developer:offline-e2e']
    commands = [line for line in job['script'] if isinstance(line, str) and ' pytest ' in line]
    assert len(commands) == 1
    assert 'skills/delivery/cicd-developer/tests/test_qdrant_key_boundary.py' in commands[0].split()


def test_fixed_single_external_secret_and_complete_digest(tmp_path):
    assert hashlib.sha256(APPROVED).hexdigest() == (
        '3c01a87c18a5e89819c82c11483b99636bfb3c76581484277d96c73ac54ed2a1')
    docs = list(yaml.safe_load_all(APPROVED))
    assert len(docs) == 1
    doc = docs[0]
    assert (doc['apiVersion'], doc['kind'], doc['metadata']['name'], doc['metadata']['namespace']) == (
        'external-secrets.io/v1', 'ExternalSecret', 'qdrant-monitoring-key', 'victoria-metrics')
    assert doc['spec']['secretStoreRef'] == {'name': 'vault-backend', 'kind': 'ClusterSecretStore'}
    assert [d['remoteRef']['key'] for d in doc['spec']['data']] == [
        'prod/qdrant/application/flywheel-aws/monitoring-apikey']
    put(tmp_path)
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def mutations():
    doc = next(yaml.safe_load_all(APPROVED)); variants = []
    def change(label, mutation):
        altered = copy.deepcopy(doc); mutation(altered); variants.append(pytest.param([altered], id=label))
    change('name', lambda x: x['metadata'].__setitem__('name', 'counterfeit'))
    change('namespace', lambda x: x['metadata'].__setitem__('namespace', 'prod-us'))
    change('store', lambda x: x['spec']['secretStoreRef'].__setitem__('name', 'other'))
    change('vault-path', lambda x: x['spec']['data'][0]['remoteRef'].__setitem__('key', 'prod/qdrant/application/flywheel-aws/apikey'))
    change('extra-key', lambda x: x['spec']['data'].append(copy.deepcopy(x['spec']['data'][0])))
    change('target', lambda x: x['spec']['target'].__setitem__('name', 'qdrant-apikey'))
    change('approved-flag', lambda x: x['metadata'].__setitem__('annotations', {'approved': 'true'}))
    variants.append(pytest.param([doc, {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'x'}}], id='extra-doc'))
    return variants


@pytest.mark.parametrize('documents', mutations())
def test_changed_contract_fails_closed(tmp_path, documents):
    put(tmp_path, yaml.safe_dump_all(documents).encode())
    result = check(tmp_path)
    assert result.returncode == 1
    assert 'exact closed single-ExternalSecret file' in result.stdout


@pytest.mark.parametrize('content', [b'', b'[]\n', b'apiVersion: [\n', APPROVED + b'\n# formatting change\n'])
def test_empty_malformed_and_reformatted_files_fail_closed(tmp_path, content):
    put(tmp_path, content)
    assert check(tmp_path).returncode == 1


def test_file_and_parent_symlinks_cannot_qualify(tmp_path):
    real = tmp_path / 'real'; real.mkdir(); (real / RELATIVE.name).write_bytes(APPROVED)
    target = tmp_path / RELATIVE; target.parent.mkdir(parents=True)
    target.symlink_to(real / RELATIVE.name)
    assert check(tmp_path).returncode == 1
    target.unlink(); target.parent.rmdir(); target.parent.symlink_to(real, target_is_directory=True)
    assert check(tmp_path).returncode == 1


def test_auto_context_is_unaffected(tmp_path):
    put(tmp_path, b'apiVersion: [\n')
    result = subprocess.run([sys.executable, str(VALIDATOR), str(tmp_path)],
                            capture_output=True, text=True, check=False)
    assert 'single-ExternalSecret' not in result.stdout
