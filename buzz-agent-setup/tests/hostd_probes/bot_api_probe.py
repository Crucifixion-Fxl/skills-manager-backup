#!/usr/bin/env python3
"""P0-7/8/9/14 with hostd test bots (engineering/skills#186): history, chat list + chat_ref, member list/add, missing-scope link.
Writes a redacted receipt to tests/fixtures/hostd/probes/bot-api.json."""
from __future__ import annotations
import argparse, hashlib, json, os, re, stat, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feishu_creds as fc
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "hostd" / "probes" / "bot-api.json"


def ok(d):
    return bool(d.get("ok")) and d.get("code", 0) in (0, None)


def chat_ref(cid):  # ADR-0022
    return hashlib.sha256(("buzz-feishu-chat:v1:" + cid).encode()).hexdigest()


NOTICE = "测试 bot 能力尚未验证。怎么解决：检查隔离测试应用的权限、缺失 scope、成员及完整读回后重试。\n复制给 AI：帮我检查 hostd 测试 bot 的权限与验收结果；不要输出凭据、真实群 ID 或原始错误。"


def member_evidence(payload, expected_apps):
    """Summarize the observed complete CLI roster without exposing member identities."""
    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else {}
    users, bots = data.get("users"), data.get("bots")
    rows_valid = isinstance(users, list) and isinstance(bots, list) and all(isinstance(row, dict) for row in users + bots)
    meta = payload.get("meta") if isinstance(payload, dict) else None
    pagination = meta.get("pagination") if isinstance(meta, dict) else None
    pagination_valid = ((meta is None or isinstance(meta, dict))
                        and (pagination is None or (isinstance(pagination, dict) and pagination.get("complete") is True)))
    complete = (rows_valid and data.get("has_more") is False
                and isinstance(data.get("truncations"), list) and not data["truncations"]
                and all(type(data.get(kind + "_total")) is int and data[kind + "_total"] == len(data[kind + "s"]) for kind in ("user", "bot"))
                and pagination_valid)
    bots = bots if rows_valid else []
    app_ids = {row.get("app_id") for row in bots if isinstance(row.get("app_id"), str)}
    app_valid = bool(bots) and all(isinstance(row.get("app_id"), str) and bool(row["app_id"]) for row in bots)
    member_valid = bool(bots) and all(isinstance(row.get("member_id"), str) and bool(row["member_id"]) for row in bots)
    return {"ok": isinstance(payload, dict) and ok(payload), "complete": bool(complete),
            "users": len(users) if isinstance(users, list) else 0, "bots": len(bots),
            "bots_have_app_id": app_valid, "bots_have_member_id": member_valid,
            "test_bot_app_ids_present": set(expected_apps).issubset(app_ids)}


def read_members(client, chat_id):
    return client.lark("hostd-test-desk", "im", "+chat-members-list", "--chat-id", chat_id,
                       "--member-id-type", "open_id", "--member-types", "user,bot",
                       "--as", "bot", "--page-all", "--page-limit", "0")


def run_probe(chat_id, client=None):
    client = client or fc
    if not isinstance(chat_id, str) or not re.fullmatch(r"oc_[A-Za-z0-9]+", chat_id):
        raise ValueError(NOTICE)
    expected_apps = {'hostd-test-desk':'cli_aa48bbeeba38dbcf',
                     'hostd-test-a':'cli_aa48b41531785bfc',
                     'hostd-test-b':'cli_aa48b406abf8dbe7'}
    if any(client.app_credentials(name)[0] != app for name, app in expected_apps.items()):
        raise ValueError(NOTICE)
    r = {"probe": "hostd-bot-api", "t": int(time.time())}
    # P0-7 history + thread list (bot)
    h = client.lark("hostd-test-desk", "api", "GET", "/open-apis/im/v1/messages", "--params",
                json.dumps({"container_id_type": "chat", "container_id": chat_id, "page_size": 20, "sort_type": "ByCreateTimeDesc"}), "--as", "bot")
    items = (h.get("data") or {}).get("items") or []
    r["P0-7_history"] = {"ok": ok(h), "items": len(items), "sender_types": sorted({(i.get("sender") or {}).get("sender_type") for i in items})}
    # P0-8 chat list + chat_ref
    c = client.lark("hostd-test-b", "api", "GET", "/open-apis/im/v1/chats", "--params", json.dumps({"page_size": 50}), "--as", "bot")
    ids = [i.get("chat_id") for i in (c.get("data") or {}).get("items") or []]
    r["P0-8_chat_list"] = {"ok": ok(c), "sees_group_x": chat_id in ids, "chat_ref_len": len(chat_ref(chat_id)), "chat_ref_stable": chat_ref(chat_id) == chat_ref(chat_id)}
    # P0-9 member list (bot) incl. bots with app_id
    test_apps = (expected_apps["hostd-test-a"], expected_apps["hostd-test-b"])
    r["P0-9_members"] = member_evidence(read_members(client, chat_id), test_apps)
    # P0-9 bot adds bot (already done in recorder probe; repeat idempotently)
    app_a = client.app_credentials("hostd-test-a")[0]
    a = client.lark("hostd-test-desk", "api", "POST", f"/open-apis/im/v1/chats/{chat_id}/members", "--params", json.dumps({"member_id_type": "app_id", "succeed_type": 1}),
                "--data", json.dumps({"id_list": [app_a]}), "--as", "bot")
    r["P0-9_bot_adds_bot"] = {"ok": ok(a), "invalid": (a.get("data") or {}).get("invalid_id_list") or []}
    # An ACK is not membership evidence. Revalidate the selected reader app and
    # issue a new same-profile, same-chat read only after the write returned.
    if client.app_credentials("hostd-test-desk")[0] != expected_apps["hostd-test-desk"]:
        raise ValueError(NOTICE)
    r["P0-9_postwrite_members"] = member_evidence(read_members(client, chat_id), test_apps)
    # P0-14 missing-scope error carries console_url (read-only call to a scope the test bot lacks)
    s = client.lark("hostd-test-b", "api", "GET", "/open-apis/calendar/v4/calendars", "--as", "bot")
    e = s.get("error") or {}
    r["P0-14_missing_scope"] = {"code": e.get("code"), "has_console_url": str(e.get("console_url", "")).startswith("https://open.feishu.cn/page/scope-apply?clientID="),
                                "has_missing_scopes": isinstance(e.get("missing_scopes"), list) and bool(e["missing_scopes"]) and all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", x) for x in e["missing_scopes"])}
    checks = {
        "P0-7_history_readable": r["P0-7_history"]["ok"] and r["P0-7_history"]["items"] > 0,
        "P0-8_chat_list_sees_group": r["P0-8_chat_list"]["ok"] and r["P0-8_chat_list"]["sees_group_x"],
        "P0-9_member_list_has_bots_with_app_id": r["P0-9_members"]["ok"] and r["P0-9_members"]["bots"] > 0 and r["P0-9_members"]["bots_have_app_id"] and r["P0-9_members"]["bots_have_member_id"],
        "P0-9_member_list_complete": r["P0-9_members"]["complete"],
        "P0-9_test_bots_present": r["P0-9_members"]["test_bot_app_ids_present"],
        "P0-9_bot_adds_bot": r["P0-9_bot_adds_bot"]["ok"] and not r["P0-9_bot_adds_bot"]["invalid"],
        "P0-9_postwrite_members_verified": all(r["P0-9_postwrite_members"][key] for key in
            ("ok", "complete", "bots_have_app_id", "bots_have_member_id", "test_bot_app_ids_present")),
        "P0-14_missing_scope_error_has_missing_scopes": r["P0-14_missing_scope"]["has_missing_scopes"],
        "P0-14_missing_scope_error_has_console_url": r["P0-14_missing_scope"]["code"] == 99991672 and r["P0-14_missing_scope"]["has_console_url"],
    }
    r["failed_checks"] = sorted(k for k, v in checks.items() if not v)
    r["ok"] = not r["failed_checks"]
    if not r["ok"]: r["notice"] = NOTICE
    return r


def _read_groups(path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from hostd.safety import read_owned
    doc = json.loads(read_owned(path))
    chat = doc.get("hostd-test-群X") if isinstance(doc, dict) else None
    if not isinstance(chat, str) or not re.fullmatch(r"oc_[A-Za-z0-9]+", chat): raise ValueError(NOTICE)
    return chat


def write_receipt(path, receipt):
    """Private receipt; nofollow every ancestor and refuse unsafe existing leaf."""
    path = Path(path).absolute()
    if '..' in path.parts: raise ValueError(NOTICE)
    directory = fd = None
    try:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in path.parts[1:-1]:
            try: child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=directory)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = child
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600:
            raise ValueError(NOTICE)
        os.ftruncate(fd, 0)
        with os.fdopen(fd, 'w') as stream:
            fd = None;json.dump(receipt, stream, indent=2, sort_keys=True);stream.write('\n')
    finally:
        if fd is not None: os.close(fd)
        if directory is not None: os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=Path)
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args(argv)
    try:
        groups = args.groups if args.groups is not None else Path.home() / 'skills/.worktree/.feishu-browser/test-groups.json'
        receipt = run_probe(_read_groups(groups))
        write_receipt(args.out, receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt['ok'] else 1
    except (Exception, SystemExit):
        print(NOTICE, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
