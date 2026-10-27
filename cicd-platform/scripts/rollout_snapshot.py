#!/usr/bin/env python3
"""Read-only rollout evidence; emits no container env, logs or credentials."""
import argparse
import collections
import json
import subprocess
import sys


def owned_by(obj, kind, uid):
    return any(o.get('kind') == kind and o.get('uid') == uid
               and o.get('controller') is True
               for o in obj['metadata'].get('ownerReferences', []))


def summarize(deploy, replica_sets, pods, expected_image, restart_at):
    uid = deploy['metadata']['uid']
    replica_sets = [r for r in replica_sets if owned_by(r, 'Deployment', uid)]
    # Hash selects the current RS; owner UID prevents unrelated same-label pods.
    template = deploy['spec']['template']
    relevant = {r['metadata']['uid']: r for r in replica_sets}
    pods = [p for p in pods if any(owned_by(p, 'ReplicaSet', u) for u in relevant)]
    current = [r for r in replica_sets
               if r['spec']['template']['metadata'].get('annotations', {}).get(
                   'kubectl.kubernetes.io/restartedAt') == restart_at]
    live_restart = template['metadata'].get('annotations', {}).get(
        'kubectl.kubernetes.io/restartedAt')
    desired = deploy['spec'].get('replicas', 1)
    status = deploy.get('status', {})
    images = [c['image'] for c in template['spec']['containers']]
    image_counts = collections.Counter(c['image'] for p in pods for c in p['spec']['containers'])
    # Readiness describes full pod readiness, including readiness gates.
    ready = sum(any(c['type'] == 'Ready' and c['status'] == 'True'
                    for c in p.get('status', {}).get('conditions', [])) for p in pods)
    terminating = sum('deletionTimestamp' in p['metadata'] for p in pods)
    current_uid = current[0]['metadata']['uid'] if len(current) == 1 else None
    old_pods = [p for p in pods if not owned_by(p, 'ReplicaSet', current_uid)]
    reasons = collections.Counter(c.get('reason', 'Unknown') for p in pods
                                  for c in p.get('status', {}).get('conditions', [])
                                  if c['type'] == 'Ready' and c['status'] != 'True')
    complete = (live_restart == restart_at and len(current) == 1 and
                images == [expected_image] and set(image_counts) == {expected_image} and
                status.get('observedGeneration', 0) >= deploy['metadata']['generation'] and
                all(status.get(k, 0) == desired for k in
                    ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')) and
                len(pods) == desired and ready == desired and not terminating and not old_pods and
                all(r['spec'].get('replicas', 0) == 0 and r.get('status', {}).get('replicas', 0) == 0
                    for r in replica_sets if r['metadata']['uid'] != current_uid))
    mismatch = live_restart != restart_at or images != [expected_image] or any(
        i != expected_image for i in image_counts)
    return {
        'state': 'complete' if complete else ('target_changed' if mismatch else 'in_progress'),
        'deployment': deploy['metadata']['name'],
        'generation': deploy['metadata']['generation'],
        'observedGeneration': status.get('observedGeneration'),
        'restartAt': live_restart,
        'desired': desired,
        'replicas': {k: status.get(k, 0) for k in
                     ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')},
        'conditions': [{k: c.get(k) for k in ('type', 'status', 'reason', 'lastUpdateTime')}
                       for c in status.get('conditions', [])],
        'replicaSets': [{'name': r['metadata']['name'], 'uid': r['metadata']['uid'],
                         'currentRestart': r['metadata']['uid'] == current_uid,
                         'desired': r['spec'].get('replicas', 0),
                         'replicas': r.get('status', {}).get('replicas', 0),
                         'ready': r.get('status', {}).get('readyReplicas', 0)}
                        for r in replica_sets if r['spec'].get('replicas', 0)
                        or r.get('status', {}).get('replicas', 0)],
        'pods': {'total': len(pods), 'ready': ready, 'terminating': terminating,
                 'oldReplicaSetPods': len(old_pods), 'images': dict(image_counts),
                 'notReadyReasons': dict(reasons),
                 'restarts': sum(c.get('restartCount', 0) for p in pods
                                 for c in p.get('status', {}).get('containerStatuses', []))},
    }


def resource_fingerprint(deploy, replica_sets, pods):
    """Only deployment-owned resources matter; unrelated namespace churn is ignored."""
    uid = deploy['metadata']['uid']
    related_rs = [r for r in replica_sets if owned_by(r, 'Deployment', uid)]
    rs_uids = {r['metadata']['uid'] for r in related_rs}
    related_pods = [p for p in pods if any(owned_by(p, 'ReplicaSet', u) for u in rs_uids)]
    return (
        deploy['metadata']['uid'], deploy['metadata']['resourceVersion'],
        tuple(sorted((r['metadata']['uid'], r['metadata']['resourceVersion']) for r in related_rs)),
        tuple(sorted((p['metadata']['uid'], p['metadata']['resourceVersion']) for p in related_pods)),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('context', 'namespace', 'deployment', 'expected-image', 'restart-at'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    base = ['kubectl', '--context', args.context, '-n', args.namespace,
            '--request-timeout=20s', 'get']

    def get(*resource):
        result = subprocess.run(base + list(resource) + ['-o', 'json'],
                                capture_output=True, timeout=30, check=False)
        if result.returncode:
            # kube auth errors can contain provider details: never dump stderr.
            raise RuntimeError('kubectl read failed for ' + resource[0])
        return json.loads(result.stdout)

    try:
        d = get('deployment', args.deployment)
        rs = get('replicasets')['items']
        pods = get('pods')['items']
        # Re-read related object versions, not only spec generation: readiness
        # and deletion changes do not increment Deployment generation.
        fresh = get('deployment', args.deployment)
        fresh_rs = get('replicasets')['items']
        fresh_pods = get('pods')['items']
        final = get('deployment', args.deployment)
        if (resource_fingerprint(d, rs, pods) !=
                resource_fingerprint(fresh, fresh_rs, fresh_pods) or
                final['metadata']['uid'] != fresh['metadata']['uid'] or
                final['metadata']['resourceVersion'] != fresh['metadata']['resourceVersion']):
            print(json.dumps({'state': 'retry_snapshot', 'reason': 'related_resource_versions_changed'}))
            return 0
        print(json.dumps(summarize(d, rs, pods, args.expected_image, args.restart_at),
                         ensure_ascii=False))
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'state': 'read_failed', 'errorType': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
