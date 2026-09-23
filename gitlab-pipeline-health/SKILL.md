---
name: gitlab-pipeline-health
description: Use when someone asks to analyze GitLab pipeline duration, failure rate, job-level CI cost, weekly CI health, why CI is slow or red, or wants optimization advice from pipeline/job data. Also when they say 本周 pipeline、CI 耗时、job 失败原因、流水线健康度.
---

# gitlab-pipeline-health

## Description

先拆「长」和「红」，再给根因和建议。失败率 ≠ 提交质量。job 是分析单位。报告给人读：一分钟结论在前，表在后。数字只写进当次报告。改 yaml 用 `gitlab-ci`。

## Rules

按这个顺序判。缺一项就不要给优化建议：

1. **长还是红？** 分开写。不要一个失败率交差。
2. **长：排队还是执行？** 排队占比大 → 容量，才谈 runner。执行才是 20+ min → **不加 runner**，找关键路径最后一个完成的 job。
3. **红：挡不挡合并？** `allow_failure` 另列，不算红。挡合并的再分类，一条 pipeline 一个主因：真测试 / flaky（同 SHA 同 job 先失败后成功）/ 基础设施 / 脚本配置 / 门禁工具。
4. **红 ≠ 提交质量。** 按 MR 去重后，再看非代码（eviction、门禁崩溃、重跑即过）占阻塞失败多少。
5. **根因 → 建议一对一。** 每条建议必须解释这条失败或这段耗时，带 1–2 个 job/pipeline 链接，写谁做、不做会怎样。禁止无根因的建议，禁止无建议的根因。
6. **建议顺序：** 先不改产品代码 → 再缩墙钟 → 最后噪声。禁止：全局 retry、把 retry 扩到 `script_failure`、用 `allow_failure` 刷绿、没看排队就加 runner。infra 只留 `retry.when: runner_system_failure`。

## 输出

Eval 夹具在 `evals/`。默认 GitHub-flavored Markdown，用户没要图就不出图。摘要 ≤12 行。不要把 GraphQL 字段、REST path 或 job 全量清单当正文。

```markdown
# <project> CI 健康 · <窗口 · TZ>

【一分钟结论】
- 长：墙钟卡在哪些 job（执行，不是排队）
- 失败：阻塞失败里非代码占多少；重跑即过几次
- 下一步：只写有根因的动作；适用才写「不要加 runner / 全局 retry / allow_failure」

三张表：执行时长 p50/p90 · 排队 vs 执行 · 失败分类

【根因 → 建议】
| 根因 | 证据 | 建议 | 谁做 | 不做会怎样 |
```

## 取数

窗口：「本周」= 本 ISO 周周一 00:00 UTC → 现在，用 `created_at`，写明时区。拉 job 级时长、排队、失败原因和少量 trace。解释 job 时读**那次 pipeline 的 `sha`（没有再用 `ref`）**上的 `ci_config_path`，不要本地 checkout，也不要用默认分支 yaml 解释 MR job。trace 丢掉 token / JWT / `PRIVATE-TOKEN`。缺 live yaml 就停，不编造 sidecar/cache 原因。Harbor / `engineering/ci-templates` / 跨仓权限不是本仓一个 MR 能做完的。

## 公司陷阱

- `ci_config_path` 指到 `engineering/ci-templates` 且项目 bot 无 Reporter → 创建即 failed、0 job。
- 实例注入的 `global:code-review` 加在测试墙钟后面。红线继续硬挡；限流可 tooling 有限 retry；取数缺口改模板，不要 `allow_failure`。
- DockerHub `:latest` 会被杀掉。Pin Harbor digest。
- 关键路径大头是 e2e 时，问题在测试分层，不是再抠 sidecar。已 pin 的镜像不要当新优化。

有数之后才谈路径过滤、`needs` DAG、取消过时 pipeline、测试分片、缓存、MR 快管线。

## Examples

### Bad

- 先倒 API 字段或 20 个 job 名，没有一分钟结论
- 只报 pipeline 失败率，不拆排队 / 门禁 / 真测试 / 重跑即过
- 用默认分支 yaml 解释 MR job
- 建议全局 retry / allow_failure 刷绿 / 加 runner，且没有排队表
- 把 `allow_failure` 红叉当测试红了
- 把 trace 里的 token 贴进报告

### Good

【一分钟结论】墙钟卡在执行（排队只有数秒，不加 runner）。阻塞失败大头是门禁工具和 eviction，不是提交质量。lint 的 allow_failure 不计入。每条建议绑一条根因 + job 链接。

## References

- `gitlab-ci` — 写/审 `.gitlab-ci.yml`
- [GitLab job `failure_reason`](https://docs.gitlab.com/api/jobs/)
- [Pipelines API](https://docs.gitlab.com/api/pipelines/)
