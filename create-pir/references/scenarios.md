# 常见使用场景

> Skill 应该如何响应用户不同的表达方式。用例是给 AI 看的"风格指南"。

## 场景 1：L4 回归批量造数据（最常见）

**用户**："我要跑 L4 回归，帮我造 10 条 PIR 到我的 VicoHome 账号"

**Skill 动作**：
1. 确认当前 `.pir.env` brand / account；若匹配，直接跑
2. prod 写入显式告知："即将向 prod VicoHome 的 `<email>` 账号造 10 条 PIR，设备 `<device>`，确认继续？"
3. 用户确认后：`python ... --count 10 --no-verify`（L4 回归通常不等相册物化，节省时间）
4. 完成后汇报 10 个 trace_id 列表 + 总耗时

**回复模板**：
```
✅ 已向 prod VicoHome (<email>) 造 10 条 PIR，耗时 65 秒，全部成功。
trace_id 列表：
  00hr0IGO0t96Rv8h0LKmWnpc32Yt1
  0zjV0nqu0kJLR6nl0xWRWw6Z3zwW5
  ...
打开 VicoHome App 登录 <email>，进相册可见刚才的 10 条事件。
```

---

## 场景 2：开发调试 UI 需要相册里有数据

**用户**："相册页 UI 改了，我账号是空的，随便造一条 VN 事件"

**Skill 动作**：
- 切 `--brand viconature`
- 不需要 `--count`（默认 1 条）
- 提示用户："需要在 VicoNature App 登录账号才能看到"

---

## 场景 3：后端开发验证 PIR 推送链路

**用户**："我改了后端 PIR 上报接口，帮我造一条看通不通"

**Skill 动作**：
- **默认 staging 而非 prod**（改后端一般先 staging 验证）
- 建议用 `--dry-run` 先验证凭证/domain
- 然后正式跑
- 如果后端改了接口字段，Skill 无法预知；**第一次失败的完整响应要打给用户看**，让他判断是业务逻辑问题还是 Skill 的 payload 过时

---

## 场景 4：造数据前先检查环境能通（L4 准备阶段）

**用户**："L4 前一晚，帮我验证下 prod 环境造 PIR 的链路还通不通"

**Skill 动作**：
- 不写任何事件
- 跑 `--dry-run`（只前 3 步）
- 脚本回："登录 ✅ wakeupDevice ✅ httpToken ✅ 签名对齐"
- Skill 总结："prod VicoHome 链路健康，随时可以 L4 造数据"

---

## 场景 5：造完忘记 trace_id 回头查

**用户**："昨天造的那条 PIR trace_id 是啥，找不到了"

**Skill 动作**：
- 让用户登录对应账号的 App 查相册
- 或用 device-cloud 仓库 `tools/` 下后续可能添加的 `list-pir` 脚本
- 当前 Skill 不保存历史记录；如果需要，告诉维护者考虑加 session log

---

## 场景 6：切新品牌，凭证不在 .env

**用户**："帮我在 KiwiBit prod 造一条"（但 `.pir.env` 里是 VicoHome 账号）

**Skill 动作**：
1. 提醒：当前 `.pir.env` 是 VicoHome 账号，KiwiBit 需要单独凭证
2. 询问：
   - 要临时用 CLI 传：`--email ... --password ... --device ...`
   - 还是保存一份独立 `.pir-kiwibit.env`？
3. 推荐 KB 常用测试账号（见 brands.md 的"已知可用账号"表），由用户确认是否使用

---

## 场景 7：-2112 错误首次遇到

**用户**："跑你这个脚本报 deviceStatus=-2112，怎么回事"

**Skill 动作**：
1. 直接引用 [troubleshooting.md](troubleshooting.md) 的 Step 4 诊断流程
2. 让用户跑那段 JWT 诊断 Python
3. 90% 情况：`tenantId=None` → device_api 走错 → 检查是否被手动覆盖成 `api.addx.live`
4. 修复后重跑

不要让用户"自己 google"或"问后端"，这个错误模式我们**完全已知**。

---

## 场景 8：相册里看不到造的事件

**用户**："trace_id 都有了但相册是空的"

**Skill 动作**：
1. 核对：**登录 App 的账号**和**脚本用的账号**是不是同一个？
2. 如果脚本里的 `adminId`（JWT 里有）和 App 账号 userId 不同 → 事件落在设备主人那里，不在当前 App 账号里
3. 登录正确账号再看

---

## 风格总则

- **短**：回复 ≤ 8 行，除非用户要求详细
- **具体**：报 trace_id 不报"造了一条"
- **明确 next step**：每次都提示"下一步去 App 哪里看"
- **prod 二次确认永不省**：非交互环境下要求用户显式确认

## Skill 不做的

- 不 mock / 不伪造成功（失败就如实说，并给诊断方向）
- 不替用户记凭证（提示存 .env 即可）
- 不自动跨账号 / 跨 brand 补全（参数缺失就 ask，别自己乱填）
