"""离线 smoke：_prepare_real_video 切真 ts 段 + 抽封面。

不打云端 / 不用账号 / 不连网。只验证：
  - ffmpeg 切片真出 3 段
  - 每段确实是 mpegts（首字节 sync byte 0x47）
  - 封面 jpeg magic 对
  - sess 字段被正确填充

跑：python3 -m pytest scripts/tests/ -v
依赖：系统 ffmpeg + ffprobe
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_pir_event as cpe  # noqa: E402

VIDEO = SCRIPT_DIR / "test_videos" / "bird_a4x_1.mp4"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs system ffmpeg + ffprobe",
)
@pytest.mark.skipif(not VIDEO.is_file(), reason=f"missing test asset {VIDEO}")
def test_prepare_real_video_slices_into_three_mpegts():
    cfg = cpe.Config(
        brand="kiwibit", region="us", env="prod",
        business_api="https://example.com", device_api="https://example.com",
        video_path=str(VIDEO),
        verbose=False,
    )
    sess = cpe.Session()
    cpe._prepare_real_video(cfg, sess)

    # 1) 3 段 ts，每段非空
    assert len(sess.real_ts_segments) == 3
    for i, body in enumerate(sess.real_ts_segments):
        assert body, f"seg {i} empty"
        # MPEG-TS 第一个字节是 sync byte 0x47
        assert body[0] == 0x47, f"seg {i} not mpegts (first byte {body[0]:#x})"

    # 2) 3 个 duration，单位 ms，>3000 (≥3s)
    assert len(sess.real_ts_durations_ms) == 3
    for d in sess.real_ts_durations_ms:
        assert 3000 < d < 6000, f"unexpected duration ms {d}"

    # 3) resolution 解析正确（bird_a4x_1.mp4 是 854x480）
    assert sess.real_video_resolution == "854x480"

    # 4) 封面字节是 jpeg（magic 0xFFD8FF）
    assert sess.image_bytes
    assert sess.image_bytes[:3] == b"\xff\xd8\xff"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs system ffmpeg + ffprobe",
)
def test_prepare_real_video_missing_file_raises():
    cfg = cpe.Config(
        brand="kiwibit", region="us", env="prod",
        business_api="https://example.com", device_api="https://example.com",
        video_path="/tmp/__definitely_does_not_exist_xyz.mp4",
        verbose=False,
    )
    sess = cpe.Session()
    with pytest.raises(cpe.PirError, match="不存在"):
        cpe._prepare_real_video(cfg, sess)
