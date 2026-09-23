# Apify Agent Skills Setup

## API Key (MANDATORY — 强制要求)

**Apify 是 VOC 分析的核心工具，必须配置后才能开始任何调研工作。**

如果你还没有 API Key：

> **请立即联系 陈敬敏 (Jingmin Chen) 获取 APIFY_TOKEN。**
>
> **没有 Apify API Key = 无法执行 VOC 分析。** 这不是可选项。请先完成配置再开始调研。

## 安装方式

Apify Agent Skills 是官方提供的 AI Agent 数据采集技能集，源码：https://github.com/apify/agent-skills

### 方式一：npx skills 安装（推荐）

```bash
npx skills add apify/agent-skills
```

这会将 Apify 的所有采集技能添加到你的 AI 编码助手中（支持 Claude Code、Cursor、Codex、Gemini CLI 等）。

### 方式二：全局安装到 skills 目录

**Claude Code：**
```bash
/plugin marketplace add https://github.com/apify/agent-skills
/plugin install apify-ultimate-scraper@apify-agent-skills
```

**Cursor/Windsurf：** 参考 Claude Code 格式添加到 `.cursor/settings.json`

**Gemini CLI/Codex：** 指向 `agents/AGENTS.md` 或 `gemini-extension.json`

### 环境变量配置

安装后，需要设置 API Token：

```bash
# 在 .env 文件中添加
APIFY_TOKEN=<your-apify-token>
```

或者在 shell 环境中导出：
```bash
export APIFY_TOKEN=<your-apify-token>
```

**系统要求：**
- Node.js 20.6+

## Quick Verification

安装完成后，尝试让 AI 助手执行一个简单的数据采集任务来验证：
- "Search Reddit for discussions about [topic]"
- 如果 Apify skills 正常工作，会返回结构化的采集结果

## Available Skills

| Skill | 用途 | 覆盖平台 |
|-------|------|---------|
| Universal Scraper | AI 驱动通用爬虫 | Instagram, Facebook, TikTok, YouTube, Google Maps, Booking.com, TripAdvisor 等 50+ 平台 |
| E-Commerce | 电商数据采集 | Amazon, eBay 等（价格情报、评论、产品研究） |
| Social Media Analytics | 社交媒体分析 | 受众分析、内容分析、影响者发现、趋势分析 |
| Competitor Analysis | 竞品分析 | 品牌声誉监测、市场调研 |
| Lead Generation | 潜客采集 | Google Maps, LinkedIn, 企业网站 |
| Actor Development | Actor 开发 | 自定义 Apify Actor 开发与部署 |

## Apify REST API (Fallback)

当 Agent Skills 不可用时，仍可直接调用 Apify REST API：

```bash
# 列出 runs
curl "https://api.apify.com/v2/actor-runs?token=$APIFY_TOKEN"

# 下载 dataset
curl "https://api.apify.com/v2/datasets/<DATASET_ID>/items?format=json&token=$APIFY_TOKEN" \
  -o reference/<scraper_name>/dataset/<DATASET_ID>.json
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| npx skills 命令不存在 | 安装 Node.js: `brew install node` (需要 20.6+) |
| Auth failed | 检查 APIFY_TOKEN 是否正确设置 |
| Skills 安装成功但无法采集 | 检查网络连接和 token 权限 |
| 特定平台采集失败 | 参考 `references/failure-recovery.md` 使用替代方案或 Web Search |

## 定价说明

Apify Actors 采用按结果付费模式（pay-per-result），不同 Actor 价格不同。详见 Apify 平台各 Actor 页面。
