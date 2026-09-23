---
name: sentry-onboard-issues
description: sentry-onboard Job 失败 / Pod SENTRY_DSN 为空 / cn-prod 504 timeout / TKE 401 pull image 等 sentry 自助接入失败模式。
---

# Playbook：Sentry 自助接入失败

## 症状

按 `workflows/add-sentry.md` 配好 4 个 manifest 后，发现：
- sentry-onboard Job 失败（ImagePullBackOff / vault 报错 / sentry API 报错）
- Job 成功但 Pod `SENTRY_DSN` env 为空 / Pod 启动 `CreateContainerConfigError`
- AWS CN 集群 Pod 启动后调 sentry 504/timeout
- Sentry UI 看不到新 project

## 诊断 + 修复对照表

| 现象 | 根因 | 修复 |
|---|---|---|
| ArgoCD sync failed；Kyverno `require-resources` 拦 `<app>-sentry-onboard` Job | Job container 漏了 `resources.requests/limits.{cpu,memory}` | 重新按 `recipes/sentry/onboard-job-{eks,tke}.yaml.tmpl` 渲染；不要手写删掉 resources / imagePullSecrets / hook-delete-policy |
| Job ImagePullBackOff / `manifest unknown` / `not found` | 镜像路径错 —— 统一规则是 `<local-harbor>/base/sentry-onboard:<tag>`，常见错法是 host 写成了别的集群 Harbor，或漏了 `base/` 段（写成 flat `cicd/sentry-onboard` 或 `cicd/<env>-<region>/sentry-onboard`）| 改成本集群自己的 Harbor host + `/base/sentry-onboard`（查 `references/data/clusters.yaml -> harbor_url`）。base/sentry-onboard 由 DEV/base-images 扇出到**所有**集群（含 staging EKS / eu-prod-data / TKE），Kyverno `require-harbor-image-path` 全 fleet 放行 `base/` |
| **ArgoCD app op=Failed 但 sync=Synced / health=Healthy；failingResource 是 sentry-onboard hook Job `hookPhase=Failed`「Job has reached the specified backoff limit」** | hook Job transient 失败（vault/sentry/网络抖动）后**失败 Job 未被清**（仅 `HookSucceeded` 时失败 Job 不清）→ ArgoCD 每次 resync 重判它 Failed → op 长期 Failed（app 仍可用但显示红） | 模板现用 `hook-delete-policy: HookSucceeded,HookFailed`（失败 Job 终态即清，根治）。存量/未升级 app 即时修：克隆 Job 去 hook 注解重跑确认 onboarding 幂等 OK（`vault login OK`+project exists）→ `kubectl delete job <app>-sentry-onboard -n <ns>` → 触发一次 client-side sync（**别加 ServerSideApply**，app-owned crossplane CR 的 `.spec.deletionPolicy` 会报 field not declared in schema） |
| ArgoCD sync 卡 `Job "<app>-sentry-onboard" is invalid: spec.template: Invalid value ... immutable` | 升级 image tag 时上一个已到终态的 sentry-onboard Job 还残留；Job 的 `spec.template` 不可变，ArgoCD apply 拿不掉旧 Job 改 image | 模板现用 `hook-delete-policy: HookSucceeded,HookFailed`，成功/失败 Job 到终态都即时清，本错误**基本消除**；万一仍遇到（如 hook-delete 未触发），`kubectl delete job <app>-sentry-onboard -n <ns>` 再 sync，ArgoCD 自动以新 image 重建 |
| Job ImagePullBackOff（**TKE cn-main**，401 Unauthorized）| `imagePullSecrets` 漏写（TKE 无 mutation webhook 不自动注入），或 image host 不是 `harbor-cn.addx.live` | TKE 用 `harbor-cn.addx.live/base/sentry-onboard` + 显式 `imagePullSecrets: harbor-registry-secret`（按 `onboard-job-tke.yaml.tmpl`） |
| Job logs `vault login returned 400/403` | Vault JWT/K8s role 没允许 `<app>-sentry-onboard`，或 bound_audiences 错 | 运维查 references/sentry/README.md 第 3 项；ServiceAccount projected token audience 必须 = `https://kubernetes.default.svc`（跟 Vault role `bound_audiences` 严格一致） |
| ArgoCD `SharedResourceWarning`，或多个 app 争 `sentry-onboard-config` / `sentry-dsn` | 旧裸名 Sentry 资源落在共享 namespace；ArgoCD ownership 按 kind+namespace+name 判断 | 新接入必须用 app-scoped 名：`<app>-sentry-onboard-config`、`<app>-sentry-onboard`、`<app>-sentry-dsn`；不要在共享 namespace 继续声明裸名 |
| Job logs `GET /api/0/teams/{org}/{team}/projects/ returned 404` | ConfigMap `TEAM` 在目标 Sentry org 中不存在，或把另一个 org 的 team slug 带过来了 | 先查目标 Sentry 实例的 team 列表，再把 ConfigMap `TEAM` 改成真实 slug；不要用固定 `sentry` 兜底，也不要改 project slug 硬绕 |
| Job logs `slug collision or pre-existing project` | Sentry 已有同 slug project 但 Vault 无 DSN 记录 | 先核验同应用和现有团队；不同应用撞名则停止，不改 APP 或删除项目绕过。共享项目首次接入由已授权 operator 按迁移流程核验首个 active key、ingest 和 staging Vault 归属，用 CAS 初始化 DSN 专用路径，再重跑幂等流程；保留防自动认领保护 |
| Job logs `external deletion detected` | Sentry project 被手工删了但 Vault 还有 DSN 记录 | 停止自动写入，核验删除原因与备份后按批准的恢复/迁移流程处理；不能清 Vault 重跑来绕过保护或制造新 project ID |
| Job 成功但 Pod `SENTRY_DSN` env 为空 | **首先** 查 schema 错配（最常见），再查 workload 引用的 Secret 名是否是 `<app>-sentry-dsn` | 看 ConfigMap.VAULT_PATH_SCHEMA vs ExternalSecret.remoteRef.key 是否对齐：<br>schema=platform → key 应 `{env}/sentry/application/{app}/project`<br>schema=legacy → key 应 `{env}/app/{app}/sentry-dsn`<br>两端不一致 = Job 写新路径 ES 拉旧路径 → ES 报 "secret not found" 或拉空 |
| ExternalSecret READY=False / `ClusterSecretStore ... not found` / `SecretSyncedError` | `secretStoreRef.name` 选错，读的 Vault 跟 sentry-onboard Job 写入的 Vault 不是同一个 | 查 `references/sentry/README.md` 的 `SENTRY_DSN_CSS`；不要按通用 `clusters.yaml -> vault_css` 或 store 名字猜 Vault 域。cn-dev cutover 后普通 app CSS `vault-builder-backend` 指向 builder Vault，若 Job 仍写 ops Vault 必须先提供同实例 DSN CSS |
| Pod 启动 CreateContainerConfigError | Secret 还没生成，或 Job/ES 链路失败 | 先看 ArgoCD hook 结果；若 Job 仍可见再看是否 `Complete`、ES 是否 `Ready=True`、`<app>-sentry-dsn` Secret 是否有 `dsn` key；正常同 sync 短暂抖动应在 2 分钟内恢复 |
| `kubectl annotate externalsecret <app>-sentry-dsn force-sync` 想强制刷 | ES 没及时 refresh | 加 annotation `force-sync=$(date +%s) --overwrite`（运维执行）；或等 refreshInterval=1m 自然到 |
| `dig sentry-us.addx.live` Pod 内返回公网 IP | 集群 CoreDNS 没配 rewrite | 运维补 references/sentry/README.md 第 4 项的 CoreDNS rewrite 规则 |
| C 端应用 cn 区 prod 想接，Job fail 报 brand | 当前 cn-prod 无品牌 relay 域名 | backend / admin 不受影响；mobile-app / web-frontend 暂时只能 us / eu 区 prod。等运维建 `glitch-cn.{brand}.{tld}` ingress |
| **AWS CN EKS target Pod 502/504/timeout** 访问 sentry-cn | 没加 NAT 出口 NodePool 调度 | 在 Job 的 `nodeSelector` 加：<br>589 cn-tech-service: `node-group: nat-egress` + toleration `dedicated=nat-egress`<br>741 cn-prod: `node-group: sentry-egress` + toleration `dedicated=sentry-egress`<br>801 cn-staging: `node-group: nat-outbound` + toleration `dedicated=nat-outbound`<br>801 cn-dev: `node-group: nat-outbound` + toleration `dedicated=nat-outbound`<br>US/EU/TKE 集群不需要 |
| Sentry DSN 域名跟预期不符（C 端 prod 看到 `sentry-us.addx.live`）| Sentry `APP_TYPE` 写错 | 客户端可见的 C 端应用必须用 `mobile-app` / `web-frontend`，prod env 触发域名改写成 `glitch-{region}.{brand}.{tld}`；服务端 backend / 内部 admin **不改写** |

## 为什么不走 <替代方案>

- **手动跑 sentry-onboarding skill** —— **过时**。那个 skill 是老流程（运维手动调 REST API），新应用走 `workflows/add-sentry.md` 自助；已有项目共享/迁移的授权 operator 初始化仍由 `sentry-onboarding` 协调
- **运维直接在 Sentry UI 建 project + 写 Vault DSN** —— 短期可，但 Job 下次 sync 会发现 project 已存在但 Vault 有 DSN → idempotent OK，**但** schema 错配场景下还是会失败。先修 ConfigMap + ES，再让 Job 走正常链路
- **改 ExternalSecret refreshInterval 拉很短**（如 5s）—— 没用。Job 没写完之前 ES refresh 多频繁也拉不到东西
- **跳过 sentry-onboard，应用代码 hardcode DSN** —— 违反硬红线 #2（禁止代码硬编码密钥）

## 参考

- `workflows/add-sentry.md`（自助流程步骤）
- `recipes/sentry/onboard-config-{eks,tke}.yaml.tmpl`（ConfigMap 模板）
- `recipes/sentry/onboard-job-{eks,tke}.yaml.tmpl`（Job 模板）
- `recipes/sentry/onboard-sa.yaml.tmpl`（ServiceAccount 模板）
- `recipes/sentry/externalsecret.yaml.tmpl`（DSN 注入模板）
- `references/vault-paths/platforms.yaml -> platforms.sentry`（platform 定义）
- references/sentry/README.md（运维前置）
