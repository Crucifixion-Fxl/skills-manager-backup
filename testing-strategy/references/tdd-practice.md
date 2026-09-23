# TDD 实践方法 — 各层节律 + 新功能 test-first + Bug Fix Rule

本文档是 `testing-strategy` skill 的 TDD 规范参考，与 `code-review` skill 的 [`tdd-sufficiency-review.md`](../../code-review/references/tdd-sufficiency-review.md) 正交：
- **本文档**：**设计侧** — 教项目组**怎么在 `strategy.html` 里定义 TDD 节律**，让新功能/bug 修复按 TDD 流程走
- **code-review 侧**：**审查侧** — 审查团队是否真的在 test-first 实践（git log 分析、per-author 报告）

---

## 1. 为什么 L3-first TDD 是最大 ROI

大多数人提到 TDD 默认是 **L1 单元 TDD**，但**真正节约时间的是 L3 TDD**：

| 层级 | 典型收益 | 时间节约 |
|:---|:---|:---|
| L1 TDD | 逻辑正确 + 重构安全 | 线性（避免重写 1 个 function） |
| **L3 TDD** | **端到端路径早发现集成问题** | **10-30h/feature（集成调试）** |
| L4 TDD | AC 早锁定，减少发布扯皮 | 少返工 1-3 轮 |

### L3-first 省时间的原理

不写 L3-first 的常见代价（naturehood 实证）：
- App 端 Semantics locator 写完了，才发现后端 API 字段是 `bird_name` 不是 `species_name`
- Hub 页面实现完了，才发现后端 og 接口漏 `og:image` 字段（§191/§196）
- Admin 审核流程写完了，才发现后端没有 `resolveReport` endpoint（§1235）

这类 bug 每个至少 **2-4 小时排查 + 跨人协调**，10-30 小时/feature 是保守估计。

### L3 test-after vs test-first 时间差

| 阶段 | test-after | test-first |
|:---|:---|:---|
| 设计 | 凭空想 API 字段 | 写 expected request/response JSON |
| 实现 | 各层并行（后端 + 前端）| 后端先出，前端按 test 驱动 |
| 集成调试 | **字段不匹配返工 2-4h/bug** | 已被 test 拦截 |
| 发布前 | 手工点击验证 AC | Playwright AC 测试自动跑 |
| 后续维护 | 改字段全栈重测 | contract test 直接 red |

**一个 feature 有 5-10 字段 + 3 跨服务集成点时，L3 test-first 节约 10-30 小时**。

### 推广 L3-first 的阶梯（4 周）

不要一次性要求所有人做 L3-first：

1. **Week 1-2**：仅要求**新 feature** 的 US-level L3 test 先出 red commit
2. **Week 3-4**：bug fix 涉及集成层面（API breaking change），先出 L3 repro commit
3. **Week 5+**：老 feature 的 L3 补齐（集体欠债逐个 ticket）

团队内选一个 "TDD 先锋"（通常是 test 习惯最好的人）牵头跑 1-2 个示范。

---

## 2. 新功能 TDD 流程（test-first）

### 2.1 AC 驱动的 L3-first 4 步

每个新 US AC 开工前，**先把 L3 test skeleton 提交为 red commit**：

```bash
# Step 1: 读 US AC，写 Playwright test skeleton
# 只写 describe / it 结构 + 期望的 selector + 期望的 API response
cat > admin/tests/l3-browser/us-ex-04.spec.ts <<'EOF'
import { test, expect } from '@playwright/test';

test.describe('US-EX-04: 举报后 post 从 feed 移除', () => {
  test('reporter 不再在 feed 看到被举报的 post', async ({ page, request }) => {
    // Given: 3 个 post 在 feed 里（其中 post:P 属于 creator C）
    // When: user U 对 post:P 提交 report
    // Then: user U 再拉 feed 应该不含 post:P
    await page.goto('/explore');
    await expect(page.getByTestId('feed-item-P')).toBeVisible();  // 先看到
    await page.getByTestId('feed-item-P').getByRole('button', {name: 'Report'}).click();
    await page.getByRole('textbox', {name: 'reason'}).fill('spam');
    await page.getByRole('button', {name: 'Submit'}).click();
    await page.reload();
    await expect(page.getByTestId('feed-item-P')).not.toBeVisible();  // 移除
  });
});
EOF

# Step 2: Commit as red（预期 CI allow_failure 或本地跑）
git add admin/tests/l3-browser/us-ex-04.spec.ts
git commit -m "test(explore): red US-EX-04 report removes from feed"

# Step 3: 后端实现 contract_test + logic，让 contract 绿
#         前端实现 UI，让 Playwright 绿

# Step 4: Commit as green
git add ...
git commit -m "feat(explore): green US-EX-04 report removes from feed"
```

### 2.2 红绿重构 commit 范式

团队推广这三种 commit message 前缀：

| 前缀 | 含义 | CI 行为 |
|:---|:---|:---|
| `test(xxx): red <AC>` | 失败测试的独立 commit | CI allow_failure 或本地 |
| `feat(xxx): green <AC>` / `fix(xxx): green <AC>` | 让 test 变绿的实现 | CI 必须绿 |
| `refactor(xxx): ...` | 不改行为只改结构 | CI 必须绿 |

**效果**：
- git log 里直接看出红绿节律
- code-review skill 可以 grep `^test.*: red` 量化 TDD 实践度
- CI 可以对 `test(xxx): red` 放宽约束，对 `green/refactor` 严格

### 2.3 各层 TDD 决策表

| 场景 | L1 先写 | L2-1 先写 | L2-2 先写 | L3 先写 | L4 先写 |
|:---|:---:|:---:|:---:|:---:|:---:|
| 新 US AC | — | ✅ contract expected schema | — | ✅ **Playwright skeleton** | ⭐ AC checklist |
| 修复 crash/state bug | ✅ repro test | — | — | — | — |
| 修复集成层 bug（跨服务）| — | ✅ contract red | — | ✅ E2E red | — |
| 重构（行为不变）| ⭐ 如无 test 先补 | — | — | — | — |
| 新算法（评分/排序）| ✅ table-driven red | — | — | — | — |
| 新后端 API endpoint | ✅ logic 测试 | ✅ contract expected | — | — | — |
| Schema migration | — | — | ✅ "读回来" 断言 | — | — |
| 纯 CI/build/infra 改 | — | — | — | — | — |
| 日志/metric 实现 | ⭐ 可选 | — | — | — | — |

> `✅` = 强制 test-first，`⭐` = 建议 test-first，`—` = 不需要

---

## 3. Bug Fix Rule — 写入 strategy.html 必写章节

每个项目的 `docs/testing/strategy.html` 必须包含 **"TDD 节律"** section，模板如下：

```html
<section id="tdd-rhythm">
  <h2>9. TDD 节律</h2>
  <section id="bug-fix-rule">
    <h3>9.1 Bug Fix Rule（引自 CLAUDE.md）</h3>
    <ol>
      <li>找到相关现有测试；若有，验证其能复现 bug。</li>
      <li>若无测试，先写一个失败的复现测试。</li>
      <li>修复代码让测试通过。</li>
      <li>无对应测试的 bug 修复视为不完整。</li>
    </ol>
  </section>
  <section id="new-feature-tdd">
    <h3>9.2 新功能 TDD</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>AC 类型</th><th>先写什么</th><th>commit 前缀</th></tr></thead>
        <tbody>
          <tr><td>UI 行为 AC</td><td>L3 Playwright skeleton</td><td><code>test(xxx): red &lt;AC&gt;</code></td></tr>
          <tr><td>API 契约 AC</td><td>L2-1 contract_test expected</td><td><code>test(xxx): red &lt;AC&gt;</code></td></tr>
          <tr><td>业务逻辑 AC</td><td>L1 table-driven test</td><td><code>test(xxx): red &lt;AC&gt;</code></td></tr>
        </tbody>
      </table>
    </div>
  </section>
  <section id="tdd-exemptions">
    <h3>9.3 豁免场景（无需 test-first）</h3>
    <ul><li>CI / build / infra 修复</li><li>文档 / config 调整</li><li>lint / formatter 修复</li><li>纯工具链 refactor（但整个 refactor 结束应补 integration test）</li></ul>
  </section>
  <section id="trace-back">
    <h3>9.4 左移追溯</h3>
    <p>每次 bug 修复后必须问：<strong>为什么更左侧的测试没有发现它？</strong> 在最左侧能覆盖的层级补用例。详见本页 <a href="#tdd-debugging">TDD 调试流程 · 左移追溯</a>。</p>
  </section>
</section>
```

---

## 4. Test-first vs Test-after 的反模式

### ❌ "我们有 80% 覆盖率所以 TDD 做得好"

**覆盖率 ≠ TDD**。覆盖率高可能是 test-after 补出来的，没有 red → green 节律。看 commit 历史才知道是不是 TDD。

### ❌ 只审 L1 TDD 漏掉 L3/L4

**L3-first 的 ROI 是 L1 的 3-10 倍**（集成调试时间节约）。如果项目只要求 L1 TDD，漏掉最大收益点。

### ❌ 测试 "绿" 但没真断言 AC（placebo test）

典型：
- 产品 AC："举报后 post 从 feed 移除"
- 测试：仅断言 `INSERT INTO explore_reports` 成功 → **绿**
- 现实：代码没写入 `explore_hides`，feed 仍显示该 post
- **测试和代码双方都不覆盖真正的 AC**

对策：
- AC 反向回溯：每个 US AC 在测试里 grep，看测试断言的是"代码副作用"（写入 DB 行）还是"AC 行为"（feed 不返回）
- mutation testing pilot：改代码看 test 是否红，< 60% 分数 = placebo 多

### ❌ 新功能"事后补测试"（test-after 冒充 TDD）

表现：
- 所有 test 都和 impl 同 commit 提交
- 无独立的 `test(xxx): red ...` commit
- reviewer 看不出是先写 test 还是先写 impl

后果：
- 写 test 时已经看到 impl，测试只验证"代码做了什么"而非"AC 要求什么"
- 实现里遗漏的行为，test 也跟着遗漏

对策：
- 强制红绿 commit 范式（见 §2.2）
- CI 或 git hook 检查 `fix:` commit 含 test 变更（见 code-review skill tdd-sufficiency-review.md）

### ❌ 规则写了没人知道怎么做

项目 `CLAUDE.md` 写了 "Bug Fix Rule (TDD)"，但：
- 没有 `docs/engineering/tdd-practice.html` 教**怎么**做
- 没有示例 commit pair（`test: red X` → `fix: green X`）
- 没有各层 decision table

结果：每人按各自理解做 TDD（有人 test-after，有人根本不做，有人做得好）。

对策：
- `strategy.html` 必写 `TDD 节律` section（§3 模板）
- Onboarding 文档指向 `tdd-practice.html`
- 选 1-2 个 TDD 先锋跑示范 MR

---

## 5. 示范 MR 模板（给 TDD 先锋用）

在 MR 描述里说明 TDD 节律：

```text
## 变更说明

- 实现 US-EX-04 "举报后 post 从 feed 移除"
- 采用 L3-first TDD：先提交 red commit，再后端/前端实现

## TDD 节律

| commit | 前缀 | 说明 |
|:---|:---|:---|
| 1234abc | test(explore): red US-EX-04 | L3 Playwright test skeleton |
| 2345bcd | feat(explore): green US-EX-04 backend logic | reportLogic 写入 explore_hides |
| 3456cde | feat(explore): green US-EX-04 UI | Flutter UI 按 Semantics locator 配对 |

## 相关文档

- user story：docs/product/user-stories/explore.html#US-EX-04
- test strategy：docs/testing/scenarios/explore/l3-browser.html
```

Reviewer 可以直接看 commit 顺序验证是否真 test-first。

---

## 6. Checklist

### 设计 strategy.html 时

- [ ] `## 9. TDD 节律` 章节已写（Bug Fix Rule + 新功能决策表 + 豁免场景 + 左移追溯）
- [ ] 明确 L3-first 的推广阶梯（Week 1-2 / 3-4 / 5+）
- [ ] 明确红绿 commit 范式（`test: red` / `feat: green` / `refactor:`）
- [ ] 明确豁免场景（CI/build/infra/formatter）

### 新 US 开工前

- [ ] 每个 AC 识别先写哪层测试（查 §2.3 决策表）
- [ ] L3-App AC → Playwright skeleton 先出 red commit
- [ ] L2-1 API contract → expected schema 先写
- [ ] L1 算法 AC → table-driven test 先写

### Bug 修复前

- [ ] 找现有测试 → 若有验证能复现
- [ ] 若无 → 写失败测试为独立 `test(xxx): red reproducing Y` commit
- [ ] 修复代码 → `fix(xxx): green Y` commit
- [ ] 左移追溯：更左侧层级能否覆盖？补用例

### 定期（Monthly）

- [ ] 用 code-review skill 的 tdd-sufficiency-review 方法跑一次团队 TDD 习惯 report
- [ ] 抽查最近 10 个 fix commit，test=0 占比应 < 25%（排除豁免场景）
- [ ] mutation testing pilot 验证最核心模块（postcard / rec-engine 等）

---

## 7. 参考

- 审查侧方法：[../../code-review/references/tdd-sufficiency-review.md](../../code-review/references/tdd-sufficiency-review.md)
- 实战案例：naturehood 项目 2026-04-24 专项审查（7 人 NoTest% 38% ~ 75%，admin L1 集体欠债，0 red commit 模式）
- CLAUDE.md 项目规则示例：Bug Fix Rule + 9-step workflow
