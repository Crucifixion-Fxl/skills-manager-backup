# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Import-only IM receiver; use collab.py --help for the public CLI."""
import json
from pathlib import Path
import subprocess
import sys
import threading
from core import app_lock


def stream_events(store, app_id, lines):
    for line in lines:
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("事件不是 JSON 对象")
            yield store.route(app_id, payload)
        except (ValueError, TypeError, KeyError):
            yield "malformed"


def listen(store, lark, process_factory=subprocess.Popen):
    if lark.config.get("bot_transport_approved") is not True:
        raise ValueError("bot 传输尚未获本机策略批准；不会改变个人 profile 的 strict-mode")
    lark.verify_user()
    lock_dir = Path.home() / ".local/state/feishu-session-collab/locks"
    with app_lock(lock_dir, lark.config["app_id"]):
        proc = process_factory(lark.argv(["event", "consume", "im.message.receive_v1"], "bot"),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready = threading.Event()
        def drain_stderr():
            for line in proc.stderr:
                if "[event] ready event_key=im.message.receive_v1" in line:
                    ready.set()
                elif "WARN" in line or "dropped" in line:
                    print(json.dumps({"status": "event_warning", "hint": "CLI 报告可能丢事件；检查权限、输出 schema 和连接状态"}, ensure_ascii=False), file=sys.stderr, flush=True)
        thread = threading.Thread(target=drain_stderr, daemon=True)
        thread.start()
        try:
            if not ready.wait(30):
                raise RuntimeError("未收到 CLI ready 标记；检查 bot 权限、strict-mode 和是否有其他机器/hostd 使用同一应用")
            print(json.dumps({"status": "listening", "app_id": lark.config["app_id"]}), file=sys.stderr, flush=True)
            for status in stream_events(store, lark.config["app_id"], proc.stdout):
                print(json.dumps({"status": status}), flush=True)
            if proc.wait(timeout=10) != 0:
                raise RuntimeError("飞书接收器退出；修复连接后重启，并核实断线期间遗漏的主人回复")
        finally:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=10)
            thread.join(timeout=1)
