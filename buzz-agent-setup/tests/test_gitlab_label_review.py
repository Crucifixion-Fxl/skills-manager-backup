"""Issue #359: stage-review safety contract, independent of model output."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'gitlab_label_review.py'
spec = importlib.util.spec_from_file_location('label_review', PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.policy = dict(project_id=2048, channel_id='existing-channel', scope='brief',
                           request_value='review-ready', policy_revision='policy-3',
                           reviewer_user_id=7, submitter_user_ids=[8])
        self.issue = dict(project_id=2048, iid=22, state='opened', labels=['brief::review-ready', 'type::feature'])
        self.submission = dict(project_id=2048, issue_iid=22, note_id=31, author_id=8,
                               revision='v3', content_digest='a'*64, source_commit='b'*40)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'state.sqlite'
    def request(self):
        return m.build_request(self.policy, self.issue, self.submission)
    def test_happy_and_rejected_are_separate_from_human_approval(self):
        req=self.request()
        with m.Ledger(self.db) as ledger:
            self.assertEqual(ledger.claim(req), 'CLAIMED')
            receipt=dict(note_id=51, author_id=7, task_key=req['task_key'], verdict='changes-requested')
            self.assertEqual(ledger.record_result(req, self.request(), receipt), 'RECORDED')
            self.assertEqual(ledger.claim(req), 'ALREADY_RECORDED')
        with m.Ledger(self.db) as ledger:
            self.assertEqual(ledger.claim(req), 'ALREADY_RECORDED')
    def test_duplicate_and_parallel_connection_are_not_reclaimed(self):
        req=self.request()
        with m.Ledger(self.db) as one, m.Ledger(self.db) as two:
            self.assertEqual(one.claim(req), 'CLAIMED')
            self.assertEqual(two.claim(req), 'RECONCILE_PENDING')
    def test_label_toggle_same_input_deduplicates_new_revision_does_not(self):
        first=self.request()
        self.submission['revision']='v4'
        self.submission['content_digest']='c'*64
        self.assertNotEqual(first['task_key'], self.request()['task_key'])
    def test_reject_wrong_project_and_conflicting_scope(self):
        self.issue['project_id']=99
        with self.assertRaises(m.ReviewError): self.request()
        self.issue['project_id']=2048
        self.issue['labels'].append('brief::approved')
        with self.assertRaises(m.ReviewError): self.request()
    def test_untrusted_body_cannot_supply_submission(self):
        self.submission['author_id']=99
        with self.assertRaises(m.ReviewError): self.request()
        self.submission['author_id']=8
        del self.submission['content_digest']
        with self.assertRaises(m.ReviewError): self.request()
    def test_closed_and_missing_access_do_not_queue(self):
        self.issue['state']='closed'
        with self.assertRaises(m.ReviewError): self.request()
        with self.assertRaises(m.ReviewError): m.build_request(self.policy, None, self.submission)
    def test_independent_reviewer_required(self):
        self.policy['reviewer_user_id']=8
        with self.assertRaises(m.ReviewError): self.request()
    def test_stale_result_cannot_approve_replacement(self):
        req=self.request()
        with m.Ledger(self.db) as ledger:
            ledger.claim(req)
            self.submission['content_digest']='c'*64
            receipt=dict(note_id=51, author_id=7, task_key=req['task_key'], verdict='review-passed')
            self.assertEqual(ledger.record_result(req, self.request(), receipt), 'SUPERSEDED')
    def test_invalid_result_or_wrong_actor_does_not_consume_claim(self):
        req=self.request()
        with m.Ledger(self.db) as ledger:
            ledger.claim(req)
            for actor, verdict in [(8,'review-passed'),(7,'approved'),(7,'print-ready')]:
                with self.assertRaises(m.ReviewError):
                    ledger.record_result(req,req,dict(note_id=51,author_id=actor,task_key=req['task_key'],verdict=verdict))
            self.assertEqual(ledger.claim(req),'RECONCILE_PENDING')
    def test_missing_current_readback_and_terminal_overwrite_are_rejected(self):
        req=self.request()
        receipt=dict(note_id=51,author_id=7,task_key=req['task_key'],verdict='review-passed')
        with m.Ledger(self.db) as ledger:
            ledger.claim(req)
            with self.assertRaises(m.ReviewError): ledger.record_result(req,None,receipt)
            self.assertEqual(ledger.record_result(req,req,receipt),'RECORDED')
            with self.assertRaises(m.ReviewError): ledger.record_result(req,req,{**receipt,'verdict':'changes-requested'})

    def test_unclaimed_result_and_unknown_write_require_reconciliation(self):
        req=self.request()
        with m.Ledger(self.db) as ledger:
            with self.assertRaises(m.ReviewError):
                ledger.record_result(req,req,dict(note_id=51,author_id=7,task_key=req['task_key'],verdict='review-passed'))
            ledger.claim(req)
        with m.Ledger(self.db) as ledger:
            self.assertEqual(ledger.claim(req),'RECONCILE_PENDING')

if __name__=='__main__': unittest.main()
