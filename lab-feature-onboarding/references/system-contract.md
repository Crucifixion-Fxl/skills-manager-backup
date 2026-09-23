# 系统与数据契约

## 目录

- 组件责任和多客户端契约
- 三类 key 与 NH `lab_feature`
- PE `user_feature_settings` 与默认状态
- GrowthBook policy 与更新安全规则

## 1. 责任边界

| 组件 | 责任 | 禁止承担的责任 |
|---|---|---|
| Naturehood (NH) | 功能目录、开关 API、同步能力门禁、timeline 投影 | 不用 `lab_feature.status` 决定运行时，不向 IOT 暴露全局真相 |
| Personalization Engine (PE) | UPS 用户选择、`enabledLabFeatureKeys`/`disabledLabFeatureKeys` attributes、GrowthBook 机械评估 | 不自行发明 lifecycle，不替代业务权益 |
| GrowthBook (GB) | eligibility、lifecycle、runtime policy、环境和规则顺序 | 不保存 CMS 文案，不直接充当用户设置数据库 |
| CMS | icon、暗色 icon、详情图片、非本地化 metadata | 不保存标题和简介翻译，不承担实验主逻辑，不新增实验室专属可观测性 |
| Crowdin | 源文案和全部客户端语言翻译 | 不保存媒体或运行时 policy |
| IOT/异步服务 | 消费 PE 最终 policy，在副作用边界阻止无效成本 | 不调用 NH，不直读 NH 表或 PE 原始设置 |
| Android/iOS 宿主 | 设置页入口、红点桥接、Flutter 路由、原生入口门禁 | 不实现实验目录、不过滤真实 Timeline 数据 |
| Flutter module | 实验室 UI、客户端 allowlist、即时状态、CMS/Crowdin 内容 | 不把本地开关当成唯一授权 |
| NH client packages | Timeline 等真实业务入口和展示门禁、共享 store 消费 | 不负责设置页原生入口或 CMS 管理 |

生产链路中 NH 通过 Nacos gRPC 访问 PE；IOT 使用其已有 PE client。不要为实验功能新增 NH 与 IOT 的双向依赖。

## 1.1 多客户端支持契约

NH 目录和 PE 用户设置是跨 AI Bird 客户端共享的，客户端是否支持某个功能必须显式建模：

- 支持全部服务端功能：`allowedFeatureKeys=null`。
- 只支持子集：传不可变 allowlist；具体 key 必须以本次产品支持矩阵、目标 release 实际 package 和测试为准，不在 SOP 中写死。
- 不支持实验室：不 bootstrap，且使用空集合防御旧路由。
- list 在 service 层过滤；summary、`latestVisiblePublishTime` 和红点从过滤后的列表重算。
- update 在发 HTTP 前拒绝 allowlist 外的 key。
- tenant/bundle eligibility 仍由 GrowthBook 控制；客户端 allowlist 是版本能力边界，不替代 AB。

新增客户端复用已有 `feature_key` 时，不新增 `lab_feature` 目录行，也不批量创建 PE 用户记录。需要核对的是客户端 allowlist、原生入口、GrowthBook tenant/bundle、品牌内容和 analytics application。

## 2. 三类 key

| 名称 | 示例 | 用途 |
|---|---|---|
| `feature_key` | `name_the_bird` | 目录、用户 JSON、客户端和后端能力统一标识 |
| eligibility key | `ntb_entry_enabled` | GB boolean，控制租户/版本/会员展示和灰度资格 |
| policy key | `lab-name-the-bird-policy` | GB JSON，控制 lifecycle 和真实运行时 |

policy key 必须由 `lab-` + `feature_key` 的 snake_case 转 kebab-case + `-policy` 派生，禁止手填出另一套拼写。

现有映射仅作格式示例：

| feature_key | eligibility key | policy key |
|---|---|---|
| `name_the_bird` | `ntb_entry_enabled` | `lab-name-the-bird-policy` |
| `video_id_coach` | `feeds_identify_button_enabled` | `lab-video-id-coach-policy` |
| `bird_story` | `bird-story-config` | `lab-bird-story-policy` |

## 3. NH `lab_feature`

该表由 Naturehood server 内嵌的 `golang-migrate` 创建和变更结构，新功能记录通过 NineData DML 写入。migration 编号按分支不同：当前 master 生产候选线是 `028_lab_feature` 建表、`030_lab_feature_status_deprecated` 写最终 status 注释；staging 线不要照抄该编号。NH 建表 DDL 不申请 NineData。

```sql
CREATE TABLE IF NOT EXISTS `lab_feature` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `feature_key` VARCHAR(64) NOT NULL COMMENT '实验室功能唯一标识；客户端、业务功能、用户开关均使用此 key',
  `gb_feature_key` VARCHAR(128) NOT NULL COMMENT 'GrowthBook/PE eligibility feature key',
  `feature_type` TINYINT NOT NULL DEFAULT 0 COMMENT '功能类型：0=free，1=premium',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '兼容保留字段，已废弃；实验生命周期和实际功能运行状态以 GrowthBook + PE 最终 policy 为准',
  `crowdin_keys` JSON NOT NULL COMMENT '多语言 key 映射，至少包含 name 和 description',
  `cms_content_key` VARCHAR(128) DEFAULT NULL COMMENT 'CMS lab-feature-content key',
  `sort_order` INT NOT NULL DEFAULT 0 COMMENT '展示排序，数值越小越靠前',
  `publish_time` TIMESTAMP NULL DEFAULT NULL COMMENT '发布时间；用于新功能红点和展示时序',
  `create_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_feature_key` (`feature_key`),
  KEY `idx_status_sort` (`status`,`sort_order`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实验室功能元数据表（naturehood）';
```

新功能 DML 模板：

```sql
-- 执行前确认 NineData 会话时区为 UTC；TIMESTAMP 会按会话时区转换。
INSERT INTO `lab_feature` (
  `feature_key`, `gb_feature_key`, `feature_type`, `status`,
  `crowdin_keys`, `cms_content_key`, `sort_order`, `publish_time`
) VALUES (
  '<feature_key>',
  '<eligibility_key>',
  <0_free_or_1_premium>,
  1,
  JSON_OBJECT(
    'name', 'lab_feature.<feature_key>.name',
    'description', 'lab_feature.<feature_key>.description'
  ),
  '<cms_content_key>',
  <sort_order>,
  '<YYYY-MM-DD HH:MM:SS_UTC>'
);
```

新功能使用普通 `INSERT`，让重复 `feature_key` 直接失败，避免静默覆盖 eligibility、付费类型、Crowdin/CMS key 等已有安全语义。修改已有记录时提交独立、可审计的 UPDATE 任务：先查询 before，明确字段 diff，提供 rollback SQL；如确需改变 `publish_time` 也必须单独评审。

执行后验证：

```sql
SELECT `id`, `feature_key`, `gb_feature_key`, `feature_type`, `status`,
       `crowdin_keys`, `cms_content_key`, `sort_order`, `publish_time`
FROM `lab_feature`
WHERE `feature_key` = '<feature_key>';
```

## 4. PE `user_feature_settings`

该表位于 `personalization_engine` 数据库，PE 没有负责建表的应用 migration，表结构在数据库 Ready 后通过 NineData 只创建一次：

```sql
CREATE TABLE IF NOT EXISTS `user_feature_settings` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
  `user_id` BIGINT NOT NULL COMMENT '用户 ID',
  `setting_group` VARCHAR(64) NOT NULL COMMENT '设置分组；实验室固定为 lab',
  `value` JSON NOT NULL COMMENT '设置 JSON；实验室结构为 features 下按 feature_key 保存 enabled 和 updated_at',
  `create_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_group` (`user_id`,`setting_group`),
  KEY `idx_group_update` (`setting_group`,`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户功能设置通用表';
```

一个用户至多一行 `lab` 数据：

```json
{
  "features": {
    "name_the_bird": {
      "enabled": true,
      "updated_at": "2026-07-06T12:00:00Z"
    },
    "video_id_coach": {
      "enabled": false,
      "updated_at": "2026-07-06T12:05:00Z"
    }
  }
}
```

规则：

- 无记录或缺少某个 feature 节点表示“没有显式选择”，不等于关闭；最终开关状态继承该用户的 policy `runtimeEnabled`。
- 新功能不扫描历史用户，也不修改注册链路。
- ON/OFF 都通过 patch API 修改对应 JSON path；OFF 保存 `enabled=false`。
- 首次进入实验室只读，不写用户行。仅当用户请求的目标值与当前计算状态不同，NH 才调用 PE patch 懒写。
- 默认 ON 后首次关闭写 `enabled=false`；默认 OFF 后首次开启写 `enabled=true`；与当前计算状态相同的请求幂等返回且不 patch。
- loader 把 `enabled=true` 和 `enabled=false` 的 key 分别汇总为字符串数组 `enabledLabFeatureKeys`、`disabledLabFeatureKeys`；无记录时两者都是空数组。
- PE schema 声明必须为：

```yaml
source:
  type: mysql
  name: personalization_engine
```

环境责任：staging-us/staging-eu 的 database/user 当前由 PE overlay 中的 shared-middleware `kind: Database` 创建；prod-us 当前由 PE 仓 Crossplane overlay 创建 App 自有 RDS，`dbName=personalization_engine`，同时依赖 `engineering/crossplane-infra` 为 PE Crossplane role 提供 RDS IAM policy。两种流程都只创建 database/user 或实例，不自动创建 `user_feature_settings` 表；数据库和 NineData datasource Ready 后仍执行 NineData DDL。不要创建到 camera 实例，也不要假设部署 PE 会自动建表。每次以目标环境当前 overlay、Argo health 和 `$cicd-developer` 最新规范为准。

HTTP patch 示例：

```http
POST /user-feature-settings/patch
X-User-ID: <same-as-body-user-id>
X-Caller: naturehood
Content-Type: application/json

{
  "user_id": 123,
  "setting_group": "lab",
  "feature_key": "<feature_key>",
  "enabled": true
}
```

`X-User-ID` 必须与 body 一致。不要在日志或文档中保留真实生产用户 ID。

## 5. GrowthBook policy 与默认状态

policy JSON 的严格结构：

```json
{
  "version": 1,
  "lifecycle": "active",
  "runtimeEnabled": true,
  "allowsNewOptIn": true
}
```

合法 lifecycle 只有 `active`、`paused`、`graduated`、`off`。消费者必须严格解析并 fail closed；实际能力只看 `runtimeEnabled`，不能从 lifecycle 反推。

| 状态 | 环境默认 policy | 显式用户规则 | 实验室表现 | 实际能力 |
|---|---|---|---|---|
| active 默认 OFF | `active,false,true` | OFF=`active,false,true`；ON=`active,true,true` | 符合 eligibility 或已开启用户可见；允许符合资格用户开启 | 仅最终 `runtimeEnabled=true` |
| active 默认 ON | `active,true,true` | OFF=`active,false,true`；ON=`active,true,true` | 符合 eligibility 或默认/显式开启用户可见；允许符合资格用户切换 | 仅最终 `runtimeEnabled=true` |
| paused | `paused,false,false` | 显式 ON=`paused,true,false` | 已开启用户可见且可关闭；禁止新开启 | 已开启用户继续可用 |
| graduated | `graduated,true,false` | 通常不需要 | 开关隐藏 | 常驻功能可用，仍保留原权益 |
| off | `off,false,false` | 删除允许规则 | 开关隐藏 | 全部拒绝并停止副作用 |

Feature 全局和 active 环境 `defaultValue` 使用 active 默认 OFF：

```json
{"version":1,"lifecycle":"active","runtimeEnabled":false,"allowsNewOptIn":true}
```

该值的运行时为 false，但仍允许符合 eligibility 的用户 opt-in；它不是 `off,false,false` lifecycle。feature missing、环境 disabled、解析失败或依赖错误仍由消费者 fail closed。

GrowthBook Feature API 的 force 规则真实结构为 `type:"force"` + `value` 字段；JSON feature 的 `condition` 和 `value` 在 API 中都是 JSON 字符串，不存在名为 `force` 的值字段。API 生成的 `id` 可省略。active 环境的核心规则顺序固定为显式 OFF、显式 ON、环境默认值；如有运行时 hard-deny，放在三条核心规则之前。

```json
{
  "type": "force",
  "enabled": true,
  "condition": "{\"disabledLabFeatureKeys\":{\"$elemMatch\":{\"$eq\":\"<feature_key>\"}}}",
  "value": "{\"version\":1,\"lifecycle\":\"active\",\"runtimeEnabled\":false,\"allowsNewOptIn\":true}"
}
```

```json
{
  "type": "force",
  "enabled": true,
  "condition": "{\"enabledLabFeatureKeys\":{\"$elemMatch\":{\"$eq\":\"<feature_key>\"}}}",
  "value": "{\"version\":1,\"lifecycle\":\"active\",\"runtimeEnabled\":true,\"allowsNewOptIn\":true}"
}
```

默认 ON 的末尾无条件 force：

```json
{
  "type": "force",
  "enabled": true,
  "condition": "{}",
  "value": "{\"version\":1,\"lifecycle\":\"active\",\"runtimeEnabled\":true,\"allowsNewOptIn\":true}"
}
```

默认 OFF 时只把末尾 force 的 `runtimeEnabled` 改为 false。不要通过修改 Feature 全局 `defaultValue` 实现环境默认 ON，否则会改变其他已启用环境的 fallback。

`runtimeEnabled` 是当前用户最终的能力开关：

- 无显式用户节点：末尾环境规则决定默认 ON/OFF，NH 返回同值给客户端。
- 显式 OFF：第一条规则返回 false，覆盖默认 ON。
- 显式 ON：第二条规则返回 true，覆盖默认 OFF。
- 实际 NH guard 和 IOT 副作用只认最终 `runtimeEnabled`。

gray rollback 要求：用户已主动开启后，即使旧 eligibility 灰度不再命中，policy 的用户设置规则仍返回 `runtimeEnabled=true`。因此不要把用户设置规则嵌套在旧 eligibility 条件下。

默认 ON 仍要配置 eligibility。用户默认 ON 时可以关闭；再次开启属于新 opt-in，NH 会要求 `gbEligible=true`。若 eligibility 仍被 kill switch 或默认 false 拒绝，用户关闭后将无法重新开启。

无条件默认 ON 不受 eligibility 人群约束：未被更早 policy 规则拒绝的用户都会得到 `runtimeEnabled=true`，可见性中的 `runtimeEnabled` 也会让开关显示。因此它只适用于“所有受客户端支持的用户默认 ON”。租户、版本或会员限制如果属于运行时硬边界，必须在 policy 中增加更高优先级 hard-deny，并验证 `eligibility=false + 无显式设置` 不会被默认 ON 放行；客户端 allowlist 只是版本能力和 UX 边界，不是服务端授权。

premium 的“向所有人展示”与“只向会员展示”属于 eligibility 规则：

- 向所有人展示：不要在 eligibility 加会员限制；非会员实际使用时仍走原权益/paywall。
- 只向会员展示：用真实会员属性，如 `isUserVip` 或经代码核对后的 `vipTierIds`。
- 不要用 `payType` 代替会员状态；它表示支付方式。
- 新 tier 必须通过公共 entitlement 语义覆盖，不在每个功能散落硬编码 tier ID。

当前 active 可见性为 `platformVersionMatched && (gbEligible || runtimeEnabled)`。这保证用户主动开启后不会因普通灰度回滚而突然丢失开关；也意味着一个历史已开启用户在会员 eligibility 失效后仍可能看到开关，但实际付费能力会被原 entitlement 拒绝。若产品要求“会员失效后立即从实验室隐藏”，必须把它作为单独的 policy/可见性设计评审并补测试，不能假设单改 eligibility 已满足。

## 6. 更新安全规则

1. 调用 `$growthbook` 读取目标 feature 的完整 `environments`。
2. 展示将修改的环境、默认值、规则顺序和条件，获得用户确认。
3. 一次提交完整 environments；不要假设 API 是 merge patch。
4. 只启用授权环境；未授权环境保持原样。
5. 等待最终一致性后重新读取，逐环境比较 enabled、defaultValue 和 rules。
6. `environment.enabled=false` 表示 SDK payload 中不存在该 feature，消费者必须能 fail closed。
7. 条件中的用户标识使用 PE 实际传入的 `userId`，不要写成 `id`。
8. API 全量写回可能重建 rule `id` 和 `definition`。核对未修改环境时忽略这两个平台生成字段，但任何业务字段变化都必须视为异常并回滚。
