#!/usr/bin/env python3
"""Inventory AddX source references, not credentials or live SaaS capabilities."""
import argparse
import hashlib
import json
import re
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone

AUTH = re.compile(r'认证|登录|凭据|鉴权|OAuth|\bSSO\b|\bJWT\b|\bPAT\b|\bCookie\b|\bBearer\b|[A-Z][A-Z0-9_]*(?:TOKEN|API_KEY|SECRET_KEY|PASSWORD)|device.?auth', re.I)
KINDS = {'oauth-device': r'device.?auth|device.?flow|device.?code|设备授权',
         'oauth': r'OAuth|\bSSO\b|PKCE', 'browser-session': r'\bCookie\b|localStorage|浏览器.*登录|Session Cookie',
         'bearer-or-api-key': r'\bBearer\b|\bPAT\b|API.?Key|[A-Z][A-Z0-9_]*TOKEN',
         'password-or-basic': r'\bBasic\b|用户名.*密码|USERNAME|PASSWORD|PASSWD', 'hmac': r'HMAC|SecretKey'}

SOURCE_DOMAINS = frozenset(json.loads((Path(__file__).resolve().parents[1] / 'references/domain-classification.json').read_text())['sourceDomains'])

def classify_domain(host, path, line):
    """Conservative source classification; none of these hints prove live access."""
    if host in {'localhost', '127.0.0.1', '0.0.0.0'} or host.endswith('.svc'):
        return 'local-or-cluster-address'
    if 'example' in host or host.startswith('harbor-xxx') or 'does-not-exist' in host:
        return 'placeholder'
    if host in SOURCE_DOMAINS or host.startswith('pages.addx.'):
        return 'source-or-reference'
    if host.startswith(('docs.', 'developer.', 'apidoc.', 'support.')):
        return 'official-reference-candidate'
    if path.endswith('common-apps.md') or '示例' in line or '例如' in line:
        return 'deployment-example-candidate'
    if host.endswith(('addx.live', 'theunismart.com')):
        return 'instance-candidate-needs-owner-verification'
    return 'untriaged-external-reference'

def scan(root):
    root = root.resolve()
    records, domains = [], {}
    ignored = {'node_modules', '.venv', '.git', '__pycache__'}
    skills = []
    for directory, directories, filenames in os.walk(root / 'skills', followlinks=False):
        directories[:] = sorted(name for name in directories if name not in ignored and not (Path(directory) / name).is_symlink())
        if 'SKILL.md' in filenames:
            skills.append(Path(directory) / 'SKILL.md')
    for skill in sorted(skills):
        if skill.is_symlink() or not skill.is_file() or not skill.resolve().is_relative_to(root):
            raise ValueError(f'non-repository source: {skill}')
        body = skill.read_text(errors='replace')
        name = re.search(r'^name:\s*(.+)', body, re.M)
        paths = [skill] + sorted((skill.parent / 'references').rglob('*.md'))
        for filename in ('auth-profile.json', 'source-capabilities.json', 'auth-discovery.json', 'source-discovery.json', 'source-api-catalog.json', 'domain-classification.json'):
            paths += sorted((skill.parent / 'references').rglob(filename))
        # Cross-owner contracts remain a single source, but invalidate this owner's digest too.
        for discovery_path in list(paths):
            if discovery_path.name == 'auth-discovery.json':
                discovery = json.loads(discovery_path.read_text())
                if discovery.get('contract'):
                    contract = (discovery_path.parent / discovery['contract']).resolve()
                    if contract.name in {'inventory.json', 'tools.json'} or not contract.is_relative_to(root):
                        raise ValueError(f'unsafe discovery contract: {contract}')
                    if not contract.is_file():
                        raise ValueError(f'missing discovery contract: {contract}')
                    paths.append(contract)
        paths = sorted(set(paths))
        evidence, kinds, hosts, sources = [], set(), set(), []
        for file in paths:
            if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root):
                raise ValueError(f'non-repository source: {file}')
            text = file.read_text(errors='replace')
            relative = str(file.relative_to(root))
            sources.append({'path': relative, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
            lines = [i for i, line in enumerate(text.splitlines(), 1) if AUTH.search(line)]
            if lines:
                evidence.append({'path': relative, 'line_numbers': lines})
            for i, line in enumerate(text.splitlines(), 1):
                for host in re.findall(r'(?:https?|wss?)://([a-zA-Z0-9.-]+)(?=[:/\s`"\)\]<>]|$)', line):
                    host = host.lower()
                    hosts.add(host)
                    domains.setdefault(host, []).append({'path': relative, 'line': i, 'classification': classify_domain(host, relative, line)})
            kinds.update(k for k, pattern in KINDS.items() if re.search(pattern, text, re.I))
        records.append({'skill': name.group(1).strip('"\' ') if name else skill.parent.name,
                        'skill_path': str(skill.relative_to(root)), 'auth_candidate': bool(evidence),
                        'auth_kind_hints': sorted(kinds), 'domain_candidates': sorted(hosts),
                        'auth_evidence': evidence, 'sources': sources})
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        revision = None
    return {'schema_version': 2, 'source_revision': revision, 'generated_on': datetime.now(timezone.utc).isoformat(),
            'scope': 'AddX skills/**/SKILL.md, adjacent references/**/*.md and auth-profile/source-capabilities/auth-discovery/source-discovery JSON; generated inventory/tools excluded, excluding node_modules',
            'verification': 'source-only; hashes cover uncommitted source too; no credential reads or live access',
            'totals': {'skills': len(records), 'auth_candidates': sum(r['auth_candidate'] for r in records),
                       'auth_evidence_files': len({e['path'] for r in records for e in r['auth_evidence']})},
            'records': records, 'domain_evidence': domains}

def catalog(root, inventory, registry):
    paths = {r['skill_path']: r for r in inventory['records']}
    tools = []
    covered = set()
    by_skill = {r['skill']: r for r in inventory['records']}
    if len(by_skill) != len(inventory['records']):
        raise ValueError('duplicate Skill name in inventory')
    seen_platforms = set()
    profiles_by_tool = {}
    for source in inventory['records']:
        for evidence in source['sources']:
            if evidence['path'].endswith('/auth-profile.json'):
                profile_path = root / evidence['path']
                profile = json.loads(profile_path.read_text())
                profiles_by_tool.setdefault(profile.get('tool'), []).append(evidence['path'])
    for record in registry['tools']:
        if record['id'] in seen_platforms:
            raise ValueError(f"duplicate platform: {record['id']}")
        seen_platforms.add(record['id'])
        if len(profiles_by_tool.get(record['id'], [])) > 1:
            raise ValueError(f"duplicate authentication SSOT: {record['id']}")
        item = dict(record)
        for field in ['reference', 'accessProfile', 'authDiscovery', 'sourceDiscovery']:
            if item.get(field) and not (root / item[field]).is_file():
                raise ValueError(f'missing {field}: {item[field]}')
        owner = by_skill.get(item['owner'])
        if owner is None:
            raise ValueError(f"unknown owner: {item['owner']}")
        if item.get('accessProfile'):
            profile_path = root / item['accessProfile']
            profile = json.loads(profile_path.read_text())
            if profile.get('tool') != item['id'] or profile.get('owner') != item['owner']:
                raise ValueError(f"profile owner/tool mismatch: {item['accessProfile']}")
            item['runtimeVerification'] = profile.get('runtimeVerification', 'pending')
        item['sourceDigest'] = hashlib.sha256(json.dumps(owner['sources'], sort_keys=True).encode()).hexdigest()
        item['domainCandidates'] = owner['domain_candidates']
        covered.add(item['owner'])
        consumers = item.get('businessConsumers', [])
        if not isinstance(consumers, list) or any(c not in by_skill for c in consumers):
            raise ValueError(f"unknown business consumer: {item['id']}")
        covered.update(consumers)
        for field in ('authDiscovery', 'sourceDiscovery'):
            if item.get(field):
                discovery = json.loads((root / item[field]).read_text())
                if discovery.get('tool') != item['id'] or discovery.get('owner') != item['owner']:
                    raise ValueError(f"discovery owner/tool mismatch: {item[field]}")
        tools.append(item)
    # Never discard unexplained URLs: the maintenance skill triages them, without promoting docs to SaaS.
    pending = [{'skill': r['skill'], 'reference': r['skill_path'], 'domainCandidates': r['domain_candidates'],
                'disposition': 'needs-platform-triage',
                'domainClassifications': {host: sorted({e['classification'] for e in inventory['domain_evidence'][host] if e['path'] in {s['path'] for s in r['sources']}}) for host in r['domain_candidates']}} for r in inventory['records']
               if r['skill'] not in covered and r['domain_candidates']]
    triage = registry.get('platformTriage', [])
    allowed = {'deployment-example-only', 'placeholder-no-instance', 'instance-unconfirmed', 'existing-owner-route', 'needs-source-research'}
    for item in triage:
        if item.get('disposition') not in allowed or not item.get('reason') or not item.get('sources'):
            raise ValueError(f'incomplete platform triage: {item}')
        for source in item['sources']:
            if not (root / source).is_file():
                raise ValueError(f'missing triage source: {source}')
    return {'schemaVersion': 2, 'platformTriage': triage, 'verification': 'source-only catalog; runtime status belongs to per-SaaS evidence',
            'tools': tools, 'unclassifiedReferences': pending}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output_dir or root / 'skills/agent-harness/platform-onboarding/references'
    registry = json.loads((root / 'skills/agent-harness/platform-onboarding/references/registry.json').read_text())
    inventory = scan(root)
    tools = catalog(root, inventory, registry)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('inventory.json', inventory), ('tools.json', tools)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({**inventory['totals'], 'platformOwners': len(tools['tools']),
                      'unclassifiedReferences': len(tools['unclassifiedReferences'])}, ensure_ascii=False))

if __name__ == '__main__':
    main()
