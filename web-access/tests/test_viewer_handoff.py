import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/remote_web_session.py'


class ViewerHandoffTests(unittest.TestCase):
    def test_plan_pairs_viewer_server_and_ssh_helper_with_private_task_capability_path(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(SCRIPT), 'plan', '--mode', 'vnc',
                                     '--port', '5900', '--batch', 'viewer-task', '--root', directory,
                                     '--ssh-user', 'owner', '--ssh-host', 'build-host', '--site', 'Example'],
                                    capture_output=True, text=True, check=True)
            plan = json.loads(result.stdout)
            expected = '--capability-file=' + str(Path(plan['profile']) / 'viewer-capability.json')
            self.assertIn(expected, plan['viewer_argv'])
            self.assertIn(expected, plan['viewer_connect_argv'])
            self.assertIn('--ssh-target=owner@build-host', plan['viewer_connect_argv'])
            self.assertEqual(Path(plan['profile']).stat().st_mode & 0o777, 0o700)
            self.assertFalse((Path(plan['profile']) / 'viewer-capability.json').exists())
            self.assertEqual(plan['state'], 'PLANNED')
            self.assertNotIn('#', plan['prompt'])


if __name__ == '__main__':
    unittest.main()
