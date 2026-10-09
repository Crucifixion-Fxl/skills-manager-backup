import importlib.util
from pathlib import Path
import unittest

p = Path(__file__).resolve().parents[1] / 'scripts/minutes_contract.py'
s = importlib.util.spec_from_file_location('minutes_contract', p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


class MinutesContractTest(unittest.TestCase):
    def test_canonical_minute_dedups_tracking_urls(self):
        a = m.parse_link('https://team.feishu.cn/minutes/abc123?from=chat#t=22', ['team.feishu.cn'], [])
        b = m.parse_link('https://team.feishu.cn/minutes/abc123', ['team.feishu.cn'], [])
        self.assertEqual(a, b)

    def test_issue_container_exact_scope(self):
        actual = m.parse_link('https://gitlab.example/group/sub/repo/-/issues/110', [], [('gitlab.example', 'group/sub/repo')])
        self.assertEqual((actual['kind'], actual['iid']), ('issue', 110))

    def test_reject_unsafe_and_ambiguous_urls(self):
        for url in ['http://team.feishu.cn/minutes/a', 'https://team.feishu.cn.evil/minutes/a',
                    'https://user@team.feishu.cn/minutes/a', 'https://team.feishu.cn:444/minutes/a',
                    'https://team.feishu.cn/docx/a', 'https://team.\nfeishu.cn/minutes/a', 'https://team.feishu.cn/minutes/a/extra',
                    'https://gitlab.example/group/other/-/issues/110', '#110',
                    'https://gitlab.example/group/repo/-/issues/0']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                m.parse_link(url, ['team.feishu.cn'], [('gitlab.example', 'group/repo')])

    def test_same_version_and_reordered_claims_are_noop(self):
        a = {'p1': {'speaker': 'Unknown', 'time': None, 'text': 'A'}, 'p2': {'text': 'B'}}
        self.assertEqual(m.delta_plan(a, dict(reversed(list(a.items()))))['action'], 'noop')

    def test_revision_only_changes_delta_and_retracts_removed_claim(self):
        actual = m.delta_plan({'p1': 'same', 'p2': 'old', 'p3': 'removed'}, {'p1': 'same', 'p2': 'new', 'p4': 'added'})
        self.assertEqual(actual, {'action': 'delta', 'added': ['p4'], 'changed': ['p2'], 'removed': ['p3']})

    def test_unknown_result_never_plans_new_write(self):
        self.assertEqual(m.delta_plan({}, {'p1': 'new'}, pending=True)['action'], 'reconcile')

    def test_attribution_change_is_substantive(self):
        self.assertEqual(m.delta_plan({'p1': {'speaker': 'Unknown'}}, {'p1': {'speaker': 'Named'}})['changed'], ['p1'])


if __name__ == '__main__':
    unittest.main()
