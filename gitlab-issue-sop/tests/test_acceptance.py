import importlib.util
import json
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/acceptance.py"
spec = importlib.util.spec_from_file_location("acceptance", SCRIPT)
acc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acc)


def batch():
    return {"schema": "addx.acceptance.batch/v1", "batch_id": "iot-us-gray-1052",
            "deployment_id": "cicd:1052:deployment-1", "environment": "prod-us", "stage": "gray",
            "revision": "a" * 40, "deployed_at": "2026-10-16T09:00:00Z", "status": "success",
            "items": [{"issue": "https://gitlab.addx.ai/CLOUD/iot-service-unified/-/issues/277",
                       "owner_id": 868, "scope": ["focus-display"], "required": True, "mrs": []}]}


class BatchContractTests(unittest.TestCase):
    def test_successful_batch_has_stable_digest(self):
        b = batch()
        self.assertEqual(acc.validate_batch(b), b)
        self.assertEqual(acc.digest(b), acc.digest(json.loads(json.dumps(b))))

    def test_unstarted_or_failed_deployment_never_creates_acceptance(self):
        for status in ("created", "running", "failed"):
            b = batch(); b["status"] = status
            with self.subTest(status=status), self.assertRaises(acc.Error):
                acc.validate_batch(b)

    def test_duplicate_scope_and_missing_owner_fail_closed(self):
        for field, value in (("owner_id", 0), ("scope", ["focus-display", "focus-display"])):
            b = batch(); b["items"][0][field] = value
            with self.subTest(field=field), self.assertRaises(acc.Error):
                acc.validate_batch(b)


import copy
from datetime import datetime, timezone
import tempfile
import uuid

SOURCE = 'https://gitlab.addx.ai/CLOUD/iot-service-unified/-/issues/289#note_100'
ISSUE = batch()['items'][0]['issue']
SOURCE_PATH = acc.issue_path(SOURCE.split('#')[0])
TARGET = acc.issue_path(ISSUE)


def note(ident, author, body):
    now = datetime.now(timezone.utc).isoformat()
    return {'id': ident, 'author': {'id': author}, 'body': body, 'system': False,
            'created_at': now, 'updated_at': now}


class FakeClient:
    """Explicit protocol fixture; no real CICD or GitLab write evidence."""
    def __init__(self, value=None):
        value = value or batch()
        self.data = {'user': {'id': 868, 'state': 'active'},
                     SOURCE_PATH + '/notes/100': note(100, 22, acc.block(acc.BATCH, value)),
                     TARGET: {'id': 2770, 'project_id': 833, 'iid': 277, 'web_url': ISSUE}}
        self.lists = {TARGET + '/notes': [], TARGET + '/related_merge_requests': []}
        self.posts = 0
        self.mode = None
        self.readback_error = False

    def get(self, path):
        return copy.deepcopy(self.data[path])

    def pages(self, path):
        return copy.deepcopy(self.lists.get(path, []))

    def request(self, path, method='GET', payload=None):
        if method == 'POST':
            self.posts += 1
            if self.mode == 'drop':
                self.mode = None
                raise acc.Error('WRITE_OUTCOME_UNKNOWN')
            n = note(200 + self.posts, 868, payload['body'].rstrip('\n'))
            self.lists.setdefault(path, []).append(n)
            self.data[path + '/' + str(n['id'])] = n
            if self.mode == 'persist-then-error':
                self.mode = None
                raise acc.Error('WRITE_OUTCOME_UNKNOWN')
            return copy.deepcopy(n)
        if self.readback_error:
            self.readback_error = False
            raise acc.Error('API_READ_FAILED')
        return copy.deepcopy(self.data[path])


def details(result='passed', remaining=None):
    return {'result': result, 'reason': '专项验收已按约定范围执行',
            'evidence': ['https://gitlab.addx.ai/example/repo/-/jobs/123'],
            'remaining': remaining or [], 'decision_note': None, 'event_id': str(uuid.uuid4())}


class AcceptanceFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.client = FakeClient()
        self.service = acc.Service(self.client, SOURCE, 22)
        self.expected = {k: batch()[k] for k in ('deployment_id', 'environment', 'stage', 'revision')}

    def tearDown(self):
        self.temp.cleanup()

    def report(self, data=None):
        return self.service.report(ISSUE, data or details(), self.temp.name)

    def test_277_pre_only_comment_is_pending_in_gray(self):
        self.client.lists[TARGET + '/notes'] = [note(10, 868, 'pre-us 环境验收已通过')]
        result = self.service.check(self.expected)
        self.assertEqual(result['verdict'], 'ACCEPTANCE_BLOCKED')
        self.assertEqual(result['blockers'][0]['result'], 'pending')
        self.assertFalse(result['authorizes_deployment'])

    def test_owner_pass_is_written_and_read_back(self):
        result = self.report()
        self.assertEqual(result['verdict'], 'REPORT_VERIFIED')
        self.assertEqual(self.client.posts, 1)
        checked = self.service.check(self.expected)
        self.assertEqual(checked['verdict'], 'ACCEPTANCE_SATISFIED')
        self.assertFalse(checked['authorizes_deployment'])

    def test_233_partial_gray_keeps_notice_pending(self):
        self.report(details('partial', ['NOTICE 实际命中 stable，尚缺灰度覆盖']))
        result = self.service.check(self.expected)
        self.assertEqual(result['blockers'][0]['result'], 'partial')
        self.assertIn('NOTICE', result['blockers'][0]['remaining'][0])

    def test_241_250_287_waiver_is_na_and_not_pass(self):
        for message in ('改动功能灰度不需要验证。', 'pre，灰度环境不需验证'):
            with self.subTest(message=message):
                self.client.data[TARGET + '/notes/55'] = note(55, 868, message)
                data = details('na'); data['decision_note'] = ISSUE + '#note_55'
                self.report(data)
                item = self.service.check(self.expected)['items'][0]
                self.assertEqual(item['result'], 'na')

    def test_waiver_decision_edit_is_detected(self):
        self.client.data[TARGET + '/notes/55'] = note(55, 868, '本功能灰度免验')
        data = details('na'); data['decision_note'] = ISSUE + '#note_55'
        self.report(data)
        self.client.data[TARGET + '/notes/55']['body'] = '撤回免验，需要补测'
        with self.assertRaisesRegex(acc.Error, 'WAIVER_DECISION_CHANGED'):
            self.service.check(self.expected)

    def test_waiver_cannot_borrow_another_owners_decision(self):
        self.client.data[TARGET + '/notes/55'] = note(55, 999, '本功能免验')
        data = details('na'); data['decision_note'] = ISSUE + '#note_55'
        with self.assertRaises(acc.Error):
            self.report(data)
        self.assertEqual(self.client.posts, 0)

    def test_current_user_must_be_the_batch_owner(self):
        self.service.actor['id'] = 999
        with self.assertRaisesRegex(acc.Error, 'REPORTER_IS_NOT_OWNER'):
            self.report()
        self.assertEqual(self.client.posts, 0)

    def test_publisher_is_configuration_not_self_claim_in_note(self):
        with self.assertRaisesRegex(acc.Error, 'UNAUTHORIZED_NOTE_AUTHOR'):
            acc.Service(self.client, SOURCE, 999)

    def test_edited_batch_rejected(self):
        self.client.data[SOURCE_PATH + '/notes/100']['updated_at'] = '2026-10-21T00:00:00Z'
        with self.assertRaisesRegex(acc.Error, 'EDITED_RECEIPT'):
            acc.Service(self.client, SOURCE, 22)

    def test_same_event_retry_is_idempotent(self):
        data = details()
        first, second = self.report(data), self.report(data)
        self.assertEqual(first['receipt'], second['receipt'])
        self.assertEqual(self.client.posts, 1)

    def test_same_event_different_content_is_rejected(self):
        data = details(); self.report(data); data['reason'] = '修改同一事件的内容'
        with self.assertRaisesRegex(acc.Error, 'EVENT_CONTENT_CONFLICT'):
            self.report(data)
        self.assertEqual(self.client.posts, 1)

    def test_lost_write_response_is_reconciled_without_second_post(self):
        data = details(); self.client.mode = 'persist-then-error'
        with self.assertRaisesRegex(acc.Error, 'WRITE_OUTCOME_UNKNOWN'):
            self.report(data)
        self.assertEqual(self.report(data)['verdict'], 'REPORT_VERIFIED')
        self.assertEqual(self.client.posts, 1)

    def test_unknown_write_with_no_visible_note_never_blindly_replays(self):
        data = details(); self.client.mode = 'drop'
        with self.assertRaises(acc.Error):
            self.report(data)
        with self.assertRaisesRegex(acc.Error, 'UNRESOLVED_WRITE_OUTCOME'):
            self.report(data)
        self.assertEqual(self.client.posts, 1)

    def test_failed_readback_can_resume_the_known_note(self):
        data = details(); self.client.readback_error = True
        with self.assertRaisesRegex(acc.Error, 'API_READ_FAILED'):
            self.report(data)
        self.assertEqual(self.report(data)['verdict'], 'REPORT_VERIFIED')
        self.assertEqual(self.client.posts, 1)

    def test_replay_from_a_new_private_journal_reuses_remote_event(self):
        data = details(); first = self.report(data)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as other:
            second = self.service.report(ISSUE, data, other)
        self.assertEqual(first['receipt'], second['receipt'])
        self.assertEqual(self.client.posts, 1)

    def test_unauthorized_or_edited_receipt_never_passes(self):
        self.report()
        record = self.client.lists[TARGET + '/notes'][0]
        record['author']['id'] = 999
        with self.assertRaises(acc.Error):
            self.service.check(self.expected)
        record['author']['id'] = 868
        record['updated_at'] = '2026-10-21T00:00:00Z'
        with self.assertRaises(acc.Error):
            self.service.check(self.expected)

    def test_duplicate_event_is_detected_across_hosts(self):
        self.report()
        self.client.lists[TARGET + '/notes'].append(copy.deepcopy(self.client.lists[TARGET + '/notes'][0]))
        with self.assertRaisesRegex(acc.Error, 'DUPLICATE_EVENT'):
            self.service.check(self.expected)

    def test_changed_actual_deployment_or_scope_is_blocked(self):
        self.report()
        expected = dict(self.expected, revision='b' * 40)
        with self.assertRaisesRegex(acc.Error, 'ACTUAL_DEPLOYMENT_MISMATCH'):
            self.service.check(expected)
        self.service.batch['items'][0]['scope'] = ['other-scope']
        with self.assertRaises(acc.Error):
            self.service.check(self.expected)

    def test_pass_cannot_hide_uncovered_scope_or_missing_evidence(self):
        data = details(remaining=['仍需 NOTICE 验证'])
        with self.assertRaises(acc.Error):
            self.report(data)
        data = details(); data['evidence'] = []
        with self.assertRaises(acc.Error):
            self.report(data)
        self.assertEqual(self.client.posts, 0)

    def test_mr_reference_is_native_linked_and_retried_safely(self):
        self.service.batch['items'][0]['mrs'] = [{'project_id': 833, 'iid': 3472}]
        self.service.batch_digest = acc.digest(self.service.batch)
        self.client.lists[TARGET + '/related_merge_requests'] = [
            {'project_id': 833, 'iid': 3472, 'state': 'merged',
             'web_url': 'https://gitlab.addx.ai/CLOUD/iot-service-unified/-/merge_requests/3472'}]
        data = details()
        result = self.report(data)
        self.assertEqual(result['mr_references_verified'], 1)
        self.report(data)
        self.assertEqual(self.client.posts, 2)
        pointer = self.client.lists['projects/833/merge_requests/3472/notes'][0]['body']
        self.assertIn(result['receipt'], pointer)
        self.assertNotIn('passed', pointer)

    def test_unlinked_mr_rejected_before_write(self):
        self.service.batch['items'][0]['mrs'] = [{'project_id': 833, 'iid': 9999}]
        with self.assertRaisesRegex(acc.Error, 'MR_BINDING_UNVERIFIED'):
            self.report()
        self.assertEqual(self.client.posts, 0)

    def test_receipt_file_contains_actual_context_and_is_private(self):
        result = self.report()
        p = Path(result['state_file'])
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        state = json.loads(p.read_text())
        self.assertEqual(state['receipt']['agent']['cwd'], str(Path.cwd()))
        self.assertTrue(state['verified'])


import os
import subprocess
import sys
from unittest import mock


class ClientProtocolTests(unittest.TestCase):
    def test_reads_all_pages_and_refuses_unbounded_incomplete_scan(self):
        client = object.__new__(acc.Client)
        pages = [[{'id': i} for i in range(100)], [{'id': 100}]]
        client.request = mock.Mock(side_effect=pages)
        self.assertEqual(len(client.pages('projects/1/issues/1/notes')), 101)
        self.assertIn('page=2', client.request.call_args.args[0])
        client.request = mock.Mock(return_value=[{}] * 100)
        with self.assertRaisesRegex(acc.Error, 'PAGINATION_INCOMPLETE'):
            client.pages('projects/1/issues/1/notes')

    def test_transport_error_never_leaks_stderr(self):
        with mock.patch.object(acc.shutil, 'which', return_value='/fixture/glab'):
            client = acc.Client()
        response = subprocess.CompletedProcess([], 1, '', 'fixture-secret-do-not-print')
        with mock.patch.object(acc.subprocess, 'run', return_value=response):
            with self.assertRaisesRegex(acc.Error, '^API_READ_FAILED$'):
                client.request('user')
        self.assertEqual(client.calls, 1)

    def test_credentials_are_not_accepted_in_urls(self):
        for url in ('https://a:b@gitlab.addx.ai/a/b/-/issues/1',
                    'https://gitlab.addx.ai/a/b/-/issues/1?token=fixture',
                    'http://gitlab.addx.ai/a/b/-/issues/1',
                    'https://other.example/a/b/-/issues/1#note_1'):
            with self.subTest(url=url), self.assertRaises(acc.Error):
                acc.note_ref(url)

    def test_symlink_journal_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            target = Path(folder) / 'target'; target.mkdir(mode=0o700)
            link = Path(folder) / 'link'; link.symlink_to(target)
            with self.assertRaisesRegex(acc.Error, 'UNSAFE_STATE_PATH'):
                with acc.journal(link, 868, str(uuid.uuid4())):
                    pass


FAKE_GLAB = r'''import datetime,json,pathlib,sys
p=pathlib.Path(__file__).with_suffix('.json')
s=json.loads(p.read_text()); a=sys.argv[1:]
m=a[a.index('--method')+1]; ep=a[a.index('--method')+2]
s['calls'].append([m,ep]); base=ep.split('?')[0]
if m=='GET':
    if '?' in ep: result=s['lists'].get(base,[])
    elif ep in s['data']: result=s['data'][ep]
    else:
        print('fixture-private-response',file=sys.stderr);sys.exit(1)
else:
    body=json.load(sys.stdin)['body'];s['posts']+=1
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    result={'id':1000+s['posts'],'author':{'id':868},'body':body,'system':False,
            'created_at':now,'updated_at':now}
    s['lists'].setdefault(ep,[]).append(result);s['data'][ep+'/'+str(result['id'])]=result
p.write_text(json.dumps(s)); print(json.dumps(result))
'''


class CliBlackBoxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.root = Path(self.temp.name)
        self.binary = self.root / 'fixture-glab'
        self.binary.write_text('#!' + sys.executable + '\n' + FAKE_GLAB)
        self.binary.chmod(0o700)
        client = FakeClient()
        self.state = self.binary.with_suffix('.json')
        self.state.write_text(json.dumps({'data': client.data, 'lists': client.lists, 'posts': 0, 'calls': []}))
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, publisher=22):
        cmd = [sys.executable, str(SCRIPT), '--glab', str(self.binary), '--batch-note', SOURCE,
               '--publisher-id', str(publisher), *args]
        r = subprocess.run(cmd, text=True, capture_output=True, env=self.env, timeout=20)
        self.assertNotIn('fixture-private-response', r.stdout + r.stderr)
        return r.returncode, json.loads(r.stdout)

    def report_args(self, event, result='passed'):
        return ['report', '--issue', ISSUE, '--event-id', event, '--state-dir', str(self.root / 'journal'),
                '--result', result, '--reason', '隔离协议测试结果', '--evidence', ISSUE]

    def check_args(self):
        args = ['check']
        for k in ('deployment_id', 'environment', 'stage', 'revision'):
            args.extend(['--expect-' + k.replace('_', '-'), batch()[k]])
        return args

    def test_pending_report_retry_and_check_protocol(self):
        code, result = self.run_cli('pending')
        self.assertEqual(code, 0); self.assertEqual(result['items'][0]['result'], 'pending')
        code, result = self.run_cli(*self.check_args())
        self.assertEqual(code, 1); self.assertEqual(result['verdict'], 'ACCEPTANCE_BLOCKED')
        event = str(uuid.uuid4())
        for _ in range(2):
            code, result = self.run_cli(*self.report_args(event))
            self.assertEqual(code, 0, result)
            self.assertEqual(result['verdict'], 'REPORT_VERIFIED')
        code, result = self.run_cli(*self.check_args())
        self.assertEqual(code, 0); self.assertEqual(result['verdict'], 'ACCEPTANCE_SATISFIED')
        self.assertFalse(result['authorizes_deployment'])
        self.assertEqual(json.loads(self.state.read_text())['posts'], 1)

    def test_wrong_publisher_and_missing_source_fail_with_exit_two(self):
        code, result = self.run_cli('pending', publisher=999)
        self.assertEqual(code, 2); self.assertEqual(result['error'], 'UNAUTHORIZED_NOTE_AUTHOR')
        data = json.loads(self.state.read_text()); data['data'].pop(SOURCE_PATH + '/notes/100')
        self.state.write_text(json.dumps(data))
        code, result = self.run_cli('pending')
        self.assertEqual(code, 2); self.assertEqual(result['error'], 'API_READ_FAILED')

    def test_partial_never_turns_into_complete_acceptance(self):
        args = self.report_args(str(uuid.uuid4()), 'partial') + ['--remaining', 'NOTICE 尚未覆盖灰度']
        code, result = self.run_cli(*args)
        self.assertEqual(code, 0, result)
        code, result = self.run_cli(*self.check_args())
        self.assertEqual(code, 1); self.assertEqual(result['blockers'][0]['result'], 'partial')

    def test_machine_waiver_keeps_na_semantics(self):
        data = json.loads(self.state.read_text())
        data['data'][TARGET + '/notes/55'] = note(55, 868, '改动功能灰度不需要验证。')
        self.state.write_text(json.dumps(data))
        args = self.report_args(str(uuid.uuid4()), 'na') + ['--decision-note', ISSUE + '#note_55']
        code, result = self.run_cli(*args)
        self.assertEqual(code, 0, result)
        code, result = self.run_cli(*self.check_args())
        self.assertEqual(code, 0); self.assertEqual(result['items'][0]['result'], 'na')





class ReviewRegressionTests(unittest.TestCase):
    def test_client_server_clock_skew_does_not_poison_receipts(self):
        from datetime import timedelta
        client = FakeClient()
        source = client.data[SOURCE_PATH + '/notes/100']
        source['created_at'] = source['updated_at'] = '2026-10-16T09:00:01Z'
        original = client.request
        def request(path, method='GET', payload=None):
            result = original(path, method, payload)
            if method == 'POST':
                data = acc.extract(result['body'], acc.RECEIPT)
                server_time = (acc.timestamp(data['reported_at']) - timedelta(seconds=1)).isoformat()
                stored = client.data[path + '/' + str(result['id'])]
                stored['created_at'] = stored['updated_at'] = server_time
            return result
        client.request = request
        service = acc.Service(client, SOURCE, 22)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            service.report(ISSUE, details(), folder)
            expected = {k: batch()[k] for k in ('deployment_id', 'environment', 'stage', 'revision')}
            self.assertEqual(service.check(expected)['verdict'], 'ACCEPTANCE_SATISFIED')

    def test_receipts_before_the_gitlab_batch_are_rejected(self):
        from datetime import timedelta
        client = FakeClient(); service = acc.Service(client, SOURCE, 22)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            service.report(ISSUE, details(), folder)
            n = client.lists[TARGET + '/notes'][0]
            n['created_at'] = n['updated_at'] = (service.source_created_at - timedelta(seconds=1)).isoformat()
            expected = {k: batch()[k] for k in ('deployment_id', 'environment', 'stage', 'revision')}
            with self.assertRaisesRegex(acc.Error, 'RECEIPT_BEFORE_BATCH'):
                service.check(expected)

    def test_mr_failure_reports_confirmed_issue_and_recovery_context(self):
        import contextlib
        import io
        b = batch(); b['items'][0]['mrs'] = [{'project_id': 833, 'iid': 3472}]
        client = FakeClient(b); client.calls = 0; client.client_wall_seconds = 0
        client.lists[TARGET + '/related_merge_requests'] = [{
            'project_id': 833, 'iid': 3472, 'state': 'merged',
            'web_url': 'https://gitlab.addx.ai/CLOUD/iot-service-unified/-/merge_requests/3472'}]
        original = client.request
        def request(path, method='GET', payload=None):
            if 'merge_requests' in path and method == 'POST':
                raise acc.Error('WRITE_OUTCOME_UNKNOWN')
            return original(path, method, payload)
        client.request = request
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            output = io.StringIO()
            argv = ['--batch-note', SOURCE, '--publisher-id', '22', 'report', '--issue', ISSUE,
                    '--event-id', str(uuid.uuid4()), '--state-dir', folder, '--result', 'passed',
                    '--reason', '限定协议测试', '--evidence', ISSUE]
            with mock.patch.object(acc, 'Client', return_value=client), contextlib.redirect_stdout(output):
                status = acc.main(argv)
            result = json.loads(output.getvalue())
            self.assertEqual(status, 2)
            self.assertEqual(result['recovery']['receipt'], ISSUE + '#note_201')
            self.assertTrue(result['recovery']['issue_receipt_verified'])
            self.assertTrue(Path(result['recovery']['state_file']).is_file())
            self.assertEqual(result['recovery']['mr_references_verified'], 0)
            self.assertIn('same_event', result['recovery']['next_step'])


class CiReviewRegressionTests(unittest.TestCase):
    def test_inline_protocol_marker_text_roundtrips_and_old_receipt_stays_readable(self):
        client = FakeClient(); service = acc.Service(client, SOURCE, 22)
        data = details()
        data['reason'] = '验收说明含协议示例 <!-- ' + acc.RECEIPT + ' -->，这是普通文字'
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            result = service.report(ISSUE, data, folder)
            expected = {k: batch()[k] for k in ('deployment_id', 'environment', 'stage', 'revision')}
            self.assertEqual(service.check(expected)['verdict'], 'ACCEPTANCE_SATISFIED')
            raw = client.lists[TARGET + '/notes'][0]['body']
            self.assertEqual(acc.extract(raw, acc.RECEIPT)['reason'], data['reason'])
            self.assertEqual(service.report(ISSUE, data, folder)['receipt'], result['receipt'])
            self.assertEqual(client.posts, 1)

    def test_actual_journal_body_is_validated_before_any_post(self):
        client = FakeClient(); service = acc.Service(client, SOURCE, 22); data = details()
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            with acc.journal(folder, 868, data['event_id']) as (state, save, _):
                state['body'] = 'invalid persisted body'
                save()
            with self.assertRaisesRegex(acc.Error, 'INVALID_STRUCTURED_NOTE'):
                service.report(ISSUE, data, folder)
            self.assertEqual(client.posts, 0)

    def test_inline_event_marker_does_not_alias_another_note(self):
        client = FakeClient(); service = acc.Service(client, SOURCE, 22)
        endpoint = 'projects/833/merge_requests/3472/notes'
        event = str(uuid.uuid4()); marker = '<!-- acceptance-event:' + event + ' -->'
        client.lists[endpoint] = [note(50, 868, '普通文字引用 ' + marker)]
        body = marker + '\n本批验收回执链接'
        state = {'writes': {}}
        result = service.post_once(endpoint, marker, body, state, lambda: None)
        self.assertEqual(result['body'], body)
        self.assertEqual(client.posts, 1)

    def test_only_one_structural_block_is_allowed(self):
        value = {'schema': acc.RECEIPT, 'reason': '<!-- ' + acc.RECEIPT + ' -->'}
        raw = acc.block(acc.RECEIPT, value)
        self.assertEqual(acc.extract(raw, acc.RECEIPT), value)
        with self.assertRaises(acc.Error):
            acc.extract(raw + raw, acc.RECEIPT)

    def test_interleaved_independent_mr_journals_detect_visible_duplicates(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        client = FakeClient(); first = acc.Service(client, SOURCE, 22); second = acc.Service(client, SOURCE, 22)
        endpoint = 'projects/833/merge_requests/3472/notes'
        event = str(uuid.uuid4()); marker = '<!-- acceptance-event:' + event + ' -->'
        body = marker + '\n本批部署验收以关联Issue为准：' + ISSUE
        read_barrier, post_barrier, lock = threading.Barrier(2), threading.Barrier(2), threading.Lock()
        original_pages, original_request = client.pages, client.request
        count = [0]
        def pages(path):
            if path == endpoint:
                with lock:
                    initial = count[0] < 2
                    count[0] += 1
                if initial:
                    read_barrier.wait(timeout=5)
                    return []
            return original_pages(path)
        def request(path, method='GET', payload=None):
            result = original_request(path, method, payload)
            if path == endpoint and method == 'POST':
                post_barrier.wait(timeout=5)
            return result
        client.pages, client.request = pages, request
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as a, tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as b:
            def run(service, directory):
                with acc.journal(directory, 868, event) as (state, save, _):
                    try:
                        service.post_once(endpoint, marker, body, state, save)
                        return 'verified'
                    except acc.Error as exc:
                        return str(exc)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda x: run(*x), [(first, a), (second, b)]))
            self.assertEqual(results, ['DUPLICATE_EVENT', 'DUPLICATE_EVENT'])
            self.assertEqual(len(client.lists[endpoint]), 2)
            # A known note ID must not bypass duplicate detection on a later retry.
            record = {'writes': {endpoint: client.lists[endpoint][0]['id']}}
            with self.assertRaisesRegex(acc.Error, 'DUPLICATE_EVENT'):
                first.post_once(endpoint, marker, body, record, lambda: None)

    def test_second_mr_lost_response_recovers_without_reposting_prior_targets(self):
        value = batch(); value['items'][0]['mrs'] = [{'project_id': 833, 'iid': 3472}, {'project_id': 833, 'iid': 3473}]
        client = FakeClient(value); service = acc.Service(client, SOURCE, 22)
        client.lists[TARGET + '/related_merge_requests'] = [
            {'project_id': 833, 'iid': i, 'state': 'merged',
             'web_url': 'https://gitlab.addx.ai/CLOUD/iot-service-unified/-/merge_requests/' + str(i)} for i in (3472, 3473)]
        original = client.request; should_fail = [True]
        def request(path, method='GET', payload=None):
            result = original(path, method, payload)
            if '/merge_requests/3473/' in path and method == 'POST' and should_fail[0]:
                should_fail[0] = False
                raise acc.Error('WRITE_OUTCOME_UNKNOWN')
            return result
        client.request = request; data = details()
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            with self.assertRaisesRegex(acc.Error, 'WRITE_OUTCOME_UNKNOWN'):
                service.report(ISSUE, data, folder)
            self.assertTrue(service.progress['issue_receipt_verified'])
            self.assertEqual(service.progress['mr_references_verified'], 1)
            self.assertEqual(service.report(ISSUE, data, folder)['mr_references_verified'], 2)
            self.assertEqual(client.posts, 3)


if __name__ == "__main__":
    unittest.main()
