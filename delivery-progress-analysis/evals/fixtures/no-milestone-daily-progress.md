# 无 Milestone 日更冻结证据

此夹具不是线上项目。唯一范围：project 1021 engineering/skills、type::feature；active Milestone=0，非 APP，无 due_date 和工作日历，因此时间风险不可推断。分析时点 T=2026-10-09T01:30:00Z，昨日同口径基线 B=2026-10-08T01:30:00Z；比较窗口 (B,T]。所有链接为夹具中的冻结对象标识，不要求联网或通知。

## 覆盖
当前 open 集合 2 页/118 项，变更集合 all-state Issue 2 页/7 项、all-state MR 2 页/5 项，均分页完整。其余对象无事件且与基线相同；以下是全部有意义或可能误判的候选。当前 open 的净数相同，不代表没有推进。

## 能力与证据
- #301「设备离线提醒」：B 时 open/in-review，今天 00:40 验收 Job 成功，候选 SHA=a301、同一候选端到端离线提醒通过；01:00 Issue closed，关闭理由为验收通过。它已不在当前 open 集合。
  [Issue #301](https://gitlab.addx.ai/engineering/skills/-/issues/301)；[验收 Job](https://gitlab.addx.ai/engineering/skills/-/jobs/9301)；[关闭事件](https://gitlab.addx.ai/engineering/skills/-/issues/301#note_9301)。仅此夹具声明单组件，无组合包依赖。
- #302「通知去重」：B 时 MR !1302 open，今天 00:20 merged 到 main，合并 SHA=a302，对应 CI success；Issue 仍 open/in-progress，生产验收未执行。原生关联已核验。
  [Issue #302](https://gitlab.addx.ai/engineering/skills/-/issues/302)；[MR !1302](https://gitlab.addx.ai/engineering/skills/-/merge_requests/1302)；[Pipeline](https://gitlab.addx.ai/engineering/skills/-/pipelines/9302)。这是集成推进，不能写已交付完成。
- #303「旧版提醒入口」：今天 00:10 closed，因为重复于 #301，没有自己的验收或发布事件。
  [重复关闭说明](https://gitlab.addx.ai/engineering/skills/-/issues/303#note_9303)。不能计为第二项完成能力。
- #304「提醒配置」：updated_at 今天 00:50；只有错别字修订，状态、实现与验收没变化。
  [编辑事件](https://gitlab.addx.ai/engineering/skills/-/issues/304#note_9304)。不能写交付推进。
- #305「提醒恢复」：B 与 T 都是 open/in-progress，但窗口内 23:00 曾关闭，23:10 因生产复现 reopened；需要保留中间退步事件。
  [Issue #305](https://gitlab.addx.ai/engineering/skills/-/issues/305)；[reopen 与复现](https://gitlab.addx.ai/engineering/skills/-/issues/305#note_9305)。该风险昨日没有，本次需 owner 处理；身份信息不足，不得猜 pubkey。
- #306 type::bug 与 #307 type::maintenance 今日有活动，但调用方只允许 feature，不纳入上述进展，披露类型边界即可。

## 变体
无基线：删除昨日快照，保留全部带时间原生事件；仍可证明 #301 今天验收/关闭与 #302 今天合并，不能猜 B 时状态。另一个 #308 当前 merged，但 merge 时间未知，不能说今天合并；不得因为基线缺失声明无变化。
无变化：提供完整同口径快照与窗口事件读回，所有对象均无变化；不要重复列旧 MR，不通知正常 owner。
