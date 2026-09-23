"""Render-level regression cases for shared-only Redis consumption."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

CHECKER = Path(__file__).parents[1] / 'validators/check_workload_secret.py'
SPEC = importlib.util.spec_from_file_location('workload_secret', CHECKER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def workload():
    return {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Rollout',
            'metadata': {'name': 'order-api', 'namespace': 'staging-order-api'},
            'spec': {'template': {'spec': {'containers': [
                {'name': 'app', 'image': 'example.invalid/order-api:abc1234'},
                {'name': 'sidecar', 'image': 'example.invalid/sidecar:abc1234'}]}}}}


def check(doc):
    return MODULE.check([doc], 'staging-order-api', 'Rollout', 'order-api', 'app',
                        'order-api-redis-secret', ['REDIS_HOST', 'REDIS_PORT'])


def ref():
    return {'secretRef': {'name': 'order-api-redis-secret'}}


def test_secret_exists_but_app_does_not_consume_it(tmp_path):
    render = tmp_path / 'render.yaml'
    docs = [workload(), {'apiVersion': 'external-secrets.io/v1', 'kind': 'ExternalSecret',
                        'metadata': {'name': 'order-api-redis-secret', 'namespace': 'staging-order-api'}}]
    render.write_text(yaml.safe_dump_all(docs))
    result = subprocess.run([sys.executable, str(CHECKER), str(render), '--namespace',
                             'staging-order-api', '--kind', 'Rollout', '--workload', 'order-api',
                             '--container', 'app', '--secret', 'order-api-redis-secret',
                             '--key', 'REDIS_HOST', '--key', 'REDIS_PORT'], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'REDIS_HOST' in result.stdout and 'REDIS_PORT' in result.stdout


@pytest.mark.parametrize('case', ['sidecar', 'prefix', 'optional', 'explicit_override', 'later_source'])
def test_import_must_reach_exact_container_without_overrides(case):
    doc = workload()
    containers = doc['spec']['template']['spec']['containers']
    app = containers[0]
    app['envFrom'] = [ref()]
    if case == 'sidecar':
        containers[1]['envFrom'] = app.pop('envFrom')
    elif case == 'prefix':
        app['envFrom'][0]['prefix'] = 'OTHER_'
    elif case == 'optional':
        app['envFrom'][0]['secretRef']['optional'] = True
    elif case == 'explicit_override':
        app['env'] = [{'name': 'REDIS_HOST', 'value': 'old-endpoint'}]
    else:
        app['envFrom'].append({'configMapRef': {'name': 'later-config'}})
    assert check(doc)


def test_app_import_preserves_other_config_and_is_accepted():
    doc = workload()
    doc['spec']['template']['spec']['containers'][0].update(
        envFrom=[{'configMapRef': {'name': 'app-config'}}, ref()],
        env=[{'name': 'LOG_LEVEL', 'value': 'info'}])
    assert check(doc) == []


def test_explicit_secret_keys_have_precedence_over_envfrom():
    doc = workload()
    doc['spec']['template']['spec']['containers'][0].update(
        envFrom=[{'configMapRef': {'name': 'app-config'}}],
        env=[{'name': k, 'valueFrom': {'secretKeyRef': {
            'name': 'order-api-redis-secret', 'key': k}}} for k in ['REDIS_HOST', 'REDIS_PORT']])
    assert check(doc) == []
    altered = copy.deepcopy(doc)
    altered['spec']['template']['spec']['containers'][0]['env'][1]['valueFrom']['secretKeyRef']['key'] = 'wrong'
    assert check(altered)


def test_missing_or_ambiguous_target_is_rejected():
    doc = workload()
    assert MODULE.check([doc, doc], 'staging-order-api', 'Rollout', 'order-api', 'app', 's', ['REDIS_HOST'])
    doc['metadata']['namespace'] = 'other'
    assert check(doc)


def test_separate_connection_can_use_explicit_environment_mapping():
    doc = workload()
    app = doc['spec']['template']['spec']['containers'][0]
    app['env'] = [{'name': 'AUDIT_DB_HOST', 'valueFrom': {'secretKeyRef': {
        'name': 'audit-db-secret', 'key': 'DB_HOST'}}}]
    assert MODULE.check([doc], 'staging-order-api', 'Rollout', 'order-api', 'app',
                        'audit-db-secret', ['AUDIT_DB_HOST=DB_HOST']) == []
    app.pop('env')
    app['envFrom'] = [{'secretRef': {'name': 'audit-db-secret'}}]
    assert MODULE.check([doc], 'staging-order-api', 'Rollout', 'order-api', 'app',
                        'audit-db-secret', ['AUDIT_DB_HOST=DB_HOST'])


@pytest.mark.parametrize('target', [{}, {'name': None}, {'name': 'order-api-redis-secret'}])
def test_first_sync_cannot_wait_for_secret_in_a_later_wave(target):
    doc = workload()
    doc['spec']['template']['spec']['containers'][0]['envFrom'] = [ref()]
    consumer = {'apiVersion': 'external-secrets.io/v1', 'kind': 'ExternalSecret',
                'metadata': {'name': 'order-api-redis-secret', 'namespace': 'staging-order-api',
                             'annotations': {'argocd.argoproj.io/sync-wave': '1'}},
                'spec': {'target': target}}
    args = ('staging-order-api', 'Rollout', 'order-api', 'app',
            'order-api-redis-secret', ['REDIS_HOST', 'REDIS_PORT'])
    assert any('sync-wave' in f for f in MODULE.check([doc, consumer], *args))
    doc['metadata']['annotations'] = {'argocd.argoproj.io/sync-wave': '2'}
    assert MODULE.check([doc, consumer], *args) == []
