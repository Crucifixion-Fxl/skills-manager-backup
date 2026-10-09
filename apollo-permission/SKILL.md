---
name: apollo-permission
description: 管理 Apollo 配置中心的用户权限（授权、回收、查询）。覆盖 CN 和 US 两个实例，默认操作 appId=2（iot-camera）。当用户提到 Apollo 权限、Apollo 授权、Apollo 角色管理、给某人开 Apollo 权限、回收 Apollo 权限、查 Apollo 谁有权限，或任何涉及 Apollo Portal 用户/角色/Namespace 权限操作时使用。即使用户只说"给他开个 Apollo 权限"、"查一下谁能改这个配置"也应触发。
---

# Apollo 权限管理

通过 Apollo Portal REST API 管理两个实例的用户权限。

## Description

首次接入/变更扫描与日常认证入口见 [SaaS 接入](references/saas-access.md)；已有平台业务契约与授权门禁仍在本 Skill 维护。

自动化 Apollo 配置中心 CN/US 双实例的用户权限管理，支持授权、回收、查询操作。默认操作 appId=2（iot-camera），授权规则为 ModifyNamespace 全环境 + ReleaseNamespace 仅 FAT。凭据优先从环境变量或用户明确批准的凭据来源获取；Codex 不读取 `~/.claude/references/available-secrets.md`。禁止在输出中包含明文密码。

### 实例信息

| 实例 | URL | 环境 |
|------|-----|------|
| CN | `http://apollo-cn.addx.live:9070` | DEV, FAT, PRO |
| US | `http://apollo-us.addx.live:9070` | FAT, PRO |

### 认证方式

Apollo Portal 使用 LDAP + Spring Security form login，通过 JSESSIONID cookie 维持会话。

用户在本机安全登录页完成 LDAP form login；密码、MFA 或验证码只在页面输入，不发到聊天。已授权任务可辅助正常导航和登录检测，并通过浏览器私有会话／credential provider 将 JSESSIONID Cookie 经私密 stdin、进程内存或加密 SSH stdin 注入 native API 消费者。保留官方 Portal API 和 Cookie 登录，不将 LDAP 宣称为 Casdoor，也不擅自改用 OpenAPI 管理 Token。凭据禁止落入 argv、Cookie 文件、输出、日志或仓库；未授权不主动代持个人凭据。

声明入口若为 HTTP，先核验实际 TLS 入口、可信本机加密转发或经核验的安全传输方式，再携带密码／Cookie；不能自行猜 HTTPS 域名、端口或替换实例。登录跳转只能记录实际状态；302 跳到主页不能替代服务端当前用户核验。

只读候选先匹配部署版本与认证 profile：`GET /user` 在 Spring Security profile 下从当前 principal 返回 `userId`，姓名／邮箱可能为空，核验预期用户名的精确匹配，不能把 `/users/{userId}` 任意用户档案当成当前调用者。`GET /apps/<appId>/permissions/CreateNamespace` 可按当前 principal 返回 `{hasPermission: boolean}`；false 是有效权限读取，不是请求失败，更不是写入授权。先核验身份再读取目标 App 权限元数据，拒绝重定向到登录页／HTML冒充 JSON；只读验收不读取 Namespace 配置值、不触发发布、角色初始化或业务写。正常认证的 session 生命周期与可能存在的部署自定义用户同步副作用分别记录，不能仅由 GET 或 LDAP 名称推断无副作用。

## Rules

### 业务约定

1. 除非用户明确指定其他 App，**默认操作 appId=2（iot-camera）**
2. "开权限"含义：
   - **修改权（ModifyNamespace）**：app 级授权（所有环境生效）— `POST /apps/2/namespaces/<nsName>/roles/ModifyNamespace`
   - **发布权（ReleaseNamespace）**：**只授 FAT 环境**，不授 PRO — `POST /apps/2/envs/FAT/namespaces/<nsName>/roles/ReleaseNamespace`
3. "回收权限"：移除 app 级 ModifyNamespace + FAT 环境 ReleaseNamespace
4. 除非指定只操作一个实例，**CN 和 US 都执行**，先 CN 后 US

### 权限模型

Apollo 权限分两层：

- **App 级（所有环境）**：
  - 查询：`GET /apps/<appId>/namespaces/<nsName>/role_users`
  - 授权：`POST /apps/<appId>/namespaces/<nsName>/roles/<roleType>` Content-Type: text/plain, body=`userId`
  - 回收：`DELETE /apps/<appId>/namespaces/<nsName>/roles/<roleType>?user=<userId>`
- **环境级（特定环境）**：
  - 查询：`GET /apps/<appId>/envs/<env>/namespaces/<nsName>/role_users`
  - 授权：`POST /apps/<appId>/envs/<env>/namespaces/<nsName>/roles/<roleType>` Content-Type: text/plain, body=`userId`
  - 回收：`DELETE /apps/<appId>/envs/<env>/namespaces/<nsName>/roles/<roleType>?user=<userId>`

| 角色 | 含义 |
|------|------|
| `ModifyNamespace` | 修改配置项 |
| `ReleaseNamespace` | 发布配置 |
| `Master` | App 管理员 |

### 私密 native 请求与清理

Cookie 仅由已授权安全注入器提供；配置 stdin 示例必须使用 shell builtin `printf`，拒绝 CR/LF 并正确转义引号／反斜杠，禁止管道日志、debug/verbose 和 xtrace。固定已核验实例与允许路径，不跨域跟随重定向。HTTP 声明入口的安全传输未核验时，不提交凭据或执行 native 会话请求。任务结束只清理自己的临时内存、SSH资源和独立浏览器 profile，记录实际退出响应，不影响已有会话或假定所有会话均撤销。

### 授权请求格式

授权 POST 的 body **必须**是纯文本 userId，不是 JSON。

### LDAP 用户首次授权

LDAP 用户未在目标实例完成正常登录或用户目录尚未同步时，授权 API 可能返回 `EmptyResultDataAccessException`。先核验版本、目录和用户存在性，由用户完成正常首次登录，或让平台 owner 提供明确的初始化流程。禁止将临时委派 Master 再回收用于只读探针、用户初始化或普通授权的自动 fallback：授予 Master 本身扩大权限，后续回收也不能抹去该副作用。不得因授权失败擅自调用 `/system/master` 或角色初始化接口。

### 操作流程

**授权**：搜索用户 → 查当前权限 → 执行授权（CN+US）→ 验证 → 表格汇总

**回收**：查当前权限 → 执行回收（CN+US）→ 验证 → 表格汇总

**查询**：同时展示 app 级 + 各环境级权限

### API 参考

详细端点见 `references/api-endpoints.md`。

## Examples

### Good Example

用户说"给 jzhang 在 application namespace 开权限"：

```bash
# 1. 使用已核验的安全登录页完成 CN 登录；已授权任务私密注入 APOLLO_SESSION_COOKIE
# 禁用 xtrace/verbose；以下只展示 API 契约，真实消费者在进程内构造 Cookie Header

# 2. 授 ModifyNamespace（app 级，全环境）
printf 'header = "Cookie: %s"\n' "$APOLLO_SESSION_COOKIE" | curl -s --config - -X POST \
  'http://apollo-cn.addx.live:9070/apps/2/namespaces/application/roles/ModifyNamespace' \
  -H 'Content-Type: text/plain' -d 'jzhang'

# 3. 授 ReleaseNamespace（仅 FAT）
printf 'header = "Cookie: %s"\n' "$APOLLO_SESSION_COOKIE" | curl -s --config - -X POST \
  'http://apollo-cn.addx.live:9070/apps/2/envs/FAT/namespaces/application/roles/ReleaseNamespace' \
  -H 'Content-Type: text/plain' -d 'jzhang'

# 4. 验证
printf 'header = "Cookie: %s"\n' "$APOLLO_SESSION_COOKIE" | curl -s --config - \
  'http://apollo-cn.addx.live:9070/apps/2/namespaces/application/role_users'

# 5. 对 US 重复步骤 1-4
```

输出格式：

```
| 实例 | Namespace | 用户 | 操作 | 角色 | 范围 | 结果 |
|------|-----------|------|------|------|------|------|
| CN | application | jzhang | 授权 | ModifyNamespace | 全环境 | 成功 |
| CN | application | jzhang | 授权 | ReleaseNamespace | FAT | 成功 |
| US | application | jzhang | 授权 | ModifyNamespace | 全环境 | 成功 |
| US | application | jzhang | 授权 | ReleaseNamespace | FAT | 成功 |
```

### Bad Example

```bash
# ❌ 错误 1：用 JSON 格式发 body（会返回 500）
curl -X POST '.../roles/ModifyNamespace' \
  -H 'Content-Type: application/json' -d '"jzhang"'

# ❌ 错误 2：给 PRO 也加了发布权（违反业务规则）
curl -X POST '.../apps/2/envs/PRO/namespaces/application/roles/ReleaseNamespace' \
  -H 'Content-Type: text/plain' -d 'jzhang'

# ❌ 错误 3：只操作了 CN 没操作 US（默认应双实例同步）

# ❌ 错误 4：没有先验证用户是否存在就直接授权
```

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| 登录 302 → /signin | 密码错误或 LDAP 不可达 | 检查凭据、确认 LDAP 连通 |
| API 返回 401/403 | session 过期 | 重新登录 |
| 用户搜索无结果 | 用户未在 LDAP 中 | 确认 userId（通常是姓名拼音缩写） |
| 授权返回 409 | 用户已有该角色 | 跳过，不是错误 |
| 授权返回 500 + EmptyResultDataAccessException | 用户目录／实例版本需核验 | 走正常首次登录或 owner 明确初始化流程，禁止自动临时 Master fallback |
