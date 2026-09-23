# CHANGELOG

## 1.12.1 — 2026-09-17

### 🐦 推送验证设备鉴权

- 新增 `--device-auth-only`，仅通过设备签名获取 device token，不调用
  `/account/login`，避免合成事件覆盖真实 App 的推送注册和服务端语言。
- 设备鉴权模式要求显式提供数字 `--user-sn`，并跳过账号相册、Bird Tab 与账号级
  AI 开关查询；执行结果通过 `trace_id` 继续核对识别、通知候选和最终投递。
- 新增单次 `PIR_APP_TOKEN` 环境变量复用能力，允许在确需账号读接口时跳过
  登录；token 不从 profile 读取，也不会在配置或日志中明文输出。
- 移除登录 token、设备 token 和签名片段的成功日志。

## 1.12.0 — 2026-07-15

### 🐦 staging Video ID Coach GeoIP 链路验证

- 新增 `--ai-location-ip <PUBLIC_IP>` / `PIR_AI_LOCATION_IP`，仅允许在 staging 的
  `bird` / `small_animal` AI 识别链路使用。
- 传入该参数后，不再复用可能缺少 `AiCloudParam.location` 的 retained config；改为
  调用 `POST /deviceMsg/config`，通过 `X-Forwarded-For` 让 IOT 按测试 IP 生成新的
  `aiCloudParam`，再发送给 ai-cloud imageInfer。
- 新增 `--ai-country-no <CC>` / `PIR_AI_COUNTRY_NO`，可在不修改账号资料的前提下，
  仅对本次 staging 事件覆盖 `AiCloudParam.countryNo`。该参数必须与
  `--ai-location-ip` 同时使用。
- 使用 Protobuf 动态描述符只修改 field 5，保留原 `aiCloudParam` 中所有未知字段。
- `/deviceMsg/config` 只构建并返回配置，不发布 MQTT retained 消息，因此不会改写
  设备现有 retained config。
- prod/pre 明确拒绝该参数；手动 `--device-api` 覆盖也必须使用 HTTPS 并命中已知 staging
  preset 域名白名单；
  私网、回环和非法 IP 也会 fail-fast，防止测试能力误用。
- 默认路径保持不变：未传参数时继续读取 `/deviceMsg/queryRetainedMsg`。
- 补充回归用例，覆盖 staging/公网 IP/参数组合限制、新 config 请求和
  Protobuf 未知字段保留。

## 1.11.6 — 2026-05-08

### 📦 .gitattributes / .gitignore 关注点分离（chore，零功能影响）

之前 `scripts/test_videos/.gitattributes` 把 mp4 和 ts 都标了 binary，但其实两者
本质不同：
- `.mp4` 是**已提交**的策展测试 fixture
- `.ts` 是 **ffmpeg 运行时生成**的临时切片（normally 在 `/tmp/create_pir_video_*/`，
  Session.tmp_dirs finally 块自清）

按"`.gitignore` 管运行时 / `.gitattributes` 管策展 fixture"重新分工。同时把范围
扩展到 `test_images/` 下 **21 张 jpg/jpeg** 之前完全裸奔的图片（Windows 同事
clone 时 git 可能做 CRLF/LF 转换破坏二进制内容）。

**改动**：

- 删除 `scripts/test_videos/.gitattributes`（原 .ts/.mp4 混杂规则）
- 新增 `skills/create-pir/.gitattributes`（路径限定）：
  - `scripts/test_videos/*.mp4    binary`
  - `scripts/test_images/**/*.jpg  binary`
  - `scripts/test_images/**/*.jpeg binary`
- 新增 `skills/create-pir/.gitignore`（防御性）：
  - `scripts/test_videos/*.ts`（防止 ffmpeg 运行时切片意外落入 repo）

**关注点分离**：

| 文件 | 角色 | 覆盖 |
|---|---|---|
| `.gitattributes` | 已提交的二进制 fixture，告诉 git 怎么处理 | mp4 / jpg / jpeg |
| `.gitignore` | 运行时产物，告诉 git 别追踪 | ts (ffmpeg 切片) |

未来如果有人想真提交一个 .ts fixture 当离线测试素材，要 `git add -f` 并在
`.gitattributes` 里加对应 binary 条目（已在 .gitignore 注释里写明流程）。

**验证**：

```
git check-ignore -v scripts/test_videos/__dummy.ts   → 命中 .gitignore L18 ✓
git check-attr  -a scripts/test_videos/bird_a4x_1.mp4 → binary set ✓
git check-attr  -a scripts/test_images/birds/robin/1.jpg → binary set ✓
```

**零运行时影响**：不改任何 Python 代码 / SKILL.md / Profile 结构。仅影响 git 对二
进制资源的处理。

## 1.11.5 — 2026-05-08

### 🟢 SonarQube QG 清盘 (21 critical/major + 8 hotspot)

回归 SonarQube QG 三条 ERROR 条件 (new_critical_violations / new_major_violations
/ new_security_hotspots_reviewed)。

- **S1192 重复字面量** (9 critical → 0)：抽 11 个 URL / 5 个 APP_META / 1 个 MIME 常量
- **S1172 unused param** (2 major → 0)：删 `_cfg_from_profile_fields` 的 `name`、
  `_build_static_video_from_image` 的 `cfg`，同步更新调用方
- **S3358 nested conditional** (1 major → 0)：`_format_device_row` 三元嵌套抽出
  独立 `_online_mark` 函数
- **S3776 cognitive complexity** (9 critical)：核心业务函数 (main/step_*/wizard)
  线性流程拆分会破坏可读性与后端协议对齐。每个 `def` 行加 `# NOSONAR` + 上方
  注释说明为什么不拆，符合 sonarqube-mr-gate skill "suppress 必须有 rationale"
- **S2068/S4790/S5443 hotspots** (8 → all REVIEWED/SAFE via API)
  - S2068：测试夹具 fake password / 遮蔽 sentinel `***`
  - S4790：HMAC-SHA1 是 IoT 设备协议规定，无替代
  - S5443：pytest tmp_path 是沙箱目录

## 1.11.4 — 2026-05-08

### ✅ 真依赖 staging E2E + ffmpeg 临时目录清理 + 安全审计文档

- **新增 `scripts/tests/test_e2e_staging_real.py`**：跑真 staging API 的 E2E
  dry_run 链路（默认跳过；设 `PIR_E2E_STAGING_PROFILE=<name>` 或全套
  `PIR_E2E_STAGING_*` env 启用）。直接回应 AI code-review 红线"缺真实依赖
  E2E 自动化覆盖"。CI 主流程不跑（凭证不能进 secret store / 不污染 staging 账号），
  但 reviewer 和 maintainer 本机可一键验证完整真链路
- **修复 ffmpeg 临时目录泄漏**：`_build_static_video_from_image` 和
  `_prepare_real_video` 创建的 `/tmp/create_pir_{static,video}_*` workdir
  原本永不清理，批量造数据（`--bulk 5 --variety 5` = 25 条）会留 50 个临时目录
  到磁盘。现注册到 `Session.tmp_dirs` 由 `create_one` 末尾 `finally` 块统一 rmtree
- **强化 `scripts/validate.py:50-65` create-pir 豁免说明**：补足 5 条审计证据
  （getpass / chmod 600 profile / 签名密钥仅 env / 双层校验），明确豁免范围
  仅限"input() 误报"，凭证 / 危险命令 / 网络外联仍走完整审查

## 1.11.3 — 2026-05-08

### ✅ E2E 链路自动化覆盖

回归 AI code-review 红线"缺真实依赖 E2E 自动化覆盖"。
真实 prod E2E 需要真账号 + 真设备 + 真写用户相册数据，CI 内不可行；
改为 contract 级 E2E：拦截 `_post_json`，按 MeterSphere 录制响应回放。

`scripts/tests/test_create_one_e2e_mock.py` 7 个用例覆盖：

- dry_run 4 步严格顺序契约（login → wakeupDevice → httpToken）
- login payload 关键字段（email/password/app）
- httpToken 签名真按 HMAC-SHA1(secret, serial+ts) 计算（不是占位）
- JWT app_token 解析提取 user_id
- 登录失败 / httpToken 失败 / PIR_SIGN_SECRET 缺失 三类错误正确传播 PirError

完整造 PIR 7+ 步链路（report → uploadImage → AI infer → uploadComplete）状态依赖
过重（S3 SigV4、ts 切片、ai-cloud 多帧推理），mock 容易过度拟合而失去契约价值；
该路径继续依赖 README/SKILL.md 列出的 prod manual E2E（trace_id 可查）。

## 1.11.2 — 2026-05-08

### 🐛 bird 一键流程：自动 firmware-preset 真生效

- 修：bird 一键流程下用户没显式传 `--device-firmware-preset` 时，本应自动落 `kf126`
  但因 `cfg.device_firmware_preset` 已被 `_coalesce` fallback 到默认值，
  `if not cfg.device_firmware_preset` 永远不触发 → 实际跑出来仍是 `cx-cq121c`
  现按 `args.device_firmware_preset is None` 判定"用户没传"，落 `kf126`
- 抽 `apply_bird_auto_defaults(args, cfg)` 出来便于单测覆盖
- 单测 +3：用户没传 / 用户显式传 firmware / 用户显式传 object-type 三条路径

### 🧹 .env 策略文档统一

- SKILL.md 加载顺序改为 profile（推荐）→ 全局 → 本地，移除"`<skill_dir>/scripts/.pir.env` 推荐"误导
- "首次使用" 示例换成 `--init-profile`，避免引导用户把凭证写进 skill 目录被 plugin 分发

## 1.11.1 — 2026-05-08

### 🔒 移除内置签名密钥（含修复 .env 加载链路）

- 删除 `_BUILTIN_SIGN_SECRET_B64` 字面量；`DEVICE_SIGN_SECRET_B64` 仅从 `PIR_SIGN_SECRET` env / `.env` 读取
- **修复 .env 加载链路**：原来 `DEVICE_SIGN_SECRET_B64` 仅在 import 时绑定 `os.getenv`，profile/.env 中的 `PIR_SIGN_SECRET` 实际不生效。现移到 `resolve_config()` 末尾按优先级 env > .env 重赋值
- SKILL.md / troubleshooting.md 移除明文 secret 字面量；改用 `os.environ["PIR_SIGN_SECRET"]` 引用
- 未设置时报错指引：从 MeterSphere 场景「[自动化] PIR 事件」提取
- 同步更新 SKILL / README / troubleshooting / `.pir.env.example` 的描述与示例

## 1.10.0 — 2026-04-26

### 🎬 真视频上传 —— 让相册条目可真播放

之前所有 `--object-type` 链路上传的"3 段 ts"实际是封面 jpg 字节复用，仅 Content-Type
头标成 `video/MP2T` 骗后端。后端 uploadComplete 校验给过、相册条目可见、AI tag 也落，
但点击播放在 KB / VH / VN app 端会黑屏（jpg 当 H.264 ts 流喂解码器必败）。

v1.10.0 加 `--video <path>` / `PIR_VIDEO` 走真切片真上传链路。

### 新增

- **`--video <path>`** CLI 参数（也支持 `PIR_VIDEO` env / `.env`）
  - 接受 mp4 文件路径，或 `scripts/test_videos/` 下的素材名
  - 触发 `_prepare_real_video` 用 ffmpeg 切 3 段 mpegts + 抽视频首帧作封面
  - 切片在 `step_login` **之前**执行（fail-fast：视频/ffmpeg 有问题不浪费云端 API）
- **`scripts/test_videos/bird_a4x_1.mp4`**（1.1MB / 854×480 / 12.96s 真实鸟视频）作为开箱即用素材
- **新 helper `_prepare_real_video(cfg, sess)`**：调 ffmpeg 切片 + ffprobe 探查时长 / 分辨率
  - 输入：`cfg.video_path`
  - 输出：填充 `sess.image_bytes`（封面 jpg）/ `real_ts_segments` / `real_ts_durations_ms` /
    `real_video_resolution`
  - 视频时长 < 9s 时立即报错（切不出 3 段）
  - 系统无 ffmpeg / ffprobe 时给出友好安装指令
- **`Config.video_path`** 字段
- **`Session.real_ts_segments` / `real_ts_durations_ms` / `real_video_resolution`** 字段

### 修改

- `step_upload_ts_segments`：检测 `sess.real_ts_segments`，用真 ts 字节代替封面 jpg 上传
- `_upload_complete_body_real`：sliceList 的 `period` 用真段时长（ms），`fileSize` 用真段大小，
  `resolution` 用 ffprobe 探查的真分辨率（覆盖 `DEVICE_FIRMWARE_PRESETS` 的写死值）

### 实证

| 链路 | trace_id | 验证 |
|---|---|---|
| POC（手写脚本验证概念）| `00cF0fxJ07UU2qTxhK9tOCWhlipY1` | ✅ KB app 上真播放 12.9s 鸟视频，端到端通 |
| 固化版 `--video bird_a4x_1.mp4` | `0DIi09VO0gvt2tdjha9pOOKDlP405` | ✅ ffmpeg 切 3 段真 ts (sizes=[466240, 508540, 500456] / durations=[4380, 4410, 4343]ms / 854×480) → S3 上传 → 9 步全 200 |
| 老链路（无 `--video`，零回归 smoke） | `0BCK0UzW0j6x2JeUhQ4vOWlrl0eC4` | ✅ 行为完全等同 v1.9.1（封面 jpg 字节当 ts，相册可见但 app 端视频段黑屏 —— 加 `--video` 才真播）|

### 依赖

- 系统装 `ffmpeg` 和 `ffprobe`（仅当传 `--video` 时校验；不传走老路径不强制）
- macOS: `brew install ffmpeg`；Ubuntu: `sudo apt install ffmpeg`

### 不影响

- 不传 `--video` 时所有现有行为保持，零回归
- SS131 基站链路（`step_upload_ai_image`）未触及

## 1.9.1 — 2026-04-26

修复 `step_ai_cloud_infer` multipart 格式，让 `--object-type` 链路真正能在 prod 跑通并落 AI tag。

### 🐛 问题

v1.9.0 把 `json` 段当成普通 form field（`data={"json": ...}`），后端（Java 多段解析器）只把带 filename 的部分算作 "required form data parts"，导致 prod 一律 `400 "Missing required form data parts"`。

之前 v1.9.0 ChangeLog 把这归因为"后端契约问题，非脚本问题" —— **撤回该判断，是脚本 multipart 格式不对**。

### 🔧 修法

`step_ai_cloud_infer` 改用 `files=` 同时塞两段，让 `json` 也成为 file part（filename="json"，Content-Type: `application/json; charset=UTF-8`），并补 `Host` / `User-Agent` 两个头与参考实现 `device-cloud-client/api_steps.py:_send_ai_image_inference_impl` 对齐。

### 📊 实证

- KB prod / KF126 / `--object-type bird` / bird_cn_1.jpeg
  → trace_id `0iO80G3C0zff2zZshJ2wOs0QlJuU2`
  → 30 秒后相册自动出 **bird tag + birdName + birdStory + birdTagText + birdStdName + birdImageUrl**
- 隔离 A/B 实验：`data={"json":...}` → 400；`files={"json":(...,Content-Type)}` → 200

### ⚠ 不需要新依赖

stdlib `requests.post(files={...})` 即可，不必引 `requests-toolbelt`。

### 📍 受影响范围

仅 `step_ai_cloud_infer`（KF/CG 摄像头链路）。SS131 基站的 `uploadAIImage` 路径未改。

---

## 1.9.0 — 2026-04-25

修正 v1.7.0 设计偏差：tag 类 PIR 的链路选择从「按 object_type」改为「按设备类型」。

### 🔄 链路路由重构（不影响老链路）

之前：
```
person/pet/vehicle/package → uploadAIImage（基站项目场景的实现）
bird/small_animal          → ai-cloud imageInfer
```

现在（实证依据：MS 基站项目 vs 方案项目两组场景对比）：
```
ss131 基站设备   → uploadAIImage（v1.7.0 现有实现，照旧）
其他摄像头（CG/KF）→ ai-cloud imageInfer（统一，不分 tag 类型）
```

object_type 现在只决定 boxes 内容（name + 坐标）和 with_box 标记，不再决定走哪条链路。

### ✨ ai-cloud 链路扩展

`step_ai_cloud_infer` 加 `with_box` 参数：
- `bird / small_animal`：永远 `boxes=[]`（让模型识别）
- `person / pet / vehicle / package`：3 帧推理中**中间帧 with_box=True**，带 `OBJECT_BOX_PRESETS` 的精准坐标 + `name=<type>`
- `motion`：`boxes=[{}]` 占位

### 📊 v1.9.0 端到端结果

| 命令 | 结果 |
|------|:---:|
| 老链路（无 object-type）VH prod | ✅ 10.4s 入库（零回归确认）|
| 摄像头 + tag 推进度 | 从 v1.8.0 的"uploadAIImage 404 立即失败" → v1.9.0 的"成功推到 ai-cloud imageInfer 这一步" |
| KB KF126 + person | ✅ 8 步成功 → 卡 ai-cloud 400（同 bird，prod 多接口的契约问题） |
| VH CG625A1 + vehicle | ✅ 8 步成功 → 卡 ai-cloud 400 |

### 🆕 多 ai-cloud endpoint 实测

不同设备拿到不同的 prod ai-cloud endpoint：
- VH CG625A1（CN）→ `https://ai.addx.live/ai-cloud/...`
- KB KF126（US）→ `https://ai-us-gg.addx.live/ai-cloud/...`

endpoint 通过 `/deviceMsg/queryRetainedMsg` 运行时拿到，脚本无需写死。

### ⚠️ 残留待解（后端契约）

prod ai-cloud `imageInfer` 对客户端当前 multipart（`json` + `file0` part）回 400 "Missing required form data parts"。MS 场景在 prod 跑这些场景**也是 ERROR**，说明后端契约改了 MS 跟不上。这是后端问题，不在脚本可控范围。

**v1.9.0 的核心价值**：链路设计对齐 prod 实际架构；后端任何时候补齐契约说明，脚本只改 1 处 multipart 字段就能通。

---

## 1.8.0 — 2026-04-24

新设备（CG625A1 等）**S3 直传基础设施**就绪；不影响已有 object-type 链路。

### 🆕 双上传协议自适应
- `step_report_pir` 解析响应时判断走哪条上传路径：
  - **方式 A（老）**：`bxsCredentials.accessUri[0]` → 提取 ptoken → PUT `<business_api>/videoFile/upload/p/<ptoken>/...`（SS131 / KF126）
  - **方式 B（新）**：`credentials.{accessKeyId,secretAccessKey,sessionToken}` + `bucket` + `clientRegion` → AWS S3 SigV4 直传（CG625A1 等新摄像头）
- 两者任一齐全即可通过校验；`sess.s3_bucket` 有值时 `step_upload_image_to_storage` / `step_upload_ts_segments` 自动走 S3 分支
- `_upload_complete_body_real.serviceName`：S3 路径下自动填 `"s3"`，老路径依然 `"bxs"`

### 🔐 纯 stdlib AWS SigV4 实现（`_aws_sigv4_put`，~80 行）
- 不引入 `boto3` 依赖，用 `hmac / hashlib / time` 实现 SigV4 v4 签名
- 支持 STS 临时凭证（`x-amz-security-token` 头）
- AWS 中国区 endpoint 后缀自适应（region `cn-*` → `.amazonaws.com.cn`；其他 → `.amazonaws.com`）

### ✅ 零回归验证
- 同一命令 `--brand vicohome --env prod`（不加 `--object-type`）老链路依然 8.38s 秒级入库 ✓
- 改动只在 object-type 分支生效，没有 object-type 的老流程代码路径完全不变

### ⚠️ 已知限制（待后端接通）

**硬编码 tag 链路（vehicle/person/pet/package/motion）**：
- VH prod + CG625A1：跑通前 8 步（S3 真实上传 52KB jpg + 3 段 ts、deviceMsg/pir 成功、uploadComplete 成功），但相册 eventCount 未增加
- 缺少"新设备版 uploadAIImage"，老 `/videoFile/uploadAIImage/p/<ptoken>` 路径对新设备不适用；探测 7 个候选 URL 全 404

**AI 识别链路（bird/small_animal）**：
- KB prod + KF126 上全链路推到 ai-cloud imageInfer 这一步（endpoint 拿到 `https://ai-us-gg.addx.live/ai-cloud/deeplens/stream/imageInfer`），但后端返回 `400 Missing required form data parts`
- 客户端 multipart 格式（`file0` + `json` part）与 prod ai-cloud 当前版本预期不一致；MS 场景里同款 body 格式能在 staging EU 通过，prod 似乎收紧了校验
- **另一新发现**：KB KF126 也走 S3 直传（`a4x-prod-us` bucket / `us-east-1` region），不像 CG625A1 是 CN S3；双路径自适应正常工作

**不影响老链路（不加 --object-type）**：L4 回归造纯相册条目可以直接用，已 E2E 验证 VH/KB/VN prod。

**下一步需要 IoT 后端同学支持**：
1. CG625A1 在 S3 直传后相册物化的正确触发接口
2. prod ai-us-gg.addx.live 当前的 imageInfer multipart 字段要求

---

## 1.7.0 — 2026-04-24

支持按对象类型造 PIR，覆盖 7 种类型：person / pet / vehicle / package / motion / bird / small_animal。

### 🎯 核心能力新增 —— `--object-type`

- 不加 `--object-type` → 走老链路（向后兼容：相册有条目但视频 404，无 AI tag）
- 加 `--object-type {person,pet,vehicle,package,motion}` → 走**硬编码 tag 链路**
  - 复刻 MS 基站项目「pir事件-{vehicle,pet,package}」：从 `/deviceMsg/pir` 响应解析 imageKeyTemplate + sliceKeyTemplate + accessUri → PUT 真实上传 jpg/ts 到 `/videoFile/upload/p/{ptoken}/n/pir/...` → POST `/videoFile/uploadAIImage` 带 `boxes[].name=<type>`（共 3 次，中间那次带坐标）→ `video/uploadComplete` 用真实 sliceList 收尾
  - 相册条目会带 tag（person/pet/vehicle/package），视频详情页可播（真 ts）
- 加 `--object-type {bird,small_animal}` → 走 **AI 识别链路**
  - 复刻 MS 方案项目「生成PIR-鸟」：走硬编码链路同款真实上传 → 额外 POST `/deviceMsg/queryRetainedMsg names=["config"]` 拿 `uploadAIImage.endpoint + aiCloudParam` → POST 到外部 `ai-{env}-{region}.safemo.com/ai-cloud/deeplens/stream/imageInfer`（boxes 空，让后端 AI 模型识别真实鸟图）× 3 → uploadComplete
  - 相册 tag 是否打上取决于后端 AI 模型能否识别，脚本验证时失败仅打 warning 不 fail（`_verify_tag_on_event`）

### 📷 对象图片素材 `scripts/test_images/`（skill 自带）

全部从 MeterSphere 基站项目 / 方案项目拉取：

| 文件 | 来源 | 用途 |
|---|---|---|
| `person.jpg` | 基站项目 `recognition_0personappear_...` | 设备真实抓拍 |
| `pet.jpg` | 基站项目 `image-cat.jpg` | 手机屏摄猫 |
| `vehicle.jpg` | 基站项目 `image-car.jpg` | 手机屏摄车 |
| `package.jpg` | 基站项目 `image_package.jpg` | 包裹特写 |
| `bird_cn_1~3.jpeg` | 方案项目「中国鸟」模块 | 真实鸟类摄影 |
| `bird_us_1.jpeg` | 方案项目「美国鸟」模块 | 备选 |

可通过 `--image <path>` 或 `PIR_IMAGE` 覆盖。

### 🧰 其他新增

- `--device-firmware-preset {cx-cq121c,ss131,kf126}`：uploadComplete 里的 modelNo/firmwareType（默认 cx-cq121c 沿用老脚本）
- Config 新字段：`object_type` / `image_path` / `device_firmware_preset`
- Session 扩展：持久化从 `/deviceMsg/pir` 解析的 `ptoken` / `image_path` / `ts_paths` / `slice_periods`、以及 AI 识别用的 `ai_cloud_endpoint` / `ai_cloud_param` / `user_id`
- `step_verify_gallery` 增 `expected_tag` 参数，相册条目里找到 tag 打 ✅，找不到打 ⚠️（不 fail 执行）
- `bird` 链路前置：`step_check_ai_switches` 查 `aiAssist/queryEventObjectSwitch(includeBird=true)`，检测到 bird 开关关闭只 warning 引导用户去 App 打开（不自动修改用户账号设置）

### 📐 Box 坐标 / 切片时长（直接来自 MS 场景实采值）

| type | name | left, top, right, bottom | score | periods (ms) |
|---|---|---|---|---|
| person | "person" | 0.530, 0.188, 0.917, 1.000 | 0.926 | 3991/2933/2999 |
| pet | "pet" | 0.540, 0.352, 0.794, 0.978 | 0.895 | 3991/3933/2000 |
| vehicle | "vehicle" | 0.450, 0.313, 0.778, 0.702 | 0.816 | 3991/2933/2999 |
| package | "package" | 0.368, 0.291, 0.612, 0.624 | 0.417 | 2000/3866/2000 |
| motion | `{}` 空 | — | — | 3991/2933/2999 |
| bird / small_animal | `[]` | — | — | 3991/2933/2999 |

### ⚠️ 已知限制

- 现有 profile 继续工作，无需迁移（不加 `--object-type` 完全等于 1.6.1 的行为）
- ai-cloud 的 endpoint 从 `deviceMsg/queryRetainedMsg` 运行时拿，不同环境/设备可能差异；若解析失败脚本会报错并提示
- AI 识别链路依赖 bird 识别开关已开 + 设备型号支持 + 图片能被模型识别，3 个条件之一不满足就不会打 tag（脚本不负责保证 100% 成功）
- bird 链路目前只在 staging EU 观察过（MS 场景环境），prod US/EU 的 ai-cloud endpoint 形态待验证

## 1.6.1 — 2026-04-24

初始版本：从 `device-cloud/tools/create_pir_event.py` 迁移并封装成 create-pir skill。

### 🎯 核心能力
- 复刻 MeterSphere 场景 `[自动化] PIR 事件` 的 7 步 API 上报链路（login → wakeupDevice → httpToken → deviceMsg/wakeup → deviceMsg/pir → video/sliceReport → video/uploadComplete）
- Skill 自包含（scripts / references 全部在 skill 目录下，不依赖任何外部仓库）
- 支持 3 品牌 × 2 区域 × 3 环境 = 12 组 preset
- 端到端实测：VicoHome us {staging, prod}、KiwiBit us prod、VicoNature us prod

### 🔒 安全
- 代码里零硬编码凭证（email / password / device / userSn 全空，强制通过 .env / CLI 提供）
- 签名 key 与 测试 S3 URL 可用环境变量覆盖（`PIR_SIGN_SECRET` / `PIR_TEST_VIDEO_URL` / `PIR_TEST_IMAGE_URL`）
- `.pir.env` 强制放在 `~/.config/addx/`，绝不随 plugin 分发泄漏（plugin install 是文件系统拷贝，skill 目录下的 .env 会被复制到其他同事机器）
- `.gitignore` 规则精准：`**/.pir.env` 全局忽略、`.pir.env.example` 可提交
- prod 环境写入前强制二次确认（tty 下 `yes` 输入，Skill 层 AskUserQuestion 确认）

### 📦 依赖 & 分发
- 首次运行自动 `pip install requests`（脚本 try-import 失败时自动 subprocess 装）
- PEP 723 inline metadata 支持 `uv run` 直接执行
- 装 plugin：`claude plugin marketplace add /path/to/skills && claude plugin install addx@addx`

### 🛠 Profile 管理
- `--init-profile NAME`：对话式创建；支持 headless（给 `--email/--password/--device/--brand/--env/--region` 预填则不 prompt）
- `--profile NAME`：加载 `~/.config/addx/pir/<NAME>.env`
- `--list-profiles` / `--delete-profile`
- `--switch-device NAME`：登录 profile 账号 → 列设备 → 交互选新的 → 写回 profile
- `--edit-profile NAME`：交互式改任意字段（brand/region/env/email/password/device/userSn），密码走 getpass
- 所有 profile 文件自动 chmod 600

### 🔍 设备自动发现
- `--list-devices`：列当前账号下所有绑定设备（🟢/🔴/⚪ 在线状态 + 型号 + 名称 + sn）
- `--init-profile` 成功登录后自动调该接口，让用户从列表选编号，不用再手抄 32 位 serialNumber
- 从 MeterSphere API 定义库挖到 `POST /device/listuserdevices/v4`

### 🤖 Skill 层交互协议（SKILL.md）
- **Path A**（有 profile）：AskUserQuestion 按钮式选 profile
- **Path B**（首次使用）：8 步全对话 —— 品牌 → 环境 → 区域 → 邮箱 → 密码 → 列设备 → 选设备 → headless 建 profile → 继续造 PIR
- **🔄 变更协议**：用户说"换设备/换账号/改 profile"时，两层 AskUserQuestion 引导（选 profile → 选动作 → 给对应 CLI 命令）
- **🚫 严禁列表**：不得输出 markdown 选项让用户打字、不得自作主张让用户去终端、不得跳过 prod 二次确认、不得一条消息问多项
- 首次触发零 profile 时，脚本在 tty 友好的情况下直接进入 wizard，不抛错

### ✨ 其它
- `--show-config`（打印生效配置，密码 masked）
- `--show-presets`（列全部 12 组 preset）
- `--save-config PATH`（把当前配置写成 .env）
- `--dry-run`（只读验证登录 + 签名，不写事件）
- `--count N`（批量造 N 条）
- `--no-verify`（跳过相册物化验证）
- `--version`
- 友好错误提示：缺凭证时引导 `--init-profile`；有 profile 但忘 `--profile` 时列出可用的

## v1.11.0 (2026-04-28)

### 🐦 鸟一键造数据

- **新增 --bird-species**：从内置 10 种北美后院鸟图库选物种（或 random），自动设置 object-type=bird + firmware-preset=kf126
- **新增 --bulk N**：同物种重复造 N 条 PIR，让 KB 后端 keyshot 异步链路累积出多张 keyshot
- **新增 --variety N**：一次造 N 个不同鸟种（与 --bulk 配合）
- **新增 --verify-bird-tab**：造完后调 /app/birdTab/showInfo 报告 Bird Tab 卡片状态

### ✨ 其他改进

- **image→静帧视频自动转换**：image-only 链路（仅 --image 不传 --video）+ object_type=bird/small_animal 时，自动用 ffmpeg 把图扩为 13s 静帧 mp4，让 KB app **视频可播放**（之前是 jpg 字节占位会黑屏）
- **修复 step_check_ai_switches 字段映射 bug**：query 接口返回字段是 `eventObject + checked`，之前误用 `name + enable` 导致 bird 开关明明开着也报"未开启"假警告
- **内置鸟类图库**：scripts/test_images/birds/<species>/ 含 10 种北美鸟（来自 Wikimedia Commons CC-BY-SA / 公共领域），开箱即用

### 用法示例

```bash
# 造 5 条 Robin + 自动 verify
python create_pir_event.py --profile <name> --bird-species robin --bulk 5 --verify-bird-tab

# 一次造 5 个鸟种 × 每种 3 条 = 15 条
python create_pir_event.py --profile <name> --variety 5 --bulk 3 --verify-bird-tab
```
