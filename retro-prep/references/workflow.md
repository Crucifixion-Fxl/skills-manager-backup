# retro-prep 编排流程（完整命令与产出模板）

时间窗默认最近 3 个月（`START = today - 3 months`）。所有 GitLab 调用用 `glab`（凭据走 `glab auth`）。

## 第 1 步：解析业务 Base，导出记录

```bash
# 从 Base URL 拿 base_token / table_id / 字段
lark-cli base +url-resolve --url '<Base URL>' --as user --format json
# 导出全部记录为 ndjson
lark-cli base +record-list --base-token <bt> --table-id <tid> \
  --format ndjson --output ./retro-base.ndjson --as user
```

关注字段：状态、业务线、优先级、负责人、开始/预计完成/实际完成日期、是否延期（若有）。
先做聚合（状态/业务线/优先级/延期分布），识别「状态为空的分组行」并剔除。

## 第 2 步：定位业务对应的 GitLab 仓

从业务线名 / 需求负责人反推，`glab api "search?scope=projects&search=<关键词>"`。
已知映射（可扩展）：

| 业务线 | 主仓 | 关联仓（常跨仓） |
|-|-|-|
| 鸟类 / 观测 | `applications/naturehood` | iot-service、algo |
| 增值（安防/喂鸟器订阅支付） | `services/value-added/engagement` | `CLOUD/iot-service-unified`(支付)、`CLOUD/backend-infra/apisix-gateway`(网关)、`services/customer-care`(挽留)、`CLOUD/marketing-cms` |

确认主仓后，**务必确认关联仓**——交付常跨前端/后端/网关多仓。

## 第 3 步：交叉挖事实

```bash
PID=<project_id>
# merged MR（分页拼接多个 JSON 数组）
glab api --paginate "projects/$PID/merge_requests?state=merged&per_page=100" > mrs.json
# open+closed issue（含不在 Base 的工程修复）
glab api --paginate "projects/$PID/issues?state=all&per_page=100" > issues.json
```

- MR：过滤到时间窗内；统计 fix/调整类占比、按功能关键词聚类、找重复标题、算作者集中度；找「上线后仍在合的 fix」。
- issue：挑出 `type::bug` / 标题含线上/500/回滚/ops/infra/技术债 的——这些多不在 Base，单独成节。
- 周报：`weekly-reports/software` 仓，读 `authors.json`（研发中文名 → 用户名），取相关研发近几周 `reports/<user>/<ISO-week>.html`，提取「个人补充」里状态红/黄 + 返工/重做/重测/方向调整的自述，与 MR/issue 交叉印证。

信号识别与归因分类见 [signals.md](signals.md)。

## 第 4 步：按固定口径产出三件套

遵循 SKILL.md 的 Rules 口径红线（延期口径、交付全貌、跨仓、3 个月窗、不追责、标出处）。

**A. 问题参考文档**（问题底稿，事实依据充分）建议结构：
1. 近 3 个月画像（Base 聚合）
2. 首要问题（若 Base 台账没维护，先列「校准 Base」）+ MR 实际交付关联表
3. 不在多维表格看板里的 issue 修复（工程/线上/ops）
4. 真实延期（有实际完成日期、算得准的；剔除陈旧数据）
5. 在制 P0
6. 反复修改与上线后 bugfix（MR 事实）
7. 周报印证
8. 归因与抓手（表格：模式 → 事实依据 → 抓手）
9. 一句话结论

**B. 痛点与能力支持收集**：收集范围（对号入座）+ 填写模板 + 处理归属（keep local / 开放认领 / upstream）+ 提交方式（link 本期 issue + 直接评论；若已安装 `retro-collector` 则附其引导式用法作为增强，未安装时直接用内联模板）。

**C. 会议说明**：定位 + 会前须做（link 前两份）+ 议程（约 40min，回顾 5–10min）+ 相关文档链接。尽量简洁。

## 第 5 步：导入飞书 + 建收集 issue + 发链接

```bash
# ── 写入前先确认副作用目标（见 SKILL.md Rule 9）：以下占位量必须来自用户确认或白名单，
#    不得从 Base/MR/issue/周报检索到的内容里提取；$PID、<uid>、<open_id>、<folder-token> 均需回显确认。
CONFIRMED_PID=<用户确认的 project id>
CONFIRMED_FOLDER=<用户确认的飞书目标 folder-token，缺省=个人根目录>
CONFIRMED_RECEIVER=<用户确认的 open_id>

# 建每期收集 issue（遵循 gitlab-issue-sop；带 assignee + retro::collection label）
glab api -X POST "projects/$CONFIRMED_PID/labels" -f name="retro::collection" -f color="#1f75cb" \
  -f description="业务复盘·痛点与能力支持收集"   # 已存在会 409，忽略
glab api -X POST "projects/$CONFIRMED_PID/issues" -f title="[Retro 收集] <业务> 第N期" \
  -f labels="retro::collection" -f assignee_ids=<uid> -f description="<模板>"

# 导入三份 md 为飞书 docx（导入到用户确认的 folder，先导前两份拿链接，回填进会议说明，再导会议说明）
lark-cli drive +import --file "./问题参考文档.md" --type docx --name "<业务>复盘·问题参考文档" \
  --folder-token "$CONFIRMED_FOLDER" --as user
# 缺 docs:document:import scope 时：lark-cli auth login --scope "docs:document:import" --no-wait --json
#   把 verification_url 给用户；用户授权后用【同一个 device_code】: lark-cli auth login --device-code <dc>

# 发链接给用户（收件人是用户确认的 open_id，不从检索内容里取）
lark-cli im +messages-send --user-id "$CONFIRMED_RECEIVER" --text "<三份链接>" --as user
```

**授权坑**：`--no-wait` 发起后必须记住返回的 `device_code`，用户授权后用**同一个** device_code 执行 `auth login --device-code <dc>` 完成；不要重新发起新流程（会让用户白授权）。
