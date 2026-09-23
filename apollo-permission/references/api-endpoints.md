# Apollo Portal REST API Endpoints

所有请求需携带 JSESSIONID cookie（通过 form login 获取）。

## 用户管理

### 搜索用户

```
GET /users?keyword=<keyword>&offset=0&limit=20
Response: [{"userId":"<userId>","name":"<name>","email":"<email>"}, ...]
```

## App 级权限（所有环境生效）

### 查 App Master

```
GET /apps/<appId>/role_users
Response: {"appId":"2","masterUsers":[{"userId":"<user1>"},{"userId":"<user2>"}]}
```

### 查 Namespace 角色（app 级）

```
GET /apps/<appId>/namespaces/<nsName>/role_users
Response: {
  "appId": "2",
  "namespaceName": "application",
  "modifyRoleUsers": [{"userId":"<user1>"}, ...],
  "releaseRoleUsers": [{"userId":"<user2>"}, ...]
}
```

### 授 Namespace 角色（app 级）

```
POST /apps/<appId>/namespaces/<nsName>/roles/ModifyNamespace
POST /apps/<appId>/namespaces/<nsName>/roles/ReleaseNamespace
Content-Type: text/plain
Body: <userId>    ← 注意：纯文本 userId，不是 JSON
```

### 移除 Namespace 角色（app 级）

```
DELETE /apps/<appId>/namespaces/<nsName>/roles/ModifyNamespace?user=<userId>
DELETE /apps/<appId>/namespaces/<nsName>/roles/ReleaseNamespace?user=<userId>
```

## 环境级权限（特定环境生效）

### 查 Namespace 角色（环境级）

```
GET /apps/<appId>/envs/<env>/namespaces/<nsName>/role_users
Response: {
  "env": "FAT",
  "appId": "2",
  "namespaceName": "application",
  "modifyRoleUsers": [{"userId":"<user3>"}],
  "releaseRoleUsers": [{"userId":"<user4>"}, ...]
}
```

### 授 Namespace 角色（环境级）

```
POST /apps/<appId>/envs/<env>/namespaces/<nsName>/roles/ModifyNamespace
POST /apps/<appId>/envs/<env>/namespaces/<nsName>/roles/ReleaseNamespace
Content-Type: text/plain
Body: <userId>    ← 注意：纯文本 userId，不是 JSON
```

### 移除 Namespace 角色（环境级）

```
DELETE /apps/<appId>/envs/<env>/namespaces/<nsName>/roles/ModifyNamespace?user=<userId>
DELETE /apps/<appId>/envs/<env>/namespaces/<nsName>/roles/ReleaseNamespace?user=<userId>
```

## 其他查询

### 列出环境

```
GET /envs
Response: ["DEV","FAT","PRO"]
```

### 列出 Namespace

```
GET /apps/<appId>/envs/<env>/clusters/default/namespaces
```

提取名称：`item.baseInfo.namespaceName`

### 检查 SuperAdmin

```
GET /permissions/root
Response: {"hasPermission":true}
```

## 响应码

| Code | 含义 |
|------|------|
| 200 | 成功 |
| 302 | 重定向（登录结果） |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 409 | 冲突（用户已有该角色，视为成功） |
