# Change Detection 与 Canonical JSON Diff

本文件沉淀 ops console 里所有"跨时刻比较"的基础能力：用 canonical（sorted-keys、稳定序列化）JSON 做相等判断 + 用"new / synced / draft"三态 badge 把 admin 与外部世界的同步状态显式告诉运营。

这两块能力是四条路径的共同基座：

- **checksum**：发布包指纹，用于 staging → prod 复制验证。
- **publish-diff**：发布前告诉运营 "你这次会带上线哪些变更"。
- **change-status badge**：列表页的 chip，告诉运营 "这条我改过、这条跟线上一致、这条是新的"。
- **drift detection**：发布 preflight 里校验 admin 本地态与外部系统（GB / CMS / 上一次 prod snapshot）是否已经漂移。

真实样例见 `/home/jchen/customer-care/admin/src/lib/bundle-diff.ts`、`change-status.ts`、`checksum.ts`、`prod-snapshot.ts`。

## 1. 为什么字符串比较不行

最容易想到的相等判断是 `JSON.stringify(a) === JSON.stringify(b)`。它的问题：

- **对象 key 顺序不稳定**。`{a:1, b:2}` 和 `{b:2, a:1}` 字面量不同，但语义等价；ORM / Prisma / 前端 form lib 会按插入顺序 serialize，两次编辑同一份内容的 serialization 结果可能不一样。
- **`undefined` vs missing key**。`{a:1, b:undefined}` 和 `{a:1}` stringify 后分别是 `{"a":1}` 和 `{"a":1}`（巧合一致），但 `{a:1, b:undefined, c:2}` 和 `{a:1, c:2, b:undefined}` 则因为 key 顺序不同 stringify 不同。
- **数字规范化**。`1.0` 和 `1` 在 JavaScript 里相等，`JSON.stringify(1.0)` 返回 `"1"`。但来自 MySQL `DECIMAL(10,2)` 的字段可能回 `"1.00"`，两者就不等。
- **空字段差异**。`cms_id: ""` 和 `cms_id: null` 和 cms_id 字段缺省，三者在业务上可能是同一个"没配置"，但字符串形态完全不同。

任何一个这里失配都会触发**假 diff** —— 列表页上 "我什么都没改，这条怎么又变 draft 了"，运营会失去对 badge 的信任。

## 2. Canonical JSON 规范

最小规范（customer-care 实现见 `bundle-diff.ts:35-46`、`change-status.ts:53-67`）：

```ts
function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)   // 关键：跳过 undefined
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, v]) => [k, sortKeys(v)]);
    return Object.fromEntries(entries);
  }
  return value;
}
```

### 2.1 递归 sort keys

每一层 object 都要按字典序排列。**只 sort 顶层是不够的** —— fact 对象里嵌套 `where` / `params` / `source` 这些子 object，如果不递归，ORM 序列化顺序差异仍然触发假 diff。

### 2.2 数组顺序要不要保留

**默认保留源数组顺序**（见 customer-care 的 `sortKeys(value).map(sortKeys)`）。判据：

- 如果数组有**业务语义的顺序**（fatigues 按时间轴顺序 evaluate、规则 rules 从上到下短路匹配），必须保留。
- 如果数组只是**无序集合**（tag 列表、ID 列表），则推荐进入 canonical 前先 sort 一遍。

customer-care 的所有数组都按第一类处理 —— `fatigues[]` 的顺序有 evaluation 语义，`recipes[]` 的顺序决定 match 优先级。engagement 的 `FatigueVariant.phases[]` 同理。

### 2.3 `undefined` vs `null` vs absent

三者必须统一成**同一种规范形式**，否则 form 默认值、DB nullable 字段、TypeScript optional field 的组合会产生叉路：

- 推荐：跳过 `undefined`（canonical 输出里不出现该 key）。
- `null` 保留 —— `null` 是"明确表示无"，和"没填"语义不同。
- 方法：`.filter(([, v]) => v !== undefined)` 在 sort 之后 map 之前。

### 2.4 数字规范化

如果 DB 里有 `DECIMAL` / `NUMERIC` 字段，读出来可能是 string `"1.00"`；JavaScript 的 number 是 `1`。**必须在写入 canonical 前 normalize** —— 要么统一成 string，要么统一成 number。

customer-care 目前没有 DECIMAL 场景所以没处理；engagement 的 `max_exposures`、`cooldown_seconds` 都是 int，`JSON.stringify` 直接用原生 number 不失真。如果将来引入 DECIMAL fatigue 参数，必须在 `toFactShape()` / `toRecipeShape()` 里 `Number(value)` 一次。

## 3. Checksum 稳定性保证

checksum = SHA-256 over canonical string。用途：

- **staging → prod 复制验证**：staging 上传的 rules.json 和 prod 接收到的 rules.json 必须 checksum 一致，否则说明中途被篡改。
- **Go backend 下载后自检**：customer-care 的 backend download rules.json 后会重算 checksum 验证。
- **publish 前的 preflight**：如果本地 canonical hash == 最后一次 prod snapshot hash，说明 "这次发布会是个 no-op"，admin 应阻止提交。

### 3.1 必须用 canonical 作为输入

```ts
export function computeSnapshotChecksum(snapshot: RulesConfig): string {
  const bytes = JSON.stringify(snapshot);  // customer-care 现状
  return createHash("sha256").update(bytes).digest("hex");
}
```

customer-care 当前实现的 checksum 并**没有**走 canonical sort —— 它依赖 `snapshot-assembler.ts` 在装配时就按固定顺序构造 object。如果 assembler 任何时候顺序不稳定，checksum 就会抖动。**更健壮的做法是 checksum 输入明确经过 `canonicalJson()`**，把"稳定性保证"收敛到一个地方。

### 3.2 调试技巧："两份内容长得一样但 checksum 不同"

当运营报 "我看这两份一样啊为什么 publish 又触发了"：

1. 拿到两份 snapshot 的 raw JSON，分别跑 `canonicalJson()`。
2. `diff <(echo $a) <(echo $b)` 看字符级差异。
3. 常见元凶：
   - 新增了可选字段（`description: ""` vs `description` 不存在）。
   - 数字 `1` vs `"1"`（来自不同表 / 不同 ORM）。
   - 嵌套数组顺序被某处 sort 过（例如 frontend 的 Formik 按 alphabet reorder）。
   - 时间戳 / id / `created_at` 被误塞进了 canonical 输入。

### 3.3 什么字段绝对不能进 checksum 输入

- `id` / 数据库主键 —— 换库换表就变。
- `created_at` / `updated_at` —— 每次 save 就变。
- `owner_email` 类元数据 —— 运营查看不影响运行时行为。

customer-care 的 `toFactShape` / `toSceneShape` / `toRecipeShape` 专门做了这一层投影：只把**运行时真正消费的字段**丢进 canonical，其他管理字段 (`created_at`、`lastEditedBy`) 全部剔除。engagement 做 snapshot assembler 时也应该遵循这个规矩。

## 4. 三态 badge 模型：new / synced / draft

三态是**最小有用集合**。少于三个不够，多于三个没人能记住。

| badge | 语义 | 计算方式 |
|-------|------|---------|
| `new` | admin 有，外部（上一次 prod snapshot / GB / CMS）没有 | 外部查不到对应 key / id |
| `synced` | admin 与外部 canonical hash 一致 | `canonical(admin) === canonical(external)` |
| `draft` | admin 有、外部有，但 canonical hash 不同（本地未发布的改动）| 二者都存在且 canonical 不等 |

### 4.1 计算方法

```ts
export function computeChangeStatus(
  local: LocalRow,
  externalSnapshot: RulesConfig | null,
): ChangeStatus {
  const external = lookupInSnapshot(externalSnapshot, local.key);
  if (!external) return "new";
  return canonical(toShape(local)) === canonical(external) ? "synced" : "draft";
}
```

customer-care 分别对 fact / scene / recipe 实现了三个 compute 函数：

- `computeFactStatus(fact, prodSnapshot)`：fact 自包含，直接比。
- `computeSceneStatus(scene, prodSnapshot)`：scene 层级比较，**故意不看 recipes 数组** —— 每条 recipe 有自己的 badge。
- `computeRecipeStatus(recipe, prodSnapshot)`：定位到 scene 下的 recipe by recipe_id 再比。

### 4.2 scope 的选择

badge 的 scope 决定 canonical 输入要包不包含子集合：

- **fact-level**：fact 自己的所有字段。
- **scene-level**：scene 的元数据（entry_event / description / owner），**不包括** recipes。理由：scene 自身改了 vs scene 下某条 recipe 改了是两件事，运营需要分别显示。
- **recipe-level**：recipe 自己的所有字段（condition / fatigues / cms_id）。

engagement 对应的切分：

- touchpoint-level badge 不包括 variants。
- 每个 variant 有独立 badge。
- condition 变化触发的是 touchpoint-level 还是 variant-level badge，由 condition 挂在哪一层决定。

### 4.3 "draft" 判据的假象路径

**反模式**：用 `updated_at > last_publish.created_at` 判 draft。

- 这会把 "run migration 重写了 updated_at" 或 "admin 升级后触发 no-op save" 全部误判成 draft。
- 运营会在列表页看到一堆自己没改过的 draft，然后失去对 badge 的信任，进一步忽略真正的 draft。

**正确做法**：永远以 canonical hash 为准。`updated_at` 只用于排序 / 显示"最近编辑时间"，不参与业务判断。

## 5. 如何计算 draft：与 snapshot 的关系

draft 的对照物是"最后一次 prod snapshot"：

```ts
const prodSnapshot = await loadLatestProdSnapshot();  // 从 rules_package(env=prod, status=active) 读
rows.forEach(row => row.status = computeChangeStatus(row, prodSnapshot));
```

几个细节：

- **有没有 staging snapshot 不影响 badge**。draft 的对照物总是 prod —— staging 是"未完成的 prod"，运营关心的是 "我的这条改动距离 prod 还有多远"。
- **首次发布前** `prodSnapshot === null`，所有 row 都是 `new`。
- **snapshot 是个完整 bundle**，每次发布重新生成；可能包含几百条 fact / scene / recipe。admin 一次性 load 再在内存里做 lookup（Map<key, config>），不要对每条 row 都走一次 DB query。
- **snapshot 写入对象存储** 时要带 checksum + 版本号；列表页 load 时优先用最近的 active package 的 `rules_json` 字段，不要去 S3 下载（慢）。

## 6. UI 呈现

### 6.1 badge 的渲染

每一行渲染一个 chip：

- `new` — 蓝色 / "New"
- `synced` — 灰色 / "Synced" 或直接不显示（减少视觉噪声）
- `draft` — 黄色 / "Draft"

draft 最显眼 —— 它意味着"这条要参与下次发布"。

### 6.2 "只看 draft" 筛选

列表页加一个 filter toggle "Only show draft"，是发布前确认范围的高频操作。URL 必须镜像这个 filter（见 `admin-ia-and-flows.md` §URL sync for client-side filters）。

### 6.3 publish 时的打包范围

publish 组装 snapshot 时，**只把 `draft` + `new` 的 row 当作变更**，`synced` 的 row 理论上可以跳过（但实际上还是整包重新 assemble 最稳 —— snapshot 是 bundle 级不可分）。

"发布会带上什么" 的 UI 就是 `filter(row => row.status !== 'synced')` 的列表预览。

## 7. 反模式

### 7.1 用 `updated_at > snapshot.created_at` 作为 draft 判据

已经在 §4.3 展开。误判率 > 20%，最终运营会忽略 badge。

### 7.2 checksum 输入包含非稳定字段

- `created_at` / `id` 进入 canonical 输入，每次查询都出新 checksum。
- `owner` / `last_edited_by` 这些元数据进入输入，换个人改就触发 diff。
- 推荐：写一个明确的 "toShape(row)" 函数作为唯一入口，runtime 消费什么就进什么。

### 7.3 checksum 和 change-status 用不同的 canonical 实现

- `bundle-diff.ts` 用 replacer-based sort，`change-status.ts` 用递归 map-based sort，两者在嵌套 object 上行为略有差异。
- 结果：发布 diff 认为两个对象不同，draft badge 认为一致，或反之 —— 用户投诉 "明明显示 synced 为什么 publish 还有 diff"。
- 正确做法：`canonicalJson()` 函数只留一份，所有 checksum / diff / badge 都调它。

### 7.4 Sync state 只看 admin 本地 checksum，不看外部系统真实态

- 只看 "本地 checksum vs 上次发布的 checksum" 回答 "有没有 draft"。
- 但 GB / CMS 这种外部系统可能被其他人在 admin 之外改过（直接进 GB UI 改 feature rule）。
- 此时 admin 里的 synced 是谎言 —— 本地和 "上次发布" 一致，但外部系统已经漂走了。
- 正确做法：drift detection 放进 publish preflight（`cross-system-variant-key.md` §drift detection），单独探测外部系统 vs 本地的 canonical 差异。三态 badge 和 drift detection 是两层，不要混。

---

## 延伸阅读

- `publish-flow-state-machine.md` — snapshot 装配与 checksum 使用的上下文
- `cross-system-variant-key.md` — drift detection 在 publish preflight 里怎么接入
- customer-care 源码锚点：`bundle-diff.ts` / `change-status.ts` / `checksum.ts` / `prod-snapshot.ts`
