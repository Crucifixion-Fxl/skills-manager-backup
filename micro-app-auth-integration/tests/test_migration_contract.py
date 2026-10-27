from pathlib import Path
import json
import re
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]


class MigrationContractTest(unittest.TestCase):
    def test_single_entrypoint(self):
        retired = SKILL_ROOT.parent / "unified-feishu-auth-migration"
        self.assertFalse(any(path.is_file() for path in retired.rglob("*")))
        entry = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/migration-cutover.md", entry)
        self.assertIn("独立可信 Web 应用", entry)

    def test_local_document_links_resolve(self):
        owner = SKILL_ROOT.parent / "micro-app"
        documents = [SKILL_ROOT / "SKILL.md", *SKILL_ROOT.glob("references/*.md"), owner / "SKILL.md", *owner.glob("references/*.md")]
        for document in documents:
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                if "://" in target or target.startswith("#"):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / target.split("#", 1)[0]).is_file())

    def test_owner_discovery_entrypoints_and_source_policy_resolve(self):
        owner = SKILL_ROOT.parent / "micro-app"
        source = json.loads((owner / "references/source-discovery.json").read_text(encoding="utf-8"))
        for target in [*source["contractEntrypoints"], source["sourcePolicy"]]:
            with self.subTest(target=target):
                self.assertTrue((owner / target).is_file())
        # auth-discovery.sources[].path is relative to that JSON's directory.
        auth_path = owner / "references/auth-discovery.json"
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
        for entry in auth["sources"]:
            if entry["kind"] == "owner-contract":
                self.assertTrue((auth_path.parent / entry["path"]).is_file())

    def test_no_retired_entrypoint_in_consumers(self):
        repo = next(p for p in Path(__file__).resolve().parents if (p / ".codex-plugin" / "plugin.json").is_file())
        documents = [*repo.glob("skills/**/SKILL.md"), *repo.glob("public/**/*.html")]
        for document in documents:
            if document == SKILL_ROOT / "SKILL.md":
                continue
            with self.subTest(document=str(document.relative_to(repo))):
                self.assertNotIn("unified-feishu-auth-migration", document.read_text(encoding="utf-8"))

    def test_cutover_keeps_security_and_recovery_gates(self):
        guide = (SKILL_ROOT / "references/migration-cutover.md").read_text(encoding="utf-8")
        for requirement in (
            "不禁用守卫继续发布",
            "测试编译失败要修复测试",
            "不在全局对象存储 token",
            "凭证接口不使用通配 CORS",
            "初始登录和续期均被拒绝",
            "持久化重试",
            "代码回滚不会自动撤销",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, guide)


if __name__ == "__main__":
    unittest.main()
