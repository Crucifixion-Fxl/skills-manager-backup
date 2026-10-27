#!/usr/bin/env python3
"""Check frozen inputs and exact decision labels; semantic review is separate."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
frozen = json.loads((ROOT / 'frozen.json').read_text())
for name in ('cases', 'rubric'):
    assert hashlib.sha256((ROOT / f'{name}.json').read_bytes()).hexdigest() == frozen[f'{name}_sha256'], f'{name} changed'
rubric = json.loads((ROOT / 'rubric.json').read_text())
report = {'kind': 'exact-status-comparison-only', 'runs': {}}
for run in ('baseline', 'candidate'):
    path = ROOT / 'results' / f'{run}.json'
    if not path.exists():
        continue
    rows = json.loads(path.read_text())
    assert len(rows) == len(rubric)
    assert {r['id'] for r in rows} == set(rubric)
    decisions = []
    for row in rows:
        assert row['release_authorized'] is False
        assert isinstance(row['findings'], list) and isinstance(row['gaps'], list)
        assert isinstance(row['rationale'], str) and row['rationale']
        expected = rubric[row['id']]['status']
        decisions.append({'id': row['id'], 'expected': expected, 'actual': row['status'], 'match': row['status'] == expected})
    report['runs'][run] = {'matched': sum(d['match'] for d in decisions), 'total': len(decisions), 'decisions': decisions}
print(json.dumps(report, ensure_ascii=False, indent=2))
