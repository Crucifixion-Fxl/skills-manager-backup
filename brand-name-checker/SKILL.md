---
name: brand-name-checker
description: 品牌名/商标命名综合检查 (BNR)。并行执行 7 项检查（商标数据库、域名、搜索量、App Store、社交媒体、俚语安全、发音音译）并输出带评分的 BNR 报告。当用户想检查一个品牌名能不能用、有没有商标冲突、域名是否可注册时使用。触发关键词：品牌名检查、商标查询、BNR、命名排查、brand name check、trademark search、naming check、brand clearance、brand availability、is this name taken、can I use this name。即使用户只是说"帮我查一下 XXX 这个名字"或"check if XXX is available as a brand"也应触发。支持多品牌对比（逗号分隔）和 --classes / --geo 参数。不要用于：起名头脑风暴、logo 设计、商标注册申请、SEO 分析、竞品分析。
---

# Brand Name Checker (BNR) — 商标命名综合检查

## Description

自动化品牌名商标命名综合检查 (BNR) 工作流。并行启动 7 个 Agent 执行检查（Google 搜索量、域名可用性、商标数据库、App Store 冲突、社交媒体用户名、俚语/文化安全、发音与跨语言音译分析），然后对找到的近似商标进行发音/视觉/概念三维度相似度评分，最终输出带 A-F 评级和行动建议的 BNR 汇总报告。

## Examples

**Example 1 — 单品牌检查：**
```
用户: 帮我查一下 Nexara 这个品牌名能不能用，主要做 SaaS 软件
触发: brand-name-checker Nexara --classes 9,42
输出: BNR 报告（7 项检查评分 + 近似商标分析 + 行动建议）
```

**Example 2 — 多品牌对比：**
```
用户: 对比一下 ArcVolt 和 FluxNode 哪个更安全，做硬件的 class 9，目标美国和欧盟
触发: brand-name-checker ArcVolt, FluxNode --classes 9 --geo us,eu
输出: 两份独立 BNR 报告 + 对比矩阵 + 排名推荐
```

**Example 3 — 指定地区：**
```
用户: check if "Optera" is available as a brand in US, UK and Japan
触发: brand-name-checker Optera --geo us,uk,jp
输出: BNR 报告（商标搜索限定 US/UK/JP，发音分析优先英语/日语）
```

## Input Format

The user provides brand name(s) and optional parameters. Parse the input as follows:

- **Brand name(s)**: One or more names, comma-separated for comparison mode
- **`--classes`**: Nice classification numbers (e.g., `--classes 9,35,42`). If not specified, search broadly
- **`--geo`**: Geographic regions to check (e.g., `--geo us,eu,cn`). Default: all supported regions (`us,eu,uk,cn,hk,jp,kr`)
- **`--compare`**: Enable multi-name comparison mode (auto-enabled when multiple names provided)

Example inputs:
```
brand-name-checker Nexara
brand-name-checker Nexara, Luminos, Zephyra --classes 9,35 --geo us,eu
brand-name-checker Vortex --classes 25 --geo us,cn,hk
```

## Rules

1. **Parallel first.** All 7 Phase 1 checks MUST launch as parallel Agent subagents. Never run them sequentially.
2. **Evidence over assumptions.** Every claim in the report must link to a source (URL, search query, or database reference).
3. **Fail gracefully.** If a check fails (blocked, timeout, no results), mark it as `ERROR` in the report with the reason. Never silently skip a check.
4. **Conservative scoring.** When in doubt, score more conservatively (higher risk). False negatives (missing a conflict) are far worse than false positives.
5. **Preserve raw findings.** Show what was actually found before applying analysis. The user needs to see the evidence, not just the conclusion.
6. **Geographic specificity.** Trademark results MUST be tagged by jurisdiction. A clear name in the US may conflict in the EU.

## Phase 1: Parallel Automated Checks

Launch **7 Agent subagents simultaneously** using the Agent tool. Each agent runs one check and returns structured results.

**IMPORTANT**: Send ALL 7 Agent tool calls in a SINGLE message to ensure true parallelism.

**Context for ALL agents**: Pass the following context to every agent prompt so subagents know user parameters:
- Brand name: `{NAME}`
- Nice classes: `{CLASSES}` (or "all" if not specified)
- Target regions: `{GEO}` (or "all" if not specified)

### Agent 1: Google Search Volume

```
Task: Check Google search presence for brand name "{NAME}"
Context: Target classes: {CLASSES}, Target regions: {GEO}

Steps:
1. WebSearch: exact match query "{NAME}" (in quotes) — record approximate result count
2. WebSearch: broad query {NAME} — record result count and note top 5 results
3. WebSearch: "{NAME}" brand — check if an existing brand dominates
4. WebSearch: "{NAME}" company — check corporate presence

Return format:
- Exact match results: [count]
- Broad match results: [count]
- Top 5 results summary (title, URL, type)
- Existing brand presence: YES/NO with details
- Assessment: CLEAR / CROWDED / DOMINATED
```

### Agent 2: Domain Availability

```
Task: Check domain availability for brand name "{NAME}"

Steps:
1. WebSearch: "{NAME}.com" domain availability OR site:namecheap.com "{NAME}.com"
2. WebSearch: "{NAME}.com" whois
3. Also check variations: {NAME}.io, {NAME}.co, {NAME}.ai, {NAME}.app
4. WebFetch: https://www.namecheap.com/domains/registration/results/?domain={NAME} (if accessible)
5. If blocked, fall back to WebSearch: "{NAME}.com" "is available" OR "is taken" OR "registered"

Return format:
- {NAME}.com: AVAILABLE / TAKEN / UNKNOWN (with evidence)
- {NAME}.io: AVAILABLE / TAKEN / UNKNOWN
- {NAME}.co: AVAILABLE / TAKEN / UNKNOWN
- {NAME}.ai: AVAILABLE / TAKEN / UNKNOWN
- {NAME}.app: AVAILABLE / TAKEN / UNKNOWN
- Premium/aftermarket pricing if found
- Assessment: EXCELLENT (primary available) / GOOD (alternatives available) / POOR (all taken)
```

### Agent 3: Trademark Database Search

```
Task: Search trademark databases for conflicts with "{NAME}"

Based on --geo parameter, search the applicable regions. Default is ALL regions.

Steps per region:

**US (USPTO)**:
1. WebFetch: https://tmsearch.uspto.gov — try direct search (may be blocked)
2. Fallback WebSearch: site:tsdr.uspto.gov "{NAME}"
3. WebSearch: USPTO "{NAME}" trademark
4. WebSearch: "{NAME}" trademark registered United States

**EU (EUIPO)**:
1. WebSearch: site:euipo.europa.eu "{NAME}"
2. WebSearch: EUIPO "{NAME}" trademark
3. WebSearch: "{NAME}" trademark registered European Union

**China (CNIPA)**:
1. WebSearch: "{NAME}" trademark China CNIPA
2. WebSearch: "{NAME}" 商标 中国
3. WebSearch: site:sbj.cnipa.gov.cn "{NAME}" (may not work, fallback to general search)

**Hong Kong (HKIPD)**:
1. WebSearch: "{NAME}" trademark "Hong Kong"
2. WebSearch: site:esearch.ipd.gov.hk "{NAME}"

**Japan (JPO)**:
1. WebSearch: "{NAME}" trademark Japan JPO
2. WebSearch: "{NAME}" 商標 日本

**Korea (KIPRIS)**:
1. WebSearch: "{NAME}" trademark Korea KIPRIS
2. WebSearch: site:kipris.or.kr "{NAME}"

**WIPO (Global)**:
1. WebSearch: site:branddb.wipo.int "{NAME}"
2. WebSearch: WIPO "{NAME}" trademark global brand database

For each result found, record:
- Mark/Name
- Registration/Application number
- Status (Live/Dead/Pending)
- Nice class(es)
- Owner
- Jurisdiction
- Filing/Registration date

Return format:
- List of all found trademarks with above details
- Per-jurisdiction summary: CLEAR / CONFLICT FOUND / POSSIBLE CONFLICT
- If --classes specified, highlight class-specific conflicts
- Overall Assessment: CLEAR / CAUTION / CONFLICT

NOTE: Most databases block automated access. WebSearch indirect results are acceptable.
Flag in report: "Results obtained via indirect search — formal clearance search recommended."
```

### Agent 4: App Store Conflict Check

```
Task: Check App Store and Play Store for apps named "{NAME}"

Steps:
1. WebSearch: site:apps.apple.com "{NAME}"
2. WebSearch: site:play.google.com "{NAME}"
3. WebSearch: "{NAME}" app mobile application
4. If --classes specified and includes class 9 (software), flag any app conflicts as HIGH priority

Return format:
- Apple App Store matches: [list with app name, developer, category, URL]
- Google Play matches: [list with app name, developer, category, URL]
- Exact name matches: YES/NO
- Similar name matches: [list]
- Assessment: CLEAR / MINOR CONFLICTS / MAJOR CONFLICT
```

### Agent 5: Social Media Username Availability

```
Task: Check social media username availability for "{NAME}"

Steps:
1. WebFetch: https://knowem.com/checkusernames.php?u={NAME} (if accessible)
2. If knowem blocked, check individually via WebSearch:
   - site:twitter.com/{NAME} OR site:x.com/{NAME}
   - site:instagram.com/{NAME}
   - site:facebook.com/{NAME}
   - site:linkedin.com/company/{NAME}
   - site:tiktok.com/@{NAME}
   - site:youtube.com/@{NAME}
   - site:github.com/{NAME}
3. For each platform, determine: AVAILABLE / TAKEN / UNKNOWN

Return format:
- Per-platform status: Twitter, Instagram, Facebook, LinkedIn, TikTok, YouTube, GitHub
- Handle consistency: Can the same handle be used across platforms?
- Assessment: EXCELLENT (most available) / GOOD (key ones available) / POOR (most taken)
```

### Agent 6: Slang & Cultural Safety Check

```
Task: Check if "{NAME}" has negative slang, offensive, or culturally inappropriate meanings

Steps:
1. WebFetch: https://www.urbandictionary.com/define.php?term={NAME}
2. If blocked, WebSearch: site:urbandictionary.com "{NAME}"
3. WebSearch: "{NAME}" slang meaning
4. WebSearch: "{NAME}" offensive meaning
5. WebSearch: "{NAME}" meaning in [major languages: Spanish, French, German, Chinese, Japanese, Korean, Arabic, Hindi]
6. WebFetch: https://www.wordsafety.com/check?q={NAME} (if accessible)
7. If blocked, WebSearch: "{NAME}" inappropriate "bad word" OR profanity OR vulgar

Return format:
- Urban Dictionary entries: [list with definitions and vote counts]
- Cross-language meanings: [any negative/inappropriate meanings found]
- Cultural concerns: [any findings]
- Assessment: SAFE / CAUTION (minor issues) / UNSAFE (significant problems)
```

### Agent 7: Pronunciation & Cross-Language Transliteration Analysis

This check runs purely on AI reasoning — no external tools needed. The Agent should analyze the brand name linguistically and return a structured report.

```
Task: Analyze the pronunciation and cross-language transliteration of brand name "{NAME}"
Context: Target regions: {GEO}

This is an AI-powered linguistic analysis task. Do NOT use WebSearch or WebFetch — use your built-in language knowledge only.

Perform the following analyses:

1. **IPA Phonetic Transcription**
   - Primary pronunciation (most natural English reading): provide IPA
   - Alternative pronunciations (other plausible ways to read it): provide IPA for each
   - Flag if there are 2+ equally plausible pronunciations (ambiguity = problem)

2. **Syllable Structure & Stress Analysis**
   - Syllable count and breakdown
   - Natural stress pattern (which syllable gets emphasis)
   - Is the stress pattern intuitive or could it be read differently?
   - Ease of pronunciation: any difficult consonant clusters, vowel collisions, or awkward transitions?

3. **Cross-Language Transliteration**
   Prioritize languages matching target regions ({GEO}), but always cover major languages.
   For each target market language, provide:
   - **Chinese (Mandarin)**: most natural 音译 (phonetic transliteration) — are the characters positive/neutral/negative?
   - **Japanese**: katakana rendering (カタカナ) — does it sound natural in Japanese? Any unintended meanings?
   - **Korean**: hangul rendering (한글) — any issues?
   - **Spanish**: how would a Spanish speaker naturally read it? Any unintended meaning?
   - **French**: how would a French speaker naturally read it? Any issues?
   - **German**: how would a German speaker naturally read it? Any issues?
   - **Arabic**: how would it be transliterated? Any issues?

4. **Pronunciation Consistency Score**
   Rate 1-10 how consistently people across different languages would pronounce it:
   - 9-10: Near-universal agreement (e.g., "Google", "Nike")
   - 7-8: Minor variations but core sound preserved
   - 5-6: Significant variation across languages
   - 1-4: Major ambiguity, different people would say it very differently

5. **Memorability & Speakability Assessment**
   - Is it easy to say aloud? Can you tell someone the brand name over the phone?
   - Is it easy to spell after hearing it? (critical for word-of-mouth)
   - Syllable count impact on memorability (1-3 syllables = ideal, 4+ = harder)
   - Any tongue-twister qualities?

Return format:
- Primary IPA: [transcription]
- Alternative IPAs: [list, or "none — unambiguous"]
- Syllable breakdown: [breakdown with stress marked]
- Cross-language transliterations: [table with language, rendering, notes]
- Pronunciation consistency score: [X/10] with reasoning
- Memorability score: [X/10] with reasoning
- Key concerns: [list any issues]
- Assessment: EXCELLENT / GOOD / FAIR / POOR
```

## Phase 2: AI Similarity Analysis

**Trigger**: Run ONLY if Phase 1 Agent 3 (Trademark Database) found active/pending trademarks in relevant classes.

If no conflicting trademarks were found, skip Phase 2 and note "No similar active trademarks found in target classes" in the report.

### Similarity Assessment Framework

For EACH potentially conflicting trademark found, evaluate three dimensions:

#### 1. Phonetic Similarity (Weight: 40%)

Analyze:
- Number of syllables (same/different)
- Vowel pattern match (e.g., A-E-A vs A-E-A = high match)
- Consonant pattern match
- Stress pattern (which syllable emphasized)
- Overall sound when spoken aloud
- Rhyming or near-rhyming

Score 0-100:
- 0-20: Clearly different sounds
- 21-40: Some phonetic overlap but distinct
- 41-60: Noticeable similarity, could be confused verbally
- 61-80: High phonetic similarity
- 81-100: Nearly identical pronunciation

#### 2. Visual Similarity (Weight: 30%)

Analyze:
- Character overlap (shared letters/characters)
- Word length comparison
- Starting/ending characters (more impactful than middle)
- Overall visual shape/silhouette
- Common subsequences

Score 0-100:
- 0-20: Visually distinct
- 21-40: Minor visual overlap
- 41-60: Moderate visual similarity
- 61-80: Could be mistaken at a glance
- 81-100: Nearly identical visually

#### 3. Conceptual Similarity (Weight: 30%)

Analyze:
- Semantic meaning of each name
- Industry/category associations
- Evoked imagery or feelings
- Root word or morpheme overlap
- Target audience perception

Score 0-100:
- 0-20: Completely different concepts
- 21-40: Tangentially related
- 41-60: Same conceptual family
- 61-80: Strong conceptual overlap
- 81-100: Same concept, different expression

### Composite Score Calculation

```
Composite = (Phonetic × 0.4) + (Visual × 0.3) + (Conceptual × 0.3)
```

### Risk Classification

| Composite Score | Risk Level | Status | Recommendation |
|----------------|------------|--------|----------------|
| 0-25 | LOW | PASS | Safe to proceed |
| 26-45 | MEDIUM | WARN | Recommend legal opinion before proceeding |
| 46-65 | HIGH | REVIEW | Must obtain legal clearance before proceeding |
| 66-100 | VERY HIGH | FAIL | Do not proceed — high likelihood of opposition/confusion |

### Output Format for Each Comparison

```
## Similarity Analysis: {INPUT_NAME} vs {EXISTING_MARK}

| Dimension | Score | Key Factors |
|-----------|-------|-------------|
| Phonetic (40%) | XX/100 | [specific analysis] |
| Visual (30%) | XX/100 | [specific analysis] |
| Conceptual (30%) | XX/100 | [specific analysis] |
| **Composite** | **XX/100** | **{RISK_LEVEL}** |

Jurisdiction: {COUNTRY}
Class overlap: {YES/NO — classes}
Status: {ACTIVE/PENDING}
Risk: {LOW/MEDIUM/HIGH/VERY HIGH}
Recommendation: {specific action}
```

## Phase 3: BNR Summary Report

Generate the final report in the following format:

```markdown
# BNR Report: {BRAND_NAME}
**Date**: {TODAY}
**Requested Classes**: {CLASSES or "All"}
**Regions Checked**: {REGIONS}

---

## Executive Summary

**Overall Grade: {A/B/C/F} — {PROCEED / PROCEED WITH CAUTION / CONDITIONAL / DO NOT PROCEED}**

{1-2 sentence summary of key findings and recommendation}

---

## Check Results Summary

| # | Check | Grade | Status | Key Finding |
|---|-------|-------|--------|-------------|
| 1 | Google Search Presence | {A-F} | {PASS/WARN/REVIEW/FAIL/ERROR} | {one-line summary} |
| 2 | Domain Availability | {A-F} | {PASS/WARN/REVIEW/FAIL/ERROR} | {one-line summary} |
| 3 | Trademark Databases | {A-F} | {PASS/WARN/REVIEW/FAIL/ERROR} | {one-line summary} |
| 4 | App Store Conflicts | {A-F} | {PASS/WARN/REVIEW/FAIL/ERROR} | {one-line summary} |
| 5 | Social Media Handles | {A-F} | {PASS/WARN/REVIEW/FAIL/ERROR} | {one-line summary} |
| 6 | Slang / Cultural Safety | {A-F} | {PASS/WARN/REVIEW/FAIL/ERROR} | {one-line summary} |
| 7 | Pronunciation & Transliteration | {A-F} | {PASS/WARN/REVIEW/FAIL/ERROR} | {one-line summary} |

---

## Detailed Findings

### 1. Google Search Presence
{detailed findings from Agent 1}

### 2. Domain Availability
{detailed findings from Agent 2}

### 3. Trademark Database Search
{detailed findings from Agent 3}

{If conflicts found, include Phase 2 similarity analysis here}

### 4. App Store Conflicts
{detailed findings from Agent 4}

### 5. Social Media Username Availability
{detailed findings from Agent 5}

### 6. Slang & Cultural Safety
{detailed findings from Agent 6}

### 7. Pronunciation & Cross-Language Transliteration
{detailed findings from Agent 7: IPA, syllable analysis, cross-language table, consistency/memorability scores}

---

## Trademark Similarity Analysis

{Phase 2 results if applicable, or "No conflicting trademarks found in target classes."}

---

## Action Items

### Immediate (before proceeding)
- [ ] {required actions}

### Recommended (before launch)
- [ ] {recommended actions}

### Optional (nice to have)
- [ ] {optional improvements}

---

## Disclaimers

- This is an automated preliminary screening, NOT a formal legal opinion.
- Trademark database results were obtained via indirect web search. A formal clearance search through a trademark attorney is recommended before filing.
- Some databases (WIPO, EUIPO, CNIPA) restrict automated access; results may be incomplete.
- Domain and social media availability may change between check time and registration.
- This report does not constitute legal advice. Consult a qualified trademark attorney for formal clearance.
```

## Grading Rubric

### Per-Check Grading (A-F)

| Grade | Criteria |
|-------|----------|
| **A** | No conflicts found, fully clear |
| **B** | Minor findings, no material risk (e.g., a dead trademark, unrelated class) |
| **C** | Moderate findings requiring attention (e.g., similar name in adjacent class, some domains taken) |
| **D** | Significant findings (e.g., active trademark in same class but different region, exact social handles all taken) |
| **F** | Blocking conflict (e.g., active trademark in same class AND region, offensive meaning found) |

**Pronunciation-specific grading:**
| Grade | Criteria |
|-------|----------|
| **A** | Unambiguous pronunciation, high consistency (8-10/10), easy to spell from hearing, positive transliterations |
| **B** | Minor ambiguity (1 alternative), consistency 7/10, mostly easy cross-language |
| **C** | Multiple plausible pronunciations, consistency 5-6/10, or problematic transliteration in a target market |
| **D** | Major pronunciation ambiguity, consistency 3-4/10, or negative meaning when transliterated |
| **F** | Unpronounceable in major target markets, or transliteration creates offensive word |

### Overall Grade

| Overall | Logic |
|---------|-------|
| **A — PROCEED** | All checks A or B, no REVIEW or FAIL status |
| **B — PROCEED WITH CAUTION** | Majority A/B, 1-2 checks at C, no FAIL |
| **C — CONDITIONAL** | Any check at REVIEW status, or 3+ checks at C/D |
| **F — DO NOT PROCEED** | Any check at F, OR 2+ checks at FAIL status |

## Multi-Name Comparison Mode

When `--compare` is active (or multiple names provided):

1. Run the FULL workflow (Phase 1 + Phase 2 + Phase 3) for EACH name **in parallel** (use Agent tool with one agent per name, or batch agents across names)
2. After all individual reports complete, generate a **Comparison Matrix**:

```markdown
## Comparison Matrix

| Criteria | {Name1} | {Name2} | {Name3} |
|----------|---------|---------|---------|
| Overall Grade | {grade} | {grade} | {grade} |
| Google Presence | {grade} | {grade} | {grade} |
| Domain (.com) | {status} | {status} | {status} |
| Trademark Risk | {grade} | {grade} | {grade} |
| App Store | {grade} | {grade} | {grade} |
| Social Media | {grade} | {grade} | {grade} |
| Cultural Safety | {grade} | {grade} | {grade} |
| Pronunciation | {grade} | {grade} | {grade} |
| Highest Similarity Score | {score} | {score} | {score} |

## Ranking

1. **{Best Name}** — {Overall Grade} — {reason}
2. **{Second}** — {Overall Grade} — {reason}
3. **{Third}** — {Overall Grade} — {reason}

## Recommendation

{Which name to proceed with and why, including trade-offs}
```

## Error Handling

- If an Agent fails or times out, mark that check as `ERROR` with the reason
- If 3+ checks fail, warn the user that results are unreliable
- Always output whatever results were obtained — partial data is better than no data
- Suggest manual follow-up for any ERROR items in the Action Items section
