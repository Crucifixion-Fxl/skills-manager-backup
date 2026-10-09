"""Offline completeness/planning tests; no permission or live-platform assertions."""
import importlib.util
from pathlib import Path
import unittest
import json
import subprocess
import sys
import tempfile
import os
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('discussion_contract', Path(__file__).resolve().parents[1] / 'scripts/discussion_contract.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def message(number, timestamp=1791554340, origin='human'):
    return {'channel_id': '12345678-1234-1234-1234-123456789abc', 'pubkey': 'a'*64, 'id': format(number, '064x'), 'created_at': timestamp, 'origin': origin, 'content': '讨论', 'thread_root': format(99, '064x')}


def item(number=1, target='gitlab.example/team/repo#12', kind='progress'):
    return {'id': 'claim-1', 'event_ids': [format(number, '064x')], 'kind': kind, 'target': target, 'summary': '完成测试，等待上线'}


class DiscussionContractTest(unittest.TestCase):
    def setUp(self):
        self.window = m.daily_window('2026-10-09T14:00:00Z')
        self.projects = ['gitlab.example/team/repo']

    def plan(self, messages=None, items=None, excluded=None, previous=None, pending=False, complete=True):
        return m.plan(self.window, messages if messages is not None else [message(1)],
                      items if items is not None else [item()], excluded or {}, self.projects,
                      channel_id='12345678-1234-1234-1234-123456789abc', previous=previous or [], pending=pending, complete=complete)

    def test_beijing_2200_is_fixed_utc_1400(self):
        self.assertEqual(self.window['start'], '2026-10-08T14:00:00Z')
        self.assertEqual(self.window['end'], '2026-10-09T14:00:00Z')
        self.assertEqual(m.daily_window('2026-10-09T22:00:00+08:00'), self.window)
        for tick in ['2026-10-09T14:00:01Z', '2026-10-09T14:30:00Z', '2026-10-09T14:00:00', '2026-10-09T22:00:00Z']:
            with self.subTest(tick=tick), self.assertRaises(ValueError):
                m.daily_window(tick)

    def test_half_open_window_and_old_root_context(self):
        start, end = self.window['start_epoch'], self.window['end_epoch']
        events = [message(1, start), message(2, end), message(3, start-1)]
        result = self.plan(events)
        self.assertEqual(result['covered_event_ids'], [message(1)['id']])
        self.assertEqual(result['context_event_ids'], sorted([message(2)['id'], message(3)['id']]))
        self.assertEqual(len(result['updates']), 1)

    def test_agent_original_progress_counts_too(self):
        result = self.plan([message(1, origin='agent')])
        self.assertEqual(len(result['updates']), 1)
        self.assertEqual(result['uncovered_event_ids'], [])

    def test_uncovered_messages_never_claim_complete(self):
        r = self.plan([message(1), message(2)])
        self.assertEqual(r['uncovered_event_ids'], [message(2)['id']])
        self.assertFalse(r['coverage_complete'])
        self.assertFalse(r['can_advance_window'])

    def test_ambiguous_and_unmatched_items_are_visible_pending(self):
        for target in [None, ['gitlab.example/team/repo#12', 'gitlab.example/team/repo#13']]:
            row = item(); row['target'] = target
            r = self.plan(items=[row])
            self.assertEqual(r['updates'], [])
            self.assertEqual(len(r['pending_items']), 1)
            self.assertTrue(r['coverage_complete'])
            self.assertFalse(r['can_advance_window'])

    def test_out_of_scope_never_becomes_update(self):
        r = self.plan(items=[item(target='gitlab.example/other/repo#12')])
        self.assertEqual(r['updates'], [])
        self.assertEqual(r['pending_items'][0]['reason'], 'target_out_of_scope')

    def test_one_message_can_hold_multiple_independent_issues(self):
        a, b = item(), item(target='gitlab.example/team/repo#13'); b['id'] = 'claim-2'
        self.assertEqual(len(self.plan(items=[a,b])['updates']), 2)

    def test_duplicate_delivery_and_cross_window_evidence_noop(self):
        first = self.plan()
        r = self.plan([message(1), message(1)], previous=[first['updates'][0]['evidence_key']])
        self.assertEqual(r['updates'], [])
        self.assertEqual(len(r['noops']), 1)
        self.assertTrue(r['can_advance_window'])

    def test_reader_metadata_and_display_name_do_not_duplicate_evidence(self):
        first = self.plan()
        reread = message(1)
        reread.update(snapshot_id='new-snapshot', fetched_at='later', display_name='新名字')
        result = self.plan([message(1), reread], previous=[first['updates'][0]['evidence_key']])
        self.assertEqual(result['updates'], [])
        self.assertEqual(len(result['noops']), 1)

    def test_other_channel_or_missing_signer_is_rejected(self):
        for changed in [{'channel_id': 'b'*64}, {'pubkey': None}]:
            event = message(1)
            event.update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.plan([event])

    def test_source_hash_changes_are_not_silent_noop(self):
        first = self.plan(); changed = message(1); changed['content'] = '更新的原文'
        r = self.plan([changed], previous=[first['updates'][0]['evidence_key']])
        self.assertEqual(len(r['updates']), 1)

    def test_incomplete_scan_or_unknown_write_blocks_all_new_plans(self):
        for kwargs in [{'complete': False}, {'pending': True}]:
            r = self.plan(**kwargs)
            self.assertEqual(r['updates'], [])
            self.assertFalse(r['can_advance_window'])
            self.assertIn(r['action'], ['incomplete', 'reconcile'])

    def test_sync_and_workflow_cannot_supply_update_evidence(self):
        for origin in ['gitlab_sync', 'workflow', 'result']:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.plan([message(1, origin=origin)])
        r = self.plan([message(1, origin='gitlab_sync')], items=[], excluded={message(1)['id']: 'already_in_issue'})
        self.assertTrue(r['coverage_complete'])
        self.assertEqual(r['updates'], [])

    def test_text_marker_is_not_proof_of_machine_origin(self):
        event = message(1); event['content'] = '[channel-issue-result:v1] 人的真实讨论'
        self.assertEqual(len(self.plan([event])['updates']), 1)

    def test_original_discussion_cannot_be_excluded_as_echo(self):
        with self.assertRaises(ValueError):
            self.plan(items=[], excluded={message(1)['id']: 'workflow_output'})
        r = self.plan(items=[], excluded={message(1)['id']: 'non_substantive'})
        self.assertTrue(r['coverage_complete'])

    def test_forged_event_reference_and_exclusion_rejected(self):
        row = item(2)
        with self.assertRaises(ValueError): self.plan(items=[row])
        with self.assertRaises(ValueError): self.plan(excluded={message(2)['id']: 'non_substantive'})
        with self.assertRaises(ValueError): self.plan(excluded={message(1)['id']: 'because I said so'})

    def test_conflicting_duplicate_and_invalid_origin_rejected(self):
        other = message(1); other['content'] = '不同的同ID正文'
        with self.assertRaises(ValueError): self.plan([message(1), other])
        with self.assertRaises(ValueError): self.plan([message(1, origin='unknown')])

    def test_malformed_target_summary_and_id_rejected(self):
        for change in [{'target': 'gitlab.example/team/../repo#12'}, {'target': 'gitlab.example/team/repo#0'},
                       {'summary': ''}, {'id': 'x\n'}, {'kind': 'approved'}]:
            row=item();row.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): self.plan(items=[row])

    def test_same_source_span_and_target_does_not_post_twice(self):
        a,b=item(),item();b['id']='other-llm-id'
        with self.assertRaises(ValueError): self.plan(items=[a,b])

    def test_source_span_key_handles_multiple_claims_in_one_event(self):
        a,b=item(),item();a['span_id']='p1';b['span_id']='p2';b['id']='claim-2'
        self.assertEqual(len(self.plan(items=[a,b])['updates']),2)

    def test_empty_window_is_successful_noop(self):
        r=self.plan(messages=[],items=[])
        self.assertTrue(r['can_advance_window']);self.assertEqual(r['updates'],[])


class CliContractTest(unittest.TestCase):
    def test_window_and_plan_cli_use_fixed_tick_and_leave_unknown_reconcile(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/discussion_contract.py'
        run = subprocess.run([sys.executable, str(script), 'window', '--scheduled-for', '2026-10-09T22:00:00+08:00'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        window = json.loads(run.stdout)
        data = {'window': window, 'events': [message(1)], 'items': [item()], 'exclusions': {},
                'allowed_projects': ['gitlab.example/team/repo'], 'channel_id': '12345678-1234-1234-1234-123456789abc', 'complete': True, 'pending': True}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / 'input.json'
            snapshot.write_text(json.dumps(data))
            run = subprocess.run([sys.executable, str(script), 'plan', '--input', str(snapshot)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['action'], 'reconcile')
        self.assertEqual(result['updates'], [])
        self.assertFalse(result['can_advance_window'])

    def test_naive_or_wrong_tick_cli_fails_without_plan(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/discussion_contract.py'
        run = subprocess.run([sys.executable, str(script), 'window', '--scheduled-for', '2026-10-09T22:00:00'], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, '')


class InputSafetyTest(unittest.TestCase):
    def test_reader_rejects_symlink_fifo_device_and_parent_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            target = base / 'input.json'; target.write_text('{}')
            link = base / 'link.json'; link.symlink_to(target)
            fifo = base / 'fifo'; os.mkfifo(fifo)
            linked_dir = base / 'dir-link'; linked_dir.symlink_to(base, target_is_directory=True)
            for bad in [link, fifo, Path('/dev/null'), linked_dir/'input.json']:
                with self.subTest(bad=bad), self.assertRaises((ValueError, OSError)):
                    m.read_snapshot(bad)
            self.assertEqual(m.read_snapshot(target), {})

    def test_reader_rejects_oversized_sparse_file_and_read_race(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'input.json'
            with target.open('wb') as stream: stream.truncate(m.MAX_INPUT_BYTES + 1)
            with self.assertRaises(ValueError): m.read_snapshot(target)
            target.write_text('{}')
            original = os.read
            def mutate(fd, count):
                data = original(fd, count)
                target.write_text('{"changed": true}')
                return data
            with patch.object(m.os, 'read', side_effect=mutate), self.assertRaises(ValueError):
                m.read_snapshot(target)

    def test_depth_array_and_duplicate_key_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'input.json'
            for raw in ['[' * 30 + '0' + ']' * 30, '{"a":1,"a":2}', '{"n": NaN}']:
                target.write_text(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError): m.read_snapshot(target)
        kwargs = {'window': m.daily_window('2026-10-09T14:00:00Z'),
                  'events': [message(1)] * (m.MAX_EVENTS + 1), 'items': [], 'exclusions': {},
                  'allowed_projects': ['gitlab.example/team/repo'],
                  'channel_id': message(1)['channel_id'], 'complete': True}
        with self.assertRaises(ValueError): m.plan(**kwargs)

    def test_cli_failure_does_not_reflect_input_field_names(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/discussion_contract.py'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'input.json'
            target.write_text('{"PRIVATE_FIELD_SENTINEL": "private content"}')
            run = subprocess.run([sys.executable, str(script), 'plan', '--input', str(target)], capture_output=True, text=True, timeout=2)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, '')
        self.assertNotIn('PRIVATE_FIELD_SENTINEL', run.stderr)
        self.assertNotIn('private content', run.stderr)

    def test_cli_special_file_never_blocks(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/discussion_contract.py'
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory).resolve() / 'fifo'; os.mkfifo(fifo)
            run = subprocess.run([sys.executable, str(script), 'plan', '--input', str(fifo)], capture_output=True, text=True, timeout=2)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, '')


if __name__ == '__main__': unittest.main()
