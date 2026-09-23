---
name: apollo-permission
description: 管理 Apollo 配置中心的用户权限（授权、回收、查询）。覆盖 CN 和 US 两个实例，默认操作 appId=2（iot-camera）。当用户提到 Apollo 权限、Apollo 授权、Apollo 角色管理、给某人开 Apollo 权限、回收 Apollo 权限、查 Apollo 谁有权限，或任何涉及 Apollo Portal 用户/角色/Namespace 权限操作时使用。即使用户只说"给他开个 Apollo 权限"、"查一下谁能改这个配置"也应触发。
---

# Apollo 权限管理

通过 Apollo Portal REST API 管理两个实例的用户权限。

## Description

自动化 Apollo 配置中心 CN/US 双实例的用户权限管理，支持授权、回收、查询操作。默认操作 appId=2（iot-camera），授权规则为 ModifyNamespace 全环境 + ReleaseNamespace 仅 FAT。凭据优先从环境变量或用户明确批准的凭据来源获取；Codex 不读取 `~/.claude/references/available-secrets.md`。禁止在输出中包含明文密码。

### 实例信息

| 实例 | URL | 环境 |
|------|-----|------|
| CN | `http://apollo-cn.addx.live:9070` | DEV, FAT, PRO |
| US | `http://apollo-us.addx.live:9070` | FAT, PRO |

### 认证方式

Apollo Portal 使用 LDAP + Spring Security form login，通过 JSESSIONID cookie 维持会话。

```bash
curl -s -c <cookie_file> -b <cookie_file> \
  -X POST '<PORTAL_URL>/signin' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Origin: <PORTAL_URL>' \
  -H 'Referer: <PORTAL_URL>/signin' \
  -d 'username=<USER>&password=<PASS>' \
  -o /dev/null -w '%{http_code}'
```

成功标志：HTTP 302 + Location 为 `/`。失败时 Location 为 `/signin`。

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

### 授权请求格式

授权 POST 的 body **必须**是纯文本 userId，不是 JSON。

### LDAP 用户首次授权

LDAP 用户未登录过 Apollo 时，授权 API 返回 500 + `EmptyResultDataAccessException`。解决方法：

1. `POST /apps/2/system/master/<userId>` 临时委派 Master（自动创建本地用户记录）
2. `DELETE /apps/2/system/master/<userId>` 立即移除
3. 正常执行授权

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
# 1. 登录 CN
curl -s -c /tmp/apollo-cn.txt -b /tmp/apollo-cn.txt \
  -X POST 'http://apollo-cn.addx.live:9070/signin' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Origin: http://apollo-cn.addx.live:9070' \
  -H 'Referer: http://apollo-cn.addx.live:9070/signin' \
  -d 'username=<USER>&password=<PASS>' -o /dev/null

# 2. 授 ModifyNamespace（app 级，全环境）
curl -s -b /tmp/apollo-cn.txt -X POST \
  'http://apollo-cn.addx.live:9070/apps/2/namespaces/application/roles/ModifyNamespace' \
  -H 'Content-Type: text/plain' -d 'jzhang'

# 3. 授 ReleaseNamespace（仅 FAT）
curl -s -b /tmp/apollo-cn.txt -X POST \
  'http://apollo-cn.addx.live:9070/apps/2/envs/FAT/namespaces/application/roles/ReleaseNamespace' \
  -H 'Content-Type: text/plain' -d 'jzhang'

# 4. 验证
curl -s -b /tmp/apollo-cn.txt \
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
| 授权返回 500 + EmptyResultDataAccessException | 用户未在 Apollo 本地 DB | 用 system/master 临时委派创建记录 |
