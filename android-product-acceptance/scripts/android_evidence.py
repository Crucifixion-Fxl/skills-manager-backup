#!/usr/bin/env python3
"""Read-only Android preflight and evidence capture. Python standard library only."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET


def now():
    return dt.datetime.now().astimezone().isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def locate_adb(explicit):
    candidates = [explicit, shutil.which('adb')]
    for key in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        if os.environ.get(key):
            candidates.append(str(Path(os.environ[key]) / 'platform-tools/adb'))
    candidates.append(str(Path.home() / 'Library/Android/sdk/platform-tools/adb'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError('ADB unavailable; set --adb or install Android platform-tools.')


def call(adb, args, binary=False):
    result = subprocess.run([adb, *args], capture_output=True, timeout=30)
    if result.returncode:
        # Do not forward arbitrary device data or commands in errors.
        raise RuntimeError('ADB operation failed; inspect device authorization/connection.')
    return result.stdout if binary else result.stdout.decode('utf-8', errors='replace').strip()


def select_device(adb, requested):
    devices = []
    for line in call(adb, ['devices', '-l']).splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] in ('device', 'offline', 'unauthorized'):
            devices.append({'serial': fields[0], 'state': fields[1]})
    if requested:
        matches = [d for d in devices if d['serial'] == requested and d['state'] == 'device']
    else:
        matches = [d for d in devices if d['state'] == 'device']
    if len(matches) != 1:
        raise RuntimeError('Need exactly one authorized target device; connect/unlock it or specify --serial.')
    return matches[0]['serial']


def probe(adb, serial):
    def sh(*args):
        return call(adb, ['-s', serial, 'shell', *args])
    return {'status': 'ready', 'host_time': now(), 'serial': serial,
            'model': sh('getprop', 'ro.product.model'),
            'android_release': sh('getprop', 'ro.build.version.release'),
            'sdk': sh('getprop', 'ro.build.version.sdk'),
            'device_time': sh('date', '-Iseconds')}


def sanitize_ui(root):
    nodes = []
    sensitive = False
    # Password descendants are masked even if an ancestor alone carries the flag.
    def visit(node, inherited=False):
        nonlocal sensitive
        secret = inherited or node.get('password') == 'true'
        sensitive |= secret
        attrs = {key: node.get(key, '') for key in
                 ('text', 'content-desc', 'resource-id', 'class', 'bounds',
                  'clickable', 'enabled', 'checked', 'selected', 'password')}
        if secret:
            attrs['text'] = '[MASKED]'
            attrs['content-desc'] = '[MASKED]'
        if node.tag == 'node':
            nodes.append(attrs)
        for child in node:
            visit(child, secret)
    visit(root)
    return nodes, sensitive


def capture(adb, serial, prefix):
    prefix = Path(prefix)
    ui_path = Path(str(prefix) + '.ui.json')
    png_path = Path(str(prefix) + '.png')
    if ui_path.exists() or png_path.exists():
        raise RuntimeError('Evidence path already exists; choose a new step/run prefix.')
    remote = '/sdcard/acceptance-' + uuid.uuid4().hex + '.xml'
    base = ['-s', serial, 'shell']
    try:
        call(adb, [*base, 'uiautomator', 'dump', remote])
        root = ET.fromstring(call(adb, [*base, 'cat', remote]))
        nodes, sensitive = sanitize_ui(root)
        data = {'host_time': now(), 'serial': serial, 'nodes': nodes,
                'screenshot': None, 'status': 'screenshot_blocked_password_field' if sensitive else 'captured'}
        if not sensitive:
            png = call(adb, ['-s', serial, 'exec-out', 'screencap', '-p'], binary=True)
            if not png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise RuntimeError('Device did not return a PNG screenshot.')
            png_path.parent.mkdir(parents=True, exist_ok=True)
            png_path.write_bytes(png)
            data['screenshot'] = str(png_path.resolve())
        write_json(ui_path, data)
        return {'status': data['status'], 'ui': str(ui_path.resolve()), 'screenshot': data['screenshot']}
    finally:
        try:
            call(adb, [*base, 'rm', '-f', remote])
        except (RuntimeError, subprocess.TimeoutExpired):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', help='Path to adb')
    parser.add_argument('--serial', help='Explicit target device')
    subs = parser.add_subparsers(dest='action', required=True)
    for action in ('probe', 'capture'):
        p = subs.add_parser(action)
        p.add_argument('--out', required=True, help='JSON path for probe; fresh filename prefix for capture')
    args = parser.parse_args()
    try:
        adb = locate_adb(args.adb)
        serial = select_device(adb, args.serial)
        if args.action == 'probe':
            if Path(args.out).exists():
                raise RuntimeError('Preflight path exists; use a fresh run path.')
            result = probe(adb, serial)
            write_json(args.out, result)
        else:
            result = capture(adb, serial, args.out)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result['status'] == 'screenshot_blocked_password_field' else 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        result = {'status': 'blocked', 'host_time': now(), 'reason': str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
