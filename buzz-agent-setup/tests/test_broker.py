"""buzz-broker 的测试。默认用 exec_mode=plain（普通子进程）测协议/校验/生命周期；另有真实 bwrap 的集成测试验证隔离。"""
import base64, hashlib, http.client, importlib.util, io, json, os, shutil, sys, tarfile, tempfile, threading, time, unittest, urllib.request, urllib.error
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "broker.py"
JOB_SCRIPT = SKILL / "scripts" / "buzz_job.py"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = load_module("broker")
C = load_module("buzz_job")

TOKEN, TOKEN_B, ADMIN = "tok-agent-a-0123456789", "tok-agent-b-0123456789", "tok-admin-0123456789"
H = lambda t: hashlib.sha256(t.encode()).hexdigest()


def make_cfg(tmp, **over):
    cfg = {"port": 0, "state_dir": os.path.join(tmp, "state"), "admin_token_sha256": H(ADMIN),
           "agents": {"agent-a": {"token_sha256": H(TOKEN), "job": {"max_timeout_s": 60, "max_upload_mb": 1, "docker": False}},
                      "agent-b": {"token_sha256": H(TOKEN_B), "job": {"max_timeout_s": 60, "max_upload_mb": 1, "docker": False}},
                      "agent-nojob": {"token_sha256": H("tok-nojob-0123456789")}},
           "job": {"max_concurrent": 2, "default_timeout_s": 30, "min_timeout_s": 1, "log_cap_mb": 1, "retain_hours": 1,
                   "exec_mode": "plain", "use_systemd_scope": False, "docker_cleanup": False, "max_queue": 3}}
    for k, v in over.items(): cfg["job"][k] = v
    return cfg


def tar_bytes(files, links=(), hardlinks=()):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name); ti.size = len(data); ti.mode = 0o644; t.addfile(ti, io.BytesIO(data))
        for name, target in links:
            ti = tarfile.TarInfo(name); ti.type = tarfile.SYMTYPE; ti.linkname = target; t.addfile(ti)
        for name, target in hardlinks:
            ti = tarfile.TarInfo(name); ti.type = tarfile.LNKTYPE; ti.linkname = target; t.addfile(ti)
    return b.getvalue()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.srv = B.make_server(make_cfg(cls.tmp)); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start(); cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, token=TOKEN, spec=None, ctype="application/json"):
        headers = {}
        if token: headers["Authorization"] = f"Bearer {token}"
        if spec is not None: headers["X-Job-Spec"] = base64.b64encode(json.dumps(spec).encode()).decode(); ctype = "application/x-tar"
        if body is not None: headers["Content-Type"] = ctype
        r = urllib.request.Request(self.base + path, method=method, data=body, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp: return resp.status, resp.read()
        except urllib.error.HTTPError as e: return e.code, e.read()

    def submit(self, cmd, files=None, **spec):
        st, b = self.req("POST", "/v1/jobs", tar_bytes(files or {"hello.txt": b"hi"}), spec=dict({"cmd": cmd}, **spec))
        return st, (json.loads(b) if b else {})

    def wait(self, jid, secs=30):
        for _ in range(int(secs * 20)):
            st, b = self.req("GET", f"/v1/jobs/{jid}?offset=0"); j = json.loads(b)
            if j["status"] == "done": return j
            time.sleep(0.05)
        self.fail("job did not finish")


class Auth(Base):
    def test_no_or_wrong_token_rejected(self):
        self.assertEqual(self.req("POST", "/v1/jobs", b"x", token=None, spec={"cmd": "true"})[0], 401)
        self.assertEqual(self.req("POST", "/v1/jobs", b"x", token="nope-nope-nope", spec={"cmd": "true"})[0], 401)

    def test_agent_without_job_policy_forbidden(self):
        self.assertEqual(self.req("POST", "/v1/jobs", tar_bytes({"a": b"1"}), token="tok-nojob-0123456789", spec={"cmd": "true"})[0], 403)

    def test_agent_without_job_capability_cannot_read_jobs(self):
        st, j = self.submit("true"); self.assertEqual(st, 202)
        self.assertEqual(self.req("GET", f"/v1/jobs/{j['id']}", token="tok-nojob-0123456789")[0], 403)
        self.wait(j["id"])

    def test_other_agents_job_not_visible_or_cancellable(self):
        st, j = self.submit("mkdir -p o && echo x > o/f", artifacts=["o/**"]); self.wait(j["id"]); jid = j["id"]
        self.assertEqual(self.req("GET", f"/v1/jobs/{jid}", token=TOKEN_B)[0], 404)
        self.assertEqual(self.req("GET", f"/v1/jobs/{jid}/artifacts", token=TOKEN_B)[0], 404)
        self.assertEqual(self.req("POST", f"/v1/jobs/{jid}/cancel", b"", token=TOKEN_B)[0], 404)
        self.assertEqual(self.req("GET", f"/v1/jobs/{jid}/artifacts")[0], 200)          # the owner still can


class UploadConcurrency(Base):
    """上传/解包在真正排队（app.q）之前发生：ThreadingHTTPServer 并行处理多个连接，qsize() 那道闸门管不到
    「正在上传/解包」的请求。低信任 agent 一次开多个大上传，不能让磁盘/内存被同时接收的多个未完成上传拖垮共享 broker；
    全局在飞上传数要有上限，跟任务队列深度分开算。用真实的慢速分块发送制造确定的并发窗口，不打桩内部函数。
    限额是服务启动时一次性建好的 threading.Semaphore，不会跟着配置热加载改变大小，所以这里另起一个限额=1 的服务，
    不复用 Base 的共享服务、也不在运行期改配置。"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.srv = B.make_server(make_cfg(cls.tmp, max_concurrent_uploads=1))
        cls.port = cls.srv.server_address[1]; threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    def slow_post(self, body, spec, steps=15, step_delay=0.08):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {"Authorization": f"Bearer {TOKEN}", "X-Job-Spec": base64.b64encode(json.dumps(spec).encode()).decode(),
                       "Content-Type": "application/x-tar", "Content-Length": str(len(body))}
            conn.putrequest("POST", "/v1/jobs"); [conn.putheader(k, v) for k, v in headers.items()]; conn.endheaders()
            chunk = max(1, len(body) // steps)
            for i in range(0, len(body), chunk):
                conn.send(body[i:i + chunk]); time.sleep(step_delay)
            resp = conn.getresponse(); status = resp.status; resp.read(); return status
        finally:
            conn.close()

    def test_a_second_upload_is_turned_away_while_one_is_still_in_flight(self):
        big = tar_bytes({"a": b"x" * (200 * 1024)})
        results = []
        t = threading.Thread(target=lambda: results.append(self.slow_post(big, {"cmd": "true"}))); t.start()
        time.sleep(0.3)   # the slow upload is now mid-flight, well inside its ~1.2s window and holding the one slot
        turned_away = self.req("POST", "/v1/jobs", tar_bytes({"a": b"1"}), spec={"cmd": "true"})[0]
        t.join(timeout=10)
        self.assertEqual(turned_away, 429)
        self.assertEqual(results, [202])

    def test_the_freed_slot_is_usable_again_once_the_upload_finishes(self):
        st1, j1 = self.submit("true"); self.wait(j1["id"])            # finishes and releases its slot
        st2, j2 = self.submit("true"); self.wait(j2["id"])            # the freed slot must be usable again, not leaked
        self.assertEqual((st1, st2), (202, 202))


class SpecValidation(Base):
    def bad(self, **spec):
        st, _ = self.submit(spec.pop("cmd", "true"), **spec); self.assertEqual(st, 400, spec)

    def test_rejects_bad_specs(self):
        self.bad(cmd="")
        self.bad(cmd="x" * 30000)
        self.bad(timeout_s=0)
        self.bad(timeout_s=99999)
        self.bad(env={"lower": "x"})
        for name in ("PATH", "HOME", "DOCKER_HOST", "LD_PRELOAD", "BUZZ_TOKEN", "XDG_RUNTIME_DIR"): self.bad(env={name: "x"})
        self.bad(env={"OK": "a\x00b"})
        self.bad(env={f"V{i}": "x" for i in range(80)})
        for art in ("../x", "/etc/passwd", "a/../../b", ""): self.bad(artifacts=[art])
        self.bad(artifacts=["x"] * 80)

    def test_docker_denied_when_policy_forbids(self):
        self.bad(docker=True)
        self.bad(docker="yes")

    def test_oversize_upload_rejected(self):
        st, _ = self.req("POST", "/v1/jobs", b"\0" * (2 * 1024 * 1024), spec={"cmd": "true"}); self.assertEqual(st, 413)

    def test_early_reject_of_a_big_upload_still_returns_the_status_code(self):
        for _ in range(3):      # used to be a flaky "Broken pipe" because the server closed before the client finished sending
            st, _ = self.req("POST", "/v1/jobs", b"\0" * (16 * 1024 * 1024), spec={"cmd": "true"}); self.assertEqual(st, 413)
        st, _ = self.req("POST", "/v1/jobs", b"\0" * (16 * 1024 * 1024), spec={"cmd": ""}); self.assertEqual(st, 413)

    def test_unsafe_archives_rejected(self):
        for files, links in (({"../evil": b"x"}, ()), ({"/abs": b"x"}, ()), ({"a/../../evil": b"x"}, ()),
                             ({"ok": b"1"}, [("lnk", "/etc/passwd")]), ({"ok": b"1"}, [("lnk", "../../etc")])):
            st, _ = self.req("POST", "/v1/jobs", tar_bytes(files, links), spec={"cmd": "true"}); self.assertEqual(st, 400, (files, links))

    def test_hardlinks_rejected_even_inside_the_archive(self):
        st, _ = self.req("POST", "/v1/jobs", tar_bytes({"real": b"1"}, hardlinks=[("h", "real")]), spec={"cmd": "true"}); self.assertEqual(st, 400)

    def test_safe_relative_symlink_allowed(self):
        st, j = self.submit("cat link.txt", {"real.txt": b"data"});
        st2, b = self.req("POST", "/v1/jobs", tar_bytes({"real.txt": b"data"}, [("link.txt", "real.txt")]), spec={"cmd": "cat link.txt"})
        self.assertEqual(st2, 202); res = self.wait(json.loads(b)["id"]); self.assertIn("data", res["log"])


class Lifecycle(Base):
    def test_runs_command_captures_log_and_exit_code(self):
        st, j = self.submit("echo out-line; echo err-line >&2; cat hello.txt; exit 3"); self.assertEqual(st, 202)
        res = self.wait(j["id"]); self.assertEqual(res["exit_code"], 3)
        for s in ("out-line", "err-line", "hi"): self.assertIn(s, res["log"])

    def test_env_passed_but_reserved_env_fixed(self):
        st, j = self.submit('echo "X=$MY_VAR HOME=$HOME"', env={"MY_VAR": "v1"}); res = self.wait(j["id"])
        self.assertIn("X=v1", res["log"]); self.assertNotIn("HOME=/root", res["log"])

    def test_go_private_is_config_driven_not_hardcoded(self):
        """脚本本身不写死任何内部域名；GOPRIVATE/GONOSUMDB 只在部署方的 config.json 配了 job.go_private 时才设。"""
        st, j = self.submit('echo "P=[${GOPRIVATE-unset}] S=[${GONOSUMDB-unset}]"'); res = self.wait(j["id"])
        self.assertIn("P=[unset]", res["log"]); self.assertIn("S=[unset]", res["log"])
        self.srv.app.source["job"]["go_private"] = "gitlab.example.internal"
        try:
            st, j = self.submit('echo "P=[$GOPRIVATE] S=[$GONOSUMDB]"'); res = self.wait(j["id"])
            self.assertIn("P=[gitlab.example.internal]", res["log"]); self.assertIn("S=[gitlab.example.internal]", res["log"])
        finally:
            del self.srv.app.source["job"]["go_private"]

    def test_artifacts_returned_and_only_requested_paths(self):
        st, j = self.submit("mkdir -p out && echo result > out/r.txt && echo secret > other.txt", artifacts=["out/**"]); res = self.wait(j["id"])
        st, tar = self.req("GET", f"/v1/jobs/{j['id']}/artifacts"); self.assertEqual(st, 200)
        names = tarfile.open(fileobj=io.BytesIO(tar)).getnames(); self.assertIn("out/r.txt", names); self.assertNotIn("other.txt", names)

    def test_artifacts_never_follow_symlinks_out_of_the_workspace(self):
        secret = os.path.join(self.tmp, "host-secret.txt"); open(secret, "w").write("TOP-SECRET-HOST-DATA")
        secret_dir = os.path.join(self.tmp, "hostdir"); os.makedirs(secret_dir); open(os.path.join(secret_dir, "f"), "w").write("TOP-SECRET-HOST-DATA")
        st, j = self.submit(f"ln -s {secret} leak_file; ln -s {secret_dir} leak_dir; mkdir -p out; ln -s {secret} out/inner; echo ok > out/fine",
                            artifacts=["leak_file", "leak_dir", "out/**"]); self.wait(j["id"])
        st, tar = self.req("GET", f"/v1/jobs/{j['id']}/artifacts"); self.assertEqual(st, 200)
        t = tarfile.open(fileobj=io.BytesIO(tar)); names = t.getnames()
        self.assertEqual(names, ["out/fine"]); self.assertNotIn(b"TOP-SECRET-HOST-DATA", tar)

    def test_ci_is_not_preset(self):
        """CI=true 会让仓库的测试支撑（如 pgtest）拒绝启动 testcontainers——broker 存在的意义就是本地能跑 docker，不能替任务设它。"""
        _, j = self.submit('echo "CI=[${CI-unset}]"'); self.assertIn("CI=[unset]", self.wait(j["id"])["log"])
        _, j = self.submit('echo "CI=[${CI-unset}]"', env={"CI": "true"}); self.assertIn("CI=[true]", self.wait(j["id"])["log"])     # 任务自己要设仍可以

    def test_workspace_is_deleted_when_the_job_ends_but_log_and_artifacts_stay(self):
        """其它任务经 docker 挂载能读到 state/jobs/*：工作区只在运行期间存在，结束即删；日志和产物另存。"""
        _, j = self.submit("pwd; mkdir -p out; echo keep > out/r.txt", artifacts=["out/**"]); res = self.wait(j["id"])
        ws = res["log"].strip().splitlines()[0]
        for _ in range(100):
            if not os.path.exists(ws): break
            time.sleep(0.05)
        self.assertFalse(os.path.exists(ws)); self.assertFalse(os.path.exists(os.path.dirname(ws)))
        st, tar = self.req("GET", f"/v1/jobs/{j['id']}/artifacts"); self.assertEqual(st, 200); self.assertIn("out/r.txt", tarfile.open(fileobj=io.BytesIO(tar)).getnames())

    def test_undeletable_tree_falls_back_to_docker_rm(self):
        """容器里以 root 写的文件属于 subuid，buzz-svc 自己 rmtree 删不掉：必须交给 docker 删，不然磁盘会一直涨。"""
        calls = []; app = self.srv.app; d = os.path.join(self.tmp, "stuck"); os.makedirs(d)
        orig_rm, orig_dr = B.shutil.rmtree, app.docker_rm
        B.shutil.rmtree = lambda *a, **k: None; app.docker_rm = lambda p: calls.append(p)
        try: app.remove_tree(d)
        finally: B.shutil.rmtree, app.docker_rm = orig_rm, orig_dr
        self.assertEqual(calls, [d]); shutil.rmtree(d)

    def test_timeout_kills_job(self):
        st, j = self.submit("sleep 30", timeout_s=2); t0 = time.time(); res = self.wait(j["id"], 20)
        self.assertEqual(res["exit_code"], 124); self.assertLess(time.time() - t0, 15)

    def test_jobs_get_separate_workspaces(self):
        _, a = self.submit("echo A > mark.txt; pwd"); _, b = self.submit("ls; pwd")
        ra, rb = self.wait(a["id"]), self.wait(b["id"]); self.assertNotIn("mark.txt", rb["log"])

    def test_cancel(self):
        st, j = self.submit("sleep 30", timeout_s=60); time.sleep(0.5)
        self.assertEqual(self.req("POST", f"/v1/jobs/{j['id']}/cancel", b"")[0], 200); res = self.wait(j["id"], 10)
        self.assertEqual(res["exit_code"], 130)   # canonical "cancelled" code, not whatever signal happened to kill the process first

    def test_cancel_immediately_after_submit_is_never_racy(self):
        """先前的实现里，/cancel 的 HTTP 线程会自己直接 kill，跟 worker 线程「先记 130 再 kill」的顺序抢跑：
        谁先看到进程已死谁说了算，偶尔会把 exit_code 记成信号对应的 143/137。这里不留 0.5s 缓冲，
        重复多次，把「worker 轮询间隔内、HTTP 线程立刻杀」这个最容易触发竞态的时机集中打一遍。"""
        for _ in range(10):
            st, j = self.submit("sleep 30", timeout_s=60)
            self.assertEqual(self.req("POST", f"/v1/jobs/{j['id']}/cancel", b"")[0], 200)
            res = self.wait(j["id"], 10)
            self.assertEqual(res["exit_code"], 130, res)

    def test_log_cap_stops_runaway_output(self):
        st, j = self.submit("yes AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", timeout_s=30); res = self.wait(j["id"], 25)
        self.assertEqual(res["exit_code"], 125)

    def test_queue_full_returns_429(self):
        ids = []
        for _ in range(8):
            st, j = self.submit("sleep 2", timeout_s=10)
            if st == 429: break
            ids.append(j["id"])
        self.assertEqual(st, 429)
        for i in ids: self.req("POST", f"/v1/jobs/{i}/cancel", b"")
        for i in ids: self.wait(i, 30)


class Startup(unittest.TestCase):
    def test_leftovers_from_a_previous_run_are_swept_at_start(self):
        """重启会杀掉所有任务且内存里的任务表丢失：磁盘上的旧目录/日志/产物没人管，启动时要清掉。"""
        tmp = tempfile.mkdtemp(); jobs = os.path.join(tmp, "state", "jobs"); os.makedirs(os.path.join(jobs, "a" * 32, "ws"))
        for n in ("a" * 32 + ".log", "a" * 32 + ".tar"): open(os.path.join(jobs, n), "w").write("x")
        open(os.path.join(jobs, "a" * 32, "ws", "f"), "w").write("x")
        srv = B.make_server(make_cfg(tmp))
        try: self.assertEqual(os.listdir(jobs), [])
        finally: srv.server_close(); shutil.rmtree(tmp, ignore_errors=True)


class Admin(Base):
    def test_admin_requires_admin_token(self):
        self.assertEqual(self.req("POST", "/admin/reload", b"", token=TOKEN)[0], 401)
        self.assertEqual(self.req("POST", "/admin/reload", b"", token=ADMIN)[0], 200)


class ClientPack(unittest.TestCase):
    def test_pack_excludes_git_and_ignores_symlinks_outside(self):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(f"{d}/.git"); os.makedirs(f"{d}/src"); open(f"{d}/.git/config", "w").write("x"); open(f"{d}/src/a.go", "w").write("package a")
            os.symlink("/etc/passwd", f"{d}/leak"); os.symlink("src/a.go", f"{d}/ok_link")
            names = tarfile.open(fileobj=io.BytesIO(C.pack_workspace(d, []))).getnames()
            self.assertIn("src/a.go", names); self.assertIn("ok_link", names)
            self.assertFalse([n for n in names if n.startswith(".git")]); self.assertNotIn("leak", names)
        finally: shutil.rmtree(d, ignore_errors=True)

    def test_buzzjobignore_and_extra_excludes(self):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(f"{d}/big"); open(f"{d}/big/x.bin", "w").write("x"); open(f"{d}/keep.txt", "w").write("k"); open(f"{d}/.buzzjobignore", "w").write("big\n*.log\n")
            open(f"{d}/a.log", "w").write("l")
            names = tarfile.open(fileobj=io.BytesIO(C.pack_workspace(d, []))).getnames()
            self.assertIn("keep.txt", names); self.assertNotIn("big/x.bin", names); self.assertNotIn("a.log", names)
        finally: shutil.rmtree(d, ignore_errors=True)

    def test_safe_extract_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): C.safe_extract(io.BytesIO(tar_bytes({"../x": b"1"})), d)

    def test_flake_detector_recognises_the_rootless_port_collision_even_across_chunks(self):
        line = "Error response from daemon: failed to set up container networking: driver failed programming external connectivity on endpoint x (abc): error while calling RootlessKit PortManager.AddPort(): listen tcp4 127.0.0.1:32818: bind: address already in use"
        d = C.FlakeDetector(); self.assertFalse(d.seen)
        cut = line.index("PortManager") + 6                                   # 切在特征标记「PortManager」中间
        d.feed("ok\n" + line[:cut]); self.assertFalse(d.seen)
        d.feed(line[cut:] + "\n"); self.assertTrue(d.seen)                  # 跨块拼起来才是完整特征
        d3 = C.FlakeDetector(); d3.feed(line[:line.index("address already")] + "permission denied"); self.assertFalse(d3.seen)   # 同一处报错但原因不是端口已占用，不算
        d2 = C.FlakeDetector(); d2.feed("listen tcp4 127.0.0.1:8080: bind: address already in use"); self.assertFalse(d2.seen)   # 用户自己的端口冲突不算
        self.assertIn("address already in use", C.FLAKE_HINT); self.assertIn("L3_RUN", C.FLAKE_HINT)

    def test_proxy_config_like_sandbox(self):
        saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")}
        try:
            os.environ["HTTP_PROXY"] = "http://p.invalid:3128"; os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1"
            C.build_opener(); self.assertFalse(urllib.request.proxy_bypass("127.0.0.1"))
        finally:
            for k, v in saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v


class Cli(unittest.TestCase):
    def test_help_prints_docstring_and_does_not_start_a_server(self):
        for flag in ("-h", "--help"):
            r = subprocess.run([sys.executable, str(SCRIPT), flag], capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, (flag, r.stderr))
            self.assertIn("buzz-broker", r.stdout)
            self.assertNotIn("listening on", r.stdout)  # docstring only, the server itself must not start

    def test_client_help_prints_docstring_without_a_broker_token(self):
        for flag in ("-h", "--help"):
            r = subprocess.run([sys.executable, str(JOB_SCRIPT), flag], capture_output=True, text=True, timeout=10,
                               env={"PATH": os.environ.get("PATH", "")})     # no BUZZ_BROKER_TOKEN* on purpose: --help must not need one
            self.assertEqual(r.returncode, 0, (flag, r.stderr))
            self.assertIn("buzz-job", r.stdout)


@unittest.skipUnless(shutil.which("bwrap"), "bwrap not installed")
class BwrapIsolation(unittest.TestCase):
    """真实 bwrap：任务只看得到自己的工作区，看不到别的任务、宿主 home，写入落回工作区。"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); tools = os.path.join(cls.tmp, "tools"); os.makedirs(tools + "/bin")
        cfg = make_cfg(cls.tmp, exec_mode="bwrap", tools_dir=tools)
        try: cls.srv = B.make_server(cfg)
        except Exception as e: raise unittest.SkipTest(f"cannot start: {e}")
        cls.port = cls.srv.server_address[1]; threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_job(self, cmd, **spec):
        base = f"http://127.0.0.1:{self.port}"
        r = urllib.request.Request(base + "/v1/jobs", method="POST", data=tar_bytes({"seed.txt": b"seed"}),
                                   headers={"Authorization": f"Bearer {TOKEN}", "X-Job-Spec": base64.b64encode(json.dumps(dict({"cmd": cmd}, **spec)).encode()).decode(), "Content-Type": "application/x-tar"})
        jid = json.load(urllib.request.urlopen(r, timeout=30))["id"]
        for _ in range(400):
            q = urllib.request.Request(f"{base}/v1/jobs/{jid}?offset=0", headers={"Authorization": f"Bearer {TOKEN}"}); j = json.load(urllib.request.urlopen(q, timeout=30))
            if j["status"] == "done": return j
            time.sleep(0.05)
        self.fail("timeout")

    def test_sees_only_its_workspace_and_no_host_home(self):
        home = os.path.expanduser("~")
        j = self.run_job(f'cat seed.txt; echo; echo "home_listing=[$(ls /home 2>&1 | tr "\\n" " ")]"; echo "host_home=[$(ls {home} 2>&1 | head -c 40)]"; echo "state=[$(ls {self.tmp}/state 2>&1 | head -c 40)]"')
        self.assertEqual(j["exit_code"], 0); self.assertIn("seed", j["log"])
        self.assertNotIn(os.path.basename(home) + " ", j["log"].split("home_listing=")[1].split("]")[0])
        self.assertIn("cannot access", j["log"].split("host_home=")[1])

    def test_cannot_read_other_jobs_workspace(self):
        a = self.run_job("echo secret-from-a > a_secret.txt; pwd"); wsa = a["log"].strip().splitlines()[-1]
        b = self.run_job(f"cat {wsa}/a_secret.txt 2>&1; ls {os.path.dirname(wsa)} 2>&1 | head -c 60")
        self.assertNotIn("secret-from-a", b["log"])

    def test_workspace_path_is_same_inside_and_outside(self):
        j = self.run_job("pwd"); self.assertTrue(j["log"].strip().startswith(self.tmp))

    def test_env_is_clean(self):
        j = self.run_job('echo "user_home=$HOME"; env | grep -c -i -E "token|secret|password" || true')
        self.assertIn("user_home=/home/job", j["log"])

    def test_extra_ro_paths_are_visible_and_read_only(self):
        d = os.path.join(self.tmp, "extra-tool"); os.makedirs(d); open(os.path.join(d, "hello"), "w").write("from-extra")
        self.__class__.srv.app.source["job"]["extra_ro"] = [d, "/nonexistent-path-x"]         # 不存在的路径要被忽略，不能让整个任务起不来
        j = self.run_job(f'cat {d}/hello; echo; (echo x > {d}/w) 2>&1 | head -c 80; ls {d}')
        self.assertEqual(j["exit_code"], 0); self.assertIn("from-extra", j["log"]); self.assertNotIn("\nhello\nhello", j["log"])
        self.assertTrue(("Read-only" in j["log"]) or ("denied" in j["log"]), j["log"])
        self.assertFalse(os.path.exists(os.path.join(d, "w")))

    def test_dns_config_survives_the_private_run_tmpfs(self):
        """/etc/resolv.conf 是指向 /run/systemd/resolve 的符号链接；把 /run 盖成 tmpfs 会让任务里所有域名解析失败（go mod download、git、pip）。"""
        j = self.run_job("cat /etc/resolv.conf >/dev/null 2>&1 && echo resolv-ok || echo resolv-broken")
        self.assertIn("resolv-ok", j["log"])


if __name__ == "__main__":
    unittest.main()
