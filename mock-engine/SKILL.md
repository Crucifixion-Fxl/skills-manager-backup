---
name: mock-engine
description: Mock Engine 本地开发环境管理。启动/停止 mock 服务、加载测试数据、创建测试场景、排查启动问题时使用。当用户提到"启动 mock"、"mock 环境"、"本地开发环境"、"mock-up"、"测试数据"、"测试场景"时触发。
---

# Mock Engine — 本地开发 Mock 环境管理

通过 Docker Compose 提供统一的 mock 基础设施（MySQL、Redis、Kafka、MQTT、LocalStack、WireMock），支持本地开发和集成测试。

## Description

适用场景：

- **启动/停止 mock 环境**：`make mock-up` 一键拉起所有依赖服务
- **加载测试数据**：base fixtures 自动加载，scenario fixtures 按需加载
- **创建测试场景**：为特定业务流程准备预置数据（SQL + Redis + WireMock stubs）
- **排查启动问题**：容器状态检查、端口占用、数据加载失败排查
- **集成测试**：通过 Java SDK 或 Go SDK 在自动化测试中管理 mock 基础设施

前置条件：

- Docker Compose v2.20+（运行 `docker compose version` 验证）
- 项目根目录存在 `mock/` 目录（包含 docker-compose.yml 和 Makefile）
- 如果没有 `mock/` 目录，从 mock-engine 模板初始化：
  ```bash
  cp -r /path/to/mock-engine/core/templates/mock/ ./mock/
  # 修改 mock/Makefile 中的项目配置（数据库名、密码等）
  ```

## Rules

### 操作流程

**首次初始化：**

```bash
cd mock/
make mock-init    # 下载 compose fragments
```

**启动 Mock 环境：**

```bash
make mock-up      # 启动所有服务 + 等待健康检查 + 加载数据
make mock-status  # 验证所有服务运行正常
```

**启动应用（以 iot-service-unified 为例）：**

```bash
# 需要设置 appName 环境变量
appName=iot-service-cloud mvn spring-boot:run \
  -pl iot-service-cloud \
  -Dspring-boot.run.profiles=dev-local,mock
```

**加载测试场景：**

```bash
make mock-scenario SCENARIO=device-bindflow
# 查看可用场景：
ls fixtures/scenarios/
```

### 多栈共享模式（跨栈 Golden Dataset）

mock-engine 的 SCENARIO 以 SQL + Redis + WireMock 方式加载，适合**单栈**（Java Spring Boot）场景。

**多栈项目**（后端+引擎+App+数据管道 同一套业务规则）推荐改用 `e2e/golden-data/*.json` 模式：
- 一份 JSON fixture，各栈 test runner 独立读取
- 各栈行为漂移立即暴露
- 参考：customer-care 的 `e2e/golden-data/golden_bind_fail.json`（JS engine 13 tests + Python dagster 17 tests 共读）

详见 `testing-strategy` skill 的 "Tier 2：多栈共享 Golden Dataset" 段。

**停止/重置：**

```bash
make mock-down    # 停止服务（保留数据）
make mock-clean   # 停止 + 删除数据卷
make mock-reset   # 完全重置（clean + init + up）
```

### 默认端口映射

| 服务 | 端口 | 环境变量覆盖 |
|------|------|-------------|
| MySQL | 13306 | MOCK_MYSQL_PORT |
| Redis (business) | 16379 | MOCK_REDIS_BUSINESS_PORT |
| Redis (readonly) | 16380 | MOCK_REDIS_READONLY_PORT |
| Redis (event) | 16381 | MOCK_REDIS_EVENT_PORT |
| Kafka | 19092 | MOCK_KAFKA_PORT |
| MQTT | 11883 | MOCK_MQTT_PORT |
| LocalStack | 14566 | MOCK_LOCALSTACK_PORT |
| WireMock | 19999 | MOCK_WIREMOCK_PORT |

### 测试数据管理

**Base Fixtures（自动加载）：** 放在 `mock/fixtures/base/` 下，按文件名排序执行：

```
fixtures/base/
├── 01-seed-users.sql        # INSERT 测试用户
├── 02-seed-devices.sql      # INSERT 测试设备
└── 03-seed-bindings.sql     # INSERT 绑定关系
```

**Scenario Fixtures（按需加载）：** 每个场景一个子目录：

```
fixtures/scenarios/device-bindflow/
├── data.sql                 # MySQL 数据
├── redis-cmds.txt           # Redis CLI 命令（每行一条）
└── stubs-override/          # WireMock stubs 覆盖
```

`redis-cmds.txt` 格式：
```
# 注释行
SET user:1001:token abc123
HSET device:DEV001 status online
EXPIRE user:1001:token 3600
```

**WireMock Stubs：** 放在 `mock/stubs/<service-name>/` 下：

```json
{
  "request": {
    "method": "POST",
    "urlPath": "/stripe/v1/customers"
  },
  "response": {
    "status": 200,
    "jsonBody": { "id": "cus_mock_001" }
  }
}
```

支持高级特性：条件路由（queryParameters/bodyPatterns）、动态模板（response-template）、状态机（scenarioName）、Webhook 回调（serveEventListeners）。

### 常见问题排查

**MySQL 连接失败：**
```bash
docker ps --filter name=mock-mysql
docker logs mock-mysql
docker exec -e MYSQL_PWD=root123456 mock-mysql mysql -uroot -e "SELECT 1"
```

**Bind mount 不生效（Docker-in-Docker）：** SQL 文件没有自动加载到 MySQL：
```bash
docker exec -e MYSQL_PWD=root123456 -i mock-mysql mysql -uroot < mock/sql/init-schema.sql
```

**WireMock stubs 未加载：**
```bash
curl http://localhost:19999/__admin/mappings | python3 -m json.tool
docker cp mock/stubs/. mock-wiremock:/home/wiremock/mappings/
curl -X POST http://localhost:19999/__admin/mappings/reset
```

**Spring Bean 缺失（CloudBeanCondition）：** 确保设置 `appName=iot-service-cloud` 环境变量。

### SDK 集成（自动化测试）

**Java（Testcontainers）：**

```java
// pom.xml: <dependency>com.addx:mock-engine</dependency>
MockInfraManager manager = MockInfraManager.fromProjectMockDir("./mock");
manager.startAll();
Map<String, String> props = manager.getAllProperties();
```

**Go：**

```go
// go.mod: require gitlab.addx.ai/tools/mock-engine/go
infra := mockinfra.New(mockinfra.WithProjectDir("./mock"))
infra.Start(ctx)
defer infra.Stop(ctx)
infra.LoadScenario(ctx, "device-bindflow")
```

## Examples

### Bad Example

```
用户: 帮我启动 mock 环境
AI: 让我直接 docker compose up 启动

docker compose up -d

# 然后直接启动应用
mvn spring-boot:run -pl iot-service-cloud
```

问题：
- 没有进入 `mock/` 目录
- 没有使用 `make mock-init` 初始化 fragments
- 没有使用 `make mock-up`（会跳过健康检查和数据加载）
- 没有设置 `appName` 环境变量和 `mock` profile

### Good Example

```
用户: 帮我启动 mock 环境
AI: 好的，让我检查 mock 目录并启动环境。

cd mock/
# 检查是否已初始化
ls .fragments/Makefile.include 2>/dev/null || make mock-init
# 启动并等待所有服务就绪
make mock-up
# 验证服务状态
make mock-status

# 启动应用（带正确的环境变量和 profile）
cd ..
appName=iot-service-cloud mvn spring-boot:run \
  -pl iot-service-cloud \
  -Dspring-boot.run.profiles=dev-local,mock

# 验证健康检查
curl -s http://localhost:7777/actuator/health
```

### Bad Example

```
用户: 我需要测试设备绑定流程
AI: 让我直接调用绑定 API

curl -X POST http://localhost:7777/device/bindoperation \
  -H "Content-Type: application/json" \
  -d '{"bindCode":"DEV001"}'
```

问题：
- 没有先加载测试场景数据
- 没有提供 Authorization token
- 缺少必要的请求参数

### Good Example

```
用户: 我需要测试设备绑定流程
AI: 好的，先加载绑定测试场景的数据，然后逐步测试。

# 1. 加载设备绑定场景数据
cd mock/
make mock-scenario SCENARIO=device-bindflow

# 2. 先登录获取 token
TOKEN=$(curl -s -X POST http://localhost:7777/account/login \
  -H "Content-Type: application/json" \
  -d '{"email":"testuser1@mock.local","password":"password","loginType":99}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")

# 3. 绑定检查
curl -s -X POST http://localhost:7777/device/bindcheck \
  -H "Content-Type: application/json" \
  -H "Authorization: $TOKEN" \
  -d '{"serialNumber":"MOCK_DEV_UNBIND"}'

# 4. 执行绑定
curl -s -X POST http://localhost:7777/device/bindoperation \
  -H "Content-Type: application/json" \
  -H "Authorization: $TOKEN" \
  -d '{"bindCode":"MOCK_DEV_UNBIND","bindContentSrc":0,"deviceNetType":0,"width":300,"height":300}'
```

## 与 dev-infra 协作

多 worktree 并行开发时，mock-engine 容器命名要避免冲突：
- 共享策略（L1 服务，如 MySQL）：单容器共享 + per-worktree DB name `${PROJECT}_${WORKTREE_ID}`
- 独占策略（L2 服务，如 Prometheus/Grafana）：容器名带 `$WORKTREE_ID` 后缀

参考 `dev-infra` skill 的 Rule 12b 共享容器 per-DB 隔离模式。

## 相关资源

- mock-engine 仓库：`gitlab.addx.ai/tools/mock-engine`
- 设计文档：`mock-engine/docs/specs/2026-03-20-mock-engine-generalization-design.md`
