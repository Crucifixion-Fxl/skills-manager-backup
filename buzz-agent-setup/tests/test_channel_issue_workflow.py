"""Native template/schema and rejection paths, without deploying a Workflow."""
import importlib.util
from pathlib import Path
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('render_channel_issue', ROOT/'scripts/render_channel_issue_workflow.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


class ChannelIssueWorkflowTest(unittest.TestCase):
    def workflow(self):
        return yaml.load(m.render('project-desk'),Loader=yaml.BaseLoader)

    def test_daily_beijing_2200_disabled(self):
        w=self.workflow()
        self.assertEqual(w['enabled'],'false')
        self.assertEqual(w['trigger'],{'on':'schedule','cron':'0 14 * * *'})
        self.assertEqual(set(w),{'name','description','enabled','trigger','steps'})
        self.assertEqual(set(w['steps'][0]),{'id','action','text'})
        self.assertEqual(w['steps'][0]['action'],'send_message')

    def test_registered_agent_and_fixed_window_contract(self):
        text=self.workflow()['steps'][0]['text']
        for fragment in ['@project-desk','$addx:channel-issue-progress','22:00','24小时','AI','[channel-issue-wake:v1]']:
            self.assertIn(fragment,text)
        self.assertNotIn('{{trigger.text}}',text)
        self.assertNotIn('{{trigger.message_id}}',text) # schedule has no original human message
        self.assertNotIn('22:00前完成',text)

    def test_yaml_injection_and_unverified_agent_name_rejected(self):
        for agent in ['', 'desk\nsteps: evil','@desk','<desk-name>','A-desk','a'*65]:
            with self.subTest(agent=agent),self.assertRaises(ValueError):m.render(agent)

    def test_setup_entry_and_method_exist(self):
        repo=ROOT.parents[2]
        for ref in ['SKILL.md','references/runtime-setup.md','references/scheduled-workflows.md']:
            self.assertIn('channel-issue-workflow.md',(ROOT/ref).read_text())
        self.assertTrue((repo/'skills/collaboration/channel-issue-progress/SKILL.md').exists())


if __name__ == '__main__':unittest.main()
