"""broker_admin 的测试：令牌按不透明 id 存文件（改名不换令牌、不换文件），配置里只有 sha256。"""
import hashlib, importlib.util, json, os, stat, sys, tempfile, unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = load_module("broker")
A = load_module("broker_admin")


class AdminOps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.cfgp = os.path.join(self.tmp, "config.json"); self.tokdir = os.path.join(self.tmp, "tokens")
        A.init_config(self.cfgp, os.path.join(self.tmp, "admin-token"), port=0, state_dir=os.path.join(self.tmp, "state"))

    def mint(self, label="dev-one", **kw): return A.mint(self.cfgp, self.tokdir, label, **kw)

    def test_init_creates_admin_token_file_0600_and_only_hash_in_config(self):
        tok = open(os.path.join(self.tmp, "admin-token")).read().strip()
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.tmp, "admin-token")).st_mode), 0o600)
        raw = open(self.cfgp).read(); self.assertNotIn(tok, raw); self.assertIn(hashlib.sha256(tok.encode()).hexdigest(), raw)
        self.assertEqual(stat.S_IMODE(os.stat(self.cfgp).st_mode), 0o644)          # 目录 0750 属组 buzz-svc 才是门；文件要让 buzz-svc（other）读得到

    def test_mint_writes_hash_only_and_token_file_named_by_opaque_id(self):
        info = self.mint("dev-one"); cfg = json.load(open(self.cfgp)); ent = cfg["agents"]["dev-one"]
        tok = open(info["token_file"]).read().strip()
        self.assertEqual(ent["token_sha256"], hashlib.sha256(tok.encode()).hexdigest()); self.assertNotIn(tok, json.dumps(cfg))
        self.assertNotIn("dev-one", os.path.basename(info["token_file"]))                 # 文件名不含 agent 名：改名不需要动文件
        self.assertEqual(stat.S_IMODE(os.stat(info["token_file"]).st_mode), 0o600)
        self.assertGreaterEqual(len(tok), 40)

    def test_mint_policy_options(self):
        self.mint("dev-two", docker=False, max_timeout_s=900, max_upload_mb=50)
        pol = json.load(open(self.cfgp))["agents"]["dev-two"]["job"]
        self.assertEqual((pol["docker"], pol["max_timeout_s"], pol["max_upload_mb"]), (False, 900, 50))
        self.mint("dev-three"); self.assertTrue(json.load(open(self.cfgp))["agents"]["dev-three"]["job"]["docker"])

    def test_bad_or_duplicate_labels_refused(self):
        for bad in ("", "Has Space", "../x", "a" * 80, "UPPER"):
            with self.assertRaises(ValueError): self.mint(bad)
        self.mint("dev-one")
        with self.assertRaises(ValueError): self.mint("dev-one")

    def test_rename_keeps_the_same_token_and_file(self):
        info = self.mint("old-name"); tok = open(info["token_file"]).read().strip()
        A.rename(self.cfgp, "old-name", "new-name"); cfg = json.load(open(self.cfgp))
        self.assertNotIn("old-name", cfg["agents"]); self.assertEqual(cfg["agents"]["new-name"]["token_sha256"], hashlib.sha256(tok.encode()).hexdigest())
        self.assertTrue(os.path.exists(info["token_file"]))
        with self.assertRaises(ValueError): A.rename(self.cfgp, "missing", "x1")
        self.mint("other")
        with self.assertRaises(ValueError): A.rename(self.cfgp, "new-name", "other")

    def test_revoke_removes_entry_and_token_file(self):
        info = self.mint("gone"); A.revoke(self.cfgp, self.tokdir, "gone")
        self.assertNotIn("gone", json.load(open(self.cfgp))["agents"]); self.assertFalse(os.path.exists(info["token_file"]))
        with self.assertRaises(ValueError): A.revoke(self.cfgp, self.tokdir, "gone")

    def test_list_never_shows_secrets(self):
        info = self.mint("dev-one"); tok = open(info["token_file"]).read().strip()
        out = json.dumps(A.list_agents(self.cfgp)); self.assertIn("dev-one", out)
        self.assertNotIn(tok, out); self.assertNotIn(hashlib.sha256(tok.encode()).hexdigest(), out)

    def test_rotate_admin_invalidates_the_old_token_and_keeps_the_file_private(self):
        af = os.path.join(self.tmp, "admin-token"); old = open(af).read().strip(); app = B.App(self.cfgp)
        self.assertTrue(app.is_admin(old))
        A.rotate_admin(self.cfgp, af); new = open(af).read().strip()
        self.assertNotEqual(old, new); self.assertFalse(app.is_admin(old)); self.assertTrue(app.is_admin(new))
        self.assertEqual(stat.S_IMODE(os.stat(af).st_mode), 0o600); self.assertNotIn(new, open(self.cfgp).read())

    def test_broker_picks_up_changes_without_restart(self):
        app = B.App(self.cfgp)
        info = self.mint("dev-one"); tok = open(info["token_file"]).read().strip()
        self.assertEqual(app.auth(tok), "dev-one")
        A.rename(self.cfgp, "dev-one", "dev-renamed"); self.assertEqual(app.auth(tok), "dev-renamed")
        A.revoke(self.cfgp, self.tokdir, "dev-renamed"); self.assertIsNone(app.auth(tok))


if __name__ == "__main__": unittest.main()
