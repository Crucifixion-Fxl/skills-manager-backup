---
name: voc-analysis
description: VOC (Voice of Customer) 分析与市场竞争情报研究。通过 Apify Agent Skills 和 Web Search 从 Reddit、Twitter、Amazon、App Store、YouTube、Facebook Groups、Discord 等多平台采集用户反馈，进行语义标注、痛点挖掘、情感分析和竞品对比，生成数据驱动的市场洞察报告。当用户提到 VOC 分析、用户反馈分析、市场调研、竞品分析、用户评论分析、pain point 分析、customer feedback、market research、competitive intelligence、产品评价分析、用户画像、customer sentiment、review analysis，或需要从多平台采集和分析用户声音时使用此 Skill。即使用户只说"帮我看看用户怎么评价这个产品"或"分析一下竞品"，也应触发。
---

# VOC Analysis — 市场调研与用户声音分析

## Description

Senior market research analyst workflow for comprehensive, data-driven VOC (Voice of Customer) analysis and competitive intelligence. Covers the full pipeline: multi-platform data collection (Reddit, Twitter, Amazon, App Store, YouTube, etc.) via Apify Agent Skills and web search, LLM semantic tagging, Python statistical counting, and Chinese report generation with English quotes preserved.

## Prerequisites: Apify API Key (MANDATORY — 必须先完成)

**Apify 是本 Skill 的核心依赖，没有 Apify 无法执行 VOC 分析。**

Before starting ANY research, you MUST verify Apify is configured:

1. Check `APIFY_TOKEN` environment variable: `echo $APIFY_TOKEN`
2. Or check `.env` file for `APIFY_TOKEN=...`

**If no API key is found, STOP IMMEDIATELY. Do NOT proceed. Tell the user:**

> "**本 Skill 必须使用 Apify 进行多平台数据采集，没有 API Key 无法开始调研。**
>
> 请按以下步骤操作：
> 1. 联系 **陈敬敏 (Jingmin Chen)** 获取 APIFY_TOKEN
> 2. 安装 Apify Agent Skills: `npx skills add apify/agent-skills`
> 3. 配置环境变量: 在 `.env` 文件中添加 `APIFY_TOKEN=<your-token>`
> 4. 配置完成后再来找我开始调研。"

**Apify 不是可选项。** 仅靠 Web Search 无法完成完整的 VOC 分析（数据量不足、无法结构化采集、无法覆盖多平台）。必须确保 Apify 可用后才能开始工作。

Read `references/apify-setup.md` for detailed installation and configuration steps.

## Rules

1. **Data coverage is everything.** Incomplete data → biased conclusions. Always collect from multiple platforms, build diverse queries, and cross-validate findings. Aim for 5+ query layers and 3+ platforms minimum.

2. **Be a detective, not a tourist.** Don't just check what exists — actively construct 10+ diverse queries. Explore adjacent topics, competitor spaces, and problem-space queries where users describe issues without mentioning the brand.

3. **Analysis is iterative.** Initial data → preliminary analysis → discover gaps/questions → collect targeted data → deeper analysis. It's normal (and expected) to collect more data mid-analysis when patterns or questions emerge.

4. **LLM reads ALL raw data directly.** Point the LLM to raw JSON files and process every single item — no sampling, no pre-filtering, no manual copy-paste. This eliminates bias and ensures complete coverage.

5. **Semantic tagging over keyword matching.** LLM tags items by understanding context (e.g., "I gave up trying to connect it" → `connectivity_issue` + `setup_difficult`). Python is ONLY used to count those tags statistically.

## High-Level Workflow

```
Step 0: Check existing Apify runs (cache-first)
    → Evaluate relevance of each run's input parameters
    → Download only relevant datasets
Step 0.5: Gap analysis (MANDATORY)
    → Map existing data to 5-layer query strategy
    → Identify missing platforms, angles, competitors, time ranges
    → Build 5-10+ new queries to fill gaps
Step 1: Plan query strategy (5 layers)
    → Direct → Problem-space → Competitor → Use-case → Long-tail
Step 2: Collect data (Apify + Web Search fallback)
    → Store in reference/<scraper>/dataset/<id>.json
Step 3: LLM analyzes ALL raw data
    → Reads every item, tags semantically, extracts quotes with URLs
    → Tags feature-level data: features_mentioned, feature_sentiment, is_noise
    → Saves tagged data to reference/tagged/
Step 4: Python counts tags (ONLY statistical counting)
    → Frequencies, percentages, cross-tabs, visualizations
Step 4.5: Feature-Level Analysis (for product comparison)
    → Per-feature metrics: mention rate, positive rate, negative rate, avg rating
    → Noise filtering: exclude subscription/pricing complaints from quality metrics
    → Competitive benchmarking tables with pp differences
Step 5: Generate report in Chinese (English quotes preserved)
    → Save as .html in docs/
```

Each step is detailed in the reference files below.

## Reference Files

Read the relevant reference file when you reach that phase of the workflow:

| File | When to Read | Content |
|------|-------------|---------|
| `references/apify-setup.md` | Before starting | MCP installation, verification, troubleshooting |
| `references/data-collection.md` | Steps 0–2 | Cache management, Python scripts, run checking, dataset download, gap analysis |
| `references/query-strategy.md` | Step 1 | 5-layer query design, platform selection, query examples |
| `references/analysis-methodology.md` | Steps 3–4 | LLM tagging workflow, Python counting, analysis pipeline |
| `references/report-template.md` | Step 5 | Complete report template with all sections |
| `references/failure-recovery.md` | When runs fail | Failure analysis, alternative actors, web search fallback |
| `references/amazon-guide.md` | When scraping Amazon | ASIN search methods, review scraper input formats |

## Data Storage Structure

```
reference/
├── apify_runs_cache.json           # Central cache (keep updated)
├── scripts/                         # Automation scripts
│   ├── build_apify_cache.py        # Fetch all runs + details
│   └── download_datasets.py        # Batch download relevant datasets
├── tagged/                          # LLM-tagged datasets
│   └── <platform>_<id>_tagged.json
├── reddit_scraper/dataset/
├── twitter_scraper/dataset/
├── amazon_reviews/dataset/
├── appstore_reviews/dataset/
├── google_play_reviews/dataset/
├── youtube_scraper/dataset/
└── ...other platforms.../dataset/
```

## Tools

### Primary: IDE Built-in Web Search
- Free, unlimited, real-time — use as primary discovery tool
- Best for: validation, niche platforms, recent events, quick checks
- Always start with web search for discovery before Apify scraping

### Secondary: Apify Agent Skills

Apify provides AI-native agent skills for web scraping and data extraction. Source: https://github.com/apify/agent-skills

**安装方式（二选一）：**

1. **npx 一键安装（推荐）：**
   ```bash
   npx skills add apify/agent-skills
   ```

2. **全局安装到 skills 目录：**
   ```bash
   # Claude Code
   /plugin marketplace add https://github.com/apify/agent-skills
   /plugin install apify-ultimate-scraper@apify-agent-skills
   ```

**可用 Skills 包括：**
- **Universal Scraper** — AI 驱动的通用网页爬虫（Instagram, Facebook, TikTok, YouTube, Google Maps 等 50+ 平台）
- **E-Commerce** — 电商数据采集（Amazon, eBay 等价格情报、评论、产品研究）
- **Social Media Analytics** — 受众分析、内容分析、影响者发现、趋势分析
- **Competitor Analysis** — 竞品分析、品牌声誉监测、市场调研
- **Lead Generation** — B2B/B2C 潜客采集（Google Maps, LinkedIn 等）

**环境要求：**
- Node.js 20.6+
- `APIFY_TOKEN` 环境变量（`.env` 文件或 shell 环境中设置）

**Decision tree:**
```
Need data? → Apify skill available?
  YES → Try Apify first → Failed? → Web Search fallback
  NO  → Web Search (primary)
```

See `references/failure-recovery.md` for detailed fallback strategies.

## 5-Layer Query Strategy (Summary)

| Layer | Purpose | Example |
|-------|---------|---------|
| 1. Direct | Brand/product mentions | `"Bird Buddy" review` |
| 2. Problem-space | Pain points (no brand) | `"bird feeder camera not connecting"` |
| 3. Competitor | Competitive products | `"Bird Buddy vs Birdfy"` |
| 4. Use-case | Target users/scenarios | `r/birdwatching smart feeder` |
| 5. Long-tail | Seasonal, niche, integrations | `"Bird Buddy winter setup"` |

Full details in `references/query-strategy.md`.

## Analysis Approach (Summary)

### What LLM Does (ALL analysis):
- Reads raw JSON files directly (`reference/*/dataset/*.json`)
- Processes EVERY item — no sampling
- Identifies patterns, extracts quotes with URLs
- Performs sentiment analysis by reading content
- Tags items semantically (pain_points, feature_requests, sentiment, user_type)
- Tags **feature-level** data: features_mentioned, feature_sentiment, is_noise
- Saves tagged data to `reference/tagged/`

### What Python Does (ONLY counting):
- Counts tag frequencies from LLM-tagged data
- Calculates percentages and distributions
- Generates cross-tabulations (e.g., pain_points by user_type)
- **Calculates per-feature metrics**: mention rate, positive rate, negative rate, avg rating
- **Excludes noise items** from feature quality metrics (e.g., pure subscription complaints)
- Creates visualizations (charts, tables)

**Python does NOT**: read raw data, find patterns, extract quotes, or do sentiment analysis.

### Feature-Level Analysis (for product comparison):
- Per-feature metrics: 提及率, 好评率, 差评率, 均分
- Noise filtering: exclude pricing/subscription complaints from feature quality metrics
- Formulas: `好评率 = 好评数 / (有效提及数 - 噪音数)`, `差评率 = 质量差评数 / (有效提及数 - 噪音数)`
- Competitive benchmarking: per-feature head-to-head tables with pp differences

Full details in `references/analysis-methodology.md`.

## Report Format (Summary)

- **Language**: Chinese analysis, English quotes preserved verbatim
- **Location**: Save as `.html` in `docs/`
- **Structure**: Self-contained HTML report with Executive Summary → Research Strategy → Methodology → Findings → Competitive Intelligence → Sentiment Analysis → User Personas → Recommendations
- **Every claim** must have inline references with clickable URLs
- **Every quote** in original English with source link, using semantic HTML (`<blockquote>`, `<cite>`, linked source)

Use `references/report-template.md` for section content and analysis order only; convert the final project report to HTML authoring SSOT instead of Markdown.

## Quality Checklist

### Data Collection
- [ ] Checked cache and existing runs; evaluated relevance
- [ ] **Gap analysis completed** (MANDATORY)
  - [ ] Mapped to 5-layer strategy
  - [ ] Identified platform gaps (target: 3+ of 8 major platforms)
  - [ ] Identified query angle gaps by layer
  - [ ] Coverage percentage calculated
- [ ] Built 5-10+ NEW queries to fill gaps
- [ ] Failed runs analyzed and recovered (see `references/failure-recovery.md`)
- [ ] Final dataset: existing + new ≥ 100 data points

### Analysis
- [ ] LLM read ALL raw JSON files (every item, no sampling)
- [ ] Semantic tagging completed (not keyword matching)
- [ ] Python tag counting completed (frequencies, percentages)
- [ ] Findings quantified (%, counts, averages, sample sizes)
- [ ] All quotes have clickable source URLs
- [ ] Cross-platform validation performed
- [ ] Iterative data collection documented if performed
- [ ] **Feature-level analysis** (if comparing products):
  - [ ] Feature keyword sets defined
  - [ ] Feature-level sentiment tagged (per-feature positive/negative)
  - [ ] Noise items identified and filtered (subscription/pricing complaints)
  - [ ] Per-feature metrics calculated (mention rate, positive rate, negative rate, avg rating)
  - [ ] Competitive benchmarking table with pp differences

### Report
- [ ] Written in Chinese, English quotes preserved
- [ ] Research strategy section explains query logic
- [ ] Each dataset mapped to strategy layer
- [ ] Gap analysis documented
- [ ] Python scripts in appendix (if used)
- [ ] Saved as `.html` in `docs/`

## Examples

**Good Example — Semantic tagging by LLM:**
User review: "I gave up trying to connect it after 2 hours"
→ LLM tags: `connectivity_issue`, `setup_difficult`, sentiment: `negative`
→ Python counts: connectivity_issue appears 47 times (23% of total)

**Bad Example — Python keyword matching:**
User review: "I gave up trying to connect it after 2 hours"
→ Python: `if "connect" in text: tag = "connectivity"` ← misses context, no sentiment, no semantic understanding
→ Misses nuance: "gave up" signals frustration; "2 hours" signals severity

**Good Example — Multi-platform gap analysis:**
After initial Reddit scrape (Layer 1), analyst identifies: no Amazon reviews (Layer 1), no competitor data (Layer 3), no problem-space queries (Layer 2). Builds 8 new queries to fill gaps before proceeding to analysis.

**Bad Example — Single-source analysis:**
Analyst scrapes one Reddit thread, finds 15 comments, writes a report claiming "users love the product" — no gap analysis, no cross-platform validation, insufficient sample size.

## Golden Rules

1. **VOC = LLM reads ALL raw JSON** — never copy-paste or pre-filter
2. **Gap analysis is MANDATORY** — don't skip to analysis without checking coverage
3. **Existing data is never enough** — always build new queries
4. **Research is iterative** — collect more data when questions arise mid-analysis
5. **LLM semantic tagging → Python tag counting** — never use Python keyword matching
6. **Process ENTIRE datasets** — thousands of items, not samples
7. **Every claim needs a number and a source link**
8. **Feature-level analysis for comparisons** — compute per-feature mention rate, positive rate, negative rate; filter noise (subscription complaints) from quality metrics; always show sample size (N=X) with percentages
