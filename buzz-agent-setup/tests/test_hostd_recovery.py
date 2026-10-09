"""Retry scheduling derives from metadata and explicit partial reads, never counts."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd.recovery import plan_recovery
import buzz_feishu_group_sync as gs

class RecoveryTests(unittest.TestCase):
    def test_empty_and_terminal_do_not_schedule(self):
        state=gs.State(b2f={'a':'unknown','b':'failed','c':'skipped','d':'om_ack'},f2b={'om_a':'removed'})
        self.assertEqual(plan_recovery(state,now=100).phases,frozenset())
        self.assertIsNone(plan_recovery(state,now=100).next_retry_at)
    def test_pending_and_retry_select_actual_phase(self):
        state=gs.State(b2f={'a':'pending:100:om_parent,card'},e2f={'b':'retry:100'},f2r={'om_a|on_u|Typing':'retry:100'},agent_intros={'c':'pending:100'})
        plan=plan_recovery(state,now=101)
        self.assertEqual(plan.phases,frozenset({'buzz','feishu','members'}))
        self.assertEqual(plan.next_retry_at,102)
    def test_expired_ambiguous_and_unstamped_pending_never_schedule(self):
        state=gs.State(b2f={'a':'pending:1','b':'pending'},f2b={'om_a':'pending:1'},f2r={'om_a|on_u|Typing':'pending:1'})
        self.assertFalse(plan_recovery(state,now=4000).phases)
    def test_partial_reads_and_pending_targets_schedule_without_send_metadata(self):
        plan=plan_recovery(gs.State(),now=100,partial_reads=('buzz','members'),pending_targets=True)
        self.assertEqual(plan.phases,frozenset({'buzz','members','feishu'}))
        self.assertTrue(all('BODY' not in reason for reason in plan.reasons))
    def test_member_signed_event_window_and_persistent_refusal(self):
        state=gs.State(member_events={'a':{'created_at':100},'b':{'created_at':100},'c':{'created_at':1}},member_event_blocks={'b':'retry_refused'})
        self.assertEqual(plan_recovery(state,now=200).phases,frozenset({'members'}))
        self.assertFalse(plan_recovery(state,now=1000).phases)
    def test_bounded_backoff_and_input_validation(self):
        state=gs.State(b2f={'a':'retry:100'})
        self.assertEqual(plan_recovery(state,now=101,attempt=99).next_retry_at,161)
        for kwargs in [{'now':True},{'now':-1},{'now':100,'attempt':-1},{'now':100,'partial_reads':['bad']},{'now':100,'pending_targets':1}]:
            with self.assertRaises(ValueError):plan_recovery(state,**kwargs)
    def test_state_immutable_and_no_metadata_values_in_plan(self):
        state=gs.State(b2f={'secret-source-id':'retry:100'})
        before=dict(state.b2f);plan=plan_recovery(state,now=101)
        self.assertEqual(state.b2f,before);self.assertNotIn('secret-source-id',repr(plan))
