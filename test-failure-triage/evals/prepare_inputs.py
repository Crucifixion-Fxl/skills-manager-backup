#!/usr/bin/env python3
"""Export opaque-ID cases without labels or descriptive case keys for blind eval."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
suite = json.loads(Path(__file__).with_name("evals.json").read_text())
cases = [{k: c[k] for k in ["id", "prompt", "evidence_ids"]} for c in suite["evals"]]
args.output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"cases": len(cases), "output": str(args.output)}))
