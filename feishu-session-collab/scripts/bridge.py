#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["websocket-client>=1.8,<2"]
# ///
"""Codex local Unix WebSocket adapter. Never starts or restarts app servers."""
import collections
import json
from pathlib import Path
import socket
import time
from wire import delivery_text


class Codex:
    def __init__(self, home):
        import websocket
        self.home = str(Path(home).resolve())
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(10)
        try:
            sock.connect(self.home + '/app-server-control/app-server-control.sock')
            self.ws = websocket.create_connection('ws://localhost/', socket=sock, timeout=10)
        except Exception:
            sock.close()
            raise
        self.seq = 0
        self.notifications = collections.deque()
        try:
            info = self.call('initialize', {'clientInfo': {'name':'feishu_session_collab', 'version':'0.2.0'}, 'capabilities':{'experimentalApi':True}})
            if str(Path(info['codexHome']).resolve()) != self.home:
                raise ValueError('Codex 控制连接返回了其他 home')
            self.ws.send(json.dumps({'method':'initialized'}))
        except Exception:
            self.close()
            raise

    def close(self):
        self.ws.close()

    def call(self, method, params):
        self.seq += 1
        seq = self.seq
        self.ws.send(json.dumps({'id':seq, 'method':method, 'params':params}))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.ws.settimeout(max(0.1, deadline - time.monotonic()))
            raw = self.ws.recv()
            if not raw:
                raise RuntimeError('Codex 控制连接已断开')
            response = json.loads(raw)
            if response.get('id') == seq:
                if 'error' in response:
                    raise RuntimeError('Codex API: ' + str(response['error'].get('message', '调用失败')))
                return response['result']
            public_complete = response.get('method') == 'item/completed' and response.get('params', {}).get('item', {}).get('type') == 'agentMessage'
            if 'id' not in response and (response.get('method') == 'thread/started' or public_complete):
                self.notifications.append(response)
        raise RuntimeError('Codex 控制接口超时')

    def pages(self, method, params):
        cursor = None
        seen = set()
        for _ in range(1000):
            data = self.call(method, dict(params, **({'cursor':cursor} if cursor else {})))
            yield from data['data']
            cursor = data.get('nextCursor')
            if not cursor:
                return
            if cursor in seen:
                raise RuntimeError('Codex 分页游标重复')
            seen.add(cursor)
        raise RuntimeError('Codex 分页未完整完成')


def public_items(turns):
    return [item for turn in reversed(turns) for item in turn.get('items', []) if item.get('type') in {'userMessage','agentMessage'}]


def recent_turns(codex, meta):
    try:
        return codex.call('thread/turns/list', {'threadId':meta['id'],'limit':3,'itemsView':'full','sortDirection':'desc'})['data']
    except RuntimeError as error:
        if not meta.get('preview') and 'not materialized yet' in str(error):
            return []
        raise


def deliver(store, codex, session, entry):
    client = 'feishu-inbox-' + str(entry['id'])
    content = [{'type':'text', 'text':delivery_text(entry), 'text_elements':[]}]
    row = store.db.execute('SELECT * FROM deliveries WHERE entry=?', (entry['id'],)).fetchone()
    if row and row['status'] == 'accepted':
        store.ack(session, entry['id'])
        return
    if row:
        if row['session'] != session or row['client'] != client:
            raise ValueError('收件箱投递身份冲突')
        entries = list(codex.pages('thread/queue/list', {'threadId':session, 'limit':100})) if hasattr(codex, 'pages') else codex.call('thread/queue/list', {'threadId':session})['data']
        queued = next((q for q in entries if q.get('clientUserMessageId') == client), None)
        if not queued:
            raise RuntimeError('此前队列投递结果未知；保留收件箱，不自动重投')
    else:
        with store.db:
            store.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)', (entry['id'], session, client, 'attempting', '', 0))
        queued = codex.call('thread/queue/add', {'threadId':session, 'clientUserMessageId':client, 'input':content})['queuedSubmission']
    if queued.get('clientUserMessageId') != client or queued.get('input') != content or not queued.get('id'):
        raise ValueError('Codex 队列回执内容或客户端消息 ID 不匹配')
    with store.db:
        store.db.execute('UPDATE deliveries SET status=?,submission=? WHERE entry=?', ('accepted',queued['id'],entry['id']))
        store.ack(session, entry['id'])


def start_queued(store, codex, meta):
    if meta.get('status', {}).get('type') != 'idle':
        return
    row = store.db.execute("SELECT * FROM deliveries WHERE session=? AND status='accepted' AND started=0 ORDER BY entry LIMIT 1", (meta['id'],)).fetchone()
    if not row:
        return
    # If the frontend already consumed it, never recreate or restart its turn.
    queued = list(codex.pages('thread/queue/list', {'threadId':meta['id'],'limit':100})) if hasattr(codex,'pages') else codex.call('thread/queue/list', {'threadId':meta['id']})['data']
    if any(q.get('id') == row['submission'] for q in queued):
        result = codex.call('thread/queue/start', {'threadId':meta['id'],'queuedSubmissionId':row['submission']})
        if not result.get('turn', {}).get('id'):
            raise RuntimeError('队列启动缺少 turn 回执')
    with store.db:
        store.db.execute('UPDATE deliveries SET started=1 WHERE entry=?', (row['entry'],))
