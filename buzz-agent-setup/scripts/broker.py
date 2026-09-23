#!/usr/bin/env python3
"""buzz-broker —— 沙箱外的「能力代理」。以专用用户 buzz-svc 运行，让沙箱里的 agent 能做沙箱本身做不到的事。

当前能力：job（在 rootless docker 环境里运行 agent 提交的任意命令，例如 TDD 里的 make test-l2 / docker compose）。
（browser 能力：现有的 neopace-fetch 之后迁进来。）

为什么是它：沙箱里的 docker 客户端连不上任何守护进程，沙箱里的测试进程也连不到宿主机容器的映射端口，所以要 docker 的命令和测试进程必须在沙箱外跑。
安全模型（任意命令也不给宿主机 root/jchen 的文件）：
- 任务以 buzz-svc 身份运行——不是 jchen、不在 docker 组、没有 sudo、环境里没有任何密钥；docker 是 buzz-svc 名下的 rootless 实例，容器逃逸最多到 buzz-svc；
- 每个任务再套一层 bwrap：只看得到自己的工作区（同一路径在内外一致，docker -v 才能用）和只读工具链，看不到别的任务、buzz-svc 的 home、别的用户；systemd scope 限内存/进程数；
- 只监听 127.0.0.1，每个 agent 一个令牌（配置里只存 sha256），策略按令牌走（不绑定 agent 名字，改名只是改配置里的标签）；
- 上传的 tar 严格校验（无绝对路径/..、无硬链接/设备、符号链接不出工作区）；日志、上传、产物都有大小上限；审计日志记录谁在何时跑了什么（命令只留前 200 字符）。
退出码：命令自己的退出码 | 124 超时 | 125 日志超限 | 130 被取消 | 1 内部错误。"""
import base64, glob, hashlib, hmac, json, os, posixpath, queue, re, shutil, signal, subprocess, sys, tarfile, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

ROUTE = re.compile(r"^/v1/jobs/([0-9a-f]{32})(/artifacts|/cancel)?$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
RESERVED = re.compile(r"^(PATH|HOME|USER|LOGNAME|SHELL|PWD|OLDPWD|TMPDIR|LANG|LC_.*|DOCKER_.*|LD_.*|BUZZ_.*|XDG_.*|GOMODCACHE|GOCACHE|GOPATH|GOROOT|GOTOOLCHAIN|UV_.*|TESTCONTAINERS_(DOCKER_SOCKET_OVERRIDE|HOST_OVERRIDE))$")
MAX_SPEC, MAX_CMD, MAX_ENV, MAX_ART, MAX_ENV_VALUE = 16384, 20000, 50, 50, 4096
DEFAULTS = {"max_concurrent": 2, "default_timeout_s": 1800, "min_timeout_s": 10, "log_cap_mb": 50, "retain_hours": 6, "exec_mode": "bwrap",
            "use_systemd_scope": True, "memory_max": "12G", "tasks_max": 8192, "docker_cleanup": True, "max_queue": 20, "max_artifact_mb": 500,
            "tools_dir": "/opt/buzz-toolchains", "max_concurrent_uploads": 4}


def validate_spec(spec, policy, jc):
    if not isinstance(spec, dict): raise ValueError("spec must be an object")
    cmd = spec.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > MAX_CMD or "\x00" in cmd: raise ValueError("bad cmd")
    timeout = spec.get("timeout_s", jc["default_timeout_s"])
    mx = policy.get("max_timeout_s", 7200)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not jc["min_timeout_s"] <= timeout <= mx: raise ValueError(f"timeout_s must be {jc['min_timeout_s']}..{mx}")
    env = spec.get("env", {})
    if not isinstance(env, dict) or len(env) > MAX_ENV: raise ValueError("bad env")
    for k, v in env.items():
        if not ENV_NAME.match(k) or RESERVED.match(k): raise ValueError(f"env name not allowed: {k[:30]}")
        if not isinstance(v, str) or len(v) > MAX_ENV_VALUE or "\x00" in v: raise ValueError("bad env value")
    arts = spec.get("artifacts", [])
    if not isinstance(arts, list) or len(arts) > MAX_ART: raise ValueError("bad artifacts")
    for a in arts:
        if not isinstance(a, str) or not a or len(a) > 300 or a.startswith("/") or ".." in a.replace("\\", "/").split("/") or "\x00" in a: raise ValueError("bad artifact path")
    docker = spec.get("docker", policy.get("docker", True))
    if not isinstance(docker, bool): raise ValueError("bad docker flag")
    if docker and not policy.get("docker", True): raise ValueError("docker not allowed for this agent")
    return {"cmd": cmd, "timeout_s": timeout, "env": env, "artifacts": arts, "docker": docker}


def extract_upload(path, dest, max_bytes, max_files=300000):
    """安全解包：拒绝绝对路径、..、硬链接、设备/管道；符号链接只能指向工作区内。"""
    total = files = 0
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(path, "r:*") as t:
        for m in t:
            name = m.name.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if name.startswith("/") or ".." in parts: raise ValueError(f"unsafe path in archive: {name[:60]!r}")
            if m.islnk() or m.isdev() or m.isfifo(): raise ValueError(f"unsupported member type: {name[:60]!r}")
            if m.issym():
                tgt = posixpath.normpath(posixpath.join(posixpath.dirname("/".join(parts)), m.linkname))
                if m.linkname.startswith("/") or tgt == ".." or tgt.startswith("../"): raise ValueError(f"symlink leaves workspace: {name[:60]!r}")
            total += max(m.size, 0); files += 1
            if total > max_bytes or files > max_files: raise ValueError("archive too large")
            t.extract(m, dest, filter="data")


class Job:
    def __init__(self, agent, spec, state):
        self.id = uuid.uuid4().hex; self.agent = agent; self.spec = spec
        self.dir = os.path.join(state, "jobs", self.id); self.ws = os.path.join(self.dir, "ws"); self.cache = os.path.join(self.dir, "cache")
        self.log = os.path.join(state, "jobs", self.id + ".log"); self.art = os.path.join(state, "jobs", self.id + ".tar")
        self.status, self.exit_code, self.created, self.started, self.ended = "queued", None, time.time(), None, None
        self.cancelled = False; self.proc = None


class App:
    def __init__(self, source):
        self.source = source; self._cfg = None; self._mtime = None; self.cfg
        self.state = os.path.abspath(os.path.expanduser(self.cfg.get("state_dir") or "~/state"))
        os.makedirs(os.path.join(self.state, "jobs"), mode=0o700, exist_ok=True)
        for n in os.listdir(os.path.join(self.state, "jobs")):          # a restart kills every job and forgets them: whatever is on disk is orphaned
            p = os.path.join(self.state, "jobs", n)
            if os.path.isdir(p): self.remove_tree(p)
            else:
                try: os.remove(p)
                except OSError: pass
        self.jobs, self.lock, self.q, self.running = {}, threading.Lock(), None, 0
        import queue as _q; self.q = _q.Queue()
        self.upload_sem = threading.BoundedSemaphore(self.jc["max_concurrent_uploads"])          # qsize() only gates queued jobs; an upload is still
                                                                                                    # being written+extracted to disk before that, and
                                                                                                    # ThreadingHTTPServer runs requests in parallel
        for _ in range(self.jc["max_concurrent"]): threading.Thread(target=self.worker, daemon=True).start()

    @property
    def cfg(self):
        if isinstance(self.source, dict): return self.source
        st = os.stat(self.source); m = (st.st_mtime_ns, st.st_ino, st.st_size)      # coarse kernel timestamps: mtime alone can miss a second write in the same tick
        if m != self._mtime:
            with open(self.source) as f: self._cfg = json.load(f)
            self._mtime = m
        return self._cfg

    @property
    def jc(self): return dict(DEFAULTS, **self.cfg.get("job", {}))

    def auth(self, token):
        h = hashlib.sha256(token.encode()).hexdigest(); found = None
        for label, a in self.cfg.get("agents", {}).items():
            if hmac.compare_digest(h, a.get("token_sha256", "")): found = label
        return found

    def is_admin(self, token): return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self.cfg.get("admin_token_sha256", "-"))

    # ---- lifecycle ----
    def submit(self, job):
        with self.lock:
            self.gc()
            if self.q.qsize() >= self.jc["max_queue"]: return False
            self.jobs[job.id] = job; self.q.put(job); return True

    def gc(self):
        cutoff = time.time() - self.jc["retain_hours"] * 3600
        for jid, j in list(self.jobs.items()):
            if j.status == "done" and (j.ended or 0) < cutoff:
                for p in (j.log, j.art):
                    try: os.remove(p)
                    except OSError: pass
                self.remove_tree(j.dir); del self.jobs[jid]

    def worker(self):
        while True:
            job = self.q.get()
            try:
                with self.lock: self.running += 1
                if job.cancelled: job.exit_code = 130; job.status = "done"; job.ended = time.time()
                else: self.run(job)
            except Exception as e:
                with open(job.log, "ab") as lf: lf.write(f"\nINTERNAL ERROR {type(e).__name__}: {str(e)[:120]}\n".encode())
                job.exit_code, job.status, job.ended = 1, "done", time.time()
            finally:
                with self.lock: self.running -= 1
                self.cleanup_docker(job)

    def env_for(self, job):
        jc = self.jc; tools = jc["tools_dir"]; sock = self.cfg.get("docker_socket") or f"/run/user/{os.getuid()}/docker.sock"
        e = {"HOME": "/home/job", "USER": "job", "LOGNAME": "job", "SHELL": "/bin/bash", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
             "PATH": f"{tools}/bin:{tools}/go/bin:{tools}/node/bin:/usr/local/bin:/usr/bin:/bin",
             "GOMODCACHE": f"{tools}/gomod", "GOCACHE": f"{job.cache}/go-build", "GOPATH": f"{job.cache}/gopath",
             "UV_CACHE_DIR": f"{job.cache}/uv", "UV_PYTHON_INSTALL_DIR": f"{tools}/uv-python", "UV_PYTHON_DOWNLOADS": "never",
             "npm_config_cache": f"{job.cache}/npm", "XDG_CACHE_HOME": f"{job.cache}/xdg"}
        if jc.get("go_private"): e.update(GOPRIVATE=jc["go_private"], GONOSUMDB=jc["go_private"])          # e.g. an internal GitLab host — set in config.json's job.go_private, never hardcoded here
        if job.spec["docker"]: e.update(DOCKER_HOST=f"unix://{sock}", TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=sock)
        if jc["exec_mode"] == "plain": e.update(HOME=job.cache, TMPDIR=job.cache, PATH=os.environ.get("PATH", "/usr/bin:/bin"))
        e.update(job.spec["env"]); return e

    def argv_for(self, job, env):
        jc = self.jc
        if jc["exec_mode"] == "plain": return ["bash", "-c", job.spec["cmd"]]
        tools = jc["tools_dir"]; sock = self.cfg.get("docker_socket") or f"/run/user/{os.getuid()}/docker.sock"
        a = ["bwrap", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session",
             "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc", "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
             "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
             "--tmpfs", "/tmp", "--tmpfs", "/dev/shm", "--tmpfs", "/run", "--tmpfs", "/home/job"]
        if os.path.isdir("/run/systemd/resolve"): a += ["--ro-bind", "/run/systemd/resolve", "/run/systemd/resolve"]       # /etc/resolv.conf points here
        if os.path.isdir(tools): a += ["--ro-bind", tools, tools]
        for extra in jc.get("extra_ro", []):
            if os.path.exists(extra): a += ["--ro-bind", extra, extra]           # e.g. the buzz CLI 0.5.20 that feishu-bridge L3 needs
        a += ["--bind", job.ws, job.ws, "--bind", job.cache, job.cache]
        if job.spec["docker"] and os.path.exists(sock): a += ["--bind", sock, sock]
        a += ["--clearenv"]
        for k, v in env.items(): a += ["--setenv", k, v]
        a += ["--chdir", job.ws, "--", "bash", "-c", job.spec["cmd"]]
        if jc["use_systemd_scope"]:
            a = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={jc['memory_max']}", "-p", "MemorySwapMax=0", "-p", f"TasksMax={jc['tasks_max']}", "--"] + a
        return a

    def run(self, job):
        jc = self.jc; job.status, job.started = "running", time.time()
        os.makedirs(job.cache, mode=0o700, exist_ok=True)
        env = self.env_for(job); argv = self.argv_for(job, env)
        outer = {k: os.environ[k] for k in ("PATH", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "HOME") if k in os.environ}
        if jc["exec_mode"] == "plain": outer = dict(env)
        cap = jc["log_cap_mb"] * 1024 * 1024; rc = None
        with open(job.log, "ab") as lf:
            job.proc = subprocess.Popen(argv, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=job.ws, env=outer, start_new_session=True)
            deadline = job.started + job.spec["timeout_s"]
            while job.proc.poll() is None:
                if time.time() > deadline: rc = 124; lf.write(b"\nTIMEOUT: job killed\n")
                elif os.path.getsize(job.log) > cap: rc = 125; lf.write(b"\nLOG CAP EXCEEDED: job killed\n")
                elif job.cancelled: rc = 130
                if rc is not None: self.kill(job); break
                time.sleep(0.15)
            if rc is None: rc = job.proc.returncode
        job.exit_code = rc if rc >= 0 else 128 - rc
        self.collect_artifacts(job)
        self.remove_tree(job.dir)          # the workspace only lives while the job runs (other jobs can read state/jobs through a docker bind mount)
        job.ended, job.status = time.time(), "done"
        with open(os.path.join(self.state, "audit.log"), "a") as a:
            a.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "id": job.id, "agent": job.agent, "cmd": job.spec["cmd"][:200],
                                "docker": job.spec["docker"], "exit": job.exit_code, "seconds": round(job.ended - job.started, 1)}) + "\n")

    @staticmethod
    def kill(job):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try: os.killpg(job.proc.pid, sig)
            except (ProcessLookupError, PermissionError): pass
            try: job.proc.wait(timeout=3); break
            except subprocess.TimeoutExpired: continue

    def collect_artifacts(self, job):
        pats = job.spec["artifacts"]
        if not pats: return
        ws = os.path.realpath(job.ws); cap = self.jc["max_artifact_mb"] * 1024 * 1024; total = 0; seen = set()
        def inside(path): return path == ws or path.startswith(ws + os.sep)
        with tarfile.open(job.art, "w") as t:
            for pat in pats:
                for p in sorted(glob.glob(os.path.join(ws, pat), recursive=True)):
                    if os.path.islink(p) or not inside(os.path.realpath(p)): continue           # never follow a link out of the workspace
                    files = [p] if os.path.isfile(p) else [os.path.join(dp, f) for dp, _, fn in os.walk(p) for f in fn]
                    for fp in files:
                        arc = os.path.relpath(fp, ws)
                        if arc in seen or os.path.islink(fp) or not os.path.isfile(fp) or not inside(os.path.realpath(fp)): continue
                        total += os.path.getsize(fp)
                        if total > cap: return
                        seen.add(arc); t.add(fp, arcname=arc, recursive=False)

    def remove_tree(self, path):
        shutil.rmtree(path, ignore_errors=True)
        if os.path.exists(path): self.docker_rm(path)

    def docker_rm(self, path):
        """容器里以 root 写出的文件属于 subuid，buzz-svc 自己删不掉；让 rootless docker 里的 root 来删。"""
        sock = self.cfg.get("docker_socket") or f"/run/user/{os.getuid()}/docker.sock"; env = {"PATH": os.environ.get("PATH", ""), "DOCKER_HOST": f"unix://{sock}"}
        try:
            subprocess.run(["docker", "run", "--rm", "--pull=never", "--network", "none", "--entrypoint", "rm", "-v", f"{os.path.dirname(path)}:/w", self.cfg.get("cleanup_image", "alpine:latest"),
                            "-rf", f"/w/{os.path.basename(path)}"], env=env, capture_output=True, timeout=180)
        except Exception: pass
        shutil.rmtree(path, ignore_errors=True)

    def cleanup_docker(self, job):
        if not (self.jc["docker_cleanup"] and job.spec["docker"] and self.jc["exec_mode"] == "bwrap"): return
        with self.lock:
            if self.running > 0 or not self.q.empty(): return       # another job may be using the daemon: prune later
        sock = self.cfg.get("docker_socket") or f"/run/user/{os.getuid()}/docker.sock"; env = {"PATH": os.environ.get("PATH", ""), "DOCKER_HOST": f"unix://{sock}"}
        try:
            ids = subprocess.run(["docker", "ps", "-aq"], env=env, capture_output=True, text=True, timeout=30).stdout.split()
            if ids: subprocess.run(["docker", "rm", "-f", *ids], env=env, capture_output=True, timeout=120)
            subprocess.run(["docker", "network", "prune", "-f"], env=env, capture_output=True, timeout=60)
            subprocess.run(["docker", "volume", "prune", "-f"], env=env, capture_output=True, timeout=60)
        except Exception: pass


def make_server(source):
    app = App(source)

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def send_json(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
            self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(b); self.close_connection = True
        def reject(self, code, obj):
            """早拒绝时先限量排空请求体：否则客户端还在上传就被关连接，看到的是 EPIPE 而不是错误码。"""
            try:
                left = min(int(self.headers.get("Content-Length", "0") or 0), 256 * 1024 * 1024)
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk: break
                    left -= len(chunk)
            except (ValueError, OSError): pass
            self.send_json(code, obj)
        def token(self):
            g = self.headers.get("Authorization", ""); return g[7:] if g.startswith("Bearer ") else ""
        def agent(self):
            t = self.token(); label = app.auth(t) if t else None
            if not label: self.send_json(401, {"error": "unauthorized"}); return None, None
            pol = app.cfg["agents"][label].get("job")
            if pol is None: self.send_json(403, {"error": "job capability not granted"}); return None, None
            return label, pol
        def do_POST(self):
            sp = urlsplit(self.path)
            if sp.path.startswith("/admin/"):
                if not app.is_admin(self.token()): return self.send_json(401, {"error": "unauthorized"})
                if sp.path == "/admin/reload": app._mtime = None; app.cfg; return self.send_json(200, {"ok": True})
                if sp.path == "/admin/restart":
                    self.send_json(200, {"ok": True}); threading.Timer(0.5, lambda: os._exit(0)).start(); return
                return self.send_json(404, {"error": "not found"})
            label, pol = self.agent()
            if not label: return
            m = ROUTE.match(sp.path)
            if m and m.group(2) == "/cancel":
                job = app.jobs.get(m.group(1))
                if not job or job.agent != label: return self.send_json(404, {"error": "not found"})
                job.cancelled = True     # the worker loop (polls every 0.15s) is the sole killer, so the exit code is always the canonical 130 and never whatever signal happened to land first
                return self.send_json(200, {"ok": True})
            if sp.path != "/v1/jobs": return self.send_json(404, {"error": "not found"})
            try: n = int(self.headers.get("Content-Length", ""))
            except ValueError: return self.send_json(411, {"error": "length required"})
            if n > pol.get("max_upload_mb", 500) * 1024 * 1024: return self.reject(413, {"error": "upload too large"})
            try:
                raw = base64.b64decode(self.headers.get("X-Job-Spec", ""), validate=True)
                if len(raw) > MAX_SPEC: raise ValueError("spec too large")
                spec = validate_spec(json.loads(raw), pol, app.jc)
            except (ValueError, TypeError) as e: return self.reject(400, {"error": f"bad spec: {str(e)[:120]}"})
            if app.q.qsize() >= app.jc["max_queue"]: return self.reject(429, {"error": "queue full"})
            if not app.upload_sem.acquire(blocking=False): return self.reject(429, {"error": "too many uploads in flight"})
            try:
                job = Job(label, spec, app.state); os.makedirs(job.dir, mode=0o700); up = job.dir + ".upload.tar"
                try:
                    left = n
                    with open(up, "wb") as f:
                        while left > 0:
                            chunk = self.rfile.read(min(left, 1 << 20))
                            if not chunk: raise ValueError("short body")
                            f.write(chunk); left -= len(chunk)
                    extract_upload(up, job.ws, pol.get("max_upload_mb", 500) * 4 * 1024 * 1024)
                except (ValueError, tarfile.TarError, OSError) as e:
                    shutil.rmtree(job.dir, ignore_errors=True); return self.send_json(400, {"error": f"bad archive: {str(e)[:120]}"})
                finally:
                    try: os.remove(up)
                    except OSError: pass
            finally:
                app.upload_sem.release()
            if not app.submit(job): shutil.rmtree(job.dir, ignore_errors=True); return self.send_json(429, {"error": "queue full"})
            self.send_json(202, {"id": job.id})
        def do_GET(self):
            label, pol = self.agent()
            if not label: return
            sp = urlsplit(self.path); m = ROUTE.match(sp.path); job = app.jobs.get(m.group(1)) if m else None
            if not job or job.agent != label: return self.send_json(404, {"error": "not found"})
            if m.group(2) == "/artifacts":
                if job.status != "done" or not os.path.exists(job.art): return self.send_json(409, {"error": "not ready or no artifacts"})
                size = os.path.getsize(job.art); self.send_response(200); self.send_header("Content-Type", "application/x-tar")
                self.send_header("Content-Length", str(size)); self.send_header("Connection", "close"); self.end_headers()
                with open(job.art, "rb") as f:
                    while chunk := f.read(65536): self.wfile.write(chunk)
                self.close_connection = True; return
            try: off = max(0, int(parse_qs(sp.query).get("offset", ["0"])[0]))
            except ValueError: off = 0
            data = b""
            if os.path.exists(job.log):
                with open(job.log, "rb") as f: f.seek(off); data = f.read(65536)
            self.send_json(200, {"id": job.id, "status": job.status, "exit_code": job.exit_code, "log": data.decode("utf-8", "replace"), "next_offset": off + len(data),
                                 "has_artifacts": os.path.exists(job.art)})

    srv = ThreadingHTTPServer(("127.0.0.1", app.cfg.get("port", 18950)), H); srv.daemon_threads = True; srv.app = app
    return srv


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__); sys.exit(0)
    srv = make_server(os.environ.get("BUZZ_BROKER_CONFIG") or "/opt/buzz-broker/etc/config.json")
    signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(SystemExit(0)))
    print(f"buzz-broker listening on 127.0.0.1:{srv.server_address[1]}", flush=True)
    try: srv.serve_forever()
    except (KeyboardInterrupt, SystemExit): pass
