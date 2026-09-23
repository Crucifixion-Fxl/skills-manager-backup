# 故障排查手册

> create-pir 脚本 7 步链路每一步可能的失败模式和定位方法。

## 🎯 快速诊断：先看失败卡在哪一步

脚本日志格式：`[HH:MM:SS] ✅ <步骤名>` / `❌ <错误>`。卡在哪步决定排查方向。

```
step1 ✅ 登录           → step2 ✅ wakeupDevice       → step3 ✅ 获取 device-token
step4 ✅ deviceMsg/wakeup → step5 ✅ /deviceMsg/pir    → step6 ✅ video/sliceReport
step7 ✅ video/uploadComplete → ✅ 相册可见
```

---

## Step 1 — 登录失败

### ❌ `ACCOUNT_NOT_REGISTERED` / `result=-1001`

**根因**：请求 body 里 `app.tenantId` 与账号实际 tenant 不匹配。服务端按 `(email, tenantId)` 联合查表。

**排查**：
1. 确认当前 brand：`--show-config` 看 `app_meta.tenantId`
2. 对 **VicoNature**：确保 tenantId=**vicoo**（不是 nature）—— 脚本 preset 已正确处理，报这个错说明被用户手动覆盖了
3. 对 KiwiBit：确保 tenantId=kiwibit + domain 是 `*.kiwibit.com`
4. 对 VicoHome：tenantId=vicoo + domain 是 `*.vicohome.io`

### ❌ `WRONG_PASSWORD` / `result=-1021`

密码错。检查 `.pir.env` 的 `PIR_PASSWORD` 或命令行 `--password`。

### ❌ HTTP 5xx

后端临时异常，重试 1-2 次。持续失败查 [troubleshooting.addx.live] 的后端状态。

---

## Step 2 — wakeupDevice 失败

通常很少卡这步。如果失败看响应 `msg` 字段，常见：
- `设备不存在`：serialNumber 不属于当前账号
- `设备已下线`：设备被解绑了

---

## Step 3 — 获取 device-token (httpToken) 失败

### ❌ `invalid signature`

**根因**：签名算法不对或 secret 错。

**排查**：
- 确认 `PIR_SIGN_SECRET` env 已设置（脚本不内置默认值）；从 MeterSphere 场景「[自动化] PIR 事件」提取并写入 `~/.config/addx/pir/<profile>.env`
- secret 当前跨环境 / 品牌通用；如果某天这个报错密集出现，后端**换了 secret** —— 重新从 MeterSphere 场景导出更新即可

### ❌ `result=-1` or 4xx

很少发生。看完整响应判断。

---

## Step 4 — deviceMsg/wakeup 失败（⭐ 最常见踩坑点）

### ❌ 响应 `"deviceStatus": -2112`，脚本抛 `响应缺少 data.value.traceId`

**这是 90% 的 KiwiBit / VicoNature 首次使用踩的坑**。

**根因**：device_api 走到了**跨租户的 `api.addx.live`**，该域名无法识别 OEM 品牌设备；JWT payload 里 `tenantId: None`。

**诊断流程**（跑一次判断）：

```python
import os, time, base64, hmac, hashlib, requests, json

SERIAL = "你的设备 serial"
DEVICE_API = "你脚本用的 device_api"  # 看 --show-config

# 1) 拿 device-token
ts = int(time.time())
# secret 从 env / profile 注入；不要内联到代码
secret = base64.b64decode(os.environ["PIR_SIGN_SECRET"])
sig = base64.b64encode(hmac.new(secret, f"{SERIAL}{ts}".encode(), hashlib.sha1).digest()).decode().replace("+","-").replace("/","_")
r = requests.post(f"{DEVICE_API}/deviceMsg/httpToken",
    json={"serialNumber":SERIAL,"signature":sig,"time":ts,"name":"httpToken","id":4,"value":{}},
    timeout=10).json()
tok = r["data"]["value"]["token"]

# 2) 解码 JWT payload
payload = json.loads(base64.urlsafe_b64decode(tok.replace("Bearer ","").split(".")[1] + "==="))
print("tenantId:", payload.get("tenantId"))
```

**结果判断**：
- `tenantId` 是 `vicoo` / `kiwibit` → 域名对，问题在别处
- `tenantId` 是 **`None`** → 域名错，改走 business_api 同域：
  - VicoHome / Nature → `api-us.vicohome.io`（或 staging/pre 版）
  - KiwiBit → `api-us.kiwibit.com`（或 staging/pre 版）

**修法**：脚本 preset 已经正确。如果出现此错，检查：
- 用户是否手动传了 `--device-api api.addx.live` → 删掉
- `.pir.env` 里是否写死了错的 `PIR_DEVICE_API` → 删掉

---

## Step 5 — /deviceMsg/pir 失败

### ❌ `未知异常! 设备序列号找不到！`

和 Step 4 -2112 同根因 —— device_api 错。按 Step 4 处理。

### ❌ response `traceId/serialNumber 回显不一致`

**根因**：请求里传的 traceId / serialNumber，跟响应里的对不上。脚本写了严格校验。

**排查**：极少触发；如果遇到，大概率后端在多实例路由时丢了状态，重试。

---

## Step 6 / 7 — videoSliceReport / videoUploadComplete 失败

脚本 body 是从 MeterSphere 场景里复制的，**videoPath 和 imagePath 用的是 staging 的 S3 假 URL**（`a4x-staging-us-vip-3d.s3.amazonaws.com/test/...`）。

### ❌ `fileSize/videoPath 字段校验失败`

后端可能更新了校验规则。改脚本里 `TEST_IMAGE_URL` / `TEST_VIDEO_URL` 或 uploadComplete body 里的 videoKey / resolution。

---

## 相册轮询失败（最终步骤）

### ⚠️ 等待 90s 相册仍未出现 traceId

**这不一定是失败** —— PIR 已经上报成功（前面 7 步都 ✅，有 trace_id），只是服务端**异步物化延迟**（视频流处理 / 事件合并等背景 worker 有抖动）。

**处理**：
- 过几分钟再手动查相册
- 或加 `--verify-timeout 300`（5 分钟）
- 或 `--no-verify` 跳过验证（L4 批量造数据用）

### 相册里**完全没事件**（eventCount=0）

**根因**：造的事件落在了**设备归属账号**的相册里，不是**你登录的账号**。

举例：用 `tltest1` 账号登录 + 造 `ae73e695...` 设备的 PIR → 相册在 tltest1 账号下可见。如果你改用 `otheruser@x.com` 账号登录同一台 `ae73e695...` 设备造 PIR，事件会落在 `tltest1` 账号下（因为设备归属它），`otheruser` 账号查相册自然空。

**修法**：登录"设备主人"账号查相册。脚本会在日志打出 `adminId`（JWT payload 里），对照那个 userId 去找。

---

## JWT 诊断脚本（可随时用）

上面 Step 4 那段 Python 代码可以独立跑，我推荐**脚本升级后把这段嵌进去**：每次 Step 3 拿到 device-token 后自动解码 JWT，若 `tenantId == None` 立即警告"device-api 域名不匹配 brand"。

---

## 联系人

脚本或 preset 出现新的未知失败模式，把完整脚本输出（含 `result`, `msg`, 响应 JSON）反馈给 device-cloud 维护者。

---

## `--object-type` 专项故障（v1.7.0+）

### 症状：`deviceMsg/pir 响应缺少 imageKeyTemplate / sliceKeyTemplate / accessUri`

这 3 个字段是"对象类型链路"的前置，脚本从 `/deviceMsg/pir` 响应 `data.value.*` 里拿。
**常见原因**：老固件设备响应 schema 不同；或该环境的 `/deviceMsg/pir` 接口版本不一致。
**修法**：短期去掉 `--object-type` 退回老链路；中期用 MS 场景验证过的设备型号（基站 SS131W1 / 喂鸟器 KF126 / CX CQ121C-JS）。

### 症状：鸟类 PIR 造成功但相册里无 bird tag

脚本会打 `⚠️  未在相册条目里看到 'bird' tag`。
**常见原因**（按概率）：
1. tag 晚到（最常见）→ 默认 verify-timeout 太短，AI 异步打 tag 通常 ~30s。加 `--verify-timeout 180` 再试，或单独轮询相册。
2. 账号的 bird 识别开关没开 → App → 设备 → 设置 → AI 检测 → 鸟识别
3. 设备不支持鸟识别 → 只有 KiwiBit KF 系列原生支持；普通 VH 摄像头需要 VIP 开通
4. 图片没被模型识别 → 换一张更清晰的鸟图（或直接用 skill 自带的 `test_images/bird_cn_*.jpeg`）
5. 设备 AI 模型不含此类别（v1.9.1 实测）→ KF126 喂鸟器 `deviceAiEventList=['bird','nuisance_animal']`，传 person/pet/vehicle/package 图片永远不会出对应 tag。要 person tag 必须用普通监控摄像头（CG 系列）。
   *脚本目前不会主动校验设备 AI 模型清单*；如需查清单，登录 App → 设备 → 设置 → AI 检测，看可选开关。

### 症状：`ai-cloud imageInfer 失败 [400]: Missing required form data parts`（v1.9.0 已修，v1.9.1 修复）

**根因**：`json` 段被作为普通 form field 发送，Java 后端的多段解析器只把带 filename 的部分算作 "required form data parts"，form field 不算。
**修法**：升级到 v1.9.1+。如果你还在 1.9.0，检查 `step_ai_cloud_infer` 的 `requests.post(...)` 调用，确保 `json` 段也走 `files=` 而不是 `data=`。

### 症状：`retainedMsg 未返回 ai-cloud endpoint`

`step_query_retained_msg_for_aicloud` 拿不到 `data[0].value.uploadAIImage.endpoint`。
**常见原因**：设备配置禁用了 AI 云推理；或该环境不部署 ai-cloud 服务。
**修法**：换 staging EU 上的 KF 设备；或退回 MeterSphere UI 直接触发场景。

### 症状（v1.10.0 `--video`）：app 端相册条目可见但点视频黑屏

**这是 v1.10.0 之前的所有版本的固有行为** —— "3 段 ts" 实际是封面 jpg 字节，
当 H.264 ts 流喂解码器必败。

**修法**：升级到 v1.10.0+ 并加 `--video <path>`：
```bash
python create_pir_event.py --profile xxx --object-type bird --video bird_a4x_1.mp4
```
脚本会用 ffmpeg 切真 mpegts 上传，KB app 可真播放。

### 症状（v1.10.0 `--video`）：`ffmpeg 切片只生成 N 段，期望 ≥3`

**根因**：视频时长不够 (< 9s)，按 4.3s 切不出 3 段；或视频 keyframe 间隔太大、
`-force_key_frames` 没生效（极少见）。
**修法**：用更长的视频 (≥10s)；或在 ffmpeg 转码时强制 keyframe，参考
`scripts/test_videos/README.md` 的转码命令。

### 症状（v1.10.0 `--video`）：`需要系统装有 ffmpeg / ffprobe`

**修法**：
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- 暂不想装：去掉 `--video` 退回 v1.9.1 行为（视频段是假的，但相册条目和 AI tag 仍可见）

### 症状：`PUT 上传封面失败 [403 / 404]`

`step_upload_image_to_storage` 上传 jpg 到 `/videoFile/upload/p/{ptoken}/n/pir/...` 失败。
**常见原因**：ptoken TTL 过期；accessUri 域名不是 business_api。
**修法**：查日志里 `/deviceMsg/pir` → `上传封面` 的间隔；accessUri 不匹配时后续版本会解析完整 scheme+host 再拼 path。
