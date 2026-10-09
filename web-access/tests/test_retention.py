import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('rws', Path(__file__).parents[1] / 'scripts/remote_web_session.py')
rws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rws)

class RetentionTests(unittest.TestCase):
    def test_reuse_requires_saved_choice_same_site_account_and_unexpired_private_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = rws.session_profile(root, 'reddit-personal', site='Reddit', account='me', retention='save', now=1000)
            self.assertEqual(rws.session_profile(root, 'reddit-personal', site='Reddit', account='me', retention='save', resume=True, now=1001), profile)
            for args in [dict(site='Amazon', account='me'), dict(site='Reddit', account='other'), dict(site='Reddit', account='me', now=1000 + 8 * 86400)]:
                with self.assertRaises(rws.PlanError):
                    rws.session_profile(root, 'reddit-personal', retention='save', resume=True, **args)
            profile.chmod(0o755)
            with self.assertRaises(rws.PlanError):
                rws.session_profile(root, 'reddit-personal', site='Reddit', account='me', retention='save', resume=True, now=1001)

    def test_once_cannot_be_reused_or_silently_promoted_to_saved_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rws.session_profile(root, 'once', site='Amazon', account='me', retention='once')
            with self.assertRaises(rws.PlanError):
                rws.session_profile(root, 'once', site='Amazon', account='me', retention='save', resume=True)
            with self.assertRaises(rws.PlanError):
                rws.session_profile(root, 'unscoped', site='Reddit', retention='save')
            rws.delete_profile(root, 'once')
            self.assertFalse((root / 'once').exists())

    def test_vnc_plan_contains_complete_user_prompt_and_visible_browser_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = rws._build_parser().parse_args(['plan', '--root', tmp, '--batch', 'reddit', '--port', '5900',
                                                  '--ssh-user', 'me', '--ssh-host', 'devbox', '--site', 'Reddit'])
            result = rws._plan(args)
            self.assertEqual(result['mode'], 'vnc')
            self.assertEqual(result['browser_env']['DISPLAY'], ':99')
            self.assertFalse(any('headless' in arg for arg in result['browser_argv']))
            self.assertEqual(result['desktop_argv'][-2:], ['-s', '0'])
            self.assertIn('一次性', result['prompt'])
            self.assertIn('完成', result['prompt'])
            self.assertNotIn('Cookie', result['prompt'])

    def test_generic_handoff_preserves_task_and_requires_caller_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = rws._build_parser().parse_args(['plan', '--root', tmp, '--batch', 'verification', '--port', '5900',
                '--ssh-user', 'me', '--ssh-host', 'devbox', '--site', 'Amazon', '--reason', 'verification',
                '--action', '完成当前页面的安全校验'])
            result = rws._plan(args)
            self.assertIn('完成当前页面的安全校验', result['prompt'])
            self.assertEqual(result['handoff']['reason'], 'verification')
            self.assertEqual(result['handoff']['verification'], 'caller-required')
            self.assertEqual(result['state'], 'PLANNED')
            self.assertNotIn('ssh', result['prompt'].lower())
            self.assertNotIn('inspect', result['prompt'].lower())

if __name__ == '__main__':
    unittest.main()
