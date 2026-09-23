# 案例: PIR 灵敏度 A/B 实验
# Case: PIR Sensitivity A/B Experiment

本文件是 `device-setting-override` Skill 的具体应用案例参考。**不是固定模板**,
其它参数实验也参照同样的步骤即可,只需要替换:

- xlsx 中两组 SN
- 两份 overrideJson 内容
- experiment-prefix

## 背景 / Context

某型号设备 PIR 灵敏度需要做 A/B 对照:23k SN 用高灵敏(灵敏组),23k SN 用低灵敏
(不灵敏组),其它型号默认设备保持不变。整个实验周期通过监控对比两组的运动检测
触发率/误报率,决定后续是否调整型号默认值。

## 实验载荷 / Experiment payloads

两组都只覆盖 `value.pirSensor.MotionDetection.*` 和 `value.pirSensor.P824M.*`,
其它顶层字段(`liveSpeakerVolume` / `codecProfile` / `pir` 标量等)保留型号默认。

### 灵敏组 / Sensitive group

10 宏块判定有效运动 + 1/10 有效运动帧 + PIR 阈值 5。

```json
{
  "value": {
    "pirSensor": {
      "MotionDetection": {
        "1": {"areaThresh": 0.000694, "mdRatioThresh": 0.10, "sadMoveThresh": 0.0039215},
        "2": {"areaThresh": 0.000694, "mdRatioThresh": 0.10, "sadMoveThresh": 0.0039215},
        "3": {"areaThresh": 0.000694, "mdRatioThresh": 0.10, "sadMoveThresh": 0.0039215}
      },
      "P824M": {
        "1": {"voltageThresh": 5, "windowSize": 2, "pulseCounterThresh": 2},
        "2": {"voltageThresh": 5, "windowSize": 2, "pulseCounterThresh": 2},
        "3": {"voltageThresh": 5, "windowSize": 2, "pulseCounterThresh": 2}
      }
    }
  }
}
```

### 不灵敏组 / Insensitive group

120 宏块 + 5/10 有效运动帧 + PIR 阈值 100。

```json
{
  "value": {
    "pirSensor": {
      "MotionDetection": {
        "1": {"areaThresh": 0.008333, "mdRatioThresh": 0.50, "sadMoveThresh": 0.0039215},
        "2": {"areaThresh": 0.008333, "mdRatioThresh": 0.50, "sadMoveThresh": 0.0039215},
        "3": {"areaThresh": 0.008333, "mdRatioThresh": 0.50, "sadMoveThresh": 0.0039215}
      },
      "P824M": {
        "1": {"voltageThresh": 100, "windowSize": 2, "pulseCounterThresh": 2},
        "2": {"voltageThresh": 100, "windowSize": 2, "pulseCounterThresh": 2},
        "3": {"voltageThresh": 100, "windowSize": 2, "pulseCounterThresh": 2}
      }
    }
  }
}
```

> `MotionDetection` 和 `P824M` 下的 `1` / `2` / `3` 对应 UI 上低 / 中 / 高三档 PIR
> 灵敏度;override 改的是"每档对应的物理参数"。详见
> `docs/product/user-stories/setting-override.md` "Reference: Setting Response Shape"。

## xlsx 形状 / Input file shape

| 项 | 值 |
|:--|:--|
| Sheet 1 | `灵敏组` — column A 表头 `serial_number`,以下每行一个 32-hex SN(23,283 行) |
| Sheet 2 | `不灵敏组` — 同上结构(23,284 行) |
| 跨 sheet 重叠 | 0 |
| Total | 46,567 unique SN |

## 完整执行步骤 / Full procedure

### 第 0 步:准备本地两份 JSON 文件

把上面两段 JSON 各存成一个 `.json` 文件,例如:
- `~/work/pir/sensitive.json`
- `~/work/pir/insensitive.json`

### 第 1 步:Dry-run 预演(staging-us)

```bash
 export IOT_ADMIN_AK='<staging-us 的 inner-api accessKey>'
 export IOT_ADMIN_SK='<staging-us 的 inner-api secret base64>'

python3 scripts/upsert.py \
  --env staging-us \
  --input ~/Downloads/pir灵敏度实验.xlsx \
  --sensitive-json   @~/work/pir/sensitive.json \
  --insensitive-json @~/work/pir/insensitive.json \
  --experiment-prefix pir_sensitivity_2026q2 \
  --expire-at 0
```

预期输出:
```
[plan] env=staging-us host=https://api-staging-us.vicohome.io
[plan] experiment-prefix=pir_sensitivity_2026q2 separator='_' expireAt=0 repush=False
[plan] group=sensitive   sheet=灵敏组   experiment=pir_sensitivity_2026q2_sensitive   sns=23283 batches=24
[plan] group=insensitive sheet=不灵敏组 experiment=pir_sensitivity_2026q2_insensitive sns=23284 batches=24
[plan] dry-run=YES (no POST)
```

### 第 2 步:Smoke 测试(只跑 sensitive 第 1 批,1000 SN)

```bash
python3 scripts/upsert.py \
  --env staging-us \
  --input ~/Downloads/pir灵敏度实验.xlsx \
  --sensitive-json   @~/work/pir/sensitive.json \
  --insensitive-json @~/work/pir/insensitive.json \
  --experiment-prefix pir_sensitivity_2026q2 \
  --expire-at 0 \
  --only-group sensitive --limit-batches 1 \
  --confirm
```

人工抽查清单:
1. 命令输出有 `[ok ] batch#0000 ... succeeded=1000 repushFailed=0`
2. 任挑 1 个该批 SN 调 `deviceMsg/setting`,验证 `data.value.pirSensor.MotionDetection.1.areaThresh = 0.000694` 和 `data.value.pirSensor.P824M.1.voltageThresh = 5`
3. 验证 `data.value` 下其它字段(`liveSpeakerVolume` / `pir` / `codecProfile`)与未覆盖时一致
4. Grafana 看 `setting_override_applied_total` 有增量,`setting_override_failed_total` 无新增
5. `out/run-<ts>-staging-us/failed/` 为空

### 第 3 步:全量铺开(46.6k SN,~47 批)

```bash
python3 scripts/upsert.py \
  --env staging-us \
  --input ~/Downloads/pir灵敏度实验.xlsx \
  --sensitive-json   @~/work/pir/sensitive.json \
  --insensitive-json @~/work/pir/insensitive.json \
  --experiment-prefix pir_sensitivity_2026q2 \
  --expire-at 0 \
  --start-batch 1 \
  --qps-sleep 1.0 \
  --confirm
```

`--start-batch 1` 跳过 smoke 那批(已写过,跳过更干净);`--qps-sleep 1.0` 拉长批间隔。
总耗时约 60-90 秒。

### 第 4 步:实验中途监控

```bash
# 全局活跃实验聚合
python3 scripts/admin_cli.py --env staging-us stats

# 单 SN 抽查
python3 scripts/admin_cli.py --env staging-us \
  get --sn 82ceb8756cf6d3fa7d0923a72a677759
```

`stats` 输出预期 `pir_sensitivity_2026q2_sensitive: 23283` 和
`pir_sensitivity_2026q2_insensitive: 23284`。

### 第 5 步:实验结束清理(>1000 SN 必须用 list + sn-file 模式)

```bash
# 1) 拉灵敏组全量 SN
python3 scripts/admin_cli.py --env staging-us \
  list --experiment pir_sensitivity_2026q2_sensitive \
  --all --page-size 1000 \
  --out out/sensitive-sns.jsonl
jq -r .serialNumber out/sensitive-sns.jsonl > out/sensitive-sns.txt

# 2) 拉不灵敏组全量 SN
python3 scripts/admin_cli.py --env staging-us \
  list --experiment pir_sensitivity_2026q2_insensitive \
  --all --page-size 1000 \
  --out out/insensitive-sns.jsonl
jq -r .serialNumber out/insensitive-sns.jsonl > out/insensitive-sns.txt

# 3) 分批删除 + repush
python3 scripts/admin_cli.py --env staging-us \
  delete --sn-file out/sensitive-sns.txt --repush --confirm
python3 scripts/admin_cli.py --env staging-us \
  delete --sn-file out/insensitive-sns.txt --repush --confirm
```

为什么不用 `delete --experiment <tag> --repush --confirm`:
后端 `collectSnsByExperiment` 硬截断 1000 个 SN(见 `references/architecture.md` §7),
4w 设备删完后只前 1000 个会被立即重发,其余会等到下次主动拉 setting 才回到型号默认。

## 验证不动 sibling key 的代码证据

合并语义证明在 `references/architecture.md` §3。具体到 PIR 实验:

1. 调用端 `DeviceSettingService.applySettingOverride` 取 `overrideData.getJSONObject("value")` 后传给 merger
2. merger 顶层只看到 `pirSensor` 这一个 key,只对它递归;`liveSpeakerVolume` / `pir` / `codecProfile` 等同级 sibling 完全不被访问
3. 进入 `pirSensor` 后只看到 `MotionDetection` / `P824M` 两个 key,只对它们递归;若 base 有其它芯片家族(如 `Foo123` / `Bar456`)同级保留

US 文档 AC-1.2 即是这条语义的验收标准。
