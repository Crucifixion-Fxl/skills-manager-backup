#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["websocket-client>=1.8,<2"]
# ///
"""Local CLI for the Feishu collaboration skill. JSON input/output; no shell eval."""
import argparse
import json
from pathlib import Path
import sys
import time
from core import Store, build_request
from listener import listen, stream_events
from records import poll_record
from transport import Lark, send_request


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def read_json(path):
    return json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text())


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(Path.home() / ".local/state/feishu-session-collab/state.sqlite"))
    p.add_argument("--config", help="批准的 CLI/profile/主人配置 JSON；不能包含凭据")
    subs = p.add_subparsers(dest="command", required=True)
    b = subs.add_parser("bind", help="登记已通过 bot 发送并回读的 session 根消息")
    for field in ("session", "app", "owner", "chat", "root"):
        b.add_argument("--" + field, required=True)
    b.add_argument("--thread", default="")
    w = subs.add_parser("wire", help="接入一个已加载的 Codex session；默认发送当前 recap hello")
    w.add_argument("--session", required=True)
    w.add_argument("--home", required=True, help="已批准的 Codex home")
    w.add_argument("--epoch", help="同一次接入的稳定 ID；重试沿用，重新接入用新 ID")
    a = subs.add_parser("alias", help="登记 bot 在原话题中的后续消息 ID")
    a.add_argument("--session", required=True)
    a.add_argument("--message", required=True)
    for command in ("finish", "inbox", "ack", "watch", "poll", "send-request"):
        c = subs.add_parser(command)
        c.add_argument("--session", required=True)
        if command == "inbox":
            c.add_argument("--wait", type=float, default=0, help="最多等待 60 秒，持续等待由宿主多轮调用")
        if command == "ack":
            c.add_argument("--id", type=int, required=True)
        if command in {"watch", "send-request"}:
            c.add_argument("--input", default="-", help="JSON 文件或 stdin")
    for command in ("request", "listen", "replay"):
        c = subs.add_parser(command)
        if command == "request":
            c.add_argument("--input", default="-")
        if command == "replay":
            c.add_argument("--app", required=True)
    return p


def run(args):
    if args.command == "request":
        return build_request(**read_json(args.input))
    store = Store(args.db)
    try:
        if args.command == "bind":
            store.bind(args.session, args.app, args.owner, args.chat, args.root, args.thread)
            return {"status": "bound", "session": args.session}
        if args.command == "alias":
            store.alias(args.session, args.message)
            return {"status": "registered"}
        if args.command == "finish":
            store.finish(args.session)
            return {"status": "closed"}
        if args.command == "ack":
            store.ack(args.session, args.id)
            return {"status": "acked"}
        if args.command == "inbox":
            if not 0 <= args.wait <= 60:
                raise ValueError("单次等待必须在 0 到 60 秒之间")
            until = time.monotonic() + args.wait
            while True:
                entries = store.inbox(args.session)
                if entries or time.monotonic() >= until:
                    return {"entries": entries}
                time.sleep(min(0.25, max(0, until - time.monotonic())))
        if args.command == "watch":
            record = read_json(args.input)
            store.watch(args.session, record)
            return {"status": "registered", "hint": "运行 poll 建立成功基线后再发送协作通知"}
        if args.command == "replay":
            for status in stream_events(store, args.app, sys.stdin):
                emit({"status": status})
            return {"status": "replayed"}
        config = read_json(args.config) if args.config else None
        if args.command == "poll":
            records = store.watches(args.session)
            lark = Lark(config) if any(r["kind"] == "feishu_task" for r in records) else None
            if lark:
                lark.verify_user()
            statuses = []
            for record in records:
                try:
                    statuses.append({"url": record["url"], "status": poll_record(store, args.session, record, lark)})
                except (RuntimeError, ValueError, KeyError) as error:
                    statuses.append({"url": record["url"], "status": "failed", "hint": str(error)})
            return {"records": statuses, "ok": all(r["status"] != "failed" for r in statuses)}
        lark = Lark(config)
        if args.command == "wire":
            from bridge import Codex, public_items, recent_turns
            from daemon import verify_bot, completed_ids, recovery_ids
            from wire import Wire, interactive
            import uuid
            home = str(Path(args.home).resolve())
            if home not in [str(Path(h).resolve()) for h in config.get("codex_homes", [])]:
                raise ValueError("Codex home 未在接入配置中")
            verify_bot(lark)
            client = Codex(home)
            try:
                meta = client.call("thread/read", {"threadId": args.session, "includeTurns": False})["thread"]
                if not interactive(meta):
                    raise ValueError("只接入已加载、可直接接收主人输入的主 session")
                turns = recent_turns(client, meta)
                message = Wire(store, lark).hello(meta, public_items(turns), home, args.epoch or str(uuid.uuid4()), completed_ids=completed_ids(turns), recovery_ids=recovery_ids(turns))
                return {"status": "read_back", "session": args.session, **message}
            finally:
                client.close()
        if args.command == "listen":
            listen(store, lark)
            return {"status": "stopped"}
        if args.command == "send-request":
            plan = read_json(args.input)
            row = store.db.execute("SELECT initialized FROM watches WHERE session=? AND url=?", (args.session, plan["record"]["url"])).fetchone()
            if not row or not row[0] or store.session(args.session)["closed"]:
                raise ValueError("发送前须对该 session 的协作记录建立完整基线")
            binding = store.session(args.session)
            if binding["owner"] != config["owner_id"]:
                raise ValueError("个人发送身份必须与 session 主人一致")
            return send_request(lark, plan)
    finally:
        store.close()


def main():
    try:
        result = run(parser().parse_args())
        emit(result)
        return 1 if result.get("ok") is False or result.get("status") in {"outcome_unknown", "sent_unverified"} else 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "hint": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
