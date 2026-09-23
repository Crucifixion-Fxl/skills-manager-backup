# 跨系统 variantKey 边界字符串参考

## 1. 适用场景

任何后台都会面对这样一个分裂：**内容**住在 admin / CMS，**实验与灰度规则**住在 GrowthBook / Optimizely / Dittofeed / 自研分流器。运行时的标准问法永远是「这个用户该走哪个 variant」，拿到答案之后再回 admin 去查对应内容。

每一个开了 A/B 的触点、SmartPopup、push campaign、付费墙皮肤都逃不掉这个问题。问题也永远是同一个：**admin DB 行**与**实验系统 variation**之间，让哪一个字符串做边界 token？

## 2. 三种候选方案，只有一种活得下来

| 候选 | 长什么样 | 为什么失败 |
|------|---------|-----------|
| **admin DB 主键**（BigInt / UUID） | `variant_id = 47294` | 扛不住删除＋重建。运营归档了旧 variant 再新建同名的，PK 会翻一轮，实验系统还指向那条死 id。而且 GrowthBook UI 里看到一串数字，运营根本认不出来自己在路由哪个 variant。 |
| **完整 JSON content blob**（把 cmsSlug + actionConfig 序列化进 GB 的 value） | `value = {"cmsSlug":"banner_v1","actionType":"OPEN_PAYWALL",...}` | GB 的 variation 编辑框是单行 text input，塞结构化内容根本看不了。discriminated-union 校验和 CMS catalog 检查在 GB 里无处安放。snapshot 的原子性被破坏，任何内容改动都要走 GB API write。 |
| **人类可读 slug**（如 `summer_banner_v2`） | `variantKey: "treatment_a"`，两边存的是同一个字符串 | 能扛删除（运营可以用同名 slug 重建）。GB UI 里一眼能读。运行时按字符串相等匹配，零歧义。内容依旧留在 admin 里，校验、预览、catalog 都有家可归。 |

结论只有一种：**variantKey 是一根由 admin 拥有的、snake_case 人类可读字符串**。

## 3. 规则

admin 校验正则（`^[a-z0-9_]{1,64}$`），强制 `UNIQUE(touchpointId, variantKey)` per dimension，一经创建不可改。GB 那边原样存同一根字符串作为 variation 的 `value`。

## 4. 「admin 拥有」具体意味着什么

- 运营先在 admin 里创建 variant（content-first 是 80% 以上团队的真实工作方式——创意永远先于实验配线）。admin 按 display name 自动建议一个默认值（`Summer promo` → `summer_promo`），但允许运营覆盖。
- admin 不要做成「从 GB 拉 variantKey 下拉」这种严格 dropdown——那样会逼运营走 GB-first，content-first 团队立刻失血。**推荐 combobox**：GB 当前 variation value 作为建议项显示（做一个 sanity check），但允许自由输入；当 GB 那边还没有这个 key 时显示一条 warning 而不是报错。
- admin 的每一行 variant 旁必须挂一个**复制按钮**，吐出那根字符串；再加一个「全部复制」按钮吐 `ctrl, treatment_a, treatment_b` 方便粘到 GB。永远不要复制 JSON——这个边界 token 本质就是一根字符串。
- GB 是消费方。运行时 SDK 调 `getFeatureValue("<slug>_<dimension>")` 拿到 variantKey，再用这根字符串去 admin 查内容。

## 5. Content-first vs Experiment-first 决策流

绝大多数 ops console 的默认路径应该是 content-first。原因是创意的沉没成本远大于实验配线：运营花一下午做好 banner 素材，结果因为 GB 那边 experiment 还没开而无法录入 admin，就等于白干。

```
                    ┌─────────────────────────┐
                    │ 运营决定新加一个 variant │
                    └───────────┬─────────────┘
                                │
                    ┌───────────┴─────────────┐
                    │ 手头是哪一份先到位的？  │
                    └───────────┬─────────────┘
                                │
                ┌───────────────┴───────────────┐
                │                               │
         创意素材先到位                   实验方案先定
         (content-first, 默认)           (experiment-first, 少见)
                │                               │
                ▼                               ▼
   ┌─────────────────────────┐   ┌───────────────────────────────┐
   │ admin 里创建 variant    │   │ 运营在 GB 里先手配一个        │
   │ 按 display name 自动    │   │ variation value，比如          │
   │ 建议 variantKey         │   │ `treatment_a`                  │
   │   ↓                     │   │   ↓                            │
   │ 运营确认或覆盖 key      │   │ admin combobox 显示这个 key    │
   │   ↓                     │   │ 作为建议项                     │
   │ admin DB 写入内容       │   │   ↓                            │
   │   ↓                     │   │ 运营在 admin 里创建同名        │
   │ GB 那边可能还没这根 key │   │ variant、补上内容               │
   │ → drift 面板 WARN       │   │   ↓                            │
   │ → 不阻塞编辑            │   │ 两边 key 对齐                   │
   │   ↓                     │   └───────────────────────────────┘
   │ 等 GB 那边补 rule       │
   └─────────────────────────┘
                │                               │
                └───────────────┬───────────────┘
                                │
                                ▼
                ┌─────────────────────────────┐
                │ Publish 前 detectDrift 统一 │
                │ 校验两边是否对齐            │
                │ missingContent → BLOCK       │
                │ orphanVariants → WARN        │
                └─────────────────────────────┘
```

流程图的两条输出都一样：**admin 永远负责内容，GB 永远负责路由**，detectDrift 负责在 publish 前把两边对齐。content-first 为默认路径是设计选择——不是技术限制。

## 6. Drift detection 做对了是什么样子

既然 admin 和 GB 各自独立编辑，一个纯边界函数就足够把三种失败模式挑出来：

```ts
detectDrift(variants, gbFeature) → {
  ok: boolean;
  missingContent: string[];  // GB 路由到了这根 key，admin 却没有对应内容 → BLOCK publish
  orphanVariants: string[];  // admin 有内容，GB 没有引用 → WARNING
  gbUnreachable: boolean;    // GB API 挂了 → BLOCK publish
}
```

- `missingContent` 必须 block。快照上线后 GB 把用户路由到一根没有内容的 key，运行时就是一片白屏。
- `orphanVariants` 是 soft warning。这几行 DB 数据只是吃点空间，归档即可。
- `gbUnreachable` 必须 block。对着未知的 GB 状态发布比等一会儿更糟。

**这是整个模型里唯一的硬跨系统 gate**。combobox、校验、复制按钮都只是 UX 润滑剂；真正的 invariant 就活在这个函数以及 `POST /publish-orders` 的 preflight 里，preflight 在 drift 出现时以 `409 DRIFT_DETECTED` 拒绝发布。

详细的状态机用法见 [publish-flow-state-machine.md](publish-flow-state-machine.md)。

## 7. PATCH 全量替换陷阱

GrowthBook 的 `PATCH /api/v1/features/{id}`（以及 scene 新建会用到的 `POST /api/v1/features/{id}`）**不是 JSON Merge Patch，而是整份替换**。只写一个字段就提交，其他字段全会被抹掉。

这种看起来人畜无害的代码写下去就是灾难：

```ts
// ❌ 危险 — 会把 operator 在 GB UI 刚加的所有字段全部覆盖为空
await req(`/api/v1/features/${id}`, {
  method: "POST",
  body: JSON.stringify({
    defaultValue: "treatment_a",  // 只想改这一个字段
  }),
});
// 实际后果: description / tags / owner / environmentSettings 一起丢
```

正解是强制 `GET → 本地 merge → 整份 POST`：

```ts
// ✅ 安全 — 先 GET 保留所有字段，只覆盖想改的那个
const current = await getFeature(id);
if (!current) throw new Error("feature missing");
await req(`/api/v1/features/${id}`, {
  method: "POST",
  body: JSON.stringify({
    ...current,                // 保留 operator 改过的其他字段
    defaultValue: "treatment_a",
  }),
});
```

这也是为什么「admin 写回 GB」必须受限于很窄的一批字段，而不是放开让 admin 成为 GB 的通用编辑 UI。详见 [growthbook-integration.md](growthbook-integration.md) §3。

## 8. 为什么不让 admin 自动创建 GB feature

看起来很诱人，但边界应该这样切：

- **admin 拥有内容。** DB 行、编辑器、预览、catalog 检查。
- **GB 拥有灰度。** Targeting 规则、百分比、实验数学、谁在什么时候改了规则的审计。

让 admin 对 GB API write-through（每次 touchpoint POST 就建 feature、每次 variant create 就 patch variations）会把一个干净的两系统模型变成双向同步，然后问题就来了：admin save 时 GB 挂了怎么办？事务边界在哪？怎么回滚？

**折中方案**：admin 只在 touchpoint 创建那一刻调 GB 做一次 feature skeleton 的 seed——建 feature + 一个 `ctrl` variation + 默认 rule——让运营打开 GB 就能搜到 feature，省掉「这个 feature 要去哪建」的困惑。这一步之后，admin 对 rollout 规则保持只读。

## 9. 让 GB 那边的 variationKey 更可读

跑完一轮之后，投诉频次第一的永远是：「运营在 GB 里看到 `treatment_a`，根本不知道这是个啥」。两个轻量对策：

1. **用命名规范，不用 UUID。** 推广 `summer_banner_v2`、`pw_annual_cta` 这类 slug，别用 `treatment_a`。admin 新建 variant 的表单按 display name 建议默认值，运营可以手改。
2. **把人话描述同步给 GB。** admin 保存时，PATCH GB feature 的 `description` 字段或 experiment variation 的 `description`，让 `treatment_a` 旁边有「夏季大促 banner · 跳年卡」这样的提示。写入面最小（只写 description，不写 rollout），风险最小。建议在核心 drift-detection 稳定后再上。

## 10. 反向导航：从简到繁的四层升级

运营频繁在 admin 和 GB 之间切，一键跳转是刚需。不要一上来就做最贵的方案，按以下升级序：

1. **admin → GB deep-link（零成本）。** 每行 variant 挂一个指向 `<GB_UI_BASE>/features/<feature_id>` 的链接。环境变量 + 字符串拼接就够。
2. **GB → admin bookmarklet（20 行 JS）。** 把 `javascript:` URL 拖进书签栏。在 GB feature 页点一下，扫出 feature id，打开对应 admin touchpoint。零安装、零维护、跨浏览器。
3. **Tampermonkey 用户脚本。** 在 GB 页面注入一个 "Open in Admin" 按钮，或显示 admin 侧 drift 状态 overlay。需要每个运营装 Tampermonkey + 脚本。
4. **Chrome extension。** MV3、Web Store 审核或内部签名、长期 MV3 breaking change 维护。只在 bookmarklet / 脚本证明不够用时才做（对小于 50 人的运营团队基本永远用不到）。

**不要跳级。** 见过团队一上来就做 extension，结果审核卡两周、没人装的惨剧。Level 0 做到位，再按量化信号（日均反查次数、运营抱怨频次）决定要不要爬。完整四层的 cost / benefit / 升级条件对照见 [growthbook-integration.md](growthbook-integration.md) §9。

## 11. 一句话总结

- 跨越 admin ↔ 实验系统 的边界 token 是**一根由 admin 拥有、GB 原样保存的 `variantKey` 字符串**。
- **内容留在 admin，灰度留在 GB，永远不要混。**
- 唯一必须在代码里强制的跨系统 invariant 是 **publish preflight 里的 drift detection**。
- UX 润滑剂（combobox、复制按钮、deep-link、命名规范、GB description 同步）能降低首次做错的概率，但替代不了 preflight 这道硬闸。
