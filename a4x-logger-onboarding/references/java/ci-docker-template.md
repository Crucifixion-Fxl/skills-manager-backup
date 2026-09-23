# CI/Docker 私有 Maven 私服鉴权模板

> **前置条件**:业务服务的 GitLab project 必须能访问 Nexus 私服 `nexus.addx.live`。
> CI 使用 Nexus 用户名/密码(`NEXUS_USER` / `NEXUS_PASS`)通过 `settings.xml` 注入,
> 不走 GitLab Job Token 拉 Maven 包(Nexus 不支持 GitLab Job Token 鉴权)。

> **注意**:`ci/java.yml` 当前没有自动化 `mvn deploy` job(注释说"基础设施就绪后单独加")。
> 1.0.0 release artifact 是**手动 deploy** 到 Nexus 的;后续 minor / patch release 在 deploy job 实装前依然走手动。这不影响业务方消费 SDK —— Nexus 上 1.0.0 是稳定可拉的。

SDK 已发布在内部 Nexus (`nexus.addx.live`,parent `java/pom.xml` `distributionManagement` 指向 `https://nexus.addx.live/nexus/content/repositories/releases`),任何非 SDK 仓库的 build / test / image build
都要配鉴权才能 `mvn compile` / `mvn package` 拉到 SDK 依赖。

## §1 Maven `settings.xml`

CI 通过 `settings.xml` 把凭据注入 Maven。`settings.xml` **不能进 Git**,
只在 CI 运行时或本地 `~/.m2/settings.xml` 存在。

### 完整 `settings.xml` 模板

```xml
<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.2.0
            https://maven.apache.org/xsd/settings-1.2.0.xsd">

  <servers>
    <!-- id 必须与 pom.xml <repository id="..."> / <distributionManagement> 完全一致 -->
    <server>
      <id>a4x-release</id>
      <username>${env.NEXUS_USER}</username>
      <password>${env.NEXUS_PASS}</password>
    </server>
    <server>
      <id>a4x-snapshot</id>
      <username>${env.NEXUS_USER}</username>
      <password>${env.NEXUS_PASS}</password>
    </server>
  </servers>

</settings>
```

`${env.NEXUS_USER}` / `${env.NEXUS_PASS}` 由 GitLab CI Variables 注入,
Maven 在运行时读取环境变量展开,**凭据不以明文写进文件**。

### 业务 `pom.xml` `<repositories>` 块(按需添加)

如果 SDK 已发布到 Nexus,在业务 pom.xml 里声明私服地址,Maven 才知道去哪里找:

```xml
<repositories>
  <repository>
    <id>a4x-release</id>
    <name>a4x-release</name>
    <url>https://nexus.addx.live/nexus/content/repositories/releases</url>
    <releases><enabled>true</enabled></releases>
    <snapshots><enabled>false</enabled></snapshots>
  </repository>
  <repository>
    <id>a4x-snapshot</id>
    <name>a4x-snapshot</name>
    <url>https://nexus.addx.live/nexus/content/repositories/snapshots</url>
    <releases><enabled>false</enabled></releases>
    <snapshots><enabled>true</enabled></snapshots>
  </repository>
</repositories>
```

> **注意**:`<id>` 必须与 `settings.xml` 里的 `<server id>` 完全一致,
> Maven 据此查找凭据。

## §2 GitLab CI `.gitlab-ci.yml`

参考 SDK 自身 `ci/java.yml`(commit `858483e`)的实战配置:

```yaml
stages:
  - test
  - build
  - deploy

# --- 公共变量 ---

variables:
  # Maven 本地仓库放在项目目录内,GitLab cache 才能按 key 命中
  MAVEN_OPTS: "-Dmaven.repo.local=$CI_PROJECT_DIR/.m2/repository"
  # --batch-mode: 无交互; --no-transfer-progress: 省 log; --errors: 报错详情
  MAVEN_CLI_OPTS: "--batch-mode --no-transfer-progress --errors --fail-at-end"

# --- 可复用锚点 ---

.java-base: &java-base
  # 钉死小版本(不用浮动 tag 如 maven:3.9-eclipse-temurin-11):
  # 浮动 tag 不同时间拉到不同 image → build 不可复现;
  # mirror cache 损坏时浮动 tag 无法自愈。
  image: maven:3.9.9-eclipse-temurin-11
  cache:
    key: java-m2
    paths:
      - .m2/repository

# --- test job ---

java-test:
  stage: test
  <<: *java-base
  rules:
    - if: $CI_MERGE_REQUEST_ID
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  script:
    # -s 指定本地 settings.xml(CI 侧由 GitLab File Variable 或 before_script 生成)
    - mvn $MAVEN_CLI_OPTS -s settings.xml test

# --- build image job ---

build-image:
  stage: build
  image:
    name: gcr.io/kaniko-project/executor:v1.23.2-debug
    entrypoint: [""]
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  script:
    - cp /kaniko/.docker-secret/config.json /kaniko/.docker/config.json
    # NEXUS_USER / NEXUS_PASS 通过 --build-arg 传入 Dockerfile builder stage
    - >-
      /kaniko/executor
      --context "${CI_PROJECT_DIR}"
      --dockerfile "Dockerfile"
      --build-arg "NEXUS_USER=${NEXUS_USER}"
      --build-arg "NEXUS_PASS=${NEXUS_PASS}"
      --destination "${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHA}"
      --customPlatform=linux/amd64
      --cache=true
      --cache-repo="${CI_REGISTRY_IMAGE}/cache"
```

**关键决策说明**:

| 点 | 做法 | 原因 |
|---|---|---|
| Maven image 钉小版本 | `maven:3.9.9-eclipse-temurin-11` | 可复现 build;SDK 自身同款版本 |
| `NEXUS_PASS` 走 `--build-arg` | 仅传入 builder stage | runtime 镜像不含凭据(见 §3) |
| M2 cache key `java-m2` | 固定 key | 多 job 共用同一缓存,冷启动只下一次 |
| `--no-transfer-progress` | MAVEN_CLI_OPTS | CI log 不被 download bar 淹没 |

## §3 Dockerfile 多 stage

**核心安全原则**:`settings.xml` / `NEXUS_PASS` 仅存在于 builder stage;
runtime stage 通过 `COPY --from=builder` 只拿 jar 文件,天然不含凭据。

```dockerfile
# ==========================================================
# Stage 1: builder — 带 settings.xml,拉私服,编译打包
# ==========================================================
FROM maven:3.9.9-eclipse-temurin-11 AS builder

# CI 通过 --build-arg 传入 Nexus 凭据
ARG NEXUS_USER=""
ARG NEXUS_PASS=""

WORKDIR /build

# 先 COPY pom.xml,利用 layer cache:依赖未变时跳过 dependency:go-offline
COPY pom.xml ./
# 如果是多模块 reactor,把子模块 pom 也复制进来
# COPY module-a/pom.xml ./module-a/
# COPY module-b/pom.xml ./module-b/

# 生成 settings.xml — 在同一 RUN 使用并立即删除:
# 原因:kaniko / BuildKit 的 layer snapshot 在 RUN 结束时取 diff;
# 如果 settings.xml 在独立 COPY 指令里,cache layer 会永久留在镜像中。
# 在 RUN 内生成 + 用完 + 删除,snapshot 结束时文件不存在,cache 里无残留。
RUN printf '<settings>\n\
  <servers>\n\
    <server><id>a4x-release</id><username>%s</username><password>%s</password></server>\n\
    <server><id>a4x-snapshot</id><username>%s</username><password>%s</password></server>\n\
  </servers>\n\
</settings>\n' \
  "$NEXUS_USER" "$NEXUS_PASS" "$NEXUS_USER" "$NEXUS_PASS" > /tmp/ci-settings.xml \
  && mvn --batch-mode --no-transfer-progress -s /tmp/ci-settings.xml dependency:go-offline \
  && rm -f /tmp/ci-settings.xml

# 复制源码(pom 已在上层,源码变化不影响依赖 layer cache)
COPY src ./src

# 打包;再次生成 settings.xml + 编译 + 删除,同一 RUN
RUN printf '<settings>\n\
  <servers>\n\
    <server><id>a4x-release</id><username>%s</username><password>%s</password></server>\n\
    <server><id>a4x-snapshot</id><username>%s</username><password>%s</password></server>\n\
  </servers>\n\
</settings>\n' \
  "$NEXUS_USER" "$NEXUS_PASS" "$NEXUS_USER" "$NEXUS_PASS" > /tmp/ci-settings.xml \
  && mvn --batch-mode --no-transfer-progress -s /tmp/ci-settings.xml package -DskipTests \
  && rm -f /tmp/ci-settings.xml

# ==========================================================
# Stage 2: runtime — 只含 JRE + jar,无任何凭据
# ==========================================================
FROM eclipse-temurin:11-jre

RUN groupadd -g 1001 appuser && useradd -u 1001 -g appuser appuser

# 只从 builder 拿编译产物,settings.xml / M2 缓存 / 源码全部丢弃
COPY --from=builder /build/target/*.jar /app/app.jar

USER appuser
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
```

**为什么 settings.xml 在 RUN 内生成而不是 `COPY settings.xml`**:

- `COPY settings.xml` 会产生一个独立 layer,kaniko 会把这个含明文密码的 layer
  推到 cache-repo — 任何能读 cache-repo 的人都能拿到 `NEXUS_PASS`。
- 在 RUN 内 `printf ... > /tmp/ci-settings.xml && ... && rm -f /tmp/ci-settings.xml`,
  layer snapshot 结束时文件已不存在,cache layer 里无残留。

## §4 JIB 备选(无 Dockerfile)

`jib-maven-plugin` 让 Maven 直接构建并推送镜像,无需 Docker daemon / Kaniko。
适合 CI 环境限制(无特权容器)或希望跳过 Dockerfile 维护的场景。

### `pom.xml` 中配置 JIB

```xml
<build>
  <plugins>
    <plugin>
      <groupId>com.google.cloud.tools</groupId>
      <artifactId>jib-maven-plugin</artifactId>
      <version>3.4.3</version>
      <configuration>
        <from>
          <!-- 基础镜像与 Dockerfile runtime stage 保持一致 -->
          <image>eclipse-temurin:11-jre</image>
          <!-- 如果基础镜像在私有 registry,配凭据 -->
          <!-- <auth><username>${env.REGISTRY_USER}</username>
                      <password>${env.REGISTRY_PASS}</password></auth> -->
        </from>
        <to>
          <image>${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHA}</image>
          <auth>
            <username>${env.CI_REGISTRY_USER}</username>
            <password>${env.CI_REGISTRY_PASSWORD}</password>
          </auth>
        </to>
        <container>
          <mainClass>com.example.YourMainClass</mainClass>
          <ports><port>8080</port></ports>
          <user>1001:1001</user>
        </container>
      </configuration>
    </plugin>
  </plugins>
</build>
```

### GitLab CI JIB job

```yaml
build-jib:
  stage: build
  image: maven:3.9.9-eclipse-temurin-11
  cache:
    key: java-m2
    paths:
      - .m2/repository
  variables:
    MAVEN_OPTS: "-Dmaven.repo.local=$CI_PROJECT_DIR/.m2/repository"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  script:
    # Nexus 凭据走 settings.xml;镜像 registry 凭据走 pom.xml env 变量
    - mvn --batch-mode --no-transfer-progress -s settings.xml
        jib:build
        -Djib.serialize.Dmaven.test.skip=true
```

**JIB vs Dockerfile/Kaniko 对比**:

| 维度 | JIB | Dockerfile + Kaniko |
|---|---|---|
| Docker daemon | 不需要 | 不需要(Kaniko) |
| Layer 细粒度 | JIB 自动按 dependencies/resources/classes 分 layer | 手动 COPY 控制 |
| 凭据安全 | Maven 进程内,不进 image layer | 需要同层清理(见 §3) |
| Dockerfile 灵活性 | 低(无法 RUN 自定义命令) | 高 |

## §5 本地开发 `~/.m2/settings.xml`

本地开发不走 CI,凭据通过 personal access token 或 Nexus 账户配置。

```xml
<!-- ~/.m2/settings.xml -->
<settings>
  <servers>
    <server>
      <id>a4x-release</id>
      <username>your-nexus-username</username>
      <!-- Nexus 用户名密码,或 GitLab Personal Access Token -->
      <password>your-nexus-password-or-pat</password>
    </server>
    <server>
      <id>a4x-snapshot</id>
      <username>your-nexus-username</username>
      <password>your-nexus-password-or-pat</password>
    </server>
  </servers>
</settings>
```

**本地 vs CI 凭据对照**:

| 场景 | 凭据来源 | 写在哪里 |
|---|---|---|
| 本地开发 | 个人 Nexus 账户或 Personal Access Token | `~/.m2/settings.xml` |
| GitLab CI | GitLab CI Variables(`NEXUS_USER`/`NEXUS_PASS`) | `settings.xml`(运行时生成) |
| Docker 构建 | `--build-arg NEXUS_USER/NEXUS_PASS` | Dockerfile builder stage RUN 内 |

**快速验证本地配置是否生效**:

```bash
mvn --batch-mode -s ~/.m2/settings.xml \
    dependency:resolve \
    -Dartifact=com.a4x.logger:a4x-logger-starter:1.0.0
# 看到 BUILD SUCCESS 且无 401 错误,说明 settings.xml 配置正确
```
