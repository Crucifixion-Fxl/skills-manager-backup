"""A fixed authorized instance must never become a source-authored waiver."""
import copy
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL / 'validators/check_repo_boundary.py'
FIXTURE = SKILL / 'references/approved-exceptions/streampark-stage-pull.yaml'
RELATIVE = Path('clusters/aws-390709477306-us-staging/streampark-access/registry-pull.yaml')
APPROVED = FIXTURE.read_bytes()

def test_security_regressions_are_in_actual_ci_entrypoint():
    repository = next(parent for parent in SKILL.parents if (parent/'.gitlab/ci/cicd-developer-offline-e2e.yml').is_file())
    job = yaml.safe_load((repository/'.gitlab/ci/cicd-developer-offline-e2e.yml').read_text())['cicd-developer:offline-e2e']
    commands = [line for line in job['script'] if isinstance(line, str) and ' pytest ' in line]
    assert len(commands) == 1
    assert 'skills/delivery/cicd-developer/tests/test_streampark_pull_boundary.py' in commands[0].split()
    assert job.get('allow_failure', False) is False

def check(root):
    return subprocess.run([sys.executable, str(VALIDATOR), str(root), '--repo-context', 'k8s'],
                          capture_output=True, text=True, check=False)

def put(root, data=APPROVED, relative=RELATIVE):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target

def test_fixed_approved_five_and_complete_digest(tmp_path):
    assert hashlib.sha256(APPROVED).hexdigest() == '63431c97819e39e6e52fdb119797a4245c962f11d1515f0bae5442457b8844b7'
    docs = list(yaml.safe_load_all(APPROVED))
    assert [(d['apiVersion'], d['kind'], d['metadata']['name'], d['metadata']['namespace']) for d in docs] == [
        ('v1', 'ServiceAccount', 'eso-vault-reader-streampark-job-pull', 'staging-streampark-jobs'),
        ('rbac.authorization.k8s.io/v1', 'Role', 'eso-vault-reader-streampark-job-pull-tokenrequest', 'staging-streampark-jobs'),
        ('rbac.authorization.k8s.io/v1', 'RoleBinding', 'eso-vault-reader-streampark-job-pull-tokenrequest', 'staging-streampark-jobs'),
        ('external-secrets.io/v1', 'SecretStore', 'vault-streampark-job-pull-reader', 'staging-streampark-jobs'),
        ('external-secrets.io/v1', 'ExternalSecret', 'streampark-job-pull', 'staging-streampark-jobs'),
    ]
    put(tmp_path)
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'checked 5 YAML document(s)' in result.stdout

@pytest.mark.parametrize('removed', range(5))
def test_removing_any_document_cannot_inherit_exception(tmp_path, removed):
    docs = list(yaml.safe_load_all(APPROVED)); docs.pop(removed)
    put(tmp_path, yaml.safe_dump_all(docs).encode())
    assert check(tmp_path).returncode == 1

def mutations():
    docs = list(yaml.safe_load_all(APPROVED)); variants = []
    def change(label, mutation):
        altered = copy.deepcopy(docs); mutation(altered); variants.append(pytest.param(altered, id=label))
    for i in range(5):
        change('name-' + str(i), lambda x, i=i: x[i]['metadata'].__setitem__('name', 'counterfeit'))
        change('namespace-' + str(i), lambda x, i=i: x[i]['metadata'].__setitem__('namespace', 'staging-other'))
    change('extra-sixth', lambda x: x.append(copy.deepcopy(x[0])))
    change('role-list-secrets', lambda x: x[1]['rules'][0].__setitem__('verbs', ['create', 'list']))
    change('wildcard-token-subject', lambda x: x[1]['rules'][0].__setitem__('resourceNames', ['*']))
    change('wrong-eso-subject', lambda x: x[2]['subjects'][0].__setitem__('namespace', 'other'))
    change('vault-admin-role', lambda x: x[3]['spec']['provider']['vault']['auth']['jwt'].__setitem__('role', 'admin'))
    change('wrong-vault-server', lambda x: x[3]['spec']['provider']['vault'].__setitem__('server', 'https://example.org'))
    change('long-ttl', lambda x: x[3]['spec']['provider']['vault']['auth']['jwt']['kubernetesServiceAccountToken'].__setitem__('expirationSeconds', 3600))
    change('wrong-audience', lambda x: x[3]['spec']['provider']['vault']['auth']['jwt']['kubernetesServiceAccountToken'].__setitem__('audiences', ['sts.amazonaws.com']))
    change('shared-target', lambda x: x[4]['spec']['target'].__setitem__('name', 'harbor-registry-secret'))
    change('wrong-credential-type', lambda x: x[4]['spec']['target']['template'].__setitem__('type', 'Opaque'))
    change('added-data', lambda x: x[4]['spec']['target']['template'].__setitem__('data', {'extra': 'counterfeit'}))
    change('different-vault-path', lambda x: x[4]['spec']['data'][0]['remoteRef'].__setitem__('key', 'prod/other/application/other/credentials'))
    change('source-approved-flag', lambda x: x[3]['metadata'].__setitem__('annotations', {'approved': 'true'}))
    return variants

@pytest.mark.parametrize('documents', mutations())
def test_changed_security_contract_fails_closed(tmp_path, documents):
    put(tmp_path, yaml.safe_dump_all(documents).encode())
    result = check(tmp_path)
    assert result.returncode == 1
    assert 'exact closed five-object file' in result.stdout

@pytest.mark.parametrize('content', [b'', b'[]\n', b'apiVersion: [\n', APPROVED + b'\n# formatting change\n',
                                     APPROVED + b'---\nkind: Secret\nmetadata: {name: extra}\n'])
def test_empty_malformed_extra_and_reformatted_files_fail_closed(tmp_path, content):
    put(tmp_path, content)
    assert check(tmp_path).returncode == 1

@pytest.mark.parametrize('relative', [
    'clusters/aws-002497567426-us-tech-service/streampark-access/registry-pull.yaml',
    'clusters/aws-390709477306-us-staging/other/registry-pull.yaml',
    'clusters/aws-390709477306-us-staging/streampark-access/renamed.yaml',
])
def test_approved_content_at_other_path_has_default_denial(tmp_path, relative):
    put(tmp_path, relative=Path(relative))
    result = check(tmp_path)
    assert result.returncode == 1
    assert 'literal per-app Vault path' in result.stdout and 'namespace-local SecretStore' in result.stdout

def test_file_and_parent_symlinks_cannot_qualify(tmp_path):
    real = tmp_path / 'real'; real.mkdir(); (real / 'registry-pull.yaml').write_bytes(APPROVED)
    target = tmp_path / RELATIVE; target.parent.mkdir(parents=True)
    target.symlink_to(real / 'registry-pull.yaml')
    assert check(tmp_path).returncode == 1
    target.unlink(); target.parent.rmdir(); target.parent.symlink_to(real, target_is_directory=True)
    assert check(tmp_path).returncode == 1

def test_unrelated_business_store_cannot_borrow_approved_flag(tmp_path):
    docs = list(yaml.safe_load_all(APPROVED)); store = docs[3]
    store['metadata']['name'] = 'other'; store['metadata']['annotations'] = {'approved': 'true'}
    put(tmp_path, yaml.safe_dump(store).encode(), Path('clusters/other/store.yaml'))
    result = check(tmp_path)
    assert result.returncode == 1 and 'namespace-local SecretStore' in result.stdout


def prepare_git_context(root, origin='https://gitlab.addx.ai/DEV/k8s.git', tracked=True):
    commands = [
        ['init', '-q'],
        ['config', '--local', 'remote.origin.url', origin],
        ['config', '--local', 'user.name', 'Isolated boundary test'],
        ['config', '--local', 'user.email', 'boundary-test@example.invalid'],
    ]
    if tracked:
        commands.append(['add', '--', str(RELATIVE)])
    commands.append(['commit', '-q', '--allow-empty', '-m', 'isolated fixture'])
    for command in commands:
        subprocess.run(['git', '-C', str(root), *command], check=True, capture_output=True)


def vault_check(root, relative=False):
    return subprocess.run([sys.executable, str(SKILL/'validators/check_vault_paths.py'),
                           '.' if relative else str(root)], cwd=root,
                          capture_output=True, text=True, check=False)


@pytest.mark.parametrize('relative', [False, True])
def test_canonical_pull_path_requires_exact_tracked_k8s_source(tmp_path, relative):
    put(tmp_path); prepare_git_context(tmp_path)
    result = vault_check(tmp_path, relative)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: 1 vault path(s)' in result.stdout
    assert 'reviewed-streampark-stage-pull exact files=1' in result.stdout


@pytest.mark.parametrize('origin,tracked', [
    ('https://gitlab.addx.ai/DATA/streampark.git', True),
    ('https://gitlab.addx.ai/DEV/k8s.git', False),
])
def test_canonical_pull_path_cannot_borrow_origin_or_untracked_file(tmp_path, origin, tracked):
    put(tmp_path); prepare_git_context(tmp_path, origin, tracked)
    result = vault_check(tmp_path)
    assert result.returncode == 1 and 'no rule' in result.stdout


def test_exact_path_rule_does_not_leak_to_neighbouring_yaml(tmp_path):
    put(tmp_path); prepare_git_context(tmp_path)
    put(tmp_path, relative=RELATIVE.parent/'z-other-reader.yaml')
    result = vault_check(tmp_path)
    assert result.returncode == 1
    assert 'z-other-reader.yaml' in result.stdout and 'no rule' in result.stdout


@pytest.mark.parametrize('change', ['role', 'path', 'extra-object', 'approved-flag'])
def test_canonical_pull_source_requires_complete_security_contract(tmp_path, change):
    docs = list(yaml.safe_load_all(APPROVED))
    if change == 'role':
        docs[3]['spec']['provider']['vault']['auth']['jwt']['role'] = 'other-role'
    elif change == 'path':
        docs[4]['spec']['data'][0]['remoteRef']['key'] += '-other'
    elif change == 'extra-object':
        docs.append(copy.deepcopy(docs[0]))
    else:
        docs[3]['metadata']['annotations'] = {'approved': 'true'}
    put(tmp_path, yaml.safe_dump_all(docs).encode()); prepare_git_context(tmp_path)
    result = vault_check(tmp_path)
    assert result.returncode == 1 and 'no rule' in result.stdout


def test_canonical_pull_file_symlink_is_not_authorized(tmp_path):
    put(tmp_path); prepare_git_context(tmp_path)
    target = tmp_path/RELATIVE
    real = tmp_path/'source.txt'; real.write_bytes(APPROVED)
    target.unlink(); target.symlink_to(real)
    result = vault_check(tmp_path)
    assert result.returncode == 1 and 'no rule' in result.stdout
