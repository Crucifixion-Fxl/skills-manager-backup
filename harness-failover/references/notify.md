# 飞书通知

## 发什么、何时发

| kind | 触发 | 去重 |
|:--|:--|:--|
| `exhausted` | 当前 harness 判定耗尽，**开始切换前** | key = 耗尽的 profile + 恢复时刻；恢复时刻已知时同一周期只发一次，**未知时最多每 6 小时一次**（否则 key 分不出周期，会被压制一周） |
| `stuck` | 耗尽且没有可用备选 | 同上，但每 6 小时提醒一次 |
| `switched` | 全部 agent 已确认加载新配置；带 retry 摘要（已提醒 N 条、M 条需人工、K 条发送失败） | 每次切换唯一 |
| `failure` | `aborted`（回滚，未重启）/ `partial`（部分 agent 未加载）/ `unidentified` / `retry`（要人处理）/ `drift`（漂移修复未完成）/ `error`（意外异常） | key = 类别 + 涉及对象（不含每轮都在变的计数）；同一问题每 6 小时最多一次 |
| `repaired` | 漂移已修复 | 每次唯一 |

只在**修改类命令**（`switch --apply`、`retry --apply`）里发；`detect`、dry-run 永远不发。手工切到一个健康的 profile 只发 `switched`（没有「耗尽」）。
去重状态在 `state.json` 的 `notified`，保留 7 天；**晚于当前时间 5 分钟以上的记录一律不信**（时钟回拨或被篡改都不能压制告警），`notified` 不是字典时当作空。**没发成功的不记录**，下一轮会再试。

## 发送方式

`lark-cli im +messages-send --user-id <你的 open_id> --text <消息> --as bot --idempotency-key hf-<24位摘要>`

- 以应用 bot 身份私聊你；幂等键让同一事件在 1 小时内最多送达一次；
- 子进程环境只有 `HOME`、`PATH`（lark-cli 所在目录 + `/usr/bin:/bin`）、`LANG`，**不含任何 `BUZZ_*`/token**，stdin 关闭，30 秒超时；
- 配置（`notify.json`）里的 `recipient_open_id` 必须匹配 `^ou_[A-Za-z0-9]{6,64}$`，`identity` 只能是 `bot`/`user`，`lark_cli` 必须是存在的绝对路径——否则一律不发，不会把配置内容拼进命令。

## 消息里能出现什么

固定模板 + 通过白名单字符过滤的字段：profile id、model/effort、agent 名（`[A-Za-z0-9._-]{1,32}`，超过 8 个截断为「等共 N 个」）、恢复时间、数量、主机名。
**不会出现**：日志正文、错误原文（例如回滚原因只映射为「权限/启动脚本/验证/回滚不完整/见日志」几类固定说法）、密钥、路径、频道消息内容。

## 崩溃也会通知

修改类命令里，`Ctx` 构造阶段的异常（例如 profiles 文件不合规）、运行中的意外异常、systemd 因 `TimeoutStartSec` 发来的 SIGTERM（记为 `Terminated`）都会发一条 `failure`。
`switched` 用 `finally` 保证发出：切换本身成功了，即使随后的 retry 步骤抛异常，你也会先收到「切换成功」。

## 不会做的事

- SIGKILL、断电、OOM 杀进程时没有进程可以发通知（只有 systemd 日志和 `--failed`）。
- `notify.json` / `state.json` 没有属主与权限校验（同 uid 可改）；见 SKILL.md「已知限制」。

- 5 分钟一轮，但去重保证：耗尽/切换成功每个事件一次，stuck 与失败每 6 小时最多一次，不会刷屏。
- 通知失败**不改变**切换结果和退出码；`lark-cli` 缺失/超时/报错只在输出里留一行 `(notification not sent: …)`。
- 不做「已读确认」；不在飞书本身不可用时用别的通道兜底。
