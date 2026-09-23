# 模块地图、复核结果、报告与留存格式

## 结果目录

```
~/.local/share/architecture-smell-scan/<仓名>/<YYYY-MM-DD>/
  raw.json            collect 输出
  module-map.json     AI 写的模块地图（下期以它为初稿）
  findings.json       analyze 输出（候选级，含上期候选对比）
  reviewed.json       AI 复核后的最终发现（下期 compare 用它）
  comparison.json     compare 输出：新增 / 已修 / 仍在 / 改判
  report.md           完整报告
  buzz-summary.md     频道摘要
```

上期 = 同一仓名下日期最新的目录。仓名只取 `[A-Za-z0-9._-]`。结果不写进被扫仓。

<a id="module-map"></a>
## module-map.json

| 字段 | 说明 |
|---|---|
| `modules[].name` | 模块名（报告里用；跨期保持不变） |
| `modules[].paths` | 仓内相对路径前缀**列表**；`"."` 表示仓根目录下的文件；也可以写单个文件路径 |
| `modules[].exclude` | 可选，排除的路径前缀 |
| `modules[].role` | `base`（底座 / 公共件）、`root`（组装根）、`business`、`product`（产品侧，M05 不查）、`leaf`（规范要求不依赖任何层，如 util） |
| `modules[].layer` | 可选，层名，必须出现在 `layers` 里（否则 `map_warnings` 提示、M01 对它不生效） |
| `modules[].public` | 可选，公开接口路径（ports / api 包）；其他模块只能依赖这些路径（M02） |
| `modules[].canonical` | 可选，`true` 表示名字是正本定稿的层名 / 子目录名，不报 M14 |
| `modules[].renamed_from` | 可选，旧模块名列表；上期指纹里的旧名按它映射 |
| `modules[].duty` / `basis` | 一句话职责 / 判断依据（写报告用，脚本不读） |
| `layers` | 可选，从上到下的层名顺序（M01）；后端仓只在有书面来源时写 |
| `isolated` | 可选，同层互不依赖的模块组，如 `[["feature_a", "feature_b"]]`（M02） |
| `allow` | 可选，明确允许的模块依赖，如 `[["a", "b"]]`（豁免 M01 / M02 / M04） |
| `controlled_libs` | 可选，`[{"lib": "<import 前缀>", "only_in": ["<模块>"]}]`（M03）；不填报「M03 本期未查」 |
| `product_names` | 可选，平台代码里不该出现的产品 / 品牌名（M05）；不填报「M05 本期未查」 |
| `thresholds` | 可选，本仓的阈值，按仓留存；命令行 `--threshold` 临时覆盖 |

被扫仓的 `.arch.yaml` 用同样的字段（`paths` 必须是列表），由 AI 合并进 module-map；脚本不读 `.arch.yaml`。

示例（iot-backend 试跑用的 12 模块地图，pkg/* 标为底座）：

```json
{
  "modules": [
    {"name": "server", "paths": ["cmd/server"], "role": "root", "duty": "进程入口"},
    {"name": "common", "paths": ["internal/common"], "role": "base",
     "duty": "config / nacos / client 等公共件", "basis": "目录名；被 gateway、modelengine 依赖"},
    {"name": "shadow", "paths": ["internal/shadow"], "role": "business", "duty": "设备影子"},
    {"name": "command", "paths": ["internal/command"], "role": "business", "duty": "指令下发"},
    {"name": "gateway", "paths": ["internal/gateway"], "role": "business", "duty": "设备接入"},
    {"name": "modelengine", "paths": ["internal/modelengine"], "role": "business", "duty": "物模型引擎"},
    {"name": "util", "paths": ["internal/util"], "role": "base"},
    {"name": "axdata", "paths": ["pkg/axdata"], "role": "base"},
    {"name": "logger", "paths": ["pkg/logger"], "role": "base"},
    {"name": "nacos", "paths": ["pkg/nacos"], "role": "base"},
    {"name": "redis", "paths": ["pkg/redis"], "role": "base"},
    {"name": "grpcserver", "paths": ["pkg/grpcserver"], "role": "base"}
  ]
}
```

## findings.json 关键字段

| 字段 | 说明 |
|---|---|
| `summary` | 模块数、依赖边数、环数、环内模块数、违规边数（去重后的模块对）、变更放大、有效提交数、全仓 fix 基线 `repo_fix_ratio`、有效代码行、是否查了重复代码、候选按编号计数 |
| `modules[]` | 每个模块的有效代码 `nloc`、物理行 `lines`、占比、入度 `ca` / 出度 `ce`、不稳定度、`churn`、`fixes`、`dup_lines` |
| `module_edges[]` | 模块依赖边，`count` 为 import 处数，`evidence` 为最多 5 条 `文件:行号 -> 目标`（全量在 raw.json） |
| `candidates[]` | `id`、`modules`、`detail`、`evidence`（最多 5 条）、`key`（M03 库名 / M13 表名 / M18、M19 文件路径）、`fingerprint` = 编号 \| 模块（排序后）\| key，不含行号；M18 / M19 的模块位留空（`M18\|\|文件路径`），调模块地图不影响对比 |
| `ignored` / `invalid_ignores` | 被忽略名单挡掉的候选 / 无效条目（截断显示） |
| `comparison` | 有 `--previous` 时：`new` / `fixed` / `still` / `ignored_now` / `unchecked`（候选级；本期新加进忽略名单的不算 fixed；本期没查的信号对应的旧候选进 `unchecked`，不算 fixed） |
| `unchecked_ids` | 本期没查的编号：没有可用 git 提交（没有 git 数据，或窗口内的提交全是批量提交、没改到已归入模块的代码文件）→ M11 / M12 / M19，没跑 jscpd → M17，没给清单 → M03 / M05，没有代码文件 → 全部脚本编号 |
| `thresholds` | 实际生效的阈值；与上期不同时 `notes` 里提示 |
| `renames` | module-map `renamed_from` 汇总的旧名 → 新名 |
| `map_warnings` | 模块地图字段问题（paths 不是列表、未知 role、layer 不在 layers 里等） |
| `notes` | 采集与分析提示：git / jscpd 未运行或无数据、跳过的大文件、tsconfig 解析失败、M03 / M05 未查等 |
| `unmapped_files` / `unmapped_count` | 没归入任何模块的代码文件（最多列 100 个；地图漏了就补） |
| `hot_files` / `hot_files_count` | 超长（M18）或 fix 多（M19）的文件：`path`、`module`、有效代码 `nloc`、近期提交 `churn`、`fixes`、`long`、`fix_hot`；两者兼有的（热点文件）排最前，再按 fix 数、行数排（最多列 100 个）；被忽略名单挡掉的信号不计，两项都被挡的文件不列 |

## reviewed.json

```json
{
  "items": [
    {"title": "common 装着装配代码导致 5 模块成环", "status": "confirmed",
     "fingerprints": ["M04|common+shadow|", "M06|command+common+gateway+modelengine+shadow|", "M14|common|"]},
    {"title": "capture → tools 是条件编译误报", "status": "rejected", "fingerprints": ["M07|capture+tools|"]}
  ]
}
```

- `compare` 规则：一条发现的任一成员指纹在两期都出现即为同一条；本期确认、上期没有 → 新增；两期都确认 → 仍在；上期确认、本期连候选都不再出现（也不在忽略名单里）→ 已修；上期确认、本期还在但被改判 → 改判；上期确认、成员编号本期全都没查（`unchecked_ids`）→ 未查，不算已修。`--findings` 必填。

## buzz-summary.md

```text
🏗 架构体检 · <仓名> · <YYYY-MM-DD>
<N> 个模块 · <E> 条依赖边 · <C> 个环（<M> 个模块）
和上期比：新增 <a> · 已修 <b> · 仍在 <c>        ← 首次体检写「首次体检」

<K> 条发现
1. <编号 · 编号…>  <一句话，说清哪个模块、什么问题、证据数字>
   第一步：<移哪些文件 / 拆哪条边>
…
热点文件：`<文件>`（<行数> 行 · 近半年 fix <n> 次）· …   ← 取 hot_files 前 3 个；已在主榜里的不重复；路径放反引号（路径里本身有反引号就用更长的围栏）
本期未查：<M17（无 node）/ M03、M05（未提供清单）/ X01–X03（无数据）…>
完整报告：<链接>
```

- 主榜最多 10 条，按易变性排序，M12 叠加的和热点文件（同时命中 M18、M19）往前排；同一根因合并成一条。
- 不贴源码原文；只写仓名，不写本机路径。
- 「已修」有对应提交作者时，点名感谢。

## report.md

1. **概况**：仓、HEAD、统计窗口、「应该长什么样」的来源、未查项及原因（逐条交代 `notes`）、子模块指针变更次数（这些修复在别的仓，本期看不到）
2. **模块地图**：模块 | 路径 | 职责 | 角色 / 层 | 判断依据；目录划分与模块不一致的地方
3. **发现**：每条写 编号 · 涉及模块 · 证据（文件:行号 / 数值 / 共改记录）· 规范依据（有依据的编号都要引）· 尺子判断 · 第一步 · 指纹
4. **热点文件**：`hot_files` 前 10 个：文件 | 模块 | 有效代码行 | 近期提交 | fix 次数 | 超长 / fix 多；已合并进「发现」的注明对应条目
5. **趋势**：模块数、依赖边、环数、违规边、变更放大，与上期对比
6. **新增 / 已修 / 仍在 / 改判**（来自 comparison.json）
7. **附录**：复核后未上报的候选及理由、忽略名单生效条目（作者、日期）与无效条目、遇到的规范缺口、从代码推出的分层（后端无书面来源时）

## .arch-ignore.yaml（被扫仓根目录）

```yaml
- id: M09
  modules: [shadow]
  reason: 设备影子服务以 shadow 为核心，内部已按子目录分块
  by: <姓名>
  date: 2026-09-11
```

`reason` 与 `modules` 必填；`key` 可选（M03 填库名、M13 填表名；M18 / M19 填文件路径——写了路径就按路径匹配、不看 `modules`，路径写成空字符串的条目判为无效；不写则匹配该编号该模块的所有 key）。文件超过 256KB 直接拒绝。
