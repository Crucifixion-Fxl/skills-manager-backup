# User Research Skill

这是一个可独立安装的 **Skill + Audience Personal API 客户端**。冷启动 Agent 使用完整仓库、
Python 3.9+ 和安全注入的 Project Personal key，从 self-context 发现 Project，并按公开契约执行。
运行时模块使用 `from __future__ import annotations`，`X | Y` 在 Python 3.9 导入时不会求值。
`dict[str, ...]` 从 Python 3.9 起就是合法语法。执行顺序是：

```text
Idea → 可选 VOC → Research → Typeform → 圈人 → 物化个性化链接
→ 可选 Brevo 同步 → 未发送 Campaign Draft
```

Typeform 和 Brevo 凭据、NocoDB 记录、仓库物化与异步 Sensor 都由 Platform 管理。
Agent 不直连三方服务，也不维护本地流程状态机。问卷可以单独使用；未入仓 Project 可以走通用问卷，
其答卷保留为 unmatched。结果读取与 CSV 下载独立于 Campaign Draft。

VOC 使用 Project-scoped Audience bridge：新采集默认走原生路径，以通用关键词搜索当前 Actor，
比较详情、schema、适用性和可见费用信息，再明确选择 Actor/build/input，读取所属 Dataset 并发布、下载报告。
编译路径仅用于恢复已有 Idea configuration 或 `platform_run_id`，不作为新采集的默认入口。
Reddit、Amazon 等仅是候选样例，不是白名单。Agent 不推断 `product_scope`、不接触 provider 凭据，也不采用
未经 Project API 返回的 Actor、run 或 Dataset。Campaign Draft 的 sender 由 Platform
部署配置注入；Agent 不提交、发现或猜测发件身份。配置缺失或请求中的 sender 不匹配时由 Platform 关闭失败。
本地 TDD 只证明 Draft API 的顺序与绑定，不代表线上账号配置或部署已经就绪。

## 安装

```bash
npx skills add git@gitlab.addx.ai:engineering/skills.git --skill audience-user-research
python3 /absolute/path/to/skills/audience-user-research/scripts/preflight.py
python3 /absolute/path/to/skills/audience-user-research/scripts/api.py capabilities
```

完整 clone 必须保留 `SKILL.md`、`references/`、`scripts/`、`src/` 和 `contracts/`。
客户端只依赖 Python 标准库；无需安装依赖。宿主有同契约具名工具时可以直接使用工具。

按[宿主配置](references/host-configuration.md)注入 key。完整 clone 默认请求
`https://audience-workflow-api-prod-us.addx.live`；Hermes 使用宿主注入的 origin。
不要把 key 写进命令参数、请求 JSON、日志或仓库。

## 冷启动

```bash
python3 scripts/api.py get_project_personal_key_context --request-stdin <<'JSON'
{"path":{},"query":{},"body":{}}
JSON
```

成功的在线响应给出唯一可信的 `project_id`、`binding_revision` 和 `allowed_actions`。
随后读取 Research readiness 和 query capabilities，再按[Project journey](references/typeform-research.md)
执行。所有写操作使用稳定幂等键；所有异步阶段读取同一 Research 的精确 request/receipt。

原生 VOC 可用 `scripts/tdd_native_voc_journey.py --phase collect` 采集并落地 Dataset
与续跑 state。Agent 阅读这些材料并撰写报告后，以 `--phase publish` 提交本地分析得到的
原文声音和 Markdown（不要提交 `source_coverage`），再回读和下载；采集成功本身不表示分析完成。
报告文件必须是 `USER_RESEARCH_NATIVE_VOC_OUTPUT_DIR` 里的普通 Markdown，并用绝对路径写入
`USER_RESEARCH_NATIVE_VOC_REPORT_PATH`。不要把报告放在该目录外面、子目录、符号链接或管道上。

## 验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests scripts
```

测试覆盖封闭 method/path、schema、Project binding、重定向拒绝、稳定幂等键与模糊写停止、冷启动 happy path、
通用问卷分支、无盲重试和 no-send 边界。实时 staging TDD 使用一次性小客群和未发送 Draft；
测试限制只放在 selection 输入，不写入运行时代码。已有获批 selection 大于本轮邀请人数时，可用
`python3 scripts/tdd_user_research_journey.py full-no-voc --sample-size 1` 运行冷启动驱动脚本；
脚本将实际物化人数限制在 1–5 人，以抽样人数核验物化、链接与 Brevo 同步；获批 selection 可以更大。
省略参数时仅接受不超过 5 人的 selection，并按其全量物化。这个上限只属于 TDD 驱动脚本。

## 契约

- `contracts/project-control-plane.openapi.json`：Project self-context、Idea、VOC 与 Research API。
- `contracts/audience-platform-public-v2.openapi.json`：仅保留上游历史契约快照，不进入冷启动操作清单。
- `contracts/source.json`：来源 revision 与 hash。
- `references/platform-api.md`：Agent 所用精确操作与证据。

线上能力以已部署 API 的 self-context、readiness、capabilities 和实际响应为准。仓库内契约或本地测试通过
不等于某个环境已经部署。该链路止于 Campaign Draft；不提供 send、schedule 或审批动作。
