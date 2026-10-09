"""Installed typed SDK -> original handler fixtures; no live callback claim."""
import hashlib,json,sys,unittest,asyncio,copy,time
from pathlib import Path
from unittest import mock
SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path[:0]=[str(SCRIPTS),str(SCRIPTS/'hostd')]
from hostd import sdk_evidence as se
import test_hostd_sdk_evidence as original_tests
HAS_SDK = original_tests.HAS_SDK
if HAS_SDK:
    import feishu_feed as ff
    from lark_oapi.api.im.v1 import P2ImMessageReactionCreatedV1,P2ImMessageReactionDeletedV1
APP=se.TEST_APPS['hostd-test-a']
ACTOR='on_fixture_sensitive'
def callback(kind='created',operator='user',**changes):
    event={'message_id':'om_target','reaction_type':{'emoji_type':'THUMBSUP'},'operator_type':operator,
        'action_time':'1770000000000'}
    if operator=='user':event['user_id']={'union_id':ACTOR,'open_id':'ou_fixture_sensitive','user_id':'id_fixture_sensitive'}
    else:event['app_id']=APP
    event.update(changes)
    typ='im.message.reaction.'+kind+'_v1'
    doc={'schema':'2.0','header':{'event_type':typ,'event_id':'evt_'+kind+'_'+operator,'app_id':APP},'event':event}
    model=(P2ImMessageReactionCreatedV1 if kind=='created' else P2ImMessageReactionDeletedV1)(doc)
    rows=[]
    with mock.patch.object(ff,'emit',rows.append):
        ff.handler(APP)._do_without_validation(ff.lark.JSON.marshal(model).encode())
    return rows[0]
@unittest.skipUnless(HAS_SDK, "actual installed SDK required; protected declaration controls remain active")
class TypedProducer(unittest.TestCase):
    def test_actual_typed_sdk_four_reaction_callbacks_preserve_fields_without_operator_pii(self):
        for kind in ('created','deleted'):
            for operator in ('user','app'):
                with self.subTest(kind=kind,operator=operator):
                    row=callback(kind,operator)
                    self.assertEqual(row.get('reaction_type'),'THUMBSUP')
                    self.assertEqual(row.get('operator_type'),operator)
                    self.assertEqual(row.get('action_time'),'1770000000000')
                    self.assertEqual(row.get('actor_namespace'),'user-union' if operator=='user' else 'bot-app')
                    actor=ACTOR if operator=='user' else APP
                    self.assertEqual(row.get('actor_id_hash'),se.digest((row['actor_namespace']+'\0'+actor).encode()))
                    self.assertEqual(row['chat_id'],'')
                    if operator=='app':self.assertEqual(row.get('actor_app_id'),APP)
                    for private in (ACTOR,'ou_fixture_sensitive','id_fixture_sensitive'):
                        self.assertNotIn(private,json.dumps(row))
    def test_user_identifier_hashes_use_explicit_namespaces_and_receiver_app_scope(self):
        row=callback()
        for key,namespace,identifier in [('union','user-union',ACTOR),('open','user-open','ou_fixture_sensitive'),('user','user-id','id_fixture_sensitive')]:
            expected=namespace+'\0'+(APP+'\0' if key!='union' else '')+identifier
            self.assertEqual(row.get('operator_'+key+'_id_hash'),se.digest(expected.encode()))

REVISION='00cbc7655faa8589d8e3dc0b3623c09be8fbd42c'
class GrantFixture(original_tests.ConfigFixture):
    def write_private(self,path,doc):
        raw=json.dumps(doc,separators=(',',':')).encode();path.write_bytes(raw);path.chmod(0o600)
        return {'path':str(path),'sha256':se.digest(raw)}
    def declare(self):
        target={'app_id':APP,'message_id':'om_target','root_message_id':'om_root','channel_id':'channel_fixture',
            'original_event_id':'1'*64,'root_event_id':'2'*64,'target_sha256':'3'*64,'native_target_sha256':'4'*64,
            'actor_union_sha256':se.digest(('user-union\0'+ACTOR).encode())}
        self.receipt={'schema_version':1,'run_id':self.data['run_id'],'source_revision':REVISION,
            'chat_id':self.data['target']['chat_id'],'target':copy.deepcopy(target)}
        target['owner_receipt']=self.write_private(self.root/'owner.json',self.receipt)
        self.grant={'schema_version':1,'run_id':self.data['run_id'],'source_revision':REVISION,
            'chat_id':self.data['target']['chat_id'],'targets':[target],
            'authority_pins':[self.write_private(self.root/('authority_'+str(i)+'.json'),{'fixture_boundary':i}) for i in range(5)]}
        self.data['reaction_targets']=self.write_private(self.root/'grant.json',self.grant)
    def repin_grant(self):self.data['reaction_targets']=self.write_private(self.root/'grant.json',self.grant)
class ProtectedGrant(GrantFixture,unittest.TestCase):
    def setUp(self):self.setup_config();self.declare()
    def tearDown(self):self.temp.cleanup()
    def test_exact_private_source_owner_declaration_admitted_without_writing_outputs(self):
        try:cfg=self.config()
        except se.EvidenceError:self.fail("protected original owner target declaration is not supported")
        cfg.revalidate(fresh=True)
        self.assertEqual(cfg.reaction_target(APP,'om_target')['actor_union_sha256'],self.receipt['target']['actor_union_sha256'])
        self.assertIsNone(cfg.reaction_target(APP,'om_unknown'))
        self.assertFalse(cfg.events_path.exists())
    def test_unknown_scope_receipt_or_target_schema_denied(self):
        original=copy.deepcopy(self.grant)
        changes=[{'run_id':'old'},{'chat_id':'oc_foreign'},{'source_revision':'bad'},{'unknown':'secret'},
            {'targets':[]},{'authority_pins':[]},{'targets':[dict(original['targets'][0],app_id=se.TEST_APPS['hostd-test-b'])]},
            {'targets':[dict(original['targets'][0],message_id='om_other')]},{'targets':[original['targets'][0],original['targets'][0]]},
            {'targets':[dict(original['targets'][0],actor_union_sha256='f'*64)]},
            {'targets':[dict(original['targets'][0],owner_receipt={'path':str(self.root/'missing'),'sha256':'a'*64})]}]
        for changeset in changes:
            with self.subTest(changes=list(changeset)):
                self.grant={**original,**changeset};self.repin_grant()
                with self.assertRaises(se.EvidenceError):self.config()
    def test_source_owner_receipt_authority_drift_or_mode_denies(self):
        for name in ('owner.json','authority_0.json','grant.json'):
            with self.subTest(name=name):
                cfg=self.config();p=self.root/name;raw=p.read_bytes();p.write_bytes(raw+b' ')
                with self.assertRaises(se.EvidenceError):cfg.revalidate()
                p.write_bytes(raw);p.chmod(0o644)
                with self.assertRaises(se.EvidenceError):self.config()
                p.chmod(0o600)
@unittest.skipUnless(HAS_SDK, "actual installed SDK required for original reaction producer")
class NativeReaction(GrantFixture,unittest.IsolatedAsyncioTestCase):
    asyncSetUp=original_tests.Metadata.asyncSetUp
    asyncTearDown=original_tests.Metadata.asyncTearDown
    start_producer=original_tests.NativeProducer.start_producer
    receive=original_tests.NativeProducer.receive
    connected=original_tests.NativeProducer.connected
    def payloads(self):
        rows=[]
        for kind in ('created','deleted'):
            for operator in ('app','user'):
                event={'message_id':'om_target','reaction_type':{'emoji_type':'THUMBSUP'},'operator_type':operator,
                    'action_time':str(int(time.time()*1000))}
                if operator=='app':event['app_id']=APP
                else:event['user_id']={'union_id':ACTOR,'open_id':'ou_fixture_sensitive','user_id':'id_fixture_sensitive'}
                rows.append({'schema':'2.0','header':{'event_type':'im.message.reaction.'+kind+'_v1',
                    'event_id':'evt_'+kind+'_'+operator,'app_id':APP},'event':event})
        return rows
    async def produce(self,mutate=None,declared=True):
        if declared:self.declare()
        p=await self.start_producer(reaction_payloads=self.payloads());await self.connected(p)
        # Skip unrelated business message/card. The evidence connection/session
        # and original child remain the actual production protocol.
        await self.receive(p);await self.receive(p)
        events=[]
        for _ in range(4):
            row=await self.receive(p);events.append(copy.deepcopy(row));envelope=row.pop('_sdk_evidence')
            if mutate:mutate(row)
            self.tap.enqueue(APP,p,envelope,row)
        await asyncio.wait_for(self.tap.queue.join(),5)
        return events
    async def test_real_original_child_four_typed_reactions_write_declared_chat_and_private_hashes(self):
        events=await self.produce();self.assertEqual(self.tap.state,'recording')
        rows=[json.loads(x) for x in self.tap.config.events_path.read_text().splitlines()]
        reaction=rows[1:];self.assertEqual(len(reaction),4)
        sys.path.insert(0,str(Path(__file__).parent/'hostd_probes'))
        import check_feishu_events as checker
        for raw,row in zip(events,reaction):
            checker.validate_row(row)
            self.assertEqual(raw['chat_id'],'');self.assertEqual(row['callback_chat_id'],'')
            self.assertEqual(row['chat_id'],'oc_scope');self.assertEqual(row['chat_resolution'],'declared-target')
            self.assertEqual(row['reaction_target_sha256'],self.data['reaction_targets']['sha256'])
            self.assertEqual(row['action_time'],raw['action_time']);self.assertEqual(row['actor_id_hash'],raw['actor_id_hash'])
        for private in (ACTOR,'ou_fixture_sensitive','id_fixture_sensitive'):
            self.assertNotIn(private,self.tap.config.events_path.read_text())
        self.assertFalse(json.loads(self.tap.config.receipt_path.read_text())['live_verified'])
    async def test_without_root_declared_grant_chatless_reactions_never_recorded(self):
        await self.produce(declared=False)
        self.assertEqual(self.tap.rows,1)
    async def test_invalid_actor_time_type_or_field_contradiction_disables_evidence(self):
        await self.produce();original=self.tap.children[next(iter(self.tap.children))];p=self.processes[0]
        # Actual typed original handler output is the sole success input.
        event=callback('created','user');event.update(t=self.now,app=APP)
        envelope=copy.deepcopy(original.session)
        for changes in ({'chat_id':'oc_foreign'},{'actor_id_hash':'f'*64},{'operator_type':None},{'action_time':None},
                {'action_time':str(int((self.now-100)*1000))},{'action_time':str(int((self.now+100)*1000))},
                {'action_time':123},{'_operator_app_id':APP},{'operator_union_id_hash':None},
                {'message_id':'om_unknown'},{'app':se.TEST_APPS['hostd-test-b']}):
            with self.subTest(changes=changes):
                self.tap.state='recording';self.tap.enqueue(APP,p,envelope,{**event,**changes})
                self.assertEqual(self.tap.state,'disabled')

    async def test_checker_denies_broken_callback_provenance_or_identifier_namespace(self):
        await self.produce()
        sys.path.insert(0,str(Path(__file__).parent/'hostd_probes'))
        import check_feishu_events as checker
        row=next(json.loads(x) for x in self.tap.config.events_path.read_text().splitlines()
            if json.loads(x).get('operator_type')=='user')
        for changed in ({'callback_chat_id':'oc_foreign'},{'reaction_target_sha256':'bad'},
                {'operator_open_id_namespace':'user-open:cli_foreign'},{'operator_union_id_hash':'f'*64},
                {'action_time':'1.5'}):
            with self.subTest(fields=list(changed)),self.assertRaises(checker.EvidenceError):
                checker.validate_row({**row,**changed})

    async def test_actual_typed_sdk_missing_or_contradictory_operator_time_fields_failclosed(self):
        self.declare();good=self.payloads()[1];bad=[]
        for field in ('operator_type','action_time','user_id'):
            doc=copy.deepcopy(good);del doc['event'][field];bad.append(doc)
        for changes in ({'user_id':{'open_id':'ou_fixture_sensitive'}},{'app_id':APP},
                {'action_time':str(int((self.now-100)*1000))},{'action_time':str(int((self.now+100)*1000))},
                {'action_time':'123.5'},{'action_time':None}):
            doc=copy.deepcopy(good);doc['event'].update(changes);bad.append(doc)
        bot=self.payloads()[0]
        for changes in ({'app_id':None},{'app_id':se.TEST_APPS['hostd-test-b']},
                {'user_id':{'union_id':ACTOR}}):
            doc=copy.deepcopy(bot);doc['event'].update(changes);bad.append(doc)
        p=await self.start_producer(reaction_payloads=bad);await self.connected(p)
        await self.receive(p);await self.receive(p);await self.tap.queue.join()
        for index in range(len(bad)):
            with self.subTest(index=index):
                row=await self.receive(p);envelope=row.pop('_sdk_evidence');self.tap.state='recording'
                self.tap.enqueue(APP,p,envelope,row);self.assertEqual(self.tap.state,'disabled')
        self.assertEqual(self.tap.rows,1)
    async def test_typed_sdk_receiver_header_app_conflict_never_gets_original_wire(self):
        self.declare();doc=self.payloads()[0];doc['header']['app_id']=se.TEST_APPS['hostd-test-b']
        p=await self.start_producer(reaction_payloads=[doc]);await self.connected(p)
        await self.receive(p);await self.receive(p);row=await self.receive(p)
        self.assertNotIn('_sdk_evidence',row)
        self.tap.enqueue(APP,p,None,row);self.assertEqual(self.tap.state,'disabled')
