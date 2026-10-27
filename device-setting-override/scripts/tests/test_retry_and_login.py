#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三脚本 401/未登录重试 wrapper 与 http_login 真实分支的单元测试 (不碰网络)。

跑法 / Run:
  cd scripts && python3 -m pytest tests/ -q

背景: code-review (2026-10-08) 指出三份手工复制的重试 wrapper 零测试、
http_login 四个失败分支无直测 (原"密码不进异常文案"测试把 http_login 整个
mock 掉, 属假绿)。本文件补齐这两块。
"""

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rs_auth            # noqa: E402
import verify             # noqa: E402  (stdlib only)
import admin_cli          # noqa: E402  (requests)
import upsert             # noqa: E402  (openpyxl + requests)

RS_HOST = "https://example-rs.invalid/api"


class _FakeResp:
    """requests.Response 的最小替身: 只需 status_code + json()。"""

    def __init__(self, status_code, body_dict):
        self.status_code = status_code
        self._body = body_dict

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _ctx(token_source="cache"):
    return admin_cli.AuthCtx(
        mode="ldap", rs_host=RS_HOST, ldap_token="tok-old", node_name="prod-us",
        rs_env="prod", token_source=token_source, token_env="SKILL_LDAP_TOKEN")


class RetryWrapperTests(unittest.TestCase):
    """admin_cli._ldap_invoke / upsert._send_ldap / verify._admin_call_get。

    钉住四个断言 (code-review C1): 101→重登→重试一次; env 源不重试;
    重登降级 exit 3; 二次失效不再重试。
    """

    def _run_requests_style(self, module, once_name, invoke):
        """requests 版 (admin_cli / upsert) 共用矩阵。"""
        once = getattr(module, once_name)
        reauth = "rs_auth.reauth_after_401"

        # 1) 200+code=101 + cache 源 → reauth 一次, 第二次响应返回
        with mock.patch.object(module, once_name,
                               side_effect=[_FakeResp(200, {"code": 101, "msg": "未登录"}),
                                            _FakeResp(200, {"result": 0})]) as m_once:
            with mock.patch(reauth, return_value=("tok-new", "auto-login")) as m_re:
                ctx = _ctx("cache")
                resp = invoke(ctx)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(m_once.call_count, 2)
        m_re.assert_called_once()
        self.assertEqual(ctx.ldap_token, "tok-new")

        # 2) env 源 → 不 reauth, 响应原样返回 (含 401)
        with mock.patch.object(module, once_name,
                               return_value=_FakeResp(401, None)) as m_once:
            with mock.patch(reauth) as m_re:
                ctx = _ctx("env")
                resp = invoke(ctx)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(m_once.call_count, 1)
        m_re.assert_not_called()

        # 3) reauth 抛 ManualLoginRequired → SystemExit(3)
        with mock.patch.object(module, once_name,
                               return_value=_FakeResp(200, {"code": 101})):
            with mock.patch(reauth, side_effect=rs_auth.ManualLoginRequired("manual")):
                ctx = _ctx("cache")
                with self.assertRaises(SystemExit) as cm:
                    invoke(ctx)
        self.assertEqual(cm.exception.code, rs_auth.EXIT_MANUAL_LOGIN)

        # 4) 重试后仍 101 → 原样返回, 不再第二次 reauth
        with mock.patch.object(module, once_name,
                               side_effect=[_FakeResp(200, {"code": 101}),
                                            _FakeResp(200, {"code": 101})]) as m_once:
            with mock.patch(reauth, return_value=("tok-new2", "auto-login")) as m_re:
                ctx = _ctx("cache")
                resp = invoke(ctx)
        self.assertEqual(resp._body, {"code": 101})
        self.assertEqual(m_once.call_count, 2)
        m_re.assert_called_once()

        # 5) 正常成功 → 不 reauth
        with mock.patch.object(module, once_name,
                               return_value=_FakeResp(200, {"result": 0, "data": []})) as m_once:
            with mock.patch(reauth) as m_re:
                ctx = _ctx("cache")
                resp = invoke(ctx)
        self.assertEqual(resp._body["result"], 0)
        self.assertEqual(m_once.call_count, 1)
        m_re.assert_not_called()

    def test_admin_cli_retry_matrix(self):
        self._run_requests_style(
            admin_cli, "_ldap_invoke_once",
            lambda ctx: admin_cli._ldap_invoke(ctx, "GET", "/p", None, None, 5))

    def test_upsert_retry_matrix(self):
        self._run_requests_style(
            upsert, "_send_ldap_once",
            lambda ctx: upsert._send_ldap(ctx, "POST", "/p", None, {}, 5))

    def test_verify_retry_matrix_stdlib(self):
        """verify.py (urllib) 版: 200+101 重试 / HTTPError 401 重试 / 其余原样。"""
        reauth = "rs_auth.reauth_after_401"
        mk = lambda: verify.AuthCtx(
            mode="ldap", rs_host=RS_HOST, ldap_token="tok-old",
            node_name="prod-us", rs_env="prod",
            token_source="cache", token_env="SKILL_LDAP_TOKEN")

        # 1) HTTP 200 + code=101 → 重试一次
        with mock.patch.object(verify, "_admin_call_get_ldap_once",
                               side_effect=[{"code": 101, "msg": "未登录"},
                                            {"result": 0, "data": []}]) as m_once:
            with mock.patch(reauth, return_value=("tok-new", "auto-login")) as m_re:
                body = verify._admin_call_get(mk(), "/stats")
        self.assertEqual(body["result"], 0)
        self.assertEqual(m_once.call_count, 2)
        m_re.assert_called_once()

        # 2) HTTPError 401 → 重试一次
        err401 = urllib.error.HTTPError(
            "u", 401, "Unauthorized", None, io.BytesIO(b'{"code":101}'))
        with mock.patch.object(verify, "_admin_call_get_ldap_once",
                               side_effect=[err401, {"result": 0}]) as m_once:
            with mock.patch(reauth, return_value=("tok-new", "auto-login")):
                body = verify._admin_call_get(mk(), "/stats")
        self.assertEqual(body["result"], 0)
        self.assertEqual(m_once.call_count, 2)

        # 3) HTTPError 500 → 原样复抛, 不 reauth
        err500 = urllib.error.HTTPError("u", 500, "ISE", None, io.BytesIO(b"{}"))
        with mock.patch.object(verify, "_admin_call_get_ldap_once",
                               side_effect=err500):
            with mock.patch(reauth) as m_re:
                with self.assertRaises(urllib.error.HTTPError):
                    verify._admin_call_get(mk(), "/stats")
        m_re.assert_not_called()

        # 4) env 源 + 401 → 复抛, 不 reauth (cache 源的重试行为已由 case 2 覆盖)
        env_ctx = verify.AuthCtx(mode="ldap", rs_host=RS_HOST, ldap_token="t",
                                 node_name="prod-us", rs_env="prod",
                                 token_source="env", token_env="SKILL_LDAP_TOKEN")
        with mock.patch.object(verify, "_admin_call_get_ldap_once",
                               side_effect=urllib.error.HTTPError(
                                   "u", 401, "UA", None, io.BytesIO(b"{}"))):
            with mock.patch(reauth) as m_re:
                with self.assertRaises(urllib.error.HTTPError):
                    verify._admin_call_get(env_ctx, "/stats")
        m_re.assert_not_called()

        # 5) 业务失败 (result=-1) → 原样返回
        with mock.patch.object(verify, "_admin_call_get_ldap_once",
                               return_value={"result": -1, "msg": "upstream HTTP 400"}) as m_once:
            with mock.patch(reauth) as m_re:
                body = verify._admin_call_get(mk(), "/stats")
        self.assertEqual(body["result"], -1)
        self.assertEqual(m_once.call_count, 1)
        m_re.assert_not_called()

        # 6) HTTPError 400 + 信封 code=101 → 也重试 (r3 建议 3: 与 requests 版统一,
        #    旧行为是只认 401、此形态直接复抛)
        err400 = urllib.error.HTTPError(
            "u", 400, "Bad Request", None,
            io.BytesIO('{"code":101,"msg":"未登录"}'.encode()))
        with mock.patch.object(verify, "_admin_call_get_ldap_once",
                               side_effect=[err400, {"result": 0}]) as m_once:
            with mock.patch(reauth, return_value=("tok-new", "auto-login")):
                body = verify._admin_call_get(mk(), "/stats")
        self.assertEqual(body["result"], 0)
        self.assertEqual(m_once.call_count, 2)

        # 7) HTTPError 400 + 非 101 信封 → 仍复抛 (不能把业务错误当 token 失效)
        err4xx = urllib.error.HTTPError(
            "u", 400, "Bad Request", None, io.BytesIO(b'{"code":102}'))
        with mock.patch.object(verify, "_admin_call_get_ldap_once",
                               side_effect=err4xx):
            with mock.patch(reauth) as m_re:
                with self.assertRaises(urllib.error.HTTPError):
                    verify._admin_call_get(mk(), "/stats")
        m_re.assert_not_called()


class CliEntryTests(unittest.TestCase):
    """CLI 入口级 exit code (来源:CI code-review r3 建议 4)。

    exit 2 = 参数/路由错误 (argparse choices 或 RouteMismatch);
    exit 3 = 无可用凭据降级 ManualLoginRequired。
    三脚本共用 rs_auth 与同构 build_auth_ctx, 这里抽验 admin_cli + verify 两个入口。
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        # 空 cache 目录 + 清掉所有凭据环境变量 → 稳定落入降级路径。
        self._env_patch = mock.patch.dict(
            os.environ, {"RS_AUTH_CACHE_DIR": self._tmp.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        cred_vars = ("SKILL_LDAP_TOKEN", "RS_LDAP_CN", "RS_LDAP_PASSWORD")
        saved = {var: os.environ.get(var) for var in cred_vars}
        for var in cred_vars:
            os.environ.pop(var, None)

        def _restore_creds():
            for var, val in saved.items():
                if val is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = val
        self.addCleanup(_restore_creds)
        self.addCleanup(self._tmp.cleanup)

    def _assert_undeclared_node_exit_2(self, argv, parse, build):
        """未声明 node-name 在 ldap 路径 exit 2 (argparse 不再限词表, r9 F-1 后
        由 resolve_ldap_routing 集中拦截 —— 报错附合法词表)。"""
        with mock.patch.object(sys, "argv", argv):
            args = parse()
            with self.assertRaises(SystemExit) as cm:
                build(args)
        self.assertEqual(cm.exception.code, 2)

    def test_admin_cli_undeclared_node_exit_2(self):
        self._assert_undeclared_node_exit_2(
            ["admin_cli", "--env", "prod-us", "--node-name", "prod-unknown",
             "stats"],
            admin_cli.parse_args, admin_cli.build_auth_ctx)

    def test_verify_undeclared_node_exit_2(self):
        self._assert_undeclared_node_exit_2(
            ["verify", "--env", "prod-us", "--node-name", "prod-unknown"],
            verify.parse_args, verify.build_auth_ctx)

    def test_aksk_mode_accepts_non_allowlist_node_name(self):
        """来源:CI code-review r9 F-1 —— --node-name 词表只约束 ldap 模式;
        aksk 模式 (node-name 不参与路由) 任意历史值必须能通过参数解析。"""
        with mock.patch.dict(os.environ,
                             {"IOT_ADMIN_AK": "ak", "IOT_ADMIN_SK": "c2s="},
                             clear=False):
            with mock.patch.object(
                    sys, "argv",
                    ["admin_cli", "--env", "prod-us", "--auth", "aksk",
                     "--node-name", "legacy-console-node-1", "stats"]):
                args = admin_cli.parse_args()   # 不再 exit 2
                ctx = admin_cli.build_auth_ctx(args)
        self.assertEqual(ctx.mode, "aksk")
        self.assertEqual(ctx.node_name, None)   # aksk ctx 不携带 node_name

    def test_admin_cli_no_credentials_exit_3(self):
        with mock.patch.object(sys, "argv",
                               ["admin_cli", "--env", "prod-us", "stats"]):
            args = admin_cli.parse_args()
            with self.assertRaises(SystemExit) as cm:
                admin_cli.build_auth_ctx(args)
        self.assertEqual(cm.exception.code, rs_auth.EXIT_MANUAL_LOGIN)

    def test_admin_cli_routing_conflict_exit_2(self):
        with mock.patch.object(sys, "argv",
                               ["admin_cli", "--env", "prod-us", "--rs-env", "staging", "stats"]):
            args = admin_cli.parse_args()
            with self.assertRaises(SystemExit) as cm:
                admin_cli.build_auth_ctx(args)
        self.assertEqual(cm.exception.code, 2)

    def test_upsert_routing_conflict_exit_2(self):
        """对称入口 (来源:CI code-review r6 建议 3) —— upsert 路由冲突 exit 2。"""
        with mock.patch.object(sys, "argv", [
                "upsert", "--env", "prod-us", "--rs-env", "staging",
                "--input", "x.xlsx", "--sensitive-json", "{}",
                "--insensitive-json", "{}", "--experiment-prefix", "p",
                "--expire-at", "0"]):
            args = upsert.parse_args()
            with self.assertRaises(SystemExit) as cm:
                upsert.build_auth_ctx(args, "https://target.invalid")
        self.assertEqual(cm.exception.code, 2)

    def test_upsert_no_credentials_exit_3(self):
        """对称入口 —— upsert 无凭据降级 exit 3。"""
        with mock.patch.object(sys, "argv", [
                "upsert", "--env", "prod-us",
                "--input", "x.xlsx", "--sensitive-json", "{}",
                "--insensitive-json", "{}", "--experiment-prefix", "p",
                "--expire-at", "0"]):
            args = upsert.parse_args()
            with self.assertRaises(SystemExit) as cm:
                upsert.build_auth_ctx(args, "https://target.invalid")
        self.assertEqual(cm.exception.code, rs_auth.EXIT_MANUAL_LOGIN)

    def test_verify_no_credentials_exit_3(self):
        """对称入口 —— verify 无凭据降级 exit 3。"""
        with mock.patch.object(sys, "argv",
                               ["verify", "--env", "prod-us"]):
            args = verify.parse_args()
            with self.assertRaises(SystemExit) as cm:
                verify.build_auth_ctx(args)
        self.assertEqual(cm.exception.code, rs_auth.EXIT_MANUAL_LOGIN)

    def test_admin_cli_custom_token_env(self):
        """--skill-token-env 改名后从自定义变量取显式 token (优先级最高)。"""
        with mock.patch.dict(os.environ, {"MY_CUSTOM_TOK": "tok-custom"},
                             clear=False):
            with mock.patch.object(sys, "argv",
                                   ["admin_cli", "--env", "prod-us",
                                    "--skill-token-env", "MY_CUSTOM_TOK",
                                    "stats"]):
                args = admin_cli.parse_args()
                ctx = admin_cli.build_auth_ctx(args)
        self.assertEqual(ctx.ldap_token, "tok-custom")
        self.assertEqual(ctx.token_source, "env")


class CacheWriteFailureTests(unittest.TestCase):
    """缓存写入失败 → RuntimeError 不裸穿 (来源:CI code-review r3 建议 4)。"""

    def test_unusable_cache_dir_raises_runtime_error(self):
        # RS_AUTH_CACHE_DIR 指向一个普通文件 → mkdir 失败 → RuntimeError。
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            with mock.patch.dict(os.environ, {"RS_AUTH_CACHE_DIR": f.name}):
                with self.assertRaises(RuntimeError):
                    rs_auth._save_cached_token("prod", "tok", 3600)


class HttpLoginBranchTests(unittest.TestCase):
    """http_login 真实四分支 (mock urllib, 不碰网络); 断言文案不含密码。"""

    PASSWORD = "SECRET-PW-XYZ"

    def _login(self):
        return rs_auth.http_login("prod", RS_HOST, "cn", self.PASSWORD)

    def test_success_returns_data(self):
        body = json.dumps({"code": 0, "msg": "",
                           "data": {"token": "t", "expiresInSeconds": 3600}}).encode()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = body
            m.return_value.__enter__.return_value.status = 200
            data = self._login()
        self.assertEqual(data["token"], "t")

    def test_urlerror_message_has_no_password(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("conn refused")):
            with self.assertRaises(RuntimeError) as cm:
                self._login()
        self.assertNotIn(self.PASSWORD, str(cm.exception))
        self.assertIn("登录请求失败", str(cm.exception))

    def test_login_rejected_message_has_no_password(self):
        # 上游真实形态: HTTP 200 + code=103 密码错误
        body = json.dumps({"code": 103, "msg": "密码错误", "data": None}).encode()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = body
            m.return_value.__enter__.return_value.status = 200
            with self.assertRaises(RuntimeError) as cm:
                self._login()
        self.assertNotIn(self.PASSWORD, str(cm.exception))
        self.assertIn("103", str(cm.exception))

    def test_login_failed_msg_with_password_redacted(self):
        """来源:CI code-review r2 条件性风险 —— 上游 msg 若回显密码, 必须脱敏后入文案。"""
        echo = f"invalid credentials for password {self.PASSWORD}"
        body = json.dumps({"code": 103, "msg": echo, "data": None}).encode()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = body
            m.return_value.__enter__.return_value.status = 200
            with self.assertRaises(RuntimeError) as cm:
                self._login()
        self.assertNotIn(self.PASSWORD, str(cm.exception))
        self.assertIn("***", str(cm.exception))
        self.assertIn("invalid credentials for password", str(cm.exception))

    def test_non_dict_json_body_raises_runtime_error(self):
        """顶层 JSON 非对象 (如数组) → RuntimeError, 不裸穿 AttributeError。"""
        body = json.dumps([1, 2, 3]).encode()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = body
            m.return_value.__enter__.return_value.status = 400
            with self.assertRaises(RuntimeError) as cm:
                self._login()
        self.assertIn("登录失败", str(cm.exception))

    def test_data_not_dict_raises_runtime_error(self):
        """data 字段非对象 → RuntimeError, 不裸穿 AttributeError。"""
        body = json.dumps({"code": 0, "msg": "", "data": "oops"}).encode()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = body
            m.return_value.__enter__.return_value.status = 200
            with self.assertRaises(RuntimeError) as cm:
                self._login()
        self.assertIn("data 非对象", str(cm.exception))

    def test_http_error_with_json_body(self):
        err = urllib.error.HTTPError(
            "u", 502, "Bad Gateway", None,
            io.BytesIO(json.dumps({"code": 500, "msg": "内部错误"}).encode()))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                self._login()
        self.assertNotIn(self.PASSWORD, str(cm.exception))
        self.assertIn("502", str(cm.exception))

    def test_non_json_response(self):
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b"<html>x</html>"
            m.return_value.__enter__.return_value.status = 200
            with self.assertRaises(RuntimeError) as cm:
                self._login()
        self.assertIn("不是 JSON", str(cm.exception))

    def test_success_without_token_field(self):
        """缺 token / token 非法形状 → RuntimeError (来源:r8 建议 1)。"""
        for bad_data in ({}, {"token": ""}, {"token": 12345}):
            with self.subTest(bad=bad_data):
                body = json.dumps({"code": 0, "msg": "", "data": bad_data}).encode()
                with mock.patch("urllib.request.urlopen") as m:
                    m.return_value.__enter__.return_value.read.return_value = body
                    m.return_value.__enter__.return_value.status = 200
                    with self.assertRaises(RuntimeError) as cm:
                        self._login()
                self.assertIn("token", str(cm.exception))

    def test_malformed_ttl_falls_back_to_default(self):
        """expiresInSeconds 非数值/非正/荒谬大 → 回落缺省 3 天, 不裸穿 ValueError。"""
        import tempfile
        for bad_ttl in ("abc", -5, None, 10**9):
            with self.subTest(ttl=bad_ttl):
                login_data = {"token": "t-ok", "expiresInSeconds": bad_ttl}
                with tempfile.TemporaryDirectory() as tmp:
                    with mock.patch.dict(
                            os.environ, {"RS_AUTH_CACHE_DIR": tmp,
                                         rs_auth.CN_ENV: "cn",
                                         rs_auth.PASSWORD_ENV: "pw"}):
                        with mock.patch.object(
                                rs_auth, "http_login",
                                return_value=login_data):
                            token, source = rs_auth.resolve_token(
                                "prod", RS_HOST, now=1000.0)
                    self.assertEqual((token, source), ("t-ok", "auto-login"))
                    with mock.patch.dict(os.environ,
                                         {"RS_AUTH_CACHE_DIR": tmp}):
                        # 缓存 TTL 已按缺省 3 天写 (expiresAt = 1000 + 259200)。
                        self.assertEqual(
                            rs_auth.load_cached_token(
                                "prod", now=1000.0 + 259200
                                - rs_auth.RENEW_MARGIN_SECONDS - 1), "t-ok")


class RoutingExtraTests(unittest.TestCase):
    """code-review C4 缺口: 反向推导与无连字符输入。"""

    def test_env_derived_from_node_name(self):
        self.assertEqual(
            rs_auth.resolve_ldap_routing(None, None, "prod-eu"),
            ("prod-eu", "prod", "prod-eu"))

    def test_no_hyphen_node_rejected_when_reaching_prefix_check(self):
        # 经脚本不可达 (argparse choices 锁死 env); 库级直接调用时拒绝。
        with self.assertRaises(rs_auth.RouteMismatch):
            rs_auth.resolve_ldap_routing(None, None, "produs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
