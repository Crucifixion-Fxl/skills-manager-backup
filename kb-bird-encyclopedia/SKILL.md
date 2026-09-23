---
name: kb-bird-encyclopedia
description: 从 KB（KiwiBit / naturehood）prod-us 后端导出指定鸟种的百科资料和稀有度。当用户说"导出 XX 鸟的百科"、"查 XX 鸟的稀有度"、"export bird wiki"、"鸟类百科 JSON"、"看看 XX 鸟的资料"、或任何需要从 KB 鸟类图鉴中按鸟名取数据的场景时触发。支持多语言（默认 en；用户要求才导其他语种，目标语种缺失时只告警不导出）。
---

# kb-bird-encyclopedia

通过 naturehood 公开 API 拉鸟种的 13 板块百科 JSON + rarity，输出到本地 `.json` 文件。脚本在 `scripts/fetch_bird.py`。

## Description

适用场景：

- 用户说"导出 Rose-breasted Grosbeak 的百科信息"
- 用户问"这种鸟的稀有度是什么"
- 内部需要 KB 鸟类百科原始数据做下游处理（翻译质检、校对、内容审核等）

数据源：

| 表 / API | 用途 |
|---|---|
| `species_reference` (rarity 1-6, conservation_status, image_url) | 稀有度 |
| `species_content` (locale, 13 板块 JSON) | 百科正文 |
| `GET /api/species/nearby` | common_name → scientific_name + rarity 反查 |
| `GET /api/species/content?name=&locale=` | 拉指定语种的百科 |

Rarity 字典（见 `server/migrations/002_collection_tables.up.sql` seed 注释）：

| Rarity | 含义 |
|:---:|---|
| 1 | Common |
| 2 | Uncommon |
| 3 | Rare |
| 4 | Very Rare |
| 5 | Critically Endangered / Recovering |
| 6 | Possibly Extinct |

环境：

| 变量 | 用途 | 是否必填 |
|---|---|---|
| `KB_EMAIL` | 登录邮箱 | **必填**（或用 `--email`）|
| `KB_PASSWORD` | 登录密码 | **必填**（或用 `--password`）|
| `KB_VERIFY_SSL` | 设为 `skip` 关闭严格 TLS 校验（应急 opt-out）| 选填，默认 strict |

> **前提**：本机能直连 `naturehood-prod-us.kiwibit.com`——这是 office-IP 限制的内部服务，只能从公司网络或 VPN 访问。

### Obtaining test credentials

凭据**不会**随脚本分发，必须自己提供：

- **找 skill owner 申请共享只读测试账号**——@xcao（GitLab）；账号仅有只读权限，用于跑通 skill 验证流程；不要做写操作或测任何敏感场景。
- **自己有 vicohome 账号**：直接用自己的 `--email/--password` 即可，体验和共享账号一致。

> 历史版本（!394 / !418 早期 commit）曾在脚本里硬编码过一个共享测试账号；该账号密码**已轮转**，旧 commit 历史里的凭据不再可用。

## Rules

### 操作流程

1. 登录拿 JWT：`POST https://api-us.vicohome.io/account/login/`
   - 先 POST `/user/encry` 看是否需要 SHA-256 加密密码（当前测试账号不需要）
   - 返回的 `data.token.token` 已带 `"Bearer "` 前缀
2. common_name → scientific_name 反查：分页扫 `GET /api/species/nearby?limit=200&offset=N&locale=en`，case-insensitive 匹配 `common_name` 或 `scientific_name`。**只能全局扫描**（未提供 search 接口）——`ORDER BY sr.rarity ASC, common_name`，常见鸟（rarity=1）按字母序排，找 R 开头的鸟约扫 5-6k 条 ≈ 2 分钟。
3. 多语言：每个 locale 单独 `GET /api/species/content?name=<scientific_name>&locale=<locale>`。**后端会在 locale 缺失时自动 fallback 到 en**——脚本通过对比响应里 `locale` 字段是不是请求的 locale 来检测；fallback 视为该语种不存在，**只打 NOTE 不写入输出**（用户的明确要求）。
4. 输出到 `<scientific_name>.json`，结构见下。

### 输出 JSON 结构

```json
{
  "scientific_name": "Pheucticus ludovicianus",
  "common_name_en": "Rose-breasted Grosbeak",
  "rarity": 1,
  "rarity_label": "Common",
  "conservation_status": "LC",
  "image_url": "https://bird-guide-cdn-prod-us.kiwibit.com/...",
  "silhouette_url": "https://bird-guide-cdn-prod-us.kiwibit.com/...",
  "species_id": 10571,
  "locales": {
    "en": { "field_identification": {...}, "plumages_molts_structure": {...}, ... },
    "zh": { ... }
  }
}
```

13 板块字段固定：`field_identification` / `plumages_molts_structure` / `systematics` / `distribution` / `habitat` / `movements_migration` / `diet_foraging` / `sounds_vocal_behavior` / `behavior` / `breeding` / `demography_populations` / `conservation_management` / `additional`。每个板块结构 `{ "title": "...", "content": [{ "heading": "...", "text": ["...段落..."] }] }`。

### 触发后的行为

1. 解析用户问题：拿到鸟名（common_name 优先，scientific 备选）+ 是否要多语言。
2. 跑 `scripts/fetch_bird.py`，把 stdout 直接给用户看（含稀有度）。
3. 输出文件路径。如用户问"稀有度是什么"，从 stdout 直接念出 `rarity` + `rarity_label`，不需要再读 JSON。
4. 用户没指定 `--output-dir` 时默认写当前工作目录。
5. **脚本退出非 0 时**：先去「故障诊断速查」对照 stderr 第一行匹配根因，再做下一步——不要把"network error"直接归因成"VPN 问题"或者凭"SSL"两个字就让用户改证书。

### 已知限制

- **没有 search API**，只能全局分页扫 nearby——R 开头的鸟约 2 分钟，A 开头几秒，Z 开头最久。脚本以遇到第一个 substring 匹配为准（`needle in common_name or scientific_name`）。如鸟名歧义会取最早出现的（rarity 低 + 字母序靠前）。
- **分页上限**：扫到 `PAGINATION_CEILING=20000` 条仍未命中时停扫并打 stderr 告警，不静默漏查。
- **office 网络限制**：API 域名带 `ingress.addx.io/sg: office`，只能从公司网络/VPN 访问。其他网络脚本会卡在 connect 阶段。
- **TLS 默认严格校验**。如本机 CA bundle 不全（典型：macOS python.org installer 未跑 `Install Certificates.command`），脚本会报 SSL 错误并打印两条修复路径：(a) 修 CA bundle，或 (b) `export KB_VERIFY_SSL=skip` 临时跳过校验（仅本次运行有效；trade-off 是 SSL 握手不再防 MITM，因 KB 是 office-IP 内部服务，风险可控）。
- **rarity 必走 nearby**：即使用 `--scientific`，还是要扫 nearby 拿 rarity（content API 不返 rarity）。
- **测试账号不入仓**：必须本地配 `KB_EMAIL/KB_PASSWORD` 或传 `--email/--password`，否则脚本直接报错退出。

### 故障诊断速查

| stderr 看到 | 真实原因 | 修法 |
|---|---|---|
| `ERROR: missing KB credentials.` | 没配凭据 | `export KB_EMAIL=... KB_PASSWORD=...`，或参考「Obtaining test credentials」找 skill owner 申请 |
| `urlopen failed for ... naturehood-prod-us...: ... (office-IP-restricted; need office network or VPN)` | 不在 office 网络/VPN | 连 office WiFi 或公司 VPN |
| `urlopen failed for ...: [SSL: CERTIFICATE_VERIFY_FAILED] ...` + 错误信息里有 `Install Certificates.command` / `KB_VERIFY_SSL=skip` 提示 | 本机 CA bundle 不全 | 推荐：跑 `/Applications/Python\ 3.x/Install\ Certificates.command` 治本；急用：`export KB_VERIFY_SSL=skip` 跑一次 |
| `HTTP 401 Unauthorized from .../account/login/` | 凭据失效或错 | 找 skill owner 拿新凭据，或自己改用 vicohome 个人账号 |
| 卡在 `Resolving species ...` 几十秒以上 | 全局扫 nearby 的已知限制（没 search API） | A 开头几秒，R 开头 2 分钟，Z 开头最久，耐心等 |
| `WARN: scanned 20000+ records without matching ...` | 鸟名拼写不对，或不在 species_reference | 换 `--scientific` 传学名，或用更精确的 common name |
| `ERROR: common name '...' not found.` | 同上但更早被检出 | 同上 |

## Examples

### ✅ Good：默认导出英文

```bash
python3 scripts/fetch_bird.py --name "Rose-breasted Grosbeak"
# stdout:
# Wrote ./Pheucticus_ludovicianus.json
#   Common name:  Rose-breasted Grosbeak
#   Scientific:   Pheucticus ludovicianus
#   Rarity:       1 (Common)
#   Conservation: LC
#   Locales exported: en
```

### ✅ Good：用户要求多语言

```bash
python3 scripts/fetch_bird.py --name "Rose-breasted Grosbeak" --locales en,zh,ja,ko
# stderr:
#   NOTE: locale 'ko' has no content; backend fell back to 'en'. Skipping export for ko.
# stdout:
#   Locales exported: en, zh, ja
#   Locales NOT available: ko
# 输出文件 locales 字段只含 en/zh/ja，ko 被跳过并告知用户。
```

### ✅ Good：直接给学名

```bash
python3 scripts/fetch_bird.py --scientific "Pheucticus ludovicianus" --locales en
```

### ❌ Bad：在没有 office VPN 时跑

```bash
python3 scripts/fetch_bird.py --name "..."
# 卡在 connect — naturehood-prod-us.kiwibit.com 是 office-IP 限制。
# 解决：连公司 VPN 或在 office 网络。
```

### ❌ Bad：locale 缺失时强写到 JSON

```python
# 反例：直接把 fallback 内容写进目标 locale
out["locales"]["ko"] = en_sections   # 用户会以为是真韩文
```

正确做法：检测 `response.locale != requested_locale` 时跳过该 locale 并告诉用户该语种不存在。

## References

- naturehood 仓库：`git@gitlab.addx.ai:application-layer/naturehood.git`（master）
- 表结构：`server/migrations/012_collection_species_schema.up.sql`
- API 定义：`server/api/collection.api`
- 鉴权样例：`admin/tests/helpers/flutter-login.mjs`
- 架构文档：`docs/architecture/collection/overview.md`
