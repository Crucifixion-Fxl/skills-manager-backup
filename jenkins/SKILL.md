---
name: jenkins
description: Jenkins CI/CD 流水线管理。查看 Job 构建状态与日志、触发构建、查看构建历史、管理 Job 配置、查看节点状态时使用。当用户提到 Jenkins、构建、Pipeline、Build、流水线、CI 构建失败排查时触发。
---

# Jenkins

Jenkins CI/CD 平台。公司同时保留现代和旧版控制器，操作前必须先确认目标 Job 所属控制器。

> API 用法参考 [官方 Remote Access API 文档](https://www.jenkins.io/doc/book/using/remote-access-api/)，此处只记录公司特有的规则。

## Description

适用场景：查看 Pipeline 构建状态与日志、触发构建、查看构建历史与变更记录、管理 Job 配置、查看 Agent 节点状态。

## Rules

### 连接信息

| 控制器 | 地址 | 已观测版本 | 主要 Job 形态 | 适用范围 |
|------|------|-----------|--------------|----------|
| 现代 | `https://jenkins-next.addx.live` | 2.462.1 | Folder + Pipeline | 新版流水线 |
| 旧版 | `https://jenkins.addx.live` | 2.225 | 顶层 Freestyle/Maven | 尚未迁移的旧服务 |

版本和拓扑是观测值，不是永久契约。每次调查先通过根 API 的 `X-Jenkins` 响应头确认版本，再递归或平铺枚举实际 Job。

**控制器选择顺序：**
1. 优先采用已知 Job URL、历史构建链接或迁移证据指定的控制器。
2. 没有直接证据时，分别对两个根 API 做只读认证查询，不得把 `jenkins-next` 推断为唯一实例。
3. 旧服务若通过参数化 Job 执行 `cicd/jenkins.sh`，必须检查全部同类参数化 Job；只按 Job 名搜索会漏掉共享写入入口。

**认证方式：**
- API 调用：用户名 + 密码，Basic Auth
- 凭据来源：优先使用 `JENKINS_USER` / `JENKINS_TOKEN` 环境变量；需要本地文件时用 `${A4X_PASSWORD_FILE:-$HOME/.codex/password}` 或用户显式指定的凭据文件
- 使用 `curl --config -` 从 stdin 注入 Basic Auth；不要把账号密码放入命令行参数
- HTTPS 使用 nginx 反代，需加 `-k` 跳过证书验证

### Folder 结构（现代控制器）

Job 通过 Folder 插件按业务/环境分组，顶层 4 个 Folder：

| Folder | 用途 |
|--------|------|
| `backend` | 后端服务，下分 staging-us/eu/cn、prod-cn、pr、test 等子 Folder |
| `data` | 数据相关 Job |
| `demo` | 演示/PoC 项目 |
| `safemo` | Safemo 产品线 |

**backend Folder 子目录（按环境）：**

| 子 Folder | 用途 |
|-----------|------|
| `staging-us` / `staging-eu` / `staging-cn` | 各区域 Staging 部署 |
| `prod-cn` | 中国区生产部署 |
| `pr` | PR 构建验证 |
| `test` | 测试 Job |

**API 访问嵌套 Job 时，路径中每层 Folder 用 `/job/` 连接：**
`$JENKINS_URL/job/backend/job/staging-us/job/<JOB_NAME>/api/json`

旧版 `jenkins.addx.live` 以顶层 Job 为主。必须以根 API 的实时枚举结果为准，不能套用现代控制器的 Folder 结构。

### 分支与环境映射

| 环境 | 分支模式 | 说明 |
|------|---------|------|
| Staging | `DEV-STAGE` | 开发/测试环境部署 |
| Production | `release/yyyymmdd` | 生产环境部署，按日期命名 |

上表适用于现代流水线约定。旧版参数化 Job 的应用分支由 `gitbranch` 参数决定，`cicd/jenkins.sh` 另按 `env` 选择 `DEV/CICD` 分支：`test` 使用 `test`，`internal|staging*|pre*` 使用 `staging`，`prod*` 使用 `master`。调查旧服务时必须分别记录应用 SCM 分支和 CICD 脚本分支。

### Agent 节点

26 个节点分布在多个地域，全部在线：

| 地域 | 节点名 | Executors |
|------|--------|-----------|
| 北京办公室 | `bj-31-11`, `bj-31-92`, `bj-office-31-*` | 1-5 |
| 北京 Mac Mini | `bj-office-android-mac-mini`, `bj-office-ios-mac-mini`, `bj-office-mac-mini-31-88` | 3-5 |
| 中国公有云 | `cn-public-jenkins`, `cn-public-jenkins-agent`, `cn-public-jenkins-arm-agent`, `cn-public-jenkins-AI-agent-gpu` | 3-4 |
| 中国其他 | `cn-dev-jenkins-agent`, `cn-jenkins-agent`, `cn-firmware`, `cn-factory-windows-agent` | 3 |
| 美国 | `us-jenkins-agent`, `us-public-jenkins`, `us-gcp-jenkins-agent`, `us-office-data` | 3-4 |
| 新加坡 | `sg-firmware-jenkins-node`, `sg-vicotech-agent` | 3 |
| GCP | `gcp-us-tech-service-jenkins-agent` | 3 |
| 杭州 | `hz-office-KB-App-mac-mini`, `hz-office-safemo-mac-mini` | 3 |

Built-In Node 设为 0 executors（仅调度，不执行构建）。

### API 基础规则

所有 API 请求均需 Basic Auth。只有写操作需要 CSRF Crumb；只读 `GET` 不应获取 Crumb。响应格式支持 `?format=json` 或路径加 `/api/json`。

**获取 Crumb（写操作前必须）：**

先按下文“读取凭据”定义 `jenkins_curl`；只读调查不要执行本段。

```bash
CRUMB=$(jenkins_curl \
  "$JENKINS_URL/crumbIssuer/api/json" \
  | jq -r '.crumb')
CRUMB_FIELD=$(jenkins_curl \
  "$JENKINS_URL/crumbIssuer/api/json" \
  | jq -r '.crumbRequestField')
```

后续写操作加 Header：`-H "$CRUMB_FIELD: $CRUMB"`

### 常用 API 操作

**读取凭据：**

```bash
# 先按 Job 证据选择控制器，不要默认只有 jenkins-next
JENKINS_URL="${JENKINS_URL:?set the verified Jenkins controller URL}"
# 优先使用 JENKINS_USER/JENKINS_TOKEN；否则只解析凭据文件中的 Jenkins 条目。
# 不要输出条目值，也不要扫描或打印其他服务的凭据。
: "${JENKINS_USER:?load the approved Jenkins username}"
: "${JENKINS_TOKEN:?load the approved Jenkins password or token}"
jenkins_curl() {
  printf 'user = "%s:%s"\n' "$JENKINS_USER" "$JENKINS_TOKEN" |
    curl --config - --silent --show-error --insecure "$@"
}
```

**查看所有 Job：**

```bash
jenkins_curl "$JENKINS_URL/api/json?tree=jobs[name,url,color]" | jq '.jobs[]'
```

`color` 字段含义：`blue`=成功, `red`=失败, `yellow`=不稳定, `blue_anime`=构建中, `notbuilt`=未构建, `disabled`=已禁用

**查看 Job 详情（含最近构建）：**

```bash
jenkins_curl "$JENKINS_URL/job/<JOB_NAME>/api/json?tree=name,url,color,lastBuild[number,url,result,timestamp],lastSuccessfulBuild[number],lastFailedBuild[number]" | jq .
```

**查看 Folder 下的 Job（嵌套路径）：**

```bash
# Folder 路径中的 / 替换为 /job/
jenkins_curl "$JENKINS_URL/job/<FOLDER>/job/<JOB_NAME>/api/json" | jq .
```

**查看构建历史：**

```bash
jenkins_curl "$JENKINS_URL/job/<JOB_NAME>/api/json?tree=builds[number,result,timestamp,duration,url]{0,10}" | jq '.builds[]'
```

**查看特定构建详情：**

```bash
jenkins_curl "$JENKINS_URL/job/<JOB_NAME>/<BUILD_NUMBER>/api/json?tree=result,timestamp,duration,changeSets[items[msg,author[fullName]]]" | jq .
```

**查看构建日志（Console Output）：**

```bash
# 全量日志
jenkins_curl "$JENKINS_URL/job/<JOB_NAME>/<BUILD_NUMBER>/consoleText"

# 最近构建日志
jenkins_curl "$JENKINS_URL/job/<JOB_NAME>/lastBuild/consoleText"

# 渐进式日志（适合长日志，从指定字节开始）
jenkins_curl "$JENKINS_URL/job/<JOB_NAME>/<BUILD_NUMBER>/logText/progressiveText?start=0"
```

**触发构建：**

```bash
# 无参数构建
jenkins_curl -X POST -H "$CRUMB_FIELD: $CRUMB" \
  "$JENKINS_URL/job/<JOB_NAME>/build"

# 带参数构建
jenkins_curl -X POST -H "$CRUMB_FIELD: $CRUMB" \
  "$JENKINS_URL/job/<JOB_NAME>/buildWithParameters?BRANCH=DEV-STAGE&ENV=staging"
```

**停止构建：**

```bash
jenkins_curl -X POST -H "$CRUMB_FIELD: $CRUMB" \
  "$JENKINS_URL/job/<JOB_NAME>/<BUILD_NUMBER>/stop"
```

**查看 Job 配置（config.xml）：**

```bash
jenkins_curl "$JENKINS_URL/job/<JOB_NAME>/config.xml"
```

**查看 Pipeline 阶段状态（Blue Ocean API）：**

```bash
# 获取 Pipeline Runs
jenkins_curl "$JENKINS_URL/blue/rest/organizations/jenkins/pipelines/<JOB_NAME>/runs/?latestOnly=true" | jq .

# 获取 Pipeline 各阶段
jenkins_curl "$JENKINS_URL/blue/rest/organizations/jenkins/pipelines/<JOB_NAME>/runs/<RUN_NUMBER>/nodes/" | jq '.[] | {displayName, result, durationInMillis}'
```

**查看节点/Agent 状态：**

```bash
# 所有节点
jenkins_curl "$JENKINS_URL/computer/api/json?tree=computer[displayName,offline,temporarilyOffline,idle,numExecutors]" | jq '.computer[]'

# 特定节点
jenkins_curl "$JENKINS_URL/computer/<NODE_NAME>/api/json" | jq .
```

**查看构建队列：**

```bash
jenkins_curl "$JENKINS_URL/queue/api/json?tree=items[task[name],why,inQueueSince]" | jq '.items[]'
```

### 操作红线

- **生产环境构建（release/* 分支）必须告知用户并等待确认**，禁止静默触发
- **禁止通过 API 删除 Job**，Job 删除需在 Web UI 操作并经团队确认
- **禁止修改生产 Job 的 config.xml**，配置变更需通过 Jenkinsfile + MR 流程
- **停止正在运行的构建前必须确认**，可能影响部署流程

### 常见工作流

**构建状态巡检：** 列出所有 Job → 筛选 `red`/`yellow` 状态 → 查看失败构建日志 → 定位错误原因

**排查构建失败：**

1. 查看 Job 最近构建状态，确认失败构建号
2. 拉取 Console Output 日志
3. 检查 changeSets 确认触发变更
4. 如有 Pipeline 阶段信息，定位失败阶段
5. 分析日志给出修复建议

**手动触发 Staging 部署：**

1. 确认目标 Job 和参数（分支 `DEV-STAGE`）
2. 获取 Crumb
3. 触发 buildWithParameters
4. 轮询构建状态直到完成
5. 输出构建结果

**手动触发生产部署：**

1. 确认 release 分支名（`release/yyyymmdd`）
2. **告知用户影响范围，等待明确确认**
3. 获取 Crumb → 触发构建
4. 轮询构建状态
5. 验证部署结果

### 状态矩阵

| color 值 | 含义 | 处理方式 |
|----------|------|---------|
| `blue` | 最近构建成功 | 正常 |
| `red` | 最近构建失败 | 查日志定位原因 |
| `yellow` | 最近构建不稳定 | 检查测试结果 |
| `blue_anime` | 正在构建中 | 等待完成 |
| `red_anime` | 正在构建（上次失败） | 观察本次是否修复 |
| `notbuilt` | 从未构建 | 检查是否需要触发 |
| `disabled` | Job 已禁用 | 确认是否应启用 |

## Examples

### Bad

```
直接触发 release/20260319 分支的生产构建，未告知用户
-> 违反红线：生产环境构建必须用户确认
```

```
通过 API 修改生产 Job 的 config.xml
-> 违反红线：配置变更需通过 Jenkinsfile + MR 流程
```

### Good

```
1. 查询 my-service Job 最近构建状态，发现 #128 失败（red）
2. 拉取 #128 consoleText，定位到 npm test 阶段失败
3. 检查 changeSets，发现触发 commit 为 abc123
4. 告知用户："构建 #128 因单元测试失败，错误在 test/auth.spec.ts:42，建议检查该 commit"
```

```
用户要求部署生产：
1. 确认 Job 名和参数：deploy-service, BRANCH=release/20260319
2. 向用户说明："将触发 deploy-service 的生产构建，分支 release/20260319，请确认"
3. 用户确认后获取 Crumb → 触发 buildWithParameters
4. 轮询构建状态，每 10 秒检查一次
5. 构建完成，结果 SUCCESS，耗时 3m28s
```
