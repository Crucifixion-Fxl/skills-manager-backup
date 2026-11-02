# DEV/buildbarn SG SYS RBE 三个存量入口 Service：命名单次例外

Work Item: https://gitlab.addx.ai/engineering/skills/-/issues/396
Root Issue: https://gitlab.addx.ai/DEV/k8s/-/work_items/218
Consumer: DEV/buildbarn#4 / #5（!19 / !20，部署候选 `f4820e52`）

这是一个固定的单例，按精确仓库、路径和完整文件 hash 锁定，并带有到期日。它不是新的应用接入路线，也不是调用方可以自行选用的 policy waiver。

## 为什么需要例外

hard-rule #32 要求 Service 名为 `{app}` 或 `{app}-<suffix>`。DEV/buildbarn 的 SG 运行时有 3 个 Service 早于这条检查，自 `dd86251`（2026-10-27）起一直没有改动：

| Service | selector.app | 用途 |
|---|---|---|
| `bb-cache` | `bb-frontend` | ALB Ingress 后端，域名 `bb-cache-sg.addx.live`（Bazel `--remote_cache`） |
| `bb-cache-metrics` | `bb-frontend` | VMServiceScrape `buildbarn-frontend` 的抓取目标 |
| `bb-exec` | `scheduler` | ALB Ingress 后端，域名 `bb-exec-sg.addx.live`（Bazel `--remote_executor`） |

这 3 个名字被 Ingress、VMServiceScrape 和客户端配置引用。改名属于入口迁移，不能放进镜像修复里一起做。`routes-build.yaml` 里没有“已有服务更新镜像”这个 intent，所以 `validate_delta.py` 也不适用。如果不登记例外，任何只改镜像的 MR 都会被这 3 条存量违规挡住。

## 固定范围

只有以下 3 个文件可以跳过名称断言：仓库 origin 为 `gitlab.addx.ai/DEV/buildbarn`，处于 git worktree 中、在下列精确路径上被 **git 跟踪**，并且完整字节与本目录中对应的 fixture 相同。

| 路径 | 完整文件 SHA-256 |
|---|---|
| `k8s/sg-devops/rendered/service-bb-cache.yaml` | `c32aeee80de1a546bed098a189c80aeb75da52c1d804cf534ca9c2c47b708b70` |
| `k8s/sg-devops/rendered/service-bb-cache-metrics.yaml` | `bdbabd82c156361c29e9d9fef79f642943eaabecd6d48cddb1e24590997b7e7d` |
| `k8s/sg-devops/rendered/service-bb-exec.yaml` | `8429c205deb21c0955f9a350a421f446b1e41b3ea3a85e2d1db68732c11c3057` |

本目录中的 3 个 fixture（`sg-buildbarn-service-*.yaml`）与上表文件逐字节相同，作为本 skill 自检时的权威副本。

## 边界

- **只豁免名称断言**：selector 仍必须选中同一扫描集中的某个 workload，其他所有 validator 照常执行。
- 以下情况全部 fail closed：
  - 任何字段、格式或文档数量变化；
  - 换了路径或仓库；
  - 文件是 symlink，或未被 git 跟踪；
  - 副本不在 git worktree 中；
  - Service 改名或改 selector；
  - 超过到期日。
- **到期**：`2026-12-31` 是最后一个有效的 UTC 日期，之后例外自动失效，validator 恢复报错。
- 本例外不放宽 hard-rule #32 的默认规则，不改变其他例外，不授予运行权限，也不授权任何部署。

## 退出策略

1. 先新增符合规范的 Service：`bb-frontend-cache`、`bb-frontend-metrics`、`scheduler-exec`。
2. 把 ALB/Ingress 后端和 VMServiceScrape 切到新 Service，读回 target 健康状态、客户端访问情况和抓取结果。
3. 删除旧 Service，并删除本合同、3 个 fixture 和对应的 validator 常量。

入口迁移需要单独评审。到期前如果还没完成，需要重新评审是否续期，不能直接改日期。
