# 宿主配置

<a id="runtime-bootstrap"></a>
## 运行时启动

优先使用宿主提供的具名 Audience 操作。Skill 根目录是当前 `SKILL.md` 所在目录。
完整安装的 `skills/audience-sync` 或完整 Skill clone 均可用 Python 3.9+ 运行随附适配器，
无需另行 Git clone；分发时必须保留根目录下的 `scripts/`、`src/` 和 `contracts/`：

```bash
python3 --version
python3 /absolute/path/to/skills/audience-sync/scripts/api.py summarize_project_keys
```

适配器只依赖 Python 标准库，无需安装运行时 pip 包，也无需另写客户端。
仅含语义文件的宿主包通过原生工具执行，不包含脚本。若两种传输方式都不可用，
明确指出缺少 Python 运行时、完整 Skill 文件或原生工具注册，让宿主补齐。
以下相对路径命令从 Skill 根目录执行；脚本绝对路径可在任意工作目录使用。

规范操作名可能与已安装的原生工具名不同。例如，规范 `get_project_sync_capabilities` 对应原生
`get_sync_capabilities`，两者使用同一 Project 的
`/api/platform/v3/projects/{project_id}/audience-sync/capabilities` 端点。
选择固定端点和 schema 匹配的已安装具名工具；使用宿主传输，不要自行构造原始 HTTP 客户端。

<a id="project-bootstrap"></a>
## Project 启动

确定凭据缺失或无效时，只返回这个引导网址：
https://micro-app-platform-us.addx.live/audience-sync-us。
传输失败、HTTP 503、HTTP 403 和 Project binding 不匹配并不能证明凭据缺失。
若另一把已提供 key 验证成功，不要笼统提示缺少 key。这个固定入口不是圈定人群结果网址。

使用适配器内置的生产地址 `https://audience-workflow-api-prod-us.addx.live`。
用户提供 key，无需选择环境。

1. 使用 Project Personal key（`awpk_v2`）。其他类型的凭据不是 Project key，即使另一个本地校验器接受其格式。
2. 调用 `get_project_personal_key_context` 验证 key 对应的 Project 和权限。
   若明确提供了多把 key，用 `summarize_project_keys` 分别验证，报告所有结果，
   再选择与目标 Project 和操作匹配的已验证别名。这些只读检查无需额外许可；无效条目不阻止其他检查。
3. 读取 `get_query_capabilities` 和 `get_project_sync_capabilities`，再按
   [Project 圈人查询](project-query.md)完成预览、物化和同步。

```bash
python3 scripts/preflight.py --personal-key
python3 scripts/api.py get_project_personal_key_context --request-stdin <<'REQUEST'
{"path":{},"query":{},"body":{}}
REQUEST
python3 scripts/api.py summarize_project_keys
```

Preflight 是**离线**检查，只报告 `credential_family`、`api_contract_revision`、
`validation=offline` 和 `authentication=not_attempted`。
只有成功的在线响应才证明所提供凭据通过认证。

Self-context 为 `GET /api/platform/v3/personal-key`，不带路径选择参数、query 或 body。
返回 `project_id`、`binding_revision`、`credential_profile` 和排序去重的 `allowed_actions`。
权限配置包括 `audience_sync` 和 `user_research`；按每把 key 分别报告其实际操作权限。
Research 权限可能包含本 Skill 操作清单以外的操作。在线能力标志决定当前哪些效果可执行。

Project CLI 调用可以通过同一 key 的 self-context 补齐省略的 `path.project_id`。
若 self-context 不可用，用户或可信宿主已提供的确切 Project 可支持显式 binding 的单 key 执行。
将 `AUDIENCE_PROJECT_ID` 设为该值，并在 `path` 传入相同的 `project_id`。
不要从 key 或业务标签猜测 Project。客户端会验证后续响应中的 Project 和 binding revision。

<a id="secure-configuration"></a>
## 安全配置

| 宿主输入 | 含义 |
| --- | --- |
| `AUDIENCE_SYNC_API_KEY` | 单把注入的 key |
| `AUDIENCE_SYNC_API_KEYS` | 替代输入：本地别名到 key 的 JSON 映射，最多 8 项 / 16 KiB |
| `AUDIENCE_PROJECT_ID` | 可选的可信 Project binding，约束每把所选 key |
| `AUDIENCE_PLATFORM_TIMEOUT_SECONDS` | Project 请求超时，默认及最大值均为 45 秒 |
| `AUDIENCE_SYNC_CONTRACT_REVISION` | 可选的匹配 API revision 覆盖值；Project 操作使用 `audience-project-v3` |

宿主可以将用户明确提供的具名环境变量中的凭据，复制到上述任一专用输入。
这是有明确边界的输入集合，不是扫描无关密钥。凭据值留在进程环境中，
不要出现在对话、argv、请求 JSON 或 Skill checkout 中。只使用一种输入模式。
映射别名匹配 `[a-z][a-z0-9_-]{0,31}`，且不能以 `awpk_` 开头；重复别名和非字符串值属于配置错误。
格式错误的 key 字符串分别失败。

```bash
python3 scripts/api.py summarize_project_keys
python3 scripts/api.py get_query_capabilities --key-alias research --request-stdin <<'REQUEST'
{"path":{},"query":{},"body":{}}
REQUEST
```

映射模式执行要求 `--key-alias`，即使映射只有一项。Agent 选择合适的已验证别名；
只有业务目标仍不明确时才询问用户。别名仅在本地使用。
每次操作及其精确请求回读使用同一 key 和 Project；不同 key 的权限不能合并。
超时或效果不明确时，保留所选 key。

汇总输出为 `{"keys":[...]}`。每项包含 `alias`、`status`，以及已验证的 `context`
或 `error`（可带数值型 `http_status`）。状态包括 `verified`、`invalid`（HTTP 401）、
`unavailable`（传输失败、503 或缺少 self-context）和 `error`（其他失败）。
汇总中的 `ok=true` 表示检查完成，可能仍含失败条目。

<a id="errors-and-recovery"></a>
## 错误与恢复

| 结果 | 含义与后续处理 |
| --- | --- |
| `audience_sync_request_failed`，HTTP 404 | 请求的路由未提供服务；不能据此认定 key 无效。检查操作与 API 类型。 |
| `personal_key_invalid`，HTTP 401 | 此请求使用的 key 被拒绝；继续只读验证其他已提供 key。 |
| `key_context_unavailable` | 发现能力不可用；使用已有可信 Project binding 执行单 key 调用，或报告缺少的发现依赖。 |
| HTTP 503 / `transport_error` | 服务或传输不可用；保留所选 key 和精确请求证据以便恢复。 |
| `project_binding_mismatch` | 配置或请求中的 Project 与认证上下文不一致；修正该 binding。 |
| `key_alias_required` / `unknown_key_alias` | 选择与用户目标匹配的已配置别名。 |
| `ambiguous_sync_api_keys` | 配置单 key 或映射输入中的一种。 |
| `invalid_sync_api_key` | 检查 key 类型与本地格式；离线格式检查不代表认证。 |

适配器返回单个 JSON 对象：成功为 `{"ok":true,"result":...}`，失败为
`{"ok":false,"error":"<safe_code>"}`，仅在有响应状态时附加 `http_status`。
失败退出码为 1，参数错误退出码为 2。不返回原始响应体和凭据。
恢复效果时保留原始 idempotency_key、精确请求载荷及返回的请求 ID；参见[错误与恢复](errors-and-recovery.md)。

<a id="transport-contract"></a>
## 传输契约

使用具名宿主操作或已安装的 `scripts/api.py`，通过 `--request-stdin` 传入单个有大小限制的
`{path, query, body}` 对象。所有 `query` 值必须为字符串，包括数值参数（例如 `{"limit":"100"}`）。
`body` 值保留 schema 定义的 JSON 类型。非字符串 query 值会在任何 self-context 查询或 API 请求前，
返回本地 `invalid_query_parameters` 和修正提示。HTTPS 请求发送
`Authorization: Bearer <AUDIENCE_SYNC_API_KEY>`、`Accept: application/json` 和
`Content-Type: application/json`。适配器保留固定操作路由、封闭 schema 与响应 binding，并拒绝重定向。
受控的宿主和测试集成可以覆盖 `AUDIENCE_PLATFORM_BASE_URL`。

按[平台 API](platform-api.md)报告聚合字段与已验证的 Platform 返回网址。
Platform 负责目标解析、服务商凭据、成员数据和效果就绪状态。
