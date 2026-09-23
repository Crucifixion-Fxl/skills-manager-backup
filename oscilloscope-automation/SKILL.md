---
name: oscilloscope-automation
description: 通过 VISA/SCPI 安全操作 RIGOL MHO5104，完成设备发现、身份识别、自动测量、截图、波形读取、连续监测和状态恢复；当用户提到示波器、SCPI、VISA、波形、频率/占空比测量或 MHO5104 时使用。
---

# 示波器自动化

## Description

向 AI 提供统一的示波器操作入口，但不假定不同厂商和型号使用相同命令。先用 `*IDN?` 识别厂商、型号、序列号和固件，再匹配已验证适配器。当前公司只有 RIGOL MHO5104，自动执行范围仅为已实测的 `MHO5104 / 00.02.00`。

## Rules

### 入口

使用带 PEP 723 依赖声明的脚本；优先通过 `uv run` 执行：

```bash
uv run scripts/oscilloscope.py detect
uv run scripts/oscilloscope.py --resource "$OSCILLOSCOPE_RESOURCE" identify
uv run scripts/oscilloscope.py --resource "$OSCILLOSCOPE_RESOURCE" measure --channel 1 --items frequency,period,pduty,vpp
uv run scripts/oscilloscope.py --resource "$OSCILLOSCOPE_RESOURCE" screenshot --output scope.png
uv run scripts/oscilloscope.py --resource "$OSCILLOSCOPE_RESOURCE" waveform --channel 1 --points 1000 --output wave.csv
```

`detect` 默认只枚举 USB 仪器，避免部分 VISA 后端在全量 LAN/GPIB 扫描时长时间阻塞；确需全量枚举时显式传入 `--visa-pattern '?*'`。所有正常结果输出 JSON；诊断信息输出到 stderr。不得把某台仪器的序列号或 VISA 资源写入 Skill，换机后必须重新执行 `detect`。枚举不到当前 MHO5104 时读取 [连接排障](references/connection-troubleshooting.md)，不单独创建 VISA Skill。

### 适配

1. 先执行 `identify`，再选择型号适配器。
2. 只有验证状态为 `hardware_verified` 的 `MHO5104 / 00.02.00` 才能执行 `measure`、`monitor`、`screenshot`、`waveform`、`errors`、`snapshot` 和 `restore`。
3. 型号相同但固件不同，只能声明“适配器匹配、当前固件未实测”，并限制为 `detect`、`identify` 和用户明确给出的单条只读 `query`。
4. 未匹配设备仅允许 `detect`、`identify` 和用户明确给出的只读 `query`；禁止猜测厂商命令。
5. MHO5104 任务必须读取 [RIGOL MHO5104 适配说明](references/rigol-mho-dho5000.md)；只有公司增加真实设备并完成实机验证后，才按 [适配契约](references/adapter-contract.md) 扩展新型号。

### 安全

| 操作 | 默认行为 |
|---|---|
| 枚举、身份查询、测量、截图 | 可直接执行，只读保存证据 |
| 波形传输设置 | 自动快照并在 `finally` 恢复 |
| 通道、时基、触发、DVM 设置 | 修改前快照，任务结束必须恢复 |
| `restore` | 必须显式传入 `--confirm-write`，且核对设备身份 |
| AFG 输出、自校准、固件升级、恢复出厂、网络修改 | 本脚本不开放；确有需要时先取得用户明确确认 |

- 探头地夹通常与保护地相连；测量市电、高侧或非共地信号前必须确认隔离与探头等级。
- 每轮任务保存 `resource`、`idn`、通道、探头倍率、时基、触发、采集时间和错误队列。
- `VPP` 包含过冲与振铃，不能直接当作 GPIO 高电平；边沿质量测试应使用短地弹簧并单独定义带宽和探测方法。
- PNG 和波形是二进制 TMC Block；读取期间禁用文本终止符并禁止并发控制同一会话。

### 输出

测量 JSON 必须保留原始数值、单位、适配器、验证状态和 `scope_error`。截图与 CSV 文件名由用户或上层 Skill 指定，不写入仓库默认路径。

## Examples

### Bad

```text
未执行 *IDN?，直接把 MHO5104 的 :MEASure:ITEM? 命令发送给未知示波器；结束后也不检查错误队列。
```

### Good

```text
先 detect/identify，确认 RIGOL MHO5104 和固件；匹配 rigol-mho5104 适配器后读取 CH1 频率、周期、占空比和 VPP，保存截图并核对 0,"No error"。
```

## References

- [RIGOL MHO5104 适配说明](references/rigol-mho-dho5000.md)
- [连接排障](references/connection-troubleshooting.md)
- [MHO5104 实测命令目录（318 条）](references/mho5104-command-catalog.md)
- [适配契约](references/adapter-contract.md)
