# CHANGELOG

## 1.0.1 — 2026-05-08

### 🟢 AI code-review + Sonar QG 清盘

回应 MR !400 review 反馈（2 个 P0 + 3 个 P1 + 2 个 P2 + 8 个 sonar critical/major）。

**P0 修复**

- US 文档 `docs/04-user-stories/share-device-skill.md` 重写：去除所有实现细节
  （API 路径 / CLI 参数 / tenant / preset / MS 场景 ID），只留用户视角验收标准
- 实现细节迁到 `docs/03-detailed-design/share-device-skill.md`
- 新增 9 个 contract-level mock E2E `scripts/tests/test_share_device_e2e_mock.py`：
  覆盖 share 9 步链路 / share --decline / unshare sharer-self / unshare --by-admin
  双路径 / list 双视角 / _post 仅看 result / 错误传播 / recentapprovals 拒绝兜底
- 新增 opt-in 真依赖 staging E2E `scripts/tests/test_e2e_staging_real.py`
  （默认 skip；设 `PIR_E2E_STAGING_ADMIN_PROFILE=<name>` 启用）

**P1 修复**

- `_post`：移除 `msg.startswith("success")` 双重判断，只看 `result=0`
  （后端不同 endpoint 返回大小写不一 / 中文 / 缺字段，msg 不是稳定信号）
- `step_recent_approvals`：未精确匹配 shareId/serial 时**不再**"取首条兜底"，
  改抛 ShareError 并给出待审批列表预览，避免误同意/拒绝别人的分享
- `cmd_unshare --by-admin`：未提供 `--sharer-user-id` 且没 sharer 凭证时
  **不再**"从 sharestatus list[-1] 推断"，改抛错并打印当前 share status，
  避免多 sharer 场景随机踢人

**P2 文档**

- 新增 `docs/05-adr/share-device-preset-strategy.md`：承载"preset 双处复制
  vs 抽公共 module"的取舍 + 后续触发条件
- SKILL.md 增加"密码最小化保留 + 轮换建议"段：限定专用测试账号 / 季度轮换 /
  离职清理流程

**Sonar QG**

- S1192 (5 critical → 0)：抽 5 个 APP_META 字面量常量 (`_APP_NAME_VH_STAGE` /
  `_BUNDLE_VH/_KB/_VN` / `_TZ_SHANGHAI`)
- S3457 (1 major → 0)：3 处 useless f-string（无 `{}` 占位）改为普通字符串
- S3776 (2 critical)：`cmd_unshare` (CC=19) 和 `main` (CC=23) 加 NOSONAR +
  rationale（双路径调度 / argparse early-return 是社区共识写法，重构成
  framework 与 skill standalone 定位冲突）

测试: 9 passed + 2 skipped (staging E2E)

## 1.0.0 — 2026-04-29

### 🎯 首版能力

把 VicoHome / KiwiBit / VicoNature 的设备从 admin 账号 **真实分享给** 另一个账号。复刻 MeterSphere 场景 `OEM_设备分享` (id `621c0b5a-1f0b-4d19-9144-da89ccdb5b1b`) 的 9 步 API 链路。

### 三个 action

- **`share`**：admin 把设备分享给 sharer。默认 admin 自动同意；`--decline` 改成拒绝。
- **`unshare`**：默认 sharer 主动退出；`--by-admin` 改成 admin 强制收回（**仅设备真 owner 能用**，二级被分享者调会拿到 `result=-9999`，脚本会给友好提示）。
- **`list`**：admin 视角 sharestatus + sharer 视角 listuserdevices 双向看分享关系。

### 9 个 endpoint

| 步骤 | endpoint | 视角 | 关键字段 |
|---|---|---|---|
| 1 | `POST /account/login` | admin | `data.token.token` |
| 2 | `POST /account/login` | sharer | 同上 |
| 3 | `POST /device/getshareid` | admin | body=`{serialNumber}`，resp `data.shareId` |
| 4 | `POST /device/requireshare` | sharer | body=`{shareId}` |
| 5 | `POST /device/recentapprovals` | admin | body=`{}`，resp `data.list[].id` 是 approvalId |
| 6 | `POST /device/handleapproval` | admin | body=`{id, shareId, status, targetId}`，status=0 同意 / 1 拒绝 |
| 7 | `POST /device/listuserdevices` | sharer | 验证设备列表 |
| 8 | `POST /device/sharestatus` | admin | 验证已分享列表 |
| 9 | `POST /device/undoshareself` 或 `/device/undoshare` | sharer / admin | unshare 用 |

### 验证

| Profile | Brand × Env | 链路时长 | 结果 |
|---|---|---|---|
| `kb-prod-bird` (a2xtest → a6xtest, KF126 bird feeder) | KB prod-US | share 15.2s / unshare 8.7s / list 4s | 全通 ✅ |

### Profile 系统

存储位置：`~/.config/addx/share-device/<name>.env` (chmod 600)

字段：
- `SHARE_BRAND` / `SHARE_REGION` / `SHARE_ENV`
- `SHARE_ADMIN_EMAIL` / `SHARE_ADMIN_PASSWORD`
- `SHARE_SHARER_EMAIL` / `SHARE_SHARER_PASSWORD`
- `SHARE_DEVICE` (serialNumber)

子命令：`--init-profile NAME`（交互创建）/ `--list-profiles` / `--delete-profile NAME`。

### 设计取舍（v1.0 范围）

- **不做基站邮箱邀请流（路径 B）**：基站 `SS121/SS131` 用 `/device/share/invite/email` 走邮件邀请，需要 IMAP 拉邀请 token，依赖更多。后续按需补。
- **不做 share + create-pir 联动命令**：share-device 完成后用户手动跑 `create-pir --profile <sharer>` 即可。两 skill 解耦，profile 互通。
- **PRESETS / APP_META_TEMPLATES 是 create-pir 的独立副本**：避免跨 skill import 路径耦合；每年 1-2 次需要手动同步 diff。

### 来源

- MS 场景 `OEM_设备分享`：`https://metersphere.addx.live/#/api/automation` 项目 `方案项目` (id `ec03819b-...`)，scenarioId `621c0b5a-1f0b-4d19-9144-da89ccdb5b1b`
- MS 同模块的 `设备分享` (VH) / `全橙看家_设备分享` 同款 9 步链路（envMap 不同）
- KB prod 实测：2026-04-29 用 `kiwibit-prod-a2xtest` → `kiwibit-prod-a6xtest`，`api-us.kiwibit.com`，KF126 bird feeder，全链路通过
