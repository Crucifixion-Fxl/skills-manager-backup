"""Implementation wire contract v1: real synthetic signed proofs, no IO/grants."""
from dataclasses import FrozenInstanceError,replace
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import buzz_feishu_group_sync as gs
try:
    from hostd import remote_approval as module
except ImportError:
    module=None

KEYS=[str(i).zfill(64) for i in range(1,7)]
AGENT,OWNER,MIRROR,MOWNER,PIN,OTHER=[gs._signer_pubkey(k) for k in KEYS]
CHANNEL='00000000-0000-0000-0000-000000000001'
REF=gs.chat_ref('oc_synthetic')
NOW=10000


def auth(key,owner_key,condition=''):
    pub=gs._signer_pubkey(key);owner=gs._signer_pubkey(owner_key)
    sig=gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{pub}:{condition}'.encode()).digest(),bytes.fromhex(owner_key),bytes(32)).hex()
    return ['auth',owner,condition,sig]


def canonical(body):return json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)


class RemoteApproval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body=dict(version=1,decision='approve',agent_pubkey=AGENT,agent_owner_pubkey=OWNER,app_id='cli_agent',
            channel_id=CHANNEL,chat_ref=REF,mirror_pubkey=MIRROR,mirror_owner_pubkey=MOWNER,claimed_at=8000,
            request_id='JOIN-1234abcd',request_created_at=9000,request_deadline=9000+7*86400,
            card_generation=1,card_message_sha256=hashlib.sha256(b'om_synthetic').hexdigest(),
            decision_event_sha256=hashlib.sha256(b'evt_synthetic').hexdigest(),decision_at=9200)
        cls.agent_profile=gs.sign_event(KEYS[0],0,[auth(KEYS[0],KEYS[1])],'{}',NOW)
        cls.agent_policy=gs.sign_event(KEYS[1],30177,[['d',AGENT]],canonical({'feishu':{'app_id':'cli_agent'}}),NOW)
        cls.mirror_profile=gs.sign_event(KEYS[2],0,[auth(KEYS[2],KEYS[3])],'{}',NOW)
        cls.claim=dict(channel=CHANNEL,chat_ref=REF,claimed_at=8000,heartbeat=NOW,sync_app={'version':1,'app_id':'cli_sync'})
        cls.mirror_policy=gs.sign_event(KEYS[3],30177,[['d',MIRROR]],canonical({'feishu':{'mirror':True,'bindings':[cls.claim]}}),NOW)
        cls.roster=gs.sign_event(KEYS[4],39002,[['d',CHANNEL],['p',AGENT,'','bot'],['p',OWNER,'','member'],
            ['p',MIRROR,'','bot'],['p',MOWNER,'','owner']],'',NOW)

    def m(self):
        self.assertIsNotNone(module,'missing pure remote approval parser is the genuine RED')
        return module

    def event(self,body=None,*,content=None,tags=None,key=KEYS[2],stamp=NOW,oa=False):
        data=self.body if body is None else body;raw=canonical(data) if content is None else content
        digest=hashlib.sha256(raw.encode()).hexdigest()
        tags=tags if tags is not None else [['t','hostd-card-approval-v1'],['h',data['channel_id']],
            ['p',data['agent_pubkey']],['d','hostd-card-approval-v1:'+digest]]
        if oa:tags=tags+[auth(KEYS[2],KEYS[3])]
        return gs.sign_event(key,30078,tags,raw,stamp)

    def context(self,*,now=NOW,claim=None,**overrides):
        m=self.m();scope=m.ApprovalScope(**{k:self.body[k] for k in ('agent_pubkey','agent_owner_pubkey','app_id',
                        'channel_id','chat_ref','mirror_pubkey','mirror_owner_pubkey','claimed_at')})
        policy=self.mirror_policy
        if claim is not None or now!=NOW:
            current=dict(self.claim,heartbeat=now) if claim is None else claim
            policy=gs.sign_event(KEYS[3],30177,[['d',MIRROR]],canonical({'feishu':{'mirror':True,'bindings':[current]}}),now)
        roster=self.roster if now==NOW else gs.sign_event(KEYS[4],39002,self.roster['tags'],'',now)
        params=dict(scope=scope,relay_pubkey=PIN,agent_profile=self.agent_profile,agent_policy=self.agent_policy,
            mirror_profiles=(self.mirror_profile,),mirror_policies=(policy,),roster=roster,now=now,complete=True)
        params.update(overrides);return m.ProofContext(**params)

    def pending(self,call):
        m=self.m()
        with self.assertRaises(m.ApprovalPending) as exc:call()
        self.assertEqual(exc.exception.status,'pending');self.assertIn('怎么解决',str(exc.exception))
        self.assertIn('复制给 AI',str(exc.exception));self.assertNotIn('private-body',str(exc.exception))

    def test_omitted_upstream_completeness_assertion_stays_pending(self):
        m=self.m();explicit=self.context()
        omitted=m.ProofContext(**{name:getattr(explicit,name) for name in m.ProofContext.__dataclass_fields__ if name!='complete'})
        for operation in (lambda:m.verify(self.event(),omitted),lambda:m.sync_app(omitted)):
            with self.subTest(operation=operation.__code__.co_firstlineno):self.pending(operation)

    def test_actual_mirror_signed_record_verified_immutable_metadata_only(self):
        m=self.m();event=self.event();proof=m.verify(event,self.context())
        self.assertEqual(proof.record_id,event['id']);self.assertEqual(proof.content_hash,hashlib.sha256(event['content'].encode()).hexdigest())
        self.assertEqual(proof.scope,self.context().scope);self.assertEqual(proof.request_id,self.body['request_id'])
        self.assertFalse(hasattr(proof,'content'));self.assertFalse(hasattr(proof,'body'))
        with self.assertRaises(FrozenInstanceError):proof.record_id='0'*64

    def test_optional_valid_empty_oa_and_separate_decode(self):
        m=self.m();event=self.event(oa=True);decoded=m.decode(event,now=NOW)
        self.assertEqual(decoded.record_id,event['id']);self.assertEqual(m.verify(event,self.context()).record_id,event['id'])
        self.assertNotIsInstance(decoded,m.ApprovalProof)

    def test_forged_signature_and_foreign_signer_pending(self):
        m=self.m();bad=self.event();bad['sig']='0'*128
        for event in (bad,self.event(key=KEYS[5])):self.pending(lambda:m.verify(event,self.context()))

    def test_json_duplicate_extra_missing_and_noncanonical_pending(self):
        m=self.m()
        variants=[canonical(self.body)[:-1]+',"version":1}',canonical(dict(self.body,extra='private-body')),
                  canonical({k:v for k,v in self.body.items() if k!='card_generation'}),json.dumps(self.body,sort_keys=True)]
        for raw in variants:self.pending(lambda:m.decode(self.event(content=raw),now=NOW))

    def test_invalid_version_app_hash_generation_and_bool_fields_pending(self):
        m=self.m()
        for field,value in (('version',True),('version',2),('app_id','wrong'),('card_message_sha256',''),
                ('decision_event_sha256','F'*64),('card_generation',0),('card_generation',True),('decision_at',True),('request_id','JOIN-BAD')):
            with self.subTest(field=field):self.pending(lambda:m.decode(self.event(dict(self.body,**{field:value})),now=NOW))

    def test_wrong_tag_binding_duplicate_extra_and_bad_optional_oa_pending(self):
        m=self.m();base=self.event()['tags']
        variants=[base+[['h',CHANNEL]],base+[['unknown','value']],base[:-1]+[['d','hostd-card-approval-v1:'+'0'*64]],
                  [['t','hostd-card-approval-v1'],['h',CHANNEL],['p',OTHER],base[-1]],base+[auth(KEYS[2],KEYS[3],'kind=9')]]
        for tags in variants:self.pending(lambda:m.decode(self.event(tags=tags),now=NOW))

    def test_decision_deadline_claim_and_publish_time_constraints(self):
        m=self.m()
        for change in ({'request_deadline':self.body['request_deadline']+1},{'decision_at':8999},
                {'decision_at':self.body['request_deadline']+1},{'claimed_at':9300},{'request_created_at':-1},{'decision_at':2**63}):
            self.pending(lambda:m.decode(self.event(dict(self.body,**change)),now=NOW))
        for stamp in (9199,NOW+gs.RELAY_CLOCK_SKEW_SECONDS+1):self.pending(lambda:m.decode(self.event(stamp=stamp),now=NOW))

    def test_old_approved_does_not_expire_when_request_deadline_passed(self):
        m=self.m();proof=m.verify(self.event(),self.context(now=self.body['request_deadline']+100))
        self.assertEqual(proof.record_id,self.event()['id'])

    def test_heartbeat_new_event_id_keeps_stable_claim_approval(self):
        m=self.m();context=self.context(claim=dict(self.claim,heartbeat=NOW+1),now=NOW+1)
        self.assertNotEqual(context.mirror_policies[0]['id'],self.mirror_policy['id'])
        self.assertEqual(m.verify(self.event(),context).scope.claimed_at,8000)

    def test_scope_crossrefs_and_reclaimed_identity_rejected(self):
        m=self.m();context=self.context()
        for field,value in (('app_id','cli_other'),('agent_owner_pubkey',MOWNER),('chat_ref','f'*64),
                ('mirror_owner_pubkey',OWNER),('mirror_pubkey',OTHER),('claimed_at',8001)):
            self.pending(lambda:m.verify(self.event(),replace(context,scope=replace(context.scope,**{field:value}))))
        self.pending(lambda:m.verify(self.event(),self.context(claim=dict(self.claim,claimed_at=8001))))

    def test_actual_signed_profiles_conditional_oa_and_mixed_owner_policy_rejected(self):
        m=self.m()
        bad_agent=gs.sign_event(KEYS[0],0,[auth(KEYS[0],KEYS[1],'kind=9')],'{}',NOW)
        bad_mirror=gs.sign_event(KEYS[2],0,[auth(KEYS[2],KEYS[3],'kind=9')],'{}',NOW)
        foreign=gs.sign_event(KEYS[1],30177,self.mirror_policy['tags'],self.mirror_policy['content'],NOW)
        for override in ({'agent_profile':bad_agent},{'mirror_profiles':(bad_mirror,)},{'mirror_policies':(foreign,)}):
            self.pending(lambda:m.verify(self.event(),self.context(**override)))

    def test_actual_relay_roster_signature_current_roles_and_completeness_required(self):
        m=self.m();bad=copy.deepcopy(self.roster);bad['sig']='0'*128
        unpinned=gs.sign_event(KEYS[5],39002,self.roster['tags'],'',NOW)
        missing=gs.sign_event(KEYS[4],39002,[t for t in self.roster['tags'] if t[:2]!=['p',MIRROR]],'',NOW)
        future=gs.sign_event(KEYS[4],39002,self.roster['tags'],'',NOW+gs.RELAY_CLOCK_SKEW_SECONDS+1)
        for roster in (bad,unpinned,missing,future):self.pending(lambda:m.verify(self.event(),self.context(roster=roster)))
        self.pending(lambda:m.verify(self.event(),self.context(complete=False)))

    def test_lease_expired_future_claim_and_malformed_policy_pending(self):
        m=self.m()
        for claim in (dict(self.claim,heartbeat=1),dict(self.claim,heartbeat=NOW+10000),dict(self.claim,chat_ref='bad')):
            self.pending(lambda:m.verify(self.event(),self.context(claim=claim)))
        malformed=gs.sign_event(KEYS[3],30177,[['d',MIRROR]],'{"feishu":{"mirror":true,"bindings":[]},"feishu":{}}',NOW)
        self.pending(lambda:m.verify(self.event(),self.context(mirror_policies=(malformed,))))

    def test_losing_claim_cannot_authorize_even_valid_mirror_record(self):
        m=self.m();other_profile=gs.sign_event(KEYS[5],0,[auth(KEYS[5],KEYS[3])],'{}',NOW)
        other_claim=dict(self.claim,claimed_at=7000)
        other_policy=gs.sign_event(KEYS[3],30177,[['d',OTHER]],canonical({'feishu':{'mirror':True,'bindings':[other_claim]}}),NOW)
        tags=self.roster['tags']+[['p',OTHER,'','bot']]
        roster=gs.sign_event(KEYS[4],39002,tags,'',NOW)
        context=self.context(mirror_profiles=(self.mirror_profile,other_profile),mirror_policies=(self.mirror_policy,other_policy),roster=roster)
        self.pending(lambda:m.verify(self.event(),context))

    def test_newer_losing_claim_does_not_override_valid_earliest_claim(self):
        m=self.m();other_profile=gs.sign_event(KEYS[5],0,[auth(KEYS[5],KEYS[3])],'{}',NOW)
        other_claim=dict(self.claim,claimed_at=8500)
        other_policy=gs.sign_event(KEYS[3],30177,[['d',OTHER]],canonical({'feishu':{'mirror':True,'bindings':[other_claim]}}),NOW)
        roster=gs.sign_event(KEYS[4],39002,self.roster['tags']+[['p',OTHER,'','bot']],'',NOW)
        context=self.context(mirror_profiles=(self.mirror_profile,other_profile),mirror_policies=(other_policy,self.mirror_policy),roster=roster)
        self.assertEqual(m.verify(self.event(),context).record_id,self.event()['id'])

    def test_same_chat_other_channel_earlier_claim_holds_approval(self):
        m=self.m();other_profile=gs.sign_event(KEYS[5],0,[auth(KEYS[5],KEYS[3])],'{}',NOW)
        claim=dict(self.claim,claimed_at=7000,channel='00000000-0000-0000-0000-000000000002')
        other_policy=gs.sign_event(KEYS[3],30177,[['d',OTHER]],canonical({'feishu':{'mirror':True,'bindings':[claim]}}),NOW)
        context=self.context(mirror_profiles=(self.mirror_profile,other_profile),mirror_policies=(self.mirror_policy,other_policy))
        self.pending(lambda:m.verify(self.event(),context))

    def test_latest_owner_policy_revocation_holds_old_approval(self):
        m=self.m();revoked=gs.sign_event(KEYS[3],30177,[['d',MIRROR]],canonical({'feishu':{'mirror':False,'bindings':[]}}),NOW+1)
        context=self.context(now=NOW+1,mirror_policies=(self.mirror_policy,revoked))
        self.pending(lambda:m.verify(self.event(),context))

    def test_sync_app_from_exact_signed_winning_binding_not_feishu_app_or_roster_guess(self):
        m=self.m();proof=m.sync_app(self.context());self.assertEqual(proof.app_id,'cli_sync')
        self.assertEqual(proof.claimed_at,8000);self.assertEqual(proof.mirror_pubkey,MIRROR)
        with self.assertRaises(FrozenInstanceError):proof.app_id='cli_other'
        without={k:v for k,v in self.claim.items() if k!='sync_app'}
        self.pending(lambda:m.sync_app(self.context(claim=without)))
        for sync in ({'version':True,'app_id':'cli_sync'},{'version':2,'app_id':'cli_sync'},
                {'version':1,'app_id':'bad'},{'version':1,'app_id':'cli_sync','extra':1}):
            self.pending(lambda:m.sync_app(self.context(claim=dict(self.claim,sync_app=sync))))

if __name__=='__main__':unittest.main()
