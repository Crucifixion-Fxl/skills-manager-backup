"""Actual effect adapters retain SQL/file authority across awaited fixture I/O."""
import asyncio
import json
from pathlib import Path
import sys
import unittest

TESTS=Path(__file__).resolve().parent
sys.path.insert(0,str(TESTS));sys.path.insert(0,str(TESTS.parent/'scripts'))
import test_hostd_onboarding_responsiveness as base
from hostd import join_effects as effects


@unittest.skipUnless(base.fixture.fixture.AESGCM is not None, 'protected catalog requires cryptography')
class AwaitSafety(unittest.IsolatedAsyncioTestCase):
    setUp=base.Responsiveness.setUp
    create=base.Responsiveness.create
    bound=base.Responsiveness.bound
    request=base.Responsiveness.request
    bound_effect=base.Responsiveness.bound_effect

    async def ready(self):
        self.bound();service=await self.create();row=self.bound_effect(service)
        roster=base.fixture.gs.sign_event(self.w.relay_key,39002,
            [['d',base.fixture.CHANNEL],['p',self.f.owner,'','owner'],['p',self.f.pub,'','bot']],'',1000)
        original=service.http
        def http(url,headers,timeout,body=None):
            result=original(url,headers,timeout,body)
            if url.endswith('/query') and json.loads(body)[0].get('kinds')==[39002]:return 200,json.dumps([roster]).encode()
            return result
        service.relay.http=http
        env=effects.legacy.parse_env(self.f.env_text)
        class Ops:
            def unit_status(self,unit):return 'loaded','active'
            def process(self,unit):return 123,1,env
            def journal_process(self,unit,pid):return 'subscribed to channel '+base.fixture.CHANNEL
            def invocation_id(self,unit):return '1'*32
            def journal_invocation(self,unit,pid,invocation):return 'subscribed to channel '+base.fixture.CHANNEL
        service.effects.runtime=effects.AgentRuntime(Ops())
        return service,row

    async def test_protected_config_changed_during_registration_cannot_mint_grant(self):
        service,row=await self.ready();outer=self;called=[]
        class Registrar:
            async def readback(self,row,plan):
                called.append(True)
                await asyncio.sleep(.01)
                outer.f.write(outer.f.responsible,json.dumps({'channels':[]}))
                return effects.RegistrationProof(row['request_id'],'alpha',base.fixture.CHANNEL,'oc_group','cli_agent',True,True,True,True,1000,'e'*64)
        service.effects.registrar=Registrar()
        answer=await service.effects.readback(row)
        self.assertEqual(called,[True],'must reach awaited actual registration gate')
        self.assertFalse(answer.verified)
        self.assertIsNone(self.db.conn.execute('SELECT * FROM agent_chat WHERE agent_id=?',(self.f.pub,)).fetchone())
        self.assertEqual(self.db.join_request(row['request_id'])['status'],'approved')

    async def test_unchanged_protected_config_and_fresh_runtime_mint_active_grant(self):
        service,row=await self.ready();called=[]
        class Registrar:
            async def readback(self,row,plan):
                called.append(True)
                await asyncio.sleep(.01)
                return effects.RegistrationProof(row['request_id'],'alpha',base.fixture.CHANNEL,
                    'oc_group','cli_agent',True,True,True,True,1000,'e'*64)
        service.effects.registrar=Registrar()
        answer=await service.effects.readback(row)
        self.assertEqual(called,[True])
        self.assertTrue(answer.verified)
        grant=self.db.conn.execute('SELECT * FROM agent_chat WHERE agent_id=?',(self.f.pub,)).fetchone()
        self.assertEqual((grant['status'],grant['binding_id'],grant['chat_id']),('active','alpha','oc_group'))

    async def test_revoked_approval_during_roster_prevents_any_publication(self):
        service,row=await self.ready();original=service.clients['cli_agent'].runner
        entered=asyncio.Event();loop=asyncio.get_running_loop()
        def runner(argv,**kw):
            if '+chat-members-list' in argv:
                loop.call_soon_threadsafe(entered.set)
                import time;time.sleep(.15)
            return original(argv,**kw)
        service.clients['cli_agent'].runner=runner
        task=asyncio.create_task(service.effects.apply(row))
        await asyncio.wait_for(entered.wait(),5)
        self.db.conn.execute("UPDATE join_request SET status='denied' WHERE request_id=?",(row['request_id'],))
        with self.assertRaises(effects.EffectError):await task
        self.assertEqual(self.db.effect_steps(row['request_id']),[])
        self.assertFalse(any(url.endswith('/events') for url,_ in self.w.http_calls))

    async def test_unknown_publication_is_readback_only_after_lease_expiry(self):
        self.bound();service=await self.create();row=self.bound_effect(service)
        original=service.http;publications=[]
        def http(url,headers,timeout,body=None):
            if url.endswith('/events'):
                event=json.loads(body);self.assertTrue(base.fixture.gs._nip01_event_verified(event))
                publications.append(event['id']);raise TimeoutError('OFFLINE_LOST_REPLY')
            return original(url,headers,timeout,body)
        service.relay.http=http
        with self.assertRaises(effects.EffectError):await service.effects.apply(row)
        self.assertEqual(len(publications),1)
        self.assertEqual(self.db.effect_steps(row['request_id'])[0]['status'],'unknown')
        self.w.now+=70
        await service.effects.apply(row)
        self.assertEqual(len(publications),1)
        self.assertEqual(self.db.effect_steps(row['request_id'])[0]['status'],'unknown')


if __name__=='__main__':unittest.main()
