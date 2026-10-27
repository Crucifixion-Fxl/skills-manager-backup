#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rs_auth.py 纯逻辑单元测试 (不碰网络)。

跑法 / Run:
  cd scripts && python3 -m pytest tests/test_rs_auth.py -v
或无 pytest 时:
  cd scripts && python3 tests/test_rs_auth.py

覆盖 / Covers:
  - resolve_ldap_routing: 推导 / 冲突拒绝 / 非法前缀
  - token 优先级: 显式 env > 缓存 > 自动登录; 全无 → ManualLoginRequired (降级指引)
  - 缓存: 0600 权限、TTL 边界 (RENEW_MARGIN)、invalidate 幂等、损坏文件容错
  - http_login 失败文案不含密码
"""

import json
import os
import stat
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rs_auth  # noqa: E402

RS_HOST = "https://example-rs.invalid/api"
PROD_RS_HOST = "https://example-rs-prod.invalid"


class RoutingTests(unittest.TestCase):
    """resolve_ldap_routing: 推导与冲突。"""

    def test_derive_rs_env_from_node_name(self):
        self.assertEqual(
            rs_auth.resolve_ldap_routing("prod-us", None, None),
            ("prod-us", "prod", "prod-us"))
        self.assertEqual(
            rs_auth.resolve_ldap_routing("staging-cn", None, None),
            ("staging-cn", "staging", "staging-cn"))

    def test_explicit_matching_triple_passes(self):
        self.assertEqual(
            rs_auth.resolve_ldap_routing("prod-eu", "prod", "prod-eu"),
            ("prod-eu", "prod", "prod-eu"))

    def test_node_name_defaults_to_env(self):
        self.assertEqual(
            rs_auth.resolve_ldap_routing("staging-us", "staging", None),
            ("staging-us", "staging", "staging-us"))

    def test_cross_env_rs_env_rejected(self):
        # 2026-10-08 事故复现: staging RS + prod 节点 必须被拒绝。
        with self.assertRaises(rs_auth.RouteMismatch) as cm:
            rs_auth.resolve_ldap_routing("prod-us", "staging", "prod-us")
        self.assertIn("accessKey无效", str(cm.exception))

    def test_env_node_mismatch_rejected(self):
        with self.assertRaises(rs_auth.RouteMismatch):
            rs_auth.resolve_ldap_routing("staging-us", "staging", "prod-us")

    def test_unknown_prefix_rejected(self):
        with self.assertRaises(rs_auth.RouteMismatch):
            rs_auth.resolve_ldap_routing("dev-us", None, None)

    def test_undeclared_node_rejected(self):
        """来源:CI code-review r2 P2 —— 前缀合法但节点未声明 (如 prod-unknown) 也要拦。"""
        for node in ("prod-unknown", "staging-xx", "produs"):
            with self.assertRaises(rs_auth.RouteMismatch, msg=node):
                rs_auth.resolve_ldap_routing(node, None, None)
        # 显式三元组里的未声明节点同样拒绝。
        with self.assertRaises(rs_auth.RouteMismatch):
            rs_auth.resolve_ldap_routing("prod-unknown", "prod", "prod-unknown")

    def test_both_missing_rejected(self):
        with self.assertRaises(rs_auth.RouteMismatch):
            rs_auth.resolve_ldap_routing(None, None, None)


class TokenCacheTests(unittest.TestCase):
    """缓存读写、权限、TTL 边界、invalidate。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._env_patch = mock.patch.dict(
            os.environ, {"RS_AUTH_CACHE_DIR": self._tmp.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_save_and_load_roundtrip(self):
        rs_auth._save_cached_token("prod", "tok-1", 259200, now=1000.0)
        # now=1000, expiresAt=260200, margin 300 → 1000 < 259900 有效。
        self.assertEqual(rs_auth.load_cached_token("prod", now=1000.0), "tok-1")

    def test_cache_file_is_0600(self):
        rs_auth._save_cached_token("prod", "tok-1", 259200, now=1000.0)
        path = rs_auth._cache_path("prod")
        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode, 0o600, f"cache file mode {oct(mode)} != 0600")

    def test_expired_cache_returns_none(self):
        rs_auth._save_cached_token("prod", "tok-1", 259200, now=1000.0)
        # 恰好落在 margin 内 (expiresAt-300 边界) 视为过期。
        self.assertIsNone(
            rs_auth.load_cached_token("prod", now=1000.0 + 259200 - 300))
        # 未到 margin 仍有效。
        self.assertEqual(
            rs_auth.load_cached_token("prod", now=1000.0 + 259200 - 301),
            "tok-1")

    def test_corrupt_cache_returns_none(self):
        rs_auth._cache_path("prod").write_text("not-json{", encoding="utf-8")
        # chmod 0600 确保走到"内容损坏"分支, 而不是先被权限检查拒绝 (权限有独立用例)。
        os.chmod(rs_auth._cache_path("prod"), 0o600)
        self.assertIsNone(rs_auth.load_cached_token("prod"))

    def test_loosened_cache_permissions_rejected(self):
        """来源:CI code-review r2 P2 —— 0644 缓存 fail-closed 视为未命中。"""
        rs_auth._save_cached_token("prod", "tok-1", 259200, now=1000.0)
        os.chmod(rs_auth._cache_path("prod"), 0o644)
        self.assertIsNone(rs_auth.load_cached_token("prod", now=1000.0))

    def test_symlink_cache_rejected(self):
        """符号链接非常规文件 (lstat) → 拒绝, 防缓存路径被指向任意文件。"""
        target = Path(self._tmp.name) / "real-token.json"
        target.write_text('{"token":"tok-x","expiresAt":9999999999.0}',
                          encoding="utf-8")
        os.chmod(target, 0o600)
        link = rs_auth._cache_path("prod")
        link.symlink_to(target)
        self.assertIsNone(rs_auth.load_cached_token("prod"))

    def test_other_owner_cache_rejected(self):
        """owner != 当前 euid → 拒绝 (平台无 geteuid 时跳过该维度)。"""
        if not hasattr(os, "geteuid"):
            self.skipTest("no os.geteuid on this platform")
        rs_auth._save_cached_token("prod", "tok-1", 259200, now=1000.0)
        with mock.patch("os.geteuid", return_value=os.geteuid() + 1):
            self.assertIsNone(rs_auth.load_cached_token("prod", now=1000.0))
        # 恢复真实 euid 后同一缓存可用。
        self.assertEqual(rs_auth.load_cached_token("prod", now=1000.0), "tok-1")

    def test_token_with_wrong_type_rejected(self):
        """来源:CI code-review r3 建议 4 —— token 非 str (如 dict) → 视为未命中。"""
        path = rs_auth._cache_path("prod")
        path.write_text('{"token": {"x": 1}, "expiresAt": 9999999999.0}',
                        encoding="utf-8")
        os.chmod(path, 0o600)
        self.assertIsNone(rs_auth.load_cached_token("prod", now=1000.0))


    def test_invalidate_is_idempotent(self):
        rs_auth._save_cached_token("prod", "tok-1", 259200, now=1000.0)
        rs_auth.invalidate_cached_token("prod")
        rs_auth.invalidate_cached_token("prod")  # 第二次不抛
        self.assertIsNone(rs_auth.load_cached_token("prod", now=1000.0))

    def test_cache_is_per_rs_env(self):
        rs_auth._save_cached_token("staging", "tok-stg", 259200, now=1000.0)
        rs_auth._save_cached_token("prod", "tok-prd", 259200, now=1000.0)
        self.assertEqual(rs_auth.load_cached_token("staging", now=1000.0),
                         "tok-stg")
        self.assertEqual(rs_auth.load_cached_token("prod", now=1000.0),
                         "tok-prd")


class ManualInstructionsTests(unittest.TestCase):
    """降级指引的安全不变量 (来源:CI code-review r3 P1 红线)。"""

    def test_password_never_in_curl_command_line(self):
        """任何含 curl 的行不得同时含 password —— 密码只能经 read -s + stdin。"""
        for rs_env, host in (("staging", "https://rs.invalid"),
                             ("prod", "https://rs-prod.invalid")):
            text = rs_auth.manual_login_instructions(rs_env, host)
            curl_lines = [ln for ln in text.splitlines() if "curl" in ln]
            self.assertTrue(curl_lines, "指引必须含 curl 登录命令")
            for ln in curl_lines:
                self.assertNotIn("password", ln,
                                 f"密码字段出现在 curl 命令行: {ln}")
                self.assertNotIn("-d ", ln,
                                 f"curl 用了命令行 data 参数: {ln}")
            # 安全输入路径存在。
            self.assertIn("read -rs", text)

    def test_guidance_uses_real_json_encoder(self):
        """来源:CI code-review r4 P2 —— 指引不得用 printf %s 直拼 JSON。"""
        text = rs_auth.manual_login_instructions("prod", "https://rs.invalid")
        # 禁止模板直拼 (引号/反斜杠/换行密码会产出非法 JSON)。
        self.assertNotIn("printf '{\"", text)
        # 必须由真正的 JSON encoder 编码。
        self.assertIn("json.dumps", text)

    def test_guided_encoder_equivalent_handles_special_chars(self):
        """指引中编码器的等价实现: NUL 分隔 + json.dumps, 特殊字符往返无损。"""
        cn = 'cn"with\backslash'
        pw = 'p@ss "quoted" \\ slash\nnewline'
        raw = "\0".join([cn, pw])           # 等价 printf '%s\0%s'
        a = raw.split("\0")                  # 等价 python3 -c 里的 split
        payload = json.dumps({"ldapCn": a[0], "password": a[1]})
        self.assertEqual(json.loads(payload),
                         {"ldapCn": cn, "password": pw})

class ResolveTokenTests(unittest.TestCase):
    """resolve_token 优先级与降级。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        env = {"RS_AUTH_CACHE_DIR": self._tmp.name}
        for k in (rs_auth.CN_ENV, rs_auth.PASSWORD_ENV, "SKILL_LDAP_TOKEN"):
            env[k] = ""  # 显式置空, 防宿主机环境泄漏进测试
        self._env_patch = mock.patch.dict(os.environ, env)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_priority_env_var_over_cache_and_login(self):
        with mock.patch.dict(os.environ, {"SKILL_LDAP_TOKEN": "tok-env"}):
            rs_auth._save_cached_token("prod", "tok-cache", 259200,
                                       now=time.time())
            token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-env", "env"))

    def test_priority_cache_over_login(self):
        # r6 起缓存绑定主体: CN=cn 时只复用 ldapCn=cn 的缓存。
        rs_auth._save_cached_token("prod", "tok-cache", 259200,
                                   now=time.time(), ldap_cn="cn")
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: "pw"}):
            with mock.patch.object(rs_auth, "http_login") as login:
                token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-cache", "cache"))
        login.assert_not_called()

    def test_auto_login_writes_cache(self):
        login_data = {"token": "tok-auto", "ldapCn": "cn",
                      "expiresInSeconds": 259200}
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: "pw"}):
            with mock.patch.object(rs_auth, "http_login",
                                   return_value=login_data) as login:
                token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-auto", "auto-login"))
        login.assert_called_once()
        # 缓存已写入, 第二次不再登录。
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: "pw"}):
            with mock.patch.object(rs_auth, "http_login") as login2:
                token2, source2 = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token2, source2), ("tok-auto", "cache"))
        login2.assert_not_called()

    def test_missing_password_env_degrades_to_manual(self):
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: ""}):
            with self.assertRaises(rs_auth.ManualLoginRequired) as cm:
                rs_auth.resolve_token("prod", RS_HOST)
        # 指引里带 rs_host 与两种方式。
        msg = str(cm.exception)
        self.assertIn(RS_HOST, msg)
        self.assertIn("skill-api/login", msg)
        self.assertIn(rs_auth.PASSWORD_ENV, msg)

    def test_reauth_after_401_bypasses_stale_cache(self):
        rs_auth._save_cached_token("prod", "tok-stale", 259200, now=time.time())
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: "pw"}):
            with mock.patch.object(
                    rs_auth, "http_login",
                    return_value={"token": "tok-fresh",
                                  "expiresInSeconds": 259200}):
                token, source = rs_auth.reauth_after_401("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-fresh", "auto-login"))

    def test_login_failure_message_has_no_password(self):
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: "SECRET-PW"}):
            with mock.patch.object(
                    rs_auth, "http_login",
                    side_effect=RuntimeError("登录失败: code=103 msg=密码错误")):
                with self.assertRaises(RuntimeError) as cm:
                    rs_auth.resolve_token("prod", RS_HOST)
        self.assertNotIn("SECRET-PW", str(cm.exception))

    def test_password_whitespace_preserved_not_stripped(self):
        """来源:CI code-review r4 建议 2 —— 首尾空白原样送登录, 不静默裁剪凭据。"""
        raw_pw = "  pa ss  "
        login_data = {"token": "tok-ws", "ldapCn": "cn",
                      "expiresInSeconds": 3600}
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: raw_pw}):
            with mock.patch.object(rs_auth, "http_login",
                                   return_value=login_data) as login:
                token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-ws", "auto-login"))
        # http_login 收到的是未裁剪的原值。
        self.assertEqual(login.call_args[0][3], raw_pw)

    def test_whitespace_only_password_treated_as_unset(self):
        """全空白密码视为未设置 → 降级 ManualLoginRequired。"""
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "cn",
                                          rs_auth.PASSWORD_ENV: "   "}):
            with mock.patch.object(rs_auth, "http_login") as login:
                with self.assertRaises(rs_auth.ManualLoginRequired):
                    rs_auth.resolve_token("prod", RS_HOST)
        login.assert_not_called()

    def _login_data(self, cn, token):
        return {"token": token, "ldapCn": cn, "expiresInSeconds": 259200}

    def test_account_switch_does_not_reuse_old_token(self):
        """来源:CI code-review r6 P1 —— 缓存绑定主体, 切换账号不复用旧 token。"""
        rs_auth._save_cached_token("prod", "tok-alice", 259200,
                                   now=time.time(), ldap_cn="alice")
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "bob",
                                          rs_auth.PASSWORD_ENV: "pw"}):
            with mock.patch.object(
                    rs_auth, "http_login",
                    return_value=self._login_data("bob", "tok-bob")) as login:
                token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-bob", "auto-login"))
        login.assert_called_once()                       # alice 缓存被跳过
        self.assertEqual(login.call_args[0][2], "bob")   # 用 bob 登录
        # 缓存已被 bob 的 token 覆盖 (重新绑定)。
        self.assertEqual(
            rs_auth.load_cached_token("prod", expect_cn="bob"), "tok-bob")
        self.assertIsNone(
            rs_auth.load_cached_token("prod", expect_cn="alice"))

    def test_same_principal_cache_reused(self):
        """同主体缓存正常复用, 不重登。"""
        rs_auth._save_cached_token("prod", "tok-alice", 259200,
                                   now=time.time(), ldap_cn="alice")
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "alice",
                                          rs_auth.PASSWORD_ENV: "pw"}):
            with mock.patch.object(rs_auth, "http_login") as login:
                token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-alice", "cache"))
        login.assert_not_called()

    def test_legacy_cache_without_cn_rejected_when_cn_set(self):
        """旧格式缓存 (无 ldapCn 字段) 在 CN 已知时 fail-closed 视为未命中。"""
        path = rs_auth._cache_path("prod")
        path.write_text(json.dumps(
            {"rsEnv": "prod", "token": "tok-legacy",
             "expiresAt": time.time() + 259200}), encoding="utf-8")
        os.chmod(path, 0o600)
        with mock.patch.dict(os.environ, {rs_auth.CN_ENV: "alice",
                                          rs_auth.PASSWORD_ENV: "pw"}):
            with mock.patch.object(
                    rs_auth, "http_login",
                    return_value=self._login_data("alice", "tok-new")) as login:
                token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-new", "auto-login"))
        login.assert_called_once()

    def test_cache_accepted_when_principal_unknown(self):
        """CN 未配置 (无自动登录诉求) 时缓存可用 —— 主体未知不阻断。"""
        rs_auth._save_cached_token("prod", "tok-alice", 259200,
                                   now=time.time(), ldap_cn="alice")
        token, source = rs_auth.resolve_token("prod", RS_HOST)
        self.assertEqual((token, source), ("tok-alice", "cache"))

    def test_cache_dir_symlink_rejected(self):
        """来源:CI code-review r6 建议 —— 缓存目录本身是符号链接 → 拒绝。"""
        import tempfile
        with tempfile.TemporaryDirectory() as real:
            real_dir = Path(real) / "real"
            real_dir.mkdir()
            file = real_dir / "token-prod.json"
            file.write_text(json.dumps(
                {"rsEnv": "prod", "token": "tok-x", "ldapCn": "cn",
                 "expiresAt": time.time() + 259200}), encoding="utf-8")
            os.chmod(file, 0o600)
            os.chmod(real_dir, 0o700)
            link_dir = Path(real) / "link"
            link_dir.symlink_to(real_dir)
            with mock.patch.dict(os.environ,
                                 {"RS_AUTH_CACHE_DIR": str(link_dir)}):
                self.assertIsNone(rs_auth.load_cached_token("prod"))
            # 对照: 直连真实目录可用。
            with mock.patch.dict(os.environ,
                                 {"RS_AUTH_CACHE_DIR": str(real_dir)}):
                self.assertEqual(rs_auth.load_cached_token("prod"), "tok-x")

    def test_save_rejects_symlink_cache_dir(self):
        """来源:CI code-review r7 P1 —— 写入侧同标准: 目录是符号链接 → 拒绝写入。"""
        import tempfile
        with tempfile.TemporaryDirectory() as real:
            victim = Path(real) / "victim"      # 攻击者可读的目标目录
            victim.mkdir()
            os.chmod(victim, 0o700)
            link_dir = Path(real) / "link"
            link_dir.symlink_to(victim)
            with mock.patch.dict(os.environ,
                                 {"RS_AUTH_CACHE_DIR": str(link_dir)}):
                with self.assertRaises(RuntimeError):
                    rs_auth._save_cached_token("prod", "tok-secret", 3600)
            # token 没有被写进被符号链接指向的目录。
            self.assertEqual(list(victim.iterdir()), [])

    def test_save_rejects_loose_cache_dir(self):
        """写入侧: 目录 group/other 位有权限 → RuntimeError, 不落盘。"""
        import tempfile
        with tempfile.TemporaryDirectory() as real:
            d = Path(real)
            os.chmod(d, 0o750)
            with mock.patch.dict(os.environ,
                                 {"RS_AUTH_CACHE_DIR": str(d)}):
                with self.assertRaises(RuntimeError):
                    rs_auth._save_cached_token("prod", "tok-secret", 3600)
            self.assertEqual(list(d.iterdir()), [])

    def test_save_rejects_foreign_owner_cache_dir(self):
        """写入侧: 目录 owner 非当前用户 → RuntimeError (无 geteuid 平台跳过)。"""
        if not hasattr(os, "geteuid"):
            self.skipTest("no os.geteuid on this platform")
        import tempfile
        with tempfile.TemporaryDirectory() as real:
            d = Path(real)
            with mock.patch("os.geteuid", return_value=os.geteuid() + 1):
                with mock.patch.dict(os.environ,
                                     {"RS_AUTH_CACHE_DIR": str(d)}):
                    with self.assertRaises(RuntimeError):
                        rs_auth._save_cached_token("prod", "tok", 3600)

    def test_loose_cache_dir_rejected(self):
        """缓存目录 group/other 位有权限 → fail-closed。"""
        import tempfile
        with tempfile.TemporaryDirectory() as real:
            d = Path(real)
            file = d / "token-prod.json"
            file.write_text(json.dumps(
                {"rsEnv": "prod", "token": "tok-x", "ldapCn": "cn",
                 "expiresAt": time.time() + 259200}), encoding="utf-8")
            os.chmod(file, 0o600)
            os.chmod(d, 0o750)
            with mock.patch.dict(os.environ,
                                 {"RS_AUTH_CACHE_DIR": str(d)}):
                self.assertIsNone(rs_auth.load_cached_token("prod"))
            os.chmod(d, 0o700)
            with mock.patch.dict(os.environ,
                                 {"RS_AUTH_CACHE_DIR": str(d)}):
                self.assertEqual(rs_auth.load_cached_token("prod"), "tok-x")


class UnauthenticatedDetectionTests(unittest.TestCase):
    """looks_unauthenticated: HTTP 401 与 HTTP 200+code/result=101 两种形态。"""

    def test_http_401(self):
        self.assertTrue(rs_auth.looks_unauthenticated(401, None))

    def test_http_200_code_101(self):
        # 实测部署版 RS 行为 (2026-10-08): HTTP 200 + {"code":101,"msg":"未登录"}。
        self.assertTrue(
            rs_auth.looks_unauthenticated(200, {"code": 101, "msg": "未登录"}))

    def test_http_200_result_101(self):
        self.assertTrue(
            rs_auth.looks_unauthenticated(200, {"result": 101, "msg": "x"}))

    def test_normal_responses_are_not_unauthenticated(self):
        self.assertFalse(rs_auth.looks_unauthenticated(200, {"code": 0}))
        self.assertFalse(
            rs_auth.looks_unauthenticated(
                400, {"code": 102, "message": "accessKey无效"}))
        self.assertFalse(rs_auth.looks_unauthenticated(500, None))
        self.assertFalse(rs_auth.looks_unauthenticated(200, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
