# firmware-mgr API Reference

从前端 bundle（`app.js` + 20 个 chunk-*.js）静态分析提取，未通过浏览器打开。

- **Base URL**：`https://firmware-mgr.addx.live/api`
- **测试环境 Base**：`https://firmware-mgr-test.addx.live/api`
- **认证 Header**：`token: <32 字符 token>`
- **方法**：除特殊说明外**全部 POST**，`Content-Type: application/json`，无参传 `{}`
- **响应**：`{"result": <int>, "msg": <string>, "data": <any>}`，`result == 0` 为成功

## 1. 认证 / 用户

| 端点 | 用途 | 必需参数（推断） | 已验证 |
|------|------|---------------|-------|
| `login` | 登录获取 token | username + password（具体字段未验证） | 否 |
| `logout` | 退出登录 | — | 否 |
| `get_user_info` | 当前 token 对应的用户信息 | ⚠️ 该接口要求 token 同时在 header 和 body 里，header-only 会返回 `KeyError: 'token'`。**不适合做权限体检**，体检用 `get_versions` 或 `get_models` | ✅ |
| `get_users` | 用户列表 | — | ✅ 需 admin 角色（普通 token 返回 `no auth`） |
| `modify_user_access` | 修改用户访问权限（admin） | user_id + access | 否（写操作） |
| `modify_user_active` | 启用/停用用户（admin） | user_id + active | 否（写操作） |

## 2. 元数据查询

| 端点 | 用途 | 必需参数 | 已验证 |
|------|------|---------|-------|
| `get_models` | 设备型号 cascader 树 | `{}` | ✅ 返回 SOC（如 `HI3518EV300-Hi1131`）→ 子型号（CG1/CG2/...） |
| `get_versions` | 全部已知版本号列表 | `{}`（**不能传 region**） | ✅ 返回如 `["1.9.6","1.9.6_develop",...]` |

## 3. 固件构建

### 主线（CG 系列）

| 端点 | 用途 | 备注 |
|------|------|------|
| `get_firmwares` / `download_firmware` | 固件列表 / 下载 | **完整流程详见 § 8** |

### BX 系列（管理路径前缀 `/management/firmware/`）

| 端点（拼到 `/api` 后） | 方法 | 用途 |
|----------------------|------|-----|
| `/management/firmware/getBxBuildList` | POST | BX 构建任务列表 |
| `/management/firmware/getBxBuildVersions` | POST | BX 构建版本列表 |
| `/management/firmware/getBxBuildInfo?id=<id>` | GET | 单个 BX 构建详情（**注意这个是 GET + querystring**） |
| `/management/firmware/getBxSoftsInfo?modelNo=<model>` | GET | 某型号的 BX 软件信息 |
| `/management/firmware/buildBxVersion` | POST | **触发新的 BX 构建**（写操作） |
| `/management/firmware/queryHubMinVersions?application=<app>` | GET | 查询 Hub 最低版本 |
| `/management/firmware/queryMinBxVersions` | POST | 查询 BX 最低版本 |
| `/management/firmware/querySubVersions` | POST | 子版本查询 |
| `/management/firmware/querySuggestVersions?currentFirmwareId=<id>` | GET | 推荐升级版本 |
| `/management/firmware/pubList` | POST | BX 发布列表 |

## 4. 发布管理（已实测 stage）

### 4.1 `publish_firmwares` — 发布固件 ⚠️ 写操作

完整 payload（11 字段，stage 实测 5 次成功）：

```json
{
  "region": "cn",                      // 必需。cn / us / eu
  "platform": "T23ZN-Aiw4211L",        // 必需。SOC 平台，get_models 顶层 value
  "version": "1.19.171",               // 必需。要发的固件版本（UI 上叫"升级版本"=目标版本）
  "models": ["all"],                   // 必需。子型号列表，["all"] 或 ["CG1","CG2"]
  "ota_limit": "10000000:1",           // 必需。⚠️ 是比值字符串不是数字！见下方 4.1.1 映射表
  "firmwareUpgradeType": 1,            // 必需。0=可忽略 / 1=建议升级(默认) / 2=强制升级
  "upgradeVersion": "1.19.1",          // ⚠️ 必填(type>0)。**来源版本，不是目标版本**。语义="设备当前装的版本"。例：发 1.19.171 让 1.19.1 的设备升上来 → 这里填 "1.19.1"
  "useDebug": false,                   // 必需。true=DEBUG版本（走debug白名单），false=正式版
  "minBxVersion": "0.1.0",             // 可选（实测不传也能 publish 成功，result=0）。要传就用字符串 "0.1.0"（任意版本可升）；⚠️ 不要传 null（行为未实测）
  "descriptions": []                   // 必需。发布说明数组，空数组 [] 合法
}
```

**校验顺序**：先 KeyError 校验上述字段是否在 body 里 → 再 backend 校验 `region/version/models/firmwareUpgradeType` 非空。

成功响应：`{"result": 0, "msg": "success", "data": {}}`

#### 4.1.1 ⚠️ ota_limit 是**比值字符串**（生产实测，重大坑点）

`ota_limit` **不是百分比数字**，是 `"分母:命中余数列表"` 形式的字符串。前端按设备 ID 取模分母，余数命中则推送。

**完整映射表**（已生产实测，源码 + 历史频次双重验证）：

| 灰度 % | ota_limit 值 |
|--------|------------|
| **0%** | `"10000000:1"` ⚠️ 不要用 `""` 或 `"1:1"`（见下方"100% 实测坑"） |
| **1%** | `"100:1"` |
| **5%** | `"20:1"` |
| **10%** | `"20:1,2"` |
| **15%** | `"20:1,2,3"` |
| **20%** | `"20:1,2,3,4"` |
| **25%** | `"20:1,2,3,4,5"` |
| **30%** | `"20:1,2,3,4,5,6"` |
| **35%** | `"20:1,2,3,4,5,6,7"` |
| **40%** | `"20:1,2,3,4,5,6,7,8"` |
| **45%** | `"20:1,2,3,4,5,6,7,8,9"` |
| **50%** | `"20:1,2,3,4,5,6,7,8,9,10"` |
| **55-95%** | `"20:1,2,...,N"`（N = 灰度% / 5） |
| **100%** | **空字符串 `""`** ✅ 已实测（不是 `"1:1"`！） |

**100% 全量发布的实测真相（2026-06-17 验证）**：

- ❌ 不要传 `"1:1"`。这个字符串在新版 firmware-mgr 后端 `allowOTA` 逻辑里被算成 **0%**（非空非已知分母 → 走白名单灰度分支），实际**所有非白名单设备都不会触发升级**。前端 UI 渲染时虽然把 `"1:1"` 兼容映射显示成 100%，但后端实际行为是 0%
- ✅ **必须传空字符串 `""`**。后端 `allowOTA` 走 `isEmpty` 分支 → `return true` → 真·全量推送
- 证据：UI 下拉选 100% 时前端写入的就是 `""`（见 `<Radio label="" border>100%</Radio>`）；历史 publish 记录中"全量"用 `""` 共 226 次，用 `"1:1"` 仅 2 次（其中 1 次是踩坑案例 publish 5407 已被 cancel 修正）

**踩坑警告**：

- 传整数 `0` 或字符串 `"0"` 都会被后端原样存储但 UI 不识别，"灰度比例"列显示为 `-`（坏值）。**必须传 `"10000000:1"` 才会被 UI 识别成 0%**
- ⚠️ 旧数据里 `ota_limit: ''`（空字符串）的发布在新版后端实际是**全量 100%**，不再是"被 UI 显示为 0%"。新发布 0% 灰度请用 `"10000000:1"`，不要用空串
- 这个映射规则后端 `allowOTA` 实际参与判定（不是只前端写死）。`isEmpty(ota_limit)` 走全量分支；非空 → 按 `"分母:命中余数列表"` 解析匹配 device_id，不匹配模式的字符串（如 `"1:1"`、`"0"`）一律落到"非白名单不推送"，等价于 0%

### 4.2 `cancel_publish_firmwares` — 撤销发布 ⚠️ 写操作

```json
{
  "region": "cn",                      // 必需
  "platform": "T23ZN-Hi3861L",         // ⚠️ 必需！数据存在时不带这个找不到记录
  "version": "1.19.0",                 // 必需
  "models": ["all"]                    // 必需。必须和 publish 时的 models 一致
}
```

**陷阱**：探测时如果数据库中没有匹配记录，缺 `platform` 也会返回 success（无操作即成功，骗局陷阱）。**实际撤销必须传齐 4 个字段**，否则会假成功但记录还在。

### 4.3 `get_publishs` — 查询发布记录

```json
{
  "region": "cn",
  "platform": "T23ZN-Hi3861L",
  "version": "1.19.0",
  "group": null                        // 必需字段，传 null 或 "" 都行
}
```

返回：`data` 为该 region/platform/version 的发布记录数组，无记录时为 `[]`

**⚠️ 重大坑点**（生产实测）：**`get_publishs` 不返回 0% 灰度的发布记录**，永远是 `data: []`。即使 publish_firmwares 已经 `result:0` 成功、UI 列表里能看到，这个接口也不展示。验证发布是否真生效**必须改用 `get_publish_logs`**，看里面有没有对应的 publish action 记录。

**其他已知问题**：在 stage `us`/`eu` 上 iot-service 上游有时返回 502 Bad Gateway，cn 较稳定。publish/cancel 接口本身不受这个上游问题影响。

### 4.3.1 `get_publish_logs` — 查询发布操作日志（**首选验证手段**）

```json
{ "region": "cn" }                              // 最少入参，返回该 region 全部历史
{ "region": "cn", "platform": "...", "version": "..." }   // 可加过滤
```

返回单条记录字段：
```
id, action(publish|cancel), region, platform, version, models[],
ota_limit(字符串), useDebug(0|1), firmwareUpgradeType, upgradeVersion,
force_to_current_firmware, ota_whitelist,
user_full_name(操作人), update_time
```

**用途**：发布后验证是否成功（找到对应 `action: publish` + `update_time` 接近的记录就是生效）。比 `get_publishs` 可靠。

### 4.4 其他

| 端点 | 用途 | 备注 |
|------|------|------|
| `get_publish_options` | 发布表单选项 | stage 上 504 超时（慢接口） |
| `publish_factory` | **工厂版本发布** | 字段未实测 |

（`get_publish_logs` 详见 § 4.3.1）

**已知 region**：`cn` / `us` / `eu`（三者 stage 全部支持 publish/cancel；其他值会 `KeyError`）。

## 5. OTA 名单管理（已实测）

### 5.1 数据格式（关键）

OTA 各类名单的 `data` 字段是**逗号分隔字符串**，**不是数组**：

```json
// get_ota_whitelist 返回
{"result": 0, "msg": "success",
 "data": "9466,136060,6331,926,735,1262,..."}     // 数字 user_id 列表

// get_ota_debug_whitelist 返回
{"result": 0, "msg": "success",
 "data": "cd69239896b16aad4f74d3f13bb33d0d,b1b47118bbadda551fd39c2ef42fc04a,..."}  // 32字符 hex SN
```

数据中有时会有空格 / 换行符 `\n`（人工编辑残留），不要假设干净。

### 5.2 接口入参

| 端点 | 入参 | 是否写操作 |
|------|-----|----------|
| `get_ota_whitelist` | `{"region":"cn"}` | 否 |
| `set_ota_whitelist` | `{"region":"cn","ota_whitelist":"<逗号分隔字符串>"}` | **是** |
| `get_ota_debug_whitelist` | `{"region":"cn"}` | 否 |
| `set_ota_debug_whitelist` | `{"region":"cn","ota_debug_whitelist":"<逗号分隔字符串>"}` | **是** |
| `get_ota_blacklist` / `set_ota_blacklist` | 推断同上，字段名 `ota_blacklist` | set 是写 |
| `get_ota_device_whitelist` / `set_ota_device_whitelist` | 推断同上，字段名 `ota_device_whitelist` | set 是写 |

### 5.3 写操作的覆盖性

`set_ota_*` 是**整体覆盖**（不是追加）。要"加一个 user_id"必须：

1. `get_ota_whitelist` 拿到当前完整字符串
2. 拼接：`<原字符串>,<新id>`
3. `set_ota_whitelist` 写入完整新字符串

直接 set 一个值会清空其他人的白名单。**写之前必须先 get 备份**，万一出错可恢复。

成功响应：`{"result": 0, "msg": "success", "data": null}`

## 6. 接口速查（按 SKILL.md 引用）

| 用途 | 端点 | 必需参数 | 备注 |
|------|------|---------|------|
| 当前用户信息 | `get_user_info` | — | token 必须有效 |
| 全部已知版本 | `get_versions` | `{}` | 不要传 `region`，会触发 SQL 异常 |
| 设备型号树 | `get_models` | `{}` | 返回 SOC → 子型号 cascader 数据 |
| 固件列表 | `get_firmwares` | 至少需 `region`，可能还需 `version`/`model` | 实测慢（curl 30s 仍可能超时），timeout ≥ 60s |
| 发布记录 | `get_publishs` | `region` + `platform` + `version` + `group` | 实测慢，timeout ≥ 60s。**0% 灰度的发布记录在这里查不到**，用 `get_publish_logs` 代替 |
| 发布操作日志 | `get_publish_logs` | `region`（可加过滤） | **首选验证手段**，0% 灰度也能看到 |
| 发布配置选项 | `get_publish_options` | `region` + `version` | 实测慢/可能 504，给发布表单使用 |
| OTA 帐号白名单 | `get_ota_whitelist` | `region` | data 是逗号分隔字符串 |
| OTA 设备黑名单 | `get_ota_blacklist` | `region` | 同上 |
| OTA 设备白名单 | `get_ota_device_whitelist` | `region` | 同上 |
| Debug 设备白名单 | `get_ota_debug_whitelist` | `region` | 同上 |
| Flash / Output 下载 | `get_flashs` / `get_outputs` | `region` | 真正下载用 `download_*` |
| BX 构建 | `/management/firmware/getBxBuildList` 等 | 见 § 3.2 | 路径前缀是 `/api/management/firmware/...` |

**已知 region 取值**：`cn` / `us` / `eu`（其他值如 `sg`/`jp`/`asia` 会返回 `KeyError`）。页面 UI 上 region 叫"**节点**"（"中国" → `cn`、"美国" → `us`、"欧洲" → `eu`）。用户口语用中文节点名时，调用 API 必须换成 region 代码。

**调试技巧**：不知道某个接口要哪些参数时，先传 `{}`，后端返回 `KeyError: '<field>'`，逐个补字段直到 `result == 0`。

## 7. 发布页（`/publish/publish-list`）字段映射

### 7.1 顶部筛选条

| 页面字段 | API 字段 | 说明 |
|---------|---------|-----|
| 平台 | `platform` | 选 SOC（如 `HI3518EV300-Hi1131`），决定下面表格的型号列表 |
| 版本 | `version`（如 `1.5.0`） | 要发布的固件版本 |
| **debug** | `useDebug`（bool） | 是/否，决定走 OTA 帐号白名单还是 debug 设备白名单 |
| 分组 | `group` | 设备型号维度的分组。**默认不选**，只在特定异常分组发布时才选 |
| 节点 | `region` | 中国→cn / 美国→us / 欧洲→eu |

### 7.2 主表格列

`型号 / tenant / MD5 / gitsha / 已发布 / 已升级 / 灰度比例 / 升级方式 / 升级版本 / 最新发布版本`

- "已发布" 列的 X 图标：未发布；非 X 图标：已发布
- "灰度比例" 列：当前生效的灰度，按 `ota_limit` 字符串映射回百分比（见 § 4.1.1）。**0% 灰度在这一列显示是 "0%"，不是 "-"**；如果显示 "-" 说明 `ota_limit` 取值非法
- "最新发布版本"：该型号目前线上的版本，用于发布前对比写 diff

### 7.3 发布管理弹窗（点击"发布管理"按钮后弹出）

灰度发布比例选项：`0% / 1% / 5% / 10% / 15% / ... / 100%` + `10%[旧] / 25%[旧] / 50%[旧] / 75%[旧]`（旧=遗留比例算法）。

升级方式选项：`可忽略 / 建议升级 / 强制升级` 三选一。

## 8. 下载（构建产物）

### 8.1 接口端点

| 端点 | 用途 | 实测状态 |
|------|------|---------|
| `get_firmwares` / `download_firmware` | 固件列表 / 下载 | ✅ 已实测 |
| `get_flashs` / `download_flash` | Flash 包列表 / 下载 | 端点存在，未实测 |
| `get_outputs` / `download_output` | Output 列表 / 下载（构建中间产物） | 端点存在，未实测 |

### 8.2 完整下载流程（已实测，固件下载验证通过）

**Step 1 — 列出可下载的固件**

```json
POST /api/get_firmwares
{
  "region": "us"                                       // 可选：过滤 region
  // 也可加 "platform":"...", "version":"..." 缩小范围
}
```

返回字段（每条记录）：
```
id, name, platform, model, version, gitsha, md5,
s3_bucket_name, s3_object, update_time, is_factory_released
```

**Step 2 — 用 id 换 S3 预签名 URL**

```json
POST /api/download_firmware
{ "id": 18974 }                                        // 必需，缺则 KeyError: 'id'
```

返回：
```json
{
  "result": 0,
  "msg": "success",
  "data": "https://addx-firmware-cn.s3.cn-north-1.amazonaws.com.cn/<path>?X-Amz-Algorithm=AWS4-HMAC-SHA256&...&X-Amz-Expires=600&X-Amz-Signature=..."
}
```

**Step 3 — 直接 GET 下载**

```bash
curl -o <local-name>.fw "<上一步返回的 data 字段 URL>"
```

### 8.3 重要约束（实测踩坑）

| 项 | 详情 |
|---|------|
| URL 有效期 | **10 分钟（600 秒）**，超时需重新调 download_firmware 拿新 URL |
| HTTP 方法限制 | **只能 GET，不能 HEAD**（HEAD 返回 403 Forbidden）。AWS Sig V4 按方法签名 |
| S3 区域 | `addx-firmware-cn.s3.cn-north-1.amazonaws.com.cn`（AWS 中国北区），国内访问可能需特定网络 |
| 文件大小 | 实测 T23ZN-Aiw4211L 1.19.171 固件约 13.5 MB（~14M 字节） |
| 完整性校验 | get_firmwares 返回的 `md5` 字段就是文件 MD5，下载后 `md5sum` 比对验证 |
| 权限 | 推断：能调 get_firmwares 的 token 应该都能调 download_firmware（普通用户级） |

### 8.4 同模式推断（未实测 flash/output）

按命名规律，`download_flash` 和 `download_output` 应该是同样模式：
- 入参 `{"id":<id>}`
- 返回 S3 预签名 URL
- 同样 10 分钟有效

实际使用时第一次先 KeyError 反推法验证字段名，确认后再批量。

## 9. 菜单与角色映射

| 一级菜单 | 子菜单 | 路由 | 可访问角色 |
|---------|-------|------|-----------|
| 首页 | — | `/home` | developer / product / admin |
| 构建 | 固件构建 | `/firmware` | developer / admin |
| 构建 | BX 构建 | `/firmware/bx-build` | developer / admin |
| 构建 | 构建 BX 版本 | `/firmware/bx-build-sub`（隐藏） | developer / admin |
| 发布 | 固件发布 | `/publish/publish-list` | developer / product / admin |
| 发布 | 固件发布记录 | `/publish/publish-log` | developer / product / admin |
| 下载 | Output 下载 | `/output-list` | developer / product / admin |
| 下载 | Flash 下载 | `/flash-list` | developer / product / admin |
| 下载 | 固件下载 | `/firmware-list` | developer / product / admin |
| 管理 | 用户管理 | `/management/user` | admin only |
| 管理 | 固件构建管理 | `/management/build-setting` | admin only |
| OTA | OTA 帐号白名单 | `/ota-whitelist` | product / admin |
| OTA | OTA 设备黑名单 | `/ota-blacklist` | product / admin |
| OTA | Debug 设备白名单 | `/debug-ota-whitelist` | product / admin |
| OTA | OTA 基站设备白名单 | `/ota-base-station-device-whitelist` | product / admin |

接口报 `no auth` 时，先 `get_user_info` 查当前 token 对应的角色，按上表判断是否需要换 admin token。

## 10. 站内消息

| 端点 | 用途 |
|------|------|
| `message/init` | 初始化消息会话 |
| `message/count` | 未读消息数 |
| `message/content` | 消息内容 |
| `message/has_read` | 标记已读 |
| `message/restore` | 恢复消息 |
| `message/remove_readed` | 删除已读消息（写） |

## 11. 探索新接口的方法

1. 缺参时后端会暴露异常字符串：`{"result": -1, "msg": "KeyError: '<field>'."}` —— 按提示逐一补齐
2. SQL 错误：`pymysql.err.OperationalError: (1054, "Unknown column 'X' in 'where clause'")` —— 说明该字段对该端点无效，应去掉
3. 角色不足：`{"result": 10001, "msg": "no auth"}` —— 当前 token 角色没权限
4. 慢接口（实测）：`get_publishs` / `get_publish_options` / `get_firmwares` 这几个**查询接口**在 region=cn / 缺 model 过滤时会卡住（curl 30s 仍可能没返回），timeout 设 ≥ 60s。**注意**：写接口 `publish_firmwares` 等的速度未实测。推断慢的原因是缺型号过滤导致扫全表（推断：基于"补 model 后部分调用变快"的迹象），不是事实

## 12. 关键调用示例

```bash
# Token 从环境变量读取，禁止明文写入文件或 commit
# 设置：export FIRMWARE_MGR_TOKEN="<your-token>"
# 测试环境换成 FIRMWARE_MGR_TEST_TOKEN
BASE="https://firmware-mgr.addx.live/api"
H1="token: $FIRMWARE_MGR_TOKEN"
H2="Content-Type: application/json"

# 当前用户
curl -s -X POST -H "$H1" -H "$H2" -d '{}' "$BASE/get_user_info"

# 全部版本
curl -s -X POST -H "$H1" -H "$H2" -d '{}' "$BASE/get_versions"

# 设备型号树
curl -s -X POST -H "$H1" -H "$H2" -d '{}' "$BASE/get_models"

# cn 区某版本的发布记录（注意 group 必填，传 null 即可）
curl -s -X POST -H "$H1" -H "$H2" -d '{"region":"cn","platform":"T32ZN-Aiw4211L","version":"1.19.0","group":null}' "$BASE/get_publishs"

# 验证发布是否生效（推荐这个，0% 灰度只能在这里看到）
curl -s -X POST -H "$H1" -H "$H2" -d '{"region":"cn"}' "$BASE/get_publish_logs"

# 单个 BX 构建详情（注意 GET）
curl -s -H "$H1" "$BASE/management/firmware/getBxBuildInfo?id=123"
```
