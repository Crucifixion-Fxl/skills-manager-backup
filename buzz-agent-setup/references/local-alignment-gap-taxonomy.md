# 本机 Buzz 对齐 gap 分类

这 12 类把 2026-09-21 手工审计发现的问题固定成 P1 离线门禁。它们是业务上的 gap 归类，不等于脚本的底层 check category；同一个 gap 可以由多个细检查共同证明。

JSON receipt 的每条 `checks[]` 都带 `gap_ids`，顶层 `gaps` 固定包含 LA-01…LA-12 的 `status` 与 pass/fail/unknown/not_applicable 计数；不能只在文档列分类而让机器回执无法追溯。

| ID | gap | 审计证据 | 旧配置变异 |
|---|---|---|---|
| LA-01 | agent inventory／持久 unit／canonical launcher 不完整 | `inventory`、`agent_unit`、`shared_launcher` | 删除 agent unit 或改成 wrapper／瞬时 unit |
| LA-02 | agent env 权限、键集合或启动 preflight 不一致 | `agent_env` | env 非 0600、缺 relay／binary pin、危险继承键或不可信 PATH |
| LA-03 | prompt 通用条款、角色条款或 helper pin 漂移 | `agent_prompt` | 删除任一精确条款、未知角色或旧 release helper |
| LA-04 | 责任人配置未收敛到 v2／`people_file` | `responsible_config` | v1、缺 people_file、权限错误或路径不可读 |
| LA-05 | Claude sandbox／Read deny 基线变松 | `sandbox` | 缺 release/people allowRead、宽路径、`allowAllUnixSockets` 或缺 `permissions.deny` |
| LA-06 | 所有运行面没有收敛到同一个完整 release SHA | 各 `*_release`、prompt、plugin 的 revision check | 任一 pin 留在旧 SHA |
| LA-07 | GitLab→Buzz sync manifest、配置、unit 或 launcher 漂移 | `sync_release`、`shared_launcher` | 旧 manifest、无效 sync config、自制 launcher |
| LA-08 | 飞书镜像 service／timer 入口漂移 | `feishu_release`、inventory | 旧脚本入口、缺 timer 或模板不一致 |
| LA-09 | 个人 todo service／timer／launcher 漂移 | `todo_release`、`shared_launcher` | 旧入口、缺 timer 或 launcher 分叉 |
| LA-10 | agent 入群申请 service／timer 漂移 | `join_release`、inventory | 旧入口、缺 timer 或配置不可判定 |
| LA-11 | Buzz CLI、`buzz-acp`、ACP 图片代理供应链未固定 | `buzz_cli`、launcher preflight、`media_proxy` | digest 错、非 ELF、PATH fallback、代理摘要／下游不一致 |
| LA-12 | agent 实际使用的 harness 插件未启用或 revision 落后 | `plugin_revision` | registry 旧 SHA、effective disabled、安装目录失效 |

状态语义统一如下：

- `pass`：该检查拿到了完整、可信且与目标 SHA 一致的证据。
- `fail`：检查对象已唯一确定，而且已知违反契约；旧配置变异必须落在这里。
- `unknown`：加载面、对象集合或可信证据无法完整确定，例如 systemd effective path 不可回读、存在未检查的 drop-in／transient unit。`unknown` 与 `fail` 一样使总门禁非零，但不能伪装成配置错误或数量 0。
- `not_applicable`：仅用于明确声明的兼容模式或本机确实未部署的表面；例如 stock text-only 必须有 `BUZZ_ACP_MEDIA_MODE=stock_text_only`，不能靠缺键自动降级。

P2 的 relay／GitLab／Channel 事实和 P3 的运行态沿用这组状态语义，但分别在 #150、#151 验收；P1 PASS 不能替代它们。
