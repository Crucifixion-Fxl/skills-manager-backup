#!/usr/bin/env python3
"""buzz-broker 管理工具（jchen 运行，不需要 sudo）：铸令牌 / 改名 / 撤销 / 列表 / 重启。
身份以令牌为准，agent 名只是配置里的一个标签：改名只改标签，令牌和令牌文件（按不透明 id 命名）都不动，所以 agent 改名后不用重发令牌。
用法: broker_admin.py [--config C] [--tokens DIR] <init|mint|rename|revoke|list|restart|reload> ...
  init                              首次生成配置和管理令牌（~/.local/share/buzz-broker/admin-token，0600）
  mint <label> [--no-docker] [--max-timeout S] [--max-upload-mb M]   给一个 agent 铸令牌，打印令牌文件路径（不打印令牌）
  rotate-admin                      换管理令牌（旧的立即失效）
  rename <old> <new>                agent 改名
  revoke <label>                    撤销并删除令牌文件
  list                              列出标签、策略和令牌文件名（不含令牌/哈希）
  restart | reload                  经管理端点让服务重启 / 立即重载配置（配置改动本来就会自动热加载）"""
import argparse, hashlib, json, os, re, secrets, sys, tempfile, urllib.request

HOME = os.path.expanduser("~")
DEF_CONFIG = os.environ.get("BUZZ_BROKER_CONFIG", "/opt/buzz-broker/etc/config.json")
DEF_TOKENS = os.path.join(HOME, ".local/share/buzz-broker/tokens")
DEF_ADMIN = os.path.join(HOME, ".local/share/buzz-broker/admin-token")
LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
sha = lambda t: hashlib.sha256(t.encode()).hexdigest()


def load(path):
    with open(path) as f: return json.load(f)


def save(path, cfg):
    """原子替换：服务按 (mtime,inode,size) 热加载，绝不能读到写了一半的文件。
    文件 0644：jchen 不在 buzz-svc 组里、无法 chgrp，访问控制靠目录（/opt/buzz-broker/etc 是 0750、属组 buzz-svc）；里面只有令牌的 sha256，没有令牌本身。"""
    d = os.path.dirname(path); fd, tmp = tempfile.mkstemp(dir=d, prefix=".cfg.")
    try:
        with os.fdopen(fd, "w") as f: json.dump(cfg, f, indent=2, ensure_ascii=False); f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise


def write_secret(path, value):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f: f.write(value + "\n")


def init_config(path, admin_token_file, port=18950, state_dir="/home/buzz-svc/state"):
    if os.path.exists(path): raise ValueError("config already exists")
    tok = secrets.token_urlsafe(32); write_secret(admin_token_file, tok)
    save(path, {"port": port, "state_dir": state_dir, "admin_token_sha256": sha(tok), "agents": {}, "job": {}})


def rotate_admin(path, admin_token_file):
    """管理令牌泄露或要换时用：先写新令牌文件（0600，原子替换），再换配置里的哈希；旧令牌立即失效。"""
    tok = secrets.token_urlsafe(32); tmp = admin_token_file + ".new"
    try: os.remove(tmp)
    except OSError: pass
    write_secret(tmp, tok); os.replace(tmp, admin_token_file)
    cfg = load(path); cfg["admin_token_sha256"] = sha(tok); save(path, cfg)


def mint(path, token_dir, label, docker=True, max_timeout_s=7200, max_upload_mb=500):
    if not LABEL.match(label): raise ValueError("label must match [a-z0-9][a-z0-9-]{0,40}")
    cfg = load(path)
    if label in cfg["agents"]: raise ValueError(f"{label} already exists (revoke it first to re-mint)")
    tid = secrets.token_hex(8); tok = secrets.token_urlsafe(32); tfile = os.path.join(token_dir, tid + ".token")
    write_secret(tfile, tok)
    cfg["agents"][label] = {"token_sha256": sha(tok), "token_id": tid, "job": {"docker": docker, "max_timeout_s": max_timeout_s, "max_upload_mb": max_upload_mb}}
    try: save(path, cfg)
    except BaseException: os.remove(tfile); raise
    return {"label": label, "token_file": tfile}


def rename(path, old, new):
    if not LABEL.match(new): raise ValueError("bad new label")
    cfg = load(path)
    if old not in cfg["agents"]: raise ValueError(f"{old} not found")
    if new in cfg["agents"]: raise ValueError(f"{new} already exists")
    cfg["agents"] = {(new if k == old else k): v for k, v in cfg["agents"].items()}; save(path, cfg)


def revoke(path, token_dir, label):
    cfg = load(path)
    if label not in cfg["agents"]: raise ValueError(f"{label} not found")
    ent = cfg["agents"].pop(label); save(path, cfg)
    if ent.get("token_id"):
        try: os.remove(os.path.join(token_dir, ent["token_id"] + ".token"))
        except OSError: pass


def list_agents(path):
    return [{"label": k, "token_file": v.get("token_id", "") + ".token", "job": v.get("job")} for k, v in load(path)["agents"].items()]


def admin_post(path, admin_file, endpoint):
    cfg = load(path); tok = open(admin_file).read().strip()
    r = urllib.request.Request(f"http://127.0.0.1:{cfg.get('port', 18950)}{endpoint}", method="POST", data=b"", headers={"Authorization": "Bearer " + tok})
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r, timeout=10).status


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=DEF_CONFIG); ap.add_argument("--tokens", default=DEF_TOKENS); ap.add_argument("--admin-token-file", default=DEF_ADMIN)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init"); sub.add_parser("rotate-admin"); sub.add_parser("list"); sub.add_parser("restart"); sub.add_parser("reload")
    m = sub.add_parser("mint"); m.add_argument("label"); m.add_argument("--no-docker", action="store_true"); m.add_argument("--max-timeout", type=int, default=7200); m.add_argument("--max-upload-mb", type=int, default=500)
    r = sub.add_parser("rename"); r.add_argument("old"); r.add_argument("new")
    v = sub.add_parser("revoke"); v.add_argument("label")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init": init_config(a.config, a.admin_token_file); print(f"config: {a.config}\nadmin token file: {a.admin_token_file}")
        elif a.cmd == "rotate-admin": rotate_admin(a.config, a.admin_token_file); print("admin token rotated (old one no longer valid)")
        elif a.cmd == "mint": i = mint(a.config, a.tokens, a.label, docker=not a.no_docker, max_timeout_s=a.max_timeout, max_upload_mb=a.max_upload_mb); print(f"minted {i['label']}: token file {i['token_file']}")
        elif a.cmd == "rename": rename(a.config, a.old, a.new); print(f"renamed {a.old} -> {a.new} (token and token file unchanged)")
        elif a.cmd == "revoke": revoke(a.config, a.tokens, a.label); print(f"revoked {a.label}")
        elif a.cmd == "list":
            for e in list_agents(a.config): print(json.dumps(e, ensure_ascii=False))
        else: print(admin_post(a.config, a.admin_token_file, "/admin/" + a.cmd))
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
