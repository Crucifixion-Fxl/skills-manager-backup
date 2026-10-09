"""Open and verify the local console without handing credentials to the user."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile

from .console_access import AccessError, ConsoleAccess, NOTICE, _open
from .console_browser import BrowserPlan, OwnedBrowser, _finish, _program


async def verify(browser, access):
    """Read the rendered page and graph, returning only nonsensitive evidence."""
    expression = """(async()=>{
      if(location.href!==EXPECTED_URL || document.readyState==='loading')return null;
      if(document.title!=='本机同步控制台' || !document.querySelector('#graph'))return null;
      const response=await fetch('/api/graph',{credentials:'omit',cache:'no-store',
        signal:AbortSignal.timeout(1500)});
      if(response.status!==200)return null;
      const data=await response.json();
      if(!Array.isArray(data.nodes)||!Array.isArray(data.edges)||data.notice)return null;
      if(document.querySelector('#access')?.textContent!=='已读取本机状态。')return null;
      return {page_verified:true,graph_verified:true,nodes:data.nodes.length,edges:data.edges.length};
    })()""".replace('EXPECTED_URL', json.dumps(access.origin + '/'))
    for _ in range(20):
        access._validate()
        if browser._event_failed:
            raise AccessError()
        targets = await browser._send(None, 'Target.getTargets', {})
        for target in targets.get('targetInfos', []):
            if target.get('type') != 'page' or target.get('url') != access.origin + '/':
                continue
            session = await browser._ready_target(target['targetId'])
            result = await browser._send(session, 'Runtime.evaluate', {
                'expression': expression, 'returnByValue': True, 'awaitPromise': True})
            value = result.get('result', {}).get('value')
            if (isinstance(value, dict) and value.get('page_verified') is True
                    and value.get('graph_verified') is True
                    and all(type(value.get(key)) is int and 0 <= value[key] <= 1000000
                            for key in ('nodes', 'edges'))):
                if browser._event_failed:
                    raise AccessError()
                access._validate()
                return {key: value[key] for key in ('page_verified', 'graph_verified', 'nodes', 'edges')}
        await asyncio.sleep(.1)
    raise AccessError()


def chrome_digest(binary):
    # Validate the supplied path before opening it for the digest. _program
    # performs no-follow traversal, ownership/mode and ELF checks again below.
    binary = Path(binary)
    if not binary.is_absolute() or '..' in binary.parts:
        raise AccessError()
    fd = os.open(binary.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(binary.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            if index < len(binary.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        import stat
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid not in (0, os.geteuid())
                or meta.st_mode & 0o022 or meta.st_size > 512 * 1024 * 1024):
            raise AccessError()
        with os.fdopen(os.dup(fd), 'rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        _program(binary, digest)
        return digest
    finally:
        os.close(fd)


async def launch(args):
    runtime = Path(args.runtime_dir)
    fd = _open(runtime, directory=True)
    os.close(fd)
    digest = chrome_digest(args.chrome_binary)
    client = Path(tempfile.mkdtemp(prefix='console-client-', dir=runtime))
    identity = client.stat().st_dev, client.stat().st_ino
    access = browser = None
    signals = []
    loop = asyncio.get_running_loop()
    try:
        access = ConsoleAccess.check(runtime, client, request_timeout=10)
        await access.start()
        plan = BrowserPlan.check(access, chrome_binary=args.chrome_binary, chrome_sha256=digest,
                                 profile_dir=client / 'chrome-profile')
        environment = {'DISPLAY': args.display}
        if args.xauthority:
            environment['XAUTHORITY'] = args.xauthority
        browser = OwnedBrowser(plan, browser_environment=environment)
        await browser.start()
        evidence = await asyncio.wait_for(verify(browser, access), 15)
        print(json.dumps(dict(status='verified', url=access.origin + '/',
                              access='current_owned_browser_only',
                              lifetime='closed_after_verification' if args.verify_only else 'until_launcher_stops',
                              **evidence), ensure_ascii=False), flush=True)
        if not args.verify_only:
            stopped = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stopped.set)
                signals.append(sig)
            await stopped.wait()
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        async def cleanup():
            try:
                if browser is not None:
                    await browser.close()
            finally:
                if access is not None:
                    await access.close()
            # Keep a private profile if browser ownership could not be proved
            # during cleanup; never delete an in-use or replaced directory.
            fd = _open(client, directory=True)
            try:
                meta = os.fstat(fd)
                if (meta.st_dev, meta.st_ino) != identity:
                    raise AccessError()
            finally:
                os.close(fd)
            shutil.rmtree(client)
        await _finish(cleanup())


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise AccessError()
    parser = Parser(description='在隔离 Chrome 中打开并验证本机 hostd Console；Ctrl-C 关闭。')
    parser.add_argument('--runtime-dir', default=f'/run/user/{os.geteuid()}/buzz-hostd')
    parser.add_argument('--chrome-binary', default='/opt/google/chrome/chrome')
    parser.add_argument('--display', required=True, help='已确认的本机显示器，例如 :0')
    parser.add_argument('--xauthority')
    parser.add_argument('--verify-only', action='store_true', help='只读验证后关闭，不保留访问入口')
    try:
        args = parser.parse_args(argv)
        if not args.display:
            raise AccessError()
        asyncio.run(launch(args))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(NOTICE, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
