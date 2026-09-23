---
name: firmware-mgr
description: 固件发版平台（firmware-mgr.addx.live）的查询、发布、撤销、OTA 白名单维护、构建产物（固件/Flash/Output）下载操作手册。当用户询问固件版本/发布记录/构建状态/OTA 白名单/下载等，或需要在该后台执行发布、撤销、灰度推送、白名单读写、固件下载等操作时触发。
---

# firmware-mgr

ADDX 固件发版平台（"固件发版平台"）操作手册。系统是一个 Vue 2 + iview-admin 的内部管理后台，覆盖固件构建、发布、下载、OTA 名单管理和用户管理。

## Description

- 访问入口：<https://firmware-mgr.addx.live>
- 测试环境：<https://firmware-mgr-test.addx.live>
- API base：`https://firmware-mgr.addx.live/api/`（注意 `/api/` 是必需前缀，前端代码中端点名为相对路径）
- 内网访问：DNS 在生产环境会解析到内网 IP，未连接公司网络/VPN 时 API 不可达
- 角色：`admin` / `developer` / `product`，不同菜单按角色可见
- 用途：固件构建、固件发布/撤销/灰度推送、OTA 名单维护、构建产物（固件 / Flash / Output）下载。覆盖几十种 SOC（Hi3518 / T23ZN / T31ZL / T31ZX / T41ZG / GK7202 / FH8626 / AK3918 / RK3566 / EFR32 等系列），具体平台清单见 `get_models` 顶层 value

**适用场景**：固件发布/撤销/工厂发布、OTA 名单查询/维护、设备型号与版本查询、**固件 & Flash & Output 下载**（`download_*` 接口换 S3 预签名 URL，10 分钟有效）、用户权限管理（admin only）。**写操作前必须按 Rule 3 走二次确认。下载流程见 references § 8。**

## Rules

### Rule 1 — 认证

- 所有 API 调用必须带 HTTP header `token: <token>`（**不是** `Authorization: Bearer`）
- ⚠️ **token 必须在 Header 里！光放 Cookie 不行**（实测：纯 `-b "token=..."` 没有 Header 时后端返 `no auth`）。虽然浏览器登录后 token 是存在 Cookie 里（F12 Application 能看到），但前端 JS 调用前会把 Cookie 里的 token 复制到 Request Header（`s["j"]()` 函数），后端**只从 Header 读 token**。用 curl/Postman 复现常见的错误就是只放 Cookie 没放 Header
- token 是 32 位字符（看似 MD5）。失效后会返回 `{"result": 10001, "msg": "no auth", ...}` 或 `{"result": -1, "msg": "KeyError: 'token'."}`

**Token 获取顺序（强制按此顺序，不允许颠倒）**：

1. **优先读环境变量**：生产 = `$FIRMWARE_MGR_TOKEN`，测试 = `$FIRMWARE_MGR_TEST_TOKEN`。AI 必须用 `echo $FIRMWARE_MGR_TOKEN` 之类先尝试读取
2. **环境变量为空**：让用户在对话中提供，**仅在当前会话使用，不写入任何文件**
3. **用户无 token**：引导用户去网页 SSO 登录，**从浏览器 F12 → Application → Storage → Cookies → 选 `https://firmware-mgr.addx.live` → 找名为 `token` 的 cookie → 复制 Value**（最直接，是源头存储）。
   - 备选：F12 → Network 面板 → 任意 `/api/*` 请求的 Request Headers 里复制 `token:` 的值（前端从 Cookie 读后设到 Header，间接但也能拿到）
   - ⚠️ **不要用 `localStorage.getItem('token')`** —— token **不在 localStorage 也不在 vuex/state**，是后端 SSO 登录后通过 Set-Cookie 写到浏览器 Cookie 的（前端 JS `s["j"]()` 函数从 Cookie 读 token 再设到 Header）

**绝对禁止**：

- ❌ 把 token 硬编码到 SKILL.md / references / 任何脚本文件
- ❌ 把 token 写到 git commit / shell history / 日志文件
- ❌ 把 token 作为命令行参数传（会进 history），必须放进环境变量或 stdin
- ❌ 在 Skill 文档中留示例 token（即使是过期的）

curl 调用一律写 `-H "token: $FIRMWARE_MGR_TOKEN"`，不写 `-H "token: 408c..."`。

**写操作前的强制权限体检**：进入 Rule 3 三色清单流程**之前**，AI 必须先调一次 `get_versions`（或 `get_models`）确认 token 状态：

- 返回 `result==0` + 数据 → token 有效，继续
- 返回 `result==10001 / no auth` → token 无效或角色不够，**立即停下让用户换 token，不要走 Rule 3**

⚠️ **不要用 `get_user_info` 做体检** — 实测该接口有 bug，要求 token 必须**同时在 header 和 body 里**，单凭 header 会返回 `KeyError: 'token'.`，会被误判成 token 无效。其他接口都只看 header。

体检的代价只有 1 次只读调用，但能避免"白名单已写入但 publish 时被拒绝"这种半执行状态。

### Rule 2 — 请求格式

- **所有业务接口都用 `POST`**，即使端点名以 `get_` 开头（GET 会返回 405 Method Not Allowed）
- `Content-Type: application/json`
- 请求体永远是 JSON 对象。无参时传 `{}`，不能省略 body
- 响应统一形态：`{"result": <code>, "msg": <string>, "data": <object|array|{}>}`
  - `result == 0`：成功
  - `result == 10001`：未授权 / token 无效 / 当前角色没权限访问该接口
  - `result == -1`：后端错误。`msg` 通常是裸 Python 异常字符串（如 `KeyError: 'region'.`、`pymysql.err.OperationalError: ...`），用来反推缺哪个参数

### Rule 3 — 写操作的二次确认流程（不是禁止，是必须确认）

以下是会改变线上状态的写接口，**允许调用但必须走二次确认**：

| 端点 | 影响范围 |
|------|---------|
| `publish_firmwares` | 发布固件到 region（影响所有该 region 设备的 OTA） |
| `cancel_publish_firmwares` | 撤销已发布的固件 |
| `publish_factory` | 工厂版本发布 |
| `set_ota_whitelist` / `set_ota_blacklist` / `set_ota_debug_whitelist` / `set_ota_device_whitelist` | 改 OTA 名单 |
| `modify_user_access` / `modify_user_active` | 改用户权限/启用状态 |
| `/management/firmware/buildBxVersion` | 触发 BX 版本构建 |

**调用前的强制流程**（不可省略）：

1. **取当前状态**：用对应 `get_*` 接口查现状（发布/撤销前先 `get_publishs` 查当前发布版本；改白名单前先 `get_ota_whitelist` 看当前名单并备份）

2. **结构化 payload 复述（核心规则）**：每个写操作前，把**完整 payload 的每一个字段**列出来，并明确标注三种来源。**不允许跳过任何字段**——AI 替用户做了决定，就必须暴露给用户：

   ```
   准备执行: publish_firmwares
   ============ 字段清单 ============
   ✅ 用户已确认（来自对话明示）:
     region:     cn          ← 你说"cn 区"
     version:    1.19.0      ← 你指定的版本
     platform:   T23ZN-Hi3861L  ← 你说"T23 hisi 平台"
     ota_limit:  "100:1"      ← 你说"发 1%"（已转成比值字符串）

   ⚠️  AI 推断/默认填的（你没明确说，请核对）:
     models:              ["all"]      ← AI 默认"全部子型号"
     firmwareUpgradeType: 1            ← AI 按页面默认值"建议升级"
     useDebug:            false        ← AI 按版本号无 -d/_develop 后缀推断
     minBxVersion:        "0.1.0"      ← AI 用默认值（不能填 null）
     descriptions:        []           ← AI 默认空发布说明

   ❓ 完全未涉及（必须用户明示，不许 AI 猜默认）:
     upgradeVersion:      ???          ← 设备**当前装的来源版本**（非目标版本！）
                                         你必须告诉我设备从哪个版本升上来
     <其他无合理默认的字段也列在这里>

   ============ 当前线上状态 ============
   region=cn / platform=T23ZN-Hi3861L / version=1.19.0 当前发布记录: 无 / [...]

   ============ 影响 ============
   <一句话总结：例如 "将向 cn 区 T23ZN-Hi3861L 全部子型号设备灰度推送 1.19.0，1% 设备会收到，建议升级">

   请逐项确认或修正。如果"⚠️ AI 推断"里有任何一项不对，请明确指出。
   ```

3. **等用户明确确认**：必须明确回复"执行"/"确认"/"发"等肯定词，并且 AI 必须确认用户没有要求修改任一字段。**"嗯"/"好"/"OK"/"行"这种语气词不算确认**，遇到要追问"是确认按以上字段执行吗？"

4. **执行后立即拉取一次现状**：用 `get_*` 验证状态确实变了，把结果给用户看，方便他验证或回滚

**回滚提示**：`publish_firmwares` 配套有 `cancel_publish_firmwares`（注意必须传齐 `region/platform/version/models` 4 个字段才能正确撤销，少了就是假成功）；OTA `set_*` 是覆盖式写入，回滚要靠步骤 1 备份的 `get_*` 结果。

### Rule 4 — 发布的两种模式（核心业务流程）

#### 4.0 发布请求信息收集模板（用户提交需求时必给的信息）

用户发起"帮我发布固件"类需求时，**AI 必须按下表逐项核对**。任何"必填项"缺失都不能开始执行（参考 Rule 3 三色清单的 ❓ 类）：

```
┌─ 【必填 — 缺一不可，缺则向用户追问】
│
│  □ 节点 region:        cn / us / eu          （UI 上叫"节点：中国/美国/欧洲"）
│  □ 平台 platform:      SOC 名（如 T32ZN-Aiw4211L）
│                        ↑ 不知道？让用户报型号，AI 用 get_models 反查
│                        ↑ 注意：get_models 接口偶尔数据滞后，型号可能存在但 children 为空，
│                          以页面"发布管理"弹窗显示为准
│  □ 升级版本 version:   要发的固件版本（如 1.19.171）。**UI 上叫"升级版本"**
│                        ↑ AI 用 get_versions 校验该版本号在系统中存在
│  □ 建议升级版本        从哪个**来源版本**升级过来（如 1.19.1）
│    upgradeVersion:    ⚠️ **不是目标版本**。语义=设备当前装的版本→目标版本(version)
│                        例：发 1.19.171，设备从 1.19.1 升上来 → upgradeVersion=1.19.1
│                        ↑ 必须问用户，不要默认填 = version
│  □ 最低依赖固件        允许升级的最低当前版本（实测可选，不传也行）
│    minBxVersion:      实测不传也能 publish 成功；要传就用字符串 "0.1.0"，**不要传 null**
│  □ 型号 models:        子型号列表，如 ["CG628-BD-A4X"] 或 ["all"]（该平台全部）
│  □ 灰度 ota_limit:     0% / 1% / 5% / 10% / 15% / ... / 100%（按 5% 档位选）
│                        ↑ AI 必须翻译成比值字符串：0%="10000000:1"、1%="100:1"、
│                          5%="20:1"、10%="20:1,2"、...、**100%=空串 ""**（不是 "1:1"！）
│                          ⚠️ "1:1" 在新版后端实际等价 0%（不推送任何非白名单设备），实测 2026-06-17
│                          详见 references § 4.1.1
│  □ 升级方式:           可忽略 / 建议升级 / 强制升级
│                        ↑ 默认"建议升级"，强制升级要慎重确认
│  □ 该版本是否"debug 性质": 是 / 否
│                        ↑ 看发布页选该版本后顶部"debug: 是/否"。**这不是版本号后缀能判断的**，
│                          也**不是** payload 的 useDebug 字段。它决定走哪条白名单路（见下）。
│                          判断方法（按可靠性排序）:
│                          ① 看发布页 UI 顶部"debug: 是/否"（最权威）
│                          ② 查 get_publish_logs 历史: 该 (platform, version) 所有历史 publish
│                             的 useDebug 字段全是 0 → 可能是普通版本；混合或多为 1 → 可能是 debug 性质
│                             ⚠️ 仅作辅助参考，不能单独判定（反例: 1.19.100 历史 useDebug 全是 0
│                             但实际是 debug 性质版本）。结论必须以 ① UI 为准
│                          ③ 看 get_firmwares 是否有同 version 的 -d 变体（不充分，正式版也有 -d 变体）
│                          ④ get_publish_options 直接拿 debug 字段（接口慢/504，不可靠）
│  □ useDebug (payload 字段): 几乎总是 **false**
│                        ↑ 前端逻辑 useDebug=versionString.includes("debug")，普通发布是 false。
│                          决定用 <ver>.fw（false）还是 <ver>-d.fw（true）文件。
│                          即使"该版本是 debug 性质"，useDebug 仍然填 false（用 <ver>.fw）
│
├─ 【白名单灰度模式专属 — ota_limit < 100% 时需要】
│
│  □ 白名单成员清单（按"该版本是否 debug 性质"分流，**不是**按 useDebug 字段）:
│      • 普通版本（发布页 debug: 否）→ 收集 **user_id 列表**（纯数字，如 65127）
│        AI 操作：get_ota_whitelist 备份 → 拼接 → set_ota_whitelist 写入；publish 时 useDebug=false
│      • debug 性质版本（发布页 debug: 是，如 1.19.100）→ 收集**设备长 SN 列表**（32 字符 hex）
│        AI 操作：get_ota_debug_whitelist 备份 → 拼接 → set_ota_debug_whitelist 写入；publish 时 useDebug 仍=false
│        ⚠️ debug 性质版本**不要**往 OTA 帐号白名单加 user_id（那条路对它无效）
│  □ ⚠️ 白名单是 region 分组的：**us 和 eu 共享同一份**（OTA 帐号白名单 + debug 设备白名单都共享），
│      cn 独立。所以给 us 加 user_id = eu 也生效，反之亦然。判断共享：get_ota_whitelist us 和 eu 条数/内容一致
│
├─ 【可选 — 不给则按默认】
│
│  □ 发布说明 descriptions:    默认 [] （空数组合法）
│  □ 分组 group:              默认不选；仅在"对某个异常分组定向修复"时填具体分组名
│
└─ 【最简提交示例】
   "发 1.19.171 到 us，平台 T23ZN-Aiw4211L，型号 CG628-BD-A4X，
    灰度 0% 白名单（user_id: 65127），建议从 1.19.1 升级，正式版"
   ↑ 注意"建议从 1.19.1 升级"对应 upgradeVersion=1.19.1（来源版本）
   ↑ 注意"发 1.19.171"对应 version=1.19.171（目标/发布版本）
```

**AI 处理流程**：

1. 用户给的信息往这个模板上对，**列出"已收集"和"缺失"两类**
2. 缺失的"必填项" → 向用户追问，**不要 AI 自行猜测填默认值**
3. 缺失的"可选项" → AI 用默认值并在三色清单的 ⚠️ 类标注
4. 全部齐了 → 进入 Rule 3 三色清单复述，等"执行"指令

**AI 容易踩的坑**（已实战验证）：

- ❌ 用户说"T23 hisi" → AI 直接选 T23ZN-Hi3861L（其实是 T32ZN-Aiw4211L 这种类似名字）。要让用户确认或补型号反查
- ❌ 用户说"灰度 0%" → AI 传 ota_limit=0（错！要传 "10000000:1"）
- ❌ 用户说"白名单发"但没给 user_id → AI 不要自己编一个；追问用户给确切 ID
- ❌ get_models 里某 SOC children 为空 → AI 误以为型号不存在；以发布页 UI 为准
- ❌ **`upgradeVersion` 默认填 = `version`**（错！这两个语义相反：`version`=要发的目标版本，`upgradeVersion`=设备当前来源版本。必须问用户）
- ❌ **`minBxVersion` 默认填 `null`**（错！实测延续 `"0.1.0"`，传 null 的实际行为**未实测**，谨慎避免）

**型号 ↔ 平台 反查流程**（实战踩坑后的标准做法）：

如果用户给的"型号 + 平台"组合对不上、或者用户只给型号没给平台，AI 用历史 `get_publish_logs` 反查：

1. 拉目标 region 在每个候选 platform 的 publish_logs
2. 看每个 model 在哪个 platform 出现的 publish 频次最高 → 启发式判定真平台
3. **必须告知用户这是启发式不是权威**（一定要复述："我按历史频次推断 X 型号的真平台是 Y，置信度高/低，请确认"）
4. **如果某型号在所有候选平台历史都为 0 → 必须停下问用户**，不能强行用用户给的值（可能用户写错平台，或这是新型号属于第三个平台。实战遇到过 CQ325B-CQ1-AN 实际真平台是 T23ZN-HC32L170 这种"0 历史"陷阱）

#### 4.1 两种发布模式的差异

| 维度 | 全量发布 | 白名单灰度发布 |
|------|---------|--------------|
| `ota_limit` | **空串 `""`** （100% 全量）⚠️ 不是 `"1:1"` | `"10000000:1"`(0%) 或 `"100:1"`(1%) 等 |
| 白名单准备 | 不需要 | **必须先**操作对应白名单 |
| 风险 | 高（影响全量设备） | 低（白名单外不受影响） |
| `useDebug=false` 走哪个白名单 | — | OTA 帐号白名单（user_id 数字） |
| `useDebug=true` 走哪个白名单 | — | debug 设备白名单（32 字符长 SN） |

**白名单写入约束**（实测）：

- `get_ota_whitelist` 返回的 `data` 是**逗号分隔字符串**，不是数组。`set_*` 入参字段名 `ota_whitelist` / `ota_debug_whitelist`
- `set` 是**覆盖式**：要"加一个"必须先 get 备份 → 拼接 → set 整体写回。直接 set 单值会清空其他人

**DEBUG 判定**（不要凭版本号后缀推断）：发布页顶部"debug: 是/否"是权威，对应 `useDebug`。AI 必须复述判定让用户确认。

#### 4.2 标准执行步骤

1. 按 4.0 模板核对信息齐全（缺必填项必须追问，不要 AI 自填默认）
2. **【实战重要】先查每个型号是否已 active publish**（避免重复发布）：
   ```
   get_publish_logs {region, platform, version}
   → 按 id 倒序找含该 model 的最近一条 publish，确认其后没有 cancel
   → 已 active 且参数（upgradeVersion/ota_limit/useDebug）跟本次一致 → 不重发，只加白名单即生效
   → 已 active 但参数不一致 → 按 cancel+republish 模式修正（见下面"修正模式"）
   → 无历史 publish → 列入本次新发的 models
   ```
   多型号场景下，只把"需新发"的型号放进本次 `publish_firmwares` 的 `models` 数组
3. **白名单模式专属**：先 get 备份当前白名单 → 拼接目标 ID → set 写回（覆盖式！）
4. 按 Rule 3 列三色清单复述完整 payload，等用户明确"执行"
5. 调 `publish_firmwares`（如果第 2 步发现型号全已 active，跳过这步只做第 3 步加白名单）
6. 用 **`get_publish_logs`** 验证（不能用 `get_publishs`，0% 灰度在那查不到）

**多平台批量发布**：`platform` 是单值字段，**型号跨多个 platform 必须分多次 publish_firmwares 调用**，每次只传一个 platform 和该平台下的 models 子集。白名单只需要 set 一次（region 共享），但 publish 要逐平台调。撤销同理 — 一次 cancel 只能撤一个 (region, platform, version, models) 组合，多平台发布要 cancel N 次。

**已发布参数错了的修正模式**（`cancel + republish`）：发现已经 publish 的某个字段错了（如 `ota_limit` 传错、`upgradeVersion` 错），**必须 cancel 错误那条 + 用正确字段重新 publish**，不要试图用 set 或别的方式补救。流程：
1. `cancel_publish_firmwares` 撤销错误那条（4 字段必须齐：region/platform/version/models）
2. 修正 payload，重新 publish_firmwares
3. `get_publish_logs` 看到一对 cancel + 新 publish 才算成功

完整 10 字段类型表 / `ota_limit` 比值映射 / `useDebug` 字段说明，详见 `references/api-reference.md § 4`。

### Rule 5 — 接口速查 / 发布页字段映射 / 调试技巧

**全部移到 references/api-reference.md**，避免 SKILL.md 臃肿：

- § 6 接口速查表（端点、必需参数、备注）
- § 7 发布页 UI 字段 ↔ API 字段映射
- § 10 探索新接口的方法（KeyError 反推、SQL 异常、慢接口等）

**最常用的两条要记住**：
1. region 取值只有 `cn` / `us` / `eu`，UI 上叫"节点"（中国/美国/欧洲）
2. 验证发布是否生效**必须用 `get_publish_logs`**，不要用 `get_publishs`（后者不返回 0% 灰度记录）

### Rule 6 — 菜单与角色映射

完整菜单/路由/角色权限表见 `references/api-reference.md` § 9。

要点：
- 角色三种：`admin` / `developer` / `product`
- 用户管理 + 固件构建管理 = **admin only**
- OTA 白名单/黑名单 = `product` / `admin`
- 接口报 `no auth` → 先用 `get_versions` 验证 token 是否还有效（**不要用 `get_user_info`，它有 bug**，见 Rule 1）。token 有效但仍 no auth 说明当前角色权限不够，按上表换 token

### Rule 7 — 浏览器交互（仅在 API 不够用时）

绝大多数查询都能纯 API 完成。只有在用户**明确要看 UI** 或需要复现 UI 中的某个交互流程时再用 agent-browser：

1. `npx skills add vercel-labs/agent-browser --skill agent-browser --agent claude-code -y`
2. 由用户在浏览器里完成 SSO 登录（不要代输密码）
3. 登录后用 `page.evaluate(() => localStorage.getItem('token'))` 取 token，回到 API 模式
4. UI 框架是 **iView**（Vue 2 / iview-admin）：菜单 class 前缀 `.ivu-menu-*`、表格 `.ivu-table-*`，与 Element / Ant Design 不同，不要照搬其他后台的选择器

## Examples

### Bad

```
用户："发 1.19.0 到 cn 的 T23 hisi"
AI: 准备执行 publish_firmwares
      region: cn, version: 1.19.0, platform: T23ZN-Hi3861L
    确认吗？
       （只列了 4 个字段，剩下 6 个 AI 默认填的字段没暴露 ——
        users 看不到 firmwareUpgradeType / useDebug / models / ota_limit 是什么，
        万一 AI 默认 firmwareUpgradeType=2 强制升级或 ota_limit=100 全量推送，
        用户根本不知道。违反 Rule 3 第 2 步的"完整字段三色清单"要求）
```

```
用户："把 1.19.0-d 发 1% 到 cn 的 T23 hisi 全部子型号"
AI: 准备执行...（按版本号 -d 后缀自动设 useDebug=true，没向用户确认就调 publish_firmwares）
       （版本号后缀只是经验，权威标志是用户/页面 debug 字段。AI 必须复述判定让用户确认）
```

### Good

```
用户："发 1.19.171 到 us 的 T23 AIW，13 个 CG625-BD 型号，灰度 0% 白名单（266 个 user_id）"
AI: 1) 先 get_publishs 查 us / T23ZN-Aiw4211L / 1.19.171 当前状态
   2) get_ota_whitelist us 备份原 14 个 user_id
   3) 反问用户："upgradeVersion 填什么？设备当前装的来源版本是？"
      （不要默认 = 1.19.171！这俩字段语义相反）
   4) 用户说 "1.19.1，minBxVersion 0.1.0"
   5) 列三色清单：

      准备执行: set_ota_whitelist + publish_firmwares
      ============ 字段清单 ============
      ✅ 用户已确认:
        region:               us
        platform:             T23ZN-Aiw4211L
        version:              1.19.171         ← 目标版本（要发的）
        upgradeVersion:       1.19.1           ← 来源版本（设备当前装的）
        minBxVersion:         "0.1.0"          ← 最低兼容（不是 null）
        models:               (13 个具体型号)
        ota_limit:            "10000000:1"     (0%)
        firmwareUpgradeType:  1 (建议升级)
        useDebug:             false (正式版)
        白名单合并后:          279 个 user_id

      ⚠️  AI 默认填:
        descriptions:         []

      6) 等用户"执行"
      7) set_ota_whitelist us → publish_firmwares → get_publish_logs 验证
```

```
用户："把刚才 cn 的 1.19.0 撤掉"
AI: 准备执行: cancel_publish_firmwares
    ✅ 用户已确认:
      region:    cn          ← 上下文延续
      version:   1.19.0      ← 上下文延续

    ⚠️ 必须确认（cancel 缺这两个会假成功）:
      platform:  T23ZN-Hi3861L  ← AI 从对话上下文延续，**请确认这是要撤的目标**
      models:    ["all"]        ← AI 从原 publish 推断，**必须和原 publish 时的 models 一致才能匹配到记录**

    确认这两项后才执行。
```

## References

- `references/api-reference.md` — 全部端点清单 + 入参（从前端 JS 反推 + 实测验证）
- 前端仓库：iview-admin 模板，菜单 i18n 文案在 `app.js` 的 `meta.title` 字段
- 关联 Skill：[firmware-build](../firmware-build/SKILL.md) — 摄像头固件本地/远程构建流程（构建产物会被这里管理）
