---
name: embedded-signal-validation
description: 用固件源码、构建烧录、串口日志和示波器实测组成嵌入式信号验证闭环；当用户要求验证 GPIO、PWM、UART、I2C、SPI、启动时序、中断延迟或确认代码输出与波形是否一致时使用。
---

# 嵌入式信号验证

## Description

验证“板上实际电信号是否符合代码预期”，最终交付可复查的源码版本、构建烧录结果、串口对齐记录、示波器数值、原始截图和结论。示波器控制统一委托 `oscilloscope-automation`，本 Skill 不复制厂商 SCPI 命令。

## Rules

### 输入

执行前建立 [验证契约](references/validation-contract.md)，至少确定：

| 输入 | 要求 |
|---|---|
| 工程 | 仓库、目标分支或基线、构建环境 |
| 固件 | 目标芯片、引脚/外设、期望信号计划 |
| 下载 | 烧录方式、目标设备、串口和波特率 |
| 仪器 | 示波器资源、通道、探头倍率、接地方式 |
| 验收 | 每个指标的预期值、单位和容差 |

用户已明确要求修改代码和烧录时，授权仅覆盖指定开发板和工程；不得扩展到其他设备。若用户只要求诊断或测量，则不修改代码、不烧录。

### 隔离

1. 先核对目标仓 `pwd`、分支、状态和 `git worktree list`。
2. 原工作树有用户改动或需要实验补丁时，创建独立 worktree，不覆盖、不暂存、不清理用户 WIP。
3. 构建必须遵守目标仓 `AGENTS.md`；要求 Docker/CI 同款环境时，不得用宿主机构建冒充有效结果。
4. 串口和 VISA 资源必须运行时发现或由用户指定，禁止把 `COM18`、设备序列号或个人绝对路径写成默认值。

### 闭环

1. 从源码、时钟树和外设配置推导预期值，并让固件输出可机器解析的阶段标记。
2. 构建和烧录后保存产物身份、烧录工具最终状态与设备启动日志；“构建成功”不能代替“烧录成功”。
3. 用串口阶段标记对齐测量窗口；没有可对齐标记时，使用外部触发或明确的时间基准。
4. 调用 `oscilloscope-automation` 识别仪器并采集数值、PNG 和必要的 CSV；修改通道、时基或触发前必须快照，结束后在 `finally` 恢复。
5. 按 [验证契约](references/validation-contract.md) 逐项比较，不以“波形看起来正确”代替数值验收。
6. 同时保存成功、失败、超时和 SCPI 错误；证据不足时输出 `inconclusive`，不能强行判定通过。

### 边界

- 探头接法属于结论的一部分。普通地夹不能连接高侧、非共地或市电节点；此类测量必须使用额定合适的差分/隔离方案。
- `VPP` 会包含过冲和振铃，不能直接判定 GPIO 高电平；逻辑电平、边沿质量和功能频率采用不同验收指标。
- 编译日志证明源码可构建，烧录日志证明产物已写入，串口日志证明固件运行，示波器证明引脚输出；四类证据不可互相替代。
- 首个实测案例见 [RTL8721Dx PWM 示例](references/rtl8721dx-pwm-example.md)，它是方法示例，不是所有项目的固定配置。

### 输出

结果包至少包含：

```text
evidence/
├── manifest.json
├── build.log
├── flash.log
├── serial.log
├── measurements.json
├── screenshots/
└── waveforms/           # 需要离线分析时生成
```

`manifest.json` 记录源码 commit/worktree、目标板、固件产物、串口、示波器 `idn/resource`、探头倍率、通道、容差、时间戳和最终 `pass/fail/inconclusive`。

## Examples

### Bad

```text
在有未提交改动的主工作树直接改 PWM；只看到串口打印 100 kHz 就宣布 PA12 波形正确；测完不恢复示波器现场。
```

### Good

```text
在隔离 worktree 修改 PWM，记录源码 commit；烧录指定开发板并用串口阶段标记对齐五档频率；MHO5104 逐档保存频率、周期、占空比、错误队列和截图，按容差判断并恢复原设置。
```

## References

- [验证契约](references/validation-contract.md)
- [RTL8721Dx PWM 示例](references/rtl8721dx-pwm-example.md)
