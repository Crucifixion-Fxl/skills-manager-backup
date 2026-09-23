# 测试视频素材

`--video` / `PIR_VIDEO` 用的真实视频。提供后脚本会用 ffmpeg 切成 3 段
mpegts 真上传，让 KB / VicoHome / VicoNature app 上的相册条目可真播放
（不再是封面 jpg 当 ts 流的黑屏）。

## 文件清单

| 文件 | 时长 | 分辨率 | 大小 | 内容 | 来源 |
|------|------|--------|------|------|------|
| `bird_a4x_1.mp4` | 12.96s | 854×480 | 1.1MB | 真实鸟类活动 | A4x 内部测试拍摄（2026-04-20）|

## 给视频加新素材时的要求

切片用 ffmpeg `-segment_time 4.3 -force_key_frames` 切成 3 段 mpegts，所以视频要满足：

- **时长 ≥ 9 秒**（不然切不出 3 段，脚本会立即报错）
- **H.264 baseline / main profile**（其他 profile 设备播放器可能不支持）
- **AAC 音频**（其他编码 ffmpeg 也能转，但增加 CPU 开销）
- **大小建议 < 2MB**（git 仓库友好；通过降分辨率 / 提高 crf 控制）

推荐的 ffmpeg 转码参数（同 `bird_a4x_1.mp4` 处理）：

```bash
ffmpeg -y -i input.mp4 \
  -vf "scale=854:480" \
  -c:v libx264 -profile:v main -preset slow -crf 28 \
  -force_key_frames "expr:gte(t,n_forced*2)" \
  -c:a aac -b:a 48k -ar 44100 -ac 1 \
  -movflags +faststart \
  bird_xxx.mp4
```

## 用法

```bash
# 用 skill 自带素材
python scripts/create_pir_event.py --profile my-profile \
  --object-type bird --video bird_a4x_1.mp4

# 用任意本地视频（绝对路径）
python scripts/create_pir_event.py --profile my-profile \
  --object-type bird --video /Users/me/Downloads/my_bird.mp4
```

`--video` 隐含取首帧作为 AI 识别封面，`--image` 同时存在时被忽略（封面以视频首帧为准，
保证封面 = 视频内容，符合产品语义）。
