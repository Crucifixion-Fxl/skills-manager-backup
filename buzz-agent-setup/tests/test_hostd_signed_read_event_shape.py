"""Complete event shapes, using actual synthetic Schnorr signatures and fake HTTP."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_hostd_agent_signed_reads as fixture
from hostd.signed_reads import SignedReader, ReadFailure
from hostd.agent_signed_reads import AgentReadFailure
from recovery_relay import RecoveryRelay


class EventShape(fixture.Base):
    def owner_reader(self):
        self.origin=fixture.ORIGIN
        def http(url,headers,timeout,*,body=None):
            self.calls.append((url,headers,body))
            return 200,json.dumps(self.events).encode()
        return SignedReader(RecoveryRelay(fixture.ORIGIN,fixture.KEY,fixture.PIN,http=http,
                            now=lambda:datetime.fromtimestamp(fixture.NOW,timezone.utc)))

    async def owner_query(self):
        return await self.owner_reader().read('query',filters=[{'kinds':[9],'authors':[fixture.PUB]}])

    async def own_query(self):
        return await self.reader().query([{'kinds':[9],'authors':[fixture.PUB]}])

    async def reject(self,query,failure):
        with self.assertRaises(failure) as caught:await query()
        self.assertIn('怎么解决',str(caught.exception))
        self.assertIn('复制给 AI',str(caught.exception))
        self.assertNotIn('synthetic-private-body',str(caught.exception))
        self.assertNotIn(fixture.KEY,str(caught.exception))

    async def malformed_tags(self,query,failure):
        for tags in ([[123]],[['h',fixture.CHANNEL,123]],[[]],['not-a-tag'],[[None]],[[{'h':fixture.CHANNEL}]]):
            with self.subTest(shape=type(tags[0]).__name__):
                self.events=[fixture.gs.sign_event(fixture.KEY,9,tags,'synthetic-private-body',fixture.NOW)]
                self.assertTrue(fixture.gs._nip01_event_verified(self.events[0]),'signature oracle must stay unchanged')
                await self.reject(query,failure)
        self.assertEqual(len(self.calls),6,'negative must reach actual signed HTTP response path')

    async def malformed_scalars(self,query,failure):
        for kind,stamp in ((-1,fixture.NOW),(65536,fixture.NOW),(9,-1),(9,2**63)):
            with self.subTest(kind=kind,stamp=stamp):
                self.events=[fixture.gs.sign_event(fixture.KEY,kind,[['h',fixture.CHANNEL]],'synthetic-private-body',stamp)]
                self.assertTrue(fixture.gs._nip01_event_verified(self.events[0]))
                await self.reject(query,failure)
        self.assertEqual(len(self.calls),4)

    async def test_owner_rejects_actual_signed_malformed_tags(self):
        await self.malformed_tags(self.owner_query,ReadFailure)

    async def test_own_agent_rejects_actual_signed_malformed_tags(self):
        await self.malformed_tags(self.own_query,AgentReadFailure)

    async def test_owner_rejects_actual_signed_out_of_range_scalars(self):
        await self.malformed_scalars(self.owner_query,ReadFailure)

    async def test_own_agent_rejects_actual_signed_out_of_range_scalars(self):
        await self.malformed_scalars(self.own_query,AgentReadFailure)

    async def test_owner_legal_empty_tags_and_string_tags_return_full_event(self):
        for tags in ([],[['h',fixture.CHANNEL],['p',fixture.PUB,'','bot']]):
            self.events=[fixture.gs.sign_event(fixture.KEY,9,tags,'synthetic-private-body',fixture.NOW)]
            self.assertEqual(await self.owner_query(),self.events)

    async def test_own_agent_legal_empty_tags_and_string_tags_return_full_event(self):
        for tags in ([],[['h',fixture.CHANNEL],['p',fixture.PUB,'','bot']]):
            self.events=[fixture.gs.sign_event(fixture.KEY,9,tags,'synthetic-private-body',fixture.NOW)]
            self.assertEqual(await self.own_query(),self.events)
