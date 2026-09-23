---
name: iot-service-dev-setup
description: 启动 iot-service-unified 后端本地开发环境。当新人入职、AI Agent 需要搭建后端环境、或用户说"启动后端"、"start backend"、"搭建 iot-service 环境"、"本地开发后端"、"run iot-service locally"时触发。即使用户只是提到需要调试后端接口、修改后端代码、或需要本地跑后端服务，也应使用本 Skill。
---

# iot-service-dev-setup

启动 iot-service-unified（Spring Boot 后端）本地开发环境，包含 Docker 依赖服务启动、Maven 构建、应用启动。

## 核心规则

1. **必须使用 JDK 11** — Spring Boot 2.5.5 + Apollo/Guice 在 JDK 17 下会报 `InaccessibleObjectException`
2. **使用 `dev-local` Profile** — 该 profile 已禁用 Apollo 配置中心、排除 Redis 集群模式，适合本地开发
3. **必须设置 `appName` 环境变量** — `CloudBeanCondition` 检查 `System.getenv("appName")` 来决定是否注册核心 Bean
4. **Docker 命令必须在 docker 目录执行** — `cd {iot-service-unified}/docker` 后再运行 `docker compose`
5. **先检测再操作** — 每个步骤先检查组件是否已运行，避免重复启动

## 执行流程

### Step 0: 前置检查

| 依赖 | 检查命令 | 要求 |
|------|---------|------|
| Docker Desktop | `docker info` | 必须已启动 |
| JDK 11 | `java -version` | 必须为 11.x |
| Maven | `mvn -version` | 任意版本 |
| 端口 3306/6379/9092/7777 | `lsof -i :PORT` | 必须空闲 |

如果 JDK 11 未安装：
```bash
brew install openjdk@11
export JAVA_HOME="/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home"
```

### Step 1: 启动 Docker 依赖服务

```bash
cd {iot-service-unified}/docker
docker compose up -d
```

等待所有服务就绪（约 30 秒）：
```bash
docker compose ps  # 确认 4 个服务全部 healthy/running
```

服务清单：

| 服务 | 容器名 | 端口 | 用途 |
|------|--------|------|------|
| MySQL 8.0 | iot-mysql | 3306 | 主数据库，root/root123456 |
| Redis 7 | redis | 6379 | 缓存，standalone 模式 |
| Zookeeper 3.8 | zookeeper | 2181 | 服务协调 |
| Kafka 7.4 (KRaft) | kafka | 9092 | 消息队列 |

### Step 2: Maven 构建

```bash
cd {iot-service-unified}
mvn clean package -DskipTests -pl iot-service-cloud -am
```

`-pl iot-service-cloud -am` 只构建 cloud 模块及其依赖模块，节省时间。

### Step 3: 启动后端服务

```bash
cd {iot-service-unified}
export appName=iot-service-cloud
JAVA_HOME=/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home \
java -jar iot-service-cloud/target/iot-service-cloud-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=dev-local
```

启动成功标志：日志输出 `Started IotServiceCloudApplication`，端口 7777 可访问。

验证：
```bash
curl -s http://localhost:7777/actuator/health
```

### Step 4: 创建测试用户（可选）

注册 API 需要邮箱验证码，本地开发建议直接写入数据库。关键是密码哈希逻辑：

**密码哈希说明**（理解这个才能正确创建用户）：
- App 发送密码前会先做一次 `SHA-256(明文密码)` = `client_hash`
- 服务端收到后再做 `SHA-256(client_hash + salt)` 与数据库比对
- `new_password_flag=1` 时比对字段是 `new_hashed_password`

```bash
# 以密码 Test123456 为例
CLIENT_HASH=$(echo -n "Test123456" | shasum -a 256 | awk '{print $1}')
# = 9a931c55ac02bf216550c464b1992a30c522dfabf6cb31deada5c716bc13a263

NEW_HASHED=$(echo -n "${CLIENT_HASH}" | shasum -a 256 | awk '{print $1}')
# = a0c1d0123a9b9ced263d70b3fb8db895d51c68be0556fb26f019ca8b09a28717

docker exec iot-mysql mysql -u root -proot123456 camera -e "
INSERT INTO user (email, name, hashed_password, new_hashed_password, salt, new_password_flag, internal, tenant_id, type, status)
VALUES ('test@vicohome.io', 'TestUser', '${CLIENT_HASH}', '${NEW_HASHED}', '', 1, 1, 'vicoo', 1, 0);"
```

`internal=1` 标记测试账号，跳过设备信任验证（1FA）。

## 常见问题速查

| 问题 | 原因 | 解决 |
|------|------|------|
| `InaccessibleObjectException` | JDK 版本过高 | 使用 JDK 11 |
| Apollo `connect timed out` | 连接外部 Apollo 配置中心 | 用 `dev-local` profile（已禁用 Apollo） |
| `ERR This instance has cluster support disabled` | Redis standalone 被当集群用 | 用 `dev-local` profile（`@Profile("!dev-local")` 排除集群配置） |
| `No qualifying bean: IExtensionManager` | `CloudBeanCondition` 检查失败 | `export appName=iot-service-cloud` |
| `Table 'camera.xxx' doesn't exist` | 数据库初始化脚本未执行 | `docker compose down -v && docker compose up -d` 重建 |
| 端口 7777 被占用 | 上次进程未退出 | `lsof -i :7777` 找到 PID 并 `kill` |
| 登录返回 `WRONG_PASSWORD` | 密码哈希不对 | 参考 Step 4 的双重哈希说明 |
| 登录要求邮箱验证码 | 设备不在信任列表 | 设置 `internal=1` 跳过 1FA 验证 |

## Examples

### Good — 使用 dev-local profile 并设置环境变量

```bash
export appName=iot-service-cloud
JAVA_HOME=/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home \
java -jar iot-service-cloud/target/iot-service-cloud-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=dev-local
```

### Bad — 使用 local profile（会连接 Apollo 和 Redis 集群）

```bash
java -jar iot-service-cloud/target/iot-service-cloud-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=local
```

### Bad — 忘记设置 appName 环境变量

```bash
# 会报 NoSuchBeanDefinitionException: IExtensionManager
java -jar iot-service-cloud/target/iot-service-cloud-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=dev-local
```

### Bad — 在项目根目录运行 docker compose

```bash
# 错误：必须在 docker 目录下运行
cd iot-service-unified
docker compose up -d
```

## References

- [完整搭建指南](references/full-guide.md) — 包含详细配置说明、Profile 差异对比、数据库 Schema
