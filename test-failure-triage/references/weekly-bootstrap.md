# 每周 bootstrap 与上线条件

## 固定对象

- Agent：`test-failure-investigator`，只读调查，不承担 Step 修复开发。
- 平台：`prod-cn`，`https://device-cloud-server.builder.addx.live/graphql`。
- Buzz Channel：`2117ca28-80ef-4303-8c38-1d96c18545b6`。
- 既有飞书群：`oc_28580c335e9fef47e63fbcaf8108d4bd`，软硬件TDD+测试平台。
- 建议每周一 09:30 Asia/Shanghai；relay 的星期字段 1=周日，因此 cron 为 `30 1 * * 2`。
- 统计窗口：`[触发时−7日, 触发时)`，使用 UTC；分析时点与上期报告引用显式保存。

`weekly-workflow.yaml` 默认 disabled。首次真实取数、独立身份和发布回读通过后才启用；
源码和离线评测通过不代表线上周报已运行。首期无上期报告时不计算趋势。

## 最小权限

| 层 | 所需权限 | 验证 |
| --- | --- | --- |
| Device Cloud | 本次用户授权的专用 delegated PAT，`test-plans:read`、`diagnostics:read`，初期 30 日 | Job/Plan 读取回读；PAT诊断现网缺口显式列出，RP直读补证据 |
| ReportPortal | 专用 RP key 在 Agent 容器外；固定项目 GET 代理 | 对齐 Job、Launch、Scenario item、log 和附件；明确 PARTIAL/不可达 |
| GitLab | 独立主项目身份按 buzz-agent-setup 基线；额外源码按精确项目 Reporter＋`read_api,read_repository` | actual SHA 读取、项目 membership/可见性、写拒绝；当前 Channel Canvas 只有 Host463、Plugins1403 |
| Buzz | 自己的 pubkey，限定此 Channel；respond_to 与 harness/policy 一致 | 原 Thread 摘要回读与既有群镜像验证 |
| 飞书报告 | 本期≤8行摘要经既有 Desk bridge 署名转述；长报告需要 own-bot 文档权限 | 真实小文档与授权回读；不复用个人应用或其它 agent token |

新增 Server/Client/Devium AI 源码读取范围先登记 Canvas，再签各项目 token；证据中的版本
缺失时不能以主干源码直接确认本次运行支持。仅历史分析无需设备 run/resource/lease 权限。

## 当前部署事实（2026-10-05）

已回读指定群与 Buzz 绑定。专用平台 PAT id21，两项读取 scope，到期2026-11-04；
RP 专用 key id27，固定 builder_prod_cn GET 代理回读真实launch/item/log与附件。
RP key 无服务端到期字段，代理在2026-11-04失效；操作者必须轮换/撤销上游 key。
新 Agent 自己的 Buzz 身份、463 Reporter 身份已注册，原生 harness 由 canonical launcher
在只读根文件系统、私有 PID/mount namespace 的容器中运行；不挂载 operator glab/Buzz/密钥
或 Docker socket。负向读取探针已通过。未给 Server/Client/AI 等额外源码仓 token。

真实样本 Job18119：2条最终失败、6672条RP日志、6个媒体/JSON附件，完整日志分页通过。
视频185秒帧实际查看，第一条在日文 WebView 更新/刷新页，第二条在 Labs 页；两者均缺
语言设置目标。运行类型仍缺实际mode、阶段为EXECUTION，不能用AI tags推断生成任务。
未取得失败XML/独立 screenshot 附件与部署SHA，不确认accessibility/WebView能力缺口。
定时 Workflow 是否启用与实际 Agent/群回读以部署 receipt 为准，不由模板推断。

## 首次端到端验收

1. 用真实七日窗口采集平台 Job 和关联计划；分页预算内覆盖完整，否则列出缺口。
2. 下载并查看至少一个失败现场的真实截图/XML或录像帧；日志与引用对齐，密钥已脱敏。
3. Agent 输出结构化归因及人工可读报告；运行类型和失败阶段分别汇总。
4. 本期最多8行摘要、原Thread回读与Desk署名镜像；完整JSON存私有工作区。长报告需另验证 own-bot 文档授权。
5. 回读镜像群消息，确认内容/身份/目标；同窗口重触发不会重复发报告。
6. 更新非 secret 的上线 receipt，启用该 Workflow；到期前轮换 PAT。

后续能力候选通过证据门槛进入独立优化 Issue；开发 Agent 调 Devium AI lifecycle 创建
新 TestPlan / Job，申请资源、RED/GREEN/原 Scenario 复测，MR 后发布/升级验证。
bootstrap 不自动占设备、不改源码，不把历史 failed 改成 passed。
