---
name: addx-nexus-usage
description: AddX Nexus Repository 使用指南。访问 nexus-sg.addx.live 下载或上传制品、申请上传账号、创建 repository、选择 blob store、排查 Nexus SG 部署和权限问题时使用。
---

# addx-nexus-usage

AddX SG Nexus Repository Manager 使用指南。只记录公司内部地址、权限规则、repository/blob store 约束和常用上传方式；Sonatype Nexus 官方 API 细节按需查官方文档。

## Description

| 项目 | 信息 |
|------|------|
| 服务地址 | `https://nexus-sg.addx.live/` |
| 产品版本 | Sonatype Nexus Repository Manager 3 社区版 |
| 登录方式 | 不支持飞书 SSO；上传账号由负责人发放 |
| 下载权限 | 内网白名单内支持匿名下载 |
| 上传权限 | 找负责人 `华佗（邓力恺）` 申请；CI Job 使用团队账号 |
| 服务仓库 | `https://gitlab.addx.ai/DEV/nexus-sg.git` |
| ArgoCD 仓库 | `https://gitlab.addx.ai/DEV/argocd-apps.git` |

## Rules

### 访问和认证

- Nexus 社区版无法接入飞书 SSO。用户说"无法飞书登录"时，不要按 SSO 问题排查。
- 下载制品默认不需要登录鉴权；如果匿名下载失败，优先检查内网白名单、VPN、CI Runner 网络出口、repository URL 是否正确。
- 上传制品需要账号。个人上传找 `华佗（邓力恺）` 申请；CI Job 必须单独申请团队账号，不要把个人账号用于 CI。
- 凭证只能放在本地环境变量、GitLab CI Variables、Vault 或其他受控密钥系统中；禁止写入代码、Dockerfile、README、脚本或日志。

建议环境变量。新 CI 统一使用 `NEXUS_USER` / `NEXUS_PASSWD`；历史 Java 模板可能使用 `NEXUS_PASS`，遇到旧项目时按现有变量名兼容，不要盲目改动已运行的 CI。

```bash
export NEXUS_URL="https://nexus-sg.addx.live"
export NEXUS_USER="<upload-user>"
export NEXUS_PASSWD="<upload-password>"
```

### Repository 和 blob store 红线

上传制品前必须确认目标 repository：

1. 必须使用自己创建或团队分配的 repository。
2. 不要把不同团队、不同项目的制品混到不相关 repository。
3. 上传目标必须是 hosted repository；proxy/group repository 通常不是上传入口。
4. 新建 repository 时，blob store 必须选择 `s3-*`，禁止选择 `default`。

普通上传账号不一定有 repository 创建权限。没有创建权限时找 `华佗（邓力恺）` 创建或分配 repository，不要为了绕过权限复用公共 repository。

`default` 是 Nexus 本地 PVC 存储，容量受 Kubernetes PVC 限制；`s3-*` 使用 S3 bucket，更适合制品长期存储、容量增长和后续扩展。发现用户准备向 `default` 对应的 repository 上传新制品时，先停下来提醒其改用 `s3-*` blob store 的 repository。

### 匿名下载制品

内网白名单内下载不需要登录鉴权：

```bash
curl -fL -o artifact.tar.gz \
  "${NEXUS_URL}/repository/<repo>/<path>/artifact.tar.gz"
```

### curl 上传制品

Raw hosted repository 的通用上传示例：

```bash
curl -fL \
  -u "${NEXUS_USER}:${NEXUS_PASSWD}" \
  --upload-file ./artifact.tar.gz \
  "${NEXUS_URL}/repository/<your-raw-hosted-repo>/<team>/<project>/<version>/artifact.tar.gz"
```

Maven hosted repository 优先使用 Maven 工具发布，避免只上传 `.jar` 导致 POM、metadata 或坐标信息缺失：

```bash
mvn deploy:deploy-file \
  -Durl="${NEXUS_URL}/repository/<your-maven-hosted-repo>/" \
  -DrepositoryId="<server-id-in-settings>" \
  -DgroupId="com.addx" \
  -DartifactId="my-lib" \
  -Dversion="1.0.0" \
  -Dpackaging="jar" \
  -Dfile="./my-lib-1.0.0.jar" \
  -DpomFile="./pom.xml"
```

如果必须用 curl 操作 Maven repository，只把它当作路径形态示例；正式发布前必须确认 POM、metadata、checksum 和 Maven 坐标完整，否则消费端可能无法解析依赖。

`<server-id-in-settings>` 必须在 Maven `settings.xml` 的 `<servers>` 中配置，凭证从 `NEXUS_USER` / `NEXUS_PASSWD` 注入，不要写进 `pom.xml`。

执行上传前，替换：

- `<your-raw-hosted-repo>` / `<your-maven-hosted-repo>`：自己的 hosted repository 名称。
- `<team>/<project>/<version>`：团队、项目和版本路径。
- 文件名和版本号：必须与实际制品一致。

CI Job 中使用团队账号示例：

```bash
test -n "${CI_COMMIT_TAG}" || { echo "CI_COMMIT_TAG is required; use this job only in tag pipelines"; exit 1; }

curl -fL \
  -u "${NEXUS_USER}:${NEXUS_PASSWD}" \
  --upload-file "${ARTIFACT_PATH}" \
  "${NEXUS_URL}/repository/${NEXUS_REPOSITORY}/${CI_PROJECT_PATH}/${CI_COMMIT_TAG}/${ARTIFACT_NAME}"
```

非 tag pipeline 不要直接复用这个路径模板；需要 snapshot 或 branch 版本时，先明确版本命名规则。

### 部署和配置溯源

Nexus SG 服务由 GitOps 管理：

- `DEV/nexus-sg`：Nexus 服务自身的 Dockerfile、Kubernetes manifests、overlay、Ingress、PVC、S3 bucket 等声明式配置。
- `DEV/argocd-apps`：ArgoCD Application 声明，负责把 `nexus-sg` 对应 overlay 部署到目标集群。

排查部署问题时，先看 ArgoCD Application `nexus-sg-sg-devops` 的 sync/health，再回到 `nexus-sg` 仓库检查期望状态。Nexus UI 内创建的 repository、blob store、账号等运行时配置不等同于 Git 仓库声明，变更前要确认配置来源。

### 常见排查

| 现象 | 优先检查 |
|------|----------|
| 匿名下载 401/403 | 是否在内网白名单/VPN/Runner 允许网络内；URL 是否指向正确 repository |
| 上传 401/403 | 账号是否有目标 repository 上传权限；是否误传 proxy/group repository |
| 上传成功但不符合规范 | repository 是否属于自己或团队；blob store 是否为 `s3-*` |
| CI 上传失败 | 是否使用团队账号；CI 变量是否注入；Runner 是否能访问 `nexus-sg.addx.live` |
| 存储容量问题 | 先确认 repository 使用的 blob store；`default` 走 PVC，`s3-*` 走 S3 |

## Examples

### Bad

```bash
# 把个人账号写进脚本，且上传到不明确的公共 repository
curl -u "alice:plain-password" --upload-file app.tar.gz \
  "https://nexus-sg.addx.live/repository/releases/app.tar.gz"
```

问题：凭证明文落盘，repository 归属不清，未确认 blob store 是否为 `s3-*`。

```text
新建 repository 时选择 blob store: default
```

问题：`default` 使用本地 PVC，不适合作为新制品仓库的默认存储。

### Good

```bash
export NEXUS_URL="https://nexus-sg.addx.live"
export NEXUS_USER="<team-upload-user>"
export NEXUS_PASSWD="<from-secret-store>"

curl -fL \
  -u "${NEXUS_USER}:${NEXUS_PASSWD}" \
  --upload-file ./release.tar.gz \
  "${NEXUS_URL}/repository/team-example-raw/team-example/service-a/1.2.3/release.tar.gz"
```

执行前已确认：

- `team-example-raw` 是团队自己的 hosted repository。
- repository 使用的 blob store 是 `s3-*`。
- CI 使用团队账号，凭证来自受控变量或密钥系统。
