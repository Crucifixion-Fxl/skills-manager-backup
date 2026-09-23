# CMS 与 Crowdin 接入

## 目录

- CMS collection、图标和跨环境 Transfer
- Crowdin key、双 App 项目与全语言拉取
- 翻译质量与 Flutter `LabFeatureStrings` 映射
- 完成证据

## 1. CMS `lab-feature-content`

### 字段

| 字段 | 必填 | 规则 |
|---|---|---|
| `key` | 是 | 唯一、创建后不可改；通常等于 NH `cms_content_key` 和 `feature_key` |
| `icon` | 是 | 实验室列表 icon |
| `iconDark` | 否 | 暗色主题 icon；缺失时回退到 `icon` |
| `image` | 否 | 详情/说明图，只有客户端真实消费时添加 |
| `metadata` | 否 | 非本地化 JSON，不放 title/description 翻译 |

`lab-feature-content.key` 和 NH `cms_content_key` 当前没有客户端维度。同一个功能在不同品牌使用不同图标时有两种合法方案：

1. 后端目录能按客户端选择不同 content key：创建品牌专属 CMS 记录。
2. 后端仍共享一个 content key：保留共享 CMS 记录，在 Flutter flavor 中按 `client + feature_key` 精确覆盖本地图标。

当前没有客户端维度时不要直接替换共享 CMS icon，否则会同步改变已经上线的其他品牌。

### 图标规范

列表图标优先 SVG：

- 使用 `24 x 24` viewBox 或设计系统明确尺寸，透明背景。
- 使用真实 vector path；不引用外部字体、脚本、网络资源或内嵌 raster。
- 文件名必须以 `.svg` 结尾，避免 CDN URL 无法被 Flutter SVG 分支识别。
- 提供足够对比度并在浅色/深色背景真机验证；必要时单独上传 `iconDark`。
- 去除编辑器 metadata、隐藏层和不必要的小数精度。

无法提供可靠 SVG 时使用透明 PNG，建议至少 3x 资源（72x72 或 96x96），检查缩放后边缘。

### staging 操作

1. 打开 `https://marketing-cms-staging-us.addx.live/admin/collections/lab-feature-content`。
2. 登录并新建记录；先确认 key，保存后不要重命名。
3. 上传 icon、可选 iconDark/image，等待媒体状态正常。
4. 保存并 Publish，不能只留 Draft。
5. 通过公开 API 验证：

```text
GET https://marketing-cms-staging-us.addx.live/api/v1/lab-feature-content/<key>?locale=en
```

6. 检查 `key`、`iconUrl`、可选 `iconDarkUrl`/`imageUrl`、metadata 和 HTTP 状态。
7. 在浅色/深色客户端验证实际 URL，而不是只看 CMS 预览。
8. 生产发布时调用 staging 的 `POST /api/transfer/lab-feature-content/<key>`，同步到 pre。
9. 在 `https://marketing-cms-pre-us.addx.live` 验证 App-facing API 后，再从 pre 调同一路径同步到 prod。
10. 查询 `sync-logs` 并验证目标环境；不能跳过 pre。

Admin 页面只看到 Publish 时，不表示没有 Transfer 能力，也不表示已同步到下一环境。`lab-feature-content` 已在 CMS 代码中注册 transfer/sync strategy；使用 `$marketing-cms` 的受控 API 流程。不要直接编辑只读环境，也不要用重复 Publish 代替 staging -> pre -> prod。

### 常见错误

| 症状 | 优先排查 |
|---|---|
| 客户端显示错误的旧 icon | CMS key 不存在，客户端走了错误 fallback；先验证公开 API |
| SVG 不显示 | URL 后缀、Content-Type、外部 font/script、非法 SVG |
| 保存后 API 404 | 内容仍是 Draft、key 拼写不一致、同步/缓存未完成 |
| 文案不更新 | 文案不属于 CMS；检查 Crowdin、ARB 和 resolver |

## 2. Crowdin key 规范

目录记录使用稳定的语义 key：

```text
lab_feature.<feature_key>.name
lab_feature.<feature_key>.description
```

Flutter ARB 使用 camelCase 代码 key：

```text
labFeature<PascalFeatureKey>Name
labFeature<PascalFeatureKey>Description
```

示例：

```text
feature_key: backyard_digest
catalog name: lab_feature.backyard_digest.name
catalog description: lab_feature.backyard_digest.description
ARB name: labFeatureBackyardDigestName
ARB description: labFeatureBackyardDigestDescription
```

不要把英文文案直接作为 key；不要复用含义相近但产品语义不同的旧 key。

## 3. Crowdin 工作流

调用 `$crowdin`，由该 skill 读取实时项目、bundle、token 和 API 规范；本 skill 不保存 token 或固定 project ID。

1. 区分两层文案：功能目录的 name/description，以及一个 App 首次接入时的 `lab_features`、空态、错误等通用页面文案。
2. 从每个目标客户端当前仓库读取 Crowdin config、project 和 bundle；不要硬编码历史 ID。当前 KB/VN 双端交付时，目录 key 至少分别核对 KB 与 `app_vn`，不能假设跨项目自动同步。
3. 审计是否已有同名 key 或可复用的完全同义文案。
4. 创建 `feature_<short>_<username>_<YYYYMMDD>` 分支。
5. 创建第一个源字符串，明确 `isHidden:false`，补上下文和截图说明。
6. 为当前所有目标语言完成翻译并校验占位符、品牌名、长度和语气。
7. 再处理第二个源字符串；不要先批量创建一堆空翻译。
8. 使用仓库受控脚本拉取。Lattice 使用 `lattice/tools/crowdin_sync` 及 `crowdin_sync.yaml`；Android/iOS 使用各仓 `crowdin/l10n.py` 与目标 App config。禁止手改脚本生成的 ARB/Dart/XML/strings。
9. 确认每个目标客户端的当前语言集合都有 name 和 description；原生设置标题还要覆盖 Android/iOS flavor 实际支持的语言。
10. 重新运行仓库 l10n generator、格式化、analyze 和测试，并逐语言比较 key 集合。
11. 让用户明确确认后再合入 dev；保留 feature branch，除非用户要求清理。

目标语言必须从目标 release 和 Crowdin config 现场推导，例如：

```bash
find lattice/packages/i18n_feature/l10n -name 'app_*.arb' -print | sort
find ResModule -path '*values*' -name 'strings.xml' -print | sort
find AddxAi -path '*.lproj/Localizable.strings' -print | sort
```

历史语言列表只能用于发现缺漏，不能替代当前仓库结果。

## 4. 翻译质量规则

- 标题短、可扫描，不添加实验状态或 Beta 字样，除非 PRD 明确要求。
- 简介说明用户收益，不描述内部 GB/PE/AB 机制。
- 保留产品名、鸟种学名、占位符和 ICU 结构；翻译后逐语言校验 JSON/ARB。
- 避免把英文标题在非英文语言中留空；无法可靠翻译时标记阻断，不静默回退。
- 确认 RTL 语言布局和长文本没有溢出。
- 实验室开关 OFF 时简介仍显示，因此简介不能写成只适用于 ON 状态的句子。

## 5. Flutter 文案映射

当前客户端不能用服务端字符串值动态调用生成的 localization getter。查找并更新：

```bash
rg -n "LabFeatureStrings|featureTitles|featureDescriptions|normalizedFeatureKey" packages
```

为新功能增加：

- backend `feature_key` 标准化分支；
- `LabFeatureStrings.featureTitles` 中的 title getter；
- `LabFeatureStrings.featureDescriptions` 中的 description getter；
- 缺失 key 的安全 fallback，禁止回退成另一个实验功能名称；
- `LabFeatureStrings`/page 单测，覆盖服务端语义 key、feature key 和未知 key。

## 6. 完成证据

- CMS staging 记录已 Publish，公开 API 200，icon URL 真机可渲染。
- 品牌图标不同时，已证明共享 CMS 未被覆盖且 flavor 本地资源只命中目标客户端/feature。
- Crowdin 源字符串可见，当前每种目标语言翻译齐全。
- ARB、生成代码和 resolver 一致，analyze/test 通过。
- NH `crowdin_keys` 与 Crowdin 语义 key 完全一致。
- 没有把本地化文案塞进 CMS metadata。
