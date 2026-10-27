"""Close both approved files, cardinality, identity, privileges and neighbours."""
import copy
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'validators'))
from _streampark_tls_contract import TLS_FILES


def fixture(relative):
    side = Path(relative).stem
    return (SKILL / 'references/approved-exceptions' / f'streampark-stage-tls-{side}.yaml').read_bytes()


def put(root):
    for relative, digest in TLS_FILES.items():
        data = fixture(relative)
        assert hashlib.sha256(data).hexdigest() == digest
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def check(root, context='k8s'):
    return subprocess.run([sys.executable, str(SKILL / 'validators/check_repo_boundary.py'),
                           str(root), '--repo-context', context], capture_output=True, text=True)


@pytest.mark.parametrize('ordinary_document', [False, True])
def test_both_reserved_files_absent_keeps_normal_k8s_review(tmp_path, ordinary_document):
    if ordinary_document:
        (tmp_path / 'ordinary.yaml').write_text(
            'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: ordinary\n')
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = 'checked 1 YAML document(s)' if ordinary_document else 'nothing to check'
    assert expected in result.stdout
    assert 'TLS source exception' not in result.stdout


@pytest.mark.parametrize('context', ['app', 'auto', 'argocd-apps', 'crossplane-infra'])
@pytest.mark.parametrize('mutation', ['none', 'remove-file', 'comment'])
def test_other_explicit_contexts_keep_their_existing_resource_rules(tmp_path, context, mutation):
    """Ordinary SA/RBAC/Store rules elsewhere do not invoke the k8s TLS waiver."""
    put(tmp_path)
    target = tmp_path / next(iter(TLS_FILES))
    if mutation == 'remove-file':
        target.unlink()
    elif mutation == 'comment':
        target.write_bytes(target.read_bytes() + b'\n# outside the k8s byte contract\n')
    result = check(tmp_path, context)
    assert result.returncode == 0, result.stdout + result.stderr
    count = 4 if mutation == 'remove-file' else 8
    assert f'checked {count} YAML document(s) for {context}' in result.stdout
    assert 'TLS source exception' not in result.stdout


def test_exact_eight_and_ci_entrypoint(tmp_path):
    put(tmp_path)
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'checked 8 YAML document(s)' in result.stdout
    job = yaml.safe_load((SKILL.parents[2] / '.gitlab/ci/cicd-developer-offline-e2e.yml').read_text())['cicd-developer:offline-e2e']
    command = next(x for x in job['script'] if isinstance(x, str) and ' pytest ' in x)
    assert 'skills/delivery/cicd-developer/tests/test_streampark_tls_boundary.py' in command.split()
    assert not job.get('allow_failure', False)


@pytest.mark.parametrize('relative', TLS_FILES)
@pytest.mark.parametrize('mutation', ['remove-file', 'empty', 'malformed', 'comment', 'remove-doc',
                                     'extra-doc', 'namespace', 'role-list', 'token-subject', 'eso',
                                     'vault', 'vault-role', 'audience', 'ttl', 'mount', 'automount', 'flag'])
def test_changed_or_incomplete_eight_fail_closed(tmp_path, relative, mutation):
    put(tmp_path)
    target = tmp_path / relative
    docs = list(yaml.safe_load_all(target.read_text()))
    if mutation == 'remove-file':
        target.unlink()
    elif mutation in ['empty', 'malformed', 'comment']:
        target.write_bytes({'empty': b'', 'malformed': b'kind: [\n',
                            'comment': fixture(relative) + b'\n# changed\n'}[mutation])
    else:
        jwt = docs[3]['spec']['provider']['vault']['auth']['jwt']
        if mutation == 'remove-doc': docs.pop(0)
        elif mutation == 'extra-doc': docs.append(copy.deepcopy(docs[0]))
        elif mutation == 'namespace': docs[0]['metadata']['namespace'] = 'other'
        elif mutation == 'role-list': docs[1]['rules'][0]['verbs'].append('list')
        elif mutation == 'token-subject': docs[1]['rules'][0]['resourceNames'] = ['*']
        elif mutation == 'eso': docs[2]['subjects'][0]['namespace'] = 'other'
        elif mutation == 'vault': docs[3]['spec']['provider']['vault']['server'] = 'https://other.invalid'
        elif mutation == 'vault-role': jwt['role'] = 'admin'
        elif mutation == 'audience': jwt['kubernetesServiceAccountToken']['audiences'] = ['sts.amazonaws.com']
        elif mutation == 'ttl': jwt['kubernetesServiceAccountToken']['expirationSeconds'] = 3600
        elif mutation == 'mount': jwt['path'] = 'jwt-eks-prod'
        elif mutation == 'automount': docs[0]['automountServiceAccountToken'] = True
        elif mutation == 'flag': docs[3]['metadata']['annotations'] = {'approved': 'true'}
        target.write_text(yaml.safe_dump_all(docs))
    result = check(tmp_path)
    assert result.returncode == 1
    assert 'TLS source exception requires the exact four-object file' in result.stdout


@pytest.mark.parametrize('relative', TLS_FILES)
@pytest.mark.parametrize('change', ['filename', 'cluster', 'directory'])
def test_fixed_bytes_at_other_paths_have_no_exception(tmp_path, relative, change):
    target = Path(relative)
    if change == 'filename': target = target.with_name('other.yaml')
    elif change == 'cluster': target = Path(str(target).replace('390709477306', '002497567426'))
    else: target = target.parent.parent / 'other' / target.name
    path = tmp_path / target
    path.parent.mkdir(parents=True)
    path.write_bytes(fixture(relative))
    result = check(tmp_path)
    assert result.returncode == 1
    assert 'namespace-local SecretStore' in result.stdout


@pytest.mark.parametrize('parent_link', [False, True])
def test_symlinks_do_not_qualify(tmp_path, parent_link):
    root = tmp_path / 'review'
    put(root)
    target = root / next(iter(TLS_FILES))
    if parent_link:
        real = tmp_path / 'real'
        target.parent.rename(real)
        target.parent.symlink_to(real, target_is_directory=True)
    else:
        real = tmp_path / 'real.yaml'
        target.rename(real)
        target.symlink_to(real)
    assert check(root).returncode == 1


def test_new_vault_paths_are_not_generic_consumer_grants(tmp_path):
    """This batch has no ExternalSecret; future consumers need separate review."""
    for i, key in enumerate(['staging/docker-builder/service/streampark-builder/server-tls',
                             'staging/docker-builder/application/streampark/client-tls']):
        doc = {'apiVersion': 'external-secrets.io/v1', 'kind': 'ExternalSecret',
               'metadata': {'name': f'other-{i}'}, 'spec': {'data': [
                   {'secretKey': 'key.pem', 'remoteRef': {'key': key, 'property': 'key.pem'}}]}}
        (tmp_path / f'other-{i}.yaml').write_text(yaml.safe_dump(doc))
    result = subprocess.run([sys.executable, str(SKILL / 'validators/check_vault_paths.py'),
                             str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'no rule' in result.stdout
