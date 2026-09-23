#!/usr/bin/env python3
"""Verify that an exact rendered app container consumes its expected Secret keys."""

import argparse
import os
from pathlib import Path
import stat
import sys

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise ValueError('duplicate YAML key')
                seen.add(key)
            except TypeError as exc:
                raise ValueError('invalid YAML key') from exc
        return super().construct_mapping(node, deep=deep)


def check(documents, namespace, kind, workload, container, secret, keys):
    matches = [d for d in documents if isinstance(d, dict)
               and d.get('kind') == kind
               and isinstance(d.get('metadata'), dict)
               and d['metadata'].get('namespace') == namespace
               and d['metadata'].get('name') == workload]
    if len(matches) != 1:
        return ['expected exactly one matching rendered workload']
    expected_api = 'argoproj.io/v1alpha1' if kind == 'Rollout' else 'apps/v1'
    if matches[0].get('apiVersion') != expected_api:
        return ['unsupported workload apiVersion']
    try:
        containers = matches[0]['spec']['template']['spec']['containers']
        selected = [c for c in containers if isinstance(c, dict) and c.get('name') == container]
        if len(selected) != 1:
            return ['expected exactly one matching application container']
        selected = selected[0]
        env, sources = selected.get('env', []), selected.get('envFrom', [])
        if not isinstance(env, list) or not isinstance(sources, list):
            return ['env and envFrom must be lists']
        if any(not isinstance(e, dict) for e in env + sources):
            return ['malformed environment entry']
        imported = [i for i, s in enumerate(sources)
                    if s.get('prefix', '') == '' and isinstance(s.get('secretRef'), dict)
                    and s['secretRef'].get('name') == secret
                    and s['secretRef'].get('optional', False) is False]
        failures = []
        # An app waiting for a required Secret blocks its own Argo sync wave.
        # A consumer ExternalSecret in a later wave can never be applied.
        def wave(obj):
            annotations = obj['metadata'].get('annotations') or {}
            return int(annotations.get('argocd.argoproj.io/sync-wave', '0'))
        for doc in documents:
            if not isinstance(doc, dict) or doc.get('kind') != 'ExternalSecret':
                continue
            meta = doc.get('metadata') or {}
            target = (doc.get('spec') or {}).get('target') or {}
            target_name = target.get('name')
            if target_name is None:
                target_name = meta.get('name')
            if meta.get('namespace') == namespace and (not isinstance(target_name, str) or not target_name):
                failures.append('invalid ExternalSecret target name')
            if meta.get('namespace') == namespace and target_name == secret:
                if wave(doc) > wave(matches[0]):
                    failures.append('workload sync-wave precedes its required ExternalSecret; move the workload after the consumer')
        for binding in keys:
            key, separator, secret_key = binding.partition('=')
            if not separator:
                secret_key = key
            if not key or not secret_key:
                failures.append('required keys must be ENV or ENV=SECRET_KEY')
                continue
            explicit = [e for e in env if e.get('name') == key]
            if explicit:
                if len(explicit) != 1:
                    failures.append(f'{key}: duplicate explicit environment entries')
                    continue
                value_from = explicit[0].get('valueFrom')
                ref = value_from.get('secretKeyRef') if isinstance(value_from, dict) else None
                if ('value' in explicit[0] or not isinstance(ref, dict)
                        or set(value_from) != {'secretKeyRef'}
                        or ref.get('name') != secret or ref.get('key') != secret_key
                        or ref.get('optional', False) is not False):
                    failures.append(f'{key}: explicit env overrides the expected Secret key')
            elif key != secret_key or not imported or imported[-1] != len(sources) - 1:
                failures.append(f'{key}: no unprefixed, required Secret import with provable precedence; use explicit secretKeyRef')
        return failures
    except (KeyError, TypeError, AttributeError, ValueError):
        return ['malformed rendered workload container structure']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('render', type=Path)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--kind', choices=['Rollout', 'Deployment', 'StatefulSet'], required=True)
    parser.add_argument('--workload', required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--secret', required=True)
    parser.add_argument('--key', action='append', required=True, help='ENV or ENV=SECRET_KEY (repeatable)')
    args = parser.parse_args()
    try:
        with os.fdopen(os.open(args.render, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('render must be a regular file')
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('render too large')
        documents = list(yaml.load_all(data.decode('utf-8'), Loader=UniqueKeyLoader))
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as exc:
        print(f'FAIL: cannot read rendered manifests: {type(exc).__name__}')
        return 2
    failures = check(documents, args.namespace, args.kind, args.workload, args.container, args.secret, args.key)
    for failure in failures:
        print(f'FAIL: {args.kind}/{args.workload} container {args.container}: {failure}')
    if failures:
        return 1
    print('PASS: exact application container consumes all required Secret keys')
    return 0


if __name__ == '__main__':
    sys.exit(main())
