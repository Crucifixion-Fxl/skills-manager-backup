# Review 文档批量闭环

从 review 文档批量导入 issue → 修复 → 自动验证修复 → 关闭的闭环工作流。原 `issue-tracker` skill 合并至此，成为 `gitlab-issue-sop` 的批量能力。

Label 分类统一引用 [Label Governance executable SSOT](label-system.md)，本流程不维护 review 专用 label 模型。

## 核心价值

解决三个痛点：

1. **批量导入**：review 文档产出几十个问题，手动一个个 `glab issue create` 加回写链接太慢
2. **修复验证**：标 `[x]` 不等于问题真的修了，需要自动读取文件验证矛盾是否消除
3. **状态同步**：review 文档和 GitLab issue 是两套状态，容易不同步

**本地存储策略**：review 文档本身就是本地 SSOT，不生成额外追踪文件。import 回写链接、verify/status 读取状态都直接操作 review 文档。没有 review 文档的 ad-hoc issue 走 SKILL.md 主流程创建，只存 GitLab。

## 前置条件

- 当前仓库 remote 指向 GitLab，且已安装 `glab` CLI
- `glab auth status` 鉴权正常（如失败，提示用户运行 `glab auth login`）
- review 文档遵循下方「review 文档约定格式」

## 子命令

### 1. `import` — 从 markdown 批量创建 issue

**触发词**：导入 review 问题、批量创建 issue、从 review 文档创建 issue

**输入**：一个包含问题条目的 markdown 文件路径

**执行流程**：

```
Step 1：解析 markdown 文件
  - 识别问题条目格式（支持两种）：
    格式 A（review 文档）：### R-XX 标题 + 表格（状态/严重度/问题文件/...）
    格式 B（checkbox 列表）：- [ ] P0/P1/P2 — 描述 | `file:line`
  - 提取：ID、标题、严重度、问题文件路径、问题描述、修复建议

Step 2：确认创建范围
  - 展示解析结果摘要（数量、严重度分布）
  - 询问用户：全部创建 / 只创建某个严重度 / 取消

Step 3：检查或创建 label
  - 读取继承的 Group labels，按 `label-system.md` 的 canonical taxonomy 分类
  - review finding 默认使用 type::maintenance、status::triage，可按影响面添加 area/testing
  - 原 review 严重度保留在 issue 正文，不自动等同于业务 priority
  - 公共 label 缺失时报告 Group owner，不在项目内临时创建同名 label

Step 4：批量创建 GitLab issue（遵循 SKILL.md 主规则）
  - 每个问题条目 → 一个 issue
  - title：原始 ID + 标题（如 "R-01: plugin-context Streaming 示例与 ADR-PROTO-01 矛盾"）
  - label：使用公共规范的最小集合；priority 在 triage 后由业务影响决定
  - assignee：必须指定（漂浮 issue 禁止，按 SKILL.md「指定 assignee 的原则」推断）
  - description：按 SKILL.md 标准 5 段模板，附上问题文件、问题描述、修复建议、来源文件引用

Step 5：回写 issue 链接到原始 markdown
  - 在每个问题条目的表格中插入 `| **GitLab Issue** | [#N](url) |` 行
  - 不改动其他内容
  - review 文档本身即为本地 SSOT，不生成额外追踪文件
```

### 2. `verify` — 验证修复是否真正解决了问题

**触发词**：验证修复、这个 issue 修好了吗、检查 R-XX

**输入**：issue 编号（`#N`）或 review ID（`R-XX`）

**执行流程**：

```
Step 1：获取问题上下文
  - glab issue view <number> 拉取 description
  - 提取：问题类型（矛盾/缺失/超限/格式）、涉及的文件路径和行号、问题描述

Step 2：读取文件当前内容
  - 读取 description 中引用的所有文件的当前内容
  - 文件不存在，记录为"文件缺失"

Step 3：按问题类型验证
  问题类型自动判定规则：
  - description 含"矛盾"/"不一致"/"冲突" → 跨文档矛盾类
  - description 含"缺失"/"缺少"/"未创建" → 文档缺失类
  - description 含"超过 N 行"/"超限" → 超行数类
  - description 含"格式"/"编号"/"重复" → 格式类
  - 其他 → 通用类（读取 issue 修复建议，提取可验证条件，逐条检查）

  跨文档矛盾类：
    - 读取两个文件中引用的段落
    - 检查关键术语/签名/数值是否已对齐
    - 报告对齐状态

  文档缺失类：
    - 检查目标文件/目录是否已创建
    - 检查文件是否有实质内容（非空、非纯标题）
    - 报告创建状态

  超行数类：
    - 统计当前文件行数
    - 与阈值比较
    - 报告是否达标

  格式类：
    - 读取文件，检查具体的格式问题（编号连续性、重复标题等）
    - 报告修复状态

  通用类：
    - 从 issue 修复建议中提取可验证条件（如"补充 N 个异常路径"、"新增 section"）
    - 逐条检查条件是否满足（关键语句存在性、结构变更、阈值达标）
    - 仅"文件有 diff"不算通过 — 必须证明修复建议中的具体条件已满足

Step 4：输出验证结果
  ✅ 验证通过：提示用户确认关闭（glab issue close <number>）
  ❌ 验证失败：报告哪些点还没修，给出具体位置
```

### 3. `status` — 查看当前分支的问题修复进度

**触发词**：issue 进度、还有多少问题、issue dashboard

**输入**：review 文档路径（可选，缺省时从 GitLab 查询）

**执行流程**：

```
Step 1：收集问题状态
  - 有 review 文档：解析文档中的 [ ] / [x] 状态 + issue 链接
  - 无 review 文档：glab issue list 查询
    label 选择策略：
    1. 优先使用用户指定的 --label 参数
    2. 缺省时查询当前仓库所有 open issue（不按 label 过滤）
    3. 按 label 分组展示（无 label 的归入"未分类"）

Step 2：与 GitLab 交叉比对（有 review 文档时）
  - 检查不一致：本地 [x] 但 GitLab open / 本地 [ ] 但 GitLab closed

Step 3：输出进度摘要
  格式：
  ┌─────────────────────────────────────────────┐
  │ feat/xxx — Issue Dashboard                   │
  ├─────────────────────────────────────────────┤
  │ P0:  3 open /  8 closed  ████████░░░  73%  │
  │ P1: 12 open /  7 closed  ████░░░░░░░  37%  │
  │ P2: 17 open /  0 closed  ░░░░░░░░░░░   0%  │
  ├─────────────────────────────────────────────┤
  │ 建议下一个修复: #3 PortManager 文档不足       │
  └─────────────────────────────────────────────┘
```

### 4. `fix` — 开始修复某个 issue

**触发词**：修复 #N、fix R-XX、开始修 issue

**输入**：issue 编号或 review ID

**执行流程**：

```
Step 1：获取 issue 详情
  - glab issue view <number> 拉取 description
  - 提取问题文件路径、行号、修复建议

Step 2：读取问题文件当前内容
  - 读取 description 中引用的所有文件
  - 如涉及跨文档矛盾，同时展示两个文件的相关段落
  - 高亮问题行

Step 3：展示修复上下文
  - 问题描述
  - 当前文件内容（问题区域）
  - 修复建议
  - 相关的 ADR 或架构约束

Step 4：等待用户确认开始修复
  - 用户确认后，按修复建议执行
  - 修复完成后自动调用 verify 验证
```

### 5. `sync` — review 文档与 GitLab 双向同步

**触发词**：同步 issue 状态、sync issues

**输入**：review 文档路径

**执行流程**：

```
Step 1：解析 review 文档中的 issue 条目
  - 提取每个条目的：状态（[ ] / [x]）、GitLab issue 链接（#N）
  - 跳过没有 issue 链接的条目

Step 2：glab issue view 查询每个 issue 的当前状态

Step 3：比对差异并同步
  - 文档 [x] + GitLab open → glab issue close
  - 文档 [ ] + GitLab closed → 更新文档为 [x] + 补充修复记录
  - 两者一致 → 跳过

Step 4：报告同步结果
```

## Review 文档约定格式（强制）

`import` / `status` / `sync` / `verify` 操作的 review 文档**必须**遵循以下格式，否则自动化无法定位文件/状态/issue 链接。`/code-review` 和 `/architect` 输出已符合。

```markdown
### R-XX 问题标题

| 字段 | 内容 |
|------|------|
| **状态** | [ ] |                              ← import 后不变，修复后改为 [x]
| **GitLab Issue** | [#N](url) |               ← import 自动插入此行
| **严重度** | 🔴 P0 / ⚠️ P1 / ⚠️ P2 |
| **问题文件** | `path/to/file.md` 第 N 行 |    ← verify 用此定位
| **修复建议** | ... |
| **修复记录** | abc1234 |                      ← 修复后填写 commit hash
```

`status` 通过解析所有 `| **状态** |` 行统计 open/closed；`sync` 通过 `| **GitLab Issue** |` 行定位 issue 编号；`verify` 通过 `| **问题文件** |` 行定位要检查的文件。

## glab 命令速查

```bash
# 创建 issue（含 assignee、label、description）
glab issue create \
  --title "R-01: 标题" \
  --label "type::maintenance,status::triage,area/testing" \
  --assignee @username \
  --description "$(cat issue-body.md)"

# 创建 label
glab label list -g <group-path>

# 查询 open issue（按 label 过滤）
glab issue list --label "area/testing" --state opened

# 查看 issue 详情
glab issue view <number>

# 关闭 issue
glab issue close <number>
```

部分操作 `glab` 原生命令不支持（如 label 颜色批量查询、board list 操作），使用 `glab api` 兜底：

```bash
# 列出所有 label（含颜色和 id）
glab api projects/:id/labels

# board 操作
glab api projects/:id/boards
glab api -X POST projects/:id/boards/:board_id/lists -f label_id=<id>
```

## 与其他 Skill 的联动

| 上游 Skill | 联动场景 |
|-----------|---------|
| `/code-review` | review 完成后，提示"发现 N 个问题，是否走 `gitlab-issue-sop` 批量 import 创建 issue？" |
| `/architect` | 架构审查完成后，同上 |
| `/gitlab-mr` | 创建 MR 时，MR description 附加"该分支还有 N 个 open issue" |

## 示例

### Good — 闭环一条龙

```
# 1. 一条命令批量导入（自动创建 + 自动回写链接）
从 docs/review/2026-04-14-architecture-review.md 导入 review 问题

# 2. 开始修复某个 issue（自动展示上下文）
fix #3

# 3. 修完后验证（自动读取文件检查矛盾是否消除）
verify #3
# ✅ port-manager.md 已从 67 行扩充到 182 行，6 个异常路径均已补充

# 4. 查看进度
issue 进度
# P0: 3 open / 8 closed (73%)
```

### Bad — 手动管理 issue

```
# 1. 一个个 glab issue create（11 个花 10 分钟）
# 2. 手动回写链接到 review 文档（又 10 分钟）
# 3. 修完代码后，手动标 [x] + 手动关 issue
#    忘了验证：overview.md 第 301 行真的改了吗？
# 4. 一个月后发现：标了 [x] 的问题实际没修好
```
