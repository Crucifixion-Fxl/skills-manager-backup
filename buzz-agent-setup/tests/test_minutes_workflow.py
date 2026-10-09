"""Local render contract; does not assert native relay execution or SaaS authorization."""
import importlib.util
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('render_minutes', ROOT / 'scripts/render_minutes_workflow.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


class MinutesWorkflowTest(unittest.TestCase):
    def workflow(self):
        # BaseLoader avoids YAML 1.1 treating native schema key 'on' as boolean.
        return yaml.load(m.render('project-desk', ['a' * 64], ['team.feishu.cn'], ['gitlab.example/group/repo']), Loader=yaml.BaseLoader)

    def test_native_schema_and_disabled_default(self):
        w = self.workflow()
        self.assertEqual(set(w), {'name', 'description', 'enabled', 'trigger', 'steps'})
        self.assertEqual(w['enabled'], 'false')
        self.assertEqual(w['trigger']['on'], 'message_posted')
        self.assertEqual(set(w['trigger']), {'on', 'filter'})
        self.assertEqual(set(w['steps'][0]), {'id', 'action', 'text'})
        self.assertEqual(w['steps'][0]['action'], 'send_message')

    def test_filter_has_only_verified_native_variables(self):
        f = self.workflow()['trigger']['filter']
        self.assertEqual(set(re.findall(r'\btrigger_\w+', f)), {'trigger_author', 'trigger_text'})
        self.assertIn('https://team.feishu.cn/minutes/', f)
        self.assertIn('https://gitlab.example/group/repo/-/issues/', f)
        for marker in ['minutes-issue-wake:v1', 'minutes-issue-result:v1', 'gitlab-notify:v1']:
            self.assertIn('!str_contains(trigger_text, "[' + marker + ']")', f)

    def test_wake_fixed_agent_and_source_not_untrusted_body(self):
        text = self.workflow()['steps'][0]['text']
        self.assertIn('@project-desk', text)
        self.assertIn('{{trigger.message_id}}', text)
        self.assertIn('{{trigger.channel_id}}', text)
        self.assertNotIn('{{trigger.text}}', text)
        self.assertNotIn('https://', text)
        self.assertNotIn('ready-for-implementation', text)

    def test_injection_rejected(self):
        for args in [('desk\nnext: evil', ['a'*64], ['team.feishu.cn'], []),
                     ('desk', [], ['team.feishu.cn'], []),
                     ('desk', ['a'*64], ['team.feishu.cn.evil'], []),
                     ('desk', ['a'*64], ['team.feishu.cn'], ['gitlab.example/group/../repo'])]:
            with self.assertRaises(ValueError):
                m.render(*args)


if __name__ == '__main__':
    unittest.main()
