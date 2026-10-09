# 可选 JSON 数据契约：trackman-shot-export.v1

仅适用于显式 `--format json`。默认 CSV 契约见 [schema.md](schema.md)。

两个文件是 UTF-8 JSON 对象，共享 `schema_version`、`export_id`、导出 UTC 时间、`sources`、`shot_count`、`ball_point_count` 和警告。`shots` 是杆数组。`ball_point_count` 汇总全部实际返回的测量版本的 BallTrajectory 点数；当前样本只返回 Measurement。

## 来源与主键

`sources` 包含输入链接、解析后的链接、API 请求参数、响应字节 SHA-256，以及报告 Id/Kind/Time/Settings/Environment/Client 和各分组基本元数据。它不保存账号邮箱/姓名等报告级身份信息。杆级 API 元数据原样保留，分享前按实际内容判断。

`report_index`、`group_index`、`shot_index` 从 1 开始，是此次导出中的出现顺序；`shot_index` 在分组内重新计数。`shot_key = report_index:group_index:shot_id` 在此次导出内唯一，跨导出比较需结合来源身份和 shot_id。`group_id` 在真实数据的不同球杆分组间重复，不能认为它唯一标识分组。同组重复 shot_id 报错，避免隐藏重复数据。

## 每杆结果

`shots[i]` 包含身份字段、`stroke_metadata`、`measurements`、`trajectory_counts`。

- `stroke_metadata` 保存除 Measurement / NormalizedMeasurement 外的全部杆字段，包括原始 Id、Time、Club、Ball、ImpactLocation、MeasurementDetails、Videos 等实际存在的字段。
- `measurements.Measurement`：原始测量字段，移除名字含 Trajectory 的字段。
- `measurements.NormalizedMeasurement`：标准化测量字段；整个版本不存在则为 `null`。
- `trajectory_counts.<variant>.<trajectory_name>`：对应点数；源为 null 时保留 null。

BallSpeed、ClubSpeed、AttackAngle、LaunchAngle、SpinRate、Carry、Total、CarrySide、TotalSide、MaxHeight、HangTime 等字段按 API 原名保留，没有按固定列清单丢弃其他指标。源中不存在的指标继续缺省，不能解释为 0；源中显式 null 仍保留 null。`ReducedAccuracy`、`InvalidNoTarget` 保留原值；空标记不构成物理精度保证。

## 每杆轨迹

`shots[i].measurements.<variant>.<trajectory_name>` 为 API 点数组（如 BallTrajectory、ClubTrajectory），点对象原样保存。例：

```json
{"X": 23.48113255460433, "Y": 14.785260516120088, "Z": 0.7529643700278663}
```

数组下标为点序，**不是时间**。缺失轨迹字段、显式 null、空数组 `[]` 三种状态分别保留。无轨迹的杆依旧存在于两文件中。未来源点出现时间等额外字段时原样保留；当前样本没有每点时间，不能将 HangTime 均分来伪造时间。

API X/Y/Z 不转换。由当前点值与 Carry/MaxHeight/CarrySide 的数值关系可推断 X 为前向、Y 为高度、Z 为横向，距离与米制相符；这属于样本解释，不是已验证的完整坐标系规范。横向正方向、雷达与目标线关系、TeePosition 坐标变换均未验证，机器人或其他坐标系转换必须另行确认。

结果常见距离看起来为米、球速/杆速看起来为 m/s、发射类角度看起来为度、转速为 rpm、HangTime 为秒，但本脚本不依赖这些解释、不转换数值，也不为全部字段承诺单位。特别是 stroke_metadata.ImpactLocation 与 Measurement 中同名角度字段可能不同，不能统一转换。要生成有单位列名或进行物理计算时，先核对对应字段的官方转换逻辑/文档。URL `u=m` 是网页显示设置，不是每个 API 字段的单位声明。

## 原始与标准化

官方前端分别读取 stroke.Measurement 与 stroke.NormalizedMeasurement；报告显示开关不改变这个来源区别。导出不模拟标准化、不做显示层回退。

2026-09-14 实测两个用户链接：oDXJoYU 返回 70 杆（5 组），M1iG5I5 返回 71 杆（3 组）。两链接均 `nd=true`，请求 Premium / 0 m / 25 Celsius，但接口仅返回 Measurement，报告 Settings.ShowNormalizedData 为 false。4 杆没有 Carry，保留全部杆及已有轨迹。

## 接口依据与复现边界

2026-09-14 检查官方网页资源：

- https://web-dynamic-reports.trackmangolf.com/static/js/main.8ce347a2.chunk.js
- MtmReportDataService.getReportData：`a` 对应 ActivityId/getactivityreport，`r` 对应 ReportId/getreport。
- POST https://golf-player-activities.trackmangolf.com/api/reports/getactivityreport
- 请求体可含 ActivityId、Altitude、Temperature、BallType；英尺/华氏参数转换后请求。
- ReportDataConversionService.getReportMeasurement / getReportNormalizedMeasurement 分开读取版本；BallTrajectory.map 原样读取 X/Y/Z。

这些是官方站点当前客户端实现证据，不代表 API 稳定性承诺。网络数据可能变动，SHA-256 用来区分响应版本。离线复现需要调用方保存的原始 API 响应，导出文件自身不是原始响应备份。
