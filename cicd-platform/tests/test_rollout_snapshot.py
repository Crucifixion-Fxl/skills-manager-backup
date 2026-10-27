import copy
import importlib.util
import unittest
import json
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('snapshot', Path(__file__).parents[1] / 'scripts/rollout_snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.template = {'metadata': {'annotations': {'kubectl.kubernetes.io/restartedAt': 'now'}},
                         'spec': {'containers': [{'image': 'image:tag'}]}}
        self.deploy = {'metadata': {'uid': 'd', 'resourceVersion': '1', 'name': 'app', 'generation': 2},
                       'spec': {'replicas': 1, 'template': self.template},
                       'status': {'observedGeneration': 2, 'replicas': 1, 'updatedReplicas': 1,
                                  'readyReplicas': 1, 'availableReplicas': 1}}
        self.rs = {'metadata': {'uid': 'r', 'resourceVersion': '1', 'name': 'new', 'ownerReferences':
                               [{'kind': 'Deployment', 'uid': 'd', 'controller': True}]},
                   'spec': {'replicas': 1, 'template': self.template},
                   'status': {'replicas': 1, 'readyReplicas': 1}}
        self.pod = {'metadata': {'uid': 'p', 'resourceVersion': '1', 'name': 'p', 'ownerReferences':
                                [{'kind': 'ReplicaSet', 'uid': 'r', 'controller': True}]},
                    'spec': {'containers': [{'image': 'image:tag', 'env': [{'value': 'secret'}]}]},
                    'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}

    def run_snapshot(self, rs=None, pods=None):
        return snapshot.summarize(self.deploy, rs or [self.rs], pods or [self.pod], 'image:tag', 'now')

    def test_hpa_current_desired_not_original_250(self):
        result = self.run_snapshot()
        self.assertEqual(result['state'], 'complete')
        self.assertNotIn('secret', str(result))

    def test_old_same_image_terminating_pod_blocks_completion(self):
        old = copy.deepcopy(self.rs)
        old['metadata'].update(uid='old', name='old')
        old['spec'] = {'replicas': 0, 'template': copy.deepcopy(self.template)}
        old['spec']['template']['metadata']['annotations']['kubectl.kubernetes.io/restartedAt'] = 'before'
        old['status'] = {'replicas': 0}
        p = copy.deepcopy(self.pod)
        p['metadata'].update(deletionTimestamp='now', ownerReferences=[{'kind': 'ReplicaSet', 'uid': 'old', 'controller': True}])
        self.assertEqual(self.run_snapshot([self.rs, old], [self.pod, p])['state'], 'in_progress')

    def test_unrelated_same_label_pod_ignored(self):
        p = copy.deepcopy(self.pod)
        p['metadata']['ownerReferences'][0]['uid'] = 'other'
        p['spec']['containers'][0]['image'] = 'other:tag'
        self.assertEqual(self.run_snapshot(pods=[self.pod, p])['state'], 'complete')

    def test_restart_changed_is_not_success(self):
        self.template['metadata']['annotations']['kubectl.kubernetes.io/restartedAt'] = 'different'
        self.assertEqual(self.run_snapshot()['state'], 'target_changed')

    def test_gate_not_ready_blocks_success(self):
        self.pod['status']['conditions'] = [{'type': 'Ready', 'status': 'False', 'reason': 'ReadinessGatesNotReady'}]
        self.assertEqual(self.run_snapshot()['state'], 'in_progress')

    def test_stale_observed_generation_blocks_success(self):
        self.deploy['status']['observedGeneration'] = 1
        self.assertEqual(self.run_snapshot()['state'], 'in_progress')

    def test_main_rejects_identity_or_generation_change(self):
        from types import SimpleNamespace
        for change in ({'uid': 'replacement'}, {'generation': 3, 'resourceVersion': '2'}):
            fresh = copy.deepcopy(self.deploy)
            fresh['metadata'].update(change)
            reads = [self.deploy, {'items': [self.rs]}, {'items': [self.pod]}, fresh, {'items': [self.rs]}, {'items': [self.pod]}, fresh]
            responses = [SimpleNamespace(returncode=0, stdout=json.dumps(x).encode()) for x in reads]
            args = ['snapshot', '--context', 'ctx', '--namespace', 'ns', '--deployment', 'app',
                    '--expected-image', 'image:tag', '--restart-at', 'now']
            with patch('sys.argv', args), patch.object(snapshot.subprocess, 'run', side_effect=responses), patch('builtins.print') as output:
                self.assertEqual(snapshot.main(), 0)
                self.assertEqual(json.loads(output.call_args.args[0])['state'], 'retry_snapshot')

    def test_main_rejects_pod_or_rs_status_changes(self):
        from types import SimpleNamespace
        for resource in ('pod', 'rs'):
            fresh_pod, fresh_rs = copy.deepcopy(self.pod), copy.deepcopy(self.rs)
            changed = fresh_pod if resource == 'pod' else fresh_rs
            changed['metadata']['resourceVersion'] = '2'
            if resource == 'pod':
                fresh_pod['status']['conditions'][0]['status'] = 'False'
            reads = [self.deploy, {'items': [self.rs]}, {'items': [self.pod]}, self.deploy,
                     {'items': [fresh_rs]}, {'items': [fresh_pod]}, self.deploy]
            responses = [SimpleNamespace(returncode=0, stdout=json.dumps(x).encode()) for x in reads]
            args = ['snapshot', '--context', 'ctx', '--namespace', 'ns', '--deployment', 'app',
                    '--expected-image', 'image:tag', '--restart-at', 'now']
            with patch('sys.argv', args), patch.object(snapshot.subprocess, 'run', side_effect=responses), patch('builtins.print') as output:
                self.assertEqual(snapshot.main(), 0)
                self.assertEqual(json.loads(output.call_args.args[0])['state'], 'retry_snapshot')

    def test_related_fingerprint_detects_old_pod_creation(self):
        first = snapshot.resource_fingerprint(self.deploy, [self.rs], [self.pod])
        added = copy.deepcopy(self.pod)
        added['metadata']['uid'] = 'added'
        self.assertNotEqual(first, snapshot.resource_fingerprint(self.deploy, [self.rs], [self.pod, added]))


if __name__ == '__main__':
    unittest.main()
