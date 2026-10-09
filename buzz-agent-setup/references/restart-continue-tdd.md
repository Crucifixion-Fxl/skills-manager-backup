# Agent 重启后继续原 Thread：TDD 用例

`L1-ARC-001` 检查 `@Agent continue` 的发送契约；`L4-ARC-001` 检查**已安装的自动恢复控制器**在真实 Agent 重启后自动发出继续消息、Agent 的反应和任务恢复。升级流程改动触及 Agent 重启、恢复控制器或继续发送时，先运行 L1 与恢复控制器的 L1/L2，再在指定测试 Channel 运行 L4。历史回执只证明当时的 canary，不代表后来每次升级自动通过。

## 失败先行

旧发送方式只带 `--mention <Agent pubkey>`，正文是 `continue`。它可生成 `p` tag，却不会在消息正文显示 `@Agent`。`test_rejects_old_p_tag_only_continue` 将它判为失败。缺 Agent 👀 reaction、或检查点变为 `A,A,B`，也分别由负例判为失败。

## L1：发送契约

`references/scripts/restart_continue_message.py` 的 `build_continue_args` 只生成 CLI 参数，不负责发现中断任务或执行重启。调用方确认新进程已就绪、原 Thread 根和 Agent 身份后，用返回参数发送。正文必须恰为 `@<AgentName> continue`，同一事件的 `p` tag 只指向该 Agent，`e` tag 指向中断的原 Thread 根，`h` tag 指向原 Channel。名称和 ID 无法验证时拒绝发送。

```bash
python3 -m unittest discover -s skills/agent-harness/buzz-agent-setup/tests -p 'test_restart_continue_message.py' -v
```

## L3：已安装 timer 与故障矩阵（隔离真实栈）

真实 native 二进制、隔离 Relay（docker `ghcr.io/block/buzz:0.2.1` + postgres + redis）、真实 `systemd --user`；只有外部 ACP 模型是确定性替身，所以**不算 L4**。`--installed-timer` 用安装器自己的模板（`recovery_install_apply.render_units`）写出 `buzz-recovery-l3-<id>.service/.timer`，只改单元名和测试配置路径，放在用户 manager 的 runtime 单元目录并 `enable --runtime --now`；恢复只能来自 timer tick。runner 不直接调用控制器、不发 continue、不改 journal；结束时只删除自己建的单元文件并读回 `not-found`。绝不使用 `buzz-agent-recovery.*`，也不对本机运行安装器。

```bash
python3 skills/agent-harness/buzz-agent-setup/tests/localstack/recovery_e2e.py \
  --binary <已校验 recovery-schema v10 的 native 二进制> --matrix --installed-timer
```

`--matrix` 在各自全新的栈上依次运行：默认（计划重启 + SIGKILL，两频道四 Thread）、`--lost-ack`（REC-006：本地代理转发发布但丢掉 ACK，下一轮必须精确读回同一事件）、`--crash-before-admission`（REC-007：投递给被 SIGSTOP 的接收方后再次崩溃）、`--crash-after-admission`（REC-008：恢复执行已接收并在跑时再次崩溃）、`--cancel-interleave`（REC-015：排队中的请求、慢取消与取消后的新请求交错）、`--stuck-target`（REC-019：一个受阻路由和一个永不结束的目标，旁边五个可恢复 Thread），每个都带 `--waiting-controls`（REC-005：已完成、已取消、等人回复、等审批四类对照，恢复后至少再两个完整 tick 零 continue、零执行、零回复）。每轮报告在 `/tmp/buzz-recovery-l3-*/report.json`，汇总在 `buzz-recovery-l3-matrix-*/matrix.json`。判定逻辑（oracle）的 L1 回归：`python3 -m unittest discover -s skills/agent-harness/buzz-agent-setup/tests -p 'test_recovery_l3_matrix.py' -v`。REC-004 只做 L2 跨 boot 模拟（`test_recovery_cross_boot.py`）；物理断电属于 L4，需要隔离机器和单独批准的窗口。

## L4：已安装控制器自动续接

前提：目标 release 已按 [local-upgrade-runbook.md](local-upgrade-runbook.md)「7. 自动恢复」用 `install_agent_recovery.py --apply` 安装，输出 `installed=true`；`buzz-agent-recovery.timer` enabled＋active，最近的 tick 回执 `ok=true`。验收只观察这个正式 timer：验收脚本和人都**不能手发** `@Agent continue`，也不能逐目标直接运行 `recovery_controller.py` 来代替调度；那样测到的是发送契约，不是自动恢复。

在两个明确的测试 Channel（如“Buzz×飞书 L4 编辑同步测试”）各建两个 Thread，用一次性测试 Agent、独立检查点文件、固定 Buzz CLI 和 owner 已授权身份。每个 Thread 只给一条原始 @ 任务：写 `A`，可控地等待，恢复后核对 `A`、补 `B`，在原 Thread 回复 `DONE`。另准备已完成、已取消、等待人／等待审批的对照 Thread。

1. 四个 Thread 的检查点都只有 `A` 且 Agent 正在执行；读回 native journal 中四条已接收记录（source event、Thread 根、generation）。群里“我开始了”不算持久接收证明。
2. 只操作本轮唯一允许的测试 unit：计划重启 `systemctl --user restart buzz-local-<name>.service`；另一轮用 `systemctl --user kill --signal=SIGKILL buzz-local-<name>.service` 强杀，由 systemd 拉起。确认旧进程已死、新进程 generation 不同，且没有写 `B`。
3. 什么都不发，等待 timer：读 tick 回执前进，以及 shutdown／新 generation 就绪／startup 记录。
4. 每个中断 Thread 出现**一条**由控制器发出的继续消息：正文恰为 `@<AgentName> continue`，`p` tag 只指向该 Agent，`e` 指向原 Thread 根，`h` 指向原 Channel；消息必须来自控制器，不来自验收脚本。
5. Agent 处理期间读 `buzz reactions get --event <continue ID>`，Agent 的 👀 出现在**该继续消息**上，此时检查点仍只能是 `A`。完成后 reaction 可能撤回，不能以最终时刻的空 reaction 误判它从未反应。
6. 同一 Agent 在各自原 Thread 回复 `DONE`；每份检查点恰为 `A\nB\n`。
7. 再观察至少两个完整 tick：四个目标没有重复的继续消息，对照组零继续、零新执行，审批状态不变。
8. 绑定了飞书群的，在该群独立读取卡片或文字、真实 @ 和原回复归属；不能以 Buzz 成功推断飞书成功。
9. 保存脱敏证据：原始签名事件 ID、tick 回执、检查点摘要、新旧 PID／generation。随后停测试 Agent、移出 Channel、撤销一次性 kind:30177 策略并清理本机凭据。

## 历史 canary（手动发送，2026-09-24）

这次 canary 早于自动控制器：人按 L1 构造器发了一次 `@Agent continue`，用来验证发送契约和 Agent 的反应，不是自动恢复的验收。回执 oracle：

```bash
BUZZ_RESTART_CONTINUE_RECEIPT=skills/agent-harness/buzz-agent-setup/tests/fixtures/restart_continue_live_20260924.json \
  python3 -m unittest discover -s skills/agent-harness/buzz-agent-setup/tests/integration \
  -p 'test_restart_continue_receipt.py' -v
```

回执在 [`tests/fixtures/restart_continue_live_20260924.json`](../tests/fixtures/restart_continue_live_20260924.json)。对应的 [continue 消息](buzz://message?channel=613a9560-d423-4d14-ab3d-f4fc29aecb7a&id=66bfb01d40679115102a9517e0130d7074d7f141e5195ae397d4d120a775c1eb&thread=a3ddb284abc8bc9c96714ff20d5406d7209c1e666d26d56e34767b30b1dbd58a) 正文可见 `@l4-visible-continue-tdd-20260924 continue`；处理期间读到该 Agent 的 👀/💬，完成后读到空 reaction；检查点由 `A` 恢复为 `A,B`，Agent 在同一 Thread 回复 `DONE`。
