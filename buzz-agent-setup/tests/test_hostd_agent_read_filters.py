"""Own-agent read-only replay filters: actual synthetic signatures/fake HTTP."""
import json
import unittest

import test_hostd_agent_signed_reads as fixture
from hostd.agent_signed_reads import AgentReadFailure,AgentReadRequest
from hostd.signed_reads import ReadRequest,ReadFailure

REF='4'*64
OTHER_REF='5'*64


class AgentReadFilters(fixture.Base):
    def events_at(self,*stamps,reference=REF):
        self.events=[fixture.gs.sign_event(fixture.KEY,9,[['h',fixture.CHANNEL],['e',reference]],
                     'synthetic-private-body',stamp) for stamp in stamps]

    async def accepted(self,filters):
        reader=self.reader()
        answer=await reader.query(filters)
        self.assertEqual(answer,self.events)
        self.assertEqual(json.loads(self.calls[-1][2]),filters)
        self.assertEqual(len(self.calls),1,'valid filters must reach actual NIP98/OA HTTP query')

    async def test_since_until_are_actual_scalar_wire_params(self):
        self.events_at(fixture.NOW)
        await self.accepted([{'kinds':[9],'authors':[fixture.PUB],'since':fixture.NOW-1,'until':fixture.NOW+1}])

    async def test_event_reference_filter_reaches_exact_signed_wire(self):
        self.events_at(fixture.NOW)
        await self.accepted([{'kinds':[9],'#e':[REF],'limit':2}])

    async def test_combined_filters_include_both_interval_boundaries(self):
        self.events_at(fixture.NOW-1,fixture.NOW+1)
        await self.accepted([{'kinds':[9],'authors':[fixture.PUB],'#h':[fixture.CHANNEL],
                             '#e':[REF],'since':fixture.NOW-1,'until':fixture.NOW+1,'limit':3}])

    async def test_invalid_time_scalars_and_reversed_interval_fail_before_io(self):
        for filters in ([{'since':True}],[{'until':False}],[{'since':-1}],[{'until':-1}],
                        [{'since':2**63}],[{'until':2**63}],[{'since':'1'}],[{'until':[1]}],
                        [{'since':2,'until':1}],[{'since':0,'unknown':1}]):
            with self.subTest(field=next(iter(filters[0]))):
                await self.fails(self.reader().query(filters))
        self.assertEqual(self.calls,[])

    async def test_reference_requires_nonempty_list_of_exact_lowercase_hex_ids(self):
        for value in ([],REF,['bad'],[True],['F'*64],[REF+'0'],[[REF]],[OTHER_REF+'\n']):
            with self.subTest(shape=type(value).__name__):
                await self.fails(self.reader().query([{'#e':value}]))
        self.assertEqual(self.calls,[])

    async def test_actual_signed_response_outside_requested_interval_is_pending(self):
        for stamp in (fixture.NOW-2,fixture.NOW+2):
            with self.subTest(position='before' if stamp<fixture.NOW else 'after'):
                self.calls.clear();self.events_at(stamp)
                self.assertTrue(fixture.gs._nip01_event_verified(self.events[0]))
                await self.fails(self.reader().query([{'kinds':[9],'since':fixture.NOW-1,'until':fixture.NOW+1}]))
                self.assertEqual(len(self.calls),1,'negative must reach the actual signed response check')
        self.assertEqual(AgentReadFailure().status,'pending')

    async def test_actual_signed_response_with_wrong_event_reference_is_pending(self):
        self.events_at(fixture.NOW,reference=OTHER_REF)
        self.assertTrue(fixture.gs._nip01_event_verified(self.events[0]))
        await self.fails(self.reader().query([{'kinds':[9],'#e':[REF]}]))
        self.assertEqual(len(self.calls),1,'negative must reach the actual signed response check')
        self.assertEqual(AgentReadFailure().status,'pending')

    def test_packet_parser_keeps_owner_namespace_unchanged(self):
        packet=dict(version=1,origin=fixture.ORIGIN,key=fixture.KEY,pin=fixture.PIN,now=fixture.NOW,
                    operation='query',agent=fixture.PUB,owner=fixture.OWNER,auth_tag=fixture.auth_tag(),
                    filters=[{'kinds':[9],'since':0,'until':2**63-1,'#e':[REF]}])
        request=AgentReadRequest.from_data(packet)
        self.assertEqual(request.packet(),packet)
        with self.assertRaises(ReadFailure):ReadRequest.from_data(packet)
        owner={k:v for k,v in packet.items() if k not in ('agent','owner','auth_tag')}
        with self.assertRaises(ReadFailure):ReadRequest.from_data(owner)

if __name__=='__main__':unittest.main()
