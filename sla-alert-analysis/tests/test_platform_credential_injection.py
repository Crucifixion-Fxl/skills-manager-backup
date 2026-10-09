import subprocess
import tempfile
import unittest
from pathlib import Path

LOADER = Path(__file__).resolve().parents[1] / 'scripts/_load_credentials.sh'

class HostCredentialTests(unittest.TestCase):
    def test_missing_injection_does_not_load_legacy_password_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'fixture-password'
            config.write_text('sla-alert-analysis:\n  sla-api-token: "fixture-unused-token"\n')
            env = {'PATH':'/usr/bin:/bin','LANG':'C'}
            env['A4X_PASSWORD_FILE'] = str(config)
            result=subprocess.run(['/bin/bash','-c','source "$1"; test -z "${SLA_API_TOKEN:-}${SUPERSET_USERNAME:-}${SUPERSET_PASSWORD:-}"','fixture',str(LOADER)],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0)
            self.assertEqual(result.stdout+result.stderr,'')

    def test_host_injected_values_survive_without_output(self):
        env={'PATH':'/usr/bin:/bin','LANG':'C'};env.update(SLA_API_TOKEN='fixture-sla',SUPERSET_USERNAME='fixture-user',SUPERSET_PASSWORD='fixture-password')
        result=subprocess.run(['/bin/bash','-c','source "$1"; test "$SLA_API_TOKEN" = fixture-sla && test "$SUPERSET_USERNAME" = fixture-user && test "$SUPERSET_PASSWORD" = fixture-password','fixture',str(LOADER)],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout+result.stderr,'')
