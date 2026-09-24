#!/usr/bin/env python3
"""Check app-owned alert VMRules against observed application context.

Static shape comparison only. Does not authenticate the context, prove Argo/VM
readiness, authorize notification routing, or evaluate expressions. The workflow
verifies current Argo source/destination and platform routing separately.
"""
import argparse
import json
from pathlib import Path
import re
import sys

import yaml

API = 'operator.victoriametrics.com/v1beta1'
ALERT_SET_LABEL = 'monitoring.addx.io/alert-set'
DURATION = re.compile(r'(?:[0-9]+(?:ms|s|m|h|d|w|y))+')


def validate(documents, context):
    required = {'cluster', 'id', 'repository', 'revision', 'path', 'namespace',
                'environment', 'application', 'project', 'notification_service'}
    if (not isinstance(context, dict) or set(context) != required
            or any(not isinstance(v, str) or not v for v in context.values())):
        raise ValueError('complete observed application context fields are required')
    for key in ('id', 'namespace', 'environment', 'cluster', 'application', 'project', 'notification_service'):
        value = context[key]
        limit = 40 if key == 'id' else 63
        if len(value) > limit or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', value):
            raise ValueError(f'invalid context {key}')
    if not re.fullmatch(r'https://gitlab\.addx\.ai/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.git', context['repository']):
        raise ValueError('canonical credential-free GitLab repository required')
    if any(part in {'.', '..'} for part in context['repository'].split('/')[3:]):
        raise ValueError('invalid repository path')
    if context['revision'] == 'HEAD' or any(c.isspace() or c in '*?[]' for c in context['revision']):
        raise ValueError('explicit observed revision required')
    if context['path'].startswith('/') or any(p in {'', '.', '..'} for p in context['path'].split('/')):
        raise ValueError('exact repository-relative overlay directory required')
    if not documents:
        raise ValueError('empty rules input')
    names, alerts, groups = set(), set(), set()
    count = 0
    for doc in documents:
        if not isinstance(doc, dict) or doc.get('apiVersion') != API or doc.get('kind') != 'VMRule':
            raise ValueError('only alerting VMRule documents are allowed')
        if set(doc) - {'apiVersion', 'kind', 'metadata', 'spec'}:
            raise ValueError('unexpected VMRule fields')
        meta = doc.get('metadata', {})
        name = meta.get('name', '')
        if not isinstance(name, str) or len(name) > 63 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', name):
            raise ValueError('VMRule resource name must be a DNS label of at most 63 characters')
        if not re.fullmatch(re.escape('alerts-' + context['id']) + r'(?:-[a-z0-9-]+)?', name):
            raise ValueError('resource name must use the context alerts-<id> prefix')
        if name in names:
            raise ValueError('duplicate VMRule resource name')
        names.add(name)
        if meta.get('namespace') != context['namespace']:
            raise ValueError('VMRule must live in the application namespace')
        resource_labels = meta.get('labels', {})
        if (not isinstance(resource_labels, dict)
                or resource_labels.get(ALERT_SET_LABEL) != context['id']
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in resource_labels.items())
                or 'monitoring.addx.io/evaluator-profile' in resource_labels):
            raise ValueError('alert-set label required; evaluator profiles are platform-owned')
        if set(meta) - {'name', 'namespace', 'labels'}:
            raise ValueError('unexpected metadata (hooks/owner references/annotations are not allowed)')
        spec = doc.get('spec', {})
        if set(spec) != {'groups'} or not isinstance(spec['groups'], list) or not spec['groups']:
            raise ValueError('non-empty spec.groups is required')
        for group in spec['groups']:
            if set(group) != {'name', 'interval', 'rules'}:
                raise ValueError('group may contain only name, interval, rules')
            group_name = group['name']
            if not isinstance(group_name, str) or not group_name.startswith(context['id'] + '-') or group_name in groups:
                raise ValueError('group must have a unique alert-set-prefixed name')
            groups.add(group_name)
            if group['interval'] != '1m':
                raise ValueError('self-service alert evaluation interval must be 1m')
            if not isinstance(group['rules'], list) or not group['rules']:
                raise ValueError('empty rules group')
            for rule in group['rules']:
                if set(rule) != {'alert', 'expr', 'for', 'labels', 'annotations'}:
                    raise ValueError('alert rule requires alert, expr, for, labels, annotations; recording rules not allowed')
                alert = rule['alert']
                if not isinstance(alert, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', alert) or alert in alerts:
                    raise ValueError('invalid or duplicate alert name')
                alerts.add(alert)
                if not isinstance(rule['expr'], str) or not rule['expr'].strip():
                    raise ValueError('expr must be a non-empty string')
                if not isinstance(rule['for'], str) or not DURATION.fullmatch(rule['for']):
                    raise ValueError('for must be an explicit duration string')
                labels = rule['labels']
                expected = {'notification_service': context['notification_service'], 'namespace': context['namespace'],
                            'environment': context['environment']}
                if not isinstance(labels, dict) or any(labels.get(k) != v for k, v in expected.items()):
                    raise ValueError('routing identity does not match application context')
                if labels.get('severity') not in {'critical', 'warning'}:
                    raise ValueError('severity must be critical or warning')
                for key, value in labels.items():
                    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', key) or key.startswith('__') or key in {'alertname', 'pod'} or not isinstance(value, str) or '{{' in value:
                        raise ValueError('labels must have valid names and static string values')
                annotations = rule['annotations']
                if not isinstance(annotations, dict) or any(not isinstance(annotations.get(k), str) or not annotations[k].strip()
                                                          for k in ('summary', 'description')):
                    raise ValueError('summary and description are required')
                if set(annotations) - {'summary', 'description', 'dashboard_url', 'runbook_url'}:
                    raise ValueError('unsupported annotation')
                if labels.get('scope') == 'pod' and '$labels.pod' not in annotations['summary']:
                    raise ValueError('pod alert summary must identify the pod')
                count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True, type=Path)
    parser.add_argument('rules', type=Path, nargs='+')
    args = parser.parse_args()
    try:
        docs = []
        for path in args.rules:
            docs.extend(d for d in yaml.safe_load_all(path.read_text()) if d is not None)
        count = validate(docs, json.loads(args.context.read_text()))
    except (ValueError, TypeError, KeyError, AttributeError, OSError, yaml.YAMLError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        return 1
    print(f'PASS: {count} alert rules match supplied application context; routing authorization and runtime activation are NOT verified')
    return 0


if __name__ == '__main__':
    sys.exit(main())
