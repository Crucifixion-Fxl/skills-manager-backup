# 生产晋级一致性检查

当一个已经在 staging 验证的功能准备晋级到 `main`、`master` 或 `release/*` 时，使用本参考。

## 触发边界

| MR 目标分支 | 是否执行 |
|:--|:--|
| `staging` | 不执行。staging 可以正常接收实现、缺陷修复和集成验证改动 |
| `main` / `master` | staging 已验证功能晋级生产时强制执行 |
| `release/*` | staging 已验证功能晋级生产时强制执行 |
| 其他开发分支 | 不执行 |
| 生产紧急直修 | 仅作为例外；必须记录用户批准、owner、原因、验证和强制回补 |

目标为 `staging` 时跳过 parity，不代表跳过代码评审、测试、CI 或 staging 集成验证。
它只是不要求当前分支与生产候选分支证明一致。

目标为生产分支时不存在默认普通 MR 模式：必须显式分类为
`production-promotion`、有“该类变更不存在 staging 晋级链路”证据的
`production-non-promotion`、确定性证明只退休已迁移 staging writer 的
`staging-writer-cleanup`，或明确批准的 `emergency-hotfix`。cleanup 是独立模式，按
[`staging-writer-cleanup.md`](staging-writer-cleanup.md) 校验，不得伪装成 promotion 或
non-promotion。

## 目标

证明生产候选分支完整包含 staging 已验证的功能改动，包括后续缺陷修复、评审修复、
测试、可观测性、migration 和部署配置。环境差异必须显式声明并逐文件验证。

本检查只证明代码和声明的一致性，不证明运行时行为正确。语义安全仍需使用
`face-review-repair` 和功能对应的测试方案验证。

## 必需输入

| 输入 | 含义 |
|:--|:--|
| `project_path` | GitLab 项目路径 |
| `canonical_verification_mr` | 主功能分支最后一个绑定 staging 验证结果的已合并 MR IID |
| `candidate_mr` | 当前生产候选 MR IID |
| `staging_branch` | staging 目标分支，默认 `staging` |
| `contract` | 环境差异或外部门禁存在时使用的 `release-contracts/<feature>.yaml` |

调用方不再手填 ref 或 SHA。脚本直接读取 GitLab API：从 verification MR 的 source
branch 历史中找到最早的已合并 staging MR，以其 `diff_refs.base_sha` 作为 canonical
base；使用 verification MR HEAD 作为 canonical head；使用 candidate MR HEAD 和生产
目标分支 API 当前 SHA 作为 candidate head/base。随后 fetch GitLab MR ref 与目标分支并
逐一绑定。两个 base 还必须分别是对应 head 的祖先。

## 主功能分支规则

功能上线完成前只维护一个主功能分支（canonical feature branch）：

```text
功能实现
  + 评审修复
  + staging 缺陷修复
  + 测试和可观测性
  = 主功能分支
```

生产晋级分支只是主功能分支投影到生产目标分支的临时分支。准备生产 MR 时发现新问题，
先把修复落回主功能分支，重新完成受影响的 staging 验证，再更新晋级分支。release
专属修复只能作为紧急例外，并必须有回补负责人和 Issue/MR。

## 确定性检查

执行：

```bash
uv run <gitlab-mr-skill>/scripts/release_parity_check.py \
  --project-path <group/project> \
  --canonical-verification-mr <staging-mr-iid> \
  --candidate-mr <production-mr-iid> \
  --contract release-contracts/<feature>.yaml \
  --json > /tmp/release-parity-report.json
```

脚本执行以下检查：

- canonical origin/verification MR 必须已合入 staging、source branch 相同，且
  verification MR 必须出现在该 canonical branch 的 staging MR 历史中。
- candidate MR 必须打开并目标为 `main` / `master` / `release/*`。
- GitLab MR ref 和生产目标远端 ref 必须精确等于 API 返回的 SHA。
- base 必须是对应 head 的祖先。
- 使用 `--no-renames` 获取精确变更路径，删除加新增不能冒充重命名。
- 比较每个路径修改前后的 Git 对象类型、mode 和 object ID。
- 空白、YAML/Python 缩进、文件权限和二进制内容变化都会被识别。
- 特殊文件名按 literal path 处理，不能被解释为 Git pathspec。
- 报告候选分支缺失、额外或精确变更不同的路径。
- contract 必须存在于 candidate commit 的 `release-contracts/`，脚本从该 commit
  读取并输出 blob OID/SHA-256；工作区临时文件不能参与放行。
- 校验显式允许的环境差异、候选文件 SHA-256、Git object type/mode 和内容要求。

这种比较是有意保守的：同一路径存在无法分类的基线或内容差异时应阻断，而不是猜测其
是否“等价”。

随后人工检查提交拓扑和冲突解决语义：

```bash
git log --oneline --decorate <canonical-base>..<canonical-head>
git log --oneline --decorate <candidate-base>..<candidate-head>
git cherry <candidate-head> <canonical-head>
git range-diff \
  <canonical-base>..<canonical-head> \
  <candidate-base>..<candidate-head>
```

`range-diff` 是评审证据，不是唯一门禁。squash、rebase 和冲突解决可能改变 commit
形态，因此仍须通过精确文件变更检查。

## 发布契约

每个功能建立独立契约，不使用全局可变白名单：

```yaml
version: 1
feature: lab-feature-policy

allowed_differences:
  - paths:
      - config/application-*.yml.tpl
    reason: 环境 profile 名称和 endpoint 不同
    owner: iot-platform
    temporary: false
    required_content:
      - ref: candidate
        path: config/application-staging.yml.tpl
        absent: true
      - ref: candidate
        path: config/application-prod-us.yml.tpl
        object_type: blob
        mode: "100644"
        contains:
          - "labFeaturePolicyEnabled: true"
        sha256: "<生产候选文件准确内容的 SHA-256>"

external_gates:
  - name: growthbook-production-policy
    required: true
    evidence: https://example.internal/policy/evidence
    expected_contains:
      - '"runtimeEnabled": true'
```

约束：

- YAML 重复 key、未知字段和错误字段类型直接失败。
- `version` 必须是整数 `1`，`feature` 必填。
- 每条允许差异必须包含 `paths`、`reason`、`owner` 和非空 `required_content`。
- 通配模式必须从明确的顶层目录开始；单个 `*` 不跨 `/`，`**` 才跨目录。
- 多条允许规则不能同时匹配同一路径。
- 每个候选差异路径都必须有同路径断言：
  - 候选文件存在时必须提供准确 `sha256`，可同时用 `contains` / `not_contains` 表达可读语义。
  - 候选文件应不存在时必须声明 `absent: true`。
- 非 absent 断言默认要求 Git `blob/100644`；可显式声明 `mode: "100755"`。符号链接
  `120000` 和其他 object type 不能冒充普通配置文件。
- `contains` 不能替代 SHA-256；它可能命中注释，只用于帮助评审者理解预期内容。
- 临时差异还必须提供带引号的 `expires: "YYYY-MM-DD"`，且日期必须晚于当前 UTC 日期。
- 未命中的差异规则会失败，防止过期豁免长期存在。
- 必需外部门禁必须提供可审计的 HTTPS 证据 URL 和非空 `expected_contains`。contract
  不接受 `status: ready`，防止声明者给自己放行。
- 脚本将整体 `status` 标为 `live-audit-required`，同时仅对代码输出
  `code_status: pass`。Auditor 必须使用对应平台
  Skill/API 实时打开 URL，把原始响应写入临时证据文件，并记录环境、对象 key、实际值、
  工具和 UTC 时间；无法读取等同于失败。
- 父会话必须把 Auditor 的结构化结果交给
  `scripts/validate_drive_audit.py`；只有该脚本输出 `AUDIT PASS` 才能完成 MR。

## 晋级报告

创建或更新 MR 后、启动 Driver 前记录：

```text
主功能分支基线（staging MR diff_refs.base_sha）：
主功能分支 verified SHA：
生产目标远端 ref 和 SHA：
GitLab candidate MR SHA：
contract blob OID/SHA-256：
完全一致的变更路径：
允许的环境差异及文件 SHA-256：
阻断的缺失、额外或不同路径：
外部门禁实时观测值、来源 URL 和 UTC 时间：
绑定 verified SHA 的 staging 验证：
代码一致性结果：PASS / FAIL
外部门禁结果：LIVE_AUDIT_REQUIRED / PASS / FAIL
```

任何未分类差异都必须阻断。

## CI 集成

经常执行 staging 到生产晋级的仓库，应把脚本配置为生产目标分支的 required MR job。
canonical verification MR IID、candidate MR IID 和 contract 路径可以来自 GitLab CI 变量
或项目专属 wrapper；ref 和 SHA 必须继续由脚本从 GitLab API 读取。

Skill 负责指导 Agent，只有确定性的 CI 门禁才能防止流程被绕过。
