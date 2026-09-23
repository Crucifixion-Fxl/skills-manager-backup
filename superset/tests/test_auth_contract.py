from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
LOGIN2 = ROOT / "references" / "login2-session.md"


class SupersetAuthContractTest(unittest.TestCase):
    def test_skill_routes_agent_service_accounts_to_login2_session(self):
        self.assertIn("references/login2-session.md", SKILL)
        self.assertIn("/login2/?next=", SKILL)
        self.assertIn("不要据此判定密码失效", SKILL)

    def test_login2_reference_preserves_browser_session_semantics(self):
        self.assertTrue(LOGIN2.exists())
        text = LOGIN2.read_text(encoding="utf-8")
        for required in (
            "csrf_token",
            "Referer",
            "cookie jar",
            "User-Agent",
            "HTTP 200",
            "目标页",
            "302 /login/",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_login2_reference_does_not_put_password_in_argv(self):
        text = LOGIN2.read_text(encoding="utf-8")
        self.assertIn("curl 配置文件", text)
        self.assertIn("有效用户名", text)
        self.assertNotIn('-d "username=', text)
        self.assertNotIn('--data "username=', text)

    def test_login2_reference_does_not_require_unavailable_login_parameter(self):
        # agent 的 Bash 工具没有 login 参数：不能要求调用方设置它，
        # 防线是主机侧 ~/.bashrc 门禁 + SUPERSET_EXPECTED_USER 运行时比对。
        text = LOGIN2.read_text(encoding="utf-8")
        self.assertNotIn("login: false", text)
        self.assertNotIn("必须设置 `login", text)
        self.assertIn("没有 `login` 参数", text)
        self.assertIn("login shell", text)
        self.assertIn("~/.bashrc", text)
        self.assertIn("SUPERSET_EXPECTED_USER", text)
        self.assertIn("白名单只管启动那一刻", text)

    def test_login2_probe_does_not_depend_on_jq(self):
        # agent 运行环境里没有 jq；jq 缺失曾让编码变量为空、POST 出空用户名/空密码表单。
        text = LOGIN2.read_text(encoding="utf-8")
        probe = text.split("```bash", 1)[1].split("```", 1)[0]
        self.assertNotIn("jq", probe)
        self.assertIn("command -v", probe)
        self.assertIn("urllib.parse.quote", probe)
        # 编码结果为空必须在 POST 之前停下。
        self.assertLess(
            probe.index('test -n "$SUPERSET_USER_ENCODED"'),
            probe.index("curl --config"),
        )

    def test_original_jwt_auth_preserved_and_login2_is_additive(self):
        # owner 口径：恢复一周前基线，只新增 login2 登录方式，其他不变。
        self.assertIn("认证（两步 JWT）", SKILL)
        self.assertIn("/api/v1/security/login", SKILL)
        self.assertIn("login2", SKILL)
        self.assertIn("references/login2-session.md", SKILL)
        self.assertIn("/login2/?next=", SKILL)
        self.assertIn("重定向到 `/login/`", SKILL)


if __name__ == "__main__":
    unittest.main()
