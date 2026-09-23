# RIGOL MHO5104 适配说明

## 验证范围

| 字段 | 实测值 |
|---|---|
| 设备 | RIGOL MHO5104 |
| 固件 | `00.02.00` |
| 连接 | USB Device，经 Windows VISA/PyVISA |
| 实测状态 | `hardware_verified` |
| 其他 MHO/DHO5000 | 公司当前无对应实机，不匹配执行适配器 |

VISA 资源中的序列号属于单台设备，不能作为 Skill 默认值。完整的人读教程、318 条实测命令和截图以公司 Library 的[《MHO5104使用指南》](https://pages.addx.ai/dsu/library/tools/MHO5104%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97.html)为唯一来源；Skill 同时附带从该教程整理的 [MHO5104 实测命令目录](mho5104-command-catalog.md)，供 Agent 离线检索。本文件只保留适配器执行所需差异。

## 差异

| 能力 | MHO/DHO5000 行为 |
|---|---|
| 身份 | `*IDN?` |
| 测量 | `:MEASure:ITEM? <item>,CHANnel<n>` |
| 占空比 | `PDUTy` 返回 0～1 比例，适配器乘以 100 输出 `%` |
| 截图 | `:DISPlay:DATA? PNG` 返回 IEEE 488.2/TMC Binary Block |
| 波形 | `:WAVeform:PREamble?` 配合 `:WAVeform:DATA?` |
| 错误队列 | `:SYSTem:ERRor:NEXT?`；成功为 `0,"No error"` |

已用于自动测量的项目：`FREQuency`、`PERiod`、`PDUTy`、`VPP`、`VMAX`、`VMIN`、`VAVG`、`RTIMe`。

## 二进制

- 截图和波形读取前把 PyVISA `read_termination` 临时设为 `None`；PNG 数据内部可能包含 LF，不能按文本换行截断。
- TMC 头格式为 `#<位数><负载长度><负载>`，只把负载保存为 PNG 或解析为采样码。
- NORMal/BYTE 波形换算：`time=(index-xreference)*xincrement+xorigin`，`voltage=(code-yorigin-yreference)*yincrement`。
- 深存储 RAW 读取需要先停止采集并分块传输，首版 CLI 不开放，避免改变运行状态或一次读取超大数据。

## 边界

- `MHO5104/00.02.00` 之外的设备只允许身份识别和用户明确给出的单条只读查询，不执行适配器命令。
- MHO5104 只有 4 个模拟通道；不得从 DHO5108 示例推断额外通道存在。
- AFG、伯德图、逻辑分析和协议解码依赖选件或附件，不属于首版通用 CLI。
- 任何改变前端量程、耦合、探头倍率、时基或触发的流程都必须先快照并在 `finally` 恢复。
