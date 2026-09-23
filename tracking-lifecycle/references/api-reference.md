# Tracker Manager API Reference

> Base URL: `https://us-analytics-management.theunismart.com`
> 认证: Personal Access Token（PAT）
> 捕获时间: 2026-03-03
> 捕获方式: Chrome DevTools 网络拦截

## 认证

使用 Personal Access Token（PAT）在请求头中携带：

```bash
curl -H "Authorization: Bearer $TMT_TOKEN" \
  "https://us-analytics-management.theunismart.com/api/info/getAllApplication"
```

PAT 通过平台 UI 或 `POST /api/access-tokens` 创建（见下方 PAT 管理章节），有效期 7/14/30 天。
Token 仅在创建时返回明文，请妥善保存到环境变量或文件中，不要硬编码在命令里。

响应格式统一为：
```json
{
  "code": 200,
  "data": ...,
  "errorMessage": "成功",
  "success": true
}
```

## 用户相关

### GET /api/user/getCurrentUser

获取当前登录用户信息。

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "name": "陈敬敏",
    "picture": "https://s1-imfile.feishucdn.com/...",
    "roleDesc": "普通用户",
    "roleType": 0
  }
}
```

### GET /api/role/current

获取当前用户角色权限。

## 应用管理

### GET /api/info/getAllApplication

获取所有应用列表（19 个应用）。

**响应示例（截取）：**
```json
{
  "code": 200,
  "data": [
    {
      "id": 1,
      "name": "测试应用",
      "point": "test_app",
      "type": 0,
      "createTime": "2023-10-26 02:30:42"
    },
    {
      "id": 14,
      "name": "vicohome",
      "point": "smart_camera",
      "type": 0
    },
    {
      "id": 100,
      "name": "FAPP",
      "point": "flutter_home",
      "type": 0
    }
  ]
}
```

**已知应用列表：**

| ID | 名称 | 点位标识 |
|----|------|---------|
| 1 | 测试应用 | test_app |
| 14 | vicohome | smart_camera |
| 100 | FAPP | flutter_home |
| 312 | 基站 | iot_local |
| 344 | 基站bstationd | bstationd |
| 345 | SafeRTC | safertc |
| 346 | 智能设备（嵌入式端） | smart_device |
| 391 | iot_safepush | iot_safepush |
| 525 | iot-service-staging-us | req_to_iot_local_update_device_config |
| 527 | iot_service | iot_service |
| 554 | ai-station | video_stream_eos_miss |
| 556 | ai_station | ai_infra |
| 650 | kiss_safertc | kiss_safertc |
| 651 | oauth2 | oauth2 |
| 1556 | ai_cloud | ai_cloud |
| 1909 | 厂测软件 | factory_client |
| 1990 | frps | frps |
| 1995 | bx-ddpm | bx_ddpm |
| 2016 | flywheel | flywheel |

### POST /api/info/saveOrUpdateApplicationInfo

新建或更新应用。需要 ADMIN 角色权限。

**请求体：**
```json
{
  "name": "新应用名称",
  "point": "new_app_point",
  "type": 0
}
```

**说明**：
- `type` 固定为 `0`（APPLICATION 类型）
- `point` 必须符合 snake_case 命名（`^[a-z][a-z0-9]*(_[a-z0-9]+)*$`）
- ID 自增分配，创建后不可修改
- 普通用户无权限调用，需联系管理员

## 埋点事件管理

### GET /api/info/search?applicationId={id}

搜索指定应用下的所有埋点事件（返回完整树形结构）。

**参数：**
- `applicationId` (必填): 应用 ID

**数据层级说明：**
- `type=1`: 页面 (Page)
- `type=2`: 模块 (Module)
- `type=3`: 组件 (Component)
- `type=4`: 自定义事件 (Custom Event)

树形结构通过 `parentId` 和 `children` 字段关联。

**响应示例（截取）：**
```json
{
  "code": 200,
  "data": [
    {
      "id": 2,
      "name": "测试页面",
      "point": "test_page",
      "type": 1,
      "category": "分类1",
      "parentId": 1,
      "children": [
        {
          "id": 3,
          "name": "测试模块",
          "point": "test_module_001",
          "type": 2,
          "parentId": 2,
          "children": [
            {
              "id": 4,
              "name": "测试组件",
              "point": "test_comp",
              "type": 3,
              "parentId": 3
            }
          ]
        }
      ],
      "createBy": "汪强",
      "createTime": "2023-10-30 12:59:38",
      "updateBy": "汪强",
      "updateTime": "2025-07-08 03:38:48"
    }
  ]
}
```

### GET /api/info/getAllPageByApplicationId?applicationId={id}

获取指定应用下所有页面级埋点。

## 分类标签

### GET /api/eventTag/list?applicationId={id}

获取指定应用的事件分类标签列表。

## 基础参数

### GET /api/baseSchema/list?applicationId={id}

获取指定应用的基础参数（Schema）列表。

## Context 管理

### GET /api/context/list?applicationId={id}

获取指定应用的 Context 配置列表。

## 发布管理

### GET /api/release/getAllRelease?applicationId={id}

获取指定应用的所有发布版本（工单）。

## Personal Access Token 管理

### POST /api/access-tokens

创建 Personal Access Token（任意登录用户可创建，权限继承创建者角色）。
必须通过账号登录会话创建，不能用已有 PAT 签发新 Token。

**请求体：**
```json
{
  "name": "埋点自动化",
  "expiryDays": 30
}
```

**字段说明：**
- `name` (必填): Token 名称
- `expiryDays` (必填): 有效天数，仅支持 7 / 14 / 30

**响应示例：**
```json
{
  "code": 200,
  "data": {
    "id": 1,
    "name": "埋点自动化",
    "token": "tmt_ABCDEFghijklmnop...",
    "tokenPrefix": "tmt_ABCDEFghijkl",
    "status": 0,
    "expiresAt": "2026-05-18T00:00:00Z",
    "createTime": "2026-04-18T00:00:00Z"
  }
}
```

> Token 仅在创建时返回明文，之后列表接口只显示前 16 字符前缀。请妥善保存。

### GET /api/access-tokens

列出当前用户的所有 PAT（不含明文 token）。

### POST /api/access-tokens/{id}/status

启用/禁用 Token。请求体：`{ "status": 0 }` (0=启用, 1=禁用)

### POST /api/access-tokens/{id}/delete

删除 Token。

## 批量创建事件

### POST /api/info/batchCreateEvents

批量创建埋点事件。自动按层级排序（PAGE → MODULE → COMPONENT），
自动解析 parent point 名称为数字 ID。同一事务，任一失败全部回滚。

**前提条件：**

1. 应用必须有**活跃工单**（releaseStatus ∈ {0,1,2}，即 ≠ 3「已发布」且 ≠ 4「已废弃」）；否则报 `当前应用无未发布工单`。状态语义见下方[「工单状态」](#工单状态releasestatus)。
2. **层级父节点**：MODULE 需 `parentPage`，COMPONENT 需 `parentPage` + `parentModule`（用 point 名称引用，须指向本批次或数据库中**真实存在**的节点，解析不到报 `无法解析 parent point`）。PAGE / SELF_DEFINE 无需层级父节点。
3. **应用归属**：所有事件（含 PAGE / SELF_DEFINE）都隶属某个应用，由批次顶层的 `applicationId` 统一指定，服务端写入每个事件的 `parentApplicationId`——单个事件对象里不再单独带应用字段。

> 进行中的三个状态（0 待审核 / 1 待审批 / 2 已审核）对创建事件**行为一致**，后端只按 ≠3 ≠4 判活跃、不区分具体值——不存在"草稿态不能建"之说（平台无草稿态）。

**认证：** Bearer token（与其他 API 一致）

**请求体示例：**
```json
{
  "applicationId": 14,
  "events": [
    {
      "name": "商店页面",
      "type": "PAGE",
      "trackerType": "BASE",
      "point": "store_page",
      "description": "用户进入商店页面",
      "category": "电商",
      "parameters": [
        { "name": "source", "valueType": "string", "isRequired": false, "description": "页面来源" }
      ]
    },
    {
      "name": "商品详情模块",
      "type": "MODULE",
      "trackerType": "EXP",
      "point": "product_detail",
      "parentPage": "store_page",
      "parameters": [
        { "name": "product_id", "valueType": "string", "isRequired": true, "description": "商品ID" }
      ]
    },
    {
      "name": "购买按钮",
      "type": "COMPONENT",
      "trackerType": "CLK",
      "point": "buy_btn",
      "parentPage": "store_page",
      "parentModule": "product_detail",
      "parameters": [
        { "name": "product_id", "valueType": "string", "isRequired": true, "description": "商品ID" },
        { "name": "price", "valueType": "float", "isRequired": true, "description": "商品价格" }
      ]
    }
  ]
}
```

**字段枚举映射：**

| 字段 | 请求值（字符串） | 平台值（数字） |
|------|----------------|--------------|
| type | PAGE / MODULE / COMPONENT / SELF_DEFINE | 1 / 2 / 3 / 4 |
| trackerType | BASE / CLK / EXP | 0 / 1 / 2 |
| valueType | string / integer / float / boolean / array / object | 5 / 4 / 4 / 1 / 3 / 2 |

parentPage / parentModule 使用 point 名称引用（非数字 ID），可引用本批次中的其他事件或数据库已有事件。

**响应示例：**
```json
{
  "code": 200,
  "success": true,
  "data": {
    "createdCount": 3,
    "createdEvents": {
      "store_page": 12345,
      "product_detail": 12346,
      "buy_btn": 12347
    }
  }
}
```

**错误情况：**

| 场景 | errorMessage |
|------|-------------|
| 批次内 point 重复 | 批次内存在重复的 point |
| parent point 找不到 | 无法解析 parent point: xxx |
| point 已存在 | 当前点位已经存在,无法添加 |
| 应用无活跃工单 | 当前应用无未发布工单 |
| 后端 NPE（罕见）| `{"code":500,"errorMessage":null}`——批量接口已规避常见空指针；若出现请附 applicationId + 出错 point 反馈平台团队按服务端日志定位 |
| API Key 无效 | 401 Unauthorized |

## 编辑已注册事件 / 给已有事件补参数

### POST /api/info/saveOrUpdateEventInfo

新增**或**更新单个事件（应用级用 `saveOrUpdateApplicationInfo`，本接口用于 PAGE/MODULE/COMPONENT/SELF_DEFINE）。

- **不带 `id`** → 新建单个事件（等价于 `batchCreateEvents` 的单事件版本）。
- **带 `id`** → **更新已注册事件**：可改名称/描述/分类、增删参数、增删 base schema。
  更新成功后平台会**重新生成该事件及其子事件的 iglu schema 文件**，并重置该应用的 release validate 状态。

> ⚠️ `batchCreateEvents` **只能创建**，对已存在 point 会报 `当前点位已经存在,无法添加`。
> 给「当前活跃工单内、尚未发布」的事件补参数，**必须走本接口（带 `id`）**，不要试图用 `batchCreateEvents` 升版。

**认证：** Bearer token（同其它写接口）。需要**该应用的 app owner 权限**。

**前提：** 应用必须有**活跃工单**（`releaseStatus ≠ 3 且 ≠ 4`，状态语义见下方[「工单状态」](#工单状态releasestatus)），否则报 `请先创建工单!`。

**⚠️ 全量覆盖参数语义（最易踩坑）：** 更新时服务端会**先删除该事件的全部旧参数，再写入请求体里的 `parameters` 列表**。
因此补一个参数必须传**完整参数列表（已有参数 + 新参数）**，只传新参数会丢掉旧参数。
推荐 round-trip：先 `GET /api/info/getEventDetail?eventId={id}` 取回当前完整定义，把新参数 append 到 `parameters` 后整体回传。

**字段格式（注意与 `batchCreateEvents` 的字符串枚举不同，这里用数字码）：**

| 字段 | 说明 | 取值 |
|------|------|------|
| `id` | 事件 ID（**带上 = 更新**，不带 = 新建） | 数字 |
| `type` | 事件类型 | 1=PAGE / 2=MODULE / 3=COMPONENT / 4=SELF_DEFINE |
| `name` / `point` | 名称 / 点位（point 须 snake_case） | 字符串 |
| `parentApplicationId` | 所属应用 ID | 数字 |
| `parentPageId` / `parentModuleId` | MODULE/COMPONENT 的父级 ID | 数字 |
| `category` | 分类标签（仅 PAGE/SELF_DEFINE，单个） | 字符串 |
| `baseSchemas` | base schema **ID 列表** | `List<Long>`（注意 getEventDetail 返回的是对象列表，回传时要取 `id`） |
| `parameters[].eventId` | 必填，须等于事件 `id` | 数字 |
| `parameters[].trackerType` | 埋点类型 | 0=base / 1=clk / 2=exp |
| `parameters[].valueType` | 值类型（**数字码字符串**） | "0"=null / "1"=boolean / "2"=object / "3"=array / "4"=number / "5"=string |
| `parameters[].isRequired` | 是否必填 | 0=否 / 1=是 |
| `parameters[].name` | 参数名（snake_case） | 字符串 |

**示例：给 PAGE 事件 id=2399 补一个非必填 number 参数 `device_count`**（已有参数 `source` 一并回传）：

```json
{
  "id": 2399,
  "type": 1,
  "name": "账号注销设备警告页",
  "point": "account_delete_device_warning_page",
  "parentApplicationId": 14,
  "category": "账号",
  "baseSchemas": [101],
  "parameters": [
    { "eventId": 2399, "trackerType": 0, "name": "source",       "valueType": "5", "isRequired": 0, "description": "页面来源" },
    { "eventId": 2399, "trackerType": 0, "name": "device_count", "valueType": "4", "isRequired": 0, "description": "设备数量" }
  ]
}
```

```bash
curl -X POST \
  -H "Authorization: Bearer $TMT_TOKEN" \
  -H "Content-Type: application/json" \
  -d @event-update.json \
  "https://us-analytics-management.theunismart.com/api/info/saveOrUpdateEventInfo"
```

**约束（更新时）：**

| 场景 | 行为 |
|------|------|
| 应用无活跃工单 | 报 `请先创建工单!` |
| 事件 `id` 不存在 | 报 `事件不存在` |
| 改**已发布工单继承**的 point | 报 `从之前工单继承的点位不可修改`（R7，须新建事件） |
| 改**继承参数**的 name/type | 被 `validateNameAndTypeImmutableForInheritedParams` 拦截 |
| 新 point 与他事件冲突 | 报 `当前点位已经存在,无法添加` |
| 传入失效 `parentPageId`（指向已删除/不存在页面） | ⚠️ 已知缺陷：裸 NPE `{"code":500,"errorMessage":null}`（单事件接口未做 parent 存在性校验）。补参数走上面的 getEventDetail round-trip（`parentPageId` 原样回传）可规避；新建 MODULE/COMPONENT 优先用 `batchCreateEvents` |
| 非 app owner | 权限拦截 |

> 仅当事件由**之前已发布工单**继承（point 不可改）时才退化为「新建事件」；对当前活跃工单内的事件，本接口可直接原地补参数，无需 UI 操作。

## 工单状态（releaseStatus）

工单（release）共 **5 个状态，无"草稿态"**。`GET /api/release/getAllRelease?applicationId={id}` 返回的 `releaseStatus` 含义：

| code | 枚举名 | 名称 | 说明 |
|------|--------|------|------|
| 0 | PENDING_REVIEW | 待审核 | 新建工单的**初始态**；审批被拒/取消、校验或发布前置校验不通过时的回落态 |
| 1 | PENDING_APPROVAL | 待审批 | 已创建飞书审批实例，等待结果 |
| 2 | REVIEWED | 已审核（待发布） | 审批/校验通过，可执行上线 |
| 3 | RELEASED | 已发布 | 终态（schema 已推 prod） |
| 4 | CANCELLED | 已废弃 | 终态（软废弃） |

- **活跃工单** = `releaseStatus ≠ 3 且 ≠ 4`，即 0/1/2 三个进行中状态。
- 创建/编辑事件时，后端**只判定"是否活跃"**（`getActiveReleaseLogByAppId` 用 ≠3 ≠4 过滤），**不区分 0/1/2 具体值**——三者行为完全一致。**不存在"待审核(0)不能建、已审核(2)才能建"的差异。**
- iglu schema 版本 = 该活跃工单的 `version` 字段（见 SKILL.md「数据准备」），**勿默认 `1-0-0`**。

## 平台校验规则与禁止操作（速查）

下列是后端**强制**的硬规则（与 `tracker-management` 源码逐条核对）。skill 走 API 自动化时**不得引导用户绕过**——平台会直接拒绝，AI 设计阶段就应避免。

### 写事件 / 参数（saveOrUpdateEventInfo / batchCreateEvents）

| 禁止 / 限制 | 触发 | 报错原文 |
|------|------|---------|
| **敏感字段参数名禁止**（仅校验**新增**参数；更新时只查新增的） | 参数名（大小写不敏感、子串匹配）命中：`password`/`pswd`、`secret`、`token`、`phone`、`mobile`、`email`、`ssid`、`mac`、`ip`、`address`。白名单关键词：`encrypt` | `检测到敏感字段，无法添加参数: {名}` |
| **同一事件参数 trackerType：base 与 clk/exp 不可混用** | 参数 trackerType 同时含 `0`(base) 和 `1/2`(clk/exp)，或种类超 2 | `埋点类型配置错误,base与clk/exp无法兼容` |
| **同一事件内参数名不可重复** | parameters 中 name 去重数 ≠ 总数 | `额外参数列表中有同名的属性` |
| **组件的父模块必须属于同一页面、同一应用**（禁止跨页面复用模块） | COMPONENT 的 parentModule 的页面/应用与该组件不一致 | `模块不属于当前页面` / `模块所属应用与当前事件不一致` / `模块不存在` |
| 分类只能单个 tag，且 tag 必须**已存在**（先 `GET /api/eventTag/list` 或先建） | category 含逗号；或 tag 名不在该应用 | `当前不支持多tag` / `以下标签不存在: …` |
| 引用的 base schema 必须**存在且属于同一应用** | baseSchema id 已删/跨应用 | `基础schema的id不存在` / `基础schema的所属应用id错误` |
| `parseConfig` / `valueConfig` 非空时必须是**合法 JSON** | 传了非 JSON 字符串 | `{名}的解析配置不是JSON格式` / `{名}的额外配置不是JSON格式` |
| 继承自已发布工单的 point / 参数 name·类型不可改 | 见上文「编辑已注册事件」约束表 | `从之前工单继承的点位不可修改` / `非当前版本新增的参数的名称与类型不可修改` |

> 敏感字段例外：因为只校验"**新增**参数名"，给已注册事件**补**的新参数若命中敏感词同样会被拒。涉及 PII（手机号/邮箱/IP/MAC 等）应在端上脱敏/哈希后用非敏感字段名上报，不要直接以敏感词命名埋点参数。

### 删除事件（deleteEventDetail）

**先判定能不能删（核心约束）**：埋点是否曾随**任意一次工单发布**上线（后端按 `point` 是否存在于历史快照表 `event_info_hist` 判定）。

| 埋点状态 | 能否删除 | 说明 |
|---------|---------|------|
| **已发布过**（出现在任意历史工单的发布快照里） | ❌ **永久不可删** | 后端 `无法删除已发布过的埋点`，无 force 选项可绕过。下线只能走迁移：旧点位保留停用 + 新建替代点位 |
| **本版本新建、从未发布**（仅存在于当前活跃工单，`event_info_hist` 无记录） | ✅ 可删 | 仍需满足下面的前置/子级条件 |

> ⚠️ AI **不要**为了"改名/纠错"去删一个已发布点位再重建——会被直接拒绝。已发布点位的 point/参数 name·类型本就锁死（见上文「编辑已注册事件」），需求变化时走 `saveOrUpdateEventInfo` 补参数或新建替代点位，**不要走删除**。

删除"可删"点位时，还需满足：

| 前置 / 限制 | 触发 | 报错原文 |
|------|------|---------|
| 删除同样要求**有活跃工单**（非应用类型） | 无活跃工单 | `请先创建工单!` |
| 有下层子事件时需显式 `forceDeleteFlag=1`（强制删会**级联软删全部子事件**，不可逆） | forceDeleteFlag=0 且存在子事件 | `当前删除的事件有依赖的下层结构` |
| 事件须存在 | id 无对应未删除事件 | `当前id的事件不存在` |

### 工单（release）

| 禁止 / 限制 | 触发 | 报错原文 |
|------|------|---------|
| **一个应用同一时刻只能有一个活跃工单**（建工单前不能已有活跃工单） | 已存在 releaseStatus ∈ {0,1,2} 的工单时再建 | `当前有未发布的工单!` |
| 已发布(3)的工单不可废弃；已废弃(4)不可重复废弃 | 对应状态执行废弃 | `已发布的工单无法废弃` / `工单已经是废弃状态` |
| 工单 version 格式 `\d+-\d+-\d+`（如 `1-0-x`，x=上版本+1，只增不退） | 格式不符 | 前端「版本号格式有误」 |
| 审批 / 发布 / 废弃 等状态流转走 UI（需飞书审批），AI 不替用户操作 | — | 见 SKILL.md Rule 5.3 |

> 权限：`saveOrUpdateApplicationInfo`（建应用）需 ADMIN；`saveOrUpdateEventInfo` 需该应用 app owner / admin。`deleteEventDetail`、`batchCreateEvents`、baseSchema/context/tag 写接口当前**后端未在 Controller 层强制鉴权**（MCP 走 API Key 注入 `mcp-system`≈admin），但 skill 仍应按"需 app owner"语义自律，不引导越权操作。

## URL 路由参考

| 页面 | URL 模式 |
|------|---------|
| 埋点列表 | `/spm/list/` |
| 新建埋点 | `/spm/edit/?type={1\|2\|3\|4}` |
| 查看埋点详情 | `/spm/edit/?id={id}&isView=true` |
| 编辑埋点 | `/spm/edit/?id={id}` |
| 基础参数列表 | `/schema/list/` |
| Context 列表 | `/context/list/` |
| 埋点验证 | `/check/dashboard/` |
