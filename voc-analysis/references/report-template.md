# Report Template

Write the report in Chinese. Keep all quotes and references in original English.

---

```markdown
# [Topic] 市场调研报告

## Executive Summary
[3-5 bullet points summarizing key findings]

## 研究策略

### 研究目标
- 问题 1: [e.g., 用户最大的痛点是什么？]
- 问题 2: [e.g., 与竞品相比的优劣势？]
- 问题 3: [e.g., 用户最需要什么功能？]

### 查询策略设计

#### Layer 1: 品牌直接查询
- **目的**: 获取产品直接相关讨论
- **关键词逻辑**: [产品名、品牌变体、常见昵称]
- **查询示例**: [具体查询]

#### Layer 2: 问题空间查询
- **目的**: 发现用户痛点（用户可能不提及品牌）
- **关键词逻辑**: [从已知问题出发]
- **查询示例**: [具体查询]

#### Layer 3: 竞品分析查询
- **目的**: 了解竞争格局
- **竞品识别逻辑**: [直接竞品、替代方案]
- **查询示例**: [具体查询]

#### Layer 4: 使用场景查询
- **目的**: 理解用户动机和使用环境
- **场景识别逻辑**: [谁在用、在哪用、为什么用]
- **查询示例**: [具体查询]

#### Layer 5: 长尾/小众查询
- **目的**: 发现意外洞察
- **构造逻辑**: [季节性、特定问题、集成]
- **查询示例**: [具体查询]

#### 平台选择逻辑

| 平台 | 选择原因 | 排序策略 |
|------|---------|---------|
| Reddit | [原因] | hot/top + new |
| Twitter | [原因] | Latest, 近3-6个月 |
| Amazon Reviews | [原因] | recent + helpful |
| ... | ... | ... |

### 查询迭代过程
1. 初始查询: [发现了什么]
2. Gap 分析: [识别了什么缺口]
3. 补充查询: [深入探索了什么]
4. 分析中追加采集: [分析中发现需要什么额外数据]
5. 最终确认: [如何交叉验证]

## 研究方法论

### 分析方法

本研究采用 LLM 直接读取原始 VOC 数据的方法:
1. LLM 直接读取所有原始 JSON 文件（每条数据都分析，不抽样）
2. LLM 进行语义标注（痛点、功能需求、情感、用户类型等）
3. Python 统计标签频率（仅做计数，不做分析）

### 数据源详情

#### 1. Reddit 数据
- **Actor**: [actor_id]
- **Dataset ID**: [dataset_id]
- **策略层**: Layer [N]
- **查询逻辑**: [为什么选择这些关键词/社区]
- **查询参数**: searches: `[keywords]`, subreddit: `r/[community]`, maxItems: [N]
- **Apify Run Link**: https://console.apify.com/actors/runs/[runId]
- **Dataset Link**: https://console.apify.com/storage/datasets/[dataset_id]
- **本地路径**: `reference/reddit_scraper/dataset/[dataset_id].json`
- **采集时间**: [date]
- **数据量**: [N] posts, [M] comments

#### 2. Amazon Reviews 数据
- **Actor**: [actor_id]
- **Dataset ID**: [dataset_id]
- **策略层**: Layer [N]
- **查询逻辑**: [为什么选择这些产品]
- **产品分析列表**:
  | 产品 | ASIN | 类别 | Amazon 链接 |
  |------|------|------|------------|
  | [自家产品] | [ASIN] | 自家 | https://amazon.com/dp/[ASIN] |
  | [竞品1] | [ASIN] | 竞品 | https://amazon.com/dp/[ASIN] |
- **查询参数**: ASINs: `[list]`, maxReviews: [N], country: [US], sortBy: [recent]
- **ASIN 获取方法**: [如何找到这些 ASIN]
- **Apify Run Link**: https://console.apify.com/actors/runs/[runId]
- **本地路径**: `reference/amazon_reviews/dataset/[dataset_id].json`
- **采集时间**: [date]
- **数据量**: [N] reviews
- **评分分布**: [X% 5★, Y% 4★, Z% 3★, W% 2★, V% 1★]

#### 3. [其他平台数据...]
[同样格式]

#### N. Web Search 数据（如使用）
- **平台/话题**: [platform]
- **数据来源**: Web Search
- **策略层**: Layer [N]
- **使用原因**: [无 Apify Actor / Actor 失败 / 快速验证]
- **搜索查询**:
  ```
  Query 1: "[topic] site:[platform].com"
  Query 2: "[product] [keyword] [year]"
  ```
- **采集方法**: 手动审查搜索结果前 [N] 条
- **数据量**: [N] 条讨论/评论
- **采集时间**: [date]
- **限制**: 非穷尽采集，聚焦高质量内容

### 查询策略覆盖汇总

| 策略层 | 覆盖状态 | 数据集数量 |
|--------|---------|-----------|
| Layer 1 (品牌直接) | ✅/⚠️/❌ | [N] |
| Layer 2 (问题空间) | ✅/⚠️/❌ | [N] |
| Layer 3 (竞品分析) | ✅/⚠️/❌ | [N] |
| Layer 4 (使用场景) | ✅/⚠️/❌ | [N] |
| Layer 5 (长尾小众) | ✅/⚠️/❌ | [N] |

### Gap 分析与数据完整性

**初始覆盖**: [X%]
**最终覆盖**: [Y%]
**提升**: [+Z%]
**总数据集**: 既有 ([N]) + 新增 ([N]) = [Total]

### 迭代数据采集（如适用）

[记录分析过程中发现的问题和追加采集的数据]

### 失败 Run 恢复（如适用）

| 失败 Run | Actor | 失败原因 | 恢复方案 | 结果 |
|----------|-------|---------|---------|------|
| [runId] | [actor] | [原因] | [方案] | ✅/❌ |

### 数据统计
- **总样本量**: [total]
- **时间范围**: [date range]
- **平台覆盖**: [platforms]
- **工具**: Apify Actors + Web Search

## Key Findings

### Finding 1: [类别]

**量化数据:**
- 提及频率: X 次 (占总样本 Y%)
- 情感分布: Z% 正面, W% 负面, V% 中性
- 平均评分: N/5.0 (样本量=M)

[详细分析]

**用户原声:**
> "[exact English quote]"
> - Source: [Post Title](https://direct-link.com)

**分析:**
[Chinese analysis]

### Finding 2: [类别]
[同样格式...]

## 竞品分析

### 竞品对比矩阵

| 指标 | 自家产品 | 竞品 A | 竞品 B |
|------|---------|--------|--------|
| 平均评分 | X.X/5.0 (N reviews) | Y.Y/5.0 (M reviews) | Z.Z/5.0 (K reviews) |
| 正面提及率 | XX% | YY% | ZZ% |
| 主要优势 | [数据支撑] | [数据支撑] | [数据支撑] |
| 主要痛点 | [数据支撑] | [数据支撑] | [数据支撑] |

### 功能维度 VOC 对比

逐功能对比各产品的提及率、好评率、差评率和均分。噪音评论（如纯订阅/定价抱怨）已从好评率/差评率计算中排除。

**公式说明:**
- 提及率 = 提及该功能的评论数 / 总评论数
- 好评率 = 功能好评数 / (有效提及数 - 纯噪音数)
- 差评率 = 功能质量差评数 / (有效提及数 - 纯噪音数)

#### 功能 1: [功能名称]

| 指标 | 自家产品 | 竞品 A | 竞品 B | 差异分析 |
|------|---------|--------|--------|---------|
| 提及率 | XX% (N=XX) | YY% (N=YY) | ZZ% (N=ZZ) | [谁被更多讨论] |
| 好评率 | XX% | YY% | ZZ% | [领先/落后 Xpp] |
| 差评率 | XX% | YY% | ZZ% | [领先/落后 Xpp] |
| 功能均分 | X.X/5.0 | Y.Y/5.0 | Z.Z/5.0 | |
| 噪音过滤 | N 条 | N 条 | N 条 | |

**VOC 原声:**

✅ **自家产品正面评价:**
> "[English quote]"
> — ★★★★★ [Source](https://...)

❌ **自家产品负面评价:**
> "[English quote]"
> — ★★☆☆☆ [Source](https://...)

✅ **竞品 A 正面评价:**
> "[English quote]"
> — ★★★★★ [Source](https://...)

❌ **竞品 A 负面评价:**
> "[English quote]"
> — ★☆☆☆☆ [Source](https://...)

#### 功能 2: [功能名称]
[同样格式...]

#### 功能维度综合对比

| 功能维度 | 自家好评率 | 自家差评率 | 竞品A好评率 | 竞品A差评率 | 差异 (pp) |
|---------|-----------|-----------|------------|------------|----------|
| [功能1] | XX% (N=X) | XX% | YY% (N=Y) | YY% | +/-Xpp |
| [功能2] | XX% (N=X) | XX% | YY% (N=Y) | YY% | +/-Xpp |
| [功能3] | XX% (N=X) | XX% | YY% (N=Y) | YY% | +/-Xpp |

## 用户情感分析

### 整体情感分布
- 正面: X% (N items)
- 中性: Y% (M items)
- 负面: Z% (K items)
- 总样本: [total]

### 正面主题 Top 5
1. [Theme]: XX mentions (YY%)
2. ...

### 负面主题 / 痛点 Top 5
1. [Pain Point]: XX mentions (YY%), avg rating: Z.Z/5.0
2. ...

### 功能需求（按提及频率排序）
1. [Feature]: XX mentions
2. ...

## 用户画像分析

### Persona 1: [名称]

**画像:**
- **细分占比**: XX% (N=XXX)
- **主要平台**: [platform] (XX%)
- **平均评分**: X.X/5.0

**量化特征:**
- XX% 提及 [specific pain point]
- YY% 请求 [specific feature]

**代表性引用:**
> "[English quote]"
> - Source: [Username](https://link)

### Cross-Persona 对比

| 指标 | Persona 1 | Persona 2 | Persona 3 |
|------|-----------|-----------|-----------|
| 占比 | XX% | YY% | ZZ% |
| 评分 | X.X | Y.Y | Z.Z |
| Top 痛点 | [Issue] | [Issue] | [Issue] |

## 建议

### P0 - 立即行动（影响 >30% 用户）
1. **[建议]**
   - 数据支撑: XX% 用户提及
   - 评分影响: 降低 Y.Y 分
   - 预期效果: 改善 Z% 满意度

### P1 - 高优先级（影响 10-30% 用户）
[...]

### P2 - 中优先级（影响 <10% 但重要）
[...]

## 附录

### 量化分析代码
[Python scripts used]

### 完整数据集列表
- `reference/[scraper]/dataset/[id].json` - [Description]
[... list all ...]
```
