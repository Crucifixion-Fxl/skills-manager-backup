"""Attempt timing includes pre-send failures without exposing source content."""
import json
import unittest
import test_hostd_outlet_budget as fixture
from hostd.latency_trace import LatencyTrace

base=fixture.base
setUpModule=fixture.setUpModule
tearDownModule=fixture.tearDownModule

class OutletAttemptTrace(base.TmpCase):
    assembly=fixture.Scheduling.assembly
    event=fixture.Scheduling.event
    adapter=fixture.Scheduling.adapter

    def test_failed_root_has_attempt_pair_without_delivery_or_body(self):
        world,run,worker,spec=self.assembly()
        event=self.event(tags=[['e','a'*64,'','root']],content='PRIVATE_MESSAGE_BODY')
        world.events.append(event);world.relay_events.append(event)
        path=self.tmp/'attempt.jsonl'
        self.tmp.chmod(0o700)
        run.latency_trace=LatencyTrace(path)
        adapter=self.adapter(run,spec,world)
        with self.assertRaises(base.FGS.GroupSyncError):adapter.catch_up(max_events=1)
        raw=path.read_text();rows=[json.loads(line) for line in raw.splitlines()]
        self.assertEqual([r['stage'] for r in rows],['outlet_attempt_started','outlet_attempt_finished'])
        self.assertTrue(all(r['source']==event['id'] and r['target']==base.AGENT2_PK and r['kind']==9 for r in rows))
        self.assertNotIn('PRIVATE_MESSAGE_BODY',raw)
        self.assertIsNone(run.mapping_store.delivery_by_source('test',event['id'],'b2f',agent_id=base.AGENT2_PK))
        self.assertEqual(run.mapping_store.conn.execute('SELECT attempts FROM outlet_work WHERE source_id=?',(event['id'],)).fetchone()[0],1)

if __name__=='__main__':unittest.main()
