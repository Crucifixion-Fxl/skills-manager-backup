# 单文件 HTML 深度调研报告：写作方法

> 沉淀自 2026-06 golf 仓库系列报告实战（veo-sports-cam-analysis 等 6 份，全部发布到 GitLab Pages 供高管/团队传阅）。
> 配套模板：[`../templates/report-template.html`](../templates/report-template.html)（可直接复制起步）。
> 发布用 `gitlab-pages-html` skill；轻量调研仍用本 skill 默认的 md 产出，**要对外分享/长报告才用 HTML**。

## 0. 硬原则

1. **自包含单文件**：零外部依赖——无 CDN、无外链 CSS/JS/字体/图片；图一律内联 SVG 或纯 CSS。这样 private Pages、离线、附件场景都能打开，且永不因外链失效而坏。
2. **系列视觉一致**：同一项目的第 2 份起，写作前先 `Read` 既有报告前 ~420 行复用其 CSS 体系（配色变量、组件类名全套照搬），换主题只改 `--accent` 色相。
3. **诚实优先**：查不到写 `unknown`，不编造；推断/转引/竞品口径要标注；有「来源质量警告」就单独开 danger callout 写明（如 AI 生成的伪造规格站点）。
4. **结论先行**：hero 副标题给裁决预告，第 1 节就是 TL;DR + KPI statband + verdict callout；细节供想深读的人往后翻。

## 1. 导航设计（悬浮折叠 TOC）

长报告不用侧边栏占列——**正文吃满页宽（`--maxw: 1440px`）**，目录做成左上角固定悬浮的折叠面板：

- `nav.toc { position: fixed; top/left: 14px; z-index: 60 }` + 一个开合按钮；`.toc-body` 默认 `display:none`，`.open` 时展开（带 `max-height + overflow-y:auto`）。
- 条目编号用 **CSS counter**（`counter-reset/increment` + `::before content: counter(toc)`），增删章节不用手改编号。
- JS 只有一段渐进增强脚本：按钮 toggle + 点击条目自动收起（见模板底部）。
- 章节 `section.block` 要有 `scroll-margin-top` 防锚点被遮。
- （对照：`architect` skill 管的 `docs/architecture/**` 站点文档用右侧 `.doc-outline` 体系；本方法面向 standalone 调研报告。）

## 2. 图表选型规则（实战被纠正后沉淀的硬规则）

| 数据形态 | 用什么 | 禁止 |
|---|---|---|
| **时间序列**（营收/用户增长/融资轮次/产品演进） | **时间轴柱状图 / 时间线**（内联 SVG，x 坐标按真实时间比例摆放，缺年留空） | ❌ 横向条形图——时间数据必须用时间轴表达 |
| 分类对比（定价/竞品/难度评分） | CSS `hbars` 横向条形图 | |
| 数值区间（BOM 成本带） | hbars 区间条（`left:X%;width:Y%`） | |
| 占比 | SVG donut（`stroke-dasharray`，r=54 时周长 ≈339.3） | |
| KPI 摘要 | `statband` 数据卡带 | |
| 二维定位（价格 × 能力的竞品散点） | 内联 SVG 散点 + 聚类椭圆标注 | |
| 流程/架构/飞轮 | `diagram-wrap` + 内联 SVG 框图（`arch-box/arch-arrow` 类；`<marker>` 全文只定义一次） | |
| 阶段路线图/公司史 | `.timeline .phase` 组件或 SVG 时间线 | |

每张图配 `chart-title`（"图：…"）与 `chart-note`（口径/估算依据/不可比脚注）。对比图若口径不可直接比，必须脚注说明。

## 3. 引用与置信度体系

- **inline 角标**：每个关键数字/论断后加 `<a class="ref" href="…" target="_blank" rel="noopener">[来源名]</a>`，角标文案用来源名（`[veo.com]`、`[SportsPro]`、`[官网]`）而非编号——免维护编号一致性。
- **置信度 pill**：`<span class="pill hi/mid/lo">高/中/低</span>` 标在小节标题或论断处；推断和官方确认分开写。
- **转引标注**：经第三方/竞品博客转引的原话标 ⚠️ 并在方法局限里声明。
- 文末「参考来源」章节按主题分组（`src-list` 双栏），与 inline 角标并存：角标管"这句话哪来的"，清单管"全貌"。

## 4. 表格规范

- 一律包 `.table-wrap`（横向滚动 + 圆角边框）；表头 sticky；斑马纹 + hover。
- **宽对比矩阵**（10+ 列竞品表）：`table.matrix` + `<colgroup>` 显式列宽 + `table-layout:fixed` + **冻结首列**（sticky left，首列 rowhdr 带背景和右侧阴影）——横向滚动时产品名不消失。
- 小屏前置 `.scroll-hint` 提示可横滚。

## 5. 内容结构惯例

```
hero（kicker / 主标题 / 副标题给裁决预告 / meta pills：日期·读者·方法）
01 执行摘要：TL;DR 段落 + statband KPI 带 + verdict callout（warn）+ 方法局限（danger）
02..N 正文章节：每节 = 论述 + 图表 + 表格 + callout 洞察
   - callout 三色语义：insight=洞察/裁决，warn=注意/勘误/Verdict，danger=风险/来源质量警告
倒数第二节 结论与建议：编号裁决 + 给读者的行动清单（可加时间窗表）
最后一节 参考来源：按主题分组
footer：免责声明（推断非官方确认）+ 回顶链接
```

## 6. 交付前校验（必做）

用 python3 `html.parser` 做标签配对校验。**注意 void 元素豁免名单必须含 `col`**（`<colgroup><col>` 会误报）：

```python
import html.parser
VOID={'br','meta','link','img','hr','input','col','source','wbr','area','base','embed','track'}
class P(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.stack=[]; self.errs=[]
    def handle_starttag(self, tag, attrs):
        if tag not in VOID: self.stack.append((tag,self.getpos()))
    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0]==tag: self.stack.pop()
        else: self.errs.append(f"mismatch </{tag}> at {self.getpos()}")
src=open('report.html').read()
p=P(); p.feed(src)
for t,pos in p.stack: p.errs.append(f"unclosed <{t}> at {pos}")
print("OK - balanced" if not p.errs else p.errs[:10])
```

附带检查：外部依赖应为 0（`grep -c 'src="http\|stylesheet" href="http'`）、`class="ref"` 计数、`<svg` 计数，写进交付汇报。

## 7. 多 agent 生产模式（大报告）

1. **并行调研 agents**（每个维度/垂类一个，背景运行）：prompt 里写明读者画像、要求结构化要点清单 + 每条带来源 URL + 查不到写 unknown 不编造、禁止写文件/git。
2. **写作 agent**：输入 = 各路调研的浓缩材料（含来源 URL）+ 本方法的风格约束（"先 Read 既有报告前 420 行复用 CSS"）+ 报告结构清单 + 图表规范；要求完成后自跑校验脚本并汇报"文件路径 + 校验结果 + 图表清单"。
3. **主会话**：复核校验 → commit → 用 `gitlab-pages-html` 发布 → 用 GitLab API 确认 pipeline success + pages deployment 时间戳刷新。
4. 用户中途给的口径修正（读者定位、结论分寸、图表类型）要回灌：已写完的部分由主会话直接 `Edit` 修正，别重跑 agent。

## 8. 踩过的坑（直接抄答案）

- 时间序列画成横向条形图 → 被纠正，见第 2 节规则。
- 页面默认 1180px + 268px 侧栏 → 图表显示不全；改 1440px + 悬浮 TOC 后解决。
- `<col>` 不在 void 名单 → 校验器误报 mismatch。
- SVG 写死 `width/height` → 窄容器溢出；统一 `.diagram-wrap svg { width:100%; height:auto; min-width:760px }`。
- 报告里嵌套 venue/品类内容超出读者要的范围 → 写作前确认范围（如"只要家庭 2C，排除线下场馆"），hero 副标题写明排除项。
- 大 HTML 提交别走 shell heredoc（引号会崩），用 Write 工具写文件。
