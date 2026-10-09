#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Render the disabled daily discussion Workflow. No SaaS writes or grant decisions."""
import argparse
from pathlib import Path
import re

TEMPLATE = Path(__file__).resolve().parents[1] / 'references/workflows/channel-issue-progress.yaml'


def render(agent):
    if not isinstance(agent, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', agent):
        raise ValueError('agent must be the exact verified registered name')
    return TEMPLATE.read_text().replace('__AGENT_NAME__', agent)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', required=True)
    args = parser.parse_args()
    try:
        print(render(args.agent), end='')
    except ValueError as exc:
        parser.error(str(exc))
