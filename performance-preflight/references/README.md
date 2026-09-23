# References Index — performance-preflight skill
# 参考文档索引 — performance-preflight skill

This directory is the **operational manual** for Step 3 ("Inventory existing signals") of the 8-step workflow. Every file here exists to **eliminate re-discovery** — endpoints, conventions, current magnitudes, and copy-paste-ready snippets are pre-computed so a future query lands on the answer in 1 step instead of 5.
本目录是 8 步法 Step 3（"盘点路径上现有的可观测信号"）的**操作手册**。本目录每个文件的存在都是为了**消除重复发现**——端点、命名约定、当前数量级、可直接 copy-paste 的代码片段全部预先算好，让未来的查询一步到位，不用重新摸索 5 步。

## File map / 文件地图

Read these in order based on what you need:
按需要按下表顺序读取：

| 你要做的事 / What you want | 读这个文件 / Read |
|---|---|
| 我现在该做什么？ / "What's the workflow?" | [workflow.md](workflow.md) — Step 3 procedure + anti-patterns |
| 哪个区域的 Prometheus / Grafana / Superset 域名是什么 / "What's the URL for region X?" | [endpoints.md](endpoints.md) — all domains, all regions, all auth |
| 这个服务的 Prometheus job label 叫什么 / "What's the job label for service X?" | [job-labels.md](job-labels.md) — naming conventions, including the `prod-us-X` vs `us-prod-X` trap |
| 现在 US prod 大概有多少在线设备 / 多少 QPS / "What's the current order of magnitude for X?" | [magnitudes.md](magnitudes.md) — point-in-time anchors + cross-service ratios |
| 我要在脚本里跑 PromQL，怎么写最少代码 / "How do I run a PromQL query in the fewest lines?" | [query-helpers.py](query-helpers.py) — copy-paste-runnable Python |
| iot-service-cloud / kiss / state-machine 有哪些 counter 可以用 / "What metrics does service X expose?" | [recipes/](recipes/) — per-project counter tables + standard recipes |
| 上面的项目都没列，怎么找 / "Service X is not listed anywhere — how do I find its metrics?" | [recipes/fallback.md](recipes/fallback.md) — discovery algorithm |
| 业务量级（DAU / 设备分布）从哪查 / "Where does business-level data live?" | [data-warehouse.md](data-warehouse.md) — Superset + DataHub |
| 我能用 troubleshooting skill 拿到统计量吗 / "Can troubleshooting skill give me stats?" | [troubleshooting-bridge.md](troubleshooting-bridge.md) |

## 3-line workflow summary / 3 行流程总结

```
1. endpoints.md  → pick the right region's Prometheus / Thanos / Grafana datasource
2. recipes/<project>.md (or fallback.md) → pick the metric name + label
3. query-helpers.py → run it. If the value disagrees with the developer's claim, that's a [CONFLICT].
```

## Maintenance / 维护

- **Magnitudes drift over time.** [magnitudes.md](magnitudes.md) captures point-in-time numbers; track **ratios across services**, not absolute values, when you re-query.
  **量级会漂移**。[magnitudes.md](magnitudes.md) 是点位快照；重新查询时关注**跨服务比例**，不要看绝对值。
- **Recipes age** as projects evolve (counter renames, new MDC fields, job relabels). When a query in this dir comes back empty, **re-grep the project source** before assuming the file is wrong — see workflow.md §"hard rules".
  **配方会老化**（counter 改名、新增 MDC 字段、job 重命名）。本目录的 query 返空时，**先回到项目源码 grep** 再断定文件错了——见 workflow.md §"hard rules"。
- **Add a new service** by writing a recipes/{service}.md file in the same shape as the existing ones (paths / job label / top counters / log-split rule / recipes / anchor magnitudes). Then add a row to [magnitudes.md](magnitudes.md).
  **新增服务**时按相同骨架（路径 / job 标签 / 核心 counter / 日志拆分规则 / 标准 query / 量级锚点）在 recipes/ 下加文件，再在 [magnitudes.md](magnitudes.md) 里补一行。
