---
name: device-setting-override
description: 通过 inner-api 在 /deviceMsg/setting 响应中按设备 SN 覆盖任意字段。当用户需要做设备参数 A/B 实验、紧急修复线上设备的某个错误参数、按 SN 灰度新参数、或回滚 setting 字段时使用。当用户提到"覆盖设备 setting"、"按 SN 改 setting"、"deviceMsg/setting 实验"、"setting override"、"修这台设备的某个参数"、"灰度某个参数"、"按 SN 下发不同配置"时触发。
---

# device-setting-override

通过 iot-service-cloud 的 admin API（`/inner-api/setting-override/**`）在 `/deviceMsg/setting` 响应中按设备 SN 做**字段级深合并覆盖**。常用于:

- 按 SN 子集做参数 A/B 实验(如 PIR 灵敏度、码率、灯光阈值等)
- 紧急修复线上某台/某批设备的错误参数(运维 hot-fix)
- 灰度新参数到指定 SN 子集观察后再全量
- 实验结束后回滚 setting 字段

## Description

首次接入/变更扫描与日常认证入口见 [SaaS 接入](references/saas-access.md)；已有平台业务契约与授权门禁仍在本 Skill 维护。

特性后端代码在 `iot-service-cloud/.../service/setting/`,合并语义在 `SettingOverrideMerger`。本 Skill 是运维侧的客户端封装。

### 鉴权 / Auth

两种鉴权模式, 通过 `--auth` 切换。**默认 `--auth=ldap`**; 除非用户明确要求, 一律走 ldap 模式。
**secret 规则 (2026-10-08 修订): 密码与 AK/SK 绝不落盘、绝不打印; 短 TTL token (缺省 3 天, 以响应 expiresInSeconds 为准) 允许 0600 本地缓存。**

#### `--auth=ldap` (默认) — 全自动, 无需手动登录取 token

经 **revenue-sharing 服务** 的 `/skill-api/invoke` 代理调用, 用自己的 LDAP 账号鉴权。
revenue-sharing 服务端用 PaasConfig 的 AK/SK 帮忙做签名, 然后把请求转发到 `--node-name`
指定的目标 iot-service-cloud 节点。运维 / SRE 用自己 LDAP 账号操作即可, 不需要拿到
共享 AK/SK; 每次调用在 revenue-sharing 端有审计日志关联到 LDAP cn。

**token 解析全自动** (`scripts/rs_auth.py`, 三脚本共用), 优先级:

1. 显式环境变量 `SKILL_LDAP_TOKEN` (可用 `--skill-token-env` 改名; 设了就用它, 行为同旧版)
2. 本地缓存 `~/.cache/device-setting-override/token-<rs-env>.json` (0600; 有效期以登录响应 `expiresInSeconds` 为准, 缺省 3 天, 提前 5 分钟视为到期; 到期自动重登换新, 非续期)。**缓存绑定登录主体 ldapCn** —— 配置了 `RS_LDAP_CN` 时仅复用同主体 token, 同机切换账号会自动重登换新 token, 绝不复用前一个账号的
3. **自动登录**: 环境变量 `RS_LDAP_CN` + `RS_LDAP_PASSWORD` 存在时静默登录并写缓存

| 变量 | 说明 | 必需 |
|------|------|------|
| `RS_LDAP_CN` | 你的 LDAP cn (非机密, 可放进 shell profile) | 自动登录时 |
| `RS_LDAP_PASSWORD` | 你的 LDAP 密码 (仅当前会话: `read -s` 交互输入; 绝不写入 profile/history/任何文件) | 自动登录时 |
| `SKILL_LDAP_TOKEN` | 显式 token (优先级最高, 覆盖缓存与自动登录) | 否 |

以上全都没有 → 脚本**降级**: 打印手动登录指引 (一条 curl) 后 exit 3, 不交互、不静默失败。
token 中途过期 (RS 返回 HTTP 401 或 `code=101 未登录`, 实测部署版是后者) → 自动失效缓存、
重新取 token、原请求重试一次。例外: 显式 `SKILL_LDAP_TOKEN` (token_source=env) 不自动重试,
响应原样返回按业务失败处理 (exit 1)。自动登录本身失败 (网络错/密码错) 也 exit 3。

手动登录 (不配自动登录时的备选; token 有效期以响应 `expiresInSeconds` 为准, 缺省 3 天):

```bash
# staging (操作 staging-* 节点)
RS_HOST=https://console-test.addx.live/api
# prod (操作 prod-* 节点; 2026-10-08 实证已部署可用):
RS_HOST=https://revenus-sharing-backend.addx.live

# 密码交互输入, 不进 history / 进程参数; JSON 由 python3 json.dumps 编码
# (printf 直拼遇引号/反斜杠/换行密码会产出非法 JSON), curl 经 stdin 取数:
read -rs IOT_PW
printf '%s\0%s' '<your-cn>' "$IOT_PW" \
  | python3 -c 'import json,sys; a=sys.stdin.read().split("\0"); print(json.dumps({"ldapCn":a[0],"password":a[1]}))' \
  | curl -sS -X POST "$RS_HOST/skill-api/login" \
      -H 'Content-Type: application/json' --data @-
# → {"code":0,"msg":"","data":{"token":"...","ldapCn":"<cn>","expiresInSeconds":259200}}
export SKILL_LDAP_TOKEN='<token-value>'
```

撤销 token (主动登出, 幂等; 直接删缓存文件同理):

```bash
curl -sS -X POST "$RS_HOST/skill-api/logout" -H "Authorization: $SKILL_LDAP_TOKEN"
rm ~/.cache/device-setting-override/token-<rs-env>.json
```

#### `--auth=aksk` (仅在用户明确要求时使用)

直接调 iot-service-cloud, 用 inner-api AK/SK 做 URL 级 HmacSHA1 签名。
默认**不走**这条路径; 仅在用户明确要求"用 aksk 直连"或需绕过 revenue-sharing 代理时使用。

| 变量 | 说明 | 必需 |
|------|------|------|
| `IOT_ADMIN_AK` | inner-api accessKey | 是 |
| `IOT_ADMIN_SK` | inner-api accessSecret (base64) | 是 |

签名算法: HmacSHA1 + base64(web-safe `+→-` `/→_`), 与 Java 端 `OpenApiAuthService.createSignedUrl` 等价。

#### 何时选哪种

默认走 ldap。只有当用户**明确说**要 aksk, 或 ldap 路径明显不适用时, 才切到 aksk。

| 场景 | 选哪种 |
|:--|:--|
| 任何默认场景 (个人临时操作 / 跑批 / CI / cron 都行) | **`--auth=ldap`** (默认) |
| 用户明确要求 "用 aksk 直连" / "绕过 revenue-sharing" | `--auth=aksk` |
| 目标 revenue-sharing 环境 (staging / prod) 暂时不可用 | `--auth=aksk` (作为兜底) |
| 需要在 revenue-sharing 端留下"谁调用过 admin API"的审计 | `--auth=ldap` (token 关联到 LDAP cn) |

### 环境 / Environments

#### aksk 模式 (`--env`)

直接打到对应区域 iot-service-cloud 公网入口。

| `--env` 值 | host |
|:--|:--|
| `staging-us` | `https://api-staging-us.vicohome.io` |
| `staging-eu` | `https://api-staging-eu.vicohome.io` |
| `staging-cn` | `https://api-stage.addx.live` |
| `prod-us` | `https://api-us.addx.live` |
| `prod-eu` | `https://api-eu.vicohome.io` |
| `prod-cn` | `https://api.addx.live` |

#### ldap 模式 (`--rs-env` + `--node-name`)

`--rs-env` 决定走哪一套 revenue-sharing 控制台; `--node-name` 决定服务端把请求转发到哪个
iot-service-cloud 节点 (由后端 `ConsoleServiceRegistry.queryByNodeIdAndServiceId(nodeName, "paas")`
解析)。**两者现在都可省略**——脚本会自动推导: `--node-name` 缺省取 `--env`,
`--rs-env` 缺省从 node-name 环境段推导 (`prod-us` → `prod`)。日常**只给 `--env` 一个旋钮**即可;
显式给出但与推导冲突 → 脚本直接拒绝 (exit 2) 并解释原因, 绝不按错误组合发请求。

| `--rs-env` | revenue-sharing 后端 host | 状态 |
|:--|:--|:--|
| `staging` | `https://console-test.addx.live/api` | ✅ 可用 (console-test 前端, nginx 把 /api/* 转后端); 仅可用于 `--node-name=staging-*` |
| `prod` | `https://revenus-sharing-backend.addx.live` | ✅ 已部署且端到端验收通过 (2026-10-08 实测: login + stats 经 prod RS 代理打 prod-us/eu/cn 三节点全部 result=0, 其 accessKey `revenue-sharing-prod` 已注册三集群 auth 库, PAAS_ACCESS_SECRET 配置正确); 操作 `--node-name=prod-*` 必须用它 |

**⚠️ rs-env 与 node-name 必须同环境 (2026-10-08 事故实证, 已有客户端防呆)**: RS 用**自己部署环境**的 PaasConfig 单一静态 AK/SK 签名 (与 `--node-name` 无关), 而 accessKey 注册表**按集群**存在各集群 auth 服务的库里。`--rs-env=staging` + `--node-name=prod-*` 组合 → 请求正确路由到 prod 集群, 但 staging 的 key 在 prod auth 库不存在 → `HTTP 400 {"code":102,"message":"accessKey无效"}`。这不是凭据配错, 是设计上不存在的能力; 唯一修法是换成同环境的 `--rs-env` (脚本现在会直接拦下这个组合)。

`--node-name` 有效值固定为以下 6 个 (与 `--env` 同名):

| `--node-name` | 含义 |
|:--|:--|
| `staging-cn` | 国内 staging 集群 |
| `staging-eu` | 欧洲 staging 集群 |
| `staging-us` | 美国 staging 集群 |
| `prod-cn` | 国内 prod 集群 |
| `prod-eu` | 欧洲 prod 集群 |
| `prod-us` | 美国 prod 集群 |

### 三个脚本 / Three scripts

- `scripts/upsert.py` — 批量从 xlsx 读 SN + 双组写入(默认 dry-run,`--confirm` 才发请求)
- `scripts/admin_cli.py` — 其它 4 个端点的 CLI:`get` / `stats` / `list` / `delete`
- `scripts/verify.py` — 写入后闭环体检:DB 落库 + 抽样内容 + Thanos `applied`/`failed`/`admin_request` 指标(详见 Rule 8)
- `scripts/rs_auth.py` — 共享鉴权模块(路由防呆 / token 缓存 / 自动登录 / 失效重试); 纯逻辑单测 `scripts/tests/test_rs_auth.py` (`cd scripts && python3 -m pytest tests/ -q`)

## Rules

### Rule 1 — Inviolable Invariant(最高准则)

后端有一条不可违反的最高准则: **覆盖路径任何阶段(DAO / parse / merge / observability)抛异常都不得影响给设备的响应**;失败时回退到合并前的 JSON 备份。任何对 `DeviceSettingService.initSetParameterRequest`、`SettingOverrideMerger`、`SettingOverrideDao`、observability emitter 的改动都必须保留这一不变量。详见 `docs/architecture/setting-override/overview.md` §7。

**对调用方影响:** 即使 admin API 写错了 JSON 形状或 DB 临时挂了,设备拿到的 setting 仍是型号默认,不会被半合并的脏数据污染。所以 admin 端可以放心做实验,失败兜底由后端保证。

### Rule 2 — overrideJson 形状契约

| 项 | 约束 |
|:--|:--|
| 顶层 key | 必须 ⊂ `{name, id, time, value}` (来源 `SettingOverrideValidator.ALLOWED_TOP_LEVEL_KEYS`) |
| `value` 字段 | 若存在必须是 JSON object |
| 序列化大小 | 紧凑序列化后 ≤ **16 KB** (`MAX_OVERRIDE_JSON_BYTES`) |
| 实际改 setting 字段 | 99% 情况都包在 `value` 下 — `$.data.value` 才是真正的 setting 字段 map |

**典型形状:**
```json
{
  "value": {
    "<top-level-setting-key>": {
      "<sub-key>": "<new-value>"
    }
  }
}
```

合并语义是**深合并**(只访问 override 里出现的 key,兄弟节点逐值保留)。详见 `references/architecture.md`。

### Rule 3 — experiment 标签

| 项 | 约束 |
|:--|:--|
| 正则 | `^[a-z0-9_-]{1,64}$` (来源 `SettingOverrideValidator.EXPERIMENT_PATTERN`) |
| 用途 | admin 审计指标按 experiment 分桶 + 后续 list/delete 按 experiment 过滤 |
| 多组实验 | 不同分组用不同 tag(同一前缀 + 不同后缀,如 `pir_sens_2026q2_high` / `pir_sens_2026q2_low`) |

### Rule 4 — 批量上限

| 项 | 上限 |
|:--|:--|
| upsert 单批 items | **1000** (`SettingRepushService.MAX_BATCH_SIZE`) |
| delete 按 SN 列表单批 | **1000** (同上) |
| list 单页 pageSize | 默认 200,硬上限 1000 |
| **AC-2.7 不允许部分成功** | 单批中任 1 个 item 校验失败 → 整批 1000 项被拒 |

### Rule 5 — expireAt 语义

| 值 | 含义 |
|:--|:--|
| `0` | 永不过期(运维兜底:实验/hotfix 结束后用 admin delete 显式清除) |
| `> 当前时间(UTC 秒)` | 到期自动失效,DAO 在 SQL 层过滤过期行 |
| `≤ 当前时间且 ≠ 0` | 校验失败 |

### Rule 6 — 操作三步法(永远先 dry-run + smoke,再全量)

1. **Dry-run 预演** — 不发请求,只打印计划(SN 数 / 批次数 / 最终 experiment tag);确认无误
2. **Smoke 测试** — `--limit-batches 1 --confirm`,只跑 1 批 1000 SN;人工抽 1 个 SN 调 `deviceMsg/setting` 验证字段已生效;再看监控 `setting_override_applied_total` 有增量、`setting_override_failed_total` 无新增
3. **全量铺开** — 加 `--start-batch 1` 跳过 smoke 那批,加 `--qps-sleep 1.0` 降压

任何阶段失败:`out/run-<ts>-<env>/failed/` 下落了完整 payload + response,用 `--start-batch <N>` 续跑。

### Rule 6.5 — `--confirm` 时必须显式选择 `--repush` 或 `--no-repush`

`upsert.py` 在 `--confirm` 触发时强制运维显式选择 repush 策略,二选一互斥(argparse mutually-exclusive group):

| 选择 | repushSetting | 后果 | 适用 |
|:--|:--|:--|:--|
| `--no-repush` | false | 仅写 DB, 设备需等下次主动拉 `/deviceMsg/setting` 才能看到新值(几分钟到几小时);整批 HTTP 往返 ~50-200 ms/批 | 慢实验铺开,启动时间不敏感 |
| `--repush` | true | 写库后服务端串行重发 setting 给每个 SN;单批 5-10 分钟;离线 SN 进 `repushFailedSerialNumbers` 落到 `failed/` | 紧急 hot-fix / 实验需立即开始 |

只给 `--confirm` 不给上述两个 flag → 脚本打印两种选择的对比并退出 (exit code 2),不会静默执行。
Dry-run (无 `--confirm`) 不强制选择,默认按 `--no-repush` 等价行为只打印计划。

设计原因: skills 仓库的安全 validator 禁止脚本使用交互式 stdin (agent-friendly 要求),所以用强制 flag 替代 input prompt 来达到"运维不可能漏选"的效果。

### Rule 7 — 紧急回滚 / 实验结束清理

两种 delete 模式互斥:

- **按 experiment 整批删** (`admin_cli.py delete --experiment <tag> --repush --confirm`)
  - 服务端会删除全量行,但 **repush 名单只覆盖前 1000 个 SN**(MR 2063 已知 P0 缺口)
  - 行数 ≤ 1000 时用这个最简单
- **按 SN 列表删** (推荐用于 >1000 行) — 先 `admin_cli.py list --experiment <tag> --all` 拉到 SN 列表,再 `admin_cli.py delete --sn-file <path> --repush --confirm` 分批删
  - 严格保证"删一批就 repush 一批"

### Rule 8 — 写完必须跑闭环验证(`verify.py`)

任何 `--confirm` 写入(无论 smoke 还是全量)**完成后必须**立刻跑 `scripts/verify.py` 做四维体检, 不达标禁止进入下一步。

```bash
python3 scripts/verify.py --env <env> \
  --experiment <prefix>_sensitive \
  --experiment <prefix>_insensitive \
  --sample-sn <抽 1 个灵敏组 SN> \
  --sample-sn <抽 1 个不灵敏组 SN>
```

verify.py 检查的 4 个维度(任何一项不达标 → exit code 非零 + 高亮 ❌):

| # | 维度 | 数据源 | 不达标 = 什么意思 |
|:--|:--|:--|:--|
| 1 | DB 落库 / experiment 行数 | admin `stats` | 写入根本没成 / 漏批 |
| 2 | 抽样 SN 内容 | admin `get --sn` | 内容串错组 / 没生效 / 已过期 |
| 3 | 设备命中速率 (`setting_override_applied_total` 30m increase + 5m rate) | Thanos | 写完 30 min 还没设备命中 → 设备拉 setting 链路有问题 |
| 4 | 失败计数 (`setting_override_failed_total` 30m increase by stage) | Thanos | **任何 stage 非零 = inviolable invariant 被触发, 必须立即查日志查根因** |
| 5 | admin 写入审计 (`setting_override_admin_request_total{result=...}` 30m increase) | Thanos | 出现 `validation_error` / `server_error` |

具体 metric 名 / tag / PromQL / Thanos URL 模板见 `references/observability.md`。

如果 verify 报 ❌ stage 非零, 立即 grep 日志:
```bash
grep "setting override failed" <log> | grep "stage=<stage>"
```
对应字段 `sn=<sn> stage=<X>` + 完整堆栈,可定位是哪台设备 / 哪一步出错。

### Rule 9 — 输入文件契约(xlsx)

`upsert.py` 从 xlsx 读 SN,契约严格:

- 仅 `.xlsx`(openpyxl 不支持 `.xls`)
- 必须同时存在两个 sheet(默认名 `灵敏组` / `不灵敏组`,可用 `--sensitive-sheet` / `--insensitive-sheet` 改)
- 第 1 行视为表头,**一律跳过**
- **只读第 1 列**(A 列),其它列即使有值也忽略
- A 列为 `None` 或仅空白(strip 后为空)的行直接跳过
- 同 sheet 内重复 SN 自动去重(保留首次);**跨 sheet 不去重**(后写覆盖先写)
- SN 长度 1-64 字符的服务端校验由 `SettingOverrideValidator` 强制;超长 SN 会让所在批的 1000 项**整批被拒**

数值单元格会被强转为字符串(`str(v).strip()`),前导 0 会丢;建议把 SN 列在 Excel 里设为文本格式。

## How to invoke

### 烟雾测试(staging,只跑 1 批)— `--auth=ldap` (默认)

```bash
# 自动登录凭据 (CN 非机密可放 profile; 密码绝不落盘 —— 当前会话交互输入):
export RS_LDAP_CN='<your-cn>'
read -rs RS_LDAP_PASSWORD && export RS_LDAP_PASSWORD   # 不回显、不进 history
# 登录一次后 token 进 0600 缓存 (缺省 3 天, 以响应为准), 期间无需再输密码
# (未配且无缓存时脚本会打印手动登录指引并 exit 3, 按指引 curl 登录即可)

# Dry-run plan (--auth=ldap 是默认值; 只给 --env 一个旋钮,
#  rs-env/node-name 自动推导, 也可显式给但必须同环境)
python3 scripts/upsert.py \
  --env staging-us \
  --input <xlsx-path> \
  --sensitive-json   @<sensitive.json> \
  --insensitive-json @<insensitive.json> \
  --experiment-prefix <prefix> \
  --expire-at 0

# Smoke 1 batch
python3 scripts/upsert.py \
  --env staging-us \
  --input <xlsx-path> \
  --sensitive-json   @<sensitive.json> \
  --insensitive-json @<insensitive.json> \
  --experiment-prefix <prefix> \
  --expire-at 0 \
  --only-group sensitive --limit-batches 1 --no-repush \
  --confirm
```

### 烟雾测试(staging,只跑 1 批)— `--auth=aksk` (仅在用户明确要求时用)

```bash
 export IOT_ADMIN_AK='<accessKey>'
 export IOT_ADMIN_SK='<secret-base64>'

# Dry-run plan
python3 scripts/upsert.py \
  --auth aksk \
  --env staging-us \
  --input <xlsx-path> \
  --sensitive-json   @<sensitive.json> \
  --insensitive-json @<insensitive.json> \
  --experiment-prefix <prefix> \
  --expire-at 0

# Smoke 1 batch
python3 scripts/upsert.py \
  --auth aksk \
  --env staging-us \
  --input <xlsx-path> \
  --sensitive-json   @<sensitive.json> \
  --insensitive-json @<insensitive.json> \
  --experiment-prefix <prefix> \
  --expire-at 0 \
  --only-group sensitive --limit-batches 1 --no-repush \
  --confirm

# Full rollout
python3 scripts/upsert.py \
  --auth aksk \
  --env staging-us \
  --input <xlsx-path> \
  --sensitive-json   @<sensitive.json> \
  --insensitive-json @<insensitive.json> \
  --experiment-prefix <prefix> \
  --expire-at 0 \
  --start-batch 1 --qps-sleep 1.0 --no-repush \
  --confirm
```

### 单台设备 hot-fix(无需 xlsx)

紧急场景(单台或几台设备线上参数错了),直接构造 1 行 SN 列表的 xlsx 调 upsert.py,或者用 admin_cli.py delete + 自己写一段最小调用(参考 `scripts/upsert.py` 中的 `create_signed_url` + `post_batch`)。

### 实验进度抽查 / 监控

```bash
# ldap 模式 (默认; 只给 --env 即可, token 自动解析)
python3 scripts/admin_cli.py --env staging-us stats
python3 scripts/admin_cli.py --env staging-us get --sn <sn>
python3 scripts/admin_cli.py --env staging-us list \
  --experiment <tag> --all --page-size 1000 --out out/sns.jsonl
python3 scripts/admin_cli.py --env prod-us stats   # prod 同理, 自动走 prod RS

# aksk 模式 (仅在用户明确要求时用)
python3 scripts/admin_cli.py --auth aksk --env <env> stats
python3 scripts/admin_cli.py --auth aksk --env <env> get --sn <sn>
```

### 实验结束 / 回滚

```bash
# ≤1000 行场景
python3 scripts/admin_cli.py --env <env> \
  delete --experiment <tag> --repush --confirm

# >1000 行场景(推荐)
python3 scripts/admin_cli.py --env <env> \
  list --experiment <tag> --all --page-size 1000 --out out/sns.jsonl
jq -r .serialNumber out/sns.jsonl > out/sns.txt
python3 scripts/admin_cli.py --env <env> \
  delete --sn-file out/sns.txt --repush --confirm
```

## Examples

### Bad

```
用户:"线上某型号设备 PIR 太敏感了, 给一组 SN 改成低灵敏度"
AI: 直接改 dao/sql 或 prod 配置库
→ 错: 这正是本 Skill 要解决的场景, 走 admin upsert 走 inviolable invariant 保护
```

```
用户:"覆盖 SN xxx 的 setting"
AI: payload 直接传 {"MotionDetection": ...}
→ 顶层 key 不在白名单 {name, id, time, value}, validator 会拒, 整批 1000 项整批失败
```

```
用户:"4w SN 的实验结束了, 整批删一下"
AI: admin_cli.py delete --experiment <tag> --repush
→ 服务端确实会删全量 4w 行, 但 repush 只覆盖前 1000 个 SN; 其余 3.9w 设备会等到下次主动拉 setting 才回到默认。应该用 list --all + delete --sn-file 模式
```

### Good

```
用户:"某型号 PIR 太敏感, 帮 23k SN 改低灵敏度做对照实验"
AI: 1) 读 xlsx 看 SN 数量 → 2) 按 {value: {pirSensor: {...}}} 形状构造 overrideJson → 3) dry-run 看 plan → 4) smoke 1 批 + 抽查 1 个 SN → 5) 全量 + qps-sleep 1.0 → 6) 写入完成后用 admin_cli stats 看活跃数
```

```
用户:"prod 上 SN xxx 的码率被改错了, 紧急修一下"
AI: 1) admin_cli.py get --sn xxx 看当前 override → 2) 构造 {value: {bitrate: <correct>}} → 3) upsert 1 行(--limit-batches 1 + 单 SN xlsx) → 4) 调 deviceMsg/setting 验证生效
```

## References

- `references/architecture.md` — 后端实现关键点速查(merger 语义、Inviolable Invariant、API 端点契约、监控指标)
- `references/pir-sensitivity-experiment.md` — PIR 灵敏度 A/B 实验完整案例(含两组 overrideJson 全文 + 实战命令)
