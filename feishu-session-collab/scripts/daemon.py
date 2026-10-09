#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["websocket-client>=1.8,<2"]
# ///
"""One local receiver plus automatic wiring of loaded interactive Codex sessions."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from bridge import Codex, deliver, public_items, recent_turns, start_queued
from core import Store, app_lock
from records import poll_record
from transport import Lark
from wire import Wire, interactive, redact


def log(status, **fields):
    print(json.dumps(dict(status=status, **fields), ensure_ascii=False), flush=True)


def verify_bot(lark):
    lark.verify_user()
    info = lark.call(['api','GET','/open-apis/bot/v3/info'], 'bot')
    info = info.get('bot',info)
    if info.get('open_id') != lark.config['bot_id'] or info.get('activate_status') != 2:
        raise ValueError('当前应用 bot 身份或启用状态不匹配')


def completed_ids(turns, notifications=()):
    ids = set()
    for turn in turns:
        items = turn.get('items',[])
        for index,item in enumerate(items):
            if item.get('type') == 'agentMessage' and (turn.get('status') in {'completed','failed','interrupted'} or index < len(items)-1):
                ids.add(item['id'])
    ids.update(n['params']['item']['id'] for n in notifications if n.get('method') == 'item/completed' and n.get('params',{}).get('item',{}).get('type') == 'agentMessage')
    return ids


def recovery_ids(turns):
    return {agents[-1]['id'] for turn in turns if (agents := [i for i in turn.get('items',[]) if i.get('type') == 'agentMessage'])}


def disconnect(home, clients, present):
    client = clients.pop(home,None)
    present.pop(home,None)
    if client:
        try: client.close()
        except Exception: pass


def sync_bound(store, wire, client, meta, turns, notifications, lark, last_polls):
    session = meta['id']
    if store.session(session)['closed']:
        return
    def failure(phase,error):
        log('session_error',session=session,phase=phase,error=redact(str(error))[:350])
    # Independent stages: an unknown outbound result must not prevent owner input
    # or record comments from reaching their existing Codex session.
    if time.monotonic() - last_polls.get(session,0) >= 30:
        watches = store.watches(session)
        try:
            if watches: lark.verify_user()
            for record in watches:
                try: poll_record(store,session,record,lark)
                except Exception as error: failure('record_poll',error)
            last_polls[session] = time.monotonic()
        except Exception as error: failure('record_identity',error)
    for entry in store.inbox(session):
        try:
            deliver(store,client,session,entry)
            log('queued',session=session,inbox_id=entry['id'])
        except Exception as error: failure('inbound_queue',error)
    try: start_queued(store,client,meta)
    except Exception as error: failure('queue_start',error)
    items = [n['params']['item'] for n in notifications if n.get('method') == 'item/completed' and n.get('params',{}).get('threadId') == session]
    items += public_items([t for t in turns if t.get('status') in {'completed','failed','interrupted'}])
    for item in items:
        try: wire.mirror(session,item)
        except Exception as error: failure('outbound_mirror',error)


def run(config_path, db_path, once=False):
    os.umask(0o077)
    config = json.loads(Path(config_path).read_text())
    lark = Lark(config)
    if not config.get('bot_transport_approved') or not config.get('bot_id'):
        raise ValueError('运行需要明确授权的主人 bot 身份')
    verify_bot(lark)
    store = Store(db_path)
    wire = Wire(store,lark)
    clients, present, epochs, last_polls = {}, {}, {}, {}
    stop = False
    def stopping(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    receiver = None
    with app_lock(Path(db_path).parent / 'locks', config['app_id'] + ':bridge'):
        try:
            if not once:
                receiver = subprocess.Popen([sys.executable, str(Path(__file__).with_name('collab.py')), '--db',db_path,'--config',config_path,'listen'])
            while not stop:
                if receiver and receiver.poll() is not None:
                    raise RuntimeError('上游接收器退出；停止桥接，交由服务管理器重启')
                count = 0
                for home in config['codex_homes']:
                    home = str(Path(home).resolve())
                    try:
                        if home not in clients:
                            clients[home] = Codex(home)
                        client = clients[home]
                        ids = set(client.pages('thread/loaded/list', {'limit':100}))
                        metas = []
                        for session in sorted(ids):
                            meta = client.call('thread/read', {'threadId':session,'includeTurns':False})['thread']
                            if interactive(meta):
                                metas.append(meta)
                        live = {m['id'] for m in metas}
                        resumed = set()
                        notifications = list(client.notifications)
                        client.notifications.clear()
                        for n in notifications:
                            if n['method'] == 'thread/started':
                                t = n.get('params', {}).get('thread', {})
                                if t.get('id') in live:
                                    resumed.add(t['id'])
                        for meta in metas:
                            session = meta['id']; count += 1
                            try:
                                turns = recent_turns(client,meta)
                                items = public_items(turns)
                                if session not in present.get(home,set()) or session in resumed or (home,session) in epochs:
                                    epochs.setdefault((home,session),str(uuid.uuid4()))
                                    try:
                                        done = completed_ids(turns,[n for n in notifications if n.get('params',{}).get('threadId') == session])
                                        wire.hello(meta,items,home,epochs[(home,session)],completed_ids=done,recovery_ids=recovery_ids(turns))
                                        log('wired',session=session,home=home)
                                        epochs.pop((home,session),None)
                                    except Exception as error:
                                        log('session_error',session=session,phase='hello',error=redact(str(error))[:350])
                                sync_bound(store,wire,client,meta,turns,notifications,lark,last_polls)
                            except Exception as error:
                                log('session_error',session=session,error=redact(str(error))[:350])
                        for gone in present.get(home,set()) - live:
                            epochs.pop((home,gone),None)
                        present[home] = live
                    except Exception as error:
                        log('home_error',home=home,error=redact(str(error))[:350])
                        disconnect(home,clients,present)
                log('scan_complete',interactive_sessions=count)
                if once:
                    return
                for _ in range(100):
                    if stop: break
                    time.sleep(0.1)
        finally:
            if receiver:
                receiver.terminate()
                try: receiver.wait(timeout=15)
                except subprocess.TimeoutExpired: receiver.kill();receiver.wait(timeout=5)
            for client in clients.values(): client.close()
            store.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    p.add_argument('--db',default=str(Path.home()/'.local/state/feishu-session-collab/state.sqlite'))
    p.add_argument('--once',action='store_true',help='仅接入目前运行的 session；不启动接收器')
    a = p.parse_args()
    run(a.config,a.db,a.once)
