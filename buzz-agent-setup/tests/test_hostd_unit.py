"""hostd 的 L1 单元测试（engineering/skills#186，ADR-0025–0028 的实现）。

被测的是 `scripts/hostd/` 里不碰网络的三块：registry.load（从同步配置根目录读绑定）、Hostd 的
mark / on_relay（事件只把某个绑定的某个 phase 标脏，飞书事件还带上是哪个话题）、binding_worker.Worker
（一个绑定跑哪些 phase、setup 缓存何时重做、threads 怎么透传）；加上 Round.feishu_to_buzz 的
only_threads（事件指明了哪个话题有回复时只读那个话题）和 secrets_store 的应用密钥解密。
复用 test_buzz_feishu_group_sync.py 的 FakeWorld / Env 造 Round；hostd/__main__ 依赖 websockets
（relay_feed）、secrets_store 依赖 cryptography，CI 镜像没装就整类跳过，其余测试纯标准库。
"""

import asyncio
import contextlib
import importlib.util
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
for p in (str(SCRIPTS), str(SCRIPTS / "hostd"), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import test_buzz_feishu_group_sync as base  # noqa: E402  (FakeWorld、Env、常量、FGS)
FGS = base.FGS

import registry  # noqa: E402
import binding_worker as bw  # noqa: E402


def _importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


HAS_WEBSOCKETS = _importable("websockets")    # hostd/__main__ -> relay_feed
HAS_CRYPTOGRAPHY = _importable("cryptography")  # secrets_store

if HAS_WEBSOCKETS:  # __main__ 不能按模块名 import，用独立名字加载
    _spec = importlib.util.spec_from_file_location("hostd_main_l1", SCRIPTS / "hostd" / "__main__.py")
    hostd_main = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(hostd_main)

if HAS_CRYPTOGRAPHY:
    import secrets_store  # noqa: E402
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402


def write_config(path: Path, cfg: dict, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    os.chmod(path, mode)
    return path


def binding_config(channel: str, chat_id, app_id: str, mirror_env: Path) -> dict:
    return {"channel_id": channel, "chat_id": chat_id, "desk_pubkey": base.AGENT_PK,
            "agents": {base.AGENT_PK: {"app_id": app_id, "lark_config_dir": f"/cfg/{app_id}",
                                       "lark_data_dir": f"/data/{app_id}"}},
            "mirror_env_file": str(mirror_env)}


# ================================ L1-HD-001 registry.load ================================


class RegistryLoad(base.TmpCase):
    def make_root(self) -> Path:
        root = self.tmp / "buzz-feishu-sync"
        root.mkdir()
        mirror_env = root / "alpha" / "mirror.env"
        write_config(mirror_env, {})
        mirror_env.write_text('BUZZ_RELAY_URL="https://relay.test"\n', encoding="utf-8")
        write_config(root / "alpha" / "config.json", binding_config(base.CHANNEL, "oc_alpha", "cli_app1", mirror_env))
        # 还没绑定群：chat_id 是 null
        write_config(root / "beta" / "config.json", binding_config(base.CHANNEL, None, "cli_app2", mirror_env))
        # 内容正常，但权限是 0644
        write_config(root / "gamma" / "config.json", binding_config(base.CHANNEL, "oc_gamma", "cli_app3", mirror_env),
                     mode=0o644)
        return root

    def test_only_the_owner_only_bound_config_loads_and_skips_say_why(self):
        """L1-HD-001：只有 0600 且已绑群的配置进 bindings；另两份进 skipped，原因是中文人话。"""
        reg = registry.load(self.make_root())
        self.assertEqual(list(reg.bindings), ["alpha"])
        b = reg.bindings["alpha"]
        self.assertEqual((b.channel_id, b.chat_id, b.sync_app_id), (base.CHANNEL, "oc_alpha", "cli_app1"))
        self.assertEqual(b.state_dir, self.tmp / "buzz-feishu-sync" / "alpha" / "state")
        self.assertEqual(b.relay_url, "https://relay.test")  # 从 mirror_env_file 里读出来
        self.assertIn("还没有绑定飞书群（config 里没有 chat_id）", reg.skipped["beta"])
        self.assertIn("配置文件权限不是 0600，跳过", reg.skipped["gamma"])

    def test_by_app_groups_the_bindings_of_one_sync_app(self):
        """L1-HD-002：同一个 app_id（同一条长连接，ADR-0025 P0-2）的绑定归到一起。"""
        root = self.make_root()
        mirror_env = root / "alpha" / "mirror.env"
        write_config(root / "delta" / "config.json", binding_config("c3247180-c101-48d6-9417-c28b6b103db1", "oc_delta", "cli_app1", mirror_env))
        reg = registry.load(root)
        self.assertEqual({b.name for b in reg.by_app()["cli_app1"]}, {"alpha", "delta"})
        self.assertEqual({a: [b.name for b in bs] for a, bs in reg.by_app().items()},
                         {"cli_app1": ["alpha", "delta"]})
        self.assertEqual({c: b.name for c, b in reg.by_chat().items()},
                         {"oc_alpha": "alpha", "oc_delta": "delta"})


# ================================ L1-HD-003 Hostd.mark / on_relay ================================


@unittest.skipUnless(HAS_WEBSOCKETS, "hostd/__main__ 依赖 websockets（relay_feed）")
class HostdMarking(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        os.chmod(self.tmp, 0o700)
        cfg = write_config(self.tmp / "n1.json", {"mirror_pubkey": base.MIRROR_PK})
        reg = registry.Registry()
        reg.bindings["n1"] = registry.Binding(
            name="n1", config=cfg, state_dir=self.tmp / "state", channel_id=base.CHANNEL, chat_id=base.CHAT,
            sync_app_id="cli_app1", lark_config_dir=str(self.tmp / "cfg"), lark_data_dir=str(self.tmp / "data"),
            relay_url="wss://relay.test")
        self.h = hostd_main.Hostd(reg, self.tmp / "status.json")

    def test_mirror_own_events_are_ignored(self):
        """L1-HD-003：mirror 自己发进频道的事件（飞书 → Buzz 的回声）不标脏、不唤醒。"""
        asyncio.run(self.h.on_relay("n1", {"pubkey": base.MIRROR_PK, "kind": 9}))
        self.assertEqual(self.h.dirty["n1"], set())
        self.assertFalse(self.h.wake["n1"].is_set())

    def test_relay_events_mark_members_or_buzz_by_kind(self):
        """L1-HD-004：成员事件标 members 并触发 notice；kind 9 触发 notice，其余标 buzz。"""
        for kind, dirty in ((9000, {"members", "notice"}), (9001, {"members", "notice"}),
                            (9, {"buzz", "notice"}), (7, {"buzz"})):
            with self.subTest(kind=kind):
                asyncio.run(self.h.on_relay("n1", {"pubkey": base.ALICE_PK, "kind": kind}))
                self.assertEqual(self.h.dirty["n1"], dirty)
                self.assertTrue(self.h.wake["n1"].is_set())
                self.h.dirty["n1"].clear()
                self.h.wake["n1"].clear()

    def test_reconnect_marks_buzz_even_for_the_mirror(self):
        """L1-HD-005：断线重连（_reconnected）优先于回声忽略：一律标 buzz 做一次补洞。"""
        asyncio.run(self.h.on_relay("n1", {"type": "_reconnected", "pubkey": base.MIRROR_PK}))
        self.assertEqual(self.h.dirty["n1"], {"members", "buzz", "feishu", "notice"})
        self.assertIsNone(self.h.threads['n1'])

    def test_feishu_event_adds_only_its_root_to_threads(self):
        """L1-HD-006：飞书事件带 root_id 时 threads 只加那一个 root；空 root 不加。"""
        self.h.mark("n1", "feishu", thread="om_root")
        self.assertEqual(self.h.threads["n1"], {"om_root"})
        self.assertIn("feishu", self.h.dirty["n1"])
        self.h.mark("n1", "feishu", thread="")
        self.assertEqual(self.h.threads["n1"], {"om_root"})
        self.h.mark("n1", "feishu", thread="om_other")
        self.assertEqual(self.h.threads["n1"], {"om_root", "om_other"})

    def test_catch_up_reads_every_thread_until_reset(self):
        """L1-HD-007：catch_up 把 threads 置 None（断线补洞：全量读）；之后带 root 的事件仍是 None，
        直到 worker 取走后才重新开始积累。"""
        self.h.mark("n1", "feishu", thread="om_root")
        self.h.mark("n1", "feishu", catch_up=True)
        self.assertIsNone(self.h.threads["n1"])
        self.h.mark("n1", "feishu", thread="om_late")
        self.assertIsNone(self.h.threads["n1"])


# ================================ L1-HD-008 Worker 的 phase 选择 ================================


class _Nothing:
    def close(self):
        pass


class _FakeState:
    def __init__(self):
        self.binding = None
        self.floor = self.buzz_since = self.feishu_since = self.react_since = 0


def _fake_round_factory():
    made = []

    class FakeRound:
        verdict = "off"

        def _feishu_overlap(self):
            return 900

        def __init__(self, cfg, clients, state, report, now, persist,
                     allow_bulk_removal=False, skip_backlog=False, auth_clock=None, reader_namespace=None,
                     store=None, binding_id=None, claim_relay_pubkey=None):
            for f in bw.Worker.SETUP_FIELDS:
                setattr(self, f, "v0-" + f)
            self.calls = []
            made.append(self)

        def check_claims(self, take_over=False):
            self.calls.append("check_claims")
            return type(self).verdict

        def feishu_to_buzz(self, only_threads=None):
            self.calls.append(("feishu_to_buzz", only_threads))

    for step in ("verify_identities", "load_people", "load_directory", "verify_desk", "reconcile_members",
                 "introduce_agents", "publish_membership_status", "buzz_to_feishu", "buzz_reactions_to_feishu",
                 "feishu_reactions_to_buzz"):
        def make(step):
            def method(self):
                self.calls.append(step)
            return method
        setattr(FakeRound, step, make(step))
    return FakeRound, made


class WorkerPhases(base.TmpCase):
    def patched(self, verdict="off"):
        FakeRound, made = _fake_round_factory()
        FakeRound.verdict = verdict
        state = FGS.State()
        saves = []
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(bw.dm, "MappedHostdRound", FakeRound))
        stack.enter_context(mock.patch.object(bw.hc, "load_config",
                                              lambda path: {"channel_id": base.CHANNEL, "chat_id": base.CHAT,
                                                "desk_pubkey": base.AGENT_PK,
                                                "agents": {base.AGENT_PK: {"app_id": base.AGENT_APP}}}))
        stack.enter_context(mock.patch.object(bw.gs, "_lock", lambda d: _Nothing()))
        stack.enter_context(mock.patch.object(bw.bc, "build_clients", lambda cfg, env: SimpleNamespace(agents={})))
        class MemoryStorage:
            store = None
            binding_id = 'test'
            def load(self): return state
            def save(self, value, **kwargs): saves.append(value)
            def metadata(self): return {"reader_app_id": base.AGENT_APP}
        @contextlib.contextmanager
        def storage(worker, cfg):
            yield MemoryStorage()
        stack.enter_context(mock.patch.object(bw.Worker, "_storage", storage))
        stack.enter_context(mock.patch.object(bw.gs, "prune_state", lambda s: None))
        return made, state, saves

    def test_first_run_sets_up_then_runs_every_dirty_phase(self):
        """L1-HD-008：首轮做全套 setup（身份、人员、认领、目录、Desk 校验），再按 dirty 跑 phase；
        feishu 阶段把 threads 原样透传给 feishu_to_buzz(only_threads=...)。"""
        made, state, _ = self.patched()
        w = bw.Worker(config=Path("cfg.json"), state_dir=Path("state"))
        rep = w.run({"members", "buzz", "feishu"}, threads={"om_root"})
        run = made[-1]
        for step in ("verify_identities", "load_people", "check_claims", "load_directory", "verify_desk",
                     "reconcile_members", "buzz_to_feishu"):
            self.assertIn(step, run.calls, step)
        self.assertIn(("feishu_to_buzz", {"om_root"}), run.calls)
        self.assertEqual(rep["hostd"]["verdict"], "off")
        self.assertEqual(rep["hostd"]["setup"], "fresh")
        self.assertEqual(rep["hostd"]["threads"], 1)
        self.assertEqual(state.binding, f"{base.CHANNEL}|{base.CHAT}")  # 首轮落绑定起点

    def test_cached_run_skips_people_and_restores_the_setup_fields(self):
        """L1-HD-009：未过 SETUP_TTL 且 dirty 只有 buzz 时不重做 setup（不调 load_people / 认领 / 目录），
        缓存的「谁是谁」字段写回 Round。"""
        made, _, _ = self.patched()
        w = bw.Worker(config=Path("cfg.json"), state_dir=Path("state"))
        w.run({"members"})
        w.run({"buzz"})
        first, second = made[-2], made[-1]
        for step in ("verify_identities", "load_people", "check_claims", "load_directory", "verify_desk"):
            self.assertIn(step, first.calls, step)
            self.assertNotIn(step, second.calls, step)
        self.assertEqual(second.roles, "v0-roles")  # 缓存字段写回了新 Round
        self.assertEqual(second.bot_members, "v0-bot_members")
        self.assertIn("buzz_to_feishu", second.calls)
        self.assertEqual(w.last["hostd"]["setup"], "cached")

    def test_member_change_forces_setup_even_within_ttl(self):
        """L1-HD-010：dirty 含 members（成员变了）时一定重新 setup：上一轮刚做过也重读。"""
        made, _, _ = self.patched()
        w = bw.Worker(config=Path("cfg.json"), state_dir=Path("state"))
        w.run({"members"})
        w.run({"members", "buzz"})
        second = made[-1]
        self.assertIn("load_people", second.calls)
        self.assertIn("load_directory", second.calls)
        self.assertEqual(w.last["hostd"]["setup"], "fresh")

    def test_ordinary_mixed_notice_hint_does_not_force_full_setup(self):
        made, _, _ = self.patched()
        w = bw.Worker(config=Path('cfg.json'), state_dir=Path('state'))
        w.run({'members'})
        w.run({'buzz', 'notice'})
        self.assertNotIn('load_people', made[-1].calls)
        self.assertEqual(w.last['hostd']['setup'], 'cached')

    def test_lost_verdict_runs_no_phase(self):
        """L1-HD-011：认领输了（verdict=lost）这一轮什么都不跑：不做目录 / Desk 校验，更不跑任何 phase。"""
        made, state, saves = self.patched(verdict="lost")
        w = bw.Worker(config=Path("cfg.json"), state_dir=Path("state"))
        rep = w.run({"members", "buzz", "feishu"})
        run = made[-1]
        self.assertIn("verify_identities", run.calls)   # 认输之前先要「谁是谁」
        self.assertIn("check_claims", run.calls)
        for step in ("load_directory", "verify_desk", "reconcile_members", "buzz_to_feishu", "feishu_to_buzz"):
            self.assertNotIn(step, run.calls, step)
        self.assertFalse(any(isinstance(c, tuple) and c[0] == "feishu_to_buzz" for c in run.calls))
        self.assertEqual(rep["hostd"]["verdict"], "lost")
        self.assertTrue(saves)  # state 仍然落盘
        rep2 = w.run({"buzz"})
        self.assertEqual(rep2["hostd"]["verdict"], "lost")
        self.assertFalse(any(isinstance(c, tuple) or c in ("buzz_to_feishu", "reconcile_members")
                             for c in made[-1].calls))

    def test_threads_none_is_passed_through(self):
        """L1-HD-012：threads 是 None（补洞轮）时原样传 None，Round 走全量话题轮询。"""
        made, _, _ = self.patched()
        w = bw.Worker(config=Path("cfg.json"), state_dir=Path("state"))
        w.run({"buzz"})
        w.run({"feishu"}, threads=None)
        self.assertIn(("feishu_to_buzz", None), made[-1].calls)


# ================================ L1-HD-013 feishu_to_buzz(only_threads) ================================


class OnlyThreads(base.TmpCase):
    """复用 FakeWorld：事件说哪个话题有回复，就只读那个话题；None 保持原来的热点 + 轮转。"""

    def world(self):
        w = base.FakeWorld(self.tmp)
        w.members = [m for m in w.members if m["pubkey"] != base.CAROL_PK]
        w.messages = [base.fmsg("om_a", base.ALICE_OPEN, "a", thread_id="omt_om_a"),
                      base.fmsg("om_b", base.ALICE_OPEN, "b", thread_id="omt_om_b")]
        w.threads = {"om_a": [base.fmsg("om_ra", base.BOB_OPEN, "ra", thread_id="omt_om_a")],
                     "om_b": [base.fmsg("om_rb", base.BOB_OPEN, "rb", thread_id="omt_om_b")]}
        return w

    def round_f2b(self, env, w, now, only_threads=None):
        """像 round_command 一样造一个 Round，但 feishu 阶段直接带 only_threads 调（hostd 的调用方式）。"""
        cfg = FGS.load_config(env.config)
        lock = FGS._lock(env.state_dir)
        try:
            clients = FGS._clients(cfg, env.base_env, w, http=w.http_get)
            state = FGS.load_state(env.state_dir)
            report = FGS._new_report()
            w.clock, w.sends = now, 0
            run = FGS.Round(cfg, clients, state, report, now,
                            lambda: FGS.save_state(env.state_dir, state), auth_clock=lambda: now)
            run.verify_identities()
            run.load_people()
            self.assertNotEqual(run.check_claims(False), "lost")
            run.load_directory()
            run.verify_desk()
            if not state.binding:
                state.binding = f"{cfg['channel_id']}|{cfg['chat_id']}"
                state.floor = int(now.timestamp()) - FGS.FEISHU_OVERLAP_SECONDS
                state.buzz_since = state.feishu_since = state.react_since = state.floor
            run.feishu_to_buzz(only_threads=only_threads)
            FGS.prune_state(state)
            FGS.save_state(env.state_dir, state)
            return report
        finally:
            lock.close()

    def test_only_threads_reads_just_that_thread(self):
        """L1-HD-013：only_threads={"om_b"} 时只对 om_b 调 thread_messages，om_a 的新回复不会被读到。"""
        w = self.world()
        env = base.Env(self.tmp)
        first = env.round(w)
        self.assertEqual(first["to_buzz"], 4)  # 两条根 + 两条回复
        self.assertEqual(set(w.thread_polls), {"om_a", "om_b"})
        w.threads["om_b"].append(base.fmsg("om_rb2", base.BOB_OPEN, "rb2", thread_id="omt_om_b",
                                           when=base.NOW + timedelta(minutes=5)))
        w.threads["om_a"].append(base.fmsg("om_ra2", base.BOB_OPEN, "ra2", thread_id="omt_om_a",
                                           when=base.NOW + timedelta(minutes=5)))
        w.thread_polls = []
        rep = self.round_f2b(env, w, base.NOW + timedelta(minutes=6), only_threads={"om_b"})
        self.assertEqual(w.thread_polls, ["om_b"])
        self.assertEqual(rep["to_buzz"], 1)  # 只有 om_b 的新回复
        contents = [c["content"] for c in w.buzz_sends()]
        self.assertIn("[飞书] Bob：rb2", contents)
        self.assertNotIn("[飞书] Bob：ra2", contents)

    def test_none_keeps_the_hot_and_rotation_polling(self):
        """L1-HD-014：only_threads=None 保持原行为：活跃话题都轮询（不只是最新的一个）。"""
        w = self.world()
        env = base.Env(self.tmp)
        env.round(w)
        w.threads["om_a"].append(base.fmsg("om_ra2", base.ALICE_OPEN, "ra2", thread_id="omt_om_a",
                                           when=base.NOW + timedelta(minutes=5)))
        w.thread_polls = []
        rep = self.round_f2b(env, w, base.NOW + timedelta(minutes=6), only_threads=None)
        self.assertEqual(set(w.thread_polls), {"om_a", "om_b"})
        self.assertEqual(rep["to_buzz"], 1)
        self.assertIn("[飞书] Alice：ra2", [c["content"] for c in w.buzz_sends()])

    def test_a_root_the_state_does_not_know_is_not_polled(self):
        """L1-HD-015：only_threads 里 state.threads 还不知道的 root 会被静默跳过——所以 hostd 对
        未知话题要置 threads=None（catch_up 全量读），只点名已知话题。"""
        w = self.world()
        env = base.Env(self.tmp)
        env.round(w)
        w.thread_polls = []
        rep = self.round_f2b(env, w, base.NOW + timedelta(minutes=6), only_threads={"om_brand_new"})
        self.assertEqual(w.thread_polls, [])
        self.assertEqual(rep["to_buzz"], 0)


# ================================ L1-HD-016 secrets_store ================================


@unittest.skipUnless(HAS_CRYPTOGRAPHY, "secrets_store 依赖 cryptography")
class SecretsStore(base.TmpCase):
    def store(self, app_id="cli_x1", secret="app-secret-斑马-42"):
        cfg_dir = self.tmp / "lark-cfg"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps({"apps": [{"appId": app_id}]}), encoding="utf-8")
        data_dir = self.tmp / "lark-data"
        store = data_dir / "lark-cli"
        store.mkdir(parents=True)
        key = bytes(range(32))
        (store / "master.key").write_bytes(key)
        nonce = bytes(range(12))
        (store / f"appsecret_{app_id}.enc").write_bytes(
            nonce + AESGCM(key).encrypt(nonce, secret.encode(), None))
        for path in (cfg_dir / "config.json", store / "master.key", store / f"appsecret_{app_id}.enc"):
            path.chmod(0o600)
        return str(cfg_dir), str(data_dir), secret

    def test_decrypts_the_lark_cli_store(self):
        """L1-HD-016：按 lark-cli 的格式（nonce + AES-256-GCM）能解出应用密钥。"""
        cfg_dir, data_dir, secret = self.store()
        self.assertEqual(secrets_store.app_secret("cli_x1", cfg_dir, data_dir), secret)

    def test_an_app_not_in_this_profile_is_refused_without_leaking_the_secret(self):
        """L1-HD-017：config.json 里没有这个 app_id 就拒绝，错误里不出现任何密钥。"""
        cfg_dir, data_dir, secret = self.store()
        with self.assertRaises(SystemExit) as caught:
            secrets_store.app_secret("cli_other1", cfg_dir, data_dir)
        message = str(caught.exception)
        self.assertIn("这个 lark-cli 目录里没有这个飞书应用", message)
        self.assertNotIn(secret, message)
