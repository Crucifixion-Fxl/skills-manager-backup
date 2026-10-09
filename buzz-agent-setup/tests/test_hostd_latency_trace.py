import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hostd.latency_trace import LatencyTrace


class TraceTests(unittest.TestCase):
    def test_only_bounded_metadata_and_no_payload_or_invalid_identifiers(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'trace.jsonl'
            trace = LatencyTrace(p)
            trace.record('test', 'feishu_received', source='om_123', sdk_at=1.0)
            trace.record('test', 'not-a-stage', source='secret text')
            trace.record('test', 'relay_received', source='not an identifier')
            row, = [json.loads(s) for s in p.read_text().splitlines()]
            self.assertEqual(set(row), {'binding', 'stage', 'source', 'wall', 'monotonic', 'pid', 'sdk_at'})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with p.open('ab') as f: f.truncate(8 * 2**20)
            trace.record('test', 'round_started')
            self.assertEqual(p.stat().st_size, 8 * 2**20)

    def test_symlink_hardlink_and_public_file_are_never_written(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'trace'; other = Path(d) / 'other'
            other.write_text('unchanged'); other.chmod(0o600)
            trace = LatencyTrace(p)
            p.symlink_to(other)
            trace.record('test', 'round_started')
            self.assertEqual(other.read_text(), 'unchanged')
            p.unlink(); os.link(other, p)
            trace.record('test', 'round_started')
            self.assertEqual(other.read_text(), 'unchanged')
            p.unlink(); p.write_text('public'); p.chmod(0o644)
            trace.record('test', 'round_started')
            self.assertEqual(p.read_text(), 'public')


if __name__ == '__main__': unittest.main()
