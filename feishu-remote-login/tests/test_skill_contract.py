"""SKILL.md 的契约：frontmatter 合规、关键安全规则写在里面、引用的脚本都存在、没有泄露内部信息。"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SKILL = os.path.join(ROOT, "SKILL.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def all_text_files():
    for base, _, files in os.walk(ROOT):
        if "__pycache__" in base:
            continue
        for name in files:
            if name.endswith((".md", ".py", ".sh", ".js", ".txt", ".yaml", ".yml")):
                yield os.path.join(base, name)


class Frontmatter(unittest.TestCase):
    def setUp(self):
        self.text = read(SKILL)
        m = re.match(r"---\n(.*?)\n---\n", self.text, re.S)
        self.assertIsNotNone(m, "SKILL.md needs YAML frontmatter")
        self.fm = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))

    def test_name_matches_directory_and_is_valid(self):
        self.assertEqual(self.fm["name"], os.path.basename(ROOT))
        self.assertRegex(self.fm["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")

    def test_description_says_when_to_use_it(self):
        desc = self.fm["description"]
        self.assertLessEqual(len(desc), 1024)
        for needle in ("飞书", "二维码", "无头", "触发词"):
            self.assertIn(needle, desc)

    def test_required_sections_and_size(self):
        for heading in ("## Description", "## Rules", "## Examples"):
            self.assertIn(heading, self.text)
        self.assertLessEqual(len(self.text.splitlines()), 500)


class RulesAreWrittenDown(unittest.TestCase):
    """Each of these was a real mistake made (or nearly made) while building the skill: they must not silently disappear."""

    def setUp(self):
        self.text = read(SKILL)

    def test_login_state_is_the_owners_whole_identity_and_not_for_other_agents(self):
        for needle in ("登录态", "完整飞书身份", "其它 agent 一律不得"):
            self.assertIn(needle, self.text)

    def test_same_uid_isolation_is_not_claimed(self):
        for needle in ("同一 Unix UID", "InaccessiblePaths", "静默无效"):
            self.assertIn(needle, self.text)

    def test_delete_by_default_keep_only_on_explicit_request(self):
        self.assertIn("默认删除", self.text)
        self.assertIn("明确说保留才保留", self.text)

    def test_images_must_carry_context_text(self):
        for needle in ("文字说明", "--context", "拒绝发送"):
            self.assertIn(needle, self.text)

    def test_handshake_single_qr_and_watch_the_page(self):
        for needle in ("握手", "只发单张", "watch_login.sh", "确认登录", "静态文字「扫码成功」"):
            self.assertIn(needle, self.text)

    def test_recipient_pin_phone_confirmation_and_sandbox_are_documented(self):
        for needle in ("REMOTE_LOGIN_OWNER_OPEN_ID", "awaiting_phone_confirmation", "REMOTE_LOGIN_SANDBOX", "两个阶段都生效", "只有主机和路径"):
            self.assertIn(needle, self.text)

    def test_saml_timeout_pitfall(self):
        self.assertIn("saml_login_session_timeout", self.text)
        self.assertIn("重新点", self.text)

    def test_human_tokens_are_not_handed_to_agents_by_default(self):
        for needle in ("人的 token / JWT / cookie", "只读白名单", "临时 `HOME`"):
            self.assertIn(needle, self.text)

    def test_kill_by_pid_not_by_matching_own_command_line(self):
        for needle in ("按 PID 精确停进程", "144"):
            self.assertIn(needle, self.text)

    def test_lark_cli_pitfalls(self):
        for needle in ("只接受相对路径", "uploading image", "--as bot --user-id"):
            self.assertIn(needle, self.text)


class DocsAndScriptsAgree(unittest.TestCase):
    """The docs-vs-code check, both directions, for what a reader can actually get wrong: environment variables and statuses."""

    ENV = r"REMOTE_LOGIN_[A-Z_]+|CHROMIUM_PATH|LARK_CLI"

    @classmethod
    def setUpClass(cls):
        cls.doc = read(SKILL)
        cls.scripts = {n: read(os.path.join(ROOT, "scripts", n)) for n in sorted(os.listdir(os.path.join(ROOT, "scripts")))
                       if os.path.isfile(os.path.join(ROOT, "scripts", n))}
        cls.all_code = "\n".join(cls.scripts.values())

    def test_every_environment_variable_the_doc_names_exists_in_the_scripts(self):
        for name in set(re.findall(self.ENV, self.doc)):
            self.assertIn(name, self.all_code, f"{name} is documented but no script reads it")

    def test_every_environment_variable_the_scripts_read_is_documented(self):
        for name in set(re.findall(self.ENV, self.all_code)):
            self.assertIn(name, self.doc, f"{name} is read by a script but not documented in SKILL.md")

    def test_documented_statuses_are_emitted_by_the_scripts(self):
        # comment lines are removed first: the header comments list every status, which would make this pass without any emission
        code = "\n".join(l for l in self.all_code.splitlines() if not l.lstrip().startswith(("#", "//")))
        for status in ("sent", "logged_in", "dry_run", "awaiting_phone_confirmation", "timeout", "session_ended", "interrupted"):
            self.assertIn(f"`{status}`", self.doc, status)
            self.assertRegex(code, rf"emit status {status}\b", status)

    def test_documented_exit_codes_match_the_help_of_the_scripts(self):
        help_send = self.scripts["send_qr.sh"].split("set -u", 1)[0]
        for needle in ("2 用法错误", "3 会话没有给出新码", "4 发文字失败", "5 发图片失败", "6 页面正显示"):
            self.assertIn(needle, help_send)
        self.assertIn("退出码 6", self.doc)
        self.assertIn("130", self.scripts["watch_login.sh"].split("set -u", 1)[0])


class ReferencedFilesExist(unittest.TestCase):
    def test_every_script_and_reference_mentioned_exists(self):
        text = read(SKILL)
        for rel in set(re.findall(r"scripts/([A-Za-z0-9_.-]+\.(?:sh|js|py))", text)):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", rel)), f"scripts/{rel}")
        for rel in set(re.findall(r"references/([A-Za-z0-9_.-]+\.md)", text)):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "references", rel)), f"references/{rel}")

    def test_every_script_documents_itself_with_help(self):
        for name in sorted(os.listdir(os.path.join(ROOT, "scripts"))):
            if not os.path.isfile(os.path.join(ROOT, "scripts", name)):
                continue  # __pycache__ and the like
            body = read(os.path.join(ROOT, "scripts", name))
            self.assertRegex(body, r"--help|argparse", name)


class NoInternalInformation(unittest.TestCase):
    """The repo's security scan rejects these; keeping the check here gives the failure a readable name."""

    PATTERNS = {
        "internal GitLab host": r"gitlab\.addx\.ai",
        "private IP 10.x": r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        "private IP 172.16-31": r"\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b",
        "private IP 192.168": r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
        "real Feishu open_id": r"\bou_[0-9a-f]{32}\b",
        "real Feishu chat_id": r"\boc_[0-9a-f]{32}\b",
        "company e-mail": r"[A-Za-z0-9._-]+@a4x\.io",
    }

    def test_no_pattern_appears_anywhere_in_the_skill(self):
        for path in all_text_files():
            if os.path.basename(path) == "test_skill_contract.py":
                continue
            text = read(path)
            for label, pattern in self.PATTERNS.items():
                self.assertIsNone(re.search(pattern, text), f"{label} in {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    unittest.main()
