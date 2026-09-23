# App-of-Apps Application apply 策略迁移

涉及 Application CRD、controller minor version 或 SSA/CSA 切换时，先读
`~/Project/A4x/k8s/docs/runbooks/argocd-app-of-apps-status-safe-upgrade.md`，并将迁移拆成
可独立验证的阶段。

对于 v3.3 → v3.4 这一类迁移，至少需要：

1. 在旧 controller 仍运行时建立临时 bridge。
2. 仍由旧 controller 执行最后一次 replace，先 disarm 非自管 child，并验证 live 已无
   `Replace=true` 且 UID/status/history 保留。
3. 将自管理 Argo CD 的版本切换和自身 disarm 放在旧 controller 执行的最后阶段；不能先
   升级 controller，再用新 controller 删除其 live `Replace=true`。
4. 新 controller Ready 后，用仅 annotation 的 activation sync 建立 clean
   `kubectl.kubernetes.io/last-applied-configuration`；root 操作结果必须是
   client-side `configured`/`unchanged`，`replaced=0`。

以上是逻辑阶段，不得机械拆成两个普通 root auto-sync MR。未启用
`ApplyOutOfSyncOnly=true` 的 root 会在每次 operation 重放全部 child；若先单独 disarm
非自管 children，下一次 adoption MR 可能让它们在旧 controller 下提前走 CSA，并重新建立
包含 `status` 的旧 LAP。必须采用经过评审的同一-operation可执行验证 barrier，或经过证明
不会重放已 disarm children 的隔离/selective-sync 机制。只有 sync wave、没有逐对象
status/history 门禁，不足以安全分隔阶段。

每阶段合并前必须冻结同目录 Application 变更，确认 root 与 child 无 active operation，
逐对象保存 UID、完整 status/history/operationState 和 LAP，执行 server dry-run，并确认
生成的 LAP 可解析且不含 `status`。阶段完成后从 root 的 operation result 核对真实 apply
verb；只看 root `Succeeded` 不足以证明安全。

Application 历史恢复不属于 GitOps 迁移阶段。若 CRD 没有 status subresource，主资源
patch 会增加 generation 并触发 reconcile；必须独立设计、逐对象带 resourceVersion
前置条件，并在执行任何 live patch 前取得用户明确批准。禁止恢复旧
`operationState`，也禁止伪造缺失的部署记录。
