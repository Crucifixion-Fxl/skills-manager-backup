"""Owner writeback is independent of release-agent and never authorizes release."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).parent))
from test_acceptance import FakeClient, ISSUE, SOURCE, SOURCE_PATH, TARGET, acc, batch, note
sys.modules[acc.__name__] = acc


class OwnerWritebackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.client = FakeClient()
        self.client.data.pop(SOURCE_PATH + '/notes/100')
        self.client.data[TARGET]['assignees'] = [{'id': 868}]
        self.client.calls = 0; self.client.client_wall_seconds = 0
        self.event = str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, args):
        stream = io.StringIO()
        with mock.patch.object(acc, 'Client', return_value=self.client), contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            try:
                status = acc.main(args)
            except SystemExit as exc:
                return exc.code, {'error': 'ARGPARSE_REQUIRES_BATCH'}
        return status, json.loads(stream.getvalue())

    def args(self, result='passed'):
        return ['report', '--issue', ISSUE, '--event-id', self.event, '--state-dir', self.temp.name,
                '--result', result, '--reason', '负责人当前会话明确确认结果', '--stage', 'gray',
                '--scope', 'focus-display', '--confirmation',
                '本功能无需灰度验证' if result == 'na' else '本功能灰度验收已经完成']

    def test_report_without_release_agent_or_batch_context(self):
        status, result = self.run_cli(self.args())
        self.assertEqual(status, 0, result)
        self.assertEqual(result['verdict'], 'REPORT_VERIFIED')
        self.assertFalse(result['authorizes_deployment'])
        self.assertEqual(self.client.posts, 1)

    def test_missing_deployment_context_is_recorded_not_invented_or_blocked(self):
        status, result = self.run_cli(self.args())
        self.assertEqual(status, 0, result)
        self.assertIn('environment', result['missing_context'])
        self.assertIn('runtime_revision', result['missing_context'])
        self.assertFalse(result['deployment_verified'])
        record = acc.extract(self.client.lists[TARGET + '/notes'][0]['body'], 'addx.acceptance.owner-report/v1')
        self.assertIsNone(record['environment'])
        self.assertIsNone(record['revision'])
        self.assertNotIn('batch_note', record)
        self.assertEqual(record['result'], 'passed')

    def test_current_session_waiver_does_not_need_an_existing_gitlab_comment(self):
        status, result = self.run_cli(self.args('na'))
        self.assertEqual(status, 0, result)
        record = acc.extract(self.client.lists[TARGET + '/notes'][0]['body'], 'addx.acceptance.owner-report/v1')
        self.assertEqual(record['result'], 'na')
        self.assertIsNone(record['decision_note'])
        self.assertEqual(record['owner_confirmation'], '本功能无需灰度验证')

    def test_native_issue_owner_is_required_before_write(self):
        self.client.data[TARGET]['assignees'] = [{'id': 999}]
        status, result = self.run_cli(self.args())
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'REPORTER_IS_NOT_OWNER')
        self.assertEqual(self.client.posts, 0)

    def add_mr(self, author=868, project=833):
        url = 'https://gitlab.addx.ai/CLOUD/iot-service-unified/-/merge_requests/3472'
        path = 'projects/CLOUD%2Fiot-service-unified/merge_requests/3472'
        value = {'project_id': project, 'iid': 3472, 'state': 'opened', 'web_url': url, 'author': {'id': author}}
        self.client.data[path] = value
        self.client.lists[TARGET + '/related_merge_requests'] = [value]
        return url

    def test_native_mr_author_can_report_when_issue_has_no_assignee(self):
        self.client.data[TARGET]['assignees'] = []
        url = self.add_mr()
        status, result = self.run_cli(self.args() + ['--mr', url])
        self.assertEqual(status, 0, result)
        self.assertEqual(result['mr_references_verified'], 1)
        self.assertEqual(self.client.posts, 2)
        status, result = self.run_cli(['pending', '--issue', ISSUE])
        self.assertEqual(status, 0, result)
        self.assertEqual(result['items'][0]['result'], 'passed')
        self.assertEqual(result['unverified_records'], [])

    def test_same_iid_in_another_project_is_not_a_native_association(self):
        self.client.data[TARGET]['assignees'] = []
        url = self.add_mr()
        self.client.lists[TARGET + '/related_merge_requests'][0] = dict(self.client.data['projects/CLOUD%2Fiot-service-unified/merge_requests/3472'], project_id=999)
        status, result = self.run_cli(self.args() + ['--mr', url])
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'MR_BINDING_UNVERIFIED')
        self.assertEqual(self.client.posts, 0)

    def test_same_event_is_idempotent_without_a_batch(self):
        one, a = self.run_cli(self.args()); two, b = self.run_cli(self.args())
        self.assertEqual((one, two), (0, 0), (a, b))
        self.assertEqual(a['receipt'], b['receipt'])
        self.assertEqual(self.client.posts, 1)

    def test_unknown_write_is_not_blindly_replayed(self):
        self.client.mode = 'drop'
        status, result = self.run_cli(self.args())
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'WRITE_OUTCOME_UNKNOWN')
        status, result = self.run_cli(self.args())
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'UNRESOLVED_WRITE_OUTCOME')
        self.assertEqual(self.client.posts, 1)

    def test_partial_remains_partial_and_keeps_remaining_scope(self):
        status, result = self.run_cli(self.args('partial') + ['--remaining', 'NOTICE未覆盖'])
        self.assertEqual(status, 0, result)
        status, result = self.run_cli(['pending', '--issue', ISSUE, '--stage', 'gray'])
        self.assertEqual(status, 0, result)
        self.assertEqual(result['items'][0]['result'], 'partial')
        self.assertEqual(result['items'][0]['remaining'], ['NOTICE未覆盖'])

    def test_pending_reads_current_issue_without_a_deployment_batch(self):
        status, result = self.run_cli(['pending', '--issue', ISSUE])
        self.assertEqual(status, 0, result)
        self.assertEqual(result['items'][0]['result'], 'not-recorded')
        self.assertFalse(result['deployment_verified'])

    def test_release_check_still_requires_trusted_batch(self):
        args = ['check']
        for key in ('deployment_id', 'environment', 'stage', 'revision'):
            args.extend(['--expect-' + key.replace('_', '-'), batch()[key]])
        status, result = self.run_cli(args)
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'BATCH_CONTEXT_REQUIRED')
        self.assertEqual(self.client.posts, 0)

    def test_independent_owner_pass_does_not_automatically_pass_a_release_batch(self):
        status, result = self.run_cli(self.args() + ['--environment', 'prod-us', '--revision', 'a' * 40]); self.assertEqual(status, 0, result)
        self.client.data[SOURCE_PATH + '/notes/100'] = note(100, 22, acc.block(acc.BATCH, batch()))
        check = ['--batch-note', SOURCE, '--publisher-id', '22', 'check']
        for key in ('deployment_id', 'environment', 'stage', 'revision'):
            check.extend(['--expect-' + key.replace('_', '-'), batch()[key]])
        status, result = self.run_cli(check)
        self.assertEqual(status, 1, result)
        self.assertEqual(result['verdict'], 'ACCEPTANCE_BLOCKED')


class OwnerCliProtocolTests(unittest.TestCase):
    def test_real_cli_protocol_reports_without_reading_a_batch(self):
        import os
        import subprocess
        import test_acceptance as fixtures
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            root = Path(folder); binary = root / 'fixture-glab'
            binary.write_text('#!' + sys.executable + '\n' + fixtures.FAKE_GLAB); binary.chmod(0o700)
            client = FakeClient(); client.data.pop(SOURCE_PATH + '/notes/100')
            client.data[TARGET]['assignees'] = [{'id': 868}]
            state = binary.with_suffix('.json')
            state.write_text(json.dumps({'data': client.data, 'lists': client.lists, 'posts': 0, 'calls': []}))
            event = str(uuid.uuid4())
            cmd = [sys.executable, str(fixtures.SCRIPT), '--glab', str(binary), 'report',
                   '--issue', ISSUE, '--event-id', event, '--state-dir', str(root / 'journal'),
                   '--result', 'na', '--scope', 'focus-display', '--stage', 'gray',
                   '--reason', '负责人明确免验', '--confirmation', '本功能无需灰度验证']
            for _ in range(2):
                r = subprocess.run(cmd, text=True, capture_output=True, timeout=20,
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                response = json.loads(r.stdout)
                self.assertEqual(response['verdict'], 'REPORT_VERIFIED')
                self.assertFalse(response['deployment_verified'])
            data = json.loads(state.read_text())
            self.assertEqual(data['posts'], 1)
            self.assertFalse(any('/issues/289/' in path for _, path in data['calls']))



class DecouplingBoundaryTests(unittest.TestCase):
    setUp = OwnerWritebackTests.setUp
    tearDown = OwnerWritebackTests.tearDown
    run_cli = OwnerWritebackTests.run_cli
    args = OwnerWritebackTests.args
    def test_bound_report_rejects_owner_context_options_before_write(self):
        self.client.data[SOURCE_PATH + '/notes/100'] = note(100, 22, acc.block(acc.BATCH, batch()))
        args = ['--batch-note', SOURCE, '--publisher-id', '22', *self.args(),
                '--scope', 'only-one-subcase', '--revision', 'b' * 40]
        status, result = self.run_cli(args)
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'OWNER_OPTIONS_REQUIRE_INDEPENDENT_MODE')
        self.assertEqual(self.client.posts, 0)

    def test_even_empty_owner_context_is_not_silently_ignored_in_batch_mode(self):
        self.client.data[SOURCE_PATH + '/notes/100'] = note(100, 22, acc.block(acc.BATCH, batch()))
        args = ['--batch-note', SOURCE, '--publisher-id', '22', 'report', '--issue', ISSUE,
                '--event-id', self.event, '--state-dir', self.temp.name, '--result', 'passed',
                '--reason', '旧批次范围', '--evidence', ISSUE, '--revision', '']
        status, result = self.run_cli(args)
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'OWNER_OPTIONS_REQUIRE_INDEPENDENT_MODE')
        self.assertEqual(self.client.posts, 0)

    def test_reader_does_not_promote_a_commenter_to_owner_after_assignment_changes(self):
        status, result = self.run_cli(self.args()); self.assertEqual(status, 0, result)
        receipt = result['receipt']
        self.client.data[TARGET]['assignees'] = []
        status, result = self.run_cli(['pending', '--issue', ISSUE, '--assignee', '868'])
        self.assertEqual(status, 0, result)
        self.assertEqual(result['items'][0]['result'], 'not-recorded')
        self.assertEqual(result['unverified_records'][0]['receipt'], receipt)
        self.assertEqual(result['unverified_records'][0]['reason'], 'OWNER_ROLE_UNVERIFIED')
        status, _ = self.run_cli(self.args())
        self.assertEqual(status, 2)

    def test_persisted_owner_schema_cannot_claim_deployment_authority(self):
        status, result = self.run_cli(self.args()); self.assertEqual(status, 0, result)
        raw = self.client.lists[TARGET + '/notes'][0]
        data = acc.extract(raw['body'], 'addx.acceptance.owner-report/v1')
        self.assertIs(data.get('authorizes_deployment'), False)
        data['authorizes_deployment'] = True
        raw['body'] = acc.block('addx.acceptance.owner-report/v1', data)
        status, result = self.run_cli(['pending', '--issue', ISSUE])
        self.assertEqual(status, 2)
        self.assertEqual(result['error'], 'OWNER_RECORD_IS_NOT_DEPLOYMENT_PROOF')


if __name__ == '__main__':
    unittest.main()
