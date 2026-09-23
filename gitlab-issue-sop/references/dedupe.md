# 创建 issue 前查重 (Dedupe)

提 issue 之前，先用本 skill 自带的查重脚本跨当前仓、相关代码仓和集中 issue 项目池搜相似候选，避免在 g0-ios / g0-android / iot-service-old / iot-service-unified / issues/software 等来源之间重复提单。

## 使用

```bash
# 把 issue 草稿写到工作目录
mkdir -p .issue-dedupe
cat > .issue-dedupe/proposed.md <<'EOF'
<这里是 issue 正文草稿>
EOF

# 默认覆盖 app 仓、iot-service 仓和 issues/software 下的软件 issue 项目池
python3 <skill-dir>/scripts/issue_dedupe.py \
  --title "<草稿标题>" \
  --body-file .issue-dedupe/proposed.md \
  --state all
```

参数：
- `--title` 必填，issue 标题草稿
- `--body` / `--body-file` 二选一，正文
- `--state open|closed|all`，默认 `all`
- `--repo GROUP/REPO` 可重复，覆盖默认仓
- `--extra-repo GROUP/REPO` 可重复，在默认仓基础上追加
- `--platform auto|gitlab|github`，默认 `auto`（按 `git remote` 与可用 CLI 自动选）
- `--limit` 每个仓最多取多少条 issue 做比对，默认 200
- `--top` 输出 top N 候选，默认 8
- `--json` 机器可读输出

## 输出与决策

脚本对每个候选给出 0-1 的相似度分数，并按状态给出一条 `Recommendation`：

| 候选 | 分数 | 推荐处理 |
|---|---|---|
| open / score ≥ 0.55 | 高度相似 | `merge/update existing`：补充新 evidence 到老 issue，不新建 |
| closed / score ≥ 0.55 | 高度相似 | `reopen/comment` 如同 regression；否则新建并关联 |
| open / score ≥ 0.25 | 中等相似 | 对比根因，相同就合并，不同就新建并关联 |
| closed / score ≥ 0.25 | 中等相似 | 新建并关联，除非是同一 regression |
| 无候选 | – | `new issue`，按 SOP 走 |
| **所有目标仓查询失败** | – | `insufficient data` — 先修 CLI / 权限再提，**禁止此时直接新建** |

## 写操作前确认（强制）

无论结果是哪一档，**在 GitLab 写操作（创建 / 评论 / reopen / close / label / assign）之前必须先询问用户确认**。脚本本身只读，不做写操作。

## 扩展默认仓

- 环境变量 `ISSUE_DEDUPE_REPOS="GROUP/REPO,..."`
- 工作目录文件 `.issue-dedupe-repos`（一行一个仓）
- 用户级配置 `~/.codex/issue-dedupe-repos.txt`

## 词典维护

中文 stopwords / 同义归一化在 `references/lexicon.json`。新增 OEM 品牌词、产品代号、缩写时改这个 JSON，不要改 Python 源码。

## 性能特性

- 默认 app 仓 + iot-service 仓 + `issues/software` issue 项目池并行 fetch，耗时取决于项目数
- `tokens` / `char_ngrams` / `lexicon` 加 `@functools.cache`，proposed-side 不会被反复 tokenize
- 评分维度：标题 SequenceMatcher 相似度（0.30） + 标题 token Jaccard（0.18） + 标题+正文 cosine（0.22） + 正文 cosine（0.10） + 字符 2/3-gram Jaccard（0.16） + label hint（0.04）

## 测试

```bash
python3 -m unittest skills.gitlab-issue-sop.tests.test_issue_dedupe -v
```

覆盖 12 个 case：
- 中文标题相似排序（电池横幅类）
- 默认仓常量和软件 issue 项目池
- recommendation() 各 state × 阈值组合
- 0.55 / 0.25 边界
- 全失败 → `insufficient_data` 覆盖任何分数
