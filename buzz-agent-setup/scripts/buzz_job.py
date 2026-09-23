#!/usr/bin/env python3
"""buzz-job —— 沙箱里的 agent 通过 buzz-broker 在沙箱外运行命令（含 docker）。
用法: buzz_job.py run [--timeout N] [--artifact PATH]... [--env K=V]... [--no-docker] [--dir D] -- <命令...>
把当前目录（默认排除 .git/.claude/.buzz-job 和 .buzzjobignore 里列出的）打包上传，在任务里运行命令，实时打印日志，退出码与命令一致；--artifact 指定的产物下载到 <dir>/.buzz-job/<id>/artifacts/。
退出码：命令自己的 | 124 超时 | 125 日志超限 | 130 取消 | 5 代理不可用/被拒 | 2 用法错误。"""
import base64, fnmatch, io, json, os, sys, tarfile, tempfile, time, urllib.error, urllib.request

BASE_URL = os.environ.get("BUZZ_BROKER_URL", "http://127.0.0.1:18950")
TOKEN_FILE = os.environ.get("BUZZ_BROKER_TOKEN_FILE", os.path.expanduser("~/.local/share/buzz-broker/token"))
DEFAULT_EXCLUDES = [".git", ".claude", ".buzz-job"]
MAX_EXTRACT = 1024 * 1024 * 1024


def build_opener():
    """沙箱里 127.0.0.1 是沙箱自己的空 lo，宿主机服务只能经沙箱代理到达，且 NO_PROXY=localhost,127.0.0.1 会让客户端绕开代理：有代理就显式走代理并清空 NO_PROXY。"""
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if proxy:
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = ""
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _patterns(root, extra):
    pats = list(DEFAULT_EXCLUDES) + list(extra)
    p = os.path.join(root, ".buzzjobignore")
    if os.path.exists(p):
        pats += [l.strip().rstrip("/") for l in open(p) if l.strip() and not l.startswith("#")]
    return pats


def _excluded(rel, pats):
    base = os.path.basename(rel)
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(base, p) or rel == p or rel.startswith(p + "/") for p in pats)


def pack_to_file(root, extra_excludes, fileobj, max_bytes=500 * 1024 * 1024):
    pats = _patterns(root, extra_excludes); total = 0; root = os.path.realpath(root)
    with tarfile.open(fileobj=fileobj, mode="w") as t:
        for dp, dn, fn in os.walk(root, followlinks=False):
            rel_dir = os.path.relpath(dp, root); rel_dir = "" if rel_dir == "." else rel_dir
            dn[:] = [d for d in dn if not _excluded(posix(os.path.join(rel_dir, d)), pats)]
            for name in list(dn) + fn:
                rel = posix(os.path.join(rel_dir, name)); full = os.path.join(dp, name)
                if _excluded(rel, pats): continue
                if os.path.islink(full):
                    tgt = os.path.realpath(full)
                    if not (tgt == root or tgt.startswith(root + os.sep)): continue          # a link that leaves the workspace is dropped
                    ti = tarfile.TarInfo(rel); ti.type = tarfile.SYMTYPE; ti.linkname = os.readlink(full)
                    if os.path.isabs(ti.linkname): ti.linkname = os.path.relpath(tgt, os.path.dirname(full))
                    t.addfile(ti); continue
                if os.path.isfile(full):
                    total += os.path.getsize(full)
                    if total > max_bytes: raise ValueError("workspace too large: exclude big directories in .buzzjobignore")
                    t.add(full, arcname=rel, recursive=False)
                elif os.path.isdir(full): t.add(full, arcname=rel, recursive=False)


def posix(p): return p.replace(os.sep, "/")


def pack_workspace(root, extra_excludes):
    b = io.BytesIO(); pack_to_file(root, extra_excludes, b); return b.getvalue()


def safe_extract(fileobj, dest):
    total = 0
    with tarfile.open(fileobj=fileobj, mode="r|") as t:
        for m in t:
            parts = m.name.replace("\\", "/").split("/")
            if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir()): raise ValueError(f"unsafe archive member: {m.name!r}")
            total += m.size
            if total > MAX_EXTRACT: raise ValueError("archive too large")
            t.extract(m, path=dest, filter="data")


FLAKE_RE = "RootlessKit PortManager.AddPort(): listen tcp4"
FLAKE_HINT = ("[buzz-job] 提示：日志里有 rootless docker 的端口分配冲突（RootlessKit PortManager.AddPort … address already in use）。"
              "这是 buzz-svc 的 docker 偶发的基础设施问题（本机别的服务占了 docker 挑中的端口，rootless 不会自动换端口重试），不是你的代码：直接重跑失败的用例"
              "（例如 make test-l3 L3_RUN='TestXxx|TestYyy'），不要去改代码追它。")


class FlakeDetector:
    """在流式日志里认出 rootless docker 的端口冲突特征；特征可能被切在两个日志块之间，所以保留上一块的尾部。"""
    def __init__(self): self.seen, self._tail = False, ""
    def feed(self, chunk):
        text = self._tail + chunk
        if FLAKE_RE in text and "address already in use" in text[text.index(FLAKE_RE):]: self.seen = True
        self._tail = text[-400:]


def call(method, path, token, data=None, headers=None, timeout=60):
    h = {"Authorization": "Bearer " + token}; h.update(headers or {})
    return build_opener().open(urllib.request.Request(BASE_URL + path, method=method, data=data, headers=h), timeout=timeout)


def main(argv):
    if len(argv) > 1 and argv[1] in ("-h", "--help"): print(__doc__); return 0
    if len(argv) < 2 or argv[1] != "run": print(__doc__, file=sys.stderr); return 2
    args, cmd, timeout, arts, env, docker, d = argv[2:], None, None, [], {}, True, "."
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--": cmd = " ".join(shell_quote(x) for x in args[i + 1:]) if len(args[i + 1:]) > 1 else args[i + 1]; break
        if a == "--timeout": timeout = int(args[i + 1]); i += 2
        elif a == "--artifact": arts.append(args[i + 1]); i += 2
        elif a == "--env":
            k, _, v = args[i + 1].partition("="); env[k] = v; i += 2
        elif a == "--no-docker": docker = False; i += 1
        elif a == "--dir": d = args[i + 1]; i += 2
        else: print(f"unknown option {a}", file=sys.stderr); return 2
    if not cmd: print("missing command after --", file=sys.stderr); return 2
    try: token = os.environ.get("BUZZ_BROKER_TOKEN") or open(TOKEN_FILE).read().strip()
    except OSError: print("broker token not readable", file=sys.stderr); return 5
    spec = {"cmd": cmd, "artifacts": arts, "env": env, "docker": docker}
    if timeout: spec["timeout_s"] = timeout
    with tempfile.TemporaryFile() as tf:
        try: pack_to_file(d, [], tf)
        except ValueError as e: print(str(e), file=sys.stderr); return 2
        size = tf.tell(); tf.seek(0)
        try:
            with call("POST", "/v1/jobs", token, data=tf, headers={"X-Job-Spec": base64.b64encode(json.dumps(spec).encode()).decode(), "Content-Type": "application/x-tar", "Content-Length": str(size)}, timeout=600) as r:
                jid = json.load(r)["id"]
        except urllib.error.HTTPError as e:
            print(f"broker refused the job: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}", file=sys.stderr); return 5 if e.code in (401, 403, 429) else 2
        except (urllib.error.URLError, OSError) as e:
            why = getattr(e, "reason", e)
            hint = " — connection dropped during upload, the workspace is probably over the broker's size limit: exclude big directories in .buzzjobignore" if isinstance(why, (BrokenPipeError, ConnectionResetError)) else ""
            print(f"broker not reachable ({type(why).__name__}){hint}", file=sys.stderr); return 5
    off, rc, deadline, flake = 0, None, time.time() + (timeout or 7200) + 900, FlakeDetector()
    while time.time() < deadline:
        try:
            with call("GET", f"/v1/jobs/{jid}?offset={off}", token) as r: j = json.load(r)
        except (urllib.error.URLError, OSError) as e: print(f"broker lost ({type(e).__name__})", file=sys.stderr); return 5
        if j["log"]: print(j["log"], end="", flush=True); flake.feed(j["log"])
        off = j["next_offset"]
        if j["status"] == "done" and not j["log"]: rc = j["exit_code"]; has = j.get("has_artifacts"); break
        time.sleep(0.4 if j["log"] else 2)
    if rc is None: print("timed out waiting for the job", file=sys.stderr); return 124
    if rc != 0 and flake.seen: print(FLAKE_HINT, file=sys.stderr)
    if arts and has:
        out = os.path.join(d, ".buzz-job", jid, "artifacts"); os.makedirs(out, exist_ok=True)
        try:
            with call("GET", f"/v1/jobs/{jid}/artifacts", token, timeout=300) as r: safe_extract(r, out)
            print(f"[buzz-job] artifacts: {out}", file=sys.stderr)
        except (ValueError, tarfile.TarError, urllib.error.URLError, OSError) as e: print(f"[buzz-job] could not fetch artifacts: {type(e).__name__}", file=sys.stderr)
    return rc if isinstance(rc, int) and 0 <= rc <= 255 else 1


def shell_quote(s):
    import shlex; return shlex.quote(s)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
