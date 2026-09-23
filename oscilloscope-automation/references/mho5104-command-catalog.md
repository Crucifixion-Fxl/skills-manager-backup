# MHO5104 实测命令目录

本目录随 Skill 附带 RIGOL MHO5104 固件 `00.02.00` 的 318 条实测命令快照，按 27 个功能组列出命令、作用、实测状态和返回限制。命令事实的唯一权威来源仍是公司 Library 的[《MHO5104使用指南》](https://pages.addx.ai/dsu/library/tools/MHO5104%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97.html)，可直接打开其中的[命令枚举](https://pages.addx.ai/dsu/library/tools/MHO5104%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97.html#%E5%91%BD%E4%BB%A4%E6%9E%9A%E4%B8%BE)查看可搜索版本、完整教程和实机截图。

## 使用边界

- 这里的“可用”只表示命令在该型号、固件和当时功能状态下得到有效响应，不代表可绕过适配器安全边界自动执行。
- 标为“条件可用”或“不支持”的命令必须保留原始限制，不得改写成通用能力。
- 写命令、选件相关命令和会改变仪器状态的命令不能通过通用 `query` 入口发送；自动化能力仍以适配器公开接口为准。
- 教程与本快照口径不一致时，以教程为准，并同步更新本目录后再扩大 Skill 能力。

## 闭环补充

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `*IDN?` | 查询仪器厂商、型号、序列号和固件版本 | 可用 | 返回厂商、型号、序列号和固件版本 |
| `:SYSTem:ERRor:NEXT?` | 读取并移除错误队列中的下一条错误 | 可用 | 每条候选命令后均用于核对错误队列 |
| `*OPT?` | 尝试使用通用写法查询已安装选件 | 不支持 | 返回 -113 Undefined header；改用原厂选件查询 |
| `:SYSTem:OPTion:STATus? BND` | 查询 BND 选件激活状态 | 可用 | 返回 0 |
| `:SYSTem:OPTion:STATus? AUTO` | 查询 AUTO 选件激活状态 | 可用 | 返回 1 |
| `:SYSTem:OPTion:STATus? FLEX` | 查询 FLEX 选件激活状态 | 可用 | 返回 1 |
| `:SYSTem:OPTion:STATus? AUDIO` | 查询 AUDIO 选件激活状态 | 可用 | 返回 1 |
| `:SYSTem:OPTion:STATus? AERO` | 查询 AERO 选件激活状态 | 可用 | 返回 1 |
| `:SYSTem:OPTion:STATus? UPA` | 查询 UPA 选件激活状态 | 可用 | 返回 0 |
| `:SYSTem:OPTion:STATus? AWG` | 查询 AWG 选件激活状态 | 可用 | 返回 1 |
| `:SYSTem:OPTion:STATus? BW5T10` | 查询 BW5T10 选件激活状态 | 可用 | 返回 0 |
| `:DISPlay:DATA? PNG` | 读取当前屏幕 PNG 图像数据 | 可用 | 返回完整 PNG TMC 数据块 |
| `:WAVeform:SOURce CHANnel1` | 将波形传输源选择为 CH1 | 可用 | 已写入波形传输源，不改变 CH1 前端设置 |
| `:WAVeform:MODE NORMal` | 将波形传输范围设为当前屏幕 | 可用 | 已写入屏幕波形读取模式 |
| `:WAVeform:FORMat BYTE` | 将波形传输格式设为单字节二进制 | 可用 | 已写入二进制传输格式 |
| `:WAVeform:POINts 1000` | 将波形传输点数设为 1000 | 可用 | 已写入传输点数 |
| `:WAVeform:DATA?` | 读取当前波形源采样数据块 | 可用 | 已返回并解析 CH1 的二进制 TMC 波形块 |
| `:SOURce1:FM:STATe?` | 查询 AFG1 的 FM 调制开关 | 可用 | 带通道号的 AFG FM 状态查询 |

## IEEE 488.2

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `*OPC?` | 查询此前提交的操作是否完成 | 可用 | 正常返回 |
| `*ESE?` | 查询标准事件状态使能寄存器 | 可用 | 正常返回 |
| `*SRE?` | 查询服务请求使能寄存器 | 可用 | 正常返回 |
| `*STB?` | 查询状态字节寄存器 | 可用 | 正常返回 |

## 自动设置

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:AUToset:PEAK?` | 查询自动设置是否优先检测峰值 | 可用 | 正常返回 |
| `:AUToset:OPENch?` | 查询自动设置是否自动打开有信号的通道 | 可用 | 正常返回 |
| `:AUToset:OVERlap?` | 查询自动设置后的波形是否重叠显示 | 可用 | 正常返回 |
| `:AUToset:KEEPcoup?` | 查询自动设置是否保留原耦合方式 | 可用 | 正常返回 |
| `:AUToset:LOCK?` | 查询自动设置锁定状态 | 可用 | 正常返回 |
| `:AUToset:ENAble?` | 查询自动设置功能使能状态 | 可用 | 正常返回 |

## 采集

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:ACQuire:AVERages?` | 查询平均采样使用的平均次数 | 可用 | 正常返回 |
| `:ACQuire:BITS?` | 查询当前采集分辨率位数 | 可用 | 正常返回 |
| `:ACQuire:MDEPth?` | 查询当前存储深度 | 可用 | 正常返回 |
| `:ACQuire:MEMDepth?` | 使用兼容命令查询当前存储深度 | 可用 | 正常返回 |
| `:ACQuire:TYPE?` | 查询当前采集类型 | 可用 | 正常返回 |
| `:ACQuire:SRATe?` | 查询当前实时采样率 | 可用 | 正常返回 |

## 显示

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:DISPlay:CBRightness?` | 查询光标亮度 | 可用 | 正常返回 |
| `:DISPlay:COLor?` | 查询波形颜色模式 | 可用 | 正常返回 |
| `:DISPlay:GBRightness?` | 查询网格亮度 | 可用 | 正常返回 |
| `:DISPlay:GRADing:TIME?` | 查询波形余辉时间 | 可用 | 正常返回 |
| `:DISPlay:GRID?` | 查询屏幕网格样式 | 可用 | 正常返回 |
| `:DISPlay:RULers?` | 查询屏幕标尺开关 | 可用 | 正常返回 |
| `:DISPlay:TYPE?` | 查询波形显示类型 | 可用 | 正常返回 |
| `:DISPlay:WBRightness?` | 查询波形亮度 | 可用 | 正常返回 |
| `:DISPlay:WHOLd?` | 查询波形保持显示状态 | 可用 | 正常返回 |

## 模拟通道

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:CHANnel1:BWLimit?` | 查询 CH1 带宽限制 | 可用 | 正常返回 |
| `:CHANnel1:COUPling?` | 查询 CH1 输入耦合方式 | 可用 | 正常返回 |
| `:CHANnel1:DISPlay?` | 查询 CH1 显示开关 | 可用 | 正常返回 |
| `:CHANnel1:INVert?` | 查询 CH1 极性反转状态 | 可用 | 正常返回 |
| `:CHANnel1:OFFSet?` | 查询 CH1 垂直偏移 | 可用 | 正常返回 |
| `:CHANnel1:SCALe?` | 查询 CH1 垂直档位 | 可用 | 正常返回 |
| `:CHANnel1:IMPedance?` | 查询 CH1 输入阻抗 | 可用 | 正常返回 |
| `:CHANnel1:BVOLtage?` | 查询 CH1 偏置电压 | 可用 | 正常返回 |
| `:CHANnel1:PROBe?` | 查询 CH1 探头衰减倍率 | 可用 | 正常返回 |
| `:CHANnel1:PROBe:DELay?` | 查询 CH1 探头延时校正值 | 可用 | 正常返回 |
| `:CHANnel1:PROBe:EXTattenuation?` | 查询 CH1 外部探头衰减系数 | 可用 | 正常返回 |
| `:CHANnel1:LABel:SHOW?` | 查询 CH1 标签显示开关 | 可用 | 正常返回 |
| `:CHANnel1:LABel:CONTent?` | 查询 CH1 标签文本 | 可用 | 正常返回 |
| `:CHANnel1:UNITs?` | 查询 CH1 幅度单位 | 可用 | 正常返回 |
| `:CHANnel1:VERNier?` | 查询 CH1 垂直细调状态 | 可用 | 正常返回 |
| `:CHANnel1:POSition?` | 查询 CH1 波形垂直位置 | 可用 | 正常返回 |

## 计数器

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:COUNter:CURRent?` | 查询频率计当前读数 | 可用 | 正常返回 |
| `:COUNter:ENABle?` | 查询频率计开关 | 可用 | 正常返回 |
| `:COUNter:SOURce?` | 查询频率计信源 | 可用 | 正常返回 |
| `:COUNter:MODE?` | 查询频率计测量模式 | 可用 | 正常返回 |
| `:COUNter:NDIGits?` | 查询频率计显示位数 | 可用 | 正常返回 |
| `:COUNter:STATistic:ENABle?` | 查询频率计统计开关 | 可用 | 正常返回 |
| `:COUNter:STATistic:AVERages?` | 查询频率计统计平均值 | 条件限制 | 需先产生有效计数器统计数据；当前返回 -200 Execution error |
| `:COUNter:STATistic:MAXimum?` | 查询频率计统计最大值 | 条件限制 | 需先产生有效计数器统计数据；当前返回 -200 Execution error |
| `:COUNter:STATistic:MINimum?` | 查询频率计统计最小值 | 条件限制 | 需先产生有效计数器统计数据；当前返回 -200 Execution error |

## 电压表

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:DVM:CURRent?` | 查询数字电压表当前读数 | 可用 | 正常返回 |
| `:DVM:ENABle?` | 查询数字电压表开关 | 可用 | 正常返回 |
| `:DVM:MODE?` | 查询数字电压表测量模式 | 可用 | 正常返回 |
| `:DVM:SOURce?` | 查询数字电压表信源 | 可用 | 正常返回 |

## 光标

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:CURSor:MODE?` | 查询光标测量模式 | 可用 | 正常返回 |
| `:CURSor:MANual:SOURce?` | 查询手动光标信源 | 可用 | 正常返回 |
| `:CURSor:MANual:TYPE?` | 查询手动光标类型 | 可用 | 正常返回 |
| `:CURSor:MANual:AXValue?` | 查询手动光标 A 的 X 轴值 | 条件限制 | 当前未启用手动光标，查询超时 |
| `:CURSor:MANual:AYValue?` | 查询手动光标 A 的 Y 轴值 | 条件限制 | 当前未启用手动光标，查询超时 |
| `:CURSor:MANual:BXValue?` | 查询手动光标 B 的 X 轴值 | 条件限制 | 当前未启用手动光标，查询超时 |
| `:CURSor:MANual:BYValue?` | 查询手动光标 B 的 Y 轴值 | 条件限制 | 当前未启用手动光标，查询超时 |
| `:CURSor:MANual:IXDelta?` | 查询两条手动光标的 X 轴增量倒数 | 条件限制 | 当前未启用手动光标，查询超时 |
| `:CURSor:MANual:TUNit?` | 查询手动光标时间单位 | 可用 | 正常返回 |

## 直方图

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:HISTogram:ENABle?` | 查询直方图开关 | 可用 | 正常返回 |
| `:HISTogram:HEIGht?` | 查询直方图显示高度 | 可用 | 正常返回 |
| `:HISTogram:RANGe:BOTTom?` | 查询直方图区域下边界 | 可用 | 正常返回 |
| `:HISTogram:RANGe:LEFT?` | 查询直方图区域左边界 | 可用 | 正常返回 |
| `:HISTogram:RANGe:RIGHt?` | 查询直方图区域右边界 | 可用 | 正常返回 |
| `:HISTogram:RANGe:TOP?` | 查询直方图区域上边界 | 可用 | 正常返回 |
| `:HISTogram:SOURce?` | 查询直方图信源 | 可用 | 正常返回 |
| `:HISTogram:STATistics:RESult?` | 查询直方图统计结果 | 可用 | 正常返回 |
| `:HISTogram:TYPE?` | 查询直方图类型 | 可用 | 正常返回 |

## 逻辑分析

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:LA:ACTive?` | 查询当前活动的数字通道 | 可用 | 正常返回 |
| `:LA:AUTosort?` | 查询数字通道自动排序状态 | 可用 | 正常返回 |
| `:LA:DIGital:ENABle? D0` | 查询指定数字通道的开关 | 可用 | 正常返回 |
| `:LA:DIGital:LABel? D0` | 查询指定数字通道的标签 | 可用 | 正常返回 |
| `:LA:ENABle?` | 查询逻辑分析功能开关 | 可用 | 正常返回 |
| `:LA:LAYer:ACTive?` | 查询当前活动的数字通道层 | 可用 | 正常返回 |
| `:LA:POD1:DISPlay?` | 查询 POD1 显示开关 | 可用 | 正常返回 |
| `:LA:POD1:INSert?` | 查询 POD1 插入位置 | 可用 | 正常返回 |
| `:LA:POD1:LABel:ENABle?` | 查询 POD1 标签显示开关 | 可用 | 正常返回 |
| `:LA:POD1:THReshold?` | 查询 POD1 数字阈值 | 可用 | 正常返回 |
| `:LA:POSition?` | 查询数字波形垂直位置 | 可用 | 正常返回 |
| `:LA:PROBecal:DELay?` | 查询逻辑探头延时校正值 | 可用 | 正常返回 |
| `:LA:SIZE?` | 查询数字波形显示大小 | 可用 | 正常返回 |

## 网络

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:LAN:AUToip?` | 查询 Auto-IP 开关 | 可用 | 正常返回 |
| `:LAN:DESCription?` | 查询网络接口描述 | 可用 | 正常返回 |
| `:LAN:DHCP?` | 查询 DHCP 开关 | 可用 | 正常返回 |
| `:LAN:DNS?` | 查询 DNS 服务器地址 | 可用 | 正常返回 |
| `:LAN:DSERver?` | 查询动态域名服务状态 | 可用 | 正常返回 |
| `:LAN:GATeway?` | 查询默认网关 | 可用 | 正常返回 |
| `:LAN:HOST:NAME?` | 查询仪器网络主机名 | 可用 | 正常返回 |
| `:LAN:IPADdress?` | 查询仪器 IP 地址 | 可用 | 正常返回 |
| `:LAN:MAC?` | 查询网口 MAC 地址 | 可用 | 正常返回 |
| `:LAN:MANual?` | 查询手动网络配置开关 | 可用 | 正常返回 |
| `:LAN:MDNS?` | 查询 mDNS 开关 | 可用 | 正常返回 |
| `:LAN:SMASK?` | 查询子网掩码 | 可用 | 正常返回 |
| `:LAN:STATus?` | 查询网络连接状态 | 可用 | 正常返回 |
| `:LAN:VISA?` | 查询 LAN VISA 服务配置 | 可用 | 正常返回 |

## 模板测试

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:MASK:ENABle?` | 查询模板测试开关 | 可用 | 正常返回 |
| `:MASK:FAILed?` | 查询模板测试失败次数 | 可用 | 正常返回 |
| `:MASK:OPERate?` | 查询模板测试运行状态 | 可用 | 正常返回 |
| `:MASK:OUTPut:ENABle?` | 查询模板测试结果输出开关 | 可用 | 正常返回 |
| `:MASK:OUTPut:EVENt?` | 查询模板测试输出事件类型 | 可用 | 正常返回 |
| `:MASK:OUTPut:TIME?` | 查询模板测试输出脉冲时间 | 可用 | 正常返回 |
| `:MASK:PASSed?` | 查询模板测试通过次数 | 可用 | 正常返回 |
| `:MASK:SOURce?` | 查询模板测试信源 | 可用 | 正常返回 |
| `:MASK:TOTal?` | 查询模板测试总次数 | 可用 | 正常返回 |
| `:MASK:X?` | 查询模板水平容限 | 可用 | 正常返回 |
| `:MASK:Y?` | 查询模板垂直容限 | 可用 | 正常返回 |

## 数学运算

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:MATH1:DISPlay?` | 查询数学波形显示开关 | 可用 | 正常返回 |
| `:MATH1:EXPand?` | 查询数学波形垂直扩展基准 | 可用 | 正常返回 |
| `:MATH1:GRID?` | 查询数学波形网格 | 可用 | 正常返回 |
| `:MATH1:INVert?` | 查询数学波形反相状态 | 可用 | 正常返回 |
| `:MATH1:LABel:SHOW?` | 查询数学波形标签显示开关 | 可用 | 正常返回 |
| `:MATH1:OFFSet?` | 查询数学波形垂直偏移 | 可用 | 正常返回 |
| `:MATH1:OPERator?` | 查询数学运算类型 | 可用 | 正常返回 |
| `:MATH1:SCALe?` | 查询数学波形垂直档位 | 可用 | 正常返回 |
| `:MATH1:SOURce1?` | 查询数学运算第一个信源 | 可用 | 正常返回 |
| `:MATH1:SOURce2?` | 查询数学运算第二个信源 | 可用 | 正常返回 |
| `:MATH1:WAVetype?` | 查询数学运算输出波形类型 | 可用 | 正常返回 |
| `:MATH1:FFT:SOURce?` | 查询 FFT 信源 | 可用 | 正常返回 |
| `:MATH1:FFT:UNIT?` | 查询 FFT 幅度单位 | 可用 | 正常返回 |
| `:MATH1:FFT:WINDow?` | 查询 FFT 窗函数 | 可用 | 正常返回 |
| `:MATH1:FFT:HCENter?` | 查询 FFT 中心频率 | 可用 | 正常返回 |
| `:MATH1:FFT:HSCale?` | 查询 FFT 水平频率档位 | 可用 | 正常返回 |

## 自动测量

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:MEASure:AMP:TYPE?` | 查询幅度测量基准计算方式 | 可用 | 正常返回 |
| `:MEASure:AMSource?` | 查询全部自动测量项的统一信源 | 可用 | 正常返回 |
| `:MEASure:AREA?` | 查询自动测量使用的测量区域 | 可用 | 正常返回 |
| `:MEASure:CATegory?` | 查询当前自动测量参数类别 | 可用 | 正常返回 |
| `:MEASure:COUNter:ENABle?` | 查询测量栏频率计开关 | 可用 | 正常返回 |
| `:MEASure:COUNter:SOURce?` | 查询测量栏频率计信源 | 可用 | 正常返回 |
| `:MEASure:COUNter:VALue?` | 查询测量栏频率计读数 | 可用 | 正常返回 |
| `:MEASure:INDicator?` | 查询测量指示线显示状态 | 可用 | 正常返回 |
| `:MEASure:SOURce?` | 查询自动测量默认信源 | 可用 | 正常返回 |
| `:MEASure:STATistic:COUNt?` | 查询自动测量统计次数 | 可用 | 正常返回 |
| `:MEASure:STATistic:DISPlay?` | 查询自动测量统计显示开关 | 可用 | 正常返回 |
| `:MEASure:THReshold:SOURce?` | 查询测量阈值使用的信源 | 可用 | 正常返回 |
| `:MEASure:THReshold:TYPE?` | 查询测量阈值定义方式 | 可用 | 正常返回 |
| `:MEASure:ITEM? VMAX,CHANnel1` | 查询 CH1 最大电压 | 可用 | 正常返回 |
| `:MEASure:ITEM? VMIN,CHANnel1` | 查询 CH1 最小电压 | 可用 | 正常返回 |
| `:MEASure:ITEM? VAVG,CHANnel1` | 查询 CH1 平均电压 | 可用 | 正常返回 |
| `:MEASure:ITEM? RTIMe,CHANnel1` | 查询 CH1 上升时间 | 可用 | 正常返回 |
| `:MEASure:ITEM? PDUTy,CHANnel1` | 查询 CH1 正占空比 | 可用 | 正常返回 |

## 导航

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:NAVigate:ENABle?` | 查询导航功能开关 | 可用 | 正常返回 |
| `:NAVigate:MODE?` | 查询导航模式 | 可用 | 正常返回 |
| `:NAVigate:TIME:PLAY?` | 查询时间导航播放状态 | 可用 | 正常返回 |
| `:NAVigate:TIME:SPEed?` | 查询时间导航播放速度 | 可用 | 正常返回 |

## 发生器

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:OUTPut1:LOAD?` | 查询 AFG1 输出负载设置 | 可用 | 正常返回 |
| `:OUTPut1:STATe?` | 查询 AFG1 输出开关 | 可用 | 正常返回 |
| `:SOURce1:FREQuency?` | 查询 AFG1 输出频率 | 可用 | 正常返回 |
| `:SOURce1:FUNCtion?` | 查询 AFG1 波形类型 | 可用 | 正常返回 |
| `:SOURce1:FUNCtion:ARBitrary?` | 查询 AFG1 任意波形名称 | 可用 | 正常返回 |
| `:SOURce1:FUNCtion:SQUare:DCYCle?` | 查询 AFG1 方波占空比 | 可用 | 正常返回 |
| `:SOURce1:PERiod?` | 查询 AFG1 输出周期 | 可用 | 正常返回 |
| `:SOURce1:PHASe?` | 查询 AFG1 输出相位 | 可用 | 正常返回 |
| `:SOURce1:VOLTage?` | 查询 AFG1 输出幅度 | 可用 | 正常返回 |
| `:SOURce1:VOLTage:HIGH?` | 查询 AFG1 高电平 | 可用 | 正常返回 |
| `:SOURce1:VOLTage:LOW?` | 查询 AFG1 低电平 | 可用 | 正常返回 |
| `:SOURce1:VOLTage:OFFSet?` | 查询 AFG1 直流偏置 | 可用 | 正常返回 |
| `:SOURce1:AM:STATe?` | 查询 AFG1 AM 调制开关 | 可用 | 正常返回 |
| `:SOURce:FM:STATe?` | 查询 AFG FM 调制状态，但该写法缺少通道号 | 写法不完整 | 应指定通道，改用 :SOURce1:FM:STATe? |
| `:SOURce1:PM:STATe?` | 查询 AFG1 PM 调制开关 | 可用 | 正常返回 |

## 波形录制

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:RECord:CURRent?` | 查询普通录制当前帧 | 可用 | 正常返回 |
| `:RECord:ENABle?` | 查询普通录制功能开关 | 可用 | 正常返回 |
| `:RECord:FRAMes?` | 查询普通录制总帧数 | 可用 | 正常返回 |
| `:RECord:PLAY?` | 查询普通录制播放状态 | 可用 | 正常返回 |
| `:RECord:STARt?` | 查询普通录制起始帧 | 可用 | 正常返回 |
| `:RECord:WRECord:ENABle?` | 查询快速波形录制开关 | 可用 | 正常返回 |
| `:RECord:WRECord:FINTerval?` | 查询快速录制帧间隔 | 可用 | 正常返回 |
| `:RECord:WRECord:FMAX?` | 查询快速录制最大帧数 | 可用 | 正常返回 |
| `:RECord:WRECord:FRAMes?` | 查询快速录制目标帧数 | 可用 | 正常返回 |
| `:RECord:WRECord:OPERate?` | 查询快速录制运行状态 | 可用 | 正常返回 |
| `:RECord:WRECord:PROMpt?` | 查询快速录制提示状态 | 可用 | 正常返回 |
| `:RECord:WREPlay:DIRection?` | 查询快速回放方向 | 可用 | 正常返回 |
| `:RECord:WREPlay:FCURrent?` | 查询快速回放当前帧 | 可用 | 正常返回 |
| `:RECord:WREPlay:FEND?` | 查询快速回放结束帧 | 可用 | 正常返回 |
| `:RECord:WREPlay:FINTerval?` | 查询快速回放帧间隔 | 可用 | 正常返回 |
| `:RECord:WREPlay:FMAX?` | 查询快速回放可用最大帧数 | 可用 | 正常返回 |
| `:RECord:WREPlay:FSTart?` | 查询快速回放起始帧 | 可用 | 正常返回 |
| `:RECord:WREPlay:MODE?` | 查询快速回放模式 | 可用 | 正常返回 |
| `:RECord:WREPlay:OPERate?` | 查询快速回放运行状态 | 可用 | 正常返回 |

## 保存

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:SAVE:IMAGe:COLor?` | 查询截图保存颜色模式 | 可用 | 正常返回 |
| `:SAVE:IMAGe:FORMat?` | 查询截图保存格式 | 可用 | 正常返回 |
| `:SAVE:IMAGe:HEADer?` | 查询截图是否包含界面标题栏 | 可用 | 正常返回 |
| `:SAVE:IMAGe:INVert?` | 查询截图颜色反转状态 | 可用 | 正常返回 |
| `:SAVE:OVERlap?` | 查询保存同名文件时的覆盖策略 | 可用 | 正常返回 |
| `:SAVE:PREFix?` | 查询自动保存文件名前缀 | 可用 | 正常返回 |
| `:SAVE:STATus?` | 查询最近一次保存操作状态 | 可用 | 正常返回 |

## 搜索

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:SEARch:COUNt?` | 查询搜索命中总数 | 可用 | 正常返回 |
| `:SEARch:EDGE:SLOPe?` | 查询边沿搜索斜率 | 可用 | 正常返回 |
| `:SEARch:EDGE:SOURce?` | 查询边沿搜索信源 | 可用 | 正常返回 |
| `:SEARch:EDGE:THReshold?` | 查询边沿搜索阈值 | 可用 | 正常返回 |
| `:SEARch:EVENt?` | 查询当前搜索事件序号 | 可用 | 正常返回 |
| `:SEARch:MODE?` | 查询搜索类型 | 可用 | 正常返回 |
| `:SEARch:PULSe:LWIDth?` | 查询脉宽搜索下限 | 可用 | 正常返回 |
| `:SEARch:PULSe:POLarity?` | 查询脉宽搜索极性 | 可用 | 正常返回 |
| `:SEARch:PULSe:QUALifier?` | 查询脉宽搜索比较条件 | 可用 | 正常返回 |
| `:SEARch:PULSe:SOURce?` | 查询脉宽搜索信源 | 可用 | 正常返回 |
| `:SEARch:PULSe:THReshold?` | 查询脉宽搜索阈值 | 可用 | 正常返回 |
| `:SEARch:PULSe:UWIDth?` | 查询脉宽搜索上限 | 可用 | 正常返回 |
| `:SEARch:STATe?` | 查询搜索功能运行状态 | 可用 | 正常返回 |

## 系统

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:SYSTem:AOUTput?` | 查询后面板辅助输出功能 | 可用 | 正常返回 |
| `:SYSTem:AUToscale?` | 查询自动缩放状态 | 可用 | 正常返回 |
| `:SYSTem:BEEPer?` | 查询蜂鸣器开关 | 可用 | 正常返回 |
| `:SYSTem:DATE?` | 查询系统日期 | 可用 | 正常返回 |
| `:SYSTem:DGSTatus?` | 查询仪器是否安装 DG 信号发生器模块 | 可用 | 正常返回 |
| `:SYSTem:GAMount?` | 查询屏幕水平方向网格数量 | 可用 | 正常返回 |
| `:SYSTem:GPIB?` | 查询 GPIB 地址 | 可用 | 正常返回 |
| `:SYSTem:LOCKed?` | 查询屏幕和按键锁定状态 | 可用 | 正常返回 |
| `:SYSTem:MODules?` | 查询仪器模块信息 | 可用 | 正常返回 |
| `:SYSTem:PON?` | 查询上电初始化设置 | 可用 | 正常返回 |
| `:SYSTem:PSTatus?` | 查询通电后直接开机或等待电源键的电源策略 | 可用 | 正常返回 |
| `:SYSTem:RAMount?` | 查询屏幕垂直方向网格数量 | 可用 | 正常返回 |
| `:SYSTem:RCLock?` | 查询 10 MHz 参考时钟的关闭、输入或输出模式 | 可用 | 正常返回 |
| `:SYSTem:STIMe?` | 查询屏幕是否显示系统日期和时间 | 可用 | 正常返回 |
| `:SYSTem:TIME?` | 查询系统时间 | 可用 | 正常返回 |
| `:SYSTem:VERSion?` | 查询仪器支持的 SCPI 版本 | 可用 | 正常返回 |

## 时基

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:TIMebase:DELay:ENABle?` | 查询延迟时基开关 | 可用 | 正常返回 |
| `:TIMebase:DELay:OFFSet?` | 查询延迟时基水平偏移 | 可用 | 正常返回 |
| `:TIMebase:DELay:SCALe?` | 查询延迟时基档位 | 可用 | 正常返回 |
| `:TIMebase:HREFerence:MODE?` | 查询水平参考点模式 | 可用 | 正常返回 |
| `:TIMebase:HREFerence:POSition?` | 查询水平参考点位置 | 可用 | 正常返回 |
| `:TIMebase:MAIN:OFFSet?` | 查询主时基水平偏移 | 可用 | 正常返回 |
| `:TIMebase:MAIN:SCALe?` | 查询主时基档位 | 可用 | 正常返回 |
| `:TIMebase:MODE?` | 查询时基模式 | 可用 | 正常返回 |
| `:TIMebase:ROLL?` | 查询滚动显示状态 | 可用 | 正常返回 |
| `:TIMebase:VERNier?` | 查询水平时基细调状态 | 可用 | 正常返回 |
| `:TIMebase:XY:ENABle?` | 查询 XY 显示开关 | 可用 | 正常返回 |
| `:TIMebase:XY:GRID?` | 查询 XY 显示网格 | 可用 | 正常返回 |
| `:TIMebase:XY:X?` | 查询 XY 模式 X 轴信源 | 可用 | 正常返回 |
| `:TIMebase:XY:Y?` | 查询 XY 模式 Y 轴信源 | 可用 | 正常返回 |
| `:TIMebase:XY:Z?` | 查询 XY 模式 Z 轴信源 | 可用 | 正常返回 |

## 触发通用

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:TRIGger:COUPling?` | 查询触发耦合方式 | 可用 | 正常返回 |
| `:TRIGger:HOLDoff?` | 查询触发释抑时间 | 可用 | 正常返回 |
| `:TRIGger:MODE?` | 查询当前触发类型 | 可用 | 正常返回 |
| `:TRIGger:NREJect?` | 查询触发噪声抑制状态 | 可用 | 正常返回 |
| `:TRIGger:POSition?` | 查询触发位置 | 可用 | 正常返回 |
| `:TRIGger:STATus?` | 查询当前触发状态 | 可用 | 正常返回 |
| `:TRIGger:SWEep?` | 查询触发扫描方式 | 可用 | 正常返回 |

## 触发类型

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:TRIGger:EDGE:SOURce?` | 查询边沿触发信源 | 可用 | 正常返回 |
| `:TRIGger:EDGE:SLOPe?` | 查询边沿触发斜率 | 可用 | 正常返回 |
| `:TRIGger:EDGE:LEVel?` | 查询边沿触发电平 | 可用 | 正常返回 |
| `:TRIGger:PULSe:SOURce?` | 查询脉宽触发信源 | 可用 | 正常返回 |
| `:TRIGger:PULSe:WHEN?` | 查询脉宽触发条件 | 可用 | 正常返回 |
| `:TRIGger:PULSe:POLarity?` | 查询脉宽触发极性 | 可用 | 正常返回 |
| `:TRIGger:PULSe:LWIDth?` | 查询脉宽触发时间下限 | 可用 | 正常返回 |
| `:TRIGger:PULSe:UWIDth?` | 查询脉宽触发时间上限 | 可用 | 正常返回 |
| `:TRIGger:SLOPe:SOURce?` | 查询斜率触发信源 | 可用 | 正常返回 |
| `:TRIGger:SLOPe:WHEN?` | 查询斜率触发条件 | 可用 | 正常返回 |
| `:TRIGger:SLOPe:POLarity?` | 查询斜率触发极性 | 可用 | 正常返回 |
| `:TRIGger:SLOPe:TLOWer?` | 查询斜率触发时间下限 | 可用 | 正常返回 |
| `:TRIGger:SLOPe:TUPPer?` | 查询斜率触发时间上限 | 可用 | 正常返回 |
| `:TRIGger:RUNT:SOURce?` | 查询欠幅触发信源 | 可用 | 正常返回 |
| `:TRIGger:RUNT:WHEN?` | 查询欠幅触发条件 | 可用 | 正常返回 |
| `:TRIGger:RUNT:POLarity?` | 查询欠幅脉冲极性 | 可用 | 正常返回 |
| `:TRIGger:TIMeout:SOURce?` | 查询超时触发信源 | 可用 | 正常返回 |
| `:TRIGger:TIMeout:SLOPe?` | 查询超时触发边沿 | 可用 | 正常返回 |
| `:TRIGger:TIMeout:TIME?` | 查询超时触发时间 | 可用 | 正常返回 |
| `:TRIGger:VIDeo:SOURce?` | 查询视频触发信源 | 可用 | 正常返回 |
| `:TRIGger:VIDeo:MODE?` | 查询视频触发行场模式 | 可用 | 正常返回 |
| `:TRIGger:VIDeo:STANdard?` | 查询视频触发制式 | 可用 | 正常返回 |

## 波形传输

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:WAVeform:SOURce?` | 查询当前波形传输信源 | 可用 | 正常返回 |
| `:WAVeform:MODE?` | 查询波形传输范围模式 | 可用 | 正常返回 |
| `:WAVeform:FORMat?` | 查询波形传输数据格式 | 可用 | 正常返回 |
| `:WAVeform:POINts?` | 查询波形传输点数 | 可用 | 正常返回 |
| `:WAVeform:STARt?` | 查询波形传输起始点 | 可用 | 正常返回 |
| `:WAVeform:STOP?` | 查询波形传输结束点 | 可用 | 正常返回 |
| `:WAVeform:XINCrement?` | 查询相邻波形点的时间间隔 | 可用 | 正常返回 |
| `:WAVeform:XORigin?` | 查询波形时间轴原点 | 可用 | 正常返回 |
| `:WAVeform:XREFerence?` | 查询波形时间轴参考点 | 可用 | 正常返回 |
| `:WAVeform:YINCrement?` | 查询采样码对应的电压步进 | 可用 | 正常返回 |
| `:WAVeform:YORigin?` | 查询波形电压换算原点 | 可用 | 正常返回 |
| `:WAVeform:YREFerence?` | 查询波形电压换算参考值 | 可用 | 正常返回 |
| `:WAVeform:PREamble?` | 查询波形点数及时间、电压轴完整标定参数 | 可用 | 正常返回 |

## 协议总线

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:BUS1:MODE?` | 查询 BUS1 解码协议类型 | 可用 | 正常返回 |
| `:BUS1:DISPlay?` | 查询 BUS1 显示开关 | 可用 | 正常返回 |
| `:BUS1:FORMat?` | 查询 BUS1 解码数据显示格式 | 可用 | 正常返回 |
| `:BUS1:EVENt?` | 查询 BUS1 当前解码事件序号 | 可用 | 正常返回 |
| `:BUS1:LABel?` | 查询 BUS1 标签显示状态 | 可用 | 正常返回 |
| `:BUS1:POSition?` | 查询 BUS1 垂直位置 | 可用 | 正常返回 |
| `:BUS1:RS232:TX?` | 查询 RS232 发送信号源 | 可用 | 正常返回 |
| `:BUS1:RS232:RX?` | 查询 RS232 接收信号源 | 可用 | 正常返回 |
| `:BUS1:RS232:BAUD?` | 查询 RS232 波特率 | 可用 | 正常返回 |
| `:BUS1:IIC:SCLK:SOURce?` | 查询 I²C 时钟信号源 | 可用 | 正常返回 |
| `:BUS1:IIC:SDA:SOURce?` | 查询 I²C 数据信号源 | 可用 | 正常返回 |
| `:BUS1:SPI:SCLK:SOURce?` | 查询 SPI 时钟信号源 | 可用 | 正常返回 |
| `:BUS1:SPI:MISO:SOURce?` | 查询 SPI MISO 信号源 | 可用 | 正常返回 |
| `:BUS1:SPI:MOSI:SOURce?` | 查询 SPI MOSI 信号源 | 可用 | 正常返回 |

## 伯德图

| 命令 | 作用 | 实测状态 | 返回或限制 |
|---|---|---|---|
| `:BODeplot:ENABle?` | 查询伯德图功能开关 | 可用 | 正常返回 |
| `:BODeplot:RUNStop?` | 查询伯德图扫频运行状态 | 可用 | 正常返回 |
| `:BODeplot:SWEeptype?` | 查询扫频类型 | 可用 | 正常返回 |
| `:BODeplot:REF:IN?` | 查询伯德图输入参考通道 | 可用 | 正常返回 |
| `:BODeplot:REF:OUT?` | 查询伯德图输出测量通道 | 可用 | 正常返回 |
| `:BODeplot:STARt?` | 查询扫频起始频率 | 可用 | 正常返回 |
| `:BODeplot:STOP?` | 查询扫频终止频率 | 可用 | 正常返回 |
| `:BODeplot:POINts?` | 查询扫频点数 | 可用 | 正常返回 |
| `:BODeplot:OUTput:VOLTage?` | 查询扫频激励电压 | 可用 | 正常返回 |
| `:BODeplot:AFG:SOURce?` | 查询伯德图使用的 AFG 通道 | 可用 | 正常返回 |
| `:BODeplot:AFG:LOAD?` | 查询伯德图激励输出负载 | 可用 | 正常返回 |
