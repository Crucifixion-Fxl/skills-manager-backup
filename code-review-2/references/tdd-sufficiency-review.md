# TDD 充分性 review 方法

与 [`testing-coverage-review.md`](./testing-coverage-review.md) 互补。前者检查"L1-L4 分层覆盖是否齐全 / 文档-代码-CI 对账是否闭环"，本文档检查"**团队是否真的在实践 TDD**（test-first vs test-after）"。

本文是团队 / 项目级历史审计，不定义单个 MR 的 TDD Level。每个 MR 的等级、证据优先级和
非门禁语义，以
[`testing-strategy/references/tdd-level-assessment.md`](../../testing-strategy/references/tdd-level-assessment.md)
为唯一标准；
`code-review` 不应从近 500 个 commit 的团队统计反推当前 MR 的等级。

**适用时机**：
- 项目 `CLAUDE.md` / `AGENTS.md` 写了 "Bug Fix Rule (TDD)" 或 "9-step workflow 含 TDD Impl"
- 测试覆盖率看着 OK 但产品仍频繁出现低级 bug（placebo test 嫌疑）
- 团队自我认知"我们在做 TDD"，但实际效果存疑
- 用户主动要求 "review 一下 TDD 的充分性"

**核心诊断问题**：
1. **Bug Fix Rule 落地率**：每个 `fix:` commit 是否含回归测试？
2. **Test-first 存在性**：项目历史里有没有独立的 "red commit"（先提交失败测试再 impl）？
3. **各层成熟度**：L1 unit / L2-1 contract / L2-2 integration / L3-App / L3-Admin / L4 UAT 分别落地到哪里？
4. **Placebo test**：哪些测试"绿"但没真断言 AC？
5. **Per-author 习惯**：谁 test-after / 谁 test-first / 谁 no-test，差异如何？
6. **集体欠债**：哪些模块整体无人写测试？

---

## 1. Bug Fix Rule 落地率检查

### 1.1 近 N 个 fix commit 中的 test 含量

```bash
git log --oneline -500 | grep -iE "^[a-f0-9]+ fix" | awk '{print $1}' | while read sha; do
  msg=$(git log -1 --pretty=%s "$sha" | cut -c1-80)
  files=$(git diff-tree --no-commit-id --name-only -r "$sha")
  test_c=$(echo "$files" | grep -cE "_test\.(go|dart|py)|\.test\.(tsx|ts|jsx|js)|\.spec\.(ts|js)|contract_test\.go")
  prod_c=$(echo "$files" | grep -cE "\.(go|dart|tsx|ts|jsx|js|py)$")
  non_test=$((prod_c - test_c))
  if [ "$non_test" -gt 0 ]; then
    printf "%s  test=%2d prod=%2d  %s\n" "$sha" "$test_c" "$non_test" "$msg"
  fi
done
```

**诊断阈值**：
- `fix:` commit 中 test=0 的占比 ≥ 50% → **严重违反 Bug Fix Rule**
- 占比 25-50% → 落地不一致，需追问是哪些人
- 占比 < 25% → 落地较好

**允许的例外场景**（fix=0 test 合理的情况）：
- CI / build / infra 修复
- 文档 / config 调整
- lint / formatter 修复
- 工具链 refactor（但整个 refactor 结束应补 integration test）

---

## 2. Test-first 存在性诊断（关键）

### 2.1 搜索 "red commit" 模式

```bash
# 独立的 test: commit（未混入 impl）
git log --oneline -500 | grep -iE "^[a-f0-9]+ (test|repro|reproduce|failing|red)"

# 统计每作者写了多少独立 test commit
git log --oneline -500 | grep -iE "^[a-f0-9]+ test" | awk '{print $1}' | while read sha; do
  git log -1 --pretty='%an' "$sha"
done | sort | uniq -c | sort -rn
```

**诊断**：
- **0 个 red commit 模式** → 全员 test-after（即便写测试也是事后补，不是 TDD）
- 独立 test commit 集中在 1-2 人 → 只有少数人写测试，谈不上团队实践
- 分散在 50%+ 作者 → 有团队实践

**严格 TDD 的证据**是：
- `test(xxx): red reproducing bug Y` — **失败测试的独立 commit**
- next commit: `fix(xxx): Y` — 修复让 test 变绿
- 两个 commit 一前一后

项目里找不到这种 pattern = **严格 TDD 从未实践过**，即便规则写了。

---

## 3. 各层 TDD 成熟度量化

### 3.1 L1 单元测试覆盖率

```bash
# Go logic 层
LOGIC=$(find server/internal/logic -name "*Logic.go" ! -name "*_test.go" | wc -l)
TEST=$(find server/internal/logic -name "*Logic_test.go" | wc -l)
NO_TEST=$(find server/internal/logic -name "*Logic.go" ! -name "*_test.go" | while read f; do
  [ ! -f "${f%.go}_test.go" ] && echo "$f"
done | wc -l)
echo "Logic: $LOGIC files / Test: $TEST files / 无测试: $NO_TEST"

# Flutter packages
LIB=$(find packages -name "*.dart" -path "*/lib/*" ! -name "*.g.dart" ! -name "*.freezed.dart" | wc -l)
TEST=$(find packages -name "*_test.dart" | wc -l)
echo "Dart lib: $LIB / test: $TEST"

# React frontend
SRC=$(find admin/src -name "*.ts" -o -name "*.tsx" | grep -v test | wc -l)
TEST=$(find admin/src -name "*.test.*" | wc -l)
echo "Admin src: $SRC / test: $TEST"
```

### 3.2 L2-1 API contract test 覆盖率

```bash
# 有 handler 文件的 package 中，多少有 contract_test.go
HANDLERS=$(find server/internal/handler -name "*Handler.go" ! -name "*_test.go" | wc -l)
CONTRACT=$(find server/internal/handler -name "contract_test.go" | wc -l)
echo "Handlers: $HANDLERS / contract_test packages: $CONTRACT"

# 哪些 handler 目录完全无 contract_test
find server/internal/handler -type d | while read d; do
  h=$(find "$d" -maxdepth 1 -name "*Handler.go" ! -name "*_test.go" 2>/dev/null | wc -l)
  if [ "$h" -gt 0 ] && [ ! -f "$d/contract_test.go" ]; then
    echo "$d ($h handlers)"
  fi
done
```

**各层 L1/L2-1/L2-2/L3-App/L3-Admin/L4 都量化出来**，按严重度排列：

| 层级 | 理想 TDD 成熟度 | 典型值 |
|:---|:---|:---|
| L1 (unit) | 80%+ 文件有对应 _test | 50% = 欠债 |
| L2-1 (contract) | 每个 handler package 有 contract_test | <30% = 假分层 |
| L2-2 (integration) | 核心路径 DB/Redis tx 覆盖 | — |
| L3-App | 真 UI Semantics 驱动，非 API 伪装 | 查 §见 testing-coverage-review.md §2 |
| L3-Admin | admin 核心 workflow 覆盖 | — |
| L4 (UAT) | 自动化可量化 + 手工视觉 | 多数项目 0 |

---

## 4. Placebo test 检测（最难）

**Placebo test = 测试"绿"但没真断言 AC**。难找但伤害大。

### 4.1 看 US AC 反向回溯

```bash
# 每个 US AC → 对应测试 →  测试是否真断言 AC
# 机械做法：grep US-XX-NN 在测试文件里出现
grep -rn "US-[A-Z]*-[0-9]*" server/ admin/tests/ packages/ hub/e2e/ 2>/dev/null | head -50
```

**典型 placebo 反面例**：
- 产品需求 US-EX-04: "用户 report 后该 post 从 feed 移除"
- 测试 `TestReport` 检查 `explore_reports` 表插入成功 → **绿**
- 但代码 reportLogic 根本没写入 `explore_hides`，feed 仍显示该 post
- 即 **代码和测试双方都不覆盖真正的 AC "feed 移除"**

找 placebo 要：
- 每个"重要 AC"人工对照：生产代码真的实现吗？测试断言的是"代码副作用"（DB 插入）还是"AC 行为"（feed 移除）？
- 工具辅助：**mutation testing**（改代码看 test 是否红）

### 4.2 mutation testing pilot

```bash
# Go: go-mutesting
go install github.com/avito-tech/go-mutesting/...@latest
go-mutesting server/internal/logic/postcard/

# JavaScript: stryker
npx stryker run

# 如果 mutation score < 60% → 测试无法区分正确和错误代码 = placebo 多
```

---

## 5. Per-author TDD 习惯报告

这是 "**谁的 TDD 习惯差**" 的量化工具。

### 5.1 数据收集

```bash
git log --oneline -500 | grep -iE "^[a-f0-9]+ (fix|feat)" | awk '{print $1}' | while read sha; do
  author=$(git log -1 --pretty='%an' "$sha")
  all_files=$(git diff-tree --no-commit-id --name-only -r "$sha" 2>/dev/null)
  test_c=$(echo "$all_files" | grep -cE "_test\.(go|dart|py)|\.test\.(tsx|ts|jsx|js)|\.spec\.(ts|js)|contract_test\.go")
  prod_c=$(echo "$all_files" | grep -cE "\.(go|dart|tsx|ts|jsx|js|py)$")
  non_test=$((prod_c - test_c))
  msg=$(git log -1 --pretty=%s "$sha" | cut -c1-80)
  ctype=$(echo "$msg" | grep -oE "^(fix|feat)" | head -1)
  if [ "$non_test" -gt 0 ]; then
    printf "%s|%s|%d|%d\n" "$author" "$ctype" "$test_c" "$non_test"
  fi
done > /tmp/tdd-author-raw.txt
```

### 5.2 聚合输出

```bash
python3 <<'PY'
from collections import defaultdict
stats = defaultdict(lambda: {"fix_t":0, "fix_nt":0, "feat_t":0, "feat_nt":0, "prod":0, "test":0})
with open("/tmp/tdd-author-raw.txt") as f:
    for line in f:
        parts = line.rstrip().split("|")
        if len(parts) != 4: continue
        author, ctype, test_c, prod_c = parts
        test_c = int(test_c); prod_c = int(prod_c)
        stats[author]["test"] += test_c
        stats[author]["prod"] += prod_c
        if ctype == "fix":
            stats[author]["fix_t"] += 1
            if test_c == 0: stats[author]["fix_nt"] += 1
        elif ctype == "feat":
            stats[author]["feat_t"] += 1
            if test_c == 0: stats[author]["feat_nt"] += 1

rows = []
for author, s in stats.items():
    total = s["fix_t"] + s["feat_t"]
    if total < 3: continue
    no_test = s["fix_nt"] + s["feat_nt"]
    pct = 100.0 * no_test / total
    rows.append((author, total, s["fix_t"], s["fix_nt"], s["feat_t"], s["feat_nt"], pct, s["prod"], s["test"]))

rows.sort(key=lambda r: -r[6])
print(f"{'Author':<28} {'Cmt':>4} {'Fix/NT':>7} {'Feat/NT':>8} {'NoT%':>5} {'P/T':>8}")
print("-" * 70)
for r in rows:
    a, t, ft, fnt, et, ent, pct, p, tt = r
    print(f"{a[:26]:<28} {t:>4} {ft:>3}/{fnt:<3} {et:>3}/{ent:<3} {pct:>4.0f}% {p:>3}/{tt:<3}")
PY
```

### 5.3 per-author 解读三栏

对每个 author 给 **✅ 好习惯 / ❌ 坏习惯 / 🎯 优化建议**：

好习惯举例（不要只报负面）：
- 🟢 产出独立 test: commit（至少写测试，即便 test-after）
- 🟢 测试/生产文件比高（如 > 0.3）
- 🟢 大 refactor 敢补 contract test
- 🟢 业务 fix 同 commit 含测试

坏习惯举例：
- 🔴 全 migration 0 回归测试（像 zhangjinbo 75% pattern）
- 🔴 crash/state bug fix 不含测试（违反 Bug Fix Rule）
- 🔴 0 个独立 test commit（只做 test-after 或根本不写）
- 🔴 测试/生产比极低（< 0.1）

优化建议要**具体且可执行**：
- 不是"多写测试"
- 而是"crash/state bug fix 强制同 commit 补测试，refactor/infra 可灵活"
- 或"下次 bug 修复先 commit 一个 red 测试 → next commit fix，红绿节律演练"
- 或"牵头写 docs/engineering/tdd-practice.md，把你的 10 个 test commit 的模式整理成团队示范"

---

## 5.5 分层 TDD 习惯 — 特别关注 L3/L4（**节约集成调试时间的关键**）

### 为什么 L3/L4 TDD 比 L1 TDD 更重要（而团队最常忽略）

大多数人提到 TDD 默认是 L1 单元 TDD，但**真正节约时间的是 L3/L4 TDD**：

- **L1 TDD 收益**：逻辑正确性 + 重构安全，时间节约是线性的（避免重写 1 个 function）
- **L3 TDD 收益**：**端到端路径早发现集成问题**。不写 L3-first 的常见代价：
  - App 端 Semantics locator 写完了，才发现后端 API 返回字段名是 `bird_name` 不是 `species_name`
  - Hub 页面实现完了，才发现后端 og 接口漏了 `og:image` 字段
  - Admin 审核流程写完了，才发现后端没有 resolveReport endpoint（§1235 类）
  - 这类 bug 到集成测试时才发现，每个至少 2-4 小时排查 + 跨人协调
- **L4 TDD 收益**：**AC 早锁定**。不写 L4-first 的代价：
  - 实现到一半才发现 AC 不清晰（"feed 移除" 在 reportLogic 里到底怎么实现 §63/§862）
  - 发布时才发现 L4 验收标准是新的（扯皮 + 返工）

### 5.5.1 L3-App / L3-Admin TDD 节律诊断

**查证 "有没有 L3 test-first commit"**：

```bash
# L3 独立 test commit（Playwright / integration test）
git log --oneline -500 --all | grep -iE "^[a-f0-9]+ test.*(e2e|l3|playwright|scenario|ac-)"

# 每作者写过多少 L3 test commit
git log --oneline -500 | grep -iE "^[a-f0-9]+ test" | awk '{print $1}' | while read sha; do
  files=$(git diff-tree --no-commit-id --name-only -r "$sha")
  if echo "$files" | grep -qE "l3-(api|browser)|e2e/|tests/.*\.spec\.|\.test\.ts"; then
    author=$(git log -1 --pretty='%an' "$sha")
    msg=$(git log -1 --pretty=%s "$sha" | cut -c1-70)
    echo "$sha  $author  $msg"
  fi
done | head -20
```

**典型诊断结果**（naturehood 实证）：
- 10 个独立 `test(timeline):` commit 里 **0 个是 L3 Playwright 测试**，全是 L1 widget test 和 L2 service impl test
- 即**团队 L3 TDD 完全没实践** —— 所有 Playwright 测试都是功能实现后事后补，不是先写 flow 驱动实现
- 这解释了为什么 §2 `l3-browser/` 目录 90% 是 API 测试冒充 UI 测试 —— 大家先写功能再找"能跑的测试"对齐

### 5.5.2 AC → L3 test 回溯检查

每个 User Story AC 对应的 L3 test 应该**早于 AC 实现的 commit**出现：

```bash
# 找一个 US AC 的测试文件（从 scenarios 文档）
US=US-EX-04
TEST_FILE=$(grep -rl "$US" admin/tests/ server/internal/handler/ | head -1)

# 看该测试文件首次 commit 时间 vs 对应产品代码实现时间
git log --diff-filter=A --format="%ai %s" -- "$TEST_FILE" | tail -1
# 对比：实现 $US 的代码 commit 时间
git log --all --oneline --grep="$US" | tail -1
```

**理想**：测试 commit 时间 ≤ 产品代码 commit 时间（test-first）
**现实**：大多数项目测试 commit 晚于代码 commit（test-after）

### 5.5.3 "集成调试时间" 成本量化

**L3 test-after vs test-first 的时间差**：

| 阶段 | test-after | test-first | 差 |
|:---|:---|:---|:---|
| 设计 | 凭空想 API 字段 | 写 expected request/response JSON | +30min |
| 实现 | 各层并行（后端 + 前端） | 后端先出，前端按 test 驱动 | -- |
| 集成调试 | **发现字段不匹配后返工 2-4h/bug** | 已被 test 拦截 | **-2-4h** |
| 发布前 | 手工点击验证 AC | Playwright AC 测试自动跑 | -1h |
| 后续维护 | 改字段 breaking change 全栈重新测 | contract test 直接 red | -1-3h/次 |

**一个 feature 如果有 5-10 个字段 / 3 个跨服务集成点，L3 test-first 可节约 10-30 小时/feature**。

这是团队推进 TDD 的**最大 ROI 论据** —— 比 L1 TDD 收益大 3-10 倍。

### 5.5.4 L3/L4 TDD 实践 checklist

**✅ 健康 L3/L4 TDD 的团队特征**：
- 新 US AC 开工前先出 Playwright test skeleton（describe / it 结构 + 期望 selector + 期望 API response），CI allow_failure 提交 red commit
- 后端先实现，让 API contract test（L2-1）绿
- 前端实现，让 Playwright（L3-App）绿
- 整个 US 的 L3 test 交由 MR Pipeline / 合并门禁验证；Code Review 不重复判断 Pipeline 颜色
- L4 是 AC 的人工对照（如飞书 Card 视觉还原），发布前必过

**❌ 不健康的信号**：
- Playwright 测试都是功能上线后"补齐"的
- test-after 的 L3 测试多用 `expect(locator).toBeVisible()` 浅断言而非 AC 行为断言
- scenarios/**.md 声称的 L3 测试文件不存在（孤儿引用，见 testing-coverage-review.md §1.1）
- L4 UAT 只有模板无实例（规则 vs 现实漂移）

### 5.5.5 如何推动团队迁移到 L3-first

**不要一次性要求所有人做 L3-first**。阶梯式：

1. **Week 1-2**：只要求**新 feature** 的 US-level L3 test 先出 red commit
2. **Week 3-4**：bug fix 涉及集成层面的（如 API breaking change），先出 L3 repro commit
3. **Week 5+**：老 feature 的 L3 补齐（集体欠债逐个 ticket）

**团队内选一个"先锋"（通常是 TDD 习惯最好的人）牵头跑 1-2 个示范**，其他人对照学。

---

## 6. 集体欠债诊断

### 6.1 哪些模块整个团队都没在 TDD

```bash
# 找整个目录 0 测试的 L1 欠债
for dir in server/internal/logic/*/; do
  n=$(find "$dir" -name "*Logic.go" ! -name "*_test.go" | wc -l)
  t=$(find "$dir" -name "*_test.go" | wc -l)
  if [ "$n" -gt 5 ] && [ "$t" -eq 0 ]; then
    echo "$dir $n logic files / 0 test"
  fi
done
```

### 6.2 哪些路径 TDD 文档 vs 落地漂移

```bash
# docs 中提到 TDD / test-first / failing test 的次数
grep -rn -i "tdd\|test.first\|failing.test\|red.*green.*refactor" docs/ | wc -l

# 是否存在权威 TDD 规范
ls docs/engineering/tdd-practice.md 2>&1
ls docs/testing/tdd-practice.md 2>&1
```

如果 docs 提到 TDD 几十处**但 `docs/engineering/tdd-practice.md` 不存在**，说明 TDD 停留在口号层。

---

## 7. /loop 推进模式（适合深入 TDD 调查）

TDD 充分性审查一次性做不完，建议用 `/loop 20m` 节奏分轮推进：

- Round 1：收集 git log 数据 + per-author 聚合
- Round 2：抽查 top 3 no-test author 的具体 commit 清单
- Round 3：按模块找"整目录 0 测试"欠债
- Round 4：抽查 10 个 "绿测试" 看是否 placebo
- Round 5：整理补救优先级（sub-issue）

---

## 8. 产出物建议

### 8.1 独立的 TDD sub-issue

建议专门建一个 sub-issue：
- 标题：`[review] TDD 充分性 — per-author 报告 + 集体欠债`
- labels：按 [GitLab Label 治理规范](../../../docs/standards/gitlab-label-governance.md) 使用 `type::maintenance`、`priority::p1`、`status::ready`、`area/testing`
- 描述内容：
  - 团队整体数据表（近 500 commit NoTest%）
  - 每人 ✅ 好习惯 / ❌ 坏习惯 / 🎯 优化建议
  - 集体欠债（admin L1 空 / 0 red commit / TDD 文档缺）
  - Definition of Done（人员改进 + 结构性补救）

### 8.2 Bug Fix Rule 强制 CI

- MR template 加 "本 fix 新增测试" 勾选项
- CI 在 `fix:` commit 上检查 test 文件变更，非 CI/build/infra 类必含
- 明确豁免标签：`skip-test-check: <reason>`

### 8.3 红绿 commit 范式

推广团队用范式：
- `test(xxx): red reproducing Y` — 失败测试的独立 commit（CI 可 allow_failure）
- `feat(xxx): green Y` 或 `fix(xxx): green Y` — 让 test 变绿的实现
- `refactor(xxx): ...` — 不改行为只改结构

---

## 9. 反模式 / 避坑

### ❌ "我们有 80% 覆盖率所以我们 TDD 做得好"
覆盖率 ≠ TDD。覆盖率高可能是 test-after 补出来的，没有 red → green 节律。**看 commit 历史才知道是不是 TDD**。

### ❌ 只看 fix commit，不看 feat commit
feat 类也应遵循 TDD（新功能先写 AC 测试）。只审 fix 漏掉 feat 的坏习惯。

### ❌ Per-author 数据作为追责工具
数据的目的是找到改进点，不是考核排名。**refactor / infra / config fix 不含测试是合理的**——把这些从分母里去掉后再看。

### ❌ 忽略个人情境
有人可能负责整个模块的 E2E 测试（那他 fix 不带 test 反而正常），有人可能是 fix-only 角色。**看具体 commit 内容才能判断**。

### ❌ 不写文档直接要求团队"TDD"
CLAUDE.md 写了 Bug Fix Rule 只是规则，**没有 `docs/engineering/tdd-practice.md` 就没人知道怎么做**。这是最常见的"规则 vs 实践"断层。

---

## 10. 完整 Checklist

### 数据收集
- [ ] `fix:` commit 近 500 的 test=0 占比
- [ ] `feat:` commit 近 500 的 test=0 占比
- [ ] 独立 test: commit 数量 + 作者分布
- [ ] 各层 L1/L2-1/L2-2/L3-App/L3-Admin/L4 测试文件数量
- [ ] per-author `NoTest%` + 测试/生产文件比 + 独立 test commit 数

### 诊断
- [ ] 是否有 "red commit" 模式（独立失败测试 commit）
- [ ] 哪些模块整个团队 0 测试（L1 集体欠债）
- [ ] `docs/engineering/tdd-practice.md` 是否存在
- [ ] CI 是否 enforce Bug Fix Rule

### 输出
- [ ] per-author ✅/❌/🎯 三栏分析
- [ ] 集体欠债清单
- [ ] 补救优先级（p0/p1/p2）
- [ ] 独立 sub-issue 或 report

### 补救推动
- [ ] 团队公开 review 一次
- [ ] 每个 author 4 周内写 1 个红绿对照 MR 作为实践证据
- [ ] `docs/engineering/tdd-practice.md` 权威规范
- [ ] CI `fix:` commit 含 test 检查
- [ ] mutation testing pilot 验证 placebo

---

## 11. 实战案例索引

在 naturehood 项目 2026-04-24 做过一次完整 TDD 充分性审查，可参考产出的：

- `sub-issue #35` [TDD 充分性 — per-author 报告 + admin 模块集体欠债](https://gitlab.addx.ai/applications/naturehood/-/issues/35)
  - 7 人 NoTest% 分布：38%（caoxh） ~ 75%（zhangjinbo）
  - 集体欠债：admin L1 9 TS/TSX + 16 logic 全空 / 0 red commit 模式 / docs 29 处提 TDD 但无 practice 文档
  - 反面典型：reportLogic US-EX-04 AC 未实现但测试"绿"（placebo test）
