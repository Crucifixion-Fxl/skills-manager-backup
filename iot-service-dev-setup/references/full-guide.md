# iot-service-unified 本地开发完整指南

## 目录

1. [项目概览](#项目概览)
2. [环境要求](#环境要求)
3. [Docker 服务详解](#docker-服务详解)
4. [Spring Profile 对比](#spring-profile-对比)
5. [关键配置说明](#关键配置说明)
6. [启动步骤详解](#启动步骤详解)
7. [数据库操作](#数据库操作)
8. [密码系统详解](#密码系统详解)
9. [常见问题排查](#常见问题排查)

---

## 项目概览

- **技术栈**：Spring Boot 2.5.5 + MyBatis + ShardingSphere
- **构建工具**：Maven 多模块项目（18+ 模块）
- **Java 版本**：11（maven.compiler.source/target = 11）
- **核心模块**：`iot-service-cloud`（主服务入口）
- **服务端口**：7777（HTTP，dev-local 环境禁用 SSL）

### 模块结构

```
iot-service-unified/
├── pom.xml                          # 父 POM
├── iot-service-cloud/               # 主服务模块（Spring Boot 入口）
│   ├── src/main/resources/
│   │   ├── application.yml          # 主配置
│   │   ├── application-dev-local.yml # 本地开发 profile ★
│   │   ├── application-local.yml    # 本地 profile（需外部依赖）
│   │   └── application-dev.yml      # 开发环境 profile
│   └── target/                      # 构建产物
├── common/                          # 公共模块
├── domain-*/                        # 领域模块
├── site-controller/                 # 控制器模块
└── docker/                          # Docker Compose 配置
    ├── docker-compose.yml
    └── init/                        # 数据库初始化脚本
```

## 环境要求

### JDK 11

Spring Boot 2.5.5 依赖的 Guice 库在 JDK 17+ 下会报 `InaccessibleObjectException`（反射访问被禁止）。

```bash
# macOS (Apple Silicon)
brew install openjdk@11

# 设置 JAVA_HOME
export JAVA_HOME="/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home"

# macOS (Intel)
export JAVA_HOME="/usr/local/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home"

# 验证
$JAVA_HOME/bin/java -version
# openjdk version "11.0.x"
```

### Docker Desktop

确保 Docker Desktop 已启动且分配了足够资源（建议 4GB+ 内存）。

### Maven

```bash
brew install maven
# 或使用项目自带的 mvnw
./mvnw -version
```

## Docker 服务详解

### docker-compose.yml 配置

所有服务使用自定义网络 `iot-network`（subnet: 172.20.0.0/16）。

#### MySQL 8.0

```yaml
image: mysql:8.0
container_name: iot-mysql
ports: ["3306:3306"]
environment:
  MYSQL_ROOT_PASSWORD: root123456
volumes:
  - ./init:/docker-entrypoint-initdb.d  # 自动执行初始化脚本
  - mysql_data:/var/lib/mysql
```

初始化脚本会创建 `camera` 数据库及所有表结构。

#### Redis 7 (Standalone)

```yaml
image: redis:7-alpine
container_name: redis
ports: ["6379:6379"]
command: redis-server --save 60 1 --loglevel warning
```

注意：这是 standalone 模式，不支持集群命令。所以 `dev-local` profile 用 `@Profile("!dev-local")` 排除了 `LettuceRedisConfig`（集群配置类）。

#### Zookeeper 3.8

```yaml
image: zookeeper:3.8
container_name: zookeeper
ports: ["2181:2181"]
environment:
  ZOO_MY_ID: 1
```

#### Kafka 7.4 (KRaft)

```yaml
image: confluentinc/cp-kafka:7.4.0
container_name: kafka
ports: ["9092:9092"]
environment:
  KAFKA_PROCESS_ROLES: broker,controller  # KRaft 模式，不依赖 Zookeeper
  KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://127.0.0.1:9092
```

### 常用 Docker 命令

```bash
cd {iot-service-unified}/docker

docker compose up -d              # 后台启动
docker compose down               # 停止并删除容器
docker compose down -v            # 停止并删除容器 + 数据卷（重置数据）
docker compose ps                 # 查看状态
docker compose logs -f mysql      # 查看 MySQL 日志
docker compose logs -f kafka      # 查看 Kafka 日志
docker compose restart redis      # 重启单个服务
```

## Spring Profile 对比

| 配置项 | `dev-local` | `local` | `dev` |
|--------|------------|---------|-------|
| Apollo 配置中心 | 禁用 | 启用 | 启用 |
| Redis 模式 | Standalone | Cluster | Cluster |
| MySQL 地址 | 127.0.0.1:3306 | 可能是远程 | 远程 |
| SSL | 禁用 | 可能启用 | 启用 |
| 外部依赖 | 无 | Apollo、Redis 集群 | 全部远程 |

**本地开发强烈建议使用 `dev-local`**，这是唯一不需要任何外部依赖的 profile。

## 关键配置说明

### CloudBeanCondition

`common/src/main/java/org/addx/iot/common/condition/CloudBeanCondition.java` 中检查：
```java
System.getenv("appName").equals("iot-service-cloud")
```

如果环境变量 `appName` 不等于 `iot-service-cloud`，核心 Bean（如 `IExtensionManager`）不会注册，导致 `NoSuchBeanDefinitionException`。

### LettuceRedisConfig 的 Profile 排除

```java
@Configuration
@Profile("!dev-local")  // dev-local 环境不加载此类
public class LettuceRedisConfig extends LettuceBaseConfig { ... }
```

`dev-local` 环境使用 `LettuceStandaloneConfig` 替代，连接本地 standalone Redis。

### application-dev-local.yml 核心配置

```yaml
server:
  port: 7777
  ssl:
    enabled: false

spring:
  datasource:
    dynamic:
      primary: camera
      datasource:
        camera:
          url: jdbc:mysql://127.0.0.1:3306/camera
          username: root
          password: root123456

apollo:
  bootstrap:
    enabled: false  # 禁用 Apollo

lettuce:
  businessRedis:
    host: 127.0.0.1
    port: 6379
    database: 0

kafka:
  bootstrap:
    servers: 127.0.0.1:9092
```

## 启动步骤详解

### 1. 检查端口

```bash
# 确认端口空闲
for port in 3306 6379 2181 9092 7777; do
  if lsof -i :$port > /dev/null 2>&1; then
    echo "端口 $port 被占用！"
    lsof -i :$port
  fi
done
```

### 2. 启动 Docker 服务

```bash
cd {iot-service-unified}/docker
docker compose up -d

# 等待 MySQL 就绪（初始化脚本可能需要 20-30 秒）
until docker exec iot-mysql mysql -u root -proot123456 -e "SELECT 1" 2>/dev/null; do
  echo "等待 MySQL 就绪..."
  sleep 2
done
echo "MySQL 就绪"
```

### 3. Maven 构建

```bash
cd {iot-service-unified}

# 只构建 iot-service-cloud 及其依赖
mvn clean package -DskipTests -pl iot-service-cloud -am

# 如果遇到依赖问题，先全量构建一次
mvn clean install -DskipTests
```

### 4. 启动应用

```bash
export appName=iot-service-cloud

JAVA_HOME=/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home \
java -jar iot-service-cloud/target/iot-service-cloud-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=dev-local
```

### 5. 验证

```bash
# 健康检查
curl -s http://localhost:7777/actuator/health | python3 -m json.tool

# 测试 API（应返回 ACCOUNT_NOT_REGISTERED）
curl -s http://localhost:7777/account/login \
  -H "Content-Type: application/json" \
  -d '{"email":"nobody@test.com","password":"test","authVersion":1}' | python3 -m json.tool
```

## 数据库操作

### 连接 MySQL

```bash
# 通过 Docker
docker exec -it iot-mysql mysql -u root -proot123456 camera

# 直接连接
mysql -h 127.0.0.1 -P 3306 -u root -proot123456 camera
```

### 查看表结构

```bash
docker exec iot-mysql mysql -u root -proot123456 camera -e "SHOW TABLES;"
docker exec iot-mysql mysql -u root -proot123456 camera -e "DESCRIBE user;"
```

## 密码系统详解

iot-service 使用双重 SHA-256 哈希机制保护密码：

### 流程图

```
用户输入: "Test123456"
        │
        ▼
[App 端] SHA-256("Test123456")
        = 9a931c55...63 (client_hash)
        │
        ▼ 通过 HTTP 发送到服务端
        │
[服务端] SHA-256(client_hash + salt)
        = a0c1d012...17 (server_hash)
        │
        ▼ 与数据库比较
        │
[数据库] new_hashed_password 字段
```

### authVersion 与密码校验路径

| authVersion | newPasswordFlag | 比对字段 | 哈希方式 |
|-------------|-----------------|---------|---------|
| 0 | 1 | `hashed_password` | `SHA-256(password + "")` |
| 0 | 0 | `hashed_password` | `SHA-256(password + salt)` |
| 1 (新版 App) | - | `new_hashed_password` | `SHA-256(password + salt)` |

### 注册时的存储逻辑（authVersion 新版）

```java
user.setHashedPassword(registerRequest.getPassword());           // client_hash（兼容旧 App）
user.setNewHashedPassword(CalcSalt(registerRequest.getPassword(), salt)); // SHA-256(client_hash + salt)
user.setNewPasswordFlag(1);
user.setSalt(salt);
```

### 手动创建用户

```bash
# 计算密码哈希
PASSWORD="Test123456"
CLIENT_HASH=$(echo -n "$PASSWORD" | shasum -a 256 | awk '{print $1}')
NEW_HASHED=$(echo -n "$CLIENT_HASH" | shasum -a 256 | awk '{print $1}')

# 插入数据库（salt 为空字符串，internal=1 跳过 1FA 验证）
docker exec iot-mysql mysql -u root -proot123456 camera -e "
INSERT INTO user (email, name, hashed_password, new_hashed_password, salt, new_password_flag, internal, tenant_id, type, status)
VALUES ('test@vicohome.io', 'TestUser', '$CLIENT_HASH', '$NEW_HASHED', '', 1, 1, 'vicoo', 1, 0);"
```

### 1FA 设备信任验证

新设备首次登录时，如果 App 发送 `verifyVersion=1`，服务端会检查设备 ID 是否在信任列表中。绕过方式：
- 方式一：`internal=1`（测试账号标记，代码中直接跳过验证）
- 方式二：在 `user_trust_device` 表中添加设备记录

## 常见问题排查

### 1. InaccessibleObjectException

**现象**：启动时报 `Unable to make field private ... accessible`
**原因**：JDK 17+ 的模块系统禁止了反射访问
**解决**：
```bash
# 确认使用 JDK 11
echo $JAVA_HOME
$JAVA_HOME/bin/java -version
```

### 2. Apollo connect timed out

**现象**：启动时卡在 `Connecting to apollo-cn-test.addx.live`
**原因**：`local` profile 启用了 Apollo 但无法连接内网
**解决**：使用 `dev-local` profile（`apollo.bootstrap.enabled: false`）

### 3. Redis cluster 错误

**现象**：`ERR This instance has cluster support disabled`
**原因**：本地 Redis 是 standalone 模式，但加载了集群配置
**解决**：使用 `dev-local` profile（排除 `LettuceRedisConfig`）

### 4. NoSuchBeanDefinitionException: IExtensionManager

**现象**：启动时找不到 `IExtensionManager` Bean
**原因**：`CloudBeanCondition` 检查 `System.getenv("appName")` 失败
**解决**：
```bash
export appName=iot-service-cloud
```

### 5. 数据库表不存在

**现象**：`Table 'camera.xxx' doesn't exist`
**原因**：初始化脚本未正确执行
**解决**：
```bash
cd {iot-service-unified}/docker
docker compose down -v  # 删除数据卷
docker compose up -d    # 重新创建并执行初始化脚本
```

### 6. 端口被占用

```bash
# 查找占用进程
lsof -i :7777
# 终止进程
kill <PID>
```

### 7. Maven 构建失败

```bash
# 清理本地仓库缓存后重试
mvn clean install -DskipTests -U
```

### 8. Kafka 连接失败

**现象**：`Connection to node -1 could not be established`
**原因**：Kafka 尚未完全启动（KRaft 初始化需要时间）
**解决**：等待 10-15 秒后重试，或检查 `docker compose logs kafka`
