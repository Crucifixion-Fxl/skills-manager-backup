# create-pir

为 VicoHome / KiwiBit / VicoNature 测试账号在任意环境造真实 PIR 相册事件。

Skill 本体文档：[SKILL.md](SKILL.md)。本文档是给**人**快速上手用的 TL;DR。

---

## 30 秒上手

```bash
# 1. 用 profile 模式对话式建配置（脚本自动写到 ~/.config/addx/pir/<name>.env，chmod 600）
#    凭证不会进 skill 目录，避免 plugin 分发泄漏
python scripts/create_pir_event.py --init-profile my-vh

# 2. dry-run 验证（不写事件）
python scripts/create_pir_event.py --profile my-vh --dry-run

# 3. 正式推一条
python scripts/create_pir_event.py --profile my-vh

# 4. 看所有支持的 (brand, region, env) 组合
python scripts/create_pir_event.py --show-presets

# 5. 看当前生效配置
python scripts/create_pir_event.py --show-config
```

依赖（`requests` + `protobuf`）**首次运行自动安装**，或用
`uv run scripts/create_pir_event.py`（PEP 723 兼容）。

---

## 目录结构

```
create-pir/
├── SKILL.md                    主 skill 文档（Claude Code 读）
├── README.md                   这份（人读）
├── CHANGELOG.md                版本历史
├── references/
│   ├── brands.md              品牌 × 区域 × 环境速查 + 已知账号/设备
│   ├── troubleshooting.md     故障树（-2112 / tenantId / 相册物化延迟）
│   └── scenarios.md           典型使用场景对话
└── scripts/
    ├── create_pir_event.py    脚本本体（PEP 723 / 自动装依赖）
    ├── requirements.txt       传统 pip 依赖
    ├── .pir.env.example       配置模板（可提交）
    └── .pir.env               本地凭证（git 忽略，chmod 600）
```

---

## 安全规范

1. **密码永不硬编码**：所有凭证（email/password/device/userSn）**必须**通过 `.env`、环境变量或 CLI 提供；代码默认值为空。
2. **`.pir.env` 不入库**：被 `skills/.gitignore` 的 `**/.pir.env` 规则拦截；chmod 600 本地保护。
3. **prod 写入二次确认**：tty 下强制 `yes` 确认；非 tty（CI / skill）需上层显式授权。
4. **签名密钥不内置**：`PIR_SIGN_SECRET`（base64）必须通过 env / `.env` 文件提供，凭证从 MeterSphere 场景「[自动化] PIR 事件」提取，避免落仓。
5. **测试 S3 URL 可覆盖**：`PIR_TEST_VIDEO_URL` / `PIR_TEST_IMAGE_URL` 覆盖默认假 URL。
6. **推送验证跳过登录**：优先用 `--device-auth-only`；如已安全取得当前 App token，也可用单次 `PIR_APP_TOKEN`。

---

## 常见操作速查

| 操作 | 命令 |
|------|------|
| VicoHome US prod 推 1 条 | `python scripts/create_pir_event.py` |
| KiwiBit prod 推 1 条 | `python scripts/create_pir_event.py --brand kiwibit --env prod` |
| VicoNature prod 推 1 条 | `python scripts/create_pir_event.py --brand viconature --env prod` |
| 批量 10 条不验证相册 | `python scripts/create_pir_event.py --count 10 --no-verify` |
| 查所有支持组合 | `python scripts/create_pir_event.py --show-presets` |
| 只读验证凭证 | `python scripts/create_pir_event.py --dry-run` |
| staging 验证带 GeoIP 的鸟识别链路 | `python scripts/create_pir_event.py --profile <name> --bird-species cardinal --ai-location-ip <PUBLIC_IP> --ai-country-no US` |
| 发起 Bird 推送验证（随后按 trace_id 核对） | `python scripts/create_pir_event.py --profile <name> --device-auth-only --user-sn <numeric-user-id> --bird-species robin --bulk 1` |
| 查版本 | `python scripts/create_pir_event.py --version` |

---

## staging AI 地理门禁验证

bird/small_animal 默认复用设备 retained config 中的 `aiCloudParam`。如果 retained
config 较旧或由无设备请求上下文的后台流程生成，可能没有 `location`，导致依赖州/省
信息的 AI 流程被跳过。

staging 可显式提供一个可被 GeoIP 解析的公网 IP，让脚本先调用 IOT
`POST /deviceMsg/config` 生成新的 `aiCloudParam`：

```bash
python scripts/create_pir_event.py \
  --profile <staging-profile> \
  --bird-species cardinal \
  --ai-location-ip <US_PUBLIC_IP> \
  --ai-country-no US
```

限制：

- 仅 `staging` 可用，`pre/prod` 会拒绝执行。
- 即使手动传入 `--device-api`，也必须使用 HTTPS 并命中脚本已知的 staging
  preset 域名白名单，
  防止将测试请求误发到其他环境。
- 仅 `bird` / `small_animal` 可用。
- 必须是公网 IP；私网、回环和非法地址会被拒绝。
- `--ai-country-no` 可选，必须配合 `--ai-location-ip`；它只修改本次内存中的
  `aiCloudParam.countryNo`，不修改账号国家。当测试账号国家与目标地区不同时使用。
- 该调用不会发布或覆盖设备 MQTT retained config。
- 验证 coaching 时请换用未重复上报的图片/视频；AI 链路可能对重复素材去重，
  重复 PIR 未产出 coaching 不能单独证明地理门禁失败。

---

## 出问题找这里

先看 [references/troubleshooting.md](references/troubleshooting.md)。90% 的问题是 `deviceStatus=-2112`，根因是 **device_api 对 OEM 品牌跨租户识别失败**（详见故障树）。
