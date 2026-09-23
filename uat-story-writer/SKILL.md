---
name: uat-story-writer
description: 从用户视角发现缺失的 UAT 测试场景，通过引导式访谈确认需求，输出符合 story-craftsman 模板 + Gherkin Given/When/Then 格式的用户故事文件。当用户提到"补充测试用例"、"缺少XX场景的测试"、"帮我写UAT用例"、"写测试场景"、"brainstorm test cases"、发现测试覆盖空白时，使用此 skill。即使用户只是提到某个功能"没测过XX情况"，也应触发。
---

# UAT Story Writer

从真实用户使用场景出发，发现功能模块中缺失的 UAT 测试场景，输出结构化的用户故事文档。

**核心理念**: UAT 测试从用户故事出发，不是从技术实现出发。"在家看摄像头"比"局域网环境测试"更能揭示真实需求。

## Rules

1. **用户视角优先**: 每个场景必须对应一个真实的用户行为或使用情境，而非技术条件。技术条件是场景的背景，不是场景本身
2. **信息不足不脑补**: 对测试环境能力、实际拓扑等关键约束，必须通过访谈获取，不可假设
3. **验收标准可执行**: 每条 AC 使用 Gherkin Given/When/Then 格式，确保 devium-ai 等工具可直接消费
4. **输出到 cases/ 目录**: 用户故事放在项目的 `cases/` 目录下，`features/` 目录留给 AI 生成的可执行自动化用例
5. **遵循 story-craftsman 模板**: 文件结构严格遵循 story-craftsman 的模板格式（背景、目标、角色、用户故事、进展、更新记录）
6. **Requirements review-only 模式**：被 `requirements-analysis-agent` 调用时，只审查用户旅程、happy/error/recovery 场景和 Gherkin-ready AC 缺口；不得写 `cases/` 或 `features/`，不得执行“一次一题”访谈。所有缺口必须一次性合并返回上游，由 Requirements Agent 决定 READY 或 `ATTEMPT_NEEDS_INPUT`。
7. **入口分类**：若请求提出 new or changed intended behavior，必须先由 `requirements-analysis-agent` 产出并经 PO 接受 exact accepted REQUIREMENTS Artifact；不得借“补 UAT”直接定义新需求。只有已有 accepted requirement 的测试派生，或不改变预期行为的 test-only coverage gap，才可直接进入本 Skill 的交互/写文件流程。

## Review-only mode

当调用方声明 `review_only=true` 或来源是 `requirements-analysis-agent`，跳过本 Skill 的交互式 Step 2 和写文件 Step 4。返回 `verdict`、稳定 finding ID、evidence refs、missing journeys 和合并问题集；不得把建议场景冒充已接受 AC，也不得解锁测试设计或实现。

---

## 执行流程

### Step 1: 探索现有测试覆盖

先判断输入是新/变更行为还是 test-only coverage gap。前者缺少 accepted REQUIREMENTS Artifact 时停止并转交 Requirements；后者才继续探索现有覆盖。

先了解项目已有的测试用例，找到覆盖空白：

- 读取 `features/scenarios/` 下相关的 `.feature` 文件，了解已有场景
- 读取 `cases/` 下已有的用户故事文件，避免重复
- 列出当前已覆盖的场景清单，明确指出缺失的维度

### Step 2: 引导式访谈（一次一题，多选优先）

通过提问确认以下关键约束，每次只问一个问题，优先提供多选项：

| 维度 | 目的 | 示例问题 |
|------|------|---------|
| **测试环境能力** | 确定场景粒度 | "你的测试环境能模拟网络条件吗？A) 路由器/防火墙控制 B) 只能物理切换 C) 两者都可以" |
| **实际使用拓扑** | 确定覆盖范围 | "以下哪些是你们实际会遇到的？A) 局域网 B) 4G远程 C) 跨网络 D) 以上全部" |
| **关注维度** | 确定验收深度 | "你关注的是功能性还是也关注体验指标？A) 只关注功能性 B) 也关注体验 C) 两者都要" |
| **变量控制** | 确定谁是被测对象 | "哪一端是你能控制的变量？手机端？设备端？还是两端都可以？" |

访谈原则：
- **一次一题**: 不要同时问多个问题，避免信息过载
- **多选优先**: 能给选项就给选项，降低用户回答成本
- **及时停止**: 信息足够就停止提问，不要过度访谈。通常 2-4 个问题足矣
- **UAT 纠偏**: 如果用户的描述偏向技术视角（如"测试4G网络"），引导回用户视角（如"用户在外面远程查看"）

### Step 3: 场景发散（用户故事视角）

基于访谈结果，从用户日常使用的真实情境出发发散场景。

关键思维框架——想象用户一天的使用旅程：
- **用户在哪里？** （家里、办公室、路上、朋友家...）
- **用户在做什么？** （主动查看、收到通知后查看、在做别的事时顺便看...）
- **环境发生了什么变化？** （出门了、进电梯了、切换了网络...）
- **出了什么意外？** （断网了、信号不好、设备离线了...）

以表格形式呈现发散结果，包含：场景编号、用户场景描述、对应的环境/技术条件。

呈现后询问用户："这些场景覆盖够了吗？有没有要增减的？"

### Step 4: 生成用户故事文件

确认后，按 story-craftsman 模板生成文件，保存到 `cases/` 目录。

**文件命名**: `{feature}-{dimension}.md`（小写 kebab-case），例如 `live-view-network-environments.md`

**文件结构**:

```markdown
# <主题> - 用户故事

> 参考文档: [相关feature文件](../features/scenarios/xxx.feature)

（mermaid 流程图：核心流程 + 变量维度）

## 1. 背景（Why）
## 2. 目标（What）
## 3. 角色（Who）
## 4. 用户故事（User Stories）

### 4.1 US-<MODULE>-<DIM>-01: <用户场景标题> [P0/P1/P2]

**作为** <角色>，**我希望** <能力/需求>，**以便于** <价值/目的>。

**验收标准（AC）:**
- [ ] Given <前置条件>，When <用户操作>，Then <预期结果>
- [ ] Given <前置条件>，When <用户操作>，Then <预期结果>

## 5. 进展
## 6. 更新记录
```

**验收标准编写要点**:
- Given 描述用户所处的情境和环境状态，而非技术配置
- When 描述用户的实际操作动作
- Then 描述用户可观测到的结果（界面反馈、状态变化）
- 每个 US 包含 2-4 条 AC，覆盖核心路径和关键异常

**优先级判定**:
- **P0**: 用户最常遇到的核心场景（如在家看直播、在外远程看）
- **P1**: 用户较常遇到但非每次必经的场景（如网络切换、断网恢复）
- **P2**: 极端或低频场景

---

## Examples

### Bad — 用技术视角写 UAT 用例

```
Scenario: 4G网络环境下直播测试
  Given 手机连接4G网络
  When 发起直播请求
  Then 视频流通过公网TURN服务器中转成功建立
```

### Good — 用用户视角写 UAT 用例

```
### US-LIVE-NET-02: 在外面远程查看家里摄像头 [P0]

**作为** VicoHome 用户，**我希望** 在外出使用手机流量时能远程查看家中摄像头直播，
**以便于** 随时随地了解家中情况。

**验收标准（AC）:**
- [ ] Given 手机已关闭 WiFi 并使用 4G 蜂窝网络，
      When 用户点击播放按钮，
      Then 直播画面成功加载并显示 LIVE 指示器
- [ ] Given 4G 网络直播已建立，
      When 用户进行全屏、声音切换等操作，
      Then 各功能正常响应
```

### Good — 设备分享权限场景

```
### US-SHARE-PERM-01: 家人用分享的账号查看摄像头 [P0]

**作为** 被分享的家庭成员，**我希望** 用分享链接/邀请加入后能查看摄像头直播，
**以便于** 家人也能随时关注家中状况。

**验收标准（AC）:**
- [ ] Given 主账号已将设备分享给家庭成员账号，
      When 家庭成员登录 App 并进入设备列表，
      Then 能看到被分享的设备并成功播放直播
- [ ] Given 家庭成员正在查看分享设备的直播，
      When 主账号取消该设备的分享权限，
      Then 家庭成员的直播中断并提示无权限
```

---

## 与其他 Skill 的关系

- **story-craftsman**: 本 skill 使用其模板格式，但聚焦于 UAT 场景发散和 Gherkin AC 编写
- **testing-strategy**: 本 skill 产出的用户故事属于 L4 (UAT) 层级，与整体测试分层策略对齐
- **devium-ai**: 本 skill 的产出（`cases/` 下的用户故事）是 devium-ai 生成可执行自动化用例（`features/` 下的 `.feature` 文件）的输入
