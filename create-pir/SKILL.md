---
name: create-pir
description: 为 VicoHome / KiwiBit / VicoNature 的测试账号造 PIR（人体红外）相册事件，用作 L4 回归、UI 演示、后端联调的脏数据源。通过内置的 scripts/create_pir_event.py 脚本完成 API 上报链路（默认 7 步链路；或加 --object-type {person,pet,vehicle,package,motion,bird,small_animal} 走 18 步硬编码 tag/AI 识别链路）。当用户说"造 PIR"、"造假相册"、"模拟 PIR 事件"、"create pir event"、"L4 回归造数据"、"生成 pir"、"相册里没数据要造"、"VN/KB 造数据"、"造鸟类 PIR"、"造小动物 PIR"、"造人/车/宠物/包裹 PIR"、"带 AI tag 的 PIR"，或用户需要为测试在真实相册产生事件时，使用此 Skill。
---

# create-pir

为 VicoHome / KiwiBit / VicoNature 三个品牌的测试账号在任意环境（staging / pre / prod × us / eu）**造真实 PIR 相册事件**。

本 Skill **自包含**：所需的 Python 脚本、.env 模板、品牌 preset、故障排查手册全部在 skill 目录下，**不依赖任何外部仓库**。

## Description

PIR 事件造数据用在：L4 回归测试、新 UI 冒烟、后端联调验证相册推送链路、演示 demo 的初始数据。

### 脚本位置（Skill 内置）

脚本在 skill 自身目录：`<skill_dir>/scripts/create_pir_event.py`

在对话中通过 Claude Code 调用时，`<skill_dir>` 由 skill 的 bundle path 解析；在命令行直接跑时，需要定位到 skill 目录或使用绝对路径：

```bash
# 从 skill 目录运行
python <skill_dir>/scripts/create_pir_event.py --show-config
```

脚本执行 7 步链路：`login → wakeupDevice → httpToken(签名) → deviceMsg/wakeup → deviceMsg/pir → video/sliceReport → video/uploadComplete → 相册轮询验证`。

### 依赖

脚本只依赖 Python 标准库 + `requests`。**三种安装方式任选**：

```bash
# A) 零动作：首次运行时脚本自动 pip install requests（最常用）
python <skill_dir>/scripts/create_pir_event.py

# B) 用 uv（推荐，脚本有 PEP 723 inline metadata）
uv run <skill_dir>/scripts/create_pir_event.py

# C) 显式安装到当前 Python 环境
pip install -r <skill_dir>/scripts/requirements.txt
```

### 支持的品牌 × 区域 × 环境矩阵

完整矩阵见 [references/brands.md](references/brands.md)。速查：

| 品牌 | 区域 | 已 E2E 验证 |
|------|------|:---:|
| VicoHome | us / eu × staging/pre/prod | us × {staging, prod} ✅ |
| KiwiBit | us × staging/pre/prod | us × prod ✅ |
| VicoNature | us × staging/pre/prod | us × prod ✅ |

## Rules

### 公司特定规则（这些规则 AI 不会通过通用知识知道）

1. **🔴 OEM 品牌的 device_api 必须走业务 API 同域**
   - 错误走法：`api.addx.live`（跨租户网关，对 OEM 设备 JWT `tenantId=None`，wakeup 返回 `deviceStatus=-2112`）
   - 正确走法：
     - VicoHome / VicoNature → `api-us.vicohome.io`（VN 实际 tenantId=vicoo，是 VH 的 OEM 壳）
     - KiwiBit → `api-us.kiwibit.com`（独立租户 kiwibit）
   - 脚本 preset 已按此规律配好，用户**不要手动改 `--device-api` 指向 addx.live**

2. **🔴 VicoNature 的 tenantId 是 `vicoo` 不是 `nature`**
   - OEM 实现：VN 只是 App 壳（bundle=`com.smartaddx.vicohome.nature`），数据和 VH 共用 vicoo 租户
   - 脚本 preset 已处理；如果用户自己写 APP_META 别踩这个坑

3. **🔴 prod 写入前强制二次确认**
   - tty 下会 prompt `输入 yes 执行`
   - 非 tty（被 skill 包起来跑）必须由用户**显式同意**才能去掉 `--dry-run`，不得默认直接 prod 写入

4. **🔴 造数据会污染真相册**
   - 事件会出现在真账号的相册列表（App 可见）
   - 会触发真实推送通知到用户手机
   - 视频详情页点开会 404（videoPath 用的是 staging 假 S3 URL）
   - **造完必须告诉用户 trace_id 列表**，便于后续清理

5. **🔴 签名 secret 必须从 env / profile 注入**
   - 算法：HMAC-SHA1(secret, serial+time_sec)，base64
   - 凭证从公司 MeterSphere 场景「[自动化] PIR 事件」提取，**不要**写进 SKILL/README/脚本
   - 若签名失败先怀疑 **device_api 域名选错**，再确认 `PIR_SIGN_SECRET` 已设置

### 环境变量 / .env

**推荐：profile 文件**，存放在 `~/.config/addx/pir/<name>.env`（chmod 600），用 `--profile <name>` 加载。
绝**不要**把 `.pir.env` 放到 skill 目录内（会被 plugin install 复制泄漏）。

脚本按下列顺序自动查找（越靠前优先级越高）：

```
1. ~/.config/addx/pir/<name>.env  ← profile（推荐，多账号切换；--profile 指定）
2. ~/.config/addx/pir.env          ← 全局单文件（兼容老配置）
3. ./.pir.env                       ← 当前目录（开发临时用，不要提交）
4. ./tools/.pir.env                 ← 当前目录 tools/（兼容老仓库布局）
```

`.env` 格式（参考 `scripts/.pir.env.example`）：

```
# 必填
PIR_BRAND=vicohome|kiwibit|viconature
PIR_REGION=us|eu
PIR_ENV=staging|pre|prod
PIR_EMAIL=your@email.com
PIR_PASSWORD=xxx
PIR_DEVICE=<serialNumber>
PIR_USER_SN=<userSn>
PIR_SIGN_SECRET=<base64>      # 设备签名密钥（必填，从 MeterSphere 场景「[自动化] PIR 事件」提取）

# 可选：preset 不匹配时手动覆盖域名
# PIR_BUSINESS_API=...
# PIR_DEVICE_API=...

# 可选：换 prod 真实可播视频 URL
# PIR_TEST_IMAGE_URL=...
# PIR_TEST_VIDEO_URL=...
```

**安全建议**：用 profile 形式 `~/.config/addx/pir/<name>.env`（脚本自动 chmod 600）。**不要**把 `.pir.env` 放到 skill 目录里 —— plugin 分发会把它一起复制给其他同事。

### 推送链路验证

需要验证 Bird 等真实推送时，优先使用 `--device-auth-only`。此模式只用
`PIR_SIGN_SECRET` 换取设备 token，跳过 `/account/login`，不改写同 bundle 下的推送注册和语言。
设备鉴权模式不轮询账号相册或 Bird Tab，执行后必须通过 `trace_id` 分别核对识别、候选消息和最终投递。

```bash
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> --device-auth-only --user-sn <numeric-user-id> \
  --bird-species robin --bulk 1
```

如果已安全取得当前 App token，也可通过进程环境 `PIR_APP_TOKEN` 复用 App 会话。
App token 只做单次运行注入，不写入 profile、不打印、不提交到仓库。

## 🤖 首次触发时的引导协议（强制遵守，不得变通）

**当用户触发此 skill 时**，你（Claude）必须先做以下判断和动作：

### 判断顺序

1. **先跑这条命令**检查是否有可用 profile：
   ```bash
   python <skill_dir>/scripts/create_pir_event.py --list-profiles
   ```
2. 如果有现成 profile → 进入 **Path A**
3. 如果没有 profile → 进入 **Path B**
4. 账号/设备选定后，**无论 A / B 都必须走 `Path OT：对象类型选择协议`** 再跑造数据。不走 OT = 跑了老链路相册条目无 AI tag，多数时候不是用户想要的。

---

### Path A：有现成 profile

**调用 `AskUserQuestion` 工具**让用户选一个 profile。**不要**输出 markdown 选项列表让用户打字。

```
AskUserQuestion({
  questions: [{
    question: "你要用哪个 profile？",
    header: "Profile",
    multiSelect: false,
    options: [
      { label: "<profile-name-1>", description: "<brand/env/email 的一句描述>" },
      { label: "<profile-name-2>", description: "..." },
      ...
    ]
  }]
})
```

（label 就是每个 profile 的名字，description 从 `--profile X --show-config` 读 brand/env/email 拼）

**选完 profile 后，跳到 `Path OT：对象类型选择协议`**（在下文），不要直接跑造数据。

---

### Path B：没有 profile（首次使用）

**必须**按顺序调用 3 次 `AskUserQuestion` 工具问品牌/环境/区域。**不要**把这 3 个问题合并成一段让用户回复，**不要**用 markdown 列选项让用户打字。

#### B.1 先调 AskUserQuestion 问品牌：

```
AskUserQuestion({
  questions: [{
    question: "你要往哪个 App 的账号造 PIR？",
    header: "品牌",
    multiSelect: false,
    options: [
      { label: "vicohome",   description: "VicoHome 主品牌（com.smartaddx.vicohome / vicoo 租户）" },
      { label: "kiwibit",    description: "KiwiBit 独立 OEM（KF 系列摄像头 / kiwibit 租户）" },
      { label: "viconature", description: "VicoNature OEM 壳（与 VH 共享账号和设备）" }
    ]
  }]
})
```

#### B.2 拿到品牌答案后，再调 AskUserQuestion 问环境：

```
AskUserQuestion({
  questions: [{
    question: "在哪个环境造？",
    header: "环境",
    multiSelect: false,
    options: [
      { label: "prod",    description: "⚠️ 生产环境 - 真实数据、触发推送、污染相册" },
      { label: "pre",     description: "pre-release，准生产，数据配置接近 prod" },
      { label: "staging", description: "测试环境，数据隔离不影响真实用户" }
    ]
  }]
})
```

#### B.3 拿到环境答案后，再调 AskUserQuestion 问区域：

- 如果品牌是 `kiwibit` 或 `viconature` → **跳过此步**，直接用 `us`（这两个品牌只有 us）
- 如果品牌是 `vicohome` → 调 AskUserQuestion：

```
AskUserQuestion({
  questions: [{
    question: "哪个区域？",
    header: "区域",
    multiSelect: false,
    options: [
      { label: "us", description: "美区（最常用）" },
      { label: "eu", description: "欧区" }
    ]
  }]
})
```

#### B.4 选完三项后，用 AskUserQuestion 问账号邮箱

**必须用 `AskUserQuestion`，不要用文本问**。question 留给 Other 自由输入：

```
AskUserQuestion({
  questions: [{
    question: "你的测试账号邮箱？",
    header: "邮箱",
    multiSelect: false,
    options: [
      { label: "其他", description: "在输入框里填你的邮箱（会用 Other 自由输入）" }
    ]
  }]
})
```

用户会在 AskUserQuestion 的 "Other" 输入框里打邮箱。

#### B.5 用 AskUserQuestion 问密码

同样**必须用 AskUserQuestion**，让用户在 Other 框里输入：

```
AskUserQuestion({
  questions: [{
    question: "请输入密码（注意：密码会经过此对话，LLM 能看到。如确认这是公司内测账号可继续）",
    header: "密码",
    multiSelect: false,
    options: [
      { label: "其他", description: "在输入框里填密码" }
    ]
  }]
})
```

#### B.6 拿到邮箱密码后 Bash 跑 `--list-devices` 拿设备列表

```bash
python <skill_dir>/scripts/create_pir_event.py \
  --brand <brand> --region <region> --env <env> \
  --email <email> --password <password> \
  --list-devices
```

#### B.7 解析 stdout 的设备列表，调 AskUserQuestion 让用户按钮选

```
AskUserQuestion({
  questions: [{
    question: "选一台设备造 PIR：",
    header: "设备",
    multiSelect: false,
    options: [
      { label: "<sn1>", description: "🟢 <modelNo> <deviceName>" },
      { label: "<sn2>", description: "..." },
      ...
    ]
  }]
})
```

#### B.8 全部拿到后，Bash **headless** 创建 profile（一条命令，不对话）

```bash
python <skill_dir>/scripts/create_pir_event.py \
  --init-profile <name> \
  --brand <brand> --region <region> --env <env> \
  --email <email> --password <password> \
  --device <sn>
```

email + password + device 都在参数里 → 脚本自动 headless 不 prompt 任何 input。profile 写入 `~/.config/addx/pir/<name>.env` (chmod 600)。

### ⛔ 绝对禁止在 Path B 里让用户去终端

**不许**输出类似"请在你的终端跑 ... --init-profile"的文案。用户明确要求**全程在对话里完成**。

如果用户**主动**说"我想去终端输密码更安全"，才回退到终端模式，否则默认全对话。

#### B.9 profile 配好后

1. 跑 `Bash: ... --list-profiles` 确认新 profile 存在
2. 跑 `Bash: ... --profile <name> --show-config` 把生效配置展示给用户
3. **跳到 `Path OT：对象类型选择协议`**（先决定造什么类型，再考虑 prod 确认）
4. 如果 env 是 **prod**，调 `AskUserQuestion` 做二次确认：

```
AskUserQuestion({
  questions: [{
    question: "确认要在 prod 环境给 <email> 账号造 <count> 条 <对象类型> PIR 吗？",
    header: "Prod 确认",
    multiSelect: false,
    options: [
      { label: "确认执行", description: "会产生真实相册记录和推送" },
      { label: "取消",     description: "不执行" }
    ]
  }]
})
```

5. 确认通过后按 OT 协议产出的完整命令跑（带 `--object-type` / `--device-firmware-preset` / `--image`），把脚本 JSON 结果里的 `trace_id` 和 tag 验证结果报给用户

---

---

## 🔄 变更已有 profile 协议（换账号 / 换设备）

当用户说 **"换设备"、"换账号"、"改 profile"、"edit profile"、"切换设备"、"更新账号"** 等时，Claude 按以下流程响应：

### Step 1：先列 profile 让用户选

```bash
python <skill_dir>/scripts/create_pir_event.py --list-profiles
```

### Step 2：调 AskUserQuestion 让用户选要改的 profile

```
AskUserQuestion({
  questions: [{
    question: "要改哪个 profile？",
    header: "Profile",
    options: [
      { label: "<p1>", description: "（用 --profile <p1> --show-config 读 brand/env/email 作 hint）" },
      { label: "<p2>", description: "..." }
    ]
  }]
})
```

### Step 3：再问改什么（AskUserQuestion）

```
AskUserQuestion({
  questions: [{
    question: "你想改什么？",
    header: "动作",
    options: [
      { label: "换设备",   description: "保留账号，换成账号下的另一台摄像头" },
      { label: "换账号",   description: "保留品牌/环境，换成另一个测试账号" },
      { label: "改密码",   description: "账号不变，只更新密码" },
      { label: "改其他字段", description: "手工改任意字段（brand / env / region / userSn 等）" }
    ]
  }]
})
```

### Step 4：按用户选项执行

- **「换设备」** → 让用户在终端跑 `... --switch-device <profile>` （脚本会自动登录 + 列设备 + 让选 + 写回）。或者 Claude 自己在对话里也可以：先 `--list-devices`（用 profile 的凭证）、AskUserQuestion 列设备让用户按按钮选、拿到新 sn 后用 `--edit-profile` 的脚本把 PIR_DEVICE 写回。
- **「换账号」** → 推荐不改旧 profile，而是 `--init-profile <新名字>` 建一个新的（保留历史）。如果用户坚持原地改，让他跑 `... --edit-profile <profile>`。
- **「改密码」** → 让用户在终端跑 `... --edit-profile <profile>`（密码走 getpass 不回显）。
- **「改其他字段」** → 让用户在终端跑 `... --edit-profile <profile>`。

### Step 5：改完后用 `--show-config --profile <name>` 核对

把结果贴给用户确认。

---

## 🎯 Path OT：对象类型选择协议（v1.7.0+，造 PIR 前必做）

Path A / B 决定完账号+设备后，**必须**走 OT 协议决定造什么类型的 PIR，再真跑。

### OT.0 先看用户原话里有没有明示类型

只要用户在触发句里已经说清楚要造哪类（"造鸟 PIR"、"人"、"车辆"、"小动物"、"包裹"、"motion"、"people" 等），**直接跳过 OT.1**，在确认里把类型写清楚让用户默认通过即可。**不要**为了"流程完整"再问一遍。

映射表（用户说 → `--object-type`）：

| 用户措辞 | 映射 |
|---|---|
| 人 / person / 人体 / people / 人员 | `person` |
| 宠物 / pet / 猫 / 狗 / cat / dog | `pet` |
| 车 / 车辆 / vehicle / car | `vehicle` |
| 包裹 / package / 快递 / 外卖 | `package` |
| 运动 / motion / 空 PIR / 无对象 | `motion` |
| 鸟 / bird / 喂鸟器 | `bird` |
| 小动物 / small animal / wildlife | `small_animal` |
| 未指定 / 随便 / 造一条 / test | → 走 OT.1 问 |

### OT.1 用户没指定 → AskUserQuestion 让他按钮选

```
AskUserQuestion({
  questions: [{
    question: "你想造哪种对象类型的 PIR？",
    header: "对象类型",
    multiSelect: false,
    options: [
      { label: "person",       description: "人 —— 客户端硬编码 tag，相册一定带 person tag" },
      { label: "pet",          description: "宠物 —— 硬编码 tag（猫/狗都算）" },
      { label: "vehicle",      description: "车辆 —— 硬编码 tag" },
      { label: "package",      description: "包裹 —— 硬编码 tag" },
      { label: "motion",       description: "运动（无具体对象）—— boxes 空" },
      { label: "bird",         description: "🐦 鸟 —— 调 ai-cloud 真识别，需要喂鸟器设备 + 鸟识别开关开" },
      { label: "small_animal", description: "小动物 —— 同 bird，AI 识别机制" },
      { label: "不带 tag",     description: "走老链路（仅造空相册条目，视频 404，无 AI tag）" }
    ]
  }]
})
```

选"不带 tag" → 跑命令时**不加** `--object-type`（走老链路）。

### OT.2 如果用户选的是 `bird` 或 `small_animal`

**先检查设备是否适合**，然后警告用户前置条件：

1. 用 profile 的 `serial_number` 比对 `DEVICE_FIRMWARE_PRESETS`：
   - serial 开头不像 KF 喂鸟器 → 在后续提示里**提醒用户**"当前 profile 设备不是 KF 喂鸟器系列，AI 模型很可能识别不出鸟，相册条目会产生但不一定带 bird tag"
2. 用 AskUserQuestion 让用户选 `--device-firmware-preset`：

```
AskUserQuestion({
  questions: [{
    question: "这台设备的固件 preset 是？（影响 uploadComplete 里的 modelNo/firmwareType）",
    header: "设备固件",
    multiSelect: false,
    options: [
      { label: "kf126",     description: "🐦 KiwiBit 喂鸟器（bird 最稳）" },
      { label: "ss131",     description: "基站设备 SS131W1（MeterSphere 默认）" },
      { label: "cx-cq121c", description: "CX CQ121C-JS（脚本老默认，普通摄像头）" }
    ]
  }]
})
```

3. 打 warning：**bird 识别开关必须在 App 里手动打开**；脚本只做 query 不自动改用户账号设置。

### OT.3 图片选择

默认用 skill 自带的 `scripts/test_images/<object_type>.jpg`，**不要每次都问**。

仅当以下两个条件都满足时才问用户是否换图：
1. 用户原话里提到"自己的图"、"用 XX 图"、"custom image"、"我有一张" 等
2. 或用户造 bird 连续两次失败（tag 验证打 ⚠️）

询问方式：

```
AskUserQuestion({
  questions: [{
    question: "用哪张图？",
    header: "图片",
    multiSelect: false,
    options: [
      { label: "默认（skill 自带）",  description: "用 scripts/test_images/<type>.jpg" },
      { label: "其他",               description: "在 Other 输入框填本地图片的绝对路径" }
    ]
  }]
})
```

### OT.4 造出完整命令

拿到 `--object-type`（可能为空）+ 可选 `--device-firmware-preset` + 可选 `--image` 后，拼接命令：

```bash
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> \
  [--object-type <type>] \
  [--device-firmware-preset <preset>] \
  [--image <path>] \
  [--video <mp4-path-or-test_videos-name>] \
  [--ai-location-ip <staging-public-ip>] \
  [--ai-country-no <two-letter-country>] \
  [--count <n>]
```

`--video`（v1.10.0+）：让相册条目的视频段可真播放。需要 ffmpeg。`--image` 同时存在时被忽略
（封面用视频首帧，保证封面 = 视频内容的语义一致）。

#### 🐦 v1.11.0 鸟一键造数据（推荐）

用户只需指定鸟种（或随机），skill 自动从内置 10 种北美后院鸟图库选图、自动生成静帧视频（视频可播）、自动设置 firmware preset=kf126、自动 bulk 多条让 keyshot 累积，最后自动验证 Bird Tab：

```bash
# 造 5 条 American Robin PIR + 自动验证 Bird Tab
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> \
  --bird-species robin \
  --bulk 5 \
  --verify-bird-tab

# 随机造一只鸟
--bird-species random --bulk 5

# 一次造 5 个不同鸟种 × 每种 3 条 = 15 条
--variety 5 --bulk 3 --verify-bird-tab
```

**内置图库 10 种**（位于 `scripts/test_images/birds/`）：
- robin / cardinal / chickadee / house_finch / sparrow（已 prod 验证识别成功）
- goldfinch / blue_jay / bluebird / titmouse / mourning_dove

**自动行为**：
- `--bird-species` 给出时，自动 `--object-type bird --device-firmware-preset kf126`
- image-only 时（无 --video）自动 ffmpeg 把图扩为 13s 静帧 mp4 → 切 3 段 ts → 视频可播
- `--verify-bird-tab` 调 `/app/birdTab/showInfo` 报告每张卡片的 `visits + keyShotCount`

**staging 地理门禁验证（v1.12.0+）**：

当需要验证 Video ID Coach 等依赖设备州/省信息的 AI 链路时，增加
`--ai-location-ip <PUBLIC_IP>`。脚本会调用 `/deviceMsg/config` 获取一份由 IOT 按该
IP 生成的 `aiCloudParam`，避免复用缺少 `location` 的 retained config。
如果测试账号国家与目标 IP 国家不一致，再增加 `--ai-country-no <CC>`。

- 仅允许 staging，禁止 pre/prod。
- 手动 `--device-api` 覆盖也必须使用 HTTPS，并命中脚本已知的 staging preset 域名白名单。
- 仅允许 `bird` / `small_animal`。
- `--ai-country-no` 必须与 `--ai-location-ip` 同时使用，且只接受两位 ASCII 国家码。
- 只读取新生成的响应，不发布、不覆盖设备 retained config。
- 国家码只覆盖本次事件内存中的 Protobuf，不修改账号、设备或数据库。
- 这是后端联调能力，不用于伪造线上用户地理位置。
- 必须换用未重复上报的素材验证 coaching；重复图片/视频可能被 AI 链路去重。

**Keyshot 异步特性**：
- birdStdName / birdImageUrl / 卡片：30 秒内出现
- keyShotCount：异步 AI 抽取，5-30 分钟出 1-3 张（同种 PIR 越多越快越多）

**回到 Path A/B 的 prod 二次确认**，然后真跑。

### OT.5 执行后回报结果

脚本 stdout JSON 里有：
- `trace_id`：事件 id
- `object_type`：最终类型
- `gallery_visible`：相册是否可见；设备鉴权模式返回 `null`，表示未执行账号相册验证
- 日志里的 `✅ tag 匹配 'bird'` 或 `⚠️ 未在相册条目里看到 'bird' tag`

把这三项**简洁**报给用户。bird tag 未命中时**不要**失败告终 —— 告诉用户可能原因（识别开关 / 设备型号 / 图片质量 / AI 延迟）。

---

## 🚫 严禁的替代行为

以下行为**绝对不允许**：

- ❌ 用 markdown 列表（"请从 A/B/C 选一个"）让用户打字回复
- ❌ 一条消息里把品牌+环境+区域+账号+密码全问出来
- ❌ **让用户去终端跑 `--init-profile` 输账号密码**（除非用户主动要求）—— Path B 默认全对话 AskUserQuestion 路径，不是终端
- ❌ 用"安全考虑"的理由自作主张改用终端模式。密码经过 LLM 是已知的 trade-off，公司内测账号可接受，用户知道并明确要求对话路径
- ❌ 建议用户"自己去看帮助 `--help`"
- ❌ 跳过 prod 二次 AskUserQuestion 确认就造数据
- ❌ **跳过 Path OT 直接跑造数据**（默认老链路打不出 AI tag，用户多数要的是带 tag 的 PIR）
- ❌ 用户说"造鸟 PIR"时还要问"什么对象类型" —— 用户已明示，应直接用 `--object-type bird`
- ❌ bird tag 没命中就报"失败" —— 相册条目已产生，AI 识别是概率事件，按 OT.5 给出解释而不是 fail

**原则**：Path B 全程 Claude 主动收集信息（AskUserQuestion + Bash）然后 headless 写 profile，用户不需要离开对话。Path OT 尊重用户原话，已经明示就别重复问。

---

## 操作指引

### 首次使用（新机器）

```bash
# 1. 用 profile 模式对话式建配置（chmod 600 自动处理；存到 ~/.config/addx/pir/<name>.env）
python <skill_dir>/scripts/create_pir_event.py --init-profile my-vh

# 2. dry-run 验证凭证（依赖首次自动装；只登录+签名，不写事件）
python <skill_dir>/scripts/create_pir_event.py --profile my-vh --dry-run --no-interactive

# 3. 正式推一条
python <skill_dir>/scripts/create_pir_event.py --profile my-vh
```

### 默认流程（最常见）

1. **确认当前配置**：先跑 `--show-config --no-interactive` 看生效的 brand/env/account/device。
2. **prod 操作：先 dry-run**：`--dry-run` 只跑前 3 步（登录+wakeupDevice+httpToken），不写事件；验证凭证/域名对齐。
3. **正式写入**：去掉 `--dry-run`；脚本全链路 7 步约 6~10 秒。
4. **报告 trace_id**：脚本 stdout 的 JSON 里 `results[].trace_id` 就是事件 id，要告诉用户。
5. **引导查看**：在对应 App 登录该账号 → 相册 → 找今天刚新增的那条（按时间排序）。

### 切换品牌

```bash
# VicoHome (default)
python <skill_dir>/scripts/create_pir_event.py

# KiwiBit prod
python <skill_dir>/scripts/create_pir_event.py \
  --brand kiwibit --env prod \
  --email <kb-账号> --password <> --device <kb-sn>

# VicoNature prod
python <skill_dir>/scripts/create_pir_event.py \
  --brand viconature --env prod \
  --email <vn-账号> --password <> --device <vn-sn>
```

### 批量

```bash
# 10 条，不等相册物化，适合快速造量
python <skill_dir>/scripts/create_pir_event.py --count 10 --no-verify

# 静默输出（只吐 JSON），便于 xargs / jq 处理
python <skill_dir>/scripts/create_pir_event.py --quiet --count 5 --no-verify \
  | jq -r '.results[].trace_id'
```

### 查看所有支持组合

```bash
python <skill_dir>/scripts/create_pir_event.py --show-presets
```

### 按对象类型造 PIR（v1.7.0+）

默认不加 `--object-type` 走老链路（相册条目但视频 404，无 AI tag）。加 `--object-type` 能产出真实带 tag 的相册事件：

| 类型 | `--object-type` | 机制 | 默认图片 |
|------|----|----|----|
| 人 | `person` | 客户端硬编码 `boxes[].name="person"` | scripts/test_images/person.jpg |
| 宠物 | `pet` | 硬编码 `"pet"` | pet.jpg |
| 车辆 | `vehicle` | 硬编码 `"vehicle"` | vehicle.jpg |
| 包裹 | `package` | 硬编码 `"package"` | package.jpg |
| 运动 | `motion` | `boxes:[{}]` 空 box | person.jpg |
| **鸟类** | `bird` | boxes 空 + 调外部 ai-cloud 让模型识别 | bird_cn_1.jpeg |
| **小动物** | `small_animal` | 同 bird | bird_cn_1.jpeg |

```bash
# 造一条车辆 PIR（硬编码 tag，相册条目带 vehicle tag）
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> --object-type vehicle

# 造一条鸟 PIR（走 ai-cloud 真实识别）
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> --object-type bird

# 用自己的图替代默认
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> --object-type bird --image ~/pics/my_sparrow.jpg

# 基站 / 喂鸟器设备要换固件 preset
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> --object-type bird --device-firmware-preset kf126

# v1.10.0：加 --video 让 KB app 可真播放视频段（不再黑屏）
# 视频首帧自动当封面，--image 同时存在时被忽略
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> --object-type bird --video bird_a4x_1.mp4
# 也可指向任意本地 mp4
python <skill_dir>/scripts/create_pir_event.py \
  --profile <name> --object-type bird --video /Users/me/Downloads/my_bird.mp4

# staging：让 IOT 按测试公网 IP 生成带 location 的 aiCloudParam
python <skill_dir>/scripts/create_pir_event.py \
  --profile <staging-profile> --bird-species cardinal \
  --ai-location-ip <US_PUBLIC_IP> --ai-country-no US
```

**`--video` 注意**：
- 需要系统装 `ffmpeg` + `ffprobe`（macOS `brew install ffmpeg` / Ubuntu `sudo apt install ffmpeg`）
- 视频时长必须 ≥ 9 秒（脚本切 3 段 ~4.3s mpegts，<9s 切不出 3 段会立即报错）
- skill 自带 `scripts/test_videos/bird_a4x_1.mp4`（1.1MB / 480p / 12.96s）开箱即用
- 不传 `--video` 时所有现有行为保持，相册条目可见但视频段是封面 jpg 字节，KB app 点视频会黑屏

**bird 链路注意**：
- 需要目标账号 **bird 识别开关已打开**（脚本只 warning 不自动改）
- 需要 **设备型号支持鸟识别**（KF 喂鸟器系列最稳）
- 需要 **图片能被后端 AI 模型识别**（AI 失败则不打 tag，相册条目仍产生）
- prod 上的 ai-cloud endpoint 从 `deviceMsg/queryRetainedMsg` 动态拿；若设备返回空 endpoint，脚本会报错
- staging 地理门禁联调可用 `--ai-location-ip`，必要时配合 `--ai-country-no`；
  默认仍读 retained config
- 验证 coaching 时使用未重复上报的素材，避免 AI 去重造成假阴性

## 故障排查

详细故障树见 [references/troubleshooting.md](references/troubleshooting.md)。关键速查：

| 错误 | 典型根因 | 修法 |
|------|---------|------|
| `ACCOUNT_NOT_REGISTERED` | tenantId 走错 | VN 用 `--brand viconature`，脚本会自动填 tenantId=vicoo |
| `WRONG_PASSWORD` | 账号密码错 | 检查 `.pir.env` 里 PIR_PASSWORD |
| `deviceStatus=-2112` / `设备序列号找不到` | device_api 走错，JWT tenantId=None | 换到业务同域（不要用 api.addx.live）；或用 [references/troubleshooting.md](references/troubleshooting.md) 里的 JWT 诊断流程 |
| 相册 90s 未出现 | 服务端异步物化延迟（不是失败） | PIR 已上报成功（有 trace_id），只是相册物化慢；加 `--verify-timeout 180` 或 `--no-verify` |
| HTTP 5xx | 后端临时异常 | 重试 1~2 次 |

## Examples

### ❌ Bad：把 device_api 手动覆盖成 `api.addx.live`

```bash
# 错：OEM 品牌（KB / VN）会拿到 JWT tenantId=None，wakeup 必 -2112
python <skill_dir>/scripts/create_pir_event.py \
  --brand kiwibit --env prod \
  --device-api https://api.addx.live \
  ...
```

### ✅ Good：让脚本按 preset 自动选同域 device_api

```bash
# 对：preset 会把 device_api = business_api（api-us.kiwibit.com）
python <skill_dir>/scripts/create_pir_event.py \
  --brand kiwibit --env prod \
  --email <kb-账号> --password <> --device <kb-sn>
```

### ❌ Bad：VicoNature 传 tenantId=nature

```bash
# 错：VN 是 OEM 壳，实际 tenantId=vicoo；传 nature 服务端会 ACCOUNT_NOT_REGISTERED
python <skill_dir>/scripts/create_pir_event.py \
  --business-api https://api-us.vicohome.io \
  --device-api   https://api-us.vicohome.io \
  --env prod --email <> --password <> --device <>
# 同时自己构造了 app_meta 里 tenantId=nature
```

### ✅ Good：直接用 --brand viconature

```bash
# 对：preset 自动填 tenantId=vicoo + 正确的 APP_META
python <skill_dir>/scripts/create_pir_event.py \
  --brand viconature --env prod \
  --email <> --password <> --device <>
```

### ❌ Bad：prod 环境直接写入不做二次确认

```bash
# 错：未 dry-run，未 yes 确认，就直接批量 100 条
python <skill_dir>/scripts/create_pir_event.py --env prod --count 100
```

### ✅ Good：先 dry-run 验证，再小批量

```bash
# 对：先确认凭证/域名对齐
python <skill_dir>/scripts/create_pir_event.py --env prod --dry-run
# 然后单条验证相册可见性
python <skill_dir>/scripts/create_pir_event.py --env prod
# 确认 OK 再批量
python <skill_dir>/scripts/create_pir_event.py --env prod --count 50 --no-verify
```

## 不在范围内

- 不自动给 prod 写入"授权"（prod 每次必须用户明确批）
- 不代为新建账号 / 绑定设备（环境管理另事）
- 不修复视频 URL 404 问题（要能播放要替换成真实 prod S3 URL）
- 不做清理（目前没有 `--cleanup` 子命令，未来版本可能加）

## References

- [references/brands.md](references/brands.md) — 三品牌 × 区域 × 环境 速查表 + 账号 / 设备登记
- [references/troubleshooting.md](references/troubleshooting.md) — 故障树 + JWT tenantId 诊断流程
- [references/scenarios.md](references/scenarios.md) — 常见使用场景的完整示例对话
- `scripts/create_pir_event.py` — 脚本本体（skill 自包含）
- `scripts/.pir.env.example` — .env 模板
- `scripts/requirements.txt` — Python 依赖
