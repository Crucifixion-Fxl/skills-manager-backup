# Paprika API 参考

基址 `https://api.paprika.art`（来自前端 `runtime-config.js` 的 `publicApiBaseURL`）。
响应统一为 `{requestId, data, error}`，成功看 `data`，失败看 `error.code / error.message`。

## 两套鉴权

| 用途 | 头 | 获取 |
|---|---|---|
| 控制台（项目、key、上传、账单、任务列表） | `Authorization: Bearer <accessToken>` | `POST /v1/auth/login {email,password}`，`/v1/auth/refresh` 续期 |
| 开放 API（生成、查任务） | `Authorization: Key <plainKey>` | `POST /v1/console/api-keys`，`plainKey` 只显示一次 |

账号角色需要权限：`project:manage`、`apikey:manage`、`generation:create`、`billing:manage`。

## 控制台接口（Bearer）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/auth/me` | 当前用户、角色、权限 |
| GET/POST | `/v1/console/projects` | 列表 / 创建 `{name, description, quotaAmount}` |
| PATCH | `/v1/console/projects/{id}/quota` | `{quotaAmount:"60.00"}` 设置**总额度**（消费上限，不扣款） |
| PATCH | `/v1/console/projects/{id}/status` | `{status}` 暂停/恢复 |
| GET/POST | `/v1/console/api-keys` | 列表（masked）/ 创建 `{name, projectId}` → `plainKey` |
| POST | `/v1/console/api-keys/{keyId}/revoke` | 撤销 |
| GET | `/v1/console/billing/account` | `availableBalance`、`frozenBalance`、`currency` |
| GET | `/v1/console/models` | 模型列表及每种能力的 `inputPolicy`、价格 |
| GET | `/v1/console/tasks?page=1&pageSize=5` | 最近任务及状态 |
| POST | `/v1/console/uploads` | 申请上传 `{projectId,fileName,mimeType,sizeBytes,kind,role}` → `assetId,uploadUrl,uploadMethod,uploadHeaders` |
| PUT | `uploadUrl`（预签名） | 直传文件；失败回退 `PUT /v1/console/uploads/{assetId}/content?projectId=` |
| POST | `/v1/console/uploads/{assetId}/complete` | `{projectId}` → 素材元数据（`durationMs`、`width`、`height`…），状态 `READY` |

`kind`: `IMAGE|VIDEO|AUDIO`；`role`: `REFERENCE_IMAGE|REFERENCE_VIDEO|REFERENCE_AUDIO|FIRST_FRAME|LAST_FRAME|SOURCE_IMAGE`。

## 生成：通用接口（assetId 引用，脚本用这个）

`POST /v1/open/generations`（Key；必须带 `Idempotency-Key`）

```json
{
  "projectId": "prj_...", "modelId": "model_minimax_h3_fl2va_prod",
  "capabilityType": "REFERENCE_TO_VIDEO", "taskMode": "OMNI_REFERENCE",
  "prompt": "...", "duration": 13, "resolution": "768P", "aspectRatio": "16:9",
  "hasAudio": true, "quantity": 1, "storeIo": true,
  "inputAssets": [{"assetId": "media_...", "role": "REFERENCE_IMAGE"}]
}
```

- 202 返回 `data.taskId`、`data.priceQuote.totalAmount`、`status: RESERVED`（预留额度）。
- `GET /v1/open/generations/{taskId}` 轮询：`QUEUED → RUNNING(progress 0–100) → SUCCEEDED|FAILED|CANCELED`。
- 成功后 `data.result.outputs[]`，`kind=="VIDEO"` 的 `url` 即成片（签名，约 10 分钟过期）。
- `POST /v1/open/generations/{taskId}/cancel` 取消。
- `capabilityType`：`TEXT_TO_VIDEO` / `IMAGE_TO_VIDEO`（`taskMode: BASIC_GENERATION`，需 `FIRST_FRAME`）/
  `KEYFRAMES_TO_VIDEO`（`KEYFRAME_CONTROL`，`FIRST_FRAME`+`LAST_FRAME`）/ `REFERENCE_TO_VIDEO`（`OMNI_REFERENCE`）。

## 生成：简化接口（URL 引用）

`POST /v1/minimax-h3/{text-to-video|image-to-video|keyframes-to-video|reference-to-video}`，Key，建议带 `Idempotency-Key`。
输入素材是**服务端可访问的 HTTPS URL**（`image_url`、`first_image_url`、`last_image_url`、`image_urls`、`video_urls`、`audio_urls`）。

| 字段 | 取值 |
|---|---|
| `prompt` | 必填，1–7000 字符 |
| `resolution` | `480P` \| `768P`（默认 768P） |
| `duration` | 整数 5–15 秒（默认 5） |
| `queue_tier` | `flex` \| `standard`（默认）\| `priority` |
| `prompt_optimization` | `official`（默认）\| `none`；`paprika` 暂未开放（返回 400） |
| `aspect_ratio` | 文生：`16:9\|1:1\|9:16`；参考：`adaptive\|21:9\|16:9\|4:3\|1:1\|3:4\|9:16` |

返回 `request_id`、`status_url`、`cancel_url`、`price`；轮询 `status_url`（状态 `queued/processing/completed/failed/cancelled`），
完成后读 `video.url`。`POST /v1/minimax-h3/requests/{id}/queue-tier {"queue_tier":"priority"}` 可给排队中的任务升档。

## 参考生视频的素材限制（`inputPolicy`）

| 项 | 限制 |
|---|---|
| 图片 | ≤9 张，≤30MB，jpeg/png/webp/heic/heif |
| 视频 | ≤3 个，≤50MB，mp4/mov，视频编码 h264/hevc，音轨 aac/mp3，24–60 fps，2–15 秒 |
| 音频 | ≤3 个，≤15MB，mp3/wav，2–15 秒 |
| 总数 | ≤12 个；**音频不能单独使用**（需配图片或视频） |
| 画面 | 宽高比 0.4–2.5，边长 256–5760 px |
| 输出 | 5–15 秒，480P / 768P，无 2K；单条最长 15 秒 |

## 价格（CNY / 秒）

| 分辨率 | flex | standard | priority |
|---|---|---|---|
| 480P | 0.11 | 0.15 | 0.19 |
| 768P | 0.24 | 0.28 | 0.32 |

通用接口默认走 standard。素材（图片/视频）另有免费额度，超出计入素材费，价格以提交返回的 `priceQuote` 为准。
