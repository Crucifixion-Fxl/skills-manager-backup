# MHO5104 连接排障

## 边界

本排障只服务公司当前的 RIGOL MHO5104，不独立抽象成 VISA Skill。先定位问题在物理连接、Windows 设备层、VISA 后端还是 PyVISA 脚本层，不要在同一层反复重试。

## 分层

| 现象 | 定位 | 处理 |
|---|---|---|
| Windows 也看不到仪器 | 物理连接 | 确认示波器已开机，数据线连后面板 `USB DEVICE` 而不是前面板 `USB HOST`，并更换已确认可传数据的 USB 线和电脑端口 |
| Windows 能看到，`detect` 为空 | VISA 后端 | 确认 RIGOL Ultra Sigma/Ultra Station 或其他可用 VISA Runtime 能枚举仪器；再运行 `uv run scripts/oscilloscope.py detect` |
| 默认后端失败 | 后端选择 | 用 `uv run scripts/oscilloscope.py --backend '@py' detect` 对照；`@py` 还需要系统 libusb 支持，不得将缺少 libusb 误判为示波器故障 |
| Windows 可用，WSL 不可用 | WSL USB 边界 | WSL 不会自动继承 Windows 的 USB/VISA 设备。优先在 Windows Python/VISA 环境执行脚本；只有明确将 USB 设备附加给 WSL 后，才在 WSL 使用 `@py` |
| 能枚举但 `*IDN?` 超时 | VISA 会话 | 确认资源是 `USB...::INSTR`，增大 `--timeout-ms`，关闭同时占用该仪器的 Ultra Sigma 或其他会话后重试 |
| `*IDN?` 成功但自动操作被拒绝 | 执行门禁 | 检查 `idn` 是否为 `RIGOL ... MHO5104 ... 00.02.00`；其他型号或固件只允许 `identify` 和单条只读 `query` |

## 证据

排障结果至少保留操作系统、VISA 后端、`detect` JSON、VISA `resource`、`*IDN?` 和完整错误文本。不将某台设备的序列号或资源字符串写成 Skill 默认值。
