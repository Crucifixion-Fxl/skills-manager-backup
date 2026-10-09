# Device Cloud + ReportPortal 取证契约

## 权限和调用

运行环境注入平台专用 `DEVICE_CLOUD_GRAPHQL_URL`、`DEVICE_CLOUD_READ_TOKEN`。
最小申请 scope 是 `test-plans:read`、`diagnostics:read`；不申请运行、取消或资源写权限。
2026-10-05 实测：PAT 能读 Job/Plan 列表，但 GraphQL diagnostic resolver 仍校验 Casdoor JWT；
`/automation/v1` 诊断 REST 又限定 Plan.launchedBy 等于 token subject，不能覆盖全平台历史失败。
因此本期使用明确授权的专用 RP key，留在 Agent 隔离环境外，由确定性 GET 代理提供固定
`builder_prod_cn` 项目的 launch/item/log/data 路由。Agent 只持代理 client token。
RP key 继承账号权限，代理的 GET/项目/路径/分页/字节/到期门禁才是强制读取边界。
不保存网页登录 JWT，不借 behave.ini 的共享 RP key；服务到期≠上游 key 已撤销。

```bash
python3 scripts/collect_failures.py --output /private/work/platform.json
python3 scripts/rp_direct.py --input /private/work/platform.json --output /private/work/weekly-evidence.json
python3 scripts/fetch_evidence.py --rp-proxy --input /private/work/weekly-evidence.json --output-dir /private/work/media
```

代理使用 `RP_READ_PROXY_URL=http://127.0.0.1:9374`、`RP_READ_PROXY_TOKEN`。操作者在隔离环境外
运行 `rp_read_proxy.py --config /operator/private/rp.json`，配置文件0600：rp_token、client_token、
expires_at。监听仅环回；固定 upstream，不接受用户目标 URL、不转发写方法或 redirect。
主机上其他进程仍需 token；Agent 无宿主 PID、Docker socket、operator home 或密钥文件挂载。
在具备 PAT 支持的部署可继续使用平台 diagnostics；附件窗口必须来自实际时间戳，切成
每段≤1小时、按 logId 去重。不得伪造窗口。

采集脚本输出证据包，尚未分析根因；下载≠查看。JSON 写入和日志输出脱敏，但二进制
截图/XML仍是私有现场，不上传到公开仓库。录像需实际解码/查看；无工具则写未检查，
不能将“上传调用完成”当作附件已在 RP 可查询。没有 XML 时不得断言 accessibility 未开放。

## 当前查询模型的边界

- Job 分页有 `total / records / current / size`；扫描范围内分页并检查进度。窗口用 Job 的
  completedAt，未完成时使用 updatedAt/createdAt。旧计划本周才失败也可能命中，不能只按
  TestPlan.createdAt 过滤。首次是窗口快照，需持久化去重键；每周边界变化保留引用。
- Scenario serverStatus 和 RP 状态要对齐；Job COMPLETE 不证明 Scenario 全通过。
- `planType` / `jobType` 不能直接当成 Devium AI 模式。collector 只保留 metadata 中已知
  非敏感字段，未知/不存在字段保留缺项，不能伪造 generation / execution。
- 三层数据：平台 Job/Feature/Scenario；RP 映射报告与日志/附件；instrumented timeline
  attempts/steps。旧版本可能 MISSING_LINK、PARTIAL、RP_UNAVAILABLE 或 NOT_INSTRUMENTED。
  字段/查询未部署时明确降级，不把源码存在当成现网能读。
- 首期 collector 采集最终 failed/undefined Scenario 和无 Scenario 的失败/中止 Job。
  对已恢复且最终通过的 attempts，需另采对应 timeline 才能分析恢复事件；周报写明这项
  覆盖限制，不将“未采集”写成“零恢复”。RP Launch 级分析可补线索，不能重复计数。
- 完整日志分页和采集预算分别标记；默认扫描100页×200 Job，详细RP最多20个事件，
  每item最多30页×500条日志，媒体最多40个×20MiB，超额事件仍保留但标记未分析；只看 ERROR 或尾部 50 行可能错过首次异常与前置。

## 固定源码依据

- [Server GraphQL 查询与 Job 模型](https://gitlab.addx.ai/DEVT/device-cloud-server/-/blob/226e443ae6dfc76aa552784a3b3d07906b4665aa/src/main/resources/graphql/schema.graphqls#L276)
- [诊断报告：Scenario 状态与 RP item 映射](https://gitlab.addx.ai/DEVT/device-cloud-server/-/blob/226e443ae6dfc76aa552784a3b3d07906b4665aa/src/main/java/com/addx/devicecloudserver/data/output/diagnostic/JobDiagnosticReport.java#L133)
- [诊断 timeline 与 attempt](https://gitlab.addx.ai/DEVT/device-cloud-server/-/blob/226e443ae6dfc76aa552784a3b3d07906b4665aa/src/main/java/com/addx/devicecloudserver/data/output/diagnostic/JobDiagnosticTimeline.java#L44)
- [现有 Client WebView Step](https://gitlab.addx.ai/DEVT/device-cloud-client/-/blob/35557b2d89adfb6b319b74efe88aab8abd2bcd15/features/steps/phone/webview_steps.py#L4)
- [Devium AI registered-step gate](https://gitlab.addx.ai/DEVT/devium-ai/-/blob/8258df272e335d92d9bea4b31f499f0ac56cb549/devium_ai/acp/tasks.py#L99)

固定代码说明已有模型；每次运行仍核验实际平台 schema、Client / Plugin / App 版本。
