#!/usr/bin/env python3
"""Source evidence for Payload collections; never executes a login or API call."""
import argparse
import hashlib
import json
import os
import stat
import re
import subprocess
from pathlib import Path


class SourceReader:
    """Canonical caller-selected root; no symlinks below it, even within root.

    Walk via directory descriptors so path replacement cannot redirect a read.
    Nonblocking opens and fstat reject FIFOs/devices before reading. Hashes use
    the same bytes as source inspection, rather than reopening source paths.
    """
    def __init__(self, source_root):
        self.root = source_root.resolve(strict=True)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.contents = {}

    def close(self):
        os.close(self.fd)

    def open(self, path, *, directory=False, optional=False):
        try:
            parts = path.relative_to(self.root).parts
        except ValueError as error:
            raise ValueError('non-repository source') from error
        if not parts or any(part in ('.', '..') for part in parts):
            raise ValueError('non-repository source')
        parent = os.dup(self.fd)
        try:
            for index, part in enumerate(parts):
                is_directory = index < len(parts) - 1 or directory
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if is_directory:
                    flags |= os.O_DIRECTORY
                try:
                    info = os.stat(part, dir_fd=parent, follow_symlinks=False)
                    expected = stat.S_ISDIR if is_directory else stat.S_ISREG
                    if not expected(info.st_mode):
                        raise ValueError('non-repository source: symlink or special file')
                    child = os.open(part, flags, dir_fd=parent)
                except FileNotFoundError:
                    if optional:
                        return None
                    raise ValueError('missing repository source')
                except OSError as error:
                    raise ValueError('non-repository source: unsafe path') from error
                if not expected(os.fstat(child).st_mode):
                    os.close(child)
                    raise ValueError('non-repository source: special file')
                os.close(parent)
                parent = child
            result = parent
            parent = None
            return result
        finally:
            if parent is not None:
                os.close(parent)

    def read(self, path, *, optional=False):
        fd = self.open(path, optional=optional)
        if fd is None:
            return None
        with os.fdopen(fd, 'rb') as source:
            body = source.read()
        self.contents[path] = body
        return body.decode('utf-8')

    def files(self, path, *, optional=False):
        fd = self.open(path, directory=True, optional=optional)
        if fd is None:
            return []
        try:
            return sorted(path / name for name in os.listdir(fd) if name.endswith('.ts'))
        finally:
            os.close(fd)


def inspect(source_root):
    reader = SourceReader(source_root)
    try:
        return _inspect(reader)
    finally:
        reader.close()


def _inspect(reader):
    root = reader.root
    config = root / 'src/payload.config.ts'
    text = reader.read(config)
    registered = re.search(r'^  collections: \[(.*?)^  \],', text, re.S | re.M)
    if not registered:
        raise ValueError('explicit registered collections required')
    names = re.sub(r'//[^\n]*', '', registered.group(1))
    names = [name.strip() for name in names.split(',') if name.strip()]
    if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', name) for name in names):
        raise ValueError('dynamic collections require manual source review')
    sources = {config}
    for candidate in ['src/lib/feishuAuthStrategy.ts', 'src/app/auth/callback/route.ts']:
        path = root / candidate
        if reader.read(path, optional=True) is not None:
            sources.add(path)
    collections = []
    for name in names:
        match = re.search(r"import\s*\{\s*" + name + r"\s*\}\s*from\s*['\"](\./collections/[^'\"]+)['\"]", text)
        if not match:
            raise ValueError(f'unresolved collection import: {name}')
        location = config.parent / match.group(1)
        single = location.with_suffix('.ts')
        files = [single] if reader.read(single, optional=True) is not None else reader.files(location)
        slugs = set()
        for file in files:
            sources.add(file)
            slugs.update(re.findall(r"^  slug:\s*['\"]([a-z][a-z0-9-]+)['\"]", reader.read(file), re.M))
        if len(slugs) != 1:
            raise ValueError(f'ambiguous or missing slug: {name}')
        collections.append({'name': name, 'slug': next(iter(slugs)),
                            'sources': [str(p.relative_to(root)) for p in files],
                            'status': 'source-verified', 'runtimeVerification': 'unknown', 'permissionVerification': 'unknown',
                            'actionCandidates': ['list', 'get', 'create', 'update', 'delete'],
                            'strategy': 'native-payload-rest', 'secretResponse': name == 'APIKeys'})
    endpoints = []
    for file in reader.files(root / 'src/endpoints', optional=True):
        body = reader.read(file)
        for match in re.finditer(r"path:\s*['\"]([^'\"]+)['\"],\s*method:\s*['\"](get|post|patch|delete)['\"]", body):
            sources.add(file)
            endpoints.append({'path': '/api' + match.group(1), 'method': match.group(2).upper(),
                              'source': str(file.relative_to(root)), 'status': 'source-verified',
                              'runtimeVerification': 'unknown', 'permissionVerification': 'unknown', 'strategy': 'native-owner-contract',
                              'environmentGate': 'runtime configuration and owner Skill authorization'})
    digest = {str(p.relative_to(root)): hashlib.sha256(reader.contents[p]).hexdigest() for p in sorted(sources)}
    try:
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    except subprocess.CalledProcessError:
        commit = None
    return {'schemaVersion': 1, 'framework': 'payload', 'scope': 'registered collection CRUD candidates and source endpoint candidates; UI actions, roles and deployment gates require separate verification',
            'sourceCommit': commit, 'sourceFiles': digest, 'sourceDigest': hashlib.sha256(json.dumps(digest, sort_keys=True).encode()).hexdigest(),
            'officialDocs': ['https://payloadcms.com/docs/rest-api/overview', 'https://payloadcms.com/docs/authentication/operations'],
            'collections': collections, 'endpoints': endpoints}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'collections': len(result['collections']), 'endpointCandidates': len(result['endpoints']), 'sourceDigest': result['sourceDigest']}))


if __name__ == '__main__':
    main()
