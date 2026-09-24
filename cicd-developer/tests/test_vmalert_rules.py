import copy
import json
import re

import yaml
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).parents[1] / 'validators/check_vmalert_rules.py'
spec = importlib.util.spec_from_file_location('vmalert_rules', MODULE)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(id='sample-prod', namespace='prod-sample',
                                 environment='prod-us', cluster='us-prod',
                                 repository='https://gitlab.addx.ai/app/sample.git',
                                 revision='main', path='k8s/overlays/prod-us',
                                 application='sample-prod-us', project='app-runtime',
                                 notification_service='sample')
        self.rule = {'alert': 'SampleHigh', 'expr': 'sample_metric > 10', 'for': '5m',
                     'labels': {'notification_service': 'sample', 'namespace': 'prod-sample',
                                'environment': 'prod-us', 'severity': 'critical'},
                     'annotations': {'summary': 'High value', 'description': 'Check sample'}}
        self.doc = {'apiVersion': checker.API, 'kind': 'VMRule',
                    'metadata': {'name': 'alerts-sample-prod', 'namespace': 'prod-sample',
                                 'labels': {checker.ALERT_SET_LABEL: 'sample-prod'}},
                    'spec': {'groups': [{'name': 'sample-prod-main', 'interval': '1m', 'rules': [self.rule]}]}}

    def test_valid_rules_use_existing_application_namespace(self):
        self.assertEqual(checker.validate([self.doc], self.context), 1)

    def test_identity_spoofing_fails(self):
        for key in ['namespace', 'environment', 'notification_service']:
            with self.subTest(key=key):
                doc = copy.deepcopy(self.doc)
                doc['spec']['groups'][0]['rules'][0]['labels'][key] = 'other'
                with self.assertRaises(ValueError):
                    checker.validate([doc], self.context)

    def test_reject_wrong_resource_namespace_and_evaluator_override(self):
        self.doc['metadata']['namespace'] = 'victoria-metrics'
        with self.assertRaises(ValueError):
            checker.validate([self.doc], self.context)
        self.doc['metadata']['namespace'] = 'prod-sample'
        self.doc['metadata']['labels']['monitoring.addx.io/evaluator-profile'] = 'legacy-final-labels'
        with self.assertRaises(ValueError):
            checker.validate([self.doc], self.context)

    def test_reject_recording_rule_remote_settings_and_empty_input(self):
        with self.assertRaises(ValueError):
            checker.validate([], self.context)
        self.rule['record'] = 'sample'
        with self.assertRaises(ValueError):
            checker.validate([self.doc], self.context)
        del self.rule['record']
        self.doc['spec']['groups'][0]['headers'] = ['Authorization: fake']
        with self.assertRaises(ValueError):
            checker.validate([self.doc], self.context)

    def test_pod_summary_must_retain_identity(self):
        self.rule['labels']['scope'] = 'pod'
        with self.assertRaises(ValueError):
            checker.validate([self.doc], self.context)
        self.rule['annotations']['summary'] = '{{ $labels.pod }} is high'
        self.assertEqual(checker.validate([self.doc], self.context), 1)

    def test_reject_duplicate_and_non_rule_documents(self):
        with self.assertRaises(ValueError):
            checker.validate([self.doc, self.doc], self.context)
        with self.assertRaises(ValueError):
            checker.validate([{'kind': 'Secret'}], self.context)

    def test_incomplete_context_is_not_accepted(self):
        for key in self.context:
            context = dict(self.context)
            del context[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                checker.validate([self.doc], context)

    def test_invalid_context_and_resource_names_fail(self):
        for key, value in [('id', 'Bad_ID'), ('namespace', 'bad-'),
                           ('namespace', 'a' * 64)]:
            context = dict(self.context, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                checker.validate([self.doc], context)
        for name in ['alerts-sample-prod-', 'alerts-sample-prod-' + 'a' * 64]:
            doc = copy.deepcopy(self.doc)
            doc['metadata']['name'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                checker.validate([doc], self.context)

    def test_recipe_preserves_quotes_newlines_and_template_annotations(self):
        template = (MODULE.parents[1] / 'recipes/victoriametrics/vm-alert-rule.yaml.tmpl').read_text()
        summary = "Pod's {{ $labels.pod }}: \"high\""
        description = "First line\nSecond line: 'value'"
        slots = dict(alert_set_id='sample-prod', notification_service='sample',
                     business_namespace='prod-sample', environment='prod-us',
                     alert_name='SampleHigh', pending_duration='5m', severity='critical',
                     expression_json=json.dumps(self.rule['expr']),
                     summary_json=json.dumps(summary), description_json=json.dumps(description))
        rendered = re.sub(r'\{\{\s*([A-Za-z0-9_]+)\s*\}\}', lambda m: slots[m[1]], template)
        doc = yaml.safe_load(rendered)
        self.assertEqual(checker.validate([doc], self.context), 1)
        rule = doc['spec']['groups'][0]['rules'][0]
        self.assertEqual(rule['annotations'], dict(summary=summary, description=description))
        self.assertEqual(rule['expr'], self.rule['expr'])

    def test_preserve_existing_kustomize_resource_labels(self):
        self.doc['metadata']['labels'].update({'env': 'prod-us', 'deployment.addx.io/stack': 'b'})
        self.assertEqual(checker.validate([self.doc], self.context), 1)

    def test_service_routing_identity_is_separate_from_alert_set(self):
        self.rule['labels']['notification_service'] = self.context['id']
        with self.assertRaises(ValueError):
            checker.validate([self.doc], self.context)

    def test_reject_hooks(self):
        self.doc['metadata']['annotations'] = {'argocd.argoproj.io/hook': 'PreSync'}
        with self.assertRaises(ValueError):
            checker.validate([self.doc], self.context)


if __name__ == '__main__':
    unittest.main()
