#!/usr/bin/env python3
"""Render a disabled native Workflow; no SaaS access or permission decisions."""
import argparse
import json
from pathlib import Path
import re

TEMPLATE = Path(__file__).resolve().parents[1] / 'references/workflows/minutes-issue-wake.yaml'


def render(agent, publishers, minute_hosts, issue_projects):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', agent):
        raise ValueError('agent must be the verified registered name')
    if not publishers or any(not re.fullmatch(r'[0-9a-f]{64}', p) for p in publishers):
        raise ValueError('exact publisher pubkeys required')
    if not minute_hosts or any(not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.feishu\.cn', h) for h in minute_hosts):
        raise ValueError('explicit tenant minute hosts required')
    if any(not re.fullmatch(r'[a-z0-9.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+', p)
           or any(x in ('.', '..') for x in p.split('/')) for p in issue_projects):
        raise ValueError('exact GitLab host/project paths required')
    authors = ' || '.join(f'trigger_author == {json.dumps(p)}' for p in sorted(set(publishers)))
    prefixes = [f'https://{h}/minutes/' for h in sorted(set(minute_hosts))]
    prefixes += [f'https://{p}/-/issues/' for p in sorted(set(issue_projects))]
    links = ' || '.join(f'str_contains(trigger_text, {json.dumps(p)})' for p in prefixes)
    excludes = ['[minutes-issue-wake:v1]', '[minutes-issue-result:v1]', '[gitlab-notify:v1]', 'gitlab-buzz-binding:v1']
    guard = ' && '.join(f'!str_contains(trigger_text, {json.dumps(x)})' for x in excludes)
    expression = f'({authors}) && ({links}) && {guard}'
    return TEMPLATE.read_text().replace('__FILTER_JSON_STRING__', json.dumps(expression, ensure_ascii=False)).replace('__AGENT_NAME__', agent)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', required=True)
    parser.add_argument('--publisher', action='append', required=True)
    parser.add_argument('--minutes-host', action='append', required=True)
    parser.add_argument('--issue-project', action='append', default=[])
    args = parser.parse_args()
    print(render(args.agent, args.publisher, args.minutes_host, args.issue_project), end='')
