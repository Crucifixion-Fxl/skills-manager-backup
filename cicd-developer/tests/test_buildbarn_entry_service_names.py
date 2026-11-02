"""The DEV/buildbarn SG entry Service name exception is three exact files, never a waiver."""
import hashlib
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL / 'validators/check_workload_naming.py'
FIXTURES = SKILL / 'references/approved-exceptions'
RENDERED = Path('k8s/sg-devops/rendered')
ORIGIN = 'git@gitlab.addx.ai:DEV/buildbarn.git'
SERVICES = {
    'bb-cache': ('bb-frontend', 'c32aeee80de1a546bed098a189c80aeb75da52c1d804cf534ca9c2c47b708b70'),
    'bb-cache-metrics': ('bb-frontend', 'bdbabd82c156361c29e9d9fef79f642943eaabecd6d48cddb1e24590997b7e7d'),
    'bb-exec': ('scheduler', '8429c205deb21c0955f9a350a421f446b1e41b3ea3a85e2d1db68732c11c3057'),
}
WORKLOADS = [
    {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Rollout', 'metadata': {'name': app},
     'spec': {'template': {'metadata': {'labels': {'app': app}}, 'spec': {}}}}
    for app in ('bb-frontend', 'scheduler')
]

sys.path.insert(0, str(SKILL / 'validators'))
import check_workload_naming as naming  # noqa: E402


def fixture(name):
    return (FIXTURES / f'sg-buildbarn-service-{name}.yaml').read_bytes()


def check(root):
    return subprocess.run([sys.executable, str(VALIDATOR), str(root)],
                          capture_output=True, text=True, check=False)


def git(root, *args):
    subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)


def repository(tmp_path, origin=ORIGIN, files=None, track=True):
    root = tmp_path / 'buildbarn'
    rendered = root / RENDERED
    rendered.mkdir(parents=True)
    git(root, 'init', '-q')
    git(root, 'remote', 'add', 'origin', origin)
    (rendered / 'workloads.yaml').write_text(yaml.safe_dump_all(WORKLOADS))
    for name, data in ({n: fixture(n) for n in SERVICES} if files is None else files).items():
        (rendered / f'service-{name}.yaml').write_bytes(data)
    if track:
        git(root, 'add', '-A')
    return root, rendered


def test_regression_is_in_actual_ci_entrypoint():
    repo = next(parent for parent in SKILL.parents
                if (parent / '.gitlab/ci/cicd-developer-offline-e2e.yml').is_file())
    job = yaml.safe_load((repo / '.gitlab/ci/cicd-developer-offline-e2e.yml').read_text())[
        'cicd-developer:offline-e2e']
    commands = [line for line in job['script'] if isinstance(line, str) and ' pytest ' in line]
    assert len(commands) == 1
    assert 'skills/delivery/cicd-developer/tests/test_buildbarn_entry_service_names.py' in commands[0].split()


def test_fixtures_are_the_closed_digests_and_shapes():
    assert set(naming.BUILDBARN_ENTRY_SERVICES) == set(SERVICES)
    for name, (app, digest) in SERVICES.items():
        data = fixture(name)
        assert hashlib.sha256(data).hexdigest() == digest
        assert naming.BUILDBARN_ENTRY_SERVICES[name][0] == app
        assert naming.BUILDBARN_ENTRY_SERVICES[name][2] == digest
        (doc,) = yaml.safe_load_all(data)
        assert (doc['kind'], doc['metadata']['name'], doc['metadata']['namespace']) == (
            'Service', name, 'prod-buildbarn')
        assert doc['spec']['selector'] == {'app': app}
        assert doc['spec']['type'] == 'ClusterIP'
    assert naming.BUILDBARN_ENTRY_EXPIRES_ON == date(2026, 12, 31)


def test_exact_tracked_files_in_dev_buildbarn_pass(tmp_path):
    _, rendered = repository(tmp_path)
    result = check(rendered)
    assert result.returncode == 0, result.stdout + result.stderr


def test_https_origin_is_the_same_repository(tmp_path):
    _, rendered = repository(tmp_path, origin='https://gitlab.addx.ai/DEV/buildbarn.git')
    assert check(rendered).returncode == 0


@pytest.mark.parametrize('origin', ['git@gitlab.addx.ai:DEV/buildbarn-fork.git',
                                    'git@gitlab.addx.ai:SYS/buildbarn.git',
                                    'https://example.invalid/DEV/buildbarn.git'])
def test_other_repository_is_rejected(tmp_path, origin):
    _, rendered = repository(tmp_path, origin=origin)
    result = check(rendered)
    assert result.returncode == 1
    assert result.stdout.count('name must be') == 3


def test_untracked_copy_is_rejected(tmp_path):
    _, rendered = repository(tmp_path, track=False)
    assert check(rendered).returncode == 1


def test_copy_outside_git_is_rejected(tmp_path):
    for name in SERVICES:
        (tmp_path / f'service-{name}.yaml').write_bytes(fixture(name))
    (tmp_path / 'workloads.yaml').write_text(yaml.safe_dump_all(WORKLOADS))
    result = check(tmp_path)
    assert result.returncode == 1
    assert result.stdout.count('name must be') == 3


def test_other_path_in_dev_buildbarn_is_rejected(tmp_path):
    root, rendered = repository(tmp_path, files={})
    other = root / 'k8s/us-staging/rendered'
    other.mkdir(parents=True)
    (other / 'service-bb-exec.yaml').write_bytes(fixture('bb-exec'))
    (other / 'workloads.yaml').write_text(yaml.safe_dump_all(WORKLOADS))
    git(root, 'add', '-A')
    assert check(other).returncode == 1


def mutated(name, mutate):
    doc = next(yaml.safe_load_all(fixture(name)))
    mutate(doc)
    return yaml.safe_dump(doc).encode()


@pytest.mark.parametrize('data', [
    pytest.param(fixture('bb-exec') + b'\n', id='trailing-byte'),
    pytest.param(fixture('bb-exec').replace(b'  ', b'    '), id='reformatted'),
    pytest.param(mutated('bb-exec', lambda d: d['spec'].__setitem__('type', 'LoadBalancer')), id='type'),
    pytest.param(mutated('bb-exec', lambda d: d['spec']['ports'][0].__setitem__('port', 80)), id='port'),
    pytest.param(fixture('bb-exec') + b'\n---\n' + fixture('bb-cache'), id='extra-document'),
])
def test_any_byte_change_is_rejected(tmp_path, data):
    _, rendered = repository(tmp_path, files={'bb-exec': data})
    assert check(rendered).returncode == 1


def test_renamed_or_reselected_service_is_rejected(tmp_path):
    files = {'bb-exec': mutated('bb-exec', lambda d: d['spec'].__setitem__('selector', {'app': 'bb-frontend'})),
             'bb-cache': mutated('bb-cache', lambda d: d['metadata'].__setitem__('name', 'bb-cache2'))}
    _, rendered = repository(tmp_path, files=files)
    assert check(rendered).returncode == 1


def test_symlink_is_rejected(tmp_path):
    root, rendered = repository(tmp_path, files={})
    (rendered / 'service-bb-exec.yaml').unlink(missing_ok=True)
    real = tmp_path / 'real.yaml'
    real.write_bytes(fixture('bb-exec'))
    (rendered / 'service-bb-exec.yaml').symlink_to(real)
    git(root, 'add', '-A')
    assert check(rendered).returncode == 1


def test_selector_match_rule_still_applies(tmp_path):
    root, rendered = repository(tmp_path)
    (rendered / 'workloads.yaml').write_text(yaml.safe_dump_all(WORKLOADS[:1]))
    git(root, 'add', '-A')
    result = check(rendered)
    assert result.returncode == 1
    assert 'Service/bb-exec: spec.selector' in result.stdout


def test_other_misnamed_service_still_fails(tmp_path):
    root, rendered = repository(tmp_path)
    (rendered / 'service-extra.yaml').write_text(yaml.safe_dump(
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'extra'},
         'spec': {'selector': {'app': 'scheduler'}}}))
    git(root, 'add', '-A')
    result = check(rendered)
    assert result.returncode == 1
    assert result.stdout.count('name must be') == 1 and 'Service/extra' in result.stdout


def test_exception_expires_after_its_last_utc_day(tmp_path):
    _, rendered = repository(tmp_path)
    path = rendered / 'service-bb-exec.yaml'
    selector = {'app': 'scheduler'}
    assert naming.is_approved_entry_service(path, 'bb-exec', selector, date(2026, 12, 31))
    assert not naming.is_approved_entry_service(path, 'bb-exec', selector, date(2027, 1, 1))
    assert naming.service_name_failure(path, 'bb-exec', selector, date(2027, 1, 1))
