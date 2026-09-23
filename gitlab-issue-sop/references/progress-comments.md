# Progress Comments — 进展评论 SOP

## 核心原则

**原始描述不变，进展靠 comment。**

- 描述 (description) = 不可变事实：背景 / 范围 / 依赖 / 验收 / 相关文档
- 评论 (comment / note) = 可变时间线：每次进展、MR 链接、阻塞原因、follow-up

这种分离保证了：
- 新人进来读描述就能理解 issue 要做什么（不用翻 50 条评论）
- 历史进展完整可追（不会被后面的 edit 覆盖）
- 评论可按时间线排序看演化

## 标准模板

```markdown
## 进展更新 (YYYY-MM-DD)

[一句话：状态变化或里程碑]

**相关 MR**:
- 仓库/!iid

**Follow-up**:
- 下一步
- 另一步
```

如果是阻塞更新，加一段：

```markdown
**阻塞原因**:
- 等 data team 审核 tracker schema（issue: analytics-config/#42）
```

## 何时加 comment

| 触发事件 | 是否加 |
|----------|--------|
| issue 创建后完成初版方案 | **是**（说明方案已产出、待 review） |
| 方案文档提交 commit | **是**（附文档链接或 commit） |
| status label 变化 | **是**（说明为什么变） |
| 从 issue 创建开发 branch | **是**（说明 branch 名称，进入实现） |
| MR 创建 | **是**（附 MR 链接） |
| MR 合并 | **是**（说明合并到哪个分支 / 是否已部署） |
| 部署验证完成 | **是**（说明生产环境验证通过 → 可 close） |
| 阻塞发生 | **是**（说明阻塞原因 + 预期解除时间） |
| 阻塞解除 | **是**（说明怎么解除的） |
| scope 变化 | **是**（说明为什么变；重大变化需 **同步改描述** 并 comment 说明改了什么） |
| 设计讨论中产生关键决策 | **是**（决策记录） |
| 日常 "今天写代码了" | 否（噪声） |

## 不要做的事

### 先改东西再补 issue

```
# ❌ 反模式
先写代码 / 改 SOP / 改文档，做完以后再建 issue
```

issue 应该是入口，不是补票工具。否则方案、review、commit、merge 无法形成清晰链路。

### 改原始描述记录进展

```
# ❌ 反模式
描述末尾追加 "2026-04-11: 已完成 70%"
描述末尾追加 "2026-04-12: MR 合并"
```

两条信息在同一段，时间线混乱。一个月后再看只剩最后一次，前面的进展全丢。

### `git push --force` 改 commit 来更新 issue

issue 的内容和 commit history 没关系，别混淆。

### 在 comment 里放超长实现细节

实现细节归 MR description。comment 只说 "MR 创建了，见 !18"。reviewer 自然会去 MR 看细节。

### 不写日期

```
# ❌
"MR 已合并"

# ✅
## 进展更新 (2026-04-12)
MR 已合并到 master
```

日期让后人快速定位。GitLab 评论有时间戳但日期 inline 更方便阅读。

### 只在 Slack / 飞书说不在 issue 说

issue 是 SSOT。Slack 是易失介质。重要进展一定要进 issue comment，即使已经在 Slack 告知过。

### 手工创建开发 branch，绕过 issue

```
# ❌
git checkout -b feature/foo

# ✅
从 GitLab issue 页面 Create branch
```

这样 branch、commit、MR、merge 才能持续关联到同一个 issue。

## 方案评审 comment 模板

```markdown
## 方案提交 (YYYY-MM-DD)

方案文档已提交，待 review。

**设计文档**:
- docs/plans/xxxx.md

**相关 commit**:
- abcdef1

**Follow-up**:
- 请 reviewer 在 issue 或文档上批注
- review 通过后从本 issue Create branch 开始实现
```

## 分支创建 comment 模板

```markdown
## 进展更新 (YYYY-MM-DD)

已从本 issue 创建开发分支，开始实现。

**Branch**:
- feature/123-xxx

**Follow-up**:
- 开发完成后创建 MR 并回链到本 issue
```

## 完整示例（SmartPopup #20 进展序列）

```markdown
## 进展更新 (2026-04-10)

设计定稿。dwm_smart_popup_funnel 宽表 schema 确定，6 个核心字段 + 3 个维度字段。

**相关 MR**:
- 无（待开始）

**Follow-up**:
- status: backlog → ready
- assign @lisi 起 dbt MR
```

```markdown
## 进展更新 (2026-04-11)

dbt MR 已创建，预期 2 天内合并。

**相关 MR**:
- dbt/!2988

**Follow-up**:
- 等 review
- status: ready → in-progress → in-review
```

```markdown
## 进展更新 (2026-04-12)

dbt/!2988 已合并到 master，宽表在测试环境产出正常，数据抽样对比明细表 OK。

生产环境已跑一次 full refresh，数据对齐。

**Follow-up**:
- 通知 #19（观测埋点）解除阻塞，可开始 GrowthBook 配置
- close 本 issue
```

## 跨 issue 协同

阻塞解除时应做两件事：

1. 在被阻塞的 issue 加 comment（"阻塞已解除，可以开工"）
2. 移除被阻塞 issue 的 `flag/blocked`；原 `status::*` 保持不变

```bash
# 1. comment（glab 原生命令）
glab issue note 19 --message "## 进展更新 (2026-04-12)

阻塞已解除（#20 dwm 宽表已产出）。"

# 2. 改 label（见 batch-operations.md 的 relabel.sh，底层也是 glab api -X PUT）
./relabel.sh 19 remove flag/blocked
```

这样 board 视图会同步，团队其他人立刻看到。
