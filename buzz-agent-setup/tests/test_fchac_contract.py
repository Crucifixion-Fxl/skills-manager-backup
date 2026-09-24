#!/usr/bin/env python3
"""Deterministic contract checks for the FCHAC setup guidance."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
import re
import unittest
from urllib.parse import parse_qsl, urlsplit


TEST_DIR = Path(__file__).resolve().parent
SKILL_DIR = TEST_DIR.parent
SKILL = SKILL_DIR / "SKILL.md"
MODEL = SKILL_DIR / "references" / "fchac-model.md"
ACT = SKILL_DIR / "references" / "act-authorization.md"
ROUTING = SKILL_DIR / "references" / "issue-thread-routing.md"
RUNTIME = SKILL_DIR / "references" / "runtime-setup.md"
RUN_AGENT = SKILL_DIR / "references" / "scripts" / "run-agent.py"
EXAMPLE = SKILL_DIR / "references" / "scripts" / "issue-thread-router.example.json"
SYNC_REFERENCE = SKILL_DIR / "references" / "gitlab-buzz-sync.md"
SYNC_EXAMPLE = SKILL_DIR / "references" / "scripts" / "gitlab-buzz-sync.example.json"
APPROVAL = SKILL_DIR / "references" / "approval-authz.md"
CREDENTIALS = SKILL_DIR / "references" / "agent-credentials.md"
REPO = SKILL_DIR.parents[1]
LEGACY_SYNC_ADR = REPO / "docs" / "05-adr" / "0001-buzz-agent-setup-gitlab-sync-audience-and-identity.md"
SYNC_ADR = REPO / "docs" / "05-adr" / "0004-run-gitlab-sync-as-desk-owned-agent-step.md"
HTML = REPO / "public" / "work-methods" / "buzz-agent-collaboration.html"
CI = REPO / ".gitlab-ci.yml"
BI_ISSUE_RECEIPT = (
    TEST_DIR / "fixtures" / "nh-bi-issue-write-live-receipt-20260915.json"
)
BUZZ_DEPLOY_BRIDGE = (
    "https://gitlab.addx.ai/infra/buzz-deploy/-/blob/main/"
    "docs/architecture/gitlab-bridge/gitlab-buzz-bridge.html"
)
BUZZ_DEPLOY_COLLABORATION = (
    "https://gitlab.addx.ai/infra/buzz-deploy/-/blob/docs/agent-identity-vaultwarden/"
    "docs/architecture/buzz-agent-collaboration.html"
)


class FchacSkillContractTest(unittest.TestCase):
    def assert_document_contains_all(
        self, path: Path, requirements: tuple[str, ...]
    ) -> None:
        """Assert every semantic carrier in one named document, never an aggregate."""
        text = path.read_text(encoding="utf-8")
        for requirement in requirements:
            with self.subTest(document=str(path.relative_to(REPO)), requirement=requirement):
                self.assertIn(requirement, text)

    def assert_document_excludes_all(
        self, path: Path, forbidden: tuple[str, ...]
    ) -> None:
        """Make stale wording fail against the document that actually carries it."""
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            with self.subTest(document=str(path.relative_to(REPO)), forbidden=phrase):
                self.assertNotIn(phrase, text)

    def test_entrypoint_routes_to_existing_references(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        links = re.findall(r"\[[^]]+\]\((references/[^)#]+)\)", text)
        self.assertGreaterEqual(len(set(links)), 7)
        for link in set(links):
            self.assertTrue((SKILL_DIR / link).exists(), link)

    def test_agent_model_is_complete(self) -> None:
        text = MODEL.read_text(encoding="utf-8")
        for role in (
            "-desk",
            "-feature",
            "-bug",
            "-debt",
            "-dev",
            "-bi",
            "-sre",
            "-qa",
            "-investigator",
            "-dev-executor",
            "-bi-executor",
            "-sre-executor",
            "<function>-executor",
        ):
            self.assertIn(role, text)
        for invariant in (
            "业务 Channel",
            "业务平台 Channel",
            "职能 Channel",
            "DEV-ASSESSMENT",
            "Canvas 固定四张表",
            "飞书公开知识库机器人",
            "一次过 CI 比例",
        ):
            self.assertIn(invariant, text)
        self.assertIn("public/work-methods/buzz-agent-collaboration.html", text)
        self.assertIn("candidate_html_sha256=fcc080f547934845329038d506b3e6b5ea6cb2930d276654f3d55a8bc3fe9c16", text)
        self.assertIn("published_pages_commit=ae3fe2188ba20d22dde21581e7d16d15e45a5016", text)
        self.assertIn("published_html_sha256=f2dc3b278bca06b42fdad75f014f0425963f7b9276db27f997a786426952f7e4", text)
        self.assertNotIn("bridge_html_sha256=", text)
        self.assertIn(BUZZ_DEPLOY_BRIDGE, text)
        self.assertIn(BUZZ_DEPLOY_COLLABORATION, text)
        self.assertIn("Channel 重构暂停期间，它不是当前配置真相", text)
        self.assertIn("合入并重新发布后", text)
        self.assertIn("BI 证据评论的目标驱动自动回流、单 writer／有界扫描、marker、数据出口、live receipt 和 external bot 约束", text)

    def test_new_requirement_needs_issue_before_development(self) -> None:
        model = MODEL.read_text(encoding="utf-8")
        skill = SKILL.read_text(encoding="utf-8")
        section = model.split("## Issue 先行", 1)[1].split("\n## ", 1)[0]
        for role in ("`-dev`", "`-desk`", "`-feature`", "`-bug`"):
            self.assertIn(role, section)
        for invariant in (
            "在**原 Thread** 回一条带 Issue 链接的消息",
            "根是人类消息",
            "省略 origin",
            "Thread 回链",
            "gitlab-buzz-binding:v1",
            "--reply-to <root_event_id>",
            "链接不写进 GitLab 描述或评论",
            "Closes #<iid>",
            "豁免",
        ):
            self.assertIn(invariant, section)
        self.assertIn("接新需求先有 Issue，再动手", skill)
        self.assertLess(model.index("## Issue 先行"), model.index("## DEV-ASSESSMENT"))

    def test_desk_stays_a_router_and_hands_development_to_role_agents(self) -> None:
        """L1-FCHAC-020 Desk triages, dedupes and routes; it never does the development work itself."""
        model = MODEL.read_text(encoding="utf-8")
        section = model.split("## Desk 职能边界", 1)[1].split("\n## ", 1)[0]
        for invariant in (
            "入口分诊",
            "带来源答疑",
            "查重",
            "Issue SSOT",
            "进展分析",
            "路由转交",
            "改代码",
            "建分支",
            "push",
            "开／更新／合 MR",
            "部署",
            "ACT",
            "边界是职能，不是权限",
            "同一 Thread",
            "Issue IID",
            "交接理由",
            "没有对应角色 Agent",
            "需要人处理",
            "不自己代做",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(invariant, section)
        for role in ("`-dev`", "`-feature`", "`-bug`", "`-bi`", "`-sre`", "`-qa`"):
            with self.subTest(role=role):
                self.assertIn(role, section)
        # 「做」列允许维护 Issue SSOT（建单、评论、label、assignee、关闭都是平台写），
        # 所以「不做」列不能再无限定地写「改 SSOT」「调用平台写接口」，否则表自相矛盾。
        do_column = " ".join(row.split("|")[1] for row in section.splitlines() if row.startswith("| ") and "---" not in row)
        dont_column = " ".join(row.split("|")[2] for row in section.splitlines() if row.startswith("| ") and "---" not in row)
        self.assertIn("Issue SSOT 维护", do_column)
        self.assertNotIn("改 SSOT", dont_column)
        self.assertNotIn("调用平台写接口", dont_column)
        self.assertIn("Issue 除外", dont_column)
        self.assertLess(model.index("## Desk 的两种模式"), model.index("## Desk 职能边界"))
        role_row = next(line for line in model.splitlines() if line.startswith("| `-desk` |"))
        self.assertIn("Desk 职能边界", role_row)
        issue_first = model.split("## Issue 先行", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Desk 职能边界", issue_first)
        self.assertIn("Desk 职能边界", SKILL.read_text(encoding="utf-8"))
        self.assert_document_contains_all(
            SKILL_DIR / "references" / "gitlab-buzz-sync.desk-prompt.md",
            ("不改代码", "不建分支", "不开／合 MR", "不部署", "转交", "Issue IID"),
        )
        self.assert_document_contains_all(CREDENTIALS, ("Desk 职能边界",))

    def test_saas_reverse_scope_index_is_complete(self) -> None:
        """L1-FCHAC-001 Every SaaS request can be traced back to scope, owner and Skill."""
        self.assert_document_contains_all(
            MODEL,
            (
                "## 按 SaaS 反查 token、scope、申请人和 Skill",
                "| 平台 | token 形态 | 给 Agent 的最小档 | 谁申请／签发 | 哪些 Skill 要 |",
                "| GitLab |",
                "| CICD 上线单平台（TODO） |",
                "| Sentry |",
                "| Troubleshooting |",
                "| Superset |",
                "| GrowthBook |",
                "| NineData |",
                "| DataHub |",
                "| Dagster |",
                "| dapp（SLA 指标） |",
                "| 埋点平台 |",
                "| Crowdin |",
                "| NocoDB |",
                "| 飞书多维表 |",
                "| 飞书公开知识库（TODO） |",
                "| 鸟种百科（TODO） |",
                "| Grafana／Prometheus／K8s／ArgoCD |",
                "| QA Tools |",
                "未申请到的凭据写成注释占位",
                "拿到后正负两向实测",
            ),
        )

    def test_every_registered_agent_inherits_issue_write_capability(self) -> None:
        """L1-FCHAC-018 Every LLM Agent can maintain the Issue SSOT without gaining delivery authority."""
        for document in (SKILL, MODEL, CREDENTIALS, RUNTIME):
            self.assert_document_contains_all(
                document,
                (
                    "所有注册 Agent",
                    "gitlab-issue-sop",
                    "Planner",
                    "`api`",
                    "创建／评论／更新／关闭／重开 Issue",
                    "不是 Agent",
                ),
            )
        self.assert_document_contains_all(
            MODEL,
            (
                "`-desk`、`-feature`、`-bug`、`-debt`、`-dev`、`-bi`、`-sre`、`-qa`、`-investigator`",
                "`-dev-executor`、`-bi-executor`、`-sre-executor`、`-qa-executor`、`<function>-executor`",
                "executor LLM 的 Issue token",
                "ACT broker",
            ),
        )

    def test_published_method_page_carries_every_agent_issue_baseline(self) -> None:
        """L1-FCHAC-019 The human-facing matrix preserves the inherited Issue capability and token split."""
        self.assert_document_contains_all(
            HTML,
            (
                "所有注册 Agent 都能维护 GitLab Issue SSOT",
                "Planner（access level 15）＋ <code>api</code>",
                "创建、评论、更新、关闭与重开 Issue",
                "Bridge、route-writer、签名 sidecar 与 ACT broker 是服务，不是 Agent",
                "executor LLM 的 Issue token 与 ACT broker",
                "<code>-desk</code>",
                "<code>-feature</code>",
                "<code>-bug</code>",
                "<code>-debt</code>",
                "<code>-dev</code>",
                "<code>-bi</code>",
                "<code>-sre</code>",
                "<code>-qa</code>",
                "<code>-investigator</code>",
                "<code>-dev-executor</code>",
                "<code>-bi-executor</code>",
                "<code>-sre-executor</code>",
                "<code>-qa-executor</code>",
                "<code>&lt;职能&gt;-executor</code>",
                "<b>executor LLM</b> · 独立 Planner <code>api</code>，只维护 Issue",
                "<b>ACT broker service identity</b> · Maintainer <code>api</code>",
            ),
        )

    def test_data_access_boundary_is_unambiguous(self) -> None:
        credentials = SKILL_DIR / "references" / "agent-credentials.md"
        for path in (SKILL, MODEL, credentials):
            self.assert_document_excludes_all(path, ("取数首选走它", "NineData | ✅"))
        self.assert_document_contains_all(
            SKILL,
            (
                "数据取数／排障只走 Superset／Troubleshooting",
                "SQL 只允许 SELECT",
                "dataset／database／connection 只读",
                "definition／schedule／sensor",
            ),
        )
        self.assert_document_contains_all(
            MODEL,
            (
                "业务取数／排障只走 Superset／Troubleshooting",
                "SELECT-only",
                "dataset／database／connection 只读",
                "definition／schedule／sensor",
            ),
        )
        self.assert_document_contains_all(
            credentials,
            (
                "不能用于业务取数，也不作为排障后备路径",
                "统一走 Superset／Troubleshooting",
                "SQL 只允许 SELECT",
                "definition／schedule／sensor／allowlist 外拒绝",
            ),
        )
        self.assert_document_contains_all(
            HTML,
            (
                "SQL 只允许 SELECT",
                "dataset／database 与连接配置不可写",
                "不能改 definition、schedule、sensor 或越过 allowlist",
            ),
        )

    def test_bi_issue_evidence_write_is_narrow_and_explicit(self) -> None:
        self.assert_document_contains_all(
            SKILL,
            (
                "目标驱动自动回流",
                "不要求逐次人工批准",
                "完整 Issue 生命周期是这项通用基线有意授权的能力",
                "<!-- data-review-evidence-index:v1 -->",
                "当前 writer author＋精确首行 marker",
                "受保护默认分支",
                "零个目标不写",
                "恰好一个目标写回",
                "多个候选不写",
                "不能单独授权写入",
                "allowed_project",
                "allowed_destination_project_ids",
                "确定性分类器",
                "BUZZ_ACP_AGENTS=1",
                "唯一活动进程、每次运行检查单 writer／稳定扫描且 owner 持续检查日志",
                "project＋IID＋marker 锁",
                "最多 20 页／2000 条、总耗时 30 秒",
                "固定 `order_by=created_at&sort=asc`",
                "每个 pass 按 note ID 去重并核对 `X-Total`",
                "连续两次完整扫描的 note ID 集合与 marker 候选必须一致",
                "任何下一页未读、429、超时、失败都 fail closed",
                "多条／不可编辑／内容不变分别 conflict／no-op",
                "标为 external",
                "显式 membership 只有目标项目",
                "静态文案契约不能替代这份 live receipt",
                "n>=20",
                "链接只允许目标业务的 GitLab Issue／MR、Superset dashboard／chart 与 GrowthBook experiment 页面",
                "禁止 SQL 文本、Explore 临时查询、带签名／token 的 URL 或可导出明细的链接",
                "每次签发／轮换都保存不含 secret 的 receipt",
                "GitLab 版本、project／bot／token id、scope、external、membership、Internal 可见数、canary note id、运行 PID／worker 数、时间与执行人",
                "project access token self-rotate",
                "直接 token 方案不满足",
                "创建→回读→更新同一条 marker 评论",
                "权限 canary 的清理由管理员完成",
            ),
        )

        self.assert_document_contains_all(
            MODEL,
            (
                "自己唯一的 BI 证据评论",
                "<!-- data-review-evidence-index:v1 -->",
                "有意授权完整 Issue lifecycle 的 Planner `api` 基线",
                "目标驱动自动回流",
                "不要求逐次人工批准",
                "受保护默认分支",
                "零个目标不写",
                "恰好一个目标写回",
                "多个候选不写",
                "最多 20 页／2000 条、30 秒预算",
                "固定 `order_by=created_at&sort=asc`",
                "每个 pass 按 note ID 去重并核对 `X-Total`",
                "连续两次完整扫描的 note ID 集合与 marker 候选必须一致",
                "BUZZ_ACP_AGENTS=1",
                "唯一活动进程且无第二 writer",
                "确定性 writer／broker",
                "allowed_project",
                "allowed_destination_project_ids",
                "实例管理员标为 external",
                "`n>=20` 的脱敏聚合结论",
                "链接只允许本业务 GitLab Issue／MR、Superset dashboard／chart、GrowthBook experiment",
                "禁止 SQL／Explore 临时查询、带签名／token URL 或可导出明细链接",
                "每次签发／轮换保存不含 secret 的 live receipt",
                "GitLab 版本、project／bot／token id、scope、external、membership、Internal 可见数、canary note id、运行 PID／worker 数、时间与执行人",
                "若威胁模型要求平台 deny",
            ),
        )
        self.assert_document_contains_all(
            CREDENTIALS,
            (
                "BI 底层数据只读，Issue 证据评论受控可写",
                "<!-- data-review-evidence-index:v1 -->",
                "Planner（15）＋ classic `api` Issue 生命周期基线",
                "完整 Issue 生命周期是通用基线有意授权的能力",
                "最多 20 页／2000 条、总耗时 30 秒",
                "目标驱动自动回流",
                "不要求逐次人工批准",
                "受保护默认分支",
                "零个目标不写",
                "恰好一个目标写回",
                "多个候选不写",
                "allowed_project",
                "allowed_destination_project_ids",
                "确定性分类器",
                "n>=20",
                "链接只允许本业务 GitLab Issue／MR、Superset dashboard／chart 和 GrowthBook experiment 页面",
                "禁止 SQL／Explore 临时查询、带签名／token 的 URL 和可导出明细链接",
                "BUZZ_ACP_AGENTS=1",
                "恰好一个活动 Agent 进程且不存在第二个同职责 writer",
                "固定 `order_by=created_at&sort=asc`",
                "每个 pass 按 note ID 去重并核对 `X-Total`",
                "连续两次完整扫描的 note ID 集合与 marker 候选必须一致",
                "两个真实进程同时读到零条的竞态用例",
                "直接 token 不满足该威胁模型",
                "实例管理员必须把 bot 标为 external",
                "`membership=true` 恰好只有目标项目",
                "零条才创建、恰好一条才更新、多条 fail closed",
                "写后回读并核对 author、project、Issue IID、note ID、marker 和证据链接",
                "评论只允许脱敏聚合结论和受控证据链接",
                "静态文案测试不能替代它",
                "每次签发／轮换保存不含 secret 的 live receipt",
                "GitLab 版本、project／bot／token id、scope、external、membership、Internal 可见数、canary note id、运行 PID／worker 数、时间与执行人",
                "会同时生成新 bot author",
                "吊销旧 token 前必须在上述分页预算内完整查找旧 author 的 marker 评论",
                "有记录则由管理员迁移或显式标记 superseded",
                "核验新 Agent 进程确实加载新 token 后再吊销旧 token",
                "最终只保留一个活动身份",
            ),
        )
        self.assert_document_contains_all(
            HTML,
            (
                "data-review-evidence-index:v1",
                "Project Access Token bot 必须先标为 external",
                "唯一 membership 是目标项目",
                "最多 20 页／2000 条、总耗时 30 秒",
                "目标驱动自动回流",
                "不要求逐次人工批准",
                "受保护默认分支",
                "零个目标不写",
                "恰好一个目标写回",
                "多个候选不写",
                "order_by=created_at&amp;sort=asc",
                "每个 pass 按 note ID 去重并核对",
                "连续两次完整扫描的 note ID 集合与 marker 候选必须一致",
                "BUZZ_ACP_AGENTS=1",
                "唯一活动进程且无第二 writer",
                "确定性 writer／broker",
                "allowed_project",
                "allowed_destination_project_ids",
                "零条创建、恰好一条更新、多条停止",
                "<code>n&gt;=20</code>",
                "链接只允许本业务 GitLab Issue／MR、Superset dashboard／chart、GrowthBook experiment",
                "禁止 SQL／Explore 临时查询、带签名／token URL 或可导出明细链接",
                "每次签发／轮换保存不含 secret 的 live receipt",
                "GitLab 版本、project／bot／token id、scope、external、membership、Internal 可见数、canary note id、运行 PID／worker 数、时间与执行人",
                "直接 token 不满足",
            ),
        )

    def test_bi_issue_evidence_write_is_target_driven_without_per_run_approval(self) -> None:
        for path in (SKILL, MODEL, CREDENTIALS, HTML):
            text = path.read_text(encoding="utf-8")
            self.assertIn("目标驱动自动回流", text)
            self.assertIn("不要求逐次人工批准", text)
            self.assertIn("零个目标不写", text)
            self.assertIn("恰好一个目标写回", text)
            self.assertIn("多个候选不写", text)
            self.assertNotIn("当前已签名事件 envelope", text)
            self.assertNotIn("signer pubkey 命中 owner 控制", text)
            self.assertNotIn("逐次可信授权", text)

    def test_analysis_context_is_split_between_skill_canvas_and_workflow(self) -> None:
        for path in (SKILL, MODEL, RUNTIME, HTML):
            text = path.read_text(encoding="utf-8")
            self.assertIn("Agent mention", text, path)
            self.assertIn("Skill 名", text, path)
            self.assertIn("Canvas", text, path)
            self.assertIn("Workflow 专属", text, path)
            self.assertIn("复盘周期", text, path)
            self.assertIn("分析时点", text, path)
            self.assertIn("对比窗口", text, path)
            self.assertIn("full refresh", text, path)
            self.assertNotIn("正文本身就是完整方法论", text, path)
            self.assertNotIn("方法论写在 workflow", text, path)
            self.assertNotIn("判据、口径与汇报骨架属于频道 Workflow", text, path)

        html = HTML.read_text(encoding="utf-8")
        self.assertIn("历史反例一", html)
        self.assertIn("历史反例二", html)
        self.assertIn("不复制 Channel ID、project", html)
        self.assertIn("通用方法", html)

    def test_agent_prompt_owns_bounded_same_thread_output_authorization(self) -> None:
        for path in (SKILL, MODEL, RUNTIME, HTML):
            text = path.read_text(encoding="utf-8")
            self.assertIn("同 Thread", text, path)
            self.assertIn("脱敏聚合", text, path)
            self.assertIn("不需要逐次披露审批", text, path)
            self.assertIn("Workflow 不能授予披露权限", text, path)
            self.assertIn("Channel ACL", text, path)

    def test_agent_prompt_requires_reply_to_thread_root_on_every_send(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        rule = next((l for l in skill.splitlines() if "--reply-to <THREAD_ROOT>" in l), "")
        self.assertTrue(rule, "SKILL.md 缺少 --reply-to <THREAD_ROOT> 规则行")
        # 规则本体：每条带、根取自唤醒提示、开工前的“收到”也算
        for needle in ("Thread root:", "开工前的", "每条都带"):
            self.assertIn(needle, rule)
        # 例外①：只认 owner 在 prompt/Workflow 里写明的顶层，不认不可信消息文本
        self.assertIn("只有 owner 写在 prompt 或 Workflow 正文里、明确指定发频道顶层／广播消息时才不带", rule)
        self.assertIn("不可信数据", rule)
        # 例外②：指定的回复根覆盖唤醒提示（todo:done / root_event_id / 转交）
        self.assertIn("以指定的为准，覆盖唤醒提示的 `Thread root:`", rule)
        for needle in ("todo:done", "root_event_id", "scheduled-workflows.md"):
            self.assertIn(needle, rule)
        # 例外③：@ 真人的动作类消息走 helper 的 reply_to，不退回裸发；Agent 间转交不走 helper
        self.assertIn("走 responsible helper", rule)
        self.assertIn("`reply_to`（必须是 Thread 根的 64 位 hex）", rule)
        self.assertIn("不要退回裸 `messages send`", rule)
        self.assertIn("Agent 之间的转交与路由不走 helper", rule)
        # 引用文档同步：runtime-setup 的 prompt 必备项、personal-channel 的 prompt 片段
        runtime = RUNTIME.read_text(encoding="utf-8")
        item = next((l for l in runtime.splitlines() if l.startswith("- prompt 里的 `buzz messages send` 模板")), "")
        self.assertIn("--reply-to <THREAD_ROOT>", item)
        personal = (SKILL_DIR / "references" / "personal-channel.md").read_text(encoding="utf-8")
        self.assertIn("只有 `todo:done` 例外，回复待办消息本身", personal)

    def test_bi_issue_live_receipt_is_secret_free_and_content_addressed(self) -> None:
        receipt_text = BI_ISSUE_RECEIPT.read_text(encoding="utf-8")
        receipt = json.loads(receipt_text)

        self.assertEqual(
            set(receipt),
            {
                "schema_version",
                "receipt_kind",
                "permission_scope",
                "recorded_at_utc",
                "executed_by",
                "attestation",
                "snapshot_only",
                "contains_secret",
                "readiness",
                "method",
                "gitlab",
                "canary",
                "runtime",
                "remaining_actions",
            },
        )
        self.assertEqual(
            set(receipt["readiness"]), {"gitlab_issue_write", "superset"}
        )
        self.assertEqual(
            set(receipt["method"]), {"reviewed_commit", "candidate_html_sha256"}
        )
        self.assertEqual(
            set(receipt["gitlab"]),
            {
                "base_url",
                "version",
                "project_id",
                "bot_user_id",
                "token_id",
                "access_level",
                "scopes",
                "expires_at",
                "active",
                "revoked",
                "external",
                "membership_project_ids",
                "internal_visible_count",
                "prior_exposed_token",
            },
        )
        self.assertEqual(
            set(receipt["gitlab"]["prior_exposed_token"]),
            {"token_id", "active", "revoked"},
        )
        self.assertEqual(
            set(receipt["canary"]),
            {
                "issue_iid",
                "note_id",
                "author_user_id",
                "marker",
                "create_status",
                "first_read_status",
                "update_same_note_status",
                "second_read_status",
                "admin_cleanup_status",
                "agent_read_after_cleanup_status",
                "remaining_project_marker_count",
            },
        )
        self.assertEqual(
            set(receipt["runtime"]),
            {
                "service",
                "active",
                "main_pid",
                "started_at_utc",
                "workers",
                "token_matches_env",
                "approver_allowlist_matches_env",
                "superset_password_loaded",
                "superset_credential_state",
                "env_mode",
                "prompt_mode",
                "prompt_sha256",
            },
        )
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["receipt_kind"], "bi_issue_write_permission_canary")
        self.assertEqual(receipt["permission_scope"], "gitlab_issue_write_only")
        self.assertEqual(receipt["recorded_at_utc"], "2026-09-15T05:09:52Z")
        self.assertIsNotNone(
            datetime.fromisoformat(receipt["recorded_at_utc"].replace("Z", "+00:00")).tzinfo
        )
        self.assertEqual(receipt["executed_by"], "jchen via Codex session")
        self.assertEqual(receipt["attestation"], "unsigned_operator_observation")
        self.assertTrue(receipt["snapshot_only"])
        self.assertFalse(receipt["contains_secret"])
        self.assertEqual(
            receipt["readiness"],
            {
                "gitlab_issue_write": "ready_with_disclosed_platform_limits",
                "superset": "blocked_pending_admin_password_rotation",
            },
        )
        self.assertEqual(
            receipt["method"]["reviewed_commit"],
            "8556029c8410cff7ff5a26d796ab581d1c9918d1",
        )
        reviewed_commit_digests = {
            "8556029c8410cff7ff5a26d796ab581d1c9918d1": (
                "dfbd94689453efc6665038aa31cf06e78726f0978da8327517e2289907f89d5b"
            )
        }
        self.assertEqual(
            receipt["method"]["candidate_html_sha256"],
            reviewed_commit_digests[receipt["method"]["reviewed_commit"]],
        )

        gitlab = receipt["gitlab"]
        self.assertEqual(gitlab["version"], "18.0.0")
        self.assertEqual(gitlab["project_id"], 1175)
        self.assertEqual(gitlab["bot_user_id"], 985)
        self.assertEqual(gitlab["token_id"], 952)
        self.assertEqual(gitlab["access_level"], 20)
        self.assertEqual(gitlab["scopes"], ["api", "read_repository"])
        self.assertTrue(gitlab["active"])
        self.assertFalse(gitlab["revoked"])
        self.assertTrue(gitlab["external"])
        self.assertEqual(gitlab["membership_project_ids"], [1175])
        self.assertEqual(gitlab["internal_visible_count"], 0)
        self.assertFalse(gitlab["prior_exposed_token"]["active"])
        self.assertTrue(gitlab["prior_exposed_token"]["revoked"])

        canary = receipt["canary"]
        self.assertEqual(canary["issue_iid"], 16)
        self.assertEqual(canary["note_id"], 604938)
        self.assertEqual(canary["author_user_id"], gitlab["bot_user_id"])
        self.assertEqual(canary["marker"], "data-review-evidence-index:v1")
        self.assertEqual(canary["create_status"], 201)
        self.assertEqual(canary["first_read_status"], 200)
        self.assertEqual(canary["update_same_note_status"], 200)
        self.assertEqual(canary["second_read_status"], 200)
        self.assertEqual(canary["admin_cleanup_status"], 204)
        self.assertEqual(canary["agent_read_after_cleanup_status"], 404)
        self.assertEqual(canary["remaining_project_marker_count"], 0)

        runtime = receipt["runtime"]
        self.assertEqual(runtime["service"], "buzz-local-nh-bi.service")
        self.assertTrue(runtime["active"])
        self.assertEqual(runtime["main_pid"], 2643769)
        self.assertEqual(runtime["started_at_utc"], "2026-09-15T05:08:45Z")
        self.assertEqual(runtime["workers"], 1)
        self.assertTrue(runtime["token_matches_env"])
        self.assertTrue(runtime["approver_allowlist_matches_env"])
        self.assertFalse(runtime["superset_password_loaded"])
        self.assertEqual(
            runtime["superset_credential_state"], "blocked_pending_rotation"
        )
        self.assertEqual(runtime["env_mode"], "0600")
        self.assertEqual(runtime["prompt_mode"], "0600")
        self.assertEqual(
            runtime["prompt_sha256"],
            "1693517a9eeb5033d26d2957ffcd0133505cb3f729b197d30e6121935d5220c7",
        )
        self.assertEqual(
            receipt["remaining_actions"],
            [
                "Rotate the dedicated nh_bi Superset password after its accidental "
                "tool-output exposure."
            ],
        )

        forbidden_keys = {
            "access_token",
            "api_key",
            "authorization",
            "bearer",
            "client_secret",
            "cookie",
            "cookies",
            "credential",
            "credentials",
            "dsn",
            "private_key",
            "token",
            "password",
            "private_token",
            "gitlab_token",
            "superset_password",
            "secret",
            "secret_value",
        }

        def assert_no_secret_value(value: object) -> None:
            if isinstance(value, dict):
                normalized_keys = {str(key).lower().replace("-", "_") for key in value}
                self.assertTrue(forbidden_keys.isdisjoint(normalized_keys))
                for nested in value.values():
                    assert_no_secret_value(nested)
            elif isinstance(value, list):
                for nested in value:
                    assert_no_secret_value(nested)
            elif isinstance(value, str):
                self.assertNotRegex(value, r"(?i)glpat-[A-Za-z0-9_-]+")
                self.assertNotRegex(value, r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]+")
                self.assertNotRegex(
                    value,
                    r"(?i)(?:access[_-]?token|api[_-]?key|client[_-]?secret|password|"
                    r"authorization|cookie|credential)\s*[:=]\s*\S+",
                )
                parsed = urlsplit(value)
                if parsed.scheme in {"http", "https"}:
                    query_keys = {
                        key.lower().replace("-", "_")
                        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
                    }
                    self.assertTrue(forbidden_keys.isdisjoint(query_keys))

        assert_no_secret_value(receipt)

    def test_skill_runtime_keeps_the_desk_owned_sync_topology(self) -> None:
        self.assert_document_contains_all(
            SKILL,
            (
                "owner 的主机调度器每 300 秒直接运行 `gitlab_buzz_sync_timer.py`",
                "Linux：`gitlab-buzz-sync-<channel>.timer`",
                "macOS：`ai.addx.gitlab-buzz-sync.<channel>`",
                "Desk-owned Agent Step",
                "Desk 身份的白名单环境变量",
                "gitlab_buzz_sync.py",
                "bot 在 GitLab Issue／MR 里写的 binding note",
                "Desk 的确定性 Canvas route gate 按 header 行（新消息末行，存量首行）在原 Thread 指派角色 Agent",
            ),
        )
        self.assert_document_contains_all(
            ROUTING,
            (
                "Desk 就是 router",
                "脚本只是 Desk 的确定性组件",
                "组件不拥有独立身份",
                "GitLab binding Note → 交叉检查本地 cache → Buzz 根标记搜索",
            ),
        )
    def test_collaboration_delegates_gitlab_implementation_to_bridge(self) -> None:
        self.assert_document_contains_all(
            HTML,
            (
                f'href="{BUZZ_DEPLOY_BRIDGE}"',
                "一个统一 Bridge",
                "状态消息与代码变更分开",
                "一个 Issue 对应一个 Thread",
                "路由属于 Channel",
                "原生对象是增强，不是前提",
                "自动路由不获得执行授权",
            ),
        )
        self.assert_document_excludes_all(
            HTML,
            (
                "0.2.0",
                "0.2.1",
                "CanonicalChange",
                "root_event_id",
                "issue_thread_router.py",
                "deployment_baseline",
                "visibility=public",
            ),
        )
        self.assertFalse((REPO / "public" / "work-methods" / "gitlab-buzz-bridge.html").exists())

    def test_binding_loss_fails_closed_in_each_contract_document(self) -> None:
        self.assert_document_contains_all(
            SKILL,
            (
                "首个失败立即停止后续写入且 cursor 不前进",
                "下一轮从 outbox 证据恢复",
            ),
        )
        self.assert_document_contains_all(
            ROUTING,
            (
                "update 找不到本地、GitLab comment 或 Buzz root",
                "fail closed；不创建 Thread、不提交 change digest、不推进 waterline",
            ),
        )
    def test_html_blob_hash_matches_model_provenance_and_ci_watches_only_owned_page(self) -> None:
        digest = hashlib.sha256(HTML.read_bytes()).hexdigest()
        model = MODEL.read_text(encoding="utf-8")
        self.assertIn(f"candidate_html_sha256={digest}", model)
        self.assertNotIn("bridge_html_sha256=", model)
        ci = CI.read_text(encoding="utf-8")
        self.assertIn("public/work-methods/buzz-agent-collaboration.html", ci)
        self.assertNotIn("public/work-methods/gitlab-buzz-bridge.html", ci)

    def test_gitlab_sync_is_canonical_and_bridge_design_stays_in_buzz_deploy(self) -> None:
        self.assert_document_contains_all(
            SKILL,
            (
                "[gitlab-buzz-sync.md](references/gitlab-buzz-sync.md)",
                "**已取代**",
                "[issue-thread-routing.md](references/issue-thread-routing.md)",
            ),
        )
        self.assertFalse((REPO / "public" / "work-methods" / "gitlab-buzz-bridge.html").exists())
        self.assert_document_contains_all(MODEL, (BUZZ_DEPLOY_BRIDGE,))

    def test_html_fragment_links_reveal_and_reposition_diagrams(self) -> None:
        text = HTML.read_text(encoding="utf-8")
        for anchor in ('id="agent-domain-model"', 'id="issue-routing"'):
            self.assertIn(anchor, text)
        self.assertIn('target.closest("details")', text)
        self.assertIn('target.scrollIntoView({block:"start"})', text)
        self.assertIn('window.addEventListener("load"', text)

    def test_act_contract_has_fail_closed_guards(self) -> None:
        text = ACT.read_text(encoding="utf-8")
        for guard in (
            "同 Channel、同 root Thread",
            "platform-admin pubkey allowlist",
            "一次性 ledger",
            "显示名可伪造",
            "引用即授权攻击",
            "普通 webhook",
            "payload_sha256",
        ):
            self.assertIn(guard, text)

    def test_executor_write_credentials_are_outside_the_llm_process(self) -> None:
        contracts = {
            SKILL: ("同一 Unix UID", "独立 OS principal", "executor LLM", "只提交 `act_id`", "保持禁用"),
            MODEL: ("同一 Unix UID", "独立 OS principal", "executor LLM", "只提交 `act_id`", "必须保持禁用"),
            ACT: ("同一 Unix UID", "独立 OS principal", "credential broker", "executor LLM", "`act_id`", "canonical payload", "保持禁用"),
            RUNTIME: ("OS principal", "credential broker", "executor LLM", "`act_id`", "canonical payload", "保持禁用"),
            CREDENTIALS: ("同一 Unix UID", "独立 OS principal", "credential broker", "executor LLM", "`act_id`", "保持禁用"),
            HTML: ("同一个 Unix 用户", "独立 OS principal", "credential broker", "executor LLM", "act_id", "canonical payload", "保持禁用"),
        }
        for path, requirements in contracts.items():
            self.assert_document_contains_all(path, requirements)
        self.assert_document_contains_all(HTML, ("不能满足“executor 只能经 ACT”",))

    def test_act_approval_digest_and_broker_execution_are_explicit(self) -> None:
        self.assert_document_contains_all(
            ACT,
            (
                "/approve ACT-<id> <payload_sha256>",
                "executor Agent 只提交 `act_id`",
                "broker 自行回读 Buzz 原始事件与平台当前状态",
                "一次性 ledger",
                "canonical payload",
            ),
        )
        self.assert_document_contains_all(
            APPROVAL,
            (
                "/approve ACT-MERGE-<id> <payload_sha256>",
                "executor 只提交 ACT ID",
                "重算 canonical payload digest",
                "当前 HEAD",
            ),
        )
        self.assert_document_contains_all(
            MODEL,
            (
                "/approve ACT-<id> <payload_sha256>",
                "executor LLM 只提交 `act_id`",
                "current state",
                "expiry",
                "一次性 ledger",
                "canonical payload",
            ),
        )
        self.assert_document_contains_all(
            HTML,
            (
                "/approve ACT-&lt;id&gt; &lt;payload_sha256&gt;",
                "executor LLM 只提交 <code>act_id</code>",
                "broker 独立回读 proposal／approval",
                "current state",
                "expiry",
                "一次性 ledger",
                "canonical payload",
                "由隔离 ACT broker 补",
            ),
        )
        self.assert_document_excludes_all(
            HTML,
            (
                "<code>/approve ACT-…</code>",
                "<code>/approve ACT-&lt;id&gt;</code>",
                "由 <code>-dev-executor</code> 补",
            ),
        )

    def test_platform_credentials_never_self_authorize(self) -> None:
        self.assert_document_contains_all(
            CREDENTIALS,
            (
                "AI Agent 不能自行申请、批准或扩大自己的平台权限",
                "ACT-CREDENTIAL",
                "有 API 仍不代表请求 Agent 可以自授权",
                "授权主体仍是管理员",
            ),
        )
        self.assert_document_contains_all(
            MODEL,
            (
                "Sentry、DataHub 等平台的新发、续期与扩 scope",
                "目标平台管理员亲自授权／签发",
                "ACT-CREDENTIAL",
                "API 可用性只改变执行路径，绝不产生自授权",
            ),
        )
        self.assert_document_contains_all(
            HTML,
            (
                "Sentry 平台管理员亲自授权／签发",
                "DataHub 平台管理员亲自授权／签发",
                "ACT-CREDENTIAL",
                "API 只改变签发路径，不产生自授权",
            ),
        )

    def test_gitlab_interactive_provisioning_uses_local_glab_without_ui_handoff(self) -> None:
        helper = SKILL_DIR / "scripts" / "provision_gitlab_agent_token.py"
        self.assertTrue(helper.is_file())
        self.assert_document_contains_all(
            SKILL,
            ("本地已登录的 `glab`", "不要引导用户去 GitLab UI", "PENDING journal", "同可读 HOME 不能作为该边界"),
        )
        self.assert_document_contains_all(
            CREDENTIALS,
            ("本地已登录的 `glab`", "不转去 UI", "compare-before-write", "撤销刚创建的 token"),
        )
        self.assert_document_contains_all(
            RUNTIME,
            ("provision_gitlab_agent_token.py", "不要用 UI 作为失败回退", "--authorized-admin", "128-bit 随机 operation id"),
        )
        self.assert_document_contains_all(
            MODEL,
            ("本地已登录的 `glab`", "不引导用户去 UI 创建", "authorization ref 只作审计证据"),
        )

    def test_datahub_identity_is_dedicated_or_disabled(self) -> None:
        requirements = (
            "独立 Agent 平台账号＋per-Agent PAT",
            "人的 PAT 禁止",
            "保持禁用／TODO",
        )
        self.assert_document_contains_all(MODEL, requirements)
        self.assert_document_contains_all(HTML, requirements)

    def test_channel_membership_is_not_a_git_or_saas_acl(self) -> None:
        self.assert_document_contains_all(
            MODEL,
            (
                "Channel 成员关系只决定 Buzz 上下文可见性",
                "不是 Git ACL",
                "每个 Agent 的 Git／SaaS 账号与最小 scope 另行逐身份授权",
            ),
        )
        self.assert_document_contains_all(
            HTML,
            (
                "Channel 成员关系只决定 Buzz 上下文可见性",
                "不是 Git ACL",
                "每个 Agent 的 Git／SaaS 账号与最小 scope 另行逐身份授权",
            ),
        )
        self.assert_document_excludes_all(HTML, ("成员（＝ git ACL）",))

    def test_figure_7_superset_scope_and_footer_status_are_precise(self) -> None:
        self.assert_document_contains_all(
            HTML,
            (
                "Superset · SELECT",
                "dashboard／chart 限定写",
                "本文以终态设计为主",
                "TODO／disabled／current gap",
            ),
        )

    def test_router_config_cannot_encode_executor_target(self) -> None:
        config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertRegex(config["buzz"]["desk_pubkey"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            config["buzz"]["desk_pubkey"],
            config["agents"][config["buzz"]["desk_agent"]]["pubkey"],
        )
        self.assertNotIn("router_pubkey", config["buzz"])
        self.assertRegex(config["buzz"]["cli_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsInstance(config["gitlab"]["bot_author_id"], int)
        self.assertTrue(config["gitlab"]["bot_username"])
        self.assertEqual("public", config["gitlab"]["required_project_visibility"])
        cli_path = Path(config["buzz"]["cli_path"])
        self.assertTrue(cli_path.is_absolute())
        self.assertIn("buzz-0.5.23", cli_path.parts)
        self.assertNotIn(".local/bin/buzz", str(cli_path))
        self.assertEqual(
            config["status_order"],
            ["triage", "backlog", "ready", "in-progress", "in-review"],
        )
        for rule in config["routes"]:
            agent = config["agents"][rule["target"]]
            self.assertNotEqual(agent["kind"], "executor")
            self.assertFalse(agent["name"].endswith("-executor"))

    def test_router_pins_raw_buzz_cli_and_requires_write_readback(self) -> None:
        combined = SKILL.read_text(encoding="utf-8") + ROUTING.read_text(encoding="utf-8")
        for requirement in (
            "buzz.cli_path",
            "buzz.cli_sha256",
            "buzz-0.5.23",
            "~/.local/bin/buzz",
            "NIP-10",
            "按 note id GET",
            "cursor 不推进",
        ):
            self.assertIn(requirement, combined)

    def test_retained_fchac_fixtures_are_never_auto_cleaned(self) -> None:
        text = RUNTIME.read_text(encoding="utf-8")
        self.assertIn("retained 的长期 FCHAC Project／Channel 永不删除或归档", text)
        self.assertIn("只有人明确给出 exact child", text)
        self.assertIn("evidence 默认保留", text)

    def test_runtime_really_changes_to_the_validated_repository(self) -> None:
        text = RUN_AGENT.read_text(encoding="utf-8")
        self.assertIn("workdir = _trusted_path(raw_workdir, uid, directory=True)", text)
        self.assertIn("if not workdir.is_relative_to(work_root):", text)
        self.assertIn('if not (workdir / ".git").exists():', text)
        self.assertIn("os.chdir(workdir)", text)

    def test_runtime_validates_and_literal_parses_env_without_sourcing_it(self) -> None:
        text = RUN_AGENT.read_text(encoding="utf-8")
        for guard in (
            "getattr(os, \"O_NOFOLLOW\", 0)",
            "before.st_uid != uid",
            "stat.S_IMODE(before.st_mode) != 0o600",
            "before.st_dev != after.st_dev",
            "values[key] = _literal(raw)",
            "launch_env = dict(env)",
        ):
            self.assertIn(guard, text)
        self.assertNotIn("source ", text)

    def test_desk_is_the_only_issue_routing_identity(self) -> None:
        paths = (
            SKILL,
            ROUTING,
            RUNTIME,
            SKILL_DIR / "references" / "scripts" / "README.md",
            SKILL_DIR / "references" / "scripts" / "mint-agent.py",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotIn("nh-issue", combined)
        self.assertNotIn("<business>-issue-router", combined)
        self.assertIn("Desk 就是 router", combined)

    def test_gitlab_18_polling_avoids_mutable_updated_at_offset_pages(self) -> None:
        combined = SKILL.read_text(encoding="utf-8") + ROUTING.read_text(encoding="utf-8")
        self.assertIn("order_by=created_at", combined)
        self.assertIn("连续两次一致", combined)
        self.assertIn("scan_before", combined)
        self.assertIn("排序的可变集合使用 offset page", combined)

    def test_current_sync_uses_desk_identity_and_exact_private_audience(self) -> None:
        self.assertTrue(SYNC_ADR.is_file())
        self.assertTrue(LEGACY_SYNC_ADR.is_file())
        self.assert_document_contains_all(
            SKILL,
            (
                "public 与 private 项目都同步",
                "confidential Issue、internal／confidential 评论默认不发",
                "docs/05-adr/0001-buzz-agent-setup-gitlab-sync-audience-and-identity.md",
                "docs/05-adr/0004-run-gitlab-sync-as-desk-owned-agent-step.md",
                "Desk-owned Agent Step",
                "Desk 身份的白名单环境变量",
            ),
        )
        self.assert_document_contains_all(
            LEGACY_SYNC_ADR,
            (
                "status: Superseded",
                'date: "2026-09-14"',
                "supersedes: []",
                "superseded-by:",
                "0004-run-gitlab-sync-as-desk-owned-agent-step",
                "Isolate GitLab to Buzz publication",
                "独立 Bridge service",
            ),
        )
        self.assert_document_contains_all(
            SYNC_ADR,
            (
                "status: Accepted",
                'date: "2026-09-16"',
                "deciders:",
                "supersedes:",
                "0001-buzz-agent-setup-gitlab-sync-audience-and-identity",
                "superseded-by:\n  - 0008-run-gitlab-sync-from-owner-systemd-timer",
                "## Context and Problem Statement",
                "## Considered Options",
                "## Trade-off Analysis",
                "## Decision Outcome",
                "## Consequences",
                "Desk-owned Agent Steps",
                "exact-audience rechecks",
                "PENDING/ACKED",
                "Desk runtime",
            ),
        )
        self.assert_document_contains_all(
            SYNC_REFERENCE,
            (
                "publisher_pubkey",
                "audience.allowed_pubkeys",
                "gitlab_buzz_sync.py",
                "Desk-owned Agent Step",
                "PENDING",
                "ACKED",
                "首个失败",
                "4 MiB",
                "100 页",
                "10 分钟",
            ),
        )
        self.assert_document_excludes_all(
            SYNC_REFERENCE,
            (
                "没有 service、cron、本地表或 outbox",
                "独立 Bridge service",
                "gitlab_buzz_sync_trigger.py",
                "`desk_pubkey`",
            ),
        )
        config = json.loads(SYNC_EXAMPLE.read_text(encoding="utf-8"))
        self.assertNotIn("audience", config)
        self.assertNotIn("desk_pubkey", config)

    def test_all_agents_budget_attention_and_notify_only_verified_people(self) -> None:
        for document in (SKILL, MODEL, RUNTIME):
            self.assert_document_contains_all(
                document,
                (
                    "注意力预算",
                    "GitLab 结构化字段",
                    "owner 管理的 `people_file`",
                    "当前 Channel human member",
                    "显式 `p` tag",
                    "发送后回读",
                    "不得 `@all`",
                    "未通知：<原因>",
                ),
            )
        self.assert_document_contains_all(
            SKILL,
            (
                "只有需要某个人行动、评审、决定或解除阻塞",
                "普通进展、背景和 FYI 不 @ 人",
                "同一 Thread／动作",
                "最多 3",
                "定时最终报告",
                "channel_admin",
            ),
        )

    def test_scheduled_analysis_workflows_have_standing_audience(self) -> None:
        scheduled = SKILL_DIR / "references" / "scheduled-workflows.md"
        self.assertTrue(scheduled.is_file(), scheduled)
        self.assert_document_contains_all(
            scheduled,
            (
                "gitlab-pipeline-health",
                "architecture-smell-scan",
                "delivery-progress-analysis",
                "data-review-analysis",
                "buzz-workflow-audience:v1",
                "channel_admin",
                "core_eng",
                "`person` locator",
                "ack",
            ),
        )
        for document in (SKILL, MODEL, RUNTIME, scheduled):
            self.assert_document_contains_all(
                document,
                (
                    "只读分析",
                    "channel_admin",
                ),
            )
        skill_text = SKILL.read_text(encoding="utf-8")
        runtime_text = RUNTIME.read_text(encoding="utf-8")
        self.assertIn("Feature、QA 与 executor 不配 schedule", skill_text)
        self.assertIn("`-dev` 禁止实现／写仓 schedule", skill_text)
        self.assertIn("Feature、QA 和所有 executor", runtime_text)
        for name in (
            "delivery-progress.yaml",
            "data-review.yaml",
            "pipeline-health.yaml",
            "architecture-smell.yaml",
        ):
            path = SKILL_DIR / "references" / "analysis-workflows" / name
            text = path.read_text(encoding="utf-8")
            self.assertIn("person <channel_admin_username>", text)
            self.assertIn("ack 不加站立受众", text)
            self.assertNotIn("@all", text)

    def test_scheduled_reports_notify_responsible_people_with_action_messages(self) -> None:
        """Schedule results reach the people they concern, via the existing helper (no code path change)."""
        templates = SKILL_DIR / "references" / "analysis-workflows"
        for name in (
            "delivery-progress.yaml",
            "data-review.yaml",
            "pipeline-health.yaml",
            "architecture-smell.yaml",
        ):
            text = (templates / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertIn("行动消息", text)
                self.assertIn("责任人", text)
                self.assertIn("GitLab 作者", text)
                self.assertIn("person locator", text)
        scheduled = SKILL_DIR / "references" / "scheduled-workflows.md"
        self.assert_document_contains_all(
            scheduled,
            (
                "行动消息",
                "责任人",
                "GitLab 用户名",
                "people_file",
                "同一 Thread",
                "最多 5 项",
            ),
        )
        self.assert_document_contains_all(
            RUNTIME,
            ("GitLab 用户名", "people_file", "行动消息"),
        )

    def test_platform_desk_serves_many_channels_without_business_credentials(self) -> None:
        self.assert_document_contains_all(
            MODEL,
            (
                "平台 Desk",
                "gitsecops-desk",
                "不持业务仓",
                "转交",
                "无平台层根因",
                "跨 Channel",
            ),
        )
        self.assert_document_contains_all(
            CREDENTIALS,
            ("gitsecops-desk", "平台 Desk", "中央仓", "不签发业务仓"),
        )
        self.assert_document_contains_all(
            RUNTIME,
            ("BUZZ_ACP_CHANNELS", "不固定", "平台 Desk", "触发事件的 Channel"),
        )
        self.assert_document_contains_all(
            SKILL,
            ("平台 Desk", "gitsecops-desk", "platform-feedback-summary"),
        )
        # Handoff is an agent->registered-agent mention, not human notification: the
        # "agents never --mention" rule must carry that exception, and the pubkey read from
        # an editable Canvas must be checked against the channel's bot members.
        self.assert_document_contains_all(SKILL, ("唯一例外是 Agent→注册 Agent 的转交", "role=bot"))
        self.assert_document_contains_all(
            SKILL_DIR / "references" / "scheduled-workflows.md", ("role=bot", "不走责任人 helper")
        )
        self.assert_document_contains_all(MODEL, ("role=bot", "行动消息"))

    def test_platform_agents_do_not_pin_channels_while_role_agents_still_do(self) -> None:
        """skills#120：平台类 Agent 不固定 BUZZ_ACP_CHANNELS（jchen 2026-09-20 的决定）。

        订阅面 = 它是 bot 成员的全部 Channel；只有把它的 channel_add_policy 设成 owner_only，
        拉它进频道才是 owner 的动作（skills#144 更正）。业务 Channel 的角色 Agent 仍固定到自己的
        业务 Channel。harness 收到入群通知就动态订阅（desktop-v0.5.23 源码），不用重启。
        """
        text = RUNTIME.read_text(encoding="utf-8")
        start = text.index("### 平台 Desk")
        end = text.index("用 Agent 自己身份注册档案并 join", start)
        section = text[start:end]
        for needle in (
            "平台类 Agent",
            "`skill-dev`",
            "`gitsecops-desk`",
            "不固定 `BUZZ_ACP_CHANNELS`",
            "env 里不设这个键",
            "channels add-member --role bot",
            "private",
            "`subscribed to channel`",
            "bot 成员 Channel 数",
            "追加其 UUID",
            "每个 turn 先读**触发事件所在 Channel 的 Canvas**",
            "只回原 Thread",
            "中央仓 Issue 链接",
            "「平台 Agent」一节",
            "membership notification: subscribing to new channel",
            "仍固定 `BUZZ_ACP_CHANNELS`",
        ):
            with self.subTest(section="runtime 平台 Desk", requirement=needle):
                self.assertIn(needle, section)
        for stale in (
            "不留空",
            "必须等于清单条数",
            "逗号分隔",
            "追加进 `BUZZ_ACP_CHANNELS`",
            "加成员只能由 owner",
            "以实测为准，未验证前重启一次并核对启动日志",
        ):
            with self.subTest(section="runtime 平台 Desk", forbidden=stale):
                self.assertNotIn(stale, section)
        # 业务 Channel 的角色 Agent 仍按原做法固定到自己的业务 Channel。
        self.assert_document_contains_all(RUNTIME, ("BUZZ_ACP_CHANNELS=<CH>",))
        self.assert_document_contains_all(
            MODEL, ("不固定 `BUZZ_ACP_CHANNELS`", "订阅面", "owner")
        )
        self.assert_document_contains_all(SKILL, ("不固定 `BUZZ_ACP_CHANNELS`",))
        self.assert_document_excludes_all(
            SKILL, ("`BUZZ_ACP_CHANNELS` 逗号列出",)
        )

    def test_platform_desk_handoff_is_mandatory_and_precedes_the_weekly_summary(self) -> None:
        templates = SKILL_DIR / "references" / "analysis-workflows"
        health = (templates / "pipeline-health.yaml").read_text(encoding="utf-8")
        summary = (templates / "platform-feedback-summary.yaml").read_text(encoding="utf-8")
        # Every pipeline-health run hands off exactly once, even with nothing to hand off:
        # the desk's receipt is the only completion signal the weekly summary can rely on.
        self.assertIn("gitsecops-desk", health)
        self.assertIn("无平台层根因", health)
        self.assertIn("不能省略", health)
        self.assertNotIn("没有就不转交", health)
        for needle in ("person <channel_admin_username>", "ack 不加站立受众", "待转交", "created_at"):
            with self.subTest(needle=needle):
                self.assertIn(needle, summary)
        self.assertNotIn("@all", summary)

        def cron_of(text: str) -> tuple[int, int, str]:
            match = re.search(r'cron:\s*"(\d+) (\d+) \S+ \S+ (\S+)"', text)
            self.assertIsNotNone(match, text)
            return int(match.group(2)), int(match.group(1)), match.group(3)

        health_hour, health_minute, health_dow = cron_of(health)
        summary_hour, summary_minute, summary_dow = cron_of(summary)
        self.assertEqual(health_dow, summary_dow)
        # The summary must start well after the analysis + hand-off (>= 3h of headroom).
        self.assertGreaterEqual(
            (summary_hour * 60 + summary_minute) - (health_hour * 60 + health_minute), 180
        )
        scheduled = (SKILL_DIR / "references" / "scheduled-workflows.md").read_text(encoding="utf-8")
        self.assertIn("平台反馈周报", scheduled)
        self.assertIn("顺序", scheduled)

    def test_merge_approval_uses_act_id_and_canonical_payload_digest(self) -> None:
        text = APPROVAL.read_text(encoding="utf-8")
        self.assertNotIn("/approve !", text)
        self.assertIn("/approve ACT-MERGE-<id> <payload_sha256>", text)
        self.assertIn("merge_request_iid", text)
        self.assertIn("commit_sha", text)
        self.assertIn("canonical payload", text)

    def test_agents_never_self_issue_platform_tokens(self) -> None:
        text = CREDENTIALS.read_text(encoding="utf-8")
        self.assertNotIn("AI 自己申请", text)
        self.assertNotIn("AI 自己建", text)
        self.assertIn("平台管理员授权", text)
        self.assertIn("ACT", text)

    def test_router_runtime_is_in_audited_scripts_and_ci(self) -> None:
        self.assertTrue((SKILL_DIR / "scripts" / "issue_thread_router.py").is_file())
        ci = (SKILL_DIR.parents[1] / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn("skills/buzz-agent-setup/tests", ci)
        self.assertIn("scripts/validate.py --skill skills/buzz-agent-setup --security", ci)

    def test_deployment_baseline_is_git_pinned_and_state_recovery_is_atomic(self) -> None:
        self.assert_document_contains_all(
            ROUTING,
            (
                "显式 `--initialize`",
                "config.gitlab.deployment_baseline",
                "recovery_complete=true",
                "中途失败即不产生半份 state",
            ),
        )
        self.assert_document_contains_all(
            RUNTIME,
            ("pin 回 Git 配置", "原子重建", "缺 pin 或恢复未完整即 fail closed"),
        )
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertIn("deployment_baseline", example["gitlab"])
        self.assertIsNone(example["gitlab"]["deployment_baseline"])

    def test_checkpoint_policy_and_no_rewake_contract_is_cross_carrier(self) -> None:
        self.assert_document_contains_all(
            ROUTING,
            (
                "buzz-issue-snapshot:v1",
                "不能把 `previous=null` 套在全部已有 binding 上批量 re-wake",
                "仅 `updated_at` 变化不写 checkpoint",
                "历史 checkpoint 必须用自身内嵌 policy",
                "completed action 按 `change_id` 匹配 checkpoint",
                "尚未匹配 checkpoint 的 in-flight action 每个 Issue 最多一条",
                "同 target key 只换 pubkey 也必须产生一次 Desk 重新指派",
                "`status_order` 会改变状态机语义",
            ),
        )
        self.assert_document_contains_all(
            RUNTIME,
            (
                "历史 checkpoint 用自身 policy 验证",
                "已完成 action 按 change id 使用匹配 checkpoint 的 policy 验证",
                "无 checkpoint 的 crash-window action 只在 digest 仍等于当前 policy 时恢复",
                "同 target 换 pubkey 会触发重新指派",
                "任何旧 policy 的 pending outbox 都",
                "`status_order` 变化必须显式迁移",
            ),
        )

    def test_module_runtime_topology_is_cross_carrier(self) -> None:
        self.assert_document_contains_all(
            ROUTING,
            (
                "## 模块运行位置",
                "操作者 Terminal",
                "Buzz relay runtime",
                "Agent worker 主机",
                "GitLab SaaS",
                "隔离 OS principal 或容器",
            ),
        )
        self.assert_document_contains_all(
            RUNTIME,
            (
                "Buzz relay scheduler 按 Channel 的 schedule 唤醒 Desk",
                "Desk 与 polling component 跑在同一 Agent worker turn",
                "ACT broker／action adapter 运行在隔离 OS principal 或容器",
            ),
        )

    def test_action_source_and_checkpoint_chain_contract_is_cross_carrier(self) -> None:
        self.assert_document_contains_all(
            ROUTING,
            (
                "真实 A→B→A",
                "完整 `source_snapshot` 和权威 `previous_change_id`",
                "action 与 checkpoint 必须形成唯一有序链",
                "连续两次完整读取的 id／body 签名一致",
            ),
        )
        self.assert_document_contains_all(
            RUNTIME,
            (
                "`source_snapshot + previous_change_id + policy_digest`",
                "policy epoch 后的下一业务 update",
            ),
        )

    def test_validation_boundary_is_real_gitlab_buzz_only(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("真实 E2E 只覆盖 GitLab 与 Buzz", text)
        self.assertIn("其他 SaaS 使用 mock contract", text)
        self.assertIn("reference candidate", text)
        self.assertIn("schedule 保持关闭", text)

    def test_fresh_issue_data_and_protocol_markers_are_cross_carrier(self) -> None:
        requirements = (
            "GET project → GET Issue → GET project",
            "untrusted_issue",
            "物理第一行",
            "不做 substring",
            "发布状态：reference candidate",
            "真实 public GitLab＋Buzz E2E 未完成",
            "独立 service identity／签名 sidecar 未完成",
            "独立 service identity／签名 sidecar",
            "schedule 保持关闭",
        )
        # The superseded router keeps these gates in its own references; SKILL.md no longer carries them.
        for path in (ROUTING, RUNTIME):
            self.assert_document_contains_all(path, requirements)

    def test_candidate_does_not_ship_a_deployable_schedule_artifact(self) -> None:
        artifacts = [
            path.relative_to(SKILL_DIR)
            for path in SKILL_DIR.rglob("*")
            if path.is_file()
            and path.suffix in {".yml", ".yaml"}
            and "schedule" in path.name.lower()
        ]
        self.assertEqual([], artifacts)

    def test_pending_branch_design_is_not_promoted_to_rule(self) -> None:
        text = MODEL.read_text(encoding="utf-8")
        self.assertIn("2.2.2 当前仍是待决草图", text)
        self.assertIn("批准前不得", text)


def markdown_section(text: str, heading: str) -> str:
    """Body of one Markdown heading up to the next heading of the same or higher level (fenced code ignored)."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise AssertionError(f"missing section: {heading}")
    body: list[str] = []
    in_fence = False
    for line in lines[start + 1:]:
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and re.match(rf"^#{{1,{level}}} ", line):
            break
        body.append(line)
    return "".join(body)


REPO_TABLE_HEADER = "| 仓库路径 | project id | 用途 |"


class ChannelSetupOrderContractTest(unittest.TestCase):
    def test_repository_list_in_canvas_comes_before_any_gitlab_token(self) -> None:
        """L1-GIS-194 Setup first writes the Canvas「## 代码仓库」list; tokens are derived from list × role minimum."""
        skill = SKILL.read_text(encoding="utf-8")
        order = markdown_section(skill, "## 实施顺序")
        self.assertIn("「## 代码仓库」", order)
        self.assertIn(REPO_TABLE_HEADER, order)
        self.assertLess(order.index(REPO_TABLE_HEADER), order.index("provision_gitlab_agent_token.py"))
        self.assertLess(order.index("「## 代码仓库」"), order.index("### 2. 定 Agent 与权限"))
        for required in ("先改表，再申请或收回 token", "清单 × 角色最小档", "一个 Agent"):
            with self.subTest(skill=required):
                self.assertIn(required, order)

        credentials = CREDENTIALS.read_text(encoding="utf-8")
        for required in (
            "「## 代码仓库」",
            REPO_TABLE_HEADER,
            "先改表，再申请或收回 token",
            "清单 × 角色最小档",
            "## GitLab 角色最小档",
        ):
            with self.subTest(credentials=required):
                self.assertIn(required, credentials)
        self.assertLess(credentials.index("「## 代码仓库」"), credentials.index("provision_gitlab_agent_token.py"))
        minimum = markdown_section(credentials, "## GitLab 角色最小档")
        for role, profile in (("`-desk`", "Planner"), ("`-dev`", "Developer"), ("`-debt`", "Reporter"),
                              ("`-sre`", "Reporter"), ("`-qa`", "Planner")):
            with self.subTest(role=role):
                row = next((line for line in minimum.splitlines() if line.startswith(f"| {role}")), "")
                self.assertIn(profile, row)

        for path in (SKILL, CREDENTIALS, RUNTIME):
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                template = re.search(r"(?ms)^## 代码仓库\n\n(\| 仓库路径 \| project id \| 用途 \|)\n\| --- \| --- \| --- \|\n\|", text)
                self.assertIsNotNone(template, "the Canvas「## 代码仓库」template must be a three-column list")
        # Per-Agent permission tiers belong to agent-credentials.md, never to a Canvas matrix column.
        for path in (SKILL, CREDENTIALS, RUNTIME):
            text = path.read_text(encoding="utf-8")
            with self.subTest(no_matrix=path.name):
                self.assertNotRegex(text, r"\| 仓库路径 \| project id \| 用途 \| [^\n]")

    def test_setup_interviews_first_then_does_local_work_and_lists_human_exceptions(self) -> None:
        """L1-GIS-195 Setup is: interview the user → AI finishes with local permissions → explicit human-only exceptions."""
        skill = SKILL.read_text(encoding="utf-8")
        order = markdown_section(skill, "## 实施顺序")
        self.assertTrue(order.lstrip().startswith("### 0. "), "the interview/local/human split must open the flow")
        stage0 = markdown_section(order, "### 0. ")
        headings = ("#### 一、先采访用户", "#### 二、AI 用用户本地权限直接完成", "#### 三、必须人来做或需要他人审批的例外")
        positions = [stage0.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        interview = markdown_section(stage0, headings[0])
        local = markdown_section(stage0, headings[1])
        human = markdown_section(stage0, headings[2])
        for required in ("Channel 名", "受众", "npub", "「## 代码仓库」", "角色 Agent", "默认建议",
                         "审批人", "break-glass", "since", "Sentry", "Superset", "GrowthBook"):
            with self.subTest(interview=required):
                self.assertIn(required, interview)
        for required in ("NIP-OA", "30177", "owner 身份", "Canvas", "glab", "provision_gitlab_agent_token.py",
                         "Maintainer", "systemd", "launchd", "调度配置", "dry-run", "验证套件", "不要把这些推给用户"):
            with self.subTest(local=required):
                self.assertIn(required, local)
        for required in ("npub", "吕强", "qlv", "add-member", "Maintainer", "李文斑", "GrowthBook", "UI",
                         "/login", "/approve"):
            with self.subTest(human=required):
                self.assertIn(required, human)

        credentials = CREDENTIALS.read_text(encoding="utf-8")
        table = markdown_section(credentials, "## 谁来授权、谁来签发")
        header = next(line for line in table.splitlines() if line.startswith("| 系统 |"))
        self.assertIn("执行方", header)
        rows = [line for line in table.splitlines() if line.startswith("| **")]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row=row[:40]):
                self.assertTrue("AI 用本地权限完成" in row or "需要人" in row or "不申请" in row)
        gitlab = next(row for row in rows if row.startswith("| **GitLab**"))
        self.assertIn("AI 用本地权限完成", gitlab)
        for platform in ("**Superset**", "**GrowthBook**"):
            row = next(row for row in rows if row.startswith(f"| {platform}"))
            self.assertIn("需要人", row)


if __name__ == "__main__":
    unittest.main()
