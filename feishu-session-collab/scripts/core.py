# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Import-only routes/inboxes; use collab.py --help for the public CLI."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit


def required(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("需要非空文本，不能包含控制字符")
    return value


def record_target(record):
    url = required(record.get("url"))
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError("需要无凭据的 HTTPS task/issue 链接")
    if record.get("kind") == "gitlab_issue" and not p.query:
        project, marker, iid = p.path.strip("/").rpartition("/-/issues/")
        if marker and "/" in project and all(x not in {"", ".", ".."} for x in project.split("/")) and iid.isdigit() and int(iid) > 0:
            return p.netloc, project, iid
    if record.get("kind") == "feishu_task" and p.hostname in {"applink.feishu.cn", "applink.larksuite.com"} and p.path == "/client/todo/task":
        query = parse_qs(p.query)
        guid = query.get("guid", [])
        if len(guid) == 1 and guid[0] and set(query) <= {"guid", "suite_entity_num"}:
            return p.netloc, guid[0], None
    raise ValueError("链接与 task/issue 类型不匹配；请使用记录返回的规范链接")


def build_request(record, recipient, context, needed, expected, request_id, deadline=""):
    record_target(record)
    for value in (recipient, context, needed, expected, request_id):
        required(value)
    if not recipient.startswith("ou_"):
        raise ValueError("收件人必须先唯一解析为 open_id")
    text = f"协作记录：{record['url']}\n背景与当前进展：{context}\n需要你协助：{needed}\n希望回复的内容：{expected}\n"
    if deadline:
        text += f"时间要求：{required(deadline)}\n"
    text += "请直接在上述 task/issue 记录中回复；我不跟踪此私聊的回复。若有阻塞，也请写在记录中。"
    key = hashlib.sha256(json.dumps([record["url"], recipient, request_id, text], ensure_ascii=False).encode()).hexdigest()[:40]
    return {"identity": "user", "recipient": recipient, "text": text, "record": record,
            "context": context, "needed": needed, "expected": expected, "deadline": deadline,
            "request_id": request_id, "idempotency_key": key}


@contextmanager
def app_lock(directory, app_id):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(required(app_id).encode()).hexdigest() + ".lock"
    fd = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("该应用已有本机接收器；请复用它，不要为 session 再建连接") from error
        yield
    finally:
        os.close(fd)


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=DELETE;
          CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, app TEXT, owner TEXT, chat TEXT, root TEXT, thread TEXT, closed INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS aliases (
            app TEXT, chat TEXT, alias TEXT, session TEXT, PRIMARY KEY(app,chat,alias));
          CREATE TABLE IF NOT EXISTS inbox (
            id INTEGER PRIMARY KEY, session TEXT, event_key TEXT, payload TEXT, acked INTEGER DEFAULT 0,
            UNIQUE(session,event_key));
          CREATE TABLE IF NOT EXISTS watches (
            session TEXT, url TEXT, record TEXT, initialized INTEGER DEFAULT 0, PRIMARY KEY(session,url));
          CREATE TABLE IF NOT EXISTS comments (
            session TEXT, url TEXT, id TEXT, digest TEXT, PRIMARY KEY(session,url,id));
        """)

    def close(self):
        self.db.close()

    def session(self, session):
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session,)).fetchone()
        if not row:
            raise ValueError("session 未登记")
        return dict(row)

    def bind(self, session, app, owner, chat, root, thread=""):
        for value in (session, app, owner, chat, root):
            required(value)
        old = self.db.execute("SELECT * FROM sessions WHERE id=?", (session,)).fetchone()
        if old:
            if old["closed"] or tuple(old[k] for k in ("app", "owner", "chat", "root", "thread")) != (app, owner, chat, root, thread):
                raise ValueError("不能覆盖已有 session 身份或话题绑定")
            return
        try:
            with self.db:
                self.db.execute("INSERT INTO sessions(id,app,owner,chat,root,thread) VALUES(?,?,?,?,?,?)", (session, app, owner, chat, root, thread))
                for alias in dict.fromkeys(filter(None, (root, thread))):
                    self.db.execute("INSERT INTO aliases VALUES(?,?,?,?)", (app, chat, alias, session))
        except sqlite3.IntegrityError as error:
            raise ValueError("此话题已绑定另一个 session") from error

    def alias(self, session, message_id):
        row = self.session(session)
        required(message_id)
        old = self.db.execute("SELECT session FROM aliases WHERE app=? AND chat=? AND alias=?", (row["app"], row["chat"], message_id)).fetchone()
        if old and old[0] != session:
            raise ValueError("消息已属于另一个 session")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?,?,?)", (row["app"], row["chat"], message_id, session))

    def finish(self, session):
        self.session(session)
        with self.db:
            self.db.execute("UPDATE sessions SET closed=1 WHERE id=?", (session,))

    def enqueue(self, session, key, payload):
        cursor = self.db.execute("INSERT OR IGNORE INTO inbox(session,event_key,payload) VALUES(?,?,?)", (session, key, json.dumps(payload, ensure_ascii=False)))
        return "queued" if cursor.rowcount else "duplicate"

    def route(self, app, event):
        if event.get("type") != "im.message.receive_v1" or event.get("chat_type") != "p2p" or event.get("sender_type") != "user":
            return "ignored"
        allowed = self.db.execute("SELECT id FROM sessions WHERE app=? AND chat=? AND owner=?", (app, event.get("chat_id"), event.get("sender_id"))).fetchall()
        if not allowed:
            return "ignored"
        if event.get("message_type", "text") not in {"text", "post"}:
            return "unsupported"
        keys = [event.get(k) for k in ("root_id", "thread_id", "reply_to") if event.get(k)]
        matches = set()
        for key in keys:
            row = self.db.execute("SELECT session FROM aliases WHERE app=? AND chat=? AND alias=?", (app, event["chat_id"], key)).fetchone()
            if row:
                matches.add(row[0])
            elif key == event.get("root_id") or (key == event.get("thread_id") and not matches):
                return "unbound"
        if len(matches) > 1:
            return "ambiguous"
        if not matches:
            return "unbound"
        session = matches.pop()
        binding = self.session(session)
        if binding["owner"] != event["sender_id"]:
            return "ignored"
        if binding["closed"]:
            return "closed"
        message = required(event.get("message_id"))
        body = required(event.get("content"))
        with self.db:
            result = self.enqueue(session, "im:" + app + ":" + message, {"kind": "owner_message", "body": body, "message_id": message, "author": event["sender_id"]})
            self.alias(session, message)
            if event.get("thread_id"):
                self.alias(session, event["thread_id"])
        return result

    def inbox(self, session):
        self.session(session)
        return [dict(json.loads(r["payload"]), id=r["id"]) for r in self.db.execute("SELECT * FROM inbox WHERE session=? AND acked=0 ORDER BY id", (session,))]

    def ack(self, session, entry):
        with self.db:
            cursor = self.db.execute("UPDATE inbox SET acked=1 WHERE id=? AND session=?", (entry, session))
            if not cursor.rowcount:
                raise ValueError("收件记录不属于此 session")

    def watch(self, session, record):
        if self.session(session)["closed"]:
            raise ValueError("session 已关闭")
        record_target(record)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO watches(session,url,record) VALUES(?,?,?)", (session, record["url"], json.dumps(record)))

    def watches(self, session):
        return [json.loads(r[0]) for r in self.db.execute("SELECT record FROM watches WHERE session=?", (session,))]

    def snapshot(self, session, url, comments):
        row = self.db.execute("SELECT initialized FROM watches WHERE session=? AND url=?", (session, url)).fetchone()
        if not row or self.session(session)["closed"]:
            raise ValueError("记录未绑定，或 session 已关闭")
        for comment in comments:
            for k in ("id", "body", "author", "version"):
                required(comment.get(k))
        with self.db:
            for c in comments:
                digest = hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()
                old = self.db.execute("SELECT digest FROM comments WHERE session=? AND url=? AND id=?", (session, url, c["id"])).fetchone()
                if row[0] and (not old or old[0] != digest):
                    self.enqueue(session, "comment:" + url + ":" + c["id"] + ":" + digest,
                                 {"kind": "record_context", "body": c["body"], "author": c["author"], "comment_id": c["id"], "version": c["version"], "record_url": url})
                self.db.execute("INSERT OR REPLACE INTO comments VALUES(?,?,?,?)", (session, url, c["id"], digest))
            self.db.execute("UPDATE watches SET initialized=1 WHERE session=? AND url=?", (session, url))
        return "updated" if row[0] else "baseline"
