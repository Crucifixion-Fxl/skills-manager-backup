---
name: paprika
description: 使用 Paprika（paprika.art，MiniMax-H3）视频生成平台：登录、建项目和 API key、上传素材、文生/图生/参考生视频、轮询下载、参考声音降噪、成本与配额控制。当用户要用 paprika 生成视频、做旁白/口播/双人出镜的 AI 视频，或把脚本拆成 15 秒以内的片段批量生成时使用。不要用它伪造产品真实外观、功能演示或测试数据画面。
---

# Paprika 平台使用指南

## 描述

Paprika 是企业级多模态视频生成平台，模型为 **MiniMax-H3**（`model_minimax_h3_fl2va_prod`）：文生视频、图生视频、首尾帧、
**参考生视频**（图片/视频/音频参考，可保持人物形象和声音特征），成片**自带音轨**。API 基址 `https://api.paprika.art`。
接口、参数、素材限制、价格见 `references/api.md`。

## 脚本（都有 `--help`，结果输出 JSON，进度走 stderr）

```bash
python3 scripts/paprika.py status                     # 余额、项目配额、API key 是否有效
python3 scripts/paprika.py upload REFERENCE_AUDIO=voice.wav REFERENCE_IMAGE=face.png   # 返回 assetId
python3 scripts/paprika.py gen spec.json out.mp4 --dry-run   # 只打印请求体，不提交不扣费
python3 scripts/paprika.py gen spec.json out.mp4             # 提交、轮询、下载
python3 scripts/paprika.py resume task_xxx out.mp4           # 轮询被打断后接着等（不要重新提交）
python3 scripts/paprika.py quota --set 60.00                 # 设置项目总额度
scripts/prep_voice.sh raw.mp4 voice.wav                      # 录音 -> 降噪 -> 声音参考
```

`spec.json`（参考生视频）：

```json
{
  "prompt": "... face must match <Picture 1> ... says, in the voice timbre of <Audio 1>: \"...\" ...",
  "duration": 13, "resolution": "768P", "aspect": "16:9",
  "assets": [
    {"assetId": "media_xxx", "role": "REFERENCE_IMAGE"},
    {"assetId": "media_xxx", "role": "REFERENCE_AUDIO"}
  ]
}
```

上传素材的安全策略（脚本内置）：只接受**普通文件**（拒绝 symlink/FIFO/设备文件）且不超过接口限制（图片 30MB、视频 50MB、音频 15MB）；
服务端返回的直传地址只有 **https 且主机在白名单（`.aliyuncs.com`、`.paprika.art`）**、方法为 PUT/POST 时才直传，并**禁止重定向**，
否则自动改走 API 的服务端上传，本地素材不会被发到非预期端点。

## 执行流程

1. **拆片**：单条只能 5–15 秒。按"一个说话人 + 一段完整台词"拆，说话人切换处断开。时长估算
   `ceil(词数 / 2.0) + 2`，封顶 15；实测语速 2–2.4 词/秒，片尾通常有 1–2 秒静音，剪辑时裁掉。
2. **准备参考素材**：声音用 `prep_voice.sh`（5–15 秒清晰人声）；人脸用**正脸**并裁成只含头颈的特写；
   场景从一条满意的成片里截一帧，让后续片段背景、衣着、光线一致。
3. **上传** → 记下 `assetId` → **写 spec** → 先 `gen --dry-run` → 再 `gen`。互不依赖的片段可并行提交（6 条并行实测可行），
   后台运行时输出重定向到文件，不要接管道（会缓冲）。
4. **检查**：每条抽 3–6 帧拼联系表看画面；用 `ffmpeg -af volumedetect` 按秒看音轨电平，确认人声、停顿和收尾静音。
5. **拼接**：`ffmpeg -i a.mp4 -i b.mp4 -filter_complex "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]" -map "[v]" -map "[a]" -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac out.mp4`

## 规则

### 凭证（只读环境变量，禁止写进仓库 / issue / 聊天 / 日志）

| 变量 | 用途 |
|---|---|
| `PAPRIKA_API_KEY` | 开放 API（生成、查任务），请求头 `Authorization: Key <key>`；key 只在创建时显示一次 |
| `PAPRIKA_PROJECT_ID` | 生成、上传所属项目 |
| `PAPRIKA_USER` / `PAPRIKA_PASSWD` | 控制台登录（上传素材、查余额、改配额、建项目/key），请求头 `Authorization: Bearer <登录 token>` |

脚本**只**读这些环境变量，不读任何凭证文件；需要从文件加载时自行 `set -a; . ./paprika.env; set +a`（该文件不要放进仓库）。
登录 token 缓存在 `${XDG_CACHE_HOME:-~/.cache}/paprika/token`（权限 600，缓存目录 700），脚本不会写入 skill 目录。
写入是原子的（新建私有临时文件再重命名），**不跟随 symlink**；读取时若缓存不是自己所有的普通文件、或对他人可读，就忽略它并重新登录。

### 提示词写法（参考生视频）

- 引用素材用 `<Picture N>` / `<Video N>` / `<Audio N>`，序号**按类型各自从 1 排**，顺序等于 `assets` 里同类型的先后；
  带音轨的参考视频会多占一个 Audio 序号。
- 讲明"谁说话"：`The host (face from <Picture 1>) says, in the voice timbre of <Audio 1>: "台词"`，台词用引号写原文，
  再加 `Lip movements match the words exactly`。
- 只要旁白而不要人出镜：给**声音 + 一张场景图**，写 `unseen off-screen narrator, never the visible person`。
- 干净人声：`Clean dry dialogue only: no music, no background noise, no on-screen text, no subtitles`。
- 要所有人正对镜头：`BOTH people face the camera squarely, in frontal view, for the entire shot`。
- 参考图里的品牌/印字会被复刻：裁到只剩头颈，并写 `plain solid-color tops, no logos, no prints, no text`。

### 踩坑清单

- **参考里的姿态会被继承**：参考脸/场景帧是侧脸，成片里人就一直侧脸；没有真人正脸时，可用已生成成片里的正脸帧。
- **降噪用 RNNoise（`arnndn`），不用 `afftdn`**：风扇底噪录音上 `afftdn` 信噪比 14.4→14.2（几乎无效），`arnndn` 到约 32 dB。
  降噪后用**固定增益**拉响度，不要 `loudnorm`（动态模式会抬高停顿里的噪声）。
- **音频不能单独作参考**，必须配图片或视频。
- **排队时间不定**（1–25 分钟，15 秒 768P 推理约 10 分钟）。轮询超时 ≠ 任务失败：**不要重新提交**（会重复扣费），
  用 `resume <task_id>`；task id 在提交时已打印到 stderr。
- 成片 URL 是签名的，约 10 分钟过期，完成后立刻下载（脚本自动）。
- 上传只能走控制台（Bearer）：预签名直传 → complete；开放 API 的 Key 不能上传。
- 机器上常没有 ffmpeg / pip：`scripts/get_ffmpeg.sh` 在 Linux x86_64 上可取静态版（wheel 与二进制均 sha256 固定，缓存到 `~/.cache/paprika/bin`，**每次使用都重新校验哈希并确认有 `arnndn`**，失败即删除重下），
  其他平台自行安装带 `arnndn` 滤镜的 ffmpeg 并设 `FFMPEG`（Linux/macOS 均可跑 `prep_voice.sh`）。脚本依赖 `python3`、`curl`（下载时还要 `unzip`）。
- **AI 无法听音频**：声音像不像、台词有没有念错，只能人听。红线口径（数字、属地、价格等）务必人工核对成片里实际说出口的话。

### 成本与配额

- 价格（CNY/秒，标准队列）：480P 0.15，768P 0.28；flex 更便宜、priority 更贵（见 `references/api.md`）。
  文生视频 5 秒 768P 实收 1.40，参考生视频 15 秒 768P 为 4.20；提交返回的 `priceQuote.totalAmount` 即该条价格。
- **先用便宜的验证再出正式版**：5 秒 480P 约 0.75，足够验证人脸/声音是否对得上。
- 项目**配额**是消费上限，创建项目时默认 1000，**不要用默认值**，按需设小；调高前先征得用户同意。
- 长片全用 768P 成本很高（13 分钟约 220 以上）：AI 口播适合关键段落，产品实拍、App 录屏、数据画面用真实素材。

### 边界

- 用真人的**脸和声音**做参考，必须是本人或已获本人同意；成片会以其形象说脚本台词，先确认本人知情。
- 参考素材和台词会**上传到第三方服务 Paprika**：人脸/声音属于个人生物特征数据，只用于已获同意的人；
  不要上传客户/用户数据或公司机密内容，并遵守公司隐私与供应商合规要求。
- **不要用 AI 生成产品真实外观、功能演示或测试数据画面**（真机、3D 重建、实测对比）：那不是产品的真实输出，
  与"真实数据"的宣传口径冲突。这类镜头留给真实素材，AI 只做人物口播、氛围空镜。
- 台词逐字取自脚本，不让模型自由发挥。

## Examples

### Bad

```text
用户：把这 13 分钟的脚本全部用 768P 生成出来
AI：直接拆成几十条并行提交（没估价、没验证形象/声音、项目额度用默认 1000）
→ 一次花掉上百元；参考脸是侧脸、声音带风扇底噪，成片全部要重做
```

```text
gen 轮询超时后，AI 用同一份 spec 又执行了一次 gen
→ 第一条其实还在排队，被重复扣费（应该 resume <task_id>，或复用打印出的 --idempotency-key）
```

```text
AI 把"G10 Pro 3D Replay 演示"这类镜头也交给 Paprika 生成，放进宣传片
→ 这不是产品真实输出，违反"真实数据"口径；这类镜头必须用真实素材
```

### Good

```text
用户：把这段脚本做成口播视频
AI：先 status 看余额和额度 → quota --set 设小 → prep_voice.sh 给声音降噪、裁正脸头像
   → gen --dry-run 看请求体 → 5 秒 480P 验证人脸/声音 → 满意后再按片段出 768P 正式版
   → 提交时记下 task id 和 idempotency-key，超时用 resume；成片抽帧 + 按秒看电平后交给人听
```

```text
用户：有一句是"总部在某地"这类红线口径
AI：逐字写进提示词，成片出来后明确告诉用户"我听不了音频，这句请人工核对实际说出口的话"
```

## 新账号一次性初始化

```text
1. POST /v1/auth/login {email, password}                           -> data.accessToken
2. POST /v1/console/projects {name, description, quotaAmount:"30.00"}   -> projectId   (Bearer)
3. POST /v1/console/api-keys {name, projectId}                      -> data.plainKey（只显示一次，立即存入环境变量）
```
