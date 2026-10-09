"""Preserve the observed manual-human-membership policy through hostd only."""
import copy
import json
from pathlib import Path
import sys
from unittest import mock

import test_buzz_feishu_group_sync as base
import test_hostd_bot_clients as bot
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd import config

class ConfigCompatibility(base.TmpCase):
    assembly=bot.RoundAssembly.assembly
    def test_supported_field_retained_and_input_unchanged(self):
        env=base.Env(self.tmp)
        cfg=json.loads(env.config.read_text());cfg['human_membership_sync']='feishu_to_buzz'
        before=copy.deepcopy(cfg)
        validated=config.validate_config(cfg)
        self.assertEqual(validated['human_membership_sync'],'feishu_to_buzz')
        self.assertEqual(cfg,before)
        base.write_owner_only(env.config,json.dumps(cfg))
        self.assertEqual(config.load_config(env.config),validated)
    def test_invalid_values_and_unknown_keys_fail_with_remedy(self):
        env=base.Env(self.tmp);cfg=json.loads(env.config.read_text())
        for value in [None,True,[],{},'two_way','buzz_to_feishu','']:
            with self.subTest(value=value),self.assertRaises(base.FGS.GroupSyncError) as found:
                config.validate_config(dict(cfg,human_membership_sync=value))
            self.assertIn('怎么解决',str(found.exception));self.assertIn('复制给 AI',str(found.exception))
        with self.assertRaises(base.FGS.GroupSyncError):config.validate_config(dict(cfg,unknown_secret='DO_NOT_OUTPUT'))
    def test_human_writes_suppressed_bots_and_inbound_delta_retained(self):
        _,_,world,run=self.assembly()
        run.cfg['human_membership_sync']='feishu_to_buzz'
        run.verify_identities();run.load_people();run.verify_desk()
        run.state.members_synced=123
        # Exercise the real write loop with both directions of bot/human operations.
        plan=base.FGS.MembershipPlan(add_users=('on_add',),remove_users=('on_remove',),add_bots=('cli_add',),remove_bots=('cli_remove',))
        run.bot_members['cli_remove']='ou_remove'
        world.bots['cli_remove']='ou_remove'
        listing=base.FGS.MemberListing(users=frozenset({base.union_of(base.OWNER_OPEN),'on_remove'}),bots=dict(world.bots),complete=True)
        delta=base.FGS.MemberDelta(remove_users={'on_remove'})
        calls=[]
        def write(method,chat,ids,id_type):calls.append((method,tuple(ids),id_type));return 0
        with mock.patch.object(run.clients.owner,'member_listing',return_value=listing),mock.patch.object(run.clients.owner,'change_members',side_effect=write),mock.patch.object(base.FGS,'plan_membership',return_value=plan),mock.patch.object(run,'sync_members_both_ways',return_value=delta) as inbound,mock.patch.object(run,'_record_buzz_side') as record:
            run.reconcile_members()
        self.assertEqual(calls,[('DELETE',('cli_remove',),'app_id'),('POST',('cli_add',),'app_id')])
        inbound.assert_called_once_with(listing);record.assert_called_once_with(delta)
        self.assertEqual(run.report['added_users'],0);self.assertEqual(run.report['removed_users'],0)
        self.assertEqual(run.report['added_bots'],1);self.assertEqual(run.report['removed_bots'],1)
    def test_default_plan_hook_preserves_membership_operations(self):
        plan=base.FGS.MembershipPlan(add_users=('on_add',),remove_users=('on_remove',))
        legacy=object.__new__(base.FGS.Round)
        self.assertIs(legacy._membership_plan(plan),plan)
        _,_,_,run=self.assembly()
        self.assertEqual(run._membership_plan(plan),plan)
    def test_policy_runtime_copy_retains_protected_bot_and_union(self):
        _,cfg,_,_=self.assembly()
        cfg['human_membership_sync']='feishu_to_buzz'
        runtime=bot.br.runtime_config(config.validate_config(cfg))
        self.assertEqual(runtime['human_membership_sync'],'feishu_to_buzz')
        self.assertEqual(runtime['owner_app_id'],base.AGENT_APP)
        self.assertEqual(runtime['identity'],'union_id');self.assertNotIn('owner_open_id',runtime)

    def test_real_inbound_human_departure_still_removes_buzz_member(self):
        _,_,world,run=self.assembly()
        run.cfg['human_membership_sync']='feishu_to_buzz'
        run.verify_identities();run.load_people();run.load_directory();run.verify_desk()
        run.state.members_synced=123
        bob='u:'+base.union_of(base.BOB_OPEN)
        run.state.feishu_seen={bob:''}
        run.state.buzz_seen={pk:'' for pk in run.roles}
        run.state.buzz_seen[base.BOB_PK]=bob
        run.reconcile_members()
        self.assertEqual(run.report['members_removed_from_buzz'],1)
        self.assertNotIn(base.BOB_PK,run.roles)
        self.assertFalse(any(('POST' in argv or 'DELETE' in argv) and ('union_id' in argv or 'open_id' in argv) for argv,_ in world.bot_calls))
        self.assertEqual(run.report['added_users'],0);self.assertEqual(run.report['removed_users'],0)
