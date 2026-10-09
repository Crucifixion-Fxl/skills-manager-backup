# CSV 数据契约

默认格式参照用户提供的 `TrackMan_2026-07-28_all_swings.csv`（153 列）和 `TrackMan_2026-07-28_ball_trajectory.csv`（11 列）。仅提取列结构、编码、数据规则；不将参考文件中的姓名、日期、报告 Id 或数值硬编码为新报告数据。

## all_swings：每杆一行

原 153 列及顺序固定，接口新增列或追溯列追加在末尾。源没有的参考列保留表头、单元格留空。主要映射：

| 列 | 来源/规则 |
| --- | --- |
| SourceReportId | 请求的 ActivityId/ReportId；离线没有请求上下文时取响应 Id |
| ReportId / ManifestId / ReportKind / ReportTime / ReportUpdated | report.Id / ManifestId / Kind / Time / Updated；没有则空 |
| GroupId / GroupDate / GroupClub / GroupBall | 当前 StrokeGroup.Id / Date / Club / Ball |
| Player_* | group.Player 按下划线递归展平 |
| StrokeIndex | 分组内从 1 开始的数组顺序，不是全局时间序号 |
| StrokeId / StrokeTime / StrokeClub / StrokeBall | stroke.Id / Time / Club / Ball，不造缺失字段 |
| Raw_* | stroke.Measurement 所有字段，字典递归展平、数组紧凑 JSON |
| Normalized_* | stroke.NormalizedMeasurement 所有字段，相同展开规则 |
| MeasurementDetails_* / ImpactLocation_* | 杆级元数据递归展平；不与 Raw 同名字段合并 |
| Client_* / Environment_* / ReportUser_* | 报告对应对象，递归展平 |
| Groups_JSON / Settings_JSON / Sponsors_JSON / Videos_JSON | 对应完整值的 JSON；缺失则空 |
| Schema_JSON | report.$schema，若不存在则取 report.Schema，JSON 编码 |

参考表中的 `Raw_BallTrajectory`、`Normalized_BallTrajectory` 仍保留完整点数组 JSON。`Raw_ClubTrajectory` 等其他轨迹也在结果表中保留；单独点表只展开球轨迹。

### 数据可用性标记

- `HasMeasurementData`、`HasNormalizedMeasurementData`：相应对象是否包含 Id/Time/Kind 以外的非 null、非空数组/字典字段。仅有 Id 的记录为 False。
- `IsPlaceholderRecord`：两个版本都没有上述数据时为 True。这是数据结构标记，不是质量判断。
- `HasBallSpeed`、`HasClubSpeed`：至少一个版本中该指标非 null。0 是存在的数值。
- `RawBallTrajectoryPointCount`、`RawClubTrajectoryPointCount`、`NormalizedBallTrajectoryPointCount`、`NormalizedClubTrajectoryPointCount`：对应数组长度；缺失/null/空数组均为 0。

缺 Carry 不等于占位记录，也不等于无轨迹。源精度标记 `Raw_ReducedAccuracy`、`Raw_InvalidNoTarget` 等保持原值，不按导出成功推断测量可靠性。

### 多报告追溯列

末尾追加 `ExportId`、`ReportIndex`、`GroupIndex`、`ShotKey`、`SourceURL`、`ResponseSHA256`、`NormalizedDisplayRequested`、`Request_JSON`。接口额外字段亦追加，已见 ImpactLocation 的 3 列。展平列名冲突会报错，不静默覆盖。

`ShotKey = report_index:group_index:stroke_id`，两个序号均从 1 开始。重复输入保留为不同出现项；同一组重复 stroke_id 报错。跨导出长期比较使用源报告身份和 StrokeId，不能只按出现序号比较。

## ball_trajectory：每个球轨迹点一行

前 11 列固定为：

```text
GroupId,GroupDate,GroupClub,StrokeIndex,StrokeId,StrokeTime,DataSpace,PointIndex,X,Y,Z
```

- 每杆先 raw 后 normalized，分别展开其实际 BallTrajectory；不按时间重排。
- `PointIndex` 在每杆、每个 DataSpace 内从 0 重新计数。
- XYZ 原值原顺序写出，点额外属性追加为 `Point_<field>`。
- 有点时追加 `SourceReportId`、`ReportId`、`ExportId`、`ReportIndex`、`GroupIndex`、`ShotKey`。完全没有轨迹时输出只有 11 列表头的空表。
- 结果行与轨迹行按 `(ExportId, ShotKey)` 关联，唯一点键为 `(ExportId, ShotKey, DataSpace, PointIndex)`。
- 没有轨迹的杆在结果表中存在，点表不写虚假的 0,0,0 占位点。

## 编码、空值和单位

UTF-8 BOM、逗号分隔、CRLF 换行；CSV 引号按标准转义。字符串内逗号/引号/换行保留。布尔值 True/False；有限数字不人为四舍五入；数组/对象紧凑 JSON。空单元格表示源缺失或 null，不能当作 0；需要区分这两种状态时使用 `--format json`。

时间字符串及 UTC 偏移保持源值，无本地时区转换。文件日期来自 GroupDate；本次数据为 2026-09-13，不能套用参考文件的 2026-07-28。

API X/Y/Z 未转换。当前点值与 Carry/MaxHeight/CarrySide 数值关系支持 X 前向、Y 高度、Z 横向的解释，距离与米制相符；该解释不是已验证的完整坐标规范。横向正方向、目标线/雷达关系、TeePosition 变换均未验证。

常见距离与米、球速/杆速与 m/s、发射类角度与度、转速与 rpm、HangTime 与秒相符，但脚本不做单位转换，也不为所有字段承诺单位。尤其杆级 ImpactLocation 与 Measurement 同名角度字段可能采用不同单位，禁止统一换算。网页 `u=m` 不是所有 API 字段的单位声明。

## 验证证据和来源

2026-09-14 将用户参考表显式编码的数据恢复成 API 形状，再走当前 CSV 映射：93 杆 × 153 列、2,904 点 × 11 列全部逐单元格字符串相同。其中含 4 条仅 Id 的占位记录。这个检查验证参考表兼容性，不代表重新获取了 7 月 28 日的历史 API。

同日真实请求两个用户链接：oDXJoYU 70 杆/1,664 点；M1iG5I5 71 杆/1,614 点，共 141 杆/3,278 点。均只有 Measurement，没有 NormalizedMeasurement；4 杆缺 Carry，全部保留。

接口来自官方前端 `https://web-dynamic-reports.trackmangolf.com/static/js/main.8ce347a2.chunk.js` 的 MtmReportDataService 与 ReportDataConversionService。活动接口为 `https://golf-player-activities.trackmangolf.com/api/reports/getactivityreport`。URL a/r 分别对应 ActivityId/ReportId；原始和标准化读取两个独立字段；BallTrajectory 原样映射 X/Y/Z。

接口不是承诺稳定的公开 API，响应 SHA-256 用于区分源版本。离线复现需要用户保存的原始 API 响应；CSV 本身不包含完整原始响应。
