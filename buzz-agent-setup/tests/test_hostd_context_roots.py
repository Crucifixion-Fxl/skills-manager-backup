"""Explicit context roots retain Desk provenance before approved own replies."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import test_hostd_outlet as own

base, mapped = own.base, own.mapped
gs = base.FGS
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule
CONTEXT_KEY = '78' * 32
CONTEXT_PUB = gs._signer_pubkey(CONTEXT_KEY)


class ContextRoots(base.TmpCase):
    assembly = own.OwnOutlet.assembly
    event = own.OwnOutlet.event

    def configured(self):
        world, run, adapter = self.assembly()
        run.own_outlets_active = True
        run.cfg['buzz_unmapped_senders'] = 'context'
        original = world._message_item
        def actual_thread_card(mid, id_type):
            item = original(mid, id_type)
            for root, rows in world.threads.items():
                source = next((row for row in rows if row['message_id'] == mid), None)
                if item is not None and source is not None:
                    item = dict(item, root_id=root, parent_id=root)
                    if source['msg_type'] == 'interactive':
                        item['body'] = {'content': source['content']}
            return item
        world._message_item = actual_thread_card
        return world, run, adapter

    def root(self, index=0):
        # Real incident shape: four signed nonmember roots, same channel,
        # owner + investigator mentions, no invented native mapping tags.
        return self.event(key=CONTEXT_KEY, created=int(base.NOW.timestamp())-20+index,
            content='context root '+str(index), tags=[['p', base.OWNER_PK], ['p', base.AGENT2_PK]])

    def test_four_signed_context_roots_release_five_own_replies_without_duplicates(self):
        world, run, adapter = self.configured()
        roots = [self.root(i) for i in range(4)]
        replies = [self.event(content='reply '+str(i), tags=[['e', roots[n]['id'], '', 'root'],
                    ['e', roots[n]['id'], '', 'reply']]) for i,n in enumerate((0,1,0,2,3))]
        world.events = roots + replies
        world.relay_events.extend(roots + replies)
        run.buzz_to_feishu()
        self.assertEqual(run.report['to_feishu'], 4)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]), 4)
        for reply in replies:
            self.assertTrue(adapter.deliver(reply).startswith('om_'))
        calls = [a for a,_ in world.bot_calls if '--content' in a]
        self.assertEqual(len(calls), 9)
        self.assertEqual([a[a.index('--profile')+1] for a in calls[4:]], [own.APP]*5)
        for root in roots:
            mid=run.state.b2f[root['id']]
            proof=run.resolve_feishu(mid)
            self.assertEqual(proof.event_id,root['id'])
            self.assertEqual(proof.sender_app_id,run.desk_app_id)
        run.buzz_to_feishu()
        for reply in replies:adapter.deliver(reply)
        self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),9)
        self.assertNotIn(CONTEXT_PUB,run.humans())
        self.assertIsNone(run.mapping_store.conn.execute('SELECT 1 FROM agent WHERE pubkey=?',(CONTEXT_PUB,)).fetchone())

        run.persist()
        with mapped.Store(self.tmp / 'hostd.db') as reopened:
            storage = mapped.StateAdapter(reopened, 'test', self.tmp / 'state')
            rebuilt = mapped.dm.MappedHostdRound(copy.deepcopy(run.cfg), run.clients, storage.load(),
                gs._new_report(), base.NOW, lambda: storage.save(rebuilt.state),
                store=reopened, binding_id='test', reader_namespace=base.AGENT_APP, auth_clock=lambda: base.NOW)
            rebuilt.verify_identities(); rebuilt.load_people(); rebuilt.load_directory(); rebuilt.verify_desk()
            rebuilt.own_outlets_active=True
            again = own.outlet.AgentOutlet(rebuilt, base.AGENT2_PK, adapter.env_file,
                bot_client=adapter.client, trusted_relays={'https://relay.test'},
                http=world.http_get, clock=lambda: base.NOW)
            rebuilt.buzz_to_feishu()
            for reply in replies:again.deliver(reply)
            self.assertEqual(len([a for a,_ in world.bot_calls if '--content' in a]),9)

    def test_context_is_opt_in_and_remains_labelled_not_human(self):
        world, run, _ = self.configured()
        root=self.root();world.events=[root]
        for value in (None, 'skip'):
            if value is None:run.cfg.pop('buzz_unmapped_senders',None)
            else:run.cfg['buzz_unmapped_senders']=value
            run.buzz_to_feishu()
            self.assertEqual(run.report['to_feishu'],0)
        run.cfg['buzz_unmapped_senders']='context'
        run.state.buzz_since=0
        run.buzz_to_feishu()
        self.assertEqual(run.report['to_feishu'],1)
        raw=next(a[a.index('--content')+1] for a,_ in world.bot_calls if '--content' in a)
        self.assertIn(gs.BUZZ_CONTEXT_LABEL,raw)
        self.assertNotIn(CONTEXT_PUB,run.humans())
        self.assertNotIn(CONTEXT_PUB,run.roles)

    def test_known_bots_and_mirrors_never_fall_back_to_context(self):
        _, run, _ = self.configured()
        root=self.root()
        checks=[(run.roles,'bot'),(run.cfg['agents'],{'app_id':'cli_other'}),
                (run.directory,'cli_other')]
        for mapping,value in checks:
            mapping[CONTEXT_PUB]=value
            self.assertFalse(run._route_buzz_source(root))
            mapping.pop(CONTEXT_PUB)
        run.other_mirrors.add(CONTEXT_PUB)
        self.assertFalse(run._route_buzz_source(root));run.other_mirrors.remove(CONTEXT_PUB)
        with run.mapping_store.transaction():
            run.mapping_store.register_agent(CONTEXT_PUB,owner_pubkey=base.OWNER_PK,app_id='cli_other',now=run.now_ts)
        self.assertFalse(run._route_buzz_source(root))
        self.assertFalse(run._route_buzz_source(self.event(key=base.MIRROR_KEY)))
        self.assertFalse(run._route_buzz_source(self.event()))

    def test_context_requires_real_signature_unique_channel_and_message_kind(self):
        _, run, _ = self.configured()
        root=self.root()
        invalid=[dict(root,content='changed'),dict(root,sig='00'*64),
            gs.sign_event(CONTEXT_KEY,9,[['h','00000000-0000-0000-0000-000000000001']],'wrong',run.now_ts),
            gs.sign_event(CONTEXT_KEY,9,[['h',base.CHANNEL],['h',base.CHANNEL]],'duplicate',run.now_ts),
            gs.sign_event(CONTEXT_KEY,7,[['h',base.CHANNEL]],'+',run.now_ts),
            dict(root,kind=True)]
        for event in invalid:
            with self.subTest(event=event['id']):self.assertFalse(run._route_buzz_source(event))

    def test_context_never_restores_human_mentions_or_unapproved_bot_mentions(self):
        world, run, adapter = self.configured()
        adapter.verify()  # Actual protected fixture grant and own membership proof.
        run.bot_admission = lambda pk,app: (pk == base.AGENT2_PK and app == own.APP
            and adapter._local_authorized() and adapter.has_local_grant())
        root=self.root();world.events=[root]
        run.buzz_to_feishu()
        raw=next(a[a.index('--content')+1] for a,_ in world.bot_calls if '--content' in a)
        self.assertIn('<at id='+own.MEMBER+'>',raw)
        self.assertNotIn('<at email=',raw)
        self.assertNotIn('<at user_id=',raw)
        run.bot_admission=lambda pk,app:False
        another=self.root(1);world.events.append(another)
        run.buzz_to_feishu()
        raw=[a[a.index('--content')+1] for a,_ in world.bot_calls if '--content' in a][-1]
        self.assertNotIn('<at ',raw)

    def test_unknown_source_stays_sealed_without_native_send(self):
        world, run, _ = self.configured()
        root=self.root();world.events=[root]
        run.state.b2f[root['id']]=gs.UNKNOWN
        run.state.unresolved[root['id']]=root['created_at']
        run.buzz_to_feishu()
        self.assertEqual(run.state.b2f[root['id']],gs.UNKNOWN)
        self.assertEqual([a for a,_ in world.bot_calls if '--content' in a],[])

    def test_mapping_context_is_explicit_and_exact_sender_chat_receipt_still_required(self):
        world, run, _ = self.configured()
        root=self.root();world.events=[root];world.relay_events.append(root)
        message=mapped.bot_message(root,mid='om_context_root')
        args=(root,base.CHANNEL,base.CHAT,base.API_ORIGIN,set(),{},run.desk_app_id)
        self.assertIsNone(mapped.dm.feishu_mapping(message,*args))
        self.assertIsNotNone(mapped.dm.feishu_mapping(message,*args,context_authors={CONTEXT_PUB}))
        for changes in ({'chat_id':'oc_foreign'},{'deleted':True},
                {'sender':{'sender_type':'app','id_type':'app_id','id':own.APP}},
                {'root_id':'om_unproven_parent'}):
            self.assertIsNone(mapped.dm.feishu_mapping(dict(message,**changes),*args,context_authors={CONTEXT_PUB}))
        world.messages=[message]
        self.assertIsNotNone(run.resolve_feishu('om_context_root'))
        run.cfg['buzz_unmapped_senders']='skip'
        self.assertIsNone(run.resolve_feishu('om_context_root'))

    def test_incomplete_native_history_does_not_authorize_context_root_send(self):
        world, run, _ = self.configured();world.events=[self.root()]
        with mock.patch.object(run.clients.owner,'messages',return_value=([],True)):
            with self.assertRaises(gs.GroupSyncError):run.buzz_to_feishu()
        self.assertEqual([a for a,_ in world.bot_calls if '--content' in a],[])

    def test_context_revoked_after_source_filter_cannot_use_generic_card_fallback(self):
        world, run, _ = self.configured();root=self.root()
        self.assertTrue(run._route_buzz_source(root))
        run.cfg['buzz_unmapped_senders']='skip'
        with self.assertRaises(gs.GroupSyncError):
            run._prepare_outbound(root,gs.Outbound(root['id'],None,'body',None))
        self.assertEqual([a for a,_ in world.bot_calls if '--content' in a],[])

    def test_workflow_tag_does_not_hide_nonmember_context_label(self):
        world,run,_=self.configured()
        root=self.event(key=CONTEXT_KEY,tags=[['buzz:workflow','true']],content='workflow context')
        world.events=[root];run.buzz_to_feishu()
        raw=next(a[a.index('--content')+1] for a,_ in world.bot_calls if '--content' in a)
        self.assertIn(gs.BUZZ_CONTEXT_LABEL,raw)
        self.assertIn(gs.BUZZ_WORKFLOW_LABEL,raw)

    def test_workflow_text_payload_has_context_and_actual_card_refusal_cannot_send_text(self):
        world,run,_=self.configured()
        root=self.event(key=CONTEXT_KEY,tags=[['buzz:workflow','true']],content='workflow context')
        prepared=run._prepare_outbound(root,gs.Outbound(root['id'],None,'old workflow label',None))
        self.assertIn(gs.BUZZ_CONTEXT_LABEL,prepared.text)
        self.assertIn(gs.BUZZ_WORKFLOW_LABEL,prepared.text)
        world.events=[root];world.card_reject=['content']
        # MappingWorld's Card1 success shim normally skips the provider's
        # explicit card rejection queue; use that real error envelope here.
        world._card_refusal=lambda args:base.FakeWorld._card_refusal(world,args)
        run.buzz_to_feishu()
        sends=[a for a,_ in world.bot_calls if '--content' in a]
        self.assertEqual(len(sends),1)
        self.assertEqual(sends[0][sends[0].index('--msg-type')+1],'interactive')
        self.assertEqual(run.report['to_feishu'],0)
        self.assertEqual(run.report['cards_fallback_text'],0)


if __name__ == '__main__': unittest.main()
