#!/usr/bin/env python3
"""Build/verify a source-pinned, bounded native Skill resource bundle.

This is an offline resource packager, never an installer, interpreter of linked
commands, credential loader, admission authority or production recovery tool.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess

SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
SAFE_PATH = re.compile(r'[A-Za-z0-9_./+@-]+')
CONDITION = re.compile(r'[a-z][a-z0-9-]{0,63}')
MANIFEST = 'closure-manifest.json'
MAX_PROFILE_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class ClosureError(ValueError):
    pass


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ClosureError('duplicate JSON field')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        raise ClosureError('invalid JSON document') from error


def _path(value):
    if (not isinstance(value, str) or not SAFE_PATH.fullmatch(value)
            or value.startswith('/') or any(p in {'', '.', '..'} for p in value.split('/'))):
        raise ClosureError('unsafe or escaping source path')
    return value


def _git(repo, *args):
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0',
           'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
           # A partial clone can lazy-fetch missing cat-file objects using a
           # repository-local remote/helper. Both layers must stay offline.
           'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': ''}
    try:
        value = subprocess.check_output(
            ['/usr/bin/git', '--literal-pathspecs', '-c', 'core.fsmonitor=false',
             '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args],
            env=env, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ClosureError('fixed Git source evidence unavailable') from error
    if len(value) > 16 * 1024 * 1024:
        raise ClosureError('Git source output exceeds bound')
    return value


def _tree_rows(repo, commit, path, recursive=False):
    args = ['ls-tree', '-z', '--full-tree']
    if recursive:
        args.append('-r')
    raw = _git(repo, *args, commit, '--', path)
    rows = []
    try:
        for line in raw.rstrip(b'\0').split(b'\0') if raw else []:
            metadata, name = line.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split()
            rows.append((name.decode('utf-8'), mode, kind, oid))
    except (ValueError, UnicodeError) as error:
        raise ClosureError('invalid Git source tree entry') from error
    return rows


def _blob(repo, commit, path, byte_limit):
    rows = _tree_rows(repo, commit, path)
    if len(rows) != 1 or rows[0][0] != path:
        raise ClosureError('missing exact source target: ' + path)
    _, mode, kind, oid = rows[0]
    if kind != 'blob' or mode not in {'100644', '100755'}:
        raise ClosureError('source target must be a regular file: ' + path)
    try:
        size = int(_git(repo, 'cat-file', '-s', oid))
    except ValueError as error:
        raise ClosureError('invalid source blob size') from error
    if size > byte_limit:
        raise ClosureError('source byte budget exceeded')
    raw = _git(repo, 'cat-file', 'blob', oid)
    if len(raw) != size:
        raise ClosureError('source blob changed during read')
    return raw, mode


def _profile(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PROFILE_BYTES:
        raise ClosureError('profile must be a bounded regular file')
    raw = path.read_bytes()
    value = _json(raw)
    fields = {'schema_version', 'name', 'source_commit', 'entry_tree', 'entry_skill', 'limits', 'dependencies'}
    if not isinstance(value, dict) or set(value) not in (fields, fields | {'project_references'}) or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ClosureError('invalid closure profile fields')
    if not isinstance(value['source_commit'], str) or not SHA.fullmatch(value['source_commit']):
        raise ClosureError('profile requires an exact source commit')
    entry = _path(value['entry_tree'])
    parts = entry.split('/')
    if len(parts) != 3 or parts[0] != 'skills' or parts[-1] != value['entry_skill']:
        raise ClosureError('entry must preserve skills/category/name topology')
    if (not isinstance(value['entry_skill'], str) or not isinstance(value['name'], str)
            or not CONDITION.fullmatch(value['entry_skill']) or not CONDITION.fullmatch(value['name'])):
        raise ClosureError('invalid profile identity')
    limits = value['limits']
    if not isinstance(limits, dict) or set(limits) != {'max_files', 'max_total_bytes', 'max_depth'}:
        raise ClosureError('invalid closure limits')
    for field, maximum in [('max_files', 512), ('max_total_bytes', 16 * 1024 * 1024), ('max_depth', 32)]:
        if type(limits[field]) is not int or not 0 <= limits[field] <= maximum:
            raise ClosureError('invalid closure limit')
    if not isinstance(value['dependencies'], list) or len(value['dependencies']) > 512:
        raise ClosureError('invalid dependency declarations')
    dependencies = {}
    for item in value['dependencies']:
        if not isinstance(item, dict) or set(item) - {'path', 'activation', 'reason', 'direct', 'source_evidence'} or not {'path', 'activation', 'reason'} <= set(item):
            raise ClosureError('invalid dependency declaration')
        target = _path(item['path'])
        if target.startswith(entry + '/') or target in dependencies:
            raise ClosureError('duplicate or redundant dependency declaration')
        if not isinstance(item['activation'], str) or not CONDITION.fullmatch(item['activation']):
            raise ClosureError('invalid dependency condition')
        if not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ClosureError('dependency requires source rationale')
        dependencies[target] = item
    projects = {}
    declarations = value.get('project_references', [])
    if not isinstance(declarations, list) or len(declarations) > 256:
        raise ClosureError('invalid project-context declarations')
    for item in declarations:
        if not isinstance(item, dict) or set(item) != {'from', 'link', 'reason', 'source_evidence'}:
            raise ClosureError('invalid project-context declaration')
        source = _path(item['from'])
        if (not isinstance(item['link'], str) or not isinstance(item['reason'], str)
                or not item['reason'].strip() or not isinstance(item['source_evidence'], str)
                or not item['source_evidence'].strip()):
            raise ClosureError('project-context declaration requires source evidence')
        key = (source, item['link'])
        if key in projects:
            raise ClosureError('duplicate project-context declaration')
        projects[key] = item
    return value, raw, dependencies, projects


def _links(raw):
    try:
        text = raw.decode('utf-8')
    except UnicodeError as error:
        raise ClosureError('linked Markdown must be UTF-8') from error
    fence = None
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence is not None:
            if (marker and marker.group(1)[0] == fence[0]
                    and len(marker.group(1)) >= fence[1] and not marker.group(2).strip(' \t')):
                fence = None
            continue
        if marker and (marker.group(1)[0] != '`' or '`' not in marker.group(2)):
            fence = (marker.group(1)[0], len(marker.group(1)))
            continue
        # Container/indented block state is outside this bounded scanner.
        # Refuse recognizable such fences instead of hiding later body links.
        if (re.match(r'^(?: {4,}|[ \t]*\t[ \t]*)(?:`{3,}|~{3,})', line)
                or re.match(r'^[ \t]*(?:>[ \t]*|(?:[-+*]|[0-9]+[.)])[ \t]+)+(?:`{3,}|~{3,})', line)):
            raise ClosureError('unsupported container or indented fence')
        definition = re.match(r'^ {0,3}\[(?!\^)[^\]\r\n]+\]:\s*(.*)$', line)
        if definition:
            # Inspect every definition, including shortcut/collapsed uses
            # and unused labels. Unknown local syntax must not disappear.
            if not definition.group(1):
                raise ClosureError('multiline or empty reference definition is unsupported')
            yield definition.group(1)
        for match in re.finditer(r'\]\(([^)\r\n]+)\)', line):
            yield match.group(1)


def _target(source, link):
    if not isinstance(link, str) or any(c in link for c in '$`\\<>\x00'):
        raise ClosureError('commands or unsupported link syntax are forbidden')
    if link.startswith(('https://', 'http://', 'mailto:')):
        return None  # Informational only. Never fetch or execute external links.
    target = link.split('#', 1)[0]
    if not target:
        return None
    if not SAFE_PATH.fullmatch(target) or target.startswith('/'):
        raise ClosureError('external command or unsafe link target')
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    return _path(normalized)


def _plan(repo, profile_path, enabled):
    profile, raw, dependencies, project_references = _profile(profile_path)
    commit = profile['source_commit']
    if _git(repo, 'rev-parse', '--verify', commit + '^{commit}').decode().strip() != commit:
        raise ClosureError('source commit identity mismatch')
    conditions = {d['activation'] for d in dependencies.values()} - {'mandatory'}
    enabled = set(enabled)
    if not enabled <= conditions:
        raise ClosureError('unknown requested condition')
    limits = profile['limits']
    entry = profile['entry_tree']
    entry_rows = _tree_rows(repo, commit, entry, recursive=True)
    if not entry_rows or len(entry_rows) > limits['max_files']:
        raise ClosureError('entry file budget exceeded or empty entry')
    for target, mode, kind, _ in entry_rows:
        _path(target)
        if not target.startswith(entry + '/') or mode not in {'100644', '100755'} or kind != 'blob':
            raise ClosureError('entry contains a non-regular source target')
    # Even deferred declarations must point to real files in the approved source.
    for target in dependencies:
        rows = _tree_rows(repo, commit, target)
        if len(rows) != 1 or rows[0][0] != target:
            raise ClosureError('missing declared source target: ' + target)
        if rows[0][1] not in {'100644', '100755'} or rows[0][2] != 'blob':
            raise ClosureError('dependency source must be a regular file')
    queue = deque((name, 0) for name, _, _, _ in entry_rows)
    files, contents, edges, used_project_references = {}, {}, [], set()
    total = 0
    while queue:
        source, depth = queue.popleft()
        if source in files:
            continue
        if depth > limits['max_depth']:
            raise ClosureError('recursive dependency depth budget exceeded')
        body, mode = _blob(repo, commit, source, limits['max_total_bytes'] - total)
        total += len(body)
        files[source] = {'path': source, 'bytes': len(body), 'sha256': _digest(body), 'git_mode': mode}
        contents[source] = body
        if len(files) > limits['max_files']:
            raise ClosureError('recursive dependency file budget exceeded')
        if not source.endswith('.md'):
            continue
        for link in _links(body):
            target = _target(source, link)
            if target is None:
                continue
            if (source, link) in project_references:
                # A fixed, source-reviewed project template is not an installed
                # policy resource and does not prove business evidence exists.
                used_project_references.add((source, link))
                edges.append({'from': source, 'link': link, 'target': None,
                              'activation': 'project-context', 'state': 'external-project-evidence'})
                continue
            if target.startswith(entry + '/'):
                activation = 'mandatory'
            elif target in dependencies:
                activation = dependencies[target]['activation']
            else:
                raise ClosureError('undeclared external dependency: ' + target)
            selected = activation == 'mandatory' or activation in enabled
            edges.append({'from': source, 'link': link, 'target': target,
                          'activation': activation, 'state': 'included' if selected else 'deferred'})
            if selected:
                queue.append((target, depth + 1))
    entry_path = entry + '/SKILL.md'
    if entry_path not in files:
        raise ClosureError('entry Skill document is missing')
    if set(project_references) != used_project_references:
        raise ClosureError('project-context declaration does not match an observed reference')
    for target, item in dependencies.items():
        selected = item['activation'] == 'mandatory' or item['activation'] in enabled
        if selected and target not in files:
            kind = 'mandatory' if item['activation'] == 'mandatory' else 'selected'
            raise ClosureError(kind + ' declaration is not reachable: ' + target)
    manifest = {'schema_version': 1, 'source_commit': commit, 'profile_name': profile['name'],
                'profile_sha256': _digest(raw), 'builder_sha256': _digest(Path(__file__).read_bytes()),
                'entry_path': entry_path, 'enabled_conditions': sorted(enabled),
                'missing_conditions': sorted(conditions - enabled),
                'closure_scope': 'mandatory-and-explicitly-selected-conditions',
                'files': [files[key] for key in sorted(files)],
                'references': sorted(edges, key=lambda e: (e['from'], e['link'], e['target']))}
    return manifest, contents


def build(repo, profile_path, output, *, enabled=()):
    """No existing output is replaced; only exact Git objects become resources."""
    output = Path(output).absolute()
    if os.path.lexists(output):
        raise ClosureError('output already exists; never overwrite a bundle')
    manifest, contents = _plan(Path(repo).resolve(strict=True), profile_path, enabled)
    output.parent.mkdir(parents=True, exist_ok=True)
    output = output.parent.resolve(strict=True) / output.name
    try:
        output.mkdir(mode=0o755)
    except FileExistsError as error:
        raise ClosureError('output already exists; never overwrite a bundle') from error
    try:
        for item in manifest['files']:
            destination = output / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents[item['path']])
            destination.chmod(0o555 if item['git_mode'] == '100755' else 0o444)
        raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
        # Manifest is the final file. This new-directory build is not an atomic
        # production publication; consumers must verify its approved digest.
        (output / MANIFEST).write_bytes(raw)
        (output / MANIFEST).chmod(0o444)
        receipt = verify(output, _digest(raw))
        return {**receipt, 'status': 'built', 'bundle': str(output), 'source_commit': manifest['source_commit']}
    except BaseException:
        shutil.rmtree(output)
        raise


def _read_regular(path, limit):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_size > limit:
        raise ClosureError('bundle file is non-regular or exceeds bound')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ClosureError('bundle file identity changed')
            return stream.read(limit + 1)
    except OSError as error:
        raise ClosureError('bundle file read unavailable') from error


def verify(bundle, expected_manifest_sha256, *, required_conditions=()):
    """The expected manifest digest comes from independent source/build approval."""
    if not isinstance(expected_manifest_sha256, str) or not DIGEST.fullmatch(expected_manifest_sha256):
        raise ClosureError('one trusted manifest digest is required')
    bundle = Path(bundle).absolute()
    if bundle.is_symlink() or bundle.resolve(strict=True) != bundle or not bundle.is_dir():
        raise ClosureError('bundle must be a physical directory')
    raw = _read_regular(bundle / MANIFEST, MAX_MANIFEST_BYTES)
    if _digest(raw) != expected_manifest_sha256:
        raise ClosureError('bundle manifest digest mismatch')
    if stat.S_IMODE((bundle / MANIFEST).stat().st_mode) != 0o444:
        raise ClosureError('bundle manifest mode mismatch')
    manifest = _json(raw)
    fields = {'schema_version', 'source_commit', 'profile_name', 'profile_sha256', 'builder_sha256',
              'entry_path', 'enabled_conditions', 'missing_conditions', 'closure_scope', 'files', 'references'}
    if (not isinstance(manifest, dict) or set(manifest) != fields
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1):
        raise ClosureError('invalid bundle manifest')
    for field, pattern in [('source_commit', SHA), ('profile_name', CONDITION),
                           ('profile_sha256', DIGEST), ('builder_sha256', DIGEST)]:
        if not isinstance(manifest[field], str) or not pattern.fullmatch(manifest[field]):
            raise ClosureError('invalid manifest source identity')
    _path(manifest['entry_path'])
    for field in ['enabled_conditions', 'missing_conditions']:
        values = manifest[field]
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not CONDITION.fullmatch(value) for value in values)):
            raise ClosureError('invalid manifest conditions')
    if not isinstance(manifest['files'], list) or not isinstance(manifest['references'], list):
        raise ClosureError('invalid manifest resource collections')
    if not set(required_conditions) <= set(manifest['enabled_conditions']):
        raise ClosureError('required condition is unavailable in this closure')
    expected, directories = {MANIFEST}, {''}
    files = {}
    for item in manifest['files']:
        if not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256', 'git_mode'}:
            raise ClosureError('invalid manifest file record')
        relative = _path(item['path'])
        if relative in expected or type(item['bytes']) is not int or item['bytes'] < 0 or item['bytes'] > 16 * 1024 * 1024:
            raise ClosureError('duplicate or invalid manifest file')
        if (not isinstance(item['sha256'], str) or not DIGEST.fullmatch(item['sha256'])
                or not isinstance(item['git_mode'], str) or item['git_mode'] not in {'100644', '100755'}):
            raise ClosureError('invalid manifest source identity')
        expected.add(relative); files[relative] = item
        directories.update(str(p) for p in PurePosixPath(relative).parents if str(p) != '.')
    observed = set()
    for current, dirs, names in os.walk(bundle, followlinks=False):
        parent = Path(current)
        for name in dirs:
            directory = parent / name
            if directory.is_symlink() or str(directory.relative_to(bundle)) not in directories:
                raise ClosureError('unexpected or symlink bundle directory')
        for name in names:
            path = parent / name
            relative = str(path.relative_to(bundle))
            if relative not in expected or path.is_symlink():
                raise ClosureError('unexpected or symlink bundle file')
            observed.add(relative)
            if relative == MANIFEST:
                continue
            item = files[relative]
            content = _read_regular(path, item['bytes'])
            mode = stat.S_IMODE(path.stat().st_mode)
            if len(content) != item['bytes'] or _digest(content) != item['sha256'] or mode != (0o555 if item['git_mode'] == '100755' else 0o444):
                raise ClosureError('bundle file content or mode mismatch')
    if observed != expected or manifest['entry_path'] not in files:
        raise ClosureError('missing bundle resource')
    for edge in manifest['references']:
        if (not isinstance(edge, dict) or set(edge) != {'from', 'link', 'target', 'activation', 'state'}
                or not isinstance(edge['activation'], str) or not CONDITION.fullmatch(edge['activation'])
                or not isinstance(edge['state'], str)
                or edge['state'] not in {'included', 'deferred', 'external-project-evidence'}):
            raise ClosureError('invalid manifest reference record')
        _path(edge['from'])
        if edge['target'] is not None:
            _path(edge['target'])
        target = _target(edge['from'], edge['link'])
        if edge['state'] == 'external-project-evidence':
            if edge['from'] not in files or target is None or edge['target'] is not None:
                raise ClosureError('invalid project-context reference identity')
            continue
        if edge['from'] not in files or target != edge['target']:
            raise ClosureError('invalid bundle reference identity')
        if edge['state'] == 'included' and edge['target'] not in files:
            raise ClosureError('missing required reference target')
    return {'status': 'verified', 'bundle': str(bundle), 'source_commit': manifest['source_commit'],
            'manifest_sha256': expected_manifest_sha256, 'entry_path': manifest['entry_path'],
            'file_count': len(files), 'missing_conditions': manifest['missing_conditions'],
            'external_project_reference_count': sum(e['state'] == 'external-project-evidence' for e in manifest['references']),
            'closure_scope': manifest['closure_scope']}


def verify_entry(bundle, entry, expected_manifest_sha256, *, required_conditions=()):
    receipt = verify(bundle, expected_manifest_sha256, required_conditions=required_conditions)
    bundle = Path(receipt['bundle'])
    entry = Path(entry).absolute()
    expected_entry = bundle / receipt['entry_path']
    if (entry / 'SKILL.md').resolve(strict=True) != expected_entry:
        raise ClosureError('discovery entry does not bind the approved bundle')
    manifest = _json(_read_regular(bundle / MANIFEST, MAX_MANIFEST_BYTES))
    # A flat discovery alias is allowed only at the entry itself. Reference
    # traversal uses the actual OS path (symlink then ..), not lexical normpath.
    checks = 0
    for edge in manifest['references']:
        if edge['state'] != 'included':
            continue
        source = bundle / edge['from']
        if edge['from'].startswith(str(PurePosixPath(receipt['entry_path']).parent) + '/'):
            source = entry / PurePosixPath(edge['from']).relative_to(PurePosixPath(receipt['entry_path']).parent)
        literal = source.parent / edge['link'].split('#', 1)[0]
        if literal.resolve(strict=True) != bundle / edge['target']:
            raise ClosureError('literal entry reference resolves outside its approved target')
        checks += 1
    return {**receipt, 'status': 'verified-entry', 'entry': str(expected_entry), 'relative_reference_checks': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    build_parser = sub.add_parser('build')
    build_parser.add_argument('--repo', required=True, type=Path)
    build_parser.add_argument('--profile', required=True, type=Path)
    build_parser.add_argument('--output', required=True, type=Path)
    build_parser.add_argument('--enable', action='append', default=[])
    for operation in ['verify', 'verify-entry']:
        check = sub.add_parser(operation)
        check.add_argument('--bundle', required=True, type=Path)
        check.add_argument('--expected-manifest-sha256', required=True)
        check.add_argument('--require-condition', action='append', default=[])
        if operation == 'verify-entry':
            check.add_argument('--entry', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.operation == 'build':
            result = build(args.repo, args.profile, args.output, enabled=args.enable)
        elif args.operation == 'verify':
            result = verify(args.bundle, args.expected_manifest_sha256, required_conditions=args.require_condition)
        else:
            result = verify_entry(args.bundle, args.entry, args.expected_manifest_sha256, required_conditions=args.require_condition)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ClosureError, OSError, UnicodeError) as error:
        print(json.dumps({'status': 'blocked', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
