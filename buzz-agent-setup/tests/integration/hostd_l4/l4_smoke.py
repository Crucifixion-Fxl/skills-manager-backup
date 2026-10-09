#!/usr/bin/env python3
"""Opt-in local-L3 smoke only; this is not the formal 15-binding L4 runbook.

Requires a protected reviewed run manifest, fixed test-bot app and test-group
fixture, local relay, raw ELF buzz and verified approved operator identity.
Missing reaction evidence fails the complete smoke. No default authorization.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import re
import stat
from urllib.parse import urlsplit
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
from hostd.safety import read_owned
NODE = '/home/jchen/.nvm/versions/node/v20.20.1/bin/node'
CLI_ENTRY = '/home/jchen/.npm-global/lib/node_modules/@larksuite/cli/scripts/run.js'
TEST_APPS = frozenset({'cli_aa48bbeeba38dbcf', 'cli_aa48b41531785bfc', 'cli_aa48b406abf8dbe7'})
TEST_GROUPS = frozenset({'hostd-test-群X', 'hostd-test-群W-未绑定'})
PROFILE = 'jchen-personal'
OWNER_APP = 'cli_a940faa4ec381bc4'
OWNER_OPEN = 'ou_755158d120e03b0c18dd4a9334bc3aad'
NOTICE = ('测试尚未验证：旧 smoke 仅支持明确批准的本机 L3 测试。怎么解决：使用受保护的固定测试 manifest、独立测试群/应用、localhost relay 与已核验操作者；正式15绑定请使用另行审核的迁移 runbook。'
          '\n复制给 AI：帮我检查 hostd 本机 L3 测试门禁和完整验收证据；不要读取或输出凭据，不要绕过门禁操作生产绑定。')

class GateError(ValueError): pass

@dataclass(frozen=True)
class AuthorizedRun:
    cfg: dict
    relay_url: str
    user_profile: str
    user_app_id: str
    owner_open_id: str


def _absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts or any(ord(c)<32 for c in value): raise GateError(NOTICE)
    return Path(value)


def _elf(path):
    directory = fd = None
    try:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or not stat.S_IMODE(meta.st_mode)&0o111
                or stat.S_IMODE(meta.st_mode)&0o022 or os.read(fd,4)!=b'\x7fELF'): raise GateError(NOTICE)
    finally:
        if fd is not None: os.close(fd)
        if directory is not None: os.close(directory)


def authorize_run(binding, *, opt_in=False, manifest_path=None):
    # This precedes signer/profile reads and every CLI call.
    if not opt_in or binding != 'hostd-local-l3' or manifest_path is None: raise GateError(NOTICE)
    try:
        path = _absolute(str(manifest_path)); manifest = json.loads(read_owned(path))
        expected = {'version','scope','binding','config_path','fixture_path','test_group','chat_id','channel_id','sync_app_id','relay_url','user_profile','user_app_id','owner_open_id'}
        if (not isinstance(manifest,dict) or set(manifest)!=expected or type(manifest['version']) is not int or manifest['version']!=1
                or manifest['scope']!='hostd-local-l3' or manifest['binding']!=binding
                or manifest['sync_app_id'] not in TEST_APPS or manifest['test_group'] not in TEST_GROUPS
                or manifest['user_profile']!=PROFILE or manifest['user_app_id']!=OWNER_APP or manifest['owner_open_id']!=OWNER_OPEN): raise ValueError
        parts = urlsplit(manifest['relay_url'])
        local = parts.hostname == 'localhost'
        if not local:
            try: local = ipaddress.ip_address(parts.hostname).is_loopback
            except ValueError: local = False
        if (parts.scheme not in ('ws','wss') or not local or parts.username or parts.password or parts.query or parts.fragment
                or parts.port is None or not 1 <= parts.port <= 65535): raise ValueError
        groups = json.loads(read_owned(_absolute(manifest['fixture_path'])))
        if not isinstance(groups,dict) or groups.get(manifest['test_group'])!=manifest['chat_id'] or not re.fullmatch(r'oc_[A-Za-z0-9]+',manifest['chat_id']): raise ValueError
        config_path = _absolute(manifest['config_path'])
        if not config_path.is_relative_to(path.parent) or 'buzz-feishu-sync' in config_path.parts: raise ValueError
        cfg = json.loads(read_owned(config_path))
        signer = _absolute(cfg['people_api']['signer_env_file'])
        if not signer.is_relative_to(path.parent): raise ValueError
        if (cfg['chat_id']!=manifest['chat_id'] or cfg['channel_id']!=manifest['channel_id']
                or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',cfg['channel_id'])
                or cfg['agents'][cfg['desk_pubkey']]['app_id']!=manifest['sync_app_id']): raise ValueError
        _elf(_absolute(cfg['buzz_cli']))
        return AuthorizedRun(cfg,manifest['relay_url'],PROFILE,OWNER_APP,OWNER_OPEN)
    except Exception:
        raise GateError(NOTICE) from None
OUT = Path(__file__).resolve().parents[2] / "fixtures" / "hostd" / "l4"
LIMIT = 60.0  # a big binding's run (thread reads) can take ~20 s before the next event's run starts


def lark_env():
    e = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")}
    e.update(LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", TZ="UTC")
    return e


def js(out: str) -> dict:
    i = out.find("{")
    return json.loads(out[i:]) if i >= 0 else {}


def lark(*args, profile, timeout=40) -> dict:
    if profile != PROFILE or '--profile' in args or any(x.startswith('--profile=') for x in args): raise GateError(NOTICE)
    command = [NODE, CLI_ENTRY, *args, '--profile', profile]
    if args[:2] == ('auth', 'status'):
        # auth status is a read-only diagnostic command, whose installed CLI
        # does not accept the business-command --as flag.
        if '--as' in args or any(x.startswith('--as=') for x in args): raise GateError(NOTICE)
        command += ['--verify', '--json']
    elif '--as' not in args:
        command += ['--as','user']
    elif args[args.index('--as')+1] != 'user': raise GateError(NOTICE)
    result = subprocess.run(command, env=lark_env(), capture_output=True, text=True, timeout=timeout)
    if result.returncode: raise GateError(NOTICE)
    output = js(result.stdout)
    if not isinstance(output,dict) or output.get('ok') is False or output.get('code',0) not in (0,None): raise GateError(NOTICE)
    return output


class Side:
    def __init__(self, authorized: AuthorizedRun):
        if not isinstance(authorized, AuthorizedRun): raise GateError(NOTICE)
        self.authorized = authorized
        self.cfg = authorized.cfg
        # Read back the selected app AND current human before opening signer env.
        status = lark('auth','status',profile=authorized.user_profile)
        user = lark('api','GET','/open-apis/authen/v1/user_info','--as','user',profile=authorized.user_profile)
        identity = user.get('data') or {}
        if (status.get('appId')!=authorized.user_app_id or identity.get('open_id')!=authorized.owner_open_id
                or identity.get('name')!='陈敬敏'): raise GateError(NOTICE)
        env = {k: v for k, v in os.environ.items() if k in ('PATH','HOME')}
        for line in read_owned(self.cfg['people_api']['signer_env_file']).decode().splitlines():
            if '=' in line and not line.startswith('#'):
                k,v = line.split('=',1)
                if k.strip() in {'BUZZ_PRIVATE_KEY','BUZZ_AUTH_TAG','BUZZ_RELAY_URL'}: env[k.strip()] = v.strip().strip('\"').strip("'")
        if env.get('BUZZ_RELAY_URL')!=authorized.relay_url: raise GateError(NOTICE)
        self.buzz_env = env

    def lark(self,*args,**kwargs):
        return lark(*args,profile=self.authorized.user_profile,**kwargs)

    def buzz(self, *args) -> str:
        return subprocess.run([self.cfg["buzz_cli"], *args], env=self.buzz_env, capture_output=True, text=True, timeout=60).stdout

    def buzz_recent(self, n=8) -> list:
        d = json.loads(self.buzz("messages", "get", "--channel", self.cfg["channel_id"], "--limit", str(n)) or "[]")
        return d if isinstance(d, list) else (d.get("messages") or d.get("events") or [])

    def feishu_recent(self) -> list:
        d = self.lark("im", "+chat-messages-list", "--as", "user", "--chat-id", self.cfg["chat_id"], "--order", "desc",
                 "--page-limit", "1", "--no-reactions")
        return (d.get("data") or {}).get("messages") or (d.get("data") or {}).get("items") or []


def wait(pred, limit=LIMIT, step=1.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        v = pred()
        if v:
            return round(time.time() - t0, 1), v
        time.sleep(step)
    return None, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("binding"); ap.add_argument("--agent", action="store_true")
    ap.add_argument('--allow-local-l3',action='store_true');ap.add_argument('--run-manifest',type=Path)
    a = ap.parse_args(argv)
    try: authorized = authorize_run(a.binding,opt_in=a.allow_local_l3,manifest_path=a.run_manifest);s = Side(authorized)
    except Exception:
        print(NOTICE,file=sys.stderr);return 1
    try:
        return smoke(s, a)
    except Exception:
        print(NOTICE, file=sys.stderr)
        return 1


def smoke(s, a):
    tag = f"L4-{a.binding}-{int(time.time())}"
    rec = {"probe": "hostd-l4-smoke", "binding": a.binding, "t": int(time.time()), "checks": {}}
    # 1 Buzz -> Feishu (a person's message becomes a card from the sync bot)
    s.buzz("messages", "send", "--channel", s.cfg["channel_id"], "--content", f"[L4 测试，请忽略] Buzz→飞书 {tag}-B2F")
    lat, hit = wait(lambda: next((m for m in s.feishu_recent() if f"{tag}-B2F" in json.dumps(m, ensure_ascii=False)), None))
    rec["b2f_s"] = lat
    rec["checks"]["b2f_arrived"] = lat is not None
    sync_app = s.cfg["agents"][s.cfg["desk_pubkey"]]["app_id"]
    rec["checks"]["b2f_sent_by_sync_bot"] = bool(hit) and (hit.get("sender") or {}).get("id") == sync_app
    # 2 Feishu -> Buzz (a person's message becomes "[飞书] name：" from the mirror agent)
    sent = s.lark("im", "+messages-send", "--as", "user", "--chat-id", s.cfg["chat_id"], "--text", f"[L4 测试，请忽略] 飞书→Buzz {tag}-F2B")
    fmid = (sent.get("data") or {}).get("message_id")
    lat, ev = wait(lambda: next((e for e in s.buzz_recent() if f"{tag}-F2B" in (e.get("content") or "")), None))
    rec["f2b_s"] = lat
    rec["checks"]["f2b_arrived"] = lat is not None
    rec["checks"]["f2b_by_mirror_agent"] = bool(ev) and ev.get("pubkey") == s.cfg["mirror_pubkey"]
    rec["checks"]["f2b_signed_feishu_name"] = bool(ev) and (ev.get("content") or "").startswith("[飞书]")
    # 3 Feishu reaction on that message -> Buzz kind 7 by the mirror
    rec['checks']['reaction_f2b_arrived'] = False
    rec['reaction_status'] = 'unverified'
    if fmid and s.cfg.get("reaction_sync", "two_way") != "off":
        s.lark("api", "POST", f"/open-apis/im/v1/messages/{fmid}/reactions", "--as", "user", "--data", json.dumps({"reaction_type": {"emoji_type": "OK"}}))
        target = (ev or {}).get("id")
        def reacted():
            d = json.loads(s.buzz("messages", "get", "--channel", s.cfg["channel_id"], "--limit", "15", "--kinds", "7") or "[]") if target else []
            d = d if isinstance(d, list) else (d.get("messages") or d.get("events") or [])
            return next((x for x in d if any(t[:2] == ["e", target] for t in x.get("tags", []))), None)
        lat, _ = wait(reacted)
        rec["reaction_f2b_s"] = lat
        rec["checks"]["reaction_f2b_arrived"] = lat is not None
        rec["reaction_status"] = "verified" if lat is not None else "unverified"
    # 4 optional: @ the Desk in Feishu, its Buzz answer must come back through its own bot in the same thread
    if a.agent:
        r = s.lark("im", "+chat-members-list", "--chat-id", s.cfg["chat_id"], "--as", "user", "--page-all")
        rd = r.get("data") or r
        oid = next((b.get("member_id") for b in rd.get("bots", []) if b.get("app_id") == sync_app), None)
        content = json.dumps({"text": f'<at user_id="{oid}">desk</at> [L4 测试 {tag}] 请只回复两个字：收到'}, ensure_ascii=False)
        q = s.lark("im", "+messages-send", "--as", "user", "--chat-id", s.cfg["chat_id"], "--msg-type", "text", "--content", content)
        qmid = (q.get("data") or {}).get("message_id")
        def answered():
            # Replies in a Feishu thread are not in the chat's top-level list: read the question's thread.
            d = s.lark("im", "+threads-messages-list", "--as", "user", "--thread", qmid, "--order", "desc", "--no-reactions")
            items = (d.get("data") or {}).get("messages") or (d.get("data") or {}).get("items") or []
            return next((m for m in items if m.get("message_id") != qmid and (m.get("sender") or {}).get("id") == sync_app), None)
        lat, ans = wait(answered, limit=240, step=4)
        rec["agent_reply_s"] = lat
        rec["checks"]["agent_reply_in_thread_from_own_bot"] = lat is not None
    rec["failed_checks"] = sorted(k for k, v in rec["checks"].items() if not v)
    rec["ok"] = not rec["failed_checks"]
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location('hostd_probe_receipt',Path(__file__).resolve().parents[2]/'hostd_probes'/'bot_api_probe.py')
    writer = module_from_spec(spec);spec.loader.exec_module(writer)
    if not rec['ok']: rec['notice'] = NOTICE
    writer.write_receipt(OUT / f'{a.binding}.json',rec)
    print(json.dumps(rec, ensure_ascii=False))
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
