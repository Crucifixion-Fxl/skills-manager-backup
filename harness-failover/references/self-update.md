# 更新本 skill（新错误文案 / 新 harness）

触发：`detect` 末尾出现 `N unclassified error sample(s)`；`--probe` 暴露了新的失败形态；要接入新 harness 或新账号类型。

**不做无人值守的自动 MR/合并。** agent 可以准备改动，但 MR 必须指定一位 reviewer 由人审。

## 新错误文案

1. `harness-failover learn --propose` —— 样本已脱敏，输出签名与 fixture 的草稿。**先检查**样本里没有密钥/邮箱/ID 残留，
   **也要检查有没有频道消息正文**：可疑行来自 agent 日志，日志里可能夹着用户发的文字，脱敏器不会把它当敏感信息。
2. 判断它是不是额度问题：是 → `quota`（长期）或 `transient`（瞬时）；需要人登录 → `auth`；都不是（噪声）→ 不加签名，改进可疑行过滤并加回归测试。
3. **红**：先只加样本到 `tests/fixtures/samples.json`（`sig` 用新 id）。`test_every_signature_has_a_matching_sample_fixture` 与分类测试应失败。
4. **绿**：在 `assets/signatures.json` 加签名（有恢复时刻就配 `reset` 规则；不认识的格式先在 `signatures.py` 里加解析函数并写测试）。
5. 更新 `SKILL.md` 与 [detection.md](detection.md) 的签名表；`learn --prune` 清掉已被解释的样本。
6. `uv run --extra dev pytest skills/harness-failover/tests -q` 全绿，`uv run python scripts/validate.py --skill skills/harness-failover --security` 通过。
7. 分支 `feat/harness-failover-<sig>`，提交 `test(hf): red …` → `feat(hf): green …`，用 `addx:gitlab-mr` 提 MR。
8. 合并后按 `addx` 插件更新流程更新本机插件，并重新运行 `scripts/install.sh`（定时器跑的是安装副本，不是仓库）。

## 新 harness

1. 用 ACP 握手核实 `model` / `effort` 取值（见 [profiles.md](profiles.md)），确认 effort 的 category 是 `thought_level`。
2. `profiles.py`：`HARNESSES`、`Profile.command`、`env_updates`、`identify`；`assets/profiles.default.json` 加默认项。
3. `cli.py`：`gather_health`（历史来源）、`probe_command` / `parse_probe`、（如需）`SystemOps`。每一项先写测试。
4. 采样它的额度错误，按上面的流程加签名；采样前签名标 `verified:false`。

## 铁律

- 样本必须脱敏（`learn.redact`），MR 里不出现 token / 邮箱 / UUID / 64 位十六进制。
- 补测试之后做一次**变异检查**：在副本上把关键判断改错（`>=`→`>`、删掉一次校验、默认值取反），确认至少一个测试会红。
  本 skill 的第一版测试有 5 处「能过但抓不住缺陷」，就是这样发现的。
- 修 bug 先写**会红的**复现测试，并亲眼看它对旧代码变红（本 skill 里已有一次「空转的红」：`json.dumps` 转义引号使输入根本没触发旧缺陷）。
- 真机跑一遍 `detect --probe` 再提 MR：本 skill 的三个缺陷（时间戳数字当状态码、`codex login status` 写 stderr、探测继承 stdin）都是真机暴露的。
