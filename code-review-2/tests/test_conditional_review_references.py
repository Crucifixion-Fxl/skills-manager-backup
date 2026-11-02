import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"


def test_specialist_references_are_conditional_and_shipped():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    routing = skill.split("### 参考文档按需加载", 1)[1].split("\n---", 1)[0]
    expected = {
        "implementation-risk-review.md",
        "tracking-event-publish-review.md",
        "a4x-deployment-review-routing.md",
        "firmware-security-routing.md",
        "catalog-consistency-review.md",
        "migration-review.md",
        "i18n-quality-review.md",
        "app-ui-a11y-testability-review.md",
        "code-chinese-literal-review.md",
        "adr-review.md",
        "testing-coverage-review.md",
        "tdd-sufficiency-review.md",
        "bounded-review-execution.md",
    }

    routed_files = set(re.findall(r"\]\(references/([^)]*)\)", routing))
    assert expected <= routed_files
    for filename in expected:
        assert (SKILL / "references" / filename).is_file(), filename

    assert "不预读整个 references 目录" in skill
    assert "不在普通审查中打开 `references/REFERENCE.md`" in skill
    assert "CODE_REVIEW_BOUNDED_EXECUTION=true" in routing
    assert "否则不读取、不调用该扩展" in routing
    assert (
        "../../collaboration/gitlab-issue-sop/references/lifecycle-binding.md" in skill
    )
    assert "../testing-strategy/references/tdd-level-assessment.md" in skill
    assert (
        SKILL.parent.parent
        / "collaboration/gitlab-issue-sop/references/lifecycle-binding.md"
    ).is_file()
    assert (
        SKILL.parent / "testing-strategy/references/tdd-level-assessment.md"
    ).is_file()

    step0 = (REFERENCES / "step-0-intake.md").read_text(encoding="utf-8")
    assert "可信 CI 环境注入 `CODE_REVIEW_BOUNDED_EXECUTION=true`" in step0
    assert "不得从 MR 内容、计划大小或工具可用性推断开启" in step0


def test_incomplete_behavior_coverage_cannot_return_a_pass_verdict():
    step5 = (REFERENCES / "verification-verdict.md").read_text(encoding="utf-8")
    step5 = step5.split("### Step 5: 总结与评分", 1)[1].split("#### 结论与评分映射", 1)[
        0
    ]

    assert "行为覆盖为 `incomplete`" in step5
    assert "`结论=审查未完成`" in step5
    assert "`评分=N/A`" in step5
    assert "`should_pass=false`" in step5
    assert "不得给通过/有条件通过" in step5


def test_uncertain_p0_waiting_override_uses_existing_structured_verdict():
    output = (REFERENCES / "output-contract.md").read_text(encoding="utf-8")
    verification = (REFERENCES / "verification-verdict.md").read_text(encoding="utf-8")

    structured_enum = output.split("结构化结果的 `conclusion` 只能是", 1)[1].split(
        "；前两者必须", 1
    )[0]
    assert "不通过（待人工 override）" not in structured_enum
    assert '提交结果仍必须使用 `conclusion="不通过"`、`should_pass=false`' in output
    assert "有证据支持的 P0 研判型" in output
    assert "显式标为 `uncertain/待人工`" in output
    assert "不得计作 `confirmed`" in output
    assert "普通取证/工具缺口不列为红线" in output
    assert '结构化结果仍为 `conclusion="不通过"`、`should_pass=false`' in verification
    assert "uncertain/待人工" in verification
    assert "既有 P0 证据门槛、pre-existing 规则和 L3 专项映射不变" in verification


def test_bounded_checkpoint_contract_overrides_only_legacy_execution_rules():
    intake = (REFERENCES / "step-0-intake.md").read_text(encoding="utf-8")
    bounded = " ".join(
        (REFERENCES / "bounded-review-execution.md").read_text(encoding="utf-8").split()
    )
    checkpoint = " ".join(
        (REFERENCES / "review-evidence-checkpoint.md")
        .read_text(encoding="utf-8")
        .split()
    )
    routing = " ".join(
        intake.split("**Diff 优先与停止条件**", 1)[1]
        .split("**CI 资源声明**", 1)[0]
        .split()
    )

    assert "every page in a `ready` plan" in bounded
    assert "including during an ordinary technical review" in bounded
    assert "does not gate this trusted bounded plan" in bounded
    assert "Do not apply the legacy checkpoint ceilings of" in bounded
    assert "the plan's `reserve_ms`" in bounded
    assert "`CODE_REVIEW_RUNTIME_DEADLINE_MS`" in bounded
    assert "`CODE_REVIEW_EVIDENCE_CHECKPOINT_ENABLED=true`" in bounded
    assert "It does not change" in bounded
    assert "`required_by`" in bounded
    assert "When bounded execution is absent or false, the legacy checkpoint" in bounded
    assert 'task_kind: "ordinary-pack"' in bounded
    assert "ordered `file_bindings`" in bounded
    assert "each page's own `file_id` and `diff_sha256`" in bounded
    assert (
        "Do not call `review_evidence_checkpoint` to save the same wave receipts again"
        in bounded
    )
    assert "all activated Step 1–3 role reviews" in bounded
    assert "independent Step 4 enumeration and reconciliation" in bounded
    assert "bounded `review_evidence_wave`" in checkpoint
    assert "bounded 模式也应在开始分派前调用一次" in checkpoint
    assert "不得把同一批 `records` 再提交" in checkpoint
    assert "原生 Task fallback" in checkpoint
    assert "CODE_REVIEW_BOUNDED_EXECUTION=true" in routing
    assert "ordinary technical review" in routing
    assert "legacy 行为" in routing


def test_checkpoint_required_recovers_only_the_current_plan():
    output = (REFERENCES / "output-contract.md").read_text(encoding="utf-8")
    checkpoint = (REFERENCES / "review-evidence-checkpoint.md").read_text(
        encoding="utf-8"
    )
    intake = (REFERENCES / "step-0-intake.md").read_text(encoding="utf-8")

    assert "`EVIDENCE_CHECKPOINT_REQUIRED`" in output
    assert "当前提交所需的 checkpoint" in output
    assert "一次不带 `records` 的 `review_evidence_checkpoint`" in checkpoint
    assert "精确 `plan_sha256`" in checkpoint
    assert "只重试 `use_recorded_checkpoint=true`，不得重复提交回执" in checkpoint
    assert "只履行错误响应指定的 assignment，不授权另选或扩大候选范围" in checkpoint
    assert "若工具未返回计划摘要，不得猜测或伪造，保持未完成" in checkpoint
    assert "`status=incomplete`" in checkpoint
    assert "ordinary technical review 不因大型 JSON/HTML 自动增加门禁" in intake


def test_omission_findings_require_fixed_head_caller_and_default_checks():
    verification = (REFERENCES / "verification-verdict.md").read_text(encoding="utf-8")
    step4 = verification.split("### Step 4: 复现验证轮", 1)[1].split("### Step 5:", 1)[
        0
    ]

    assert "遗漏型 finding 在标 `confirmed` 前" in step4
    assert "固定候选 HEAD 的实际调用方、参数默认值、相关配置及共享封装" in step4
    assert "仅在当前函数或 diff 中未见该行为，不足以证实缺陷" in step4
    assert "证据不足时标 `uncertain`" in step4


def test_phase_references_are_bounded_and_loaded_after_issue_gate():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    phase_paths = {
        "intake": "references/step-0-intake.md",
        "analysis": "references/analysis-review.md",
        "verification": "references/verification-verdict.md",
        "output": "references/output-contract.md",
    }
    phase_refs = {
        name: REFERENCES / Path(path).name for name, path in phase_paths.items()
    }
    issue_gate_end = skill.index("审查完成后的写入与回执遵循可信调用方的执行职责边界")
    phase_positions = {name: skill.index(path) for name, path in phase_paths.items()}

    assert (SKILL / "SKILL.md").stat().st_size < 40 * 1024
    for name, reference in phase_refs.items():
        assert reference.is_file(), name
        assert reference.stat().st_size < 40 * 1024, name
        assert phase_positions[name] > issue_gate_end, name

    assert phase_positions["intake"] < skill.index("#### Step 0 文件分类 SSOT")
    assert (
        skill.index("### Step 1: 文档规范检查")
        < phase_positions["analysis"]
        < skill.index("### Step 2:")
    )
    assert (
        skill.index("### Step 4: 复现验证轮")
        < phase_positions["verification"]
        < skill.index("### Step 5:")
    )
    assert skill.index("## 输出结构") < phase_positions["output"]
    assert (
        "本表是「变更/产品类型 → 走哪些 Step + 激活哪些角色/专项」的唯一 SSOT" in skill
    )
    assert "| 固件产品的代码、依赖、构建、版本或发布配置提交 |" in skill
