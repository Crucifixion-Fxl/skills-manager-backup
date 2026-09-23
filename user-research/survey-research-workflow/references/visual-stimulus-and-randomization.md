# 视觉刺激与随机化

在问卷包含图片、界面、包装、广告创意、概念卡或视频时使用本参考。目标不是让问卷更好看，而是确保受访者看到的刺激足以支持目标判断，并正确控制顺序、位置和版本分配。

## 1. 先定义视觉判断任务

在选择题型前说明受访者需要做什么：

- **整体识别或偏好**：只需看主题、颜色、轮廓或大致风格；图片选项通常足够。
- **并排比较**：需要同时比较少量方案；可使用一张标注清楚的合成大图加文字选择题。
- **细节判断**：需要读界面文字、发现控件差异或检查构图细节；必须提供足够大的单图、全尺寸查看方式或原型。
- **交互/可用性判断**：静态图不能替代可操作原型与任务观察。

若平台只提供不可放大的缩略图，而任务依赖细节，状态为 `blocked`；提高原图分辨率不能修复显示尺寸不足。

## 2. 选择刺激载体

| 任务 | 优先载体 | 主要限制 |
|---|---|---|
| 少量整体风格中选一个 | Picture Choice / 图片选项 | 验证实际显示尺寸、裁切与标签 |
| 少量方案同时比较 | 合成大图 + 普通选择题 | 图内位置必须平衡；移动端可能缩小 |
| 每个方案独立评分 | Sequential monadic / monadic | 需要随机分配或平衡概念顺序 |
| 需要查看细节 | 大图、全尺寸链接或原型 | 外链会增加跳出与操作负担 |
| 需要完成任务 | 可交互原型或可用性测试 | 普通问卷不能证明 usability |

合成图中的方案使用稳定 concept ID；A/B/C 只能是显示位置，不能成为分析主键。各方案的画布、比例、留白、背景、文字密度和视觉完成度保持可比，避免无意显著性。

## 3. 区分四种随机化

以下不是同一能力，必须分别记录：

1. **Answer-choice randomization**：只改变文字或图片选项的显示顺序。
2. **Stimulus-position randomization**：改变合成图内部方案的位置。
3. **Concept/order randomization**：改变多张概念卡或多道概念题的先后。
4. **Respondent assignment**：把受访者随机分到不同版本、cell 或问卷。

平台支持第 1 项不代表支持后 3 项。随机答案文字不能消除合成图内部位置效应；若文字顺序与图中 A/B/C 不一致，还会增加 response-mapping burden。

## 4. 合成图的平衡规则

- 答案顺序默认与合成图显示顺序一致，不单独随机。
- 两个概念至少准备 `AB / BA`；三个概念可使用平衡的 Latin-square 顺序，风险较高时使用全部排列。
- 由原生随机分配、外部 allocator、样本名单分组或多个投放链接平衡版本；不能验证分配时，只能报告固定顺序限制。
- 分析按 concept ID 合并，保留 variant、position 和 exposure order，用于检查位置效应。
- 样本过小无法支持多个 cell 时，优先 monadic 或定性任务，不制造形式上的随机化。

## 5. 图片质量与无障碍

- 在目标设备和实际 renderer 检查尺寸、裁切、焦点、加载时间与文字可读性；API read-back 不足以通过。
- 为非装饰图片提供中性 alt text；alt text 描述所见内容，不解释哪个方案更好。
- 图片中的关键文字不要只依赖颜色或过小字号传达。
- 移动端横向合成图出现过小文字时，减少方案数、改纵向版本、逐概念展示或换方法。

## 6. 实施记录

视觉题至少记录：

```yaml
visual_task:
detail_required: gist | moderate | fine
stimulus_ids: []
display_carrier: picture_choice | composite | sequential | prototype
answer_order_strategy:
stimulus_order_strategy:
assignment_method: native | hidden_field_external | sample_split | fixed
mobile_render_status: pass | needs_fix | not_run
desktop_render_status: pass | needs_fix | not_run
position_effect_analysis:
limitations: []
```

只有刺激材料、答案映射、分配和 renderer 均按设计工作，才能声称视觉比较已完成实施。
