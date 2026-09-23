#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "requests>=2.25",
#   "protobuf>=5.27,<7",
# ]
# ///
"""
创建 VicoHome PIR 事件脚本

复刻 MeterSphere 场景「[自动化] PIR 事件」的核心上报链路（step 2~10 + 相册验证）：
    login → wakeupDevice → httpToken → deviceMsg/wakeup(取 traceId)
      → deviceMsg/pir(核心) → video/sliceReport → video/uploadComplete
      → 轮询相册确认新事件可见

配置优先级（高 → 低）：
    命令行参数 > 环境变量 > .env 文件 > 内置默认

支持的环境变量 / .env 键：
    PIR_ENV                staging | prod  (默认 staging)
    PIR_EMAIL              登录邮箱
    PIR_PASSWORD           登录密码
    PIR_APP_TOKEN          现有 App token；提供后跳过 /account/login（仅环境变量）
    PIR_DEVICE             设备 serialNumber
    PIR_USER_SN            账号 userSn
    PIR_BUSINESS_API       覆盖 vicohome.io 域名
    PIR_DEVICE_API         覆盖 addx.live 域名
    PIR_AI_LOCATION_IP     public IP for staging AI GeoIP testing
    PIR_AI_COUNTRY_NO      account country override for staging AI testing

常用示例：
    # 交互式（首次运行，tty 下会提示输入邮箱/密码）
    python tools/create_pir_event.py

    # 用 .env 文件（自动从当前目录或 ~/.config/addx/pir.env 加载）
    python tools/create_pir_event.py --count 3

    # 用 CLI 临时覆盖
    python tools/create_pir_event.py --email a@b.com --password xxx --device <sn>

    # 查看生效配置
    python tools/create_pir_event.py --show-config

    # 把当前 CLI 参数保存成 .env 供下次复用
    python tools/create_pir_event.py --email a@b.com --password xxx \\
        --device <sn> --save-config tools/.pir.env
"""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ─── 依赖自动安装（首次运行如果缺失会自动 pip install） ───
try:
    import requests  # noqa: F401
    import google.protobuf  # noqa: F401
except ImportError:
    print("⚠️  Missing dependencies; installing requests + protobuf...", file=sys.stderr)
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "requests>=2.25",
                "protobuf>=5.27,<7",
            ]
        )
        import requests  # noqa: F401
        import google.protobuf  # noqa: F401
        print("✅ Installed requests + protobuf", file=sys.stderr)
    except subprocess.CalledProcessError as exc:
        print(
            f"❌ Failed to install dependencies: {exc}\n"
            "请手动运行：\n"
            f"    {sys.executable} -m pip install -r "
            f"{Path(__file__).resolve().parent / 'requirements.txt'}\n"
            "或使用 uv：uv run " + __file__,
            file=sys.stderr,
        )
        sys.exit(1)

# ─────────────────────────── 环境预设 ───────────────────────────

# ─────────────── 品牌 × 区域 × 环境 Preset ───────────────
#
# 来源：从 metersphere.addx.live 拉的 45 个 environment 配置中提取，
#      已标注哪些是 MeterSphere 验证过的、哪些是按命名规律推测的。
# 用户仍可通过 --business-api / --device-api 手动覆盖任一值。

__version__ = "1.12.1"

SUPPORTED_BRANDS = ("vicohome", "kiwibit", "viconature")
SUPPORTED_REGIONS = ("us", "eu")
SUPPORTED_ENVS = ("staging", "pre", "prod")

# 域名常量（避免字面量重复，便于以后批量调整）
_VH_API_STAGING_US = "https://api-staging-us.vicohome.io"
_VH_API_PRE_US = "https://api-pre-us.vicohome.io"
_VH_API_PROD_US = "https://api-us.vicohome.io"
_VH_API_STAGING_EU = "https://api-staging-eu.vicohome.io"
_VH_API_PRE_EU = "https://api-pre-eu.vicohome.io"
_VH_API_PROD_EU = "https://api-eu.vicohome.io"
_ADDX_DEVICE_STAGING = "https://api-staging-us.addx.live"
_ADDX_DEVICE_PRE = "https://api-pre.addx.live"
_ADDX_DEVICE_PROD = "https://api.addx.live"
_KB_API_STAGING_US = "https://api-staging-us.kiwibit.com"
_KB_API_PRE_US = "https://api-pre-us.kiwibit.com"
_KB_API_PROD_US = "https://api-us.kiwibit.com"
_AI_STAGING_DEVICE_HOSTS = frozenset({
    urlparse(_ADDX_DEVICE_STAGING).hostname,
    urlparse(_KB_API_STAGING_US).hostname,
    urlparse(_VH_API_STAGING_US).hostname,
})

# (brand, region, env) → {business_api, device_api, env_tag}
# env_tag 写入 APP_META 的 "env" 字段，值来自 MeterSphere 环境配置
PRESETS: dict[tuple[str, str, str], dict[str, str]] = {
    # VicoHome US — 6/6 verified from MeterSphere
    ("vicohome", "us", "staging"): {
        "business_api": _VH_API_STAGING_US,
        "device_api":   _ADDX_DEVICE_STAGING,
        "env_tag":      "staging",
    },
    ("vicohome", "us", "pre"): {
        "business_api": _VH_API_PRE_US,
        "device_api":   _ADDX_DEVICE_PRE,
        "env_tag":      "pre",
    },
    ("vicohome", "us", "prod"): {
        "business_api": _VH_API_PROD_US,
        "device_api":   _ADDX_DEVICE_PROD,
        "env_tag":      "prod-k8s",
    },
    # VicoHome EU — business_api verified, device_api 按同环境 US 规律
    ("vicohome", "eu", "staging"): {
        "business_api": _VH_API_STAGING_EU,
        "device_api":   _ADDX_DEVICE_STAGING,  # EU staging 共用 US device
        "env_tag":      "staging",
    },
    ("vicohome", "eu", "pre"): {
        "business_api": _VH_API_PRE_EU,
        "device_api":   _ADDX_DEVICE_PRE,
        "env_tag":      "pre",
    },
    ("vicohome", "eu", "prod"): {
        "business_api": _VH_API_PROD_EU,
        "device_api":   _ADDX_DEVICE_PROD,
        "env_tag":      "prod-k8s",
    },

    # KiwiBit US — device_api 和 business_api 同域（api-us.kiwibit.com 带 tenantId=kiwibit）
    # 跨租户的 api.addx.live 无法识别 KB 设备（JWT tenantId=None），2026-04-24 实测确认
    ("kiwibit", "us", "staging"): {
        "business_api": _KB_API_STAGING_US,
        "device_api":   _KB_API_STAGING_US,
        "env_tag":      "staging",
    },
    ("kiwibit", "us", "pre"): {
        "business_api": _KB_API_PRE_US,
        "device_api":   _KB_API_PRE_US,
        "env_tag":      "pre",
    },
    ("kiwibit", "us", "prod"): {
        "business_api": _KB_API_PROD_US,
        "device_api":   _KB_API_PROD_US,
        "env_tag":      "prod-k8s",
    },

    # VicoNature — OEM 壳，tenantId 实际也是 vicoo；device_api 必须走业务同域
    # 避免 api.addx.live 跨租户识别失败（JWT tenantId=None）
    ("viconature", "us", "staging"): {
        "business_api": _VH_API_STAGING_US,
        "device_api":   _VH_API_STAGING_US,
        "env_tag":      "staging",
    },
    ("viconature", "us", "pre"): {
        "business_api": _VH_API_PRE_US,
        "device_api":   _VH_API_PRE_US,
        "env_tag":      "pre",
    },
    ("viconature", "us", "prod"): {
        "business_api": _VH_API_PROD_US,
        "device_api":   _VH_API_PROD_US,
        "env_tag":      "prod-k8s",
    },
}

# APP_META 字面量常量（避免 Sonar S1192 重复字面量警告，便于以后批量更新）
_APP_NAME_VH_STAGE = "VicoHome Stage"
_BUNDLE_VH = "com.smartaddx.vicohome"
_BUNDLE_KB = "com.kb.kiwibit"
_BUNDLE_VN = "com.smartaddx.vicohome.nature"
_TZ_SHANGHAI = "Asia/Shanghai"
_MIME_JPEG = "image/jpeg"

# 品牌 × 环境 → APP_META 模板（env_tag 动态填入）
# version / versionName 取自最近一次 MeterSphere 环境配置；如果用户要更精确版本号请用 .env 或 CLI 覆盖
_APP_META_TEMPLATES: dict[tuple[str, str], dict[str, Any]] = {
    ("vicohome", "staging"): {
        "apiVersion": "v1", "appName": _APP_NAME_VH_STAGE, "appType": "Android",
        "bundle": _BUNDLE_VH, "tenantId": "vicoo",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502802, "versionName": "2.25.0_test(9589dc)",
    },
    ("vicohome", "pre"): {
        "apiVersion": "v1", "appName": _APP_NAME_VH_STAGE, "appType": "Android",
        "bundle": _BUNDLE_VH, "tenantId": "vicoo",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502907, "versionName": "2.25.0_test(80181f)",
    },
    ("vicohome", "prod"): {
        "apiVersion": "v1", "appName": _APP_NAME_VH_STAGE, "appType": "Android",
        "bundle": _BUNDLE_VH, "tenantId": "vicoo",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502907, "versionName": "2.25.0_test(80181f)",
    },
    ("kiwibit", "staging"): {
        "appName": "Kiwibit Stage", "appType": "Android",
        "bundle": _BUNDLE_KB, "tenantId": "kiwibit",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502778, "versionName": "2.25.0_test(bfebef)",
    },
    ("kiwibit", "pre"): {
        "appName": "Kiwibit", "appType": "Android",
        "bundle": _BUNDLE_KB, "tenantId": "kiwibit",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502778, "versionName": "2.25.0",
    },
    ("kiwibit", "prod"): {
        "appName": "Kiwibit", "appType": "Android",
        "bundle": _BUNDLE_KB, "tenantId": "kiwibit",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502778, "versionName": "2.25.0",
    },
    # VicoNature —— 实证：OEM 壳，实际 tenantId=vicoo 与 VicoHome 共用租户后端
    # (2026-04-24 用 a1xtest@163.com / txie@a4x.io 实测)
    ("viconature", "staging"): {
        "apiVersion": "v1", "appName": "VicoNature Stage", "appType": "Android",
        "bundle": _BUNDLE_VN, "tenantId": "vicoo",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502802, "versionName": "2.25.0_test(9589dc)",
    },
    ("viconature", "pre"): {
        "apiVersion": "v1", "appName": "VicoNature", "appType": "Android",
        "bundle": _BUNDLE_VN, "tenantId": "vicoo",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502907, "versionName": "2.25.0",
    },
    ("viconature", "prod"): {
        "apiVersion": "v1", "appName": "VicoNature", "appType": "Android",
        "bundle": _BUNDLE_VN, "tenantId": "vicoo",
        "timeZone": _TZ_SHANGHAI,
        "version": 202502907, "versionName": "2.25.0",
    },
}


def resolve_preset(brand: str, region: str, env: str) -> tuple[dict[str, str], dict[str, Any]]:
    """根据 (brand, region, env) 组合返回 (api_preset, app_meta)。"""
    key = (brand, region, env)
    api_preset = PRESETS.get(key)
    if api_preset is None:
        available = sorted(PRESETS.keys())
        raise ValueError(
            f"不支持的组合 --brand={brand} --region={region} --env={env}\n"
            f"支持的组合 (brand, region, env)：\n  " + "\n  ".join(map(str, available))
        )
    meta_base = _APP_META_TEMPLATES.get((brand, env), _APP_META_TEMPLATES[(brand, "prod")])
    app_meta = {**meta_base, "env": api_preset["env_tag"]}
    return api_preset, app_meta

# ─────────────────────────── 常量（来源：MeterSphere 场景） ─────────

# 默认值全部为空 —— 强制用户通过 .env / 环境变量 / CLI 提供
# 不硬编码任何账号 / 设备 / 密码，避免 skill 被复用到其他账号时意外泄露
DEFAULT_EMAIL = ""
DEFAULT_PASSWORD = ""
DEFAULT_SERIAL = ""
DEFAULT_USER_SN = ""

# 设备消息签名 key（HMAC-SHA1 base64）— 必须通过环境变量 PIR_SIGN_SECRET 提供
# 来源：公司 MeterSphere 场景「[自动化] PIR 事件」（脚本不内置默认值，避免凭证落仓）
DEVICE_SIGN_SECRET_B64 = os.getenv("PIR_SIGN_SECRET", "")

# 默认 APP_META 指向 vicohome prod；resolve_config 会按 brand/region/env 动态替换
_DEFAULT_PRESET, APP_META = resolve_preset("vicohome", "us", "prod")

# 相册事件的视频/图片占位 URL。当前指向 staging 假 S3（点开会 404）
# 如果 L4 回归需要视频能实际播放，通过 env 覆盖成 prod 真实可播 URL
TEST_IMAGE_URL = os.getenv(
    "PIR_TEST_IMAGE_URL",
    "https://a4x-staging-us-vip-3d.s3.amazonaws.com/test/56dca2ce-dc4b-4a02-8a34-167002875269.jpg",
)
TEST_VIDEO_URL = os.getenv(
    "PIR_TEST_VIDEO_URL",
    "https://a4x-staging-us-vip-3d.s3.amazonaws.com/test/7045a2ec-0c60-464b-8815-873cf4c8b0f2.ts",
)

# ─── 对象类型 (--object-type) ───
# 来源：MeterSphere 基站项目 pir事件-{vehicle,pet,package} + 方案项目 生成PIR-鸟
# - 硬编码 tag 类型：客户端在 uploadAIImage 的 boxes[].name 里直接打 tag
# - AI 识别类型：boxes=[]，靠后端 ai-cloud 模型识别真实图片内容
OBJECT_TYPES_HARDCODED = ("person", "pet", "vehicle", "package")
OBJECT_TYPES_AI_INFER = ("bird", "small_animal")
OBJECT_TYPES_EMPTY = ("motion",)  # boxes=[{}] 空
OBJECT_TYPES = OBJECT_TYPES_HARDCODED + OBJECT_TYPES_AI_INFER + OBJECT_TYPES_EMPTY

# 各对象类型默认 box 坐标 + score（来自 MeterSphere 场景实采值）
OBJECT_BOX_PRESETS: dict[str, dict] = {
    "person":  {"left": 0.530, "top": 0.188, "right": 0.917, "bottom": 1.000, "score": 0.926},
    "pet":     {"left": 0.540, "top": 0.352, "right": 0.794, "bottom": 0.978, "score": 0.895},
    "vehicle": {"left": 0.450, "top": 0.313, "right": 0.778, "bottom": 0.702, "score": 0.816},
    "package": {"left": 0.368, "top": 0.291, "right": 0.612, "bottom": 0.624, "score": 0.417},
    # bird/small_animal: 默认中心 box，让后端 keyshot 抽帧链路有坐标输入
    "bird":         {"left": 0.300, "top": 0.300, "right": 0.700, "bottom": 0.800, "score": 0.95},
    "small_animal": {"left": 0.300, "top": 0.300, "right": 0.700, "bottom": 0.800, "score": 0.90},
}

# 视频切片时长预设 (ms)，按对象类型微调（来自 MS 场景）
SLICE_PERIOD_PRESETS: dict[str, tuple[int, int, int]] = {
    "person":       (3991, 2933, 2999),
    "pet":          (3991, 3933, 2000),
    "vehicle":      (3991, 2933, 2999),
    "package":      (2000, 3866, 2000),
    "motion":       (3991, 2933, 2999),
    "bird":         (3991, 2933, 2999),
    "small_animal": (3991, 2933, 2999),
}

# 设备固件 preset（uploadComplete 里的设备标识）
# 说明：现脚本默认 CX 系列 (IN1B/CQ121C-JS)，和 wakeup 默认 payload (HI3861L) 一致。
# 若用户的设备是基站或喂鸟器，需要通过 --device-firmware-preset 切换。
DEVICE_FIRMWARE_PRESETS: dict[str, dict[str, str]] = {
    "cx-cq121c":  {"firmwareType": "IN1B", "modelNo": "CQ121C-JS", "version": "1.8.26", "gitSha": "aa0c8d", "resolution": "640x360"},
    "ss131":      {"firmwareType": "IN5D", "modelNo": "SS1131W1",  "version": "1.8.8",  "gitSha": "5499f9", "resolution": "1280x720"},
    "kf126":      {"firmwareType": "IN5B", "modelNo": "KF126",     "version": "1.8.8",  "gitSha": "5499f9", "resolution": "1280x720"},
}
DEFAULT_DEVICE_FIRMWARE_PRESET = "cx-cq121c"

# skill 目录 scripts/test_images/<type>.jpg 作为默认识别图
SCRIPT_DIR = Path(__file__).resolve().parent
TEST_IMAGES_DIR = SCRIPT_DIR / "test_images"
TEST_VIDEOS_DIR = SCRIPT_DIR / "test_videos"
DEFAULT_TEST_VIDEO = "bird_a4x_1.mp4"   # 480p / 12.96s 鸟视频，~1.1MB
DEFAULT_OBJECT_IMAGES: dict[str, str] = {
    "person":       "person.jpg",
    "pet":          "pet.jpg",
    "vehicle":      "vehicle.jpg",
    "package":      "package.jpg",
    "motion":       "person.jpg",       # motion 用 person 图凑数即可（boxes 为空）
    "bird":         "bird_cn_1.jpeg",
    "small_animal": "bird_cn_1.jpeg",   # 小动物暂时复用鸟图（AI 识别会分流）
}


# Profile 存储目录：~/.config/addx/pir/<profile>.env
# 为什么不放脚本同目录？因为 plugin install 会把整个 skill 目录复制到 cache，
# 敏感凭证文件会随 skill 分发泄露给其他同事。用户 home 下的文件不会被 plugin 复制。
PROFILE_DIR = Path.home() / ".config" / "addx" / "pir"

# .env 候选路径（靠前优先）
#   1. ~/.config/addx/pir/<profile>.env         ← profile 机制（通过 --profile 选择）
#   2. ~/.config/addx/pir.env                    ← 全局单文件（兼容 profile 前的老配置）
#   3. ./.pir.env                                ← 当前工作目录
#   4. ./tools/.pir.env                          ← 当前目录下的 tools/
ENV_FILE_CANDIDATES = [
    Path.home() / ".config" / "addx" / "pir.env",
    Path.cwd() / ".pir.env",
    Path.cwd() / "tools" / ".pir.env",
]


# ─────────────────────────── 数据类 ───────────────────────────


class PirError(RuntimeError):
    pass


class BirdPlanError(ValueError):
    """expand_bird_plans 输入校验错误（图库缺失 / 物种未知等）。"""


def expand_bird_plans(
    birds_dir: Path,
    bird_species: str | None,
    variety: int | None,
    bulk: int | None,
    rng: "random.Random | None" = None,
) -> list[dict]:
    """把 --bird-species / --variety / --bulk 展开为 [{image, species, label}, ...]。

    选种规则：
      - variety=N → 随机选 N 个不同物种（受 birds_dir 实际拥有的物种数限制）
      - bird_species="random" → 随机 1 种
      - bird_species=<name>   → 指定 1 种（必须存在）
      - 都没传 → 抛 BirdPlanError
    每个物种重复 bulk 条（默认 3），图按文件名循环。
    """
    if not birds_dir.is_dir():
        raise BirdPlanError(f"鸟类图库不存在: {birds_dir}")
    all_species = sorted(
        p.name for p in birds_dir.iterdir() if p.is_dir() and any(p.glob("*.jpg"))
    )
    if not all_species:
        raise BirdPlanError(f"鸟类图库为空: {birds_dir}")

    rng = rng or random.Random()
    if variety:
        n = min(variety, len(all_species))
        chosen = rng.sample(all_species, n)
    elif bird_species == "random":
        chosen = [rng.choice(all_species)]
    elif bird_species in all_species:
        chosen = [bird_species]
    elif bird_species:
        raise BirdPlanError(
            f"未知 bird-species: {bird_species!r}\n   可选: {all_species + ['random']}"
        )
    else:
        raise BirdPlanError("必须传 --bird-species 或 --variety")

    bulk_n = bulk or 3
    plans: list[dict] = []
    for species in chosen:
        imgs = sorted((birds_dir / species).glob("*.jpg"))
        for i in range(bulk_n):
            img = imgs[i % len(imgs)]
            plans.append({
                "image": str(img),
                "species": species,
                "label": f"{species} (img {img.name})",
            })
    return plans


def apply_bird_auto_defaults(args: argparse.Namespace, cfg: "Config") -> None:
    """bird 一键造数据：把 object-type / firmware-preset 自动落到 cfg。

    规则：
      - cfg.object_type 为空 → 设为 "bird"
      - 用户没显式传 --device-firmware-preset (即 args.device_firmware_preset is None) →
        设为 "kf126"（喂鸟器型号）
    cfg.device_firmware_preset 经过 resolve_config 的 _coalesce 已经 fallback 到默认值，
    必须看 args 原值才能区分"用户没传"和"用户传了默认值"。
    """
    if not cfg.object_type:
        cfg.object_type = "bird"
    if getattr(args, "device_firmware_preset", None) is None:
        cfg.device_firmware_preset = "kf126"


@dataclass
class Config:
    brand: str = "vicohome"
    region: str = "us"
    env: str = "prod"
    business_api: str = ""
    device_api: str = ""
    email: str = ""
    password: str = ""
    app_token: str = ""
    device_auth_only: bool = False
    serial_number: str = ""
    user_sn: str = ""
    count: int = 1
    verify_gallery: bool = True
    verify_timeout_sec: int = 90
    verbose: bool = True
    app_meta: dict = field(default_factory=lambda: resolve_preset("vicohome", "us", "prod")[1])
    # v1.7.0 新增：对象类型 / 图片 / 设备固件
    object_type: str = ""                       # 空=走老链路；否则见 OBJECT_TYPES
    image_path: str = ""                        # 覆盖默认 test_images/<type>.jpg
    device_firmware_preset: str = DEFAULT_DEVICE_FIRMWARE_PRESET
    # v1.10.0 新增：真视频上传（让 KB app 真能播放视频段）
    video_path: str = ""                        # mp4 文件路径；非空则走 ffmpeg 切片真上传链路
    # v1.12.0 新增：staging AI 地理门禁测试。非空时从 /deviceMsg/config 获取新 aiCloudParam。
    ai_location_ip: str = ""
    # 可选覆盖 AiCloudParam.countryNo，用于模拟与测试账号国家不同的 AI 地区。
    ai_country_no: str = ""

    def masked(self) -> dict:
        d = asdict(self)
        if d["password"]:
            d["password"] = "***"
        if d["app_token"]:
            d["app_token"] = "***"
        return d


@dataclass
class Session:
    app_token: str = ""
    device_token: str = ""
    trace_id: str = ""
    time_sec: int = 0
    time_ms: int = 0
    log_lines: list[str] = field(default_factory=list)
    # v1.7.0 新增：uploadAIImage / videoFile/upload 用的路径模板（from /deviceMsg/pir 响应）
    ptoken: str = ""
    image_path: str = ""                         # 解析后的 imageKey（含设备前缀）
    ts_paths: list[str] = field(default_factory=list)  # [tsPath0, tsPath1, tsPath2]
    slice_periods: list[int] = field(default_factory=list)
    display_model_no: str = ""
    user_id: str = ""
    ai_cloud_endpoint: str = ""
    ai_cloud_param: str = ""
    image_bytes: bytes = b""                     # 缓存本次上传的图片字节
    # v1.8.0：AWS S3 STS 直传（CG625A1 等新设备走这套，替代 ptoken + accessUri）
    # 两套上传方式互斥：sess.ptoken 有值走老方式；sess.s3_bucket 有值走 S3 SigV4
    s3_bucket: str = ""
    s3_region: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_session_token: str = ""
    # v1.10.0：真视频切片（cfg.video_path 非空时由 _prepare_real_video 填充）
    real_ts_segments: list[bytes] = field(default_factory=list)
    real_ts_durations_ms: list[int] = field(default_factory=list)
    real_video_resolution: str = ""
    # v1.11.4：本次 create_one 创建的临时目录（ffmpeg 切片输出），create_one 末尾统一清理
    tmp_dirs: list[Path] = field(default_factory=list)


# ─────────────────────────── 配置加载 ───────────────────────────


def _parse_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，支持 # 注释与双/单引号。"""
    data: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
    return data


def _profile_path(name: str) -> Path:
    """<profile> → ~/.config/addx/pir/<profile>.env"""
    return PROFILE_DIR / f"{name}.env"


def _discover_env_file(
    explicit: str | None, profile: str | None
) -> tuple[Path | None, dict[str, str]]:
    """按优先级定位 .env 文件：--profile > --env-file > 候选路径。"""
    if profile:
        p = _profile_path(profile)
        if not p.is_file():
            raise ValueError(
                f"profile {profile!r} 不存在（预期路径 {p}）。\n"
                f"用 --list-profiles 查看现有 profile，或 --init-profile {profile} 创建。"
            )
        return p, _parse_env_file(p)
    if explicit:
        p = Path(explicit).expanduser()
        return p, _parse_env_file(p)
    for p in ENV_FILE_CANDIDATES:
        if p.is_file():
            return p, _parse_env_file(p)
    return None, {}


def _list_profiles() -> list[str]:
    if not PROFILE_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILE_DIR.glob("*.env"))


# Sonar S3776 (cognitive complexity 45 > 15): 交互式 wizard 必须线性走完
# brand → region → env → email → password → device → user_sn 7 步验证 +
# 错误回退分支。拆分会破坏"问完一项继续下一项"的用户体验线性。
def _init_profile_interactive(  # NOSONAR
    name: str,
    brand: str | None = None,
    region: str | None = None,
    env_name: str | None = None,
    email: str | None = None,
    password: str | None = None,
    device: str | None = None,
    user_sn: str | None = None,
) -> Path:
    """创建一个 profile。每个参数若预先提供就跳过对应提问；
    如果 email+password+device 都给了 → headless 模式，不对话，直接写文件。
    """
    target = _profile_path(name)
    if target.exists():
        raise ValueError(f"profile {name!r} 已存在：{target}（先 --delete-profile 或换名字）")

    headless = bool(email and password and device)

    if not headless:
        print(f"\n🆕 创建新 profile：{name}", file=sys.stderr)
        print(f"   存储位置：{target}\n", file=sys.stderr)

    if brand:
        if not headless: print(f"  品牌: {brand}", file=sys.stderr)
    else:
        if headless:
            raise ValueError("headless 模式要求提供 --brand")
        brand = input("  品牌 [vicohome / kiwibit / viconature] (默认 vicohome): ").strip() or "vicohome"

    if region:
        if not headless: print(f"  区域: {region}", file=sys.stderr)
    else:
        if headless:
            raise ValueError("headless 模式要求提供 --region")
        region = input("  区域 [us / eu] (默认 us): ").strip() or "us"

    if env_name:
        if not headless: print(f"  环境: {env_name}", file=sys.stderr)
    else:
        if headless:
            raise ValueError("headless 模式要求提供 --env")
        env_name = input("  环境 [staging / pre / prod] (默认 prod): ").strip() or "prod"

    if email:
        if not headless: print(f"  账号邮箱: {email}", file=sys.stderr)
    else:
        email = input("  账号邮箱: ").strip()

    if not password:
        password = getpass.getpass("  密码: ")

    # headless 模式：device/user_sn 必须已经作为参数给了，跳过对话
    if not headless:
        # 非 headless：尝试登录查设备让用户选，失败退回手动输入
        tmp_device, tmp_user_sn = "", ""
        try:
            api_preset, app_meta = resolve_preset(brand, region, env_name)
            probe_cfg = Config(
                brand=brand, region=region, env=env_name,
                business_api=api_preset["business_api"],
                device_api=api_preset["device_api"],
                email=email, password=password, app_meta=app_meta,
                verbose=False,
            )
            sess = Session()
            step_login(probe_cfg, sess)
            picked = _pick_device_interactive(probe_cfg, sess)
            if picked is not None:
                tmp_device = picked.get("serialNumber") or picked.get("sn") or ""
                tmp_user_sn = picked.get("adminUsrSn") or picked.get("userSn") or ""
        except PirError as e:
            print(f"  ⚠️  自动查询设备失败（{e}），继续手动输入", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️  自动查询设备时出错: {type(e).__name__}，继续手动输入", file=sys.stderr)

        if not device:
            device = tmp_device or input("  设备 serialNumber: ").strip()
        if not user_sn:
            user_sn = tmp_user_sn or input("  账号 userSn (可留空): ").strip()
    # headless 下 device 已经由参数提供，user_sn 可选
    user_sn = user_sn or ""

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# create-pir profile\n"
        f"# 由 --init-profile {name} 生成\n"
        f"PIR_BRAND={brand}\n"
        f"PIR_REGION={region}\n"
        f"PIR_ENV={env_name}\n"
        f"PIR_EMAIL={email}\n"
        f"PIR_PASSWORD={password}\n"
        f"PIR_DEVICE={device}\n"
        f"PIR_USER_SN={user_sn}\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    return target


def _delete_profile(name: str) -> Path:
    target = _profile_path(name)
    if not target.exists():
        raise ValueError(f"profile {name!r} 不存在：{target}")
    target.unlink()
    return target


def _write_profile(path: Path, fields: dict[str, str], header_comment: str = "") -> None:
    """把 fields dict 写成 .env 文件格式（保持 key=value 每行一项）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# create-pir profile",
    ]
    if header_comment:
        lines.append(f"# {header_comment}")
    for key, val in fields.items():
        lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _load_profile_fields(path: Path) -> dict[str, str]:
    """读 profile 里所有 PIR_* 字段（保留顺序）。"""
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.startswith("PIR_"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
    return data


def _cfg_from_profile_fields(fields: dict[str, str]) -> Config:
    """由 profile fields 构造一个 Config（仅用于 switch-device / edit-profile）。"""
    brand = fields.get("PIR_BRAND", "vicohome")
    region = fields.get("PIR_REGION", "us")
    env_name = fields.get("PIR_ENV", "prod")
    api_preset, app_meta = resolve_preset(brand, region, env_name)
    return Config(
        brand=brand, region=region, env=env_name,
        business_api=fields.get("PIR_BUSINESS_API") or api_preset["business_api"],
        device_api=fields.get("PIR_DEVICE_API") or api_preset["device_api"],
        email=fields.get("PIR_EMAIL", ""),
        password=fields.get("PIR_PASSWORD", ""),
        serial_number=fields.get("PIR_DEVICE", ""),
        user_sn=fields.get("PIR_USER_SN", ""),
        app_meta=app_meta,
        verbose=False,
    )


# Sonar S3776 (cognitive complexity 22 > 15): 设备切换 wizard 需顺序: 加载 profile
# → 登录 → 列设备 → 交互选择 → 校验 → 写回 profile, 每一步都有 fail-fast 校验。
# 拆 sub-helpers 反而把"profile 在场上下文" 拆碎, 不利于错误处理与可读性
def cmd_switch_device(name: str) -> int:  # NOSONAR
    """登录 profile 的账号 → 列设备 → 交互选新设备 → 更新 profile 文件。"""
    path = _profile_path(name)
    if not path.exists():
        print(f"❌ profile {name!r} 不存在：{path}", file=sys.stderr)
        return 2
    fields = _load_profile_fields(path)
    if not fields.get("PIR_EMAIL") or not fields.get("PIR_PASSWORD"):
        print(f"❌ profile {name!r} 缺账号或密码，请先 --edit-profile {name} 补全", file=sys.stderr)
        return 2
    cfg = _cfg_from_profile_fields(fields)
    print(f"🔄 切换 profile {name!r} 的设备（{cfg.email} @ {cfg.brand} / {cfg.env}）")
    sess = Session()
    try:
        step_login(cfg, sess)
    except PirError as e:
        print(f"❌ 登录失败: {e}", file=sys.stderr)
        return 1

    current_sn = fields.get("PIR_DEVICE", "")
    print(f"   当前设备：{current_sn or '(未设置)'}")
    picked = _pick_device_interactive(cfg, sess)
    if picked is None:
        print("已取消，profile 未修改")
        return 0
    new_sn = picked.get("serialNumber") or picked.get("sn") or ""
    new_user_sn = picked.get("adminUsrSn") or picked.get("userSn") or fields.get("PIR_USER_SN", "")
    if not new_sn:
        print("❌ 选中的设备没有 serialNumber，取消", file=sys.stderr)
        return 1
    if new_sn == current_sn:
        print(f"✅ 新设备与当前一致（{new_sn}），未修改")
        return 0
    fields["PIR_DEVICE"] = new_sn
    fields["PIR_USER_SN"] = new_user_sn
    _write_profile(path, fields, f"由 --switch-device 更新于 {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"✅ profile {name!r} 的设备已切换：\n   {current_sn or '(空)'} → {new_sn}")
    return 0


EDITABLE_FIELDS = ("PIR_BRAND", "PIR_REGION", "PIR_ENV", "PIR_EMAIL", "PIR_PASSWORD", "PIR_DEVICE", "PIR_USER_SN")


# Sonar S3776 (cognitive complexity 22 > 15): profile 7 个字段任选编辑 wizard,
# 每个字段独立 prompt+validate 分支; 拆分会让"哪些字段已编辑/未变"状态分散
def cmd_edit_profile(name: str) -> int:  # NOSONAR
    """交互式修改 profile 任意字段。"""
    path = _profile_path(name)
    if not path.exists():
        print(f"❌ profile {name!r} 不存在：{path}", file=sys.stderr)
        return 2
    fields = _load_profile_fields(path)
    print(f"\n✏️  编辑 profile：{name}\n   存储位置：{path}\n")
    print("  当前字段值：")
    for k in EDITABLE_FIELDS:
        v = fields.get(k, "")
        if k == "PIR_PASSWORD":
            v = "***" if v else "(空)"
        print(f"    {k:18s} = {v or '(空)'}")
    print()
    print("  输入要修改的字段名（或 q 退出）；修改 PIR_DEVICE 可考虑用 --switch-device")
    while True:
        key = input("\n  字段名: ").strip().upper()
        if key in ("Q", "QUIT", "EXIT", ""):
            break
        if not key.startswith("PIR_"):
            key = f"PIR_{key}"
        if key not in EDITABLE_FIELDS:
            print(f"  ⚠️  不支持的字段（可改：{', '.join(EDITABLE_FIELDS)}）")
            continue
        if key == "PIR_PASSWORD":
            new_val = getpass.getpass("  新密码: ")
        else:
            cur = fields.get(key, "")
            new_val = input(f"  新值 [当前 {cur or '空'}]: ").strip()
            if not new_val:
                print("  未修改")
                continue
        fields[key] = new_val
        _write_profile(path, fields, f"由 --edit-profile 更新于 {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  ✅ 已保存 {key}")
    print(f"\n✅ profile 编辑完成：{path}")
    return 0


def _coalesce(*vals: str | None) -> str:
    for v in vals:
        if v:
            return v
    return ""


def _validate_ai_location_ip(value: str, env_name: str) -> None:
    if not value:
        return
    if env_name != "staging":
        raise ValueError("--ai-location-ip is available only in staging")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"--ai-location-ip is not a valid IP address: {value!r}") from exc
    if not address.is_global:
        raise ValueError("--ai-location-ip must be a globally routable GeoIP address")


def _validate_ai_country_no(value: str, env_name: str, location_ip: str) -> None:
    if not value:
        return
    if env_name != "staging":
        raise ValueError("--ai-country-no is available only in staging")
    if not location_ip:
        raise ValueError("--ai-country-no requires --ai-location-ip")
    if len(value) != 2 or not value.isascii() or not value.isalpha():
        raise ValueError("--ai-country-no must be a two-letter ASCII country code, such as US")


def _validate_ai_device_api(value: str) -> None:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or hostname not in _AI_STAGING_DEVICE_HOSTS:
        raise ValueError(
            "--ai-location-ip requires an HTTPS URL using a known staging device-api preset; "
            "refusing to send a synthetic GeoIP request to another environment"
        )


def resolve_config(args: argparse.Namespace) -> tuple[Config, Path | None]:
    env_path, env_file = _discover_env_file(args.env_file, getattr(args, "profile", None))

    brand = _coalesce(args.brand, os.getenv("PIR_BRAND"), env_file.get("PIR_BRAND"), "vicohome").lower()
    region = _coalesce(args.region, os.getenv("PIR_REGION"), env_file.get("PIR_REGION"), "us").lower()
    env_name = _coalesce(args.env, os.getenv("PIR_ENV"), env_file.get("PIR_ENV"), "prod").lower()

    if brand not in SUPPORTED_BRANDS:
        raise ValueError(f"unsupported brand {brand!r}; 支持: {SUPPORTED_BRANDS}")
    if region not in SUPPORTED_REGIONS:
        raise ValueError(f"unsupported region {region!r}; 支持: {SUPPORTED_REGIONS}")
    if env_name not in SUPPORTED_ENVS:
        raise ValueError(f"unsupported env {env_name!r}; 支持: {SUPPORTED_ENVS}")

    api_preset, app_meta = resolve_preset(brand, region, env_name)

    ai_location_ip = _coalesce(
        getattr(args, "ai_location_ip", None),
        os.getenv("PIR_AI_LOCATION_IP"),
        env_file.get("PIR_AI_LOCATION_IP"),
        "",
    )
    _validate_ai_location_ip(ai_location_ip, env_name)
    ai_country_no = _coalesce(
        getattr(args, "ai_country_no", None),
        os.getenv("PIR_AI_COUNTRY_NO"),
        env_file.get("PIR_AI_COUNTRY_NO"),
        "",
    ).upper()
    _validate_ai_country_no(ai_country_no, env_name, ai_location_ip)

    cfg = Config(
        brand=brand,
        region=region,
        env=env_name,
        business_api=_coalesce(
            args.business_api,
            os.getenv("PIR_BUSINESS_API"),
            env_file.get("PIR_BUSINESS_API"),
            api_preset["business_api"],
        ),
        device_api=_coalesce(
            args.device_api,
            os.getenv("PIR_DEVICE_API"),
            env_file.get("PIR_DEVICE_API"),
            api_preset["device_api"],
        ),
        email=_coalesce(args.email, os.getenv("PIR_EMAIL"), env_file.get("PIR_EMAIL"), DEFAULT_EMAIL),
        password=_coalesce(
            args.password, os.getenv("PIR_PASSWORD"), env_file.get("PIR_PASSWORD"), DEFAULT_PASSWORD
        ),
        app_token=_coalesce(
            os.getenv("PIR_APP_TOKEN"),
            "",
        ),
        device_auth_only=bool(getattr(args, "device_auth_only", False)),
        serial_number=_coalesce(
            args.device, os.getenv("PIR_DEVICE"), env_file.get("PIR_DEVICE"), DEFAULT_SERIAL
        ),
        user_sn=_coalesce(
            args.user_sn, os.getenv("PIR_USER_SN"), env_file.get("PIR_USER_SN"), DEFAULT_USER_SN
        ),
        count=args.count,
        verify_gallery=not args.no_verify,
        verify_timeout_sec=args.verify_timeout,
        verbose=not args.quiet,
        app_meta=app_meta,
        object_type=_coalesce(
            getattr(args, "object_type", None),
            os.getenv("PIR_OBJECT_TYPE"),
            env_file.get("PIR_OBJECT_TYPE"),
            "",
        ),
        image_path=_coalesce(
            getattr(args, "image", None),
            os.getenv("PIR_IMAGE"),
            env_file.get("PIR_IMAGE"),
            "",
        ),
        device_firmware_preset=_coalesce(
            getattr(args, "device_firmware_preset", None),
            os.getenv("PIR_DEVICE_FIRMWARE_PRESET"),
            env_file.get("PIR_DEVICE_FIRMWARE_PRESET"),
            DEFAULT_DEVICE_FIRMWARE_PRESET,
        ),
        video_path=_coalesce(
            getattr(args, "video", None),
            os.getenv("PIR_VIDEO"),
            env_file.get("PIR_VIDEO"),
            "",
        ),
        ai_location_ip=ai_location_ip,
        ai_country_no=ai_country_no,
    )
    if cfg.object_type and cfg.object_type not in OBJECT_TYPES:
        raise ValueError(
            f"unsupported object_type {cfg.object_type!r}；支持：{OBJECT_TYPES}"
        )
    if cfg.device_firmware_preset not in DEVICE_FIRMWARE_PRESETS:
        raise ValueError(
            f"unsupported device_firmware_preset {cfg.device_firmware_preset!r}；"
            f"可选：{list(DEVICE_FIRMWARE_PRESETS.keys())}"
        )

    # 签名密钥：env > .env / profile 文件 > 空（空则 generate_device_signature 报错引导）
    # 必须在这里覆盖模块级常量，否则只跑 import 时的 os.getenv，profile/.env 不会生效
    global DEVICE_SIGN_SECRET_B64
    DEVICE_SIGN_SECRET_B64 = _coalesce(
        os.getenv("PIR_SIGN_SECRET"),
        env_file.get("PIR_SIGN_SECRET"),
        "",
    )
    return cfg, env_path


def interactive_fill(cfg: Config, tty_ok: bool) -> None:
    """tty 下若密码为空，强制提示；其余字段可选覆盖。"""
    if not tty_ok:
        return
    if not cfg.password and not cfg.app_token and not cfg.device_auth_only:
        print("（未找到密码，请输入；或使用 --save-config 存到 .env 下次自动加载）", file=sys.stderr)
        while not cfg.password:
            cfg.password = getpass.getpass(f"  密码（邮箱 {cfg.email}）: ")
        return
    # 有默认密码时，仍允许用户改邮箱/设备（常见场景：临时换目标）
    print("（已加载配置；按回车保留，或输入新值覆盖）", file=sys.stderr)
    new_email = input(f"  邮箱 [{cfg.email}]: ").strip()
    if new_email:
        cfg.email = new_email
    new_device = input(f"  设备 serialNumber [{cfg.serial_number}]: ").strip()
    if new_device:
        cfg.serial_number = new_device


def save_config(cfg: Config, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# create_pir_event.py 配置文件",
        "# 由 --save-config 生成；文件权限建议 600（含密码）",
        f"PIR_BRAND={cfg.brand}",
        f"PIR_REGION={cfg.region}",
        f"PIR_ENV={cfg.env}",
        f"PIR_BUSINESS_API={cfg.business_api}",
        f"PIR_DEVICE_API={cfg.device_api}",
        f"PIR_EMAIL={cfg.email}",
        f"PIR_PASSWORD={cfg.password}",
        f"PIR_DEVICE={cfg.serial_number}",
        f"PIR_USER_SN={cfg.user_sn}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ─────────────────────────── 上报链路 ───────────────────────────


def _log(cfg: Config, msg: str) -> None:
    if cfg.verbose:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def generate_device_signature(serial_number: str, time_sec: int) -> str:
    if not DEVICE_SIGN_SECRET_B64:
        raise PirError(
            "签名密钥未配置。请设置环境变量 PIR_SIGN_SECRET（base64），"
            "或在 ~/.config/addx/pir/<profile>.env 中提供 PIR_SIGN_SECRET=…。"
            "凭证从 MeterSphere 场景「[自动化] PIR 事件」提取。"
        )
    try:
        secret = base64.b64decode(DEVICE_SIGN_SECRET_B64)
    except (ValueError, binascii.Error) as exc:
        raise PirError(f"PIR_SIGN_SECRET 不是合法 Base64: {exc}") from exc
    msg = f"{serial_number}{time_sec}".encode("utf-8")
    digest = hmac.new(secret, msg, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii").replace("+", "-").replace("/", "_")


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 15) -> dict:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    resp = requests.post(url, json=body, headers=h, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def step_login(cfg: Config, sess: Session) -> None:
    body = {
        "app": cfg.app_meta,
        "code": "",
        "countryNo": "US",
        "email": cfg.email,
        "language": "zh",
        "loginType": 0,
        "password": cfg.password,
    }
    data = _post_json(f"{cfg.business_api}/account/login", body)
    if not str(data.get("msg", "")).lower().startswith("success"):
        raise PirError(f"登录失败: {data}")
    sess.app_token = data["data"]["token"]["token"]
    _log(cfg, "✅ 登录成功")


def step_authenticate(cfg: Config, sess: Session) -> None:
    """Reuse an existing App session without mutating account login state."""
    if cfg.app_token:
        sess.app_token = cfg.app_token
        _log(cfg, "✅ 使用现有 app-token，跳过 /account/login")
        return
    step_login(cfg, sess)


def step_wakeup_device(cfg: Config, sess: Session) -> None:
    body = {
        "app": cfg.app_meta,
        "countryNo": "CN",
        "language": "zh",
        "serialNumber": cfg.serial_number,
    }
    _post_json(
        f"{cfg.business_api}/device/wakeupDevice",
        body,
        headers={"Authorization": sess.app_token},
    )
    _log(cfg, f"✅ wakeupDevice 成功 serial={cfg.serial_number}")


def step_get_device_token(cfg: Config, sess: Session) -> None:
    sess.time_sec = int(time.time())
    sess.time_ms = int(time.time() * 1000)
    signature = generate_device_signature(cfg.serial_number, sess.time_sec)
    body = {
        "serialNumber": cfg.serial_number,
        "signature": signature,
        "time": sess.time_sec,
        "name": "httpToken",
        "id": 4,
        "value": {},
    }
    data = _post_json(f"{cfg.device_api}/deviceMsg/httpToken", body)
    if str(data.get("result")) != "0":
        raise PirError(f"httpToken 失败: {data}")
    sess.device_token = data["data"]["value"]["token"]
    _log(cfg, "✅ 获取 device-token 成功")


def step_device_msg_wakeup(cfg: Config, sess: Session) -> None:
    sess.time_sec = int(time.time())
    sess.time_ms = int(time.time() * 1000)
    body = {
        "name": "wakeup",
        "time": sess.time_sec,
        "id": 0,
        "value": {
            "por": 2,
            "wifiModule": "HI3861L",
            "FromPowerUpToMqttSend": 3187,
            "batteryEvent": 0,
            "charge": 1,
            "battery": 100,
            "dtim": 58,
            "wakeupId": f"{sess.time_sec}_52",
            "wifiWakeInfo": [{"code": 102, "cnt": 0}, {"code": 109, "cnt": 0}],
            "historyWakeupInfos": [
                {
                    "wakeupId": "1739263277_64",
                    "wakeupSocDuration": 20721,
                    "wakeupReason_id": 2,
                    "wakeupTimestamp": 1739266130,
                    "videoSucceedDuration": 10064,
                    "videoDuration": 10000,
                    "videouploadDuration": 15006,
                    "isValidTrigger": True,
                    "wifiPowerLevel": 0,
                    "wakeupSocTotalDuration": 1278937,
                }
            ],
        },
    }
    data = _post_json(
        f"{cfg.device_api}/deviceMsg/wakeup",
        body,
        headers={"Authorization": sess.device_token},
    )
    if str(data.get("result")) != "0":
        raise PirError(f"deviceMsg/wakeup 失败: {json.dumps(data, ensure_ascii=False)[:500]}")
    trace_id = ((data.get("data") or {}).get("value") or {}).get("traceId")
    if not trace_id:
        raise PirError(
            "deviceMsg/wakeup 响应缺少 data.value.traceId。\n"
            f"  完整响应: {json.dumps(data, ensure_ascii=False)[:800]}\n"
            "  可能原因：设备型号与默认 payload (HI3861L / IN1B / CQ121C-JS) 不匹配，"
            "或设备尚未在当前账号下激活。"
        )
    sess.trace_id = trace_id
    _log(cfg, f"✅ deviceMsg/wakeup 成功 traceId={sess.trace_id}")


# Sonar S3776 (cognitive complexity 32 > 15): /pir/event/report 是核心 PIR 写入步,
# payload 跨设备形态/响应跨版本(老 ptoken/新 S3 STS)/解析 trace_id 多层 fallback,
# 拆完后必须共享大量 sess 状态, 拆分反加耦合
def step_report_pir(cfg: Config, sess: Session) -> None:  # NOSONAR
    time_ms = int(time.time() * 1000)
    body = {
        "name": "reportEvent",
        "time": time_ms,
        "id": 0,
        "value": {
            "event": 1,
            "videoUploadType": 1,
            "traceId": sess.trace_id,
            "motionType": "video",
            "channel": "single",
            "isValidTrigger": 1,
            "battery": 100,
            "chargingMode": 2,
        },
    }
    data = _post_json(
        f"{cfg.device_api}/deviceMsg/pir",
        body,
        headers={"Authorization": sess.device_token},
    )
    if str(data.get("result")) != "0":
        raise PirError(f"deviceMsg/pir 失败: {data}")
    value = data["data"]["value"]
    reported = value.get("traceId")
    sn = value.get("serialNumber")
    if reported != sess.trace_id or sn != cfg.serial_number:
        raise PirError(f"PIR 响应 traceId/serialNumber 不匹配: got traceId={reported} sn={sn}")

    # v1.7.0/1.8.0：提取上传路径模板（老链路用不到；object_type 分支依赖这些字段）
    # 两套上传方式（脚本二选一自动识别）：
    #   A. 老方式（SS131 / KF126 基站 / 喂鸟器）：bxsCredentials.accessUri[0] 提取 ptoken
    #   B. 新方式（CG625A1 等新摄像头）：AWS STS credentials + bucket + clientRegion
    if cfg.object_type:
        image_tmpl = value.get("imageKeyTemplate") or ""
        slice_tmpl = value.get("sliceKeyTemplate") or ""
        # --- 尝试方式 A: ptoken + accessUri ---
        bxs = value.get("bxsCredentials") or {}
        uris = bxs.get("accessUri") or []
        access_uri = uris[0] if isinstance(uris, list) and uris else ""
        # --- 尝试方式 B: AWS STS credentials ---
        aws_creds = value.get("credentials") or {}
        bucket = value.get("bucket") or ""
        region = value.get("clientRegion") or ""
        aws_aki = aws_creds.get("accessKeyId") or ""
        aws_sk = aws_creds.get("secretAccessKey") or ""
        aws_st = aws_creds.get("sessionToken") or ""

        has_method_a = bool(image_tmpl and slice_tmpl and access_uri)
        has_method_b = bool(image_tmpl and slice_tmpl and bucket and region and aws_aki and aws_sk and aws_st)

        if not (has_method_a or has_method_b):
            raise PirError(
                f"deviceMsg/pir 响应既不支持 ptoken 上传也不支持 AWS STS 直传：\n"
                f"  imageKeyTemplate={bool(image_tmpl)}  sliceKeyTemplate={bool(slice_tmpl)}\n"
                f"  方式 A (ptoken): accessUri={access_uri!r}\n"
                f"  方式 B (S3):    bucket={bucket!r} region={region!r} aki={bool(aws_aki)}\n"
                f"  完整 value: {json.dumps(value, ensure_ascii=False)[:800]}\n"
                f"  提示：设备固件不支持上传协议；去掉 --object-type 退回老链路。"
            )

        sess.image_path = image_tmpl.replace("${imgType}", "jpg")
        periods = SLICE_PERIOD_PRESETS.get(cfg.object_type, SLICE_PERIOD_PRESETS["person"])
        sess.slice_periods = list(periods)
        sess.ts_paths = [
            slice_tmpl.replace("${period}", str(periods[i]))
                      .replace("${order}", str(i))
                      .replace("${isLast}", "1" if i == 2 else "0")
            for i in range(3)
        ]
        sess.display_model_no = value.get("displayModelNo") or sess.display_model_no

        if has_method_b:
            # 优先走 AWS 直传（新设备正道；没方式 A 可退）
            sess.s3_bucket = bucket
            sess.s3_region = region
            sess.s3_access_key = aws_aki
            sess.s3_secret_key = aws_sk
            sess.s3_session_token = aws_st
            _log(cfg, f"✅ /deviceMsg/pir 上报完成（S3 直传: bucket={bucket} region={region} imagePath={sess.image_path[-50:]}）")
        else:
            import re as _re
            m = _re.search(r"/p/(.*)/n/pir", access_uri)
            if not m:
                raise PirError(
                    f"accessUri 格式异常，无法提取 ptoken：{access_uri!r}\n"
                    f"  期望形如 https://…/videoFile/upload/p/<token>/n/pir/b/pir/o/<filePath>"
                )
            sess.ptoken = m.group(1)
            _log(cfg, f"✅ /deviceMsg/pir 上报完成（ptoken 网关上传: ptoken={sess.ptoken[:20]}... imagePath={sess.image_path[-50:]}）")
    else:
        _log(cfg, "✅ /deviceMsg/pir 上报完成")


def step_video_slice_report(cfg: Config, sess: Session) -> None:
    time_ms = int(time.time() * 1000)
    body = {
        "traceId": sess.trace_id,
        "serialNumber": cfg.serial_number,
        "serviceName": "oci",
        "fileSize": 135548,
        "timezone": 480,
        "imagePath": TEST_IMAGE_URL,
        "videoPath": TEST_VIDEO_URL,
        "period": 3463,
        "order": 0,
        "isLast": 0,
        "timestamp": 345238,
        "utcTimestampMillis": str(time_ms),
    }
    data = _post_json(
        f"{cfg.business_api}/video/sliceReport",
        body,
        headers={"Authorization": sess.app_token},
    )
    if str(data.get("result")) != "0":
        raise PirError(f"videoSliceReport 失败: {data}")
    _log(cfg, "✅ video/sliceReport 成功")


# ─────────────────────────── 对象类型 (v1.7.0) 扩展步骤 ───────────────────────────


def _resolve_image_path(cfg: Config) -> Path:
    """按 --image > test_images/<type>.jpg 优先级解析本次要上传的图片。"""
    if cfg.image_path:
        p = Path(cfg.image_path).expanduser()
        if not p.is_file():
            raise PirError(f"--image 指向的文件不存在：{p}")
        return p
    default_name = DEFAULT_OBJECT_IMAGES.get(cfg.object_type, "")
    if not default_name:
        raise PirError(f"对象类型 {cfg.object_type!r} 没有默认图片映射")
    p = TEST_IMAGES_DIR / default_name
    if not p.is_file():
        raise PirError(
            f"默认图片缺失：{p}\n"
            f"  提示：从 skill 仓库重新拉取 scripts/test_images/，或用 --image <path> 指定自己的图片"
        )
    return p


def _build_static_video_from_image(
    image_path: Path, sess: "Session | None" = None
) -> Path:
    """把静态图扩展为 13s h264 静帧 mp4，用于 image-only 时也能上传真 ts 段。

    生成的视频满足 skill 切片要求（≥9s, h264, yuv420p）。视频可被 KB app 播放
    （虽是静帧，但是真实 mpegts 视频内容，解码不会失败）。

    若传入 sess，workdir 会注册到 sess.tmp_dirs 由 create_one 末尾统一清理；
    否则保留旧行为（workdir 残留，调用方负责清理）。
    """
    import shutil as _sh, subprocess, tempfile
    if _sh.which("ffmpeg") is None:
        raise PirError("image→静帧视频转换需要 ffmpeg。macOS: brew install ffmpeg")
    workdir = Path(tempfile.mkdtemp(prefix="create_pir_static_"))
    if sess is not None:
        sess.tmp_dirs.append(workdir)
    out = workdir / "static_video.mp4"
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-t", "13",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf", "scale='min(1280,iw)':-2:flags=lanczos,format=yuv420p",
            "-r", "25", "-profile:v", "main", "-preset", "fast", "-crf", "20",
            str(out),
        ], capture_output=True, check=True, timeout=60)
    except subprocess.CalledProcessError as e:
        raise PirError(
            f"image→静帧视频转换失败：\n"
            f"  stderr[-300:]: {(e.stderr or b'').decode('utf-8','replace')[-300:]}"
        )
    if not out.is_file() or out.stat().st_size < 1024:
        raise PirError("image→静帧视频生成的文件无效（<1KB）")
    return out


def _load_image_bytes(cfg: Config, sess: Session) -> bytes:
    if sess.image_bytes:
        return sess.image_bytes
    img = _resolve_image_path(cfg)
    sess.image_bytes = img.read_bytes()
    return sess.image_bytes


def _prepare_real_video(cfg: Config, sess: Session) -> None:
    """v1.10.0：用 ffmpeg 把 cfg.video_path 切成 3 段真 ts + 抽首帧封面。

    填充：
      - sess.image_bytes        ← 视频首帧 jpg（让 ai-cloud 识别真鸟视频内容）
      - sess.real_ts_segments   ← 3 段 mpegts 字节，cfg/code 后续会真上到 S3
      - sess.real_ts_durations_ms ← 每段精确时长（让 sliceList period 对齐）
      - sess.real_video_resolution ← e.g. "854x480"（让 uploadComplete resolution 对齐）

    需要系统装有 ffmpeg / ffprobe。视频时长 ≥9s 才能切 3 段。
    """
    import shutil as _sh, subprocess, tempfile
    if _sh.which("ffmpeg") is None or _sh.which("ffprobe") is None:
        raise PirError(
            "--video 需要系统装有 ffmpeg / ffprobe。\n"
            "  macOS: brew install ffmpeg\n"
            "  Ubuntu/Debian: sudo apt install ffmpeg"
        )
    src_str = cfg.video_path
    p = Path(src_str).expanduser()
    if not p.is_file():
        # 允许用 test_videos/ 下的素材名（仅文件名）
        candidate = TEST_VIDEOS_DIR / src_str
        if candidate.is_file():
            p = candidate
        else:
            raise PirError(f"--video 指向的文件不存在：{src_str}")
    workdir = Path(tempfile.mkdtemp(prefix="create_pir_video_"))
    sess.tmp_dirs.append(workdir)
    try:
        cover = workdir / "cover.jpg"
        # 所有 subprocess 都设 timeout，防 ffmpeg 卡死（坏视频/挂载源）拖死整个 PIR
        # 1) 抽首帧（最大宽 1280，按比例缩高）
        subprocess.run([
            "ffmpeg", "-y", "-i", str(p),
            "-vf", "scale='min(1280,iw)':-2",
            "-vframes", "1", "-q:v", "2",
            str(cover),
        ], capture_output=True, check=True, timeout=60)
        # 2) 探查封面分辨率（这就是 sliceList 的 resolution）
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "default=nw=1:nk=1", str(cover),
        ], capture_output=True, text=True, check=True, timeout=30)
        wh = probe.stdout.strip().split("\n")
        if len(wh) < 2:
            raise PirError(f"ffprobe 无法解析封面分辨率：{probe.stdout!r}")
        w, h = wh[0], wh[1]
        sess.real_video_resolution = f"{w}x{h}"
        # 3) 重编码 + 强制 keyframe + 切 3 段（每段 ~4.3s，13s 视频自然切 3-4 段）
        seg_template = workdir / "seg_%03d.ts"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(p), "-t", "12.9",
            "-vf", f"scale={w}:{h}",
            "-c:v", "libx264", "-profile:v", "main", "-preset", "fast", "-crf", "23",
            "-force_key_frames", "expr:gte(t,n_forced*4.3)",
            "-c:a", "aac", "-b:a", "64k", "-ar", "44100",
            "-f", "segment", "-segment_time", "4.3",
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            str(seg_template),
        ], capture_output=True, check=True, timeout=120)
        ts_files = sorted(workdir.glob("seg_*.ts"))
        if len(ts_files) < 3:
            raise PirError(
                f"ffmpeg 切片只生成 {len(ts_files)} 段，期望 ≥3。\n"
                f"  视频时长可能 <9 秒；需要更长的视频"
            )
        ts_files = ts_files[:3]
        for f in ts_files:
            sess.real_ts_segments.append(f.read_bytes())
            d = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(f),
            ], capture_output=True, text=True, check=True, timeout=30)
            sess.real_ts_durations_ms.append(int(float(d.stdout.strip()) * 1000))
        sess.image_bytes = cover.read_bytes()
        _log(cfg,
             f"✅ ffmpeg 切片：3 段真 ts "
             f"(durations_ms={sess.real_ts_durations_ms}, "
             f"sizes={[len(b) for b in sess.real_ts_segments]}, "
             f"resolution={sess.real_video_resolution})")
    except subprocess.TimeoutExpired as e:
        raise PirError(
            f"ffmpeg/ffprobe 卡死超过 {e.timeout}s (cmd={' '.join(e.cmd)})；"
            f"检查源文件是否完整 / 是否在慢挂载盘上"
        )
    except subprocess.CalledProcessError as e:
        raise PirError(
            f"ffmpeg 处理视频失败 (cmd={' '.join(e.cmd)})：\n"
            f"  stderr[-400:]: {(e.stderr or b'').decode('utf-8','replace')[-400:]}"
        )
    finally:
        _sh.rmtree(workdir, ignore_errors=True)


def _aws_sigv4_put(
    *, bucket: str, region: str, key: str, body: bytes, content_type: str,
    access_key: str, secret_key: str, session_token: str, timeout: int = 30,
) -> requests.Response:
    """AWS SigV4 PUT object（纯 stdlib 签名；不依赖 boto3）。

    Endpoint: https://{bucket}.s3.{region}.amazonaws.com/{key}
    """
    # AWS 中国区 endpoint 后缀是 .amazonaws.com.cn，其他 region 是 .amazonaws.com
    endpoint_suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    host = f"{bucket}.s3.{region}.{endpoint_suffix}"
    url = f"https://{host}/{key}"
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    datestamp = amz_date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    # canonical request
    headers_to_sign = {
        "content-type": content_type,
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
    }
    signed_headers = ";".join(sorted(headers_to_sign))
    canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in sorted(headers_to_sign))
    canonical_request = (
        f"PUT\n/{key}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    # string to sign
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )
    # signing key（4 次 HMAC-SHA256）
    def _hmac(k: bytes, msg: str) -> bytes:
        return hmac.new(k, msg.encode(), hashlib.sha256).digest()
    k_date = _hmac(("AWS4" + secret_key).encode(), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, "s3")
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    # authorization header
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    resp = requests.put(
        url,
        data=body,
        headers={
            "Host": host,
            "Content-Type": content_type,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "x-amz-security-token": session_token,
            "Authorization": authorization,
        },
        timeout=timeout,
    )
    return resp


def step_upload_image_to_storage(cfg: Config, sess: Session) -> None:
    """上传封面 jpg —— 两套上传方式自动分支：
    - sess.s3_bucket 有值 → AWS S3 STS SigV4 直传（CG625A1 等新设备）
    - sess.ptoken 有值   → 走业务 API 网关 PUT /videoFile/upload/p/{ptoken}/... （SS131/KF126 老设备）
    """
    img_bytes = _load_image_bytes(cfg, sess)
    if sess.s3_bucket:
        resp = _aws_sigv4_put(
            bucket=sess.s3_bucket, region=sess.s3_region,
            key=sess.image_path, body=img_bytes, content_type=_MIME_JPEG,
            access_key=sess.s3_access_key, secret_key=sess.s3_secret_key,
            session_token=sess.s3_session_token,
        )
        if resp.status_code not in (200, 201, 204):
            raise PirError(f"AWS S3 PUT 封面失败 [{resp.status_code}]: {resp.text[:300]}")
        _log(cfg, f"✅ S3 上传封面 jpg ({len(img_bytes)}B)")
        return
    url = f"{cfg.business_api}/videoFile/upload/p/{sess.ptoken}/n/pir/b/pir/o/{sess.image_path}"
    resp = requests.put(
        url,
        data=img_bytes,
        headers={
            "Content-Type": _MIME_JPEG,
            "Authorization": sess.app_token,
        },
        timeout=30,
    )
    if resp.status_code not in (200, 201, 204):
        raise PirError(f"PUT 上传封面失败 [{resp.status_code}]: {resp.text[:300]}")
    _log(cfg, f"✅ 上传封面 jpg ({len(img_bytes)}B)")


def _build_ai_image_payload(
    cfg: Config, sess: Session, order: int, is_last: int, with_box: bool
) -> dict:
    """构造 uploadAIImage 的 json 字段（multipart form 的 "json" part）。"""
    now_s = int(time.time())
    now_ms = int(time.time() * 1000)
    boxes: list = []
    if with_box and cfg.object_type in OBJECT_BOX_PRESETS:
        preset = OBJECT_BOX_PRESETS[cfg.object_type]
        boxes = [{
            "left":   preset["left"],
            "top":    preset["top"],
            "right":  preset["right"],
            "bottom": preset["bottom"],
            "name":   cfg.object_type,
            "classId": None,
            "score":  preset["score"],
            "triggered_zones_ids": [],
        }]
    elif cfg.object_type == "motion":
        boxes = [{}]          # MS 场景 motion 类型用空对象占位
    # bird / small_animal / with_box=False → boxes 保持 []
    return {
        "deviceSn": cfg.serial_number,
        "traceId":  sess.trace_id,
        "modelNo":  DEVICE_FIRMWARE_PRESETS[cfg.device_firmware_preset]["modelNo"],
        "order":    order,
        "isLast":   is_last,
        "userId":   sess.user_id or str(cfg.user_sn or ""),
        "detectedFrames": [{
            "imageOrder":         order,
            "image":              "file0",
            "timestamp":          now_s,
            "utcTimestampMillis": str(now_ms),
            "boxes":              boxes,
        }],
    }


# Sonar S3776 (cognitive complexity 16 > 15): multipart 严格对齐 Java 后端解析器
# (boxes / metadata / json file part), 微调任何分支会破坏后端兼容; 仅超阈值 1
def step_upload_ai_image(  # NOSONAR
    cfg: Config, sess: Session, order: int, is_last: int, with_box: bool
) -> None:
    """POST /videoFile/uploadAIImage/p/{ptoken} — multipart 上报 AI 图。"""
    img_bytes = _load_image_bytes(cfg, sess)
    img_name = _resolve_image_path(cfg).name
    payload = _build_ai_image_payload(cfg, sess, order, is_last, with_box)
    url = f"{cfg.business_api}/videoFile/uploadAIImage/p/{sess.ptoken}"
    # TODO: 此 endpoint 后端若也是 Java 多段解析器，data={"json":...} 这种 form
    # field 写法可能复现 step_ai_cloud_infer 在 v1.9.1 修过的 400 "Missing required
    # form data parts" bug。当前 SS131 基站链路尚未实测验证；如遇到 400 同症状，
    # 参考 step_ai_cloud_infer 把 json 段也改进 files= 即可。
    resp = requests.post(
        url,
        files={"file0": (f"frame_{order:04d}_{img_name}", img_bytes, _MIME_JPEG)},
        data={"json": json.dumps(payload, ensure_ascii=False)},
        headers={"Authorization": sess.app_token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise PirError(f"uploadAIImage 失败 [{resp.status_code}]: {resp.text[:300]}")
    tag_label = cfg.object_type if with_box else "(no box)"
    _log(cfg, f"✅ uploadAIImage order={order} isLast={is_last} tag={tag_label}")


# Sonar S3776 (cognitive complexity 16 > 15): 3 段 ts 上传需自动分支两套上传协议
# (老 ptoken / 新 S3 SigV4) + 真视频 fallback 占位封面, 仅超阈值 1
def step_upload_ts_segments(cfg: Config, sess: Session) -> None:  # NOSONAR
    """上传 3 段 ts —— 两套上传方式自动分支（同 step_upload_image_to_storage）。

    v1.10.0：如果 sess.real_ts_segments 已被 _prepare_real_video 填充（即 cfg.video_path
    非空），用 3 段真 mpegts 字节代替封面 jpg，让 KB app 可真播放。否则保持旧行为
    （封面 jpg 字节当 ts 上传，相册条目可见但视频播放黑屏）。
    """
    use_real = bool(sess.real_ts_segments)
    if use_real:
        # _prepare_real_video 已保证 ≥3 段；这里 assert 让"段数不够"立刻失败而非静默回退
        assert len(sess.real_ts_segments) >= len(sess.ts_paths), (
            f"real_ts_segments={len(sess.real_ts_segments)} < ts_paths={len(sess.ts_paths)}; "
            "应在 _prepare_real_video 阶段就报错"
        )
        bodies = sess.real_ts_segments[: len(sess.ts_paths)]
    else:
        if cfg.object_type:
            _log(cfg,
                 "⚠️  未传 --video：相册条目和 AI tag 会有，但 app 端点视频段会黑屏"
                 "（封面 jpg 字节当 ts 流喂解码器必败）。需真播放加 --video bird_a4x_1.mp4")
        img_bytes = _load_image_bytes(cfg, sess)
        bodies = [img_bytes] * len(sess.ts_paths)
    if sess.s3_bucket:
        for i, (ts_path, body) in enumerate(zip(sess.ts_paths, bodies)):
            resp = _aws_sigv4_put(
                bucket=sess.s3_bucket, region=sess.s3_region,
                key=ts_path, body=body, content_type="video/MP2T",
                access_key=sess.s3_access_key, secret_key=sess.s3_secret_key,
                session_token=sess.s3_session_token,
            )
            if resp.status_code not in (200, 201, 204):
                raise PirError(f"AWS S3 PUT ts 段 {i} 失败 [{resp.status_code}]: {resp.text[:200]}")
        kind = "真 ts" if use_real else "ts"
        sizes = [len(b) for b in bodies]
        _log(cfg, f"✅ S3 上传 3 段 {kind}（sizes={sizes}）")
        return
    for i, (ts_path, body) in enumerate(zip(sess.ts_paths, bodies)):
        url = f"{cfg.business_api}/videoFile/upload/p/{sess.ptoken}/n/pir/b/pir/o/{ts_path}"
        resp = requests.put(
            url,
            data=body,
            headers={
                "Content-Type": "video/MP2T",
                "Authorization": sess.app_token,
            },
            timeout=30,
        )
        if resp.status_code not in (200, 201, 204):
            raise PirError(f"PUT 上传 ts 段 {i} 失败 [{resp.status_code}]: {resp.text[:200]}")
    kind = "真 ts" if use_real else "ts"
    sizes = [len(b) for b in bodies]
    _log(cfg, f"✅ 上传 3 段 {kind}（sizes={sizes}）")


def step_query_retained_msg_for_aicloud(cfg: Config, sess: Session) -> None:
    """POST /deviceMsg/queryRetainedMsg names=['config'] — 拿 ai-cloud endpoint + aiCloudParam。"""
    body = {"names": ["config"]}
    data = _post_json(
        f"{cfg.device_api}/deviceMsg/queryRetainedMsg",
        body,
        headers={"Authorization": sess.device_token},
    )
    try:
        item = (data.get("data") or [])[0]
        upload_ai = (item.get("value") or {}).get("uploadAIImage") or {}
        sess.ai_cloud_endpoint = upload_ai.get("endpoint") or ""
        sess.ai_cloud_param = upload_ai.get("aiCloudParam") or ""
    except (IndexError, KeyError, TypeError) as exc:
        raise PirError(f"解析 queryRetainedMsg 响应失败：{exc}\n  raw={json.dumps(data)[:500]}")
    if not sess.ai_cloud_endpoint or not sess.ai_cloud_param:
        raise PirError(
            "retainedMsg did not return a complete ai-cloud endpoint/aiCloudParam; "
            "该设备可能未启用鸟识别/AI 推理，或 env/region 不支持。"
        )
    _log(cfg, f"✅ 取得 ai-cloud endpoint={sess.ai_cloud_endpoint}")


def step_query_device_config_for_aicloud(cfg: Config, sess: Session) -> None:
    """Request a fresh staging device config containing GeoIP location."""
    try:
        _validate_ai_location_ip(cfg.ai_location_ip, cfg.env)
        _validate_ai_device_api(cfg.device_api)
    except ValueError as exc:
        raise PirError(str(exc)) from exc
    data = _post_json(
        f"{cfg.device_api}/deviceMsg/config",
        {},
        headers={
            "Authorization": sess.device_token,
            "X-Forwarded-For": cfg.ai_location_ip,
        },
    )
    upload_ai = ((data.get("data") or {}).get("uploadAIImage") or {})
    sess.ai_cloud_endpoint = upload_ai.get("endpoint") or ""
    sess.ai_cloud_param = upload_ai.get("aiCloudParam") or ""
    if not sess.ai_cloud_endpoint or not sess.ai_cloud_param:
        raise PirError(
            "deviceMsg/config did not return a complete ai-cloud endpoint/aiCloudParam; "
            "verify that the device supports AI upload and the test IP resolves via GeoIP"
        )
    if cfg.ai_country_no:
        sess.ai_cloud_param = _override_ai_cloud_country_no(
            sess.ai_cloud_param, cfg.ai_country_no
        )
    _log(
        cfg,
        "✅ Loaded staging GeoIP ai-cloud config from deviceMsg/config "
        f"(sourceIp={cfg.ai_location_ip}, countryNo={cfg.ai_country_no or 'account'}, "
        f"endpoint={sess.ai_cloud_endpoint})",
    )


def _override_ai_cloud_country_no(encoded_param: str, country_no: str) -> str:
    """Patch field 5 while preserving every unknown AiCloudParam field."""
    try:
        from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
        from google.protobuf.message import DecodeError
    except ImportError as exc:
        raise PirError(
            "--ai-country-no requires protobuf; run pip install -r scripts/requirements.txt"
        ) from exc

    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "create_pir_ai_cloud_country.proto"
    file_proto.package = "create_pir"
    file_proto.syntax = "proto3"
    message = file_proto.message_type.add(name="AiCloudParamCountryPatch")
    message.field.add(
        name="countryNo",
        number=5,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    )
    descriptor = descriptor_pool.DescriptorPool().Add(file_proto).message_types_by_name[
        "AiCloudParamCountryPatch"
    ]
    message_class = message_factory.GetMessageClass(descriptor)
    try:
        raw = base64.b64decode(encoded_param, validate=True)
        parsed = message_class.FromString(raw)
        parsed.countryNo = country_no
        return base64.b64encode(parsed.SerializeToString()).decode("ascii")
    except (ValueError, binascii.Error, DecodeError) as exc:
        raise PirError(f"Failed to parse aiCloudParam Protobuf: {exc}") from exc


def step_load_ai_cloud_config(cfg: Config, sess: Session) -> None:
    if cfg.ai_location_ip:
        step_query_device_config_for_aicloud(cfg, sess)
        return
    step_query_retained_msg_for_aicloud(cfg, sess)


# Sonar S3776 (cognitive complexity 22 > 15): ai-cloud imageInfer 3 帧推理 +
# AWS S3 SigV4 直传 + multipart, 状态横跨 sess 多个字段, 拆分会引入序列化协议
def step_ai_cloud_infer(  # NOSONAR
    cfg: Config, sess: Session, order: int, is_last: int, with_box: bool = True
) -> None:
    """POST {ai_cloud_endpoint} — 走外部 AI 推理。

    boxes 内容随 object_type：
      - bird / small_animal: 永远空（让模型识别）
      - person/pet/vehicle/package: with_box=True 时带 OBJECT_BOX_PRESETS 坐标 + name
      - motion: [{}] 空对象占位
    """
    img_bytes = _load_image_bytes(cfg, sess)
    img_name = _resolve_image_path(cfg).name
    now_ms = int(time.time() * 1000)
    model_no = sess.display_model_no or DEVICE_FIRMWARE_PRESETS[cfg.device_firmware_preset]["modelNo"]
    boxes: list = []
    if cfg.object_type in OBJECT_TYPES_AI_INFER:
        boxes = []
    elif cfg.object_type == "motion":
        boxes = [{}]
    elif with_box and cfg.object_type in OBJECT_BOX_PRESETS:
        p = OBJECT_BOX_PRESETS[cfg.object_type]
        boxes = [{
            "left":   p["left"],   "top":    p["top"],
            "right":  p["right"],  "bottom": p["bottom"],
            "name":   cfg.object_type,
            "classId": None,
            "score":  p["score"],
            "triggered_zones_ids": [],
        }]
    payload = {
        "deviceSn": cfg.serial_number,
        "traceId":  sess.trace_id,
        "modelNo":  model_no,
        "order":    order,
        "isLast":   is_last,
        "userId":   sess.user_id or str(cfg.user_sn or ""),
        "detectedFrames": [{
            "imageOrder":         order,
            "image":              "file0",
            "timestamp":          0,
            "utcTimestampMillis": str(now_ms),
            "boxes":              boxes,
        }],
        "aiCloudParams": sess.ai_cloud_param,
    }
    # 关键：'json' 必须是 file part（带 filename + Content-Type），不是普通 form field。
    # Java 后端的多段解析按 file part 数"required parts"，form field 不算。
    # 与 device-cloud-client/_send_ai_image_inference_impl 的 multipart 格式对齐。
    resp = requests.post(
        sess.ai_cloud_endpoint,
        files={
            "json":  ("json", json.dumps(payload, ensure_ascii=False), "application/json; charset=UTF-8"),
            "file0": (f"frame_{order:04d}_{img_name}", img_bytes, _MIME_JPEG),
        },
        headers={
            "Authorization": sess.device_token,
            "Host":          urlparse(sess.ai_cloud_endpoint).netloc,
            "User-Agent":    "Apache-HttpClient/4.5.14 (Java/17.0.10)",
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise PirError(
            f"ai-cloud imageInfer 失败 [{resp.status_code}]: {resp.text[:600]}\n"
            f"  endpoint={sess.ai_cloud_endpoint}\n"
            f"  multipart parts: json (file) + file0 (file)\n"
            f"  device_token len={len(sess.device_token)}（不打印明文）"
        )
    _log(cfg, f"✅ ai-cloud imageInfer order={order} isLast={is_last}")


# Sonar S3776 (cognitive complexity 22 > 15): query AI 开关响应跨版本 (string 或 dict 或
# 嵌套 list 多种 schema) + bird/small_animal 双类型校验 + 仅 warning 不自动改用户账号
def step_check_ai_switches(cfg: Config, sess: Session) -> None:  # NOSONAR
    """bird/small_animal 链路前置检查（仅 warning，不自动改用户账号）。

    策略：query AI 开关；若检测到 bird 被关闭，打 warning 让用户自己去 App 打开。
    这样避免脚本在 prod 静默改用户账号设置。
    """
    try:
        body = {
            "app":           cfg.app_meta,
            "countryNo":     "US",
            "language":      "en",
            "includeBird":   True,
            "isAll":         False,
            "serialNumbers": [cfg.serial_number],
        }
        data = _post_json(
            f"{cfg.business_api}/aiAssist/queryEventObjectSwitch",
            body,
            headers={"Authorization": sess.app_token},
        )
        # data.data 在不同环境可能是 list of dict 或 dict{list:[]} 包装
        raw = data.get("data") or {}
        if isinstance(raw, list):
            items = raw
        else:
            items = raw.get("list") or []
        # items 内每项可能是 {name:..., enable:...} 或嵌套 [{...}]
        flat: list[dict] = []
        for it in items:
            if isinstance(it, dict):
                flat.append(it)
            elif isinstance(it, list):
                flat.extend(x for x in it if isinstance(x, dict))
        # KB 后端 queryEventObjectSwitch 返回的字段是 eventObject + checked
        # （早期 skill 误用 name + enable，导致一直误报"未开启"）
        # 兼容两种格式：优先 eventObject/checked，回退 name/enable
        def _is_bird_on(it: dict) -> bool:
            name = it.get("eventObject") or it.get("name")
            on = it.get("checked") if "checked" in it else it.get("enable")
            return name == "bird" and on is True
        bird_on = any(_is_bird_on(it) for it in flat)
        # 嵌套：data.list[].list[]（按设备分组的二级结构）
        if not bird_on:
            for it in flat:
                inner = it.get("list") or []
                if any(_is_bird_on(x) for x in inner if isinstance(x, dict)):
                    bird_on = True
                    break
        if not bird_on and flat:
            _log(cfg, "⚠️  账号的鸟识别开关似乎未开启。请在 App → 设置 → AI 检测里手动打开 bird；")
            _log(cfg, "    否则 ai-cloud 识别出来也不会打 tag。")
    except Exception as e:
        # 这是兜底检查，失败不阻塞主流程
        _log(cfg, f"⚠️  AI 开关预检跳过（{type(e).__name__}: {e}）")


def _upload_complete_body_real(cfg: Config, sess: Session) -> dict:
    """用真实路径的 uploadComplete body（新链路）。

    v1.10.0：如果走真视频链路（sess.real_ts_durations_ms 非空），sliceList 的
    period / fileSize / resolution 用切片实测值；否则按封面 jpg 大小回填（旧行为）。
    """
    preset = DEVICE_FIRMWARE_PRESETS[cfg.device_firmware_preset]
    now_ms = int(time.time() * 1000)
    use_real = bool(sess.real_ts_durations_ms)
    if use_real:
        periods = sess.real_ts_durations_ms[: len(sess.ts_paths)]
        sizes = [len(b) for b in sess.real_ts_segments[: len(sess.ts_paths)]]
        resolution = sess.real_video_resolution or preset["resolution"]
    else:
        periods = sess.slice_periods or [3991, 2933, 2999]
        sizes = [len(sess.image_bytes or b"")] * len(periods)
        resolution = preset["resolution"]
    slice_list = []
    cursor_start = now_ms - sum(periods)
    for i, (period, ts_path, sz) in enumerate(zip(periods, sess.ts_paths, sizes)):
        start_ts = cursor_start
        end_ts = cursor_start + period
        slice_list.append({
            "startRecordingTimestamp": start_ts,
            "endRecordingTimestamp":   end_ts,
            "startUploadingTimestamp": end_ts + 100,
            "endUploadingTimestamp":   end_ts + 200,
            "fileSize":                sz,
            "uploadSuccess":           1,
            "tryNum":                  0,
            "videoKey":                ts_path,
            "period":                  period,
            "order":                   i,
            "isLast":                  i == len(periods) - 1,
        })
        cursor_start = end_ts
    return {
        "serialNumber":                 cfg.serial_number,
        "version":                      preset["version"],
        "firmwareType":                 preset["firmwareType"],
        "modelNo":                      preset["modelNo"],
        "gitSha":                       preset["gitSha"],
        "imageKey":                     sess.image_path,
        "resolution":                   resolution,
        "s3AddressReceivedTimestamp":   now_ms,
        "totalStartRecordingTimestamp": slice_list[0]["startRecordingTimestamp"],
        "totalEndRecordingTimestamp":   slice_list[-1]["endRecordingTimestamp"],
        "traceId":                      sess.trace_id,
        "videoFormat":                  "ts",
        # S3 直传设备用 "s3"；老 ptoken 设备用 "bxs"
        "serviceName":                  "s3" if sess.s3_bucket else "bxs",
        "sliceList":                    slice_list,
    }


def step_video_upload_complete_real(cfg: Config, sess: Session) -> None:
    """用真实 imagePath/tsPath 的 uploadComplete（新链路）。"""
    body = _upload_complete_body_real(cfg, sess)
    data = _post_json(
        f"{cfg.business_api}/video/uploadComplete",
        body,
        headers={"Authorization": sess.app_token},
    )
    if str(data.get("result")) != "0":
        raise PirError(f"videoUploadComplete 失败: {data}")
    _log(cfg, "✅ video/uploadComplete 成功（真路径）")


def _extract_user_id_from_app_token(cfg: Config, sess: Session) -> None:
    """从 app_token 或登录响应里拿 user id。app_token 是 JWT，payload 里有 uid/id 字段。"""
    try:
        parts = sess.app_token.split(".")
        if len(parts) >= 2:
            pad = "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
            uid = payload.get("uid") or payload.get("userId") or payload.get("id") or ""
            if uid:
                sess.user_id = str(uid)
                return
    except Exception:
        pass
    # fallback：用 cfg.user_sn
    sess.user_id = str(cfg.user_sn or "")


def step_video_upload_complete(cfg: Config, sess: Session) -> None:
    body = {
        "serialNumber": cfg.serial_number,
        "version": "1.8.26",
        "firmwareType": "IN1B",
        "modelNo": "CQ121C-JS",
        "gitSha": "aa0c8d",
        "imageKey": "device_video_slice/cf27ebf1e6513899ff216fc058020900/010185771734505394XlfH4QXEMdc/image.jpg",
        "resolution": "640x360",
        "s3AddressReceivedTimestamp": 1734505395316,
        "totalStartRecordingTimestamp": 1734505393861,
        "totalEndRecordingTimestamp": 1734505403861,
        "traceId": sess.trace_id,
        "videoFormat": "ts",
        "serviceName": "oci",
        "sliceList": [
            {
                "startRecordingTimestamp": 1734505393861,
                "endRecordingTimestamp": 1734505397258,
                "startUploadingTimestamp": 1734505398593,
                "endUploadingTimestamp": 1734505404282,
                "fileSize": 135548,
                "uploadSuccess": 1,
                "tryNum": 1,
                "videoKey": "device_video_slice/cf27ebf1e6513899ff216fc058020900/010185771734505394XlfH4QXEMdc/slice_3463_0_0.ts",
                "period": 3463,
                "order": 0,
                "isLast": False,
            },
            {
                "startRecordingTimestamp": 1734505397324,
                "endRecordingTimestamp": 1734505403861,
                "startUploadingTimestamp": 1734505404286,
                "endUploadingTimestamp": 1734505404286,
                "fileSize": 275044,
                "uploadSuccess": 1,
                "tryNum": 0,
                "videoKey": "device_video_slice/cf27ebf1e6513899ff216fc058020900/010185771734505394XlfH4QXEMdc/slice_6600_1_1.ts",
                "period": 6600,
                "order": 1,
                "isLast": True,
            },
        ],
    }
    data = _post_json(
        f"{cfg.business_api}/video/uploadComplete",
        body,
        headers={"Authorization": sess.app_token},
    )
    if str(data.get("result")) != "0":
        raise PirError(f"videoUploadComplete 失败: {data}")
    _log(cfg, "✅ video/uploadComplete 成功")


# Sonar S3776 (cognitive complexity 16 > 15): 相册轮询 + tag 校验 + 超时分支,
# 仅超阈值 1; 拆分会让 trace_id 与 expected_tag 跨函数传递更繁琐
def step_verify_gallery(cfg: Config, sess: Session, expected_tag: str | None = None) -> bool:  # NOSONAR
    """轮询相册直到事件出现；若给了 expected_tag，额外检查事件 tag 是否匹配。

    注意 expected_tag=="motion" 或 bird/small_animal 的识别依赖后端异步 AI，
    可能相册条目先出现但 tag 晚点才打上，所以只当 warning 不当 fail。
    """
    start_of_day = int(
        time.mktime(
            time.strptime(time.strftime("%Y-%m-%d") + " 00:00:00", "%Y-%m-%d %H:%M:%S")
        )
    )
    end_of_day = start_of_day + 86400 - 1

    deadline = time.time() + cfg.verify_timeout_sec
    started = time.time()
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        body = {
            "app": cfg.app_meta,
            "countryNo": "US",
            "endTimestamp": end_of_day,
            "from": 0,
            "fromSDCard": False,
            "language": "en",
            "serialNumber": [],
            "serialNumberToActivityZone": {},
            "startTimestamp": start_of_day,
            "to": 500,
        }
        data = _post_json(
            f"{cfg.business_api}/library/newselectlibrary/newevent",
            body,
            headers={"Authorization": sess.app_token},
        )
        events = (data.get("data") or {}).get("list") or []
        for ev in events:
            trace_ids = ev.get("traceIds") or []
            if isinstance(trace_ids, str):
                trace_ids = [trace_ids]
            if sess.trace_id in trace_ids:
                _log(cfg, f"✅ 相册可见（尝试 {attempt} 次，耗时 {time.time()-started:.1f}s）")
                if expected_tag:
                    _verify_tag_on_event(cfg, ev, expected_tag)
                return True
        time.sleep(2)
    _log(
        cfg,
        f"⚠️  等待 {cfg.verify_timeout_sec}s 相册仍未出现 traceId={sess.trace_id}"
        "（PIR 已上报成功，服务端异步物化，后续可能出现）",
    )
    return False


# Sonar S3776 (cognitive complexity 17 > 15): tag 名称多种格式 (string/dict/list)
# 跨设备形态; 多 fallback 路径, 拆分会让格式判断与归一化分离
def _verify_tag_on_event(cfg: Config, event: dict, expected_tag: str) -> None:  # NOSONAR
    """从相册 event 对象中抽 tag 列表，和 expected_tag 对比，仅打日志不 raise。"""
    tag_candidates: list[str] = []
    for key in ("tags", "aiTags", "labelList", "labels", "detectObjects"):
        v = event.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    tag_candidates.append(item)
                elif isinstance(item, dict):
                    tag_candidates.append(str(item.get("name") or item.get("tag") or item.get("label") or ""))
        elif isinstance(v, str):
            tag_candidates.extend(s.strip() for s in v.split(",") if s.strip())
    tags_lower = [t.lower() for t in tag_candidates if t]
    if any(expected_tag.lower() in t or t in expected_tag.lower() for t in tags_lower):
        _log(cfg, f"   └─ ✅ tag 匹配 '{expected_tag}'：{tags_lower}")
    else:
        _log(
            cfg,
            f"   └─ ⚠️  未在相册条目里看到 '{expected_tag}' tag（实际：{tags_lower or '(空)'}）。"
            "AI 识别链路可能延迟到帐，或图片没被模型识别出来。",
        )


def list_user_devices(cfg: Config, sess: Session) -> list[dict]:
    """查询当前账号绑定的所有设备。需要 sess.app_token 已通过 step_login 获取。"""
    body = {"app": cfg.app_meta, "countryNo": "US", "language": "en"}
    data = _post_json(
        f"{cfg.business_api}/device/listuserdevices/v4",
        body,
        headers={"Authorization": sess.app_token},
    )
    if str(data.get("result")) != "0":
        raise PirError(f"查询设备列表失败: {data}")
    payload = data.get("data") or {}
    for k in ("list", "deviceList", "devices", "userDevices"):
        if isinstance(payload.get(k), list):
            return payload[k]
    if isinstance(payload, list):
        return payload
    for v in payload.values():
        if isinstance(v, list):
            return v
    return []


def _format_device_row(i: int, d: dict) -> str:
    sn = d.get("serialNumber") or d.get("sn") or "?"
    name = d.get("deviceName") or d.get("name") or ""
    model = d.get("modelNo") or d.get("model") or ""
    online_mark = _online_mark(d.get("online"))
    return f"  {i}) {online_mark} {model:22s} {name:18s} sn={sn}"


def _online_mark(online: object) -> str:
    if online == 1:
        return "🟢"
    if online == 0:
        return "🔴"
    return "⚪"


def cmd_list_devices(cfg: Config) -> int:
    """--list-devices 子命令入口。登录后打印设备列表。"""
    sess = Session()
    step_authenticate(cfg, sess)
    devices = list_user_devices(cfg, sess)
    if not devices:
        print("⚠️  账号下未找到绑定设备")
        return 1
    print(f"\n账号 {cfg.email} 下共 {len(devices)} 台设备：\n")
    for i, d in enumerate(devices, 1):
        print(_format_device_row(i, d))
    print()
    return 0


def _pick_device_interactive(cfg: Config, sess: Session) -> dict | None:
    """登录后列设备让用户编号选择，返回所选设备 dict。"""
    try:
        devices = list_user_devices(cfg, sess)
    except Exception as e:
        print(f"⚠️  查询设备列表失败（{e}），请手动输入", file=sys.stderr)
        return None
    if not devices:
        print("⚠️  账号下未找到绑定设备，请手动输入", file=sys.stderr)
        return None
    print(f"\n账号 {cfg.email} 下的设备（{len(devices)} 台）：", file=sys.stderr)
    for i, d in enumerate(devices, 1):
        print(_format_device_row(i, d), file=sys.stderr)
    while True:
        ans = input(f"\n选择设备编号 [1-{len(devices)}]，或回车手动输入: ").strip()
        if not ans:
            return None
        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(devices):
                return devices[idx - 1]
        print(f"  ⚠️  无效编号，请输入 1-{len(devices)} 或回车跳过", file=sys.stderr)


# Sonar S3776 (cognitive complexity 20 > 15): create_one 是设备形态/tag 类型的链路
# 调度中心 (老链路 / ss131 基站 / cg-kf 摄像头 / bird-AI), 每个分支 7+ 步 step_*
# 拆 dispatch table 会让流程顺序由 declarative 数据决定, 出错难追溯
def create_one(cfg: Config, dry_run: bool = False) -> dict:  # NOSONAR
    """跑完整创建 PIR 流程；dry_run=True 时只跑前 4 步只读验证，不产生事件。

    根据 cfg.object_type 分发：
      - 空（默认）→ 老链路（video/sliceReport + hardcoded uploadComplete）
      - person/pet/vehicle/package/motion → 硬编码 tag 链路（uploadAIImage + 真实路径上传）
      - bird/small_animal → AI 识别链路（额外调外部 ai-cloud/deeplens/imageInfer）
    """
    sess = Session()
    t0 = time.time()
    try:
        if (cfg.ai_location_ip or cfg.ai_country_no) and cfg.object_type not in OBJECT_TYPES_AI_INFER:
            raise PirError(
                "--ai-location-ip / --ai-country-no apply only to the "
                "bird / small_animal AI inference path"
            )
        # v1.11.0：image-only 时（用户传 --image 不传 --video，且 object_type 在 AI 推理类）
        # 自动把 image 扩展为 13s 静帧视频，让 KB app 视频可播放且 keyshot 异步链路有真 ts。
        # 仅对 bird/small_animal 启用（这些走 ai-cloud 真识别且最依赖完整链路）。
        if (
            cfg.image_path
            and not cfg.video_path
            and cfg.object_type in OBJECT_TYPES_AI_INFER
        ):
            try:
                static_v = _build_static_video_from_image(
                    Path(cfg.image_path).expanduser(), sess=sess
                )
                cfg.video_path = str(static_v)
                _log(cfg, f"✅ 静帧视频自动生成: {static_v.name}（13s, image-only 也可视频可播）")
            except PirError as e:
                _log(cfg, f"⚠️ 静帧视频生成跳过（{e}），降级走 image-only 链路")
        # v1.10.0：cfg.video_path 非空就先 ffmpeg 切片（fail-fast：视频/ffmpeg 有问题
        # 立刻报错，不去白白调云端 API）。封面字节存进 sess.image_bytes 复用 ai-cloud。
        if cfg.video_path:
            _prepare_real_video(cfg, sess)
        if cfg.device_auth_only:
            if not str(cfg.user_sn or "").isdigit():
                raise PirError("--device-auth-only 需要数字 --user-sn（目标用户 ID）")
            step_get_device_token(cfg, sess)
            # Device upload APIs accept the device JWT in the generic Authorization header.
            sess.app_token = sess.device_token
            sess.user_id = str(cfg.user_sn)
            _log(cfg, "✅ 仅使用设备签名鉴权，未调用 /account/login")
        else:
            step_authenticate(cfg, sess)
            _extract_user_id_from_app_token(cfg, sess)
            step_wakeup_device(cfg, sess)
            step_get_device_token(cfg, sess)
        if dry_run:
            _log(cfg, "🔍 dry-run：凭证/签名已验证通过，跳过后续写入步骤")
            return {
                "dry_run": True,
                "login_ok": not cfg.device_auth_only,
                "device_token_ok": True,
                "object_type": cfg.object_type or None,
                "elapsed_sec": round(time.time() - t0, 2),
            }
        step_device_msg_wakeup(cfg, sess)
        step_report_pir(cfg, sess)

        if not cfg.object_type:
            # 老链路（无 tag）—— 完全保留现有行为，零回归。
            step_video_slice_report(cfg, sess)
            step_video_upload_complete(cfg, sess)
        elif cfg.object_type not in OBJECT_TYPES:
            raise PirError(f"未知 object_type：{cfg.object_type!r}（支持：{OBJECT_TYPES}）")
        elif cfg.device_firmware_preset == "ss131":
            # 基站设备链路（uploadAIImage + ptoken 网关上传）
            # 来源：MeterSphere 基站项目「pir事件-vehicle/pet/package」（SS131 设备）
            step_upload_image_to_storage(cfg, sess)
            if sess.ptoken:
                step_upload_ai_image(cfg, sess, order=0, is_last=0, with_box=False)
                step_upload_ai_image(cfg, sess, order=1, is_last=1, with_box=True)
                step_upload_ai_image(cfg, sess, order=2, is_last=0, with_box=False)
            else:
                _log(cfg, "⚠️  ss131 路径但响应无 ptoken；跳过 uploadAIImage（仍上传 ts + 收尾，相册可能不打 tag）")
            step_upload_ts_segments(cfg, sess)
            step_video_upload_complete_real(cfg, sess)
        else:
            # 摄像头链路（ai-cloud imageInfer + S3 / ptoken 上传）
            # 来源：MeterSphere 方案项目「生成PIR-人/车/宠/鸟/包裹」（CG/KF 摄像头）
            # 所有 tag 类型走同一路径，object_type 只决定 boxes 内容（含 name 或 [] 让 ai-cloud 识别）。
            if cfg.object_type in OBJECT_TYPES_AI_INFER and not cfg.device_auth_only:
                step_check_ai_switches(cfg, sess)  # bird/small_animal 才需要前置开关检查
            step_upload_image_to_storage(cfg, sess)
            step_load_ai_cloud_config(cfg, sess)
            # 3 帧推理：中间一帧 with_box=True 关键帧，其余占位帧
            step_ai_cloud_infer(cfg, sess, order=0, is_last=0, with_box=False)
            step_ai_cloud_infer(cfg, sess, order=1, is_last=0, with_box=True)
            step_ai_cloud_infer(cfg, sess, order=2, is_last=1, with_box=False)
            step_upload_ts_segments(cfg, sess)
            step_video_upload_complete_real(cfg, sess)

        gallery_ok: bool | None = None if cfg.device_auth_only else True
        if cfg.verify_gallery and not cfg.device_auth_only:
            gallery_ok = step_verify_gallery(cfg, sess, expected_tag=cfg.object_type or None)
        elif cfg.verify_gallery:
            _log(cfg, "ℹ️ 设备鉴权模式跳过账号相册轮询，请用 trace_id 跟踪结果")
        return {
            "trace_id":        sess.trace_id,
            "object_type":     cfg.object_type or None,
            "elapsed_sec":     round(time.time() - t0, 2),
            "gallery_visible": gallery_ok,
        }
    finally:
        # 清理本次 create_one 注册的所有 ffmpeg 临时目录（避免批量造数据时磁盘膨胀）
        import shutil as _sh
        for d in sess.tmp_dirs:
            _sh.rmtree(d, ignore_errors=True)


# ─────────────────────────── CLI ───────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=f"创建 VicoHome PIR 事件 (v{__version__})",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
配置来源优先级：CLI > 环境变量 > .env 文件 > 内置默认

支持的环境变量 / .env 键：
  PIR_BRAND | PIR_REGION | PIR_ENV
  PIR_EMAIL | PIR_PASSWORD | PIR_DEVICE | PIR_USER_SN
  PIR_BUSINESS_API | PIR_DEVICE_API | PIR_SIGN_SECRET
  PIR_AI_LOCATION_IP (staging-only bird/small_animal GeoIP test)
  PIR_AI_COUNTRY_NO (optional staging override; requires PIR_AI_LOCATION_IP)

仅通过进程环境提供，不从 profile 读取：
  PIR_APP_TOKEN

Profile 管理（推荐，多账号切换）：
  ⓵ 首次使用：  python create_pir_event.py --init-profile vh-prod
  ⓶ 日常切换：  python create_pir_event.py --profile vh-prod
  ⓷ 查看列表：  python create_pir_event.py --list-profiles
  ⓸ 删除：      python create_pir_event.py --delete-profile <name>
  profile 存储路径：~/.config/addx/pir/<name>.env  (chmod 600)

.env 文件自动发现（未指定 --profile/--env-file 时，按以下顺序）：
  ~/.config/addx/pir.env → ./.pir.env → ./tools/.pir.env

品牌 × 区域 × 环境 组合支持（查看全部用 --show-presets）：
  vicohome:   us / eu × staging / pre / prod    (6 组，完整 MeterSphere 验证)
  kiwibit:    us ×     staging / pre / prod    (staging 验证；pre/prod 按命名推测)
  viconature: us ×     staging / pre / prod    (OEM 变体，共用 VH API，APP_META 不同)

示例：
  python tools/create_pir_event.py                                       # VH US prod（默认）
  python tools/create_pir_event.py --brand kiwibit --env staging         # KiwiBit US staging
  python tools/create_pir_event.py --brand viconature --region us --env prod
  python tools/create_pir_event.py --brand vicohome --region eu --env prod
  python tools/create_pir_event.py --count 5 --no-verify                 # 批量
  python tools/create_pir_event.py --show-config                         # 查看生效配置
  python tools/create_pir_event.py --show-presets                        # 列出所有 preset
  python tools/create_pir_event.py --email a@b.com --password p \\
      --device <sn> --save-config tools/.pir.env                         # 保存一份 .env
""",
    )

    g_env = p.add_argument_group("环境 / 品牌")
    g_env.add_argument(
        "--brand", choices=list(SUPPORTED_BRANDS), default=None,
        help="品牌（默认 vicohome）。支持 vicohome / kiwibit / viconature",
    )
    g_env.add_argument(
        "--region", choices=list(SUPPORTED_REGIONS), default=None,
        help="区域（默认 us）。部分品牌只在 us 区域",
    )
    g_env.add_argument(
        "--env", choices=list(SUPPORTED_ENVS), default=None,
        help="环境（默认 prod）。staging / pre / prod",
    )
    g_env.add_argument("--business-api", default=None, help="覆盖业务 API 域名")
    g_env.add_argument("--device-api", default=None, help="覆盖设备 API 域名")
    g_env.add_argument("--env-file", default=None, help="显式指定 .env 文件路径")

    g_auth = p.add_argument_group("账号 / 设备")
    g_auth.add_argument("--email", default=None, help="登录邮箱")
    g_auth.add_argument("--password", default=None, help="登录密码（建议用 .env 或环境变量）")
    g_auth.add_argument(
        "--device-auth-only",
        action="store_true",
        help="仅使用 PIR_SIGN_SECRET 换取设备 token，不登录账号、不改写推送注册或语言",
    )
    g_auth.add_argument("--device", default=None, help="设备 serialNumber")
    g_auth.add_argument("--user-sn", default=None, help="账号 userSn")

    g_run = p.add_argument_group("运行控制")
    g_run.add_argument("--count", type=int, default=1, help="创建多少条 PIR（默认 1）")
    g_run.add_argument("--no-verify", action="store_true", help="跳过相册可见性验证")
    g_run.add_argument("--verify-timeout", type=int, default=90, help="相册轮询超时秒数（默认 90）")
    g_run.add_argument("--quiet", action="store_true", help="只打印最终 JSON 结果，不打过程日志")
    g_run.add_argument("--no-interactive", action="store_true", help="禁用 tty 下的交互提示")
    g_run.add_argument(
        "--object-type",
        choices=list(OBJECT_TYPES),
        default=None,
        help="对象类型。默认（不指定）走老链路造空 PIR（无 tag）；"
             "person/pet/vehicle/package/motion 走硬编码 tag 链路；"
             "bird/small_animal 走 ai-cloud AI 识别链路",
    )
    g_run.add_argument(
        "--image",
        default=None,
        help="自定义识别图片；缺省用 scripts/test_images/<object_type>.jpg。"
             "若同时传 --video，--image 被忽略（封面用视频首帧）",
    )
    g_run.add_argument(
        "--video",
        default=None,
        help="真视频文件 (mp4)。提供后：封面取首帧、3 段 ts 真切片真上传，"
             "KB app 可真播放视频段。需要系统装有 ffmpeg。"
             f"也可直接用 scripts/test_videos/ 下的素材名（如 {DEFAULT_TEST_VIDEO}）",
    )
    g_run.add_argument(
        "--device-firmware-preset",
        choices=list(DEVICE_FIRMWARE_PRESETS.keys()),
        default=None,
        help=f"设备固件 preset（影响 uploadComplete 里 modelNo/firmwareType）。"
             f"缺省 {DEFAULT_DEVICE_FIRMWARE_PRESET}。可选：{list(DEVICE_FIRMWARE_PRESETS.keys())}",
    )
    g_run.add_argument(
        "--ai-location-ip",
        default=None,
        metavar="PUBLIC_IP",
        help="Staging-only bird/small_animal test: request /deviceMsg/config "
             "with this public IP to build a location-aware aiCloudParam; "
             "does not modify retained device config",
    )
    g_run.add_argument(
        "--ai-country-no",
        default=None,
        metavar="COUNTRY",
        help="Staging-only; requires --ai-location-ip. Override "
             "AiCloudParam.countryNo for this event (for example US) without "
             "modifying the account profile",
    )
    g_run.add_argument(
        "--bird-species",
        default=None,
        metavar="SPECIES",
        help="bird 类型专用：从内置图库选指定物种造数据（自动选图、自动转视频、自动 firmware preset=kf126）。"
             "可选值：robin / cardinal / chickadee / house_finch / sparrow / goldfinch / blue_jay / "
             "bluebird / titmouse / mourning_dove / random（随机选一个）",
    )
    g_run.add_argument(
        "--bulk",
        type=int,
        default=None,
        metavar="N",
        help="一次造 N 条同物种 PIR（让 KB Bird Tab keyshot 异步链路有足够样本积累，建议 ≥3）。"
             "与 --bird-species 配合使用。设 --variety 时表示每个物种造 N 条",
    )
    g_run.add_argument(
        "--variety",
        type=int,
        default=None,
        metavar="N",
        help="bird 类型专用：从内置图库选 N 个不同物种各造一批 PIR（与 --bulk 配合）。"
             "例 --variety 5 --bulk 3 = 5 种鸟 × 3 条 = 15 条 PIR",
    )
    g_run.add_argument(
        "--verify-bird-tab",
        action="store_true",
        help="造完后调 /app/birdTab/showInfo 自动验证 Bird Tab 卡片状态（visit count / keyShotCount）",
    )
    g_run.add_argument(
        "--dry-run",
        action="store_true",
        help="只做只读验证（登录 + httpToken），不上报 PIR、不写任何事件——prod 首次使用前强烈建议先跑一次",
    )
    g_run.add_argument(
        "--list-devices",
        action="store_true",
        help="登录并列出账号下所有绑定设备（sn / 型号 / 在线状态），不写事件",
    )

    g_profile = p.add_argument_group("Profile 管理（推荐，多账号切换）")
    g_profile.add_argument(
        "--profile", metavar="NAME", default=None,
        help="加载 ~/.config/addx/pir/<NAME>.env 的配置",
    )
    g_profile.add_argument(
        "--list-profiles", action="store_true", help="列出所有 profile 并退出",
    )
    g_profile.add_argument(
        "--init-profile", metavar="NAME", default=None,
        help="对话式创建一个新 profile（存到 ~/.config/addx/pir/<NAME>.env，chmod 600）",
    )
    g_profile.add_argument(
        "--delete-profile", metavar="NAME", default=None, help="删除一个已有 profile",
    )
    g_profile.add_argument(
        "--switch-device", metavar="NAME", default=None,
        help="切换已有 profile 的设备：登录该账号 → 列设备 → 选新的 → 更新 profile",
    )
    g_profile.add_argument(
        "--edit-profile", metavar="NAME", default=None,
        help="交互式编辑已有 profile 任意字段（邮箱/密码/设备等）",
    )

    g_meta = p.add_argument_group("配置管理")
    g_meta.add_argument("--show-config", action="store_true", help="打印生效配置并退出")
    g_meta.add_argument("--show-presets", action="store_true", help="列出全部 (brand, region, env) preset 并退出")
    g_meta.add_argument("--save-config", metavar="PATH", default=None, help="把当前配置保存为 .env")
    g_meta.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _print_presets() -> None:
    print("╭─ 可用 preset (brand, region, env) ─╮")
    by_brand: dict[str, list] = {}
    for (b, r, e), p in PRESETS.items():
        by_brand.setdefault(b, []).append((r, e, p))
    for b in SUPPORTED_BRANDS:
        rows = by_brand.get(b, [])
        if not rows:
            continue
        print(f"\n  {b}:")
        for r, e, p in sorted(rows):
            print(f"    --brand {b} --region {r:3s} --env {e:8s}  "
                  f"→  business={p['business_api']}  device={p['device_api']}")


def _prod_target_label(cfg: Config) -> str:
    """Describe the actual authentication target shown in the prod guard."""
    if cfg.device_auth_only:
        return f"user_sn={cfg.user_sn}"
    if cfg.app_token:
        return "PIR_APP_TOKEN 所属账号"
    return f"{cfg.email} 账号"


# Sonar S3776 (cognitive complexity 125 > 15): CLI argument dispatch + 6 个早退 subcommand
# (show_presets / list_profiles / init_profile / delete_profile / switch_device /
# edit_profile / show_config / save_config / list_devices) + bird 一键展开 + prod 二次确认
# + 批量执行循环 + 退出码处理。argparse + early-return 模式属社区共识写法; 重构成
# subcommand class 体系会让简单脚本变成框架, 与 skill "可读 standalone Python" 定位冲突
def main(argv: list[str] | None = None) -> int:  # NOSONAR
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.show_presets:
        _print_presets()
        return 0

    # Profile 管理子命令（在 resolve_config 前处理，不需要 .env）
    if args.list_profiles:
        profiles = _list_profiles()
        if not profiles:
            print(f"(暂无 profile；用 --init-profile <name> 创建，存到 {PROFILE_DIR})")
        else:
            print(f"profiles（{PROFILE_DIR}）:")
            for name in profiles:
                print(f"  {name}")
        return 0

    if args.init_profile:
        try:
            path = _init_profile_interactive(
                args.init_profile,
                brand=args.brand,
                region=args.region,
                env_name=args.env,
                email=args.email,
                password=args.password,
                device=args.device,
                user_sn=args.user_sn,
            )
            print(f"\n✅ profile 已创建：{path}")
            print(f"   下次直接用：  python {sys.argv[0]} --profile {args.init_profile}")
            return 0
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 2

    if args.delete_profile:
        try:
            path = _delete_profile(args.delete_profile)
            print(f"✅ 已删除 profile: {path}")
            return 0
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 2

    if args.switch_device:
        try:
            return cmd_switch_device(args.switch_device)
        except requests.RequestException as e:
            print(f"❌ 网络错误: {e}", file=sys.stderr)
            return 1

    if args.edit_profile:
        return cmd_edit_profile(args.edit_profile)

    try:
        cfg, env_path = resolve_config(args)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    if cfg.verbose and env_path:
        print(f"📄 加载 .env: {env_path}", file=sys.stderr)

    tty_ok = sys.stdin.isatty() and not args.no_interactive and not args.quiet
    interactive_fill(cfg, tty_ok)

    if args.show_config:
        print(json.dumps(cfg.masked(), ensure_ascii=False, indent=2))
        return 0

    if args.save_config:
        target = Path(args.save_config).expanduser()
        save_config(cfg, target)
        print(f"✅ 配置已保存到 {target} (chmod 600)")
        return 0

    if args.list_devices:
        if not cfg.password and not cfg.app_token:
            print("❌ 缺少密码或 App token。用 --password / PIR_APP_TOKEN / --profile 提供。", file=sys.stderr)
            return 2
        try:
            return cmd_list_devices(cfg)
        except (PirError, requests.RequestException) as e:
            print(f"❌ {e}", file=sys.stderr)
            return 1

    if cfg.device_auth_only and not str(args.user_sn or "").isdigit():
        print(
            "❌ --device-auth-only 需要本次命令显式提供数字 --user-sn（目标用户 ID）。",
            file=sys.stderr,
        )
        return 2

    if not cfg.password and not cfg.app_token and not cfg.device_auth_only:
        # tty 且允许交互 → 主动进入首次配置引导
        if tty_ok and not _list_profiles():
            print(
                "\n👋 看起来这是你第一次使用 create-pir。我帮你创建一个 profile 吧。",
                file=sys.stderr,
            )
            default_name = "default"
            name = input(f"  profile 名字 (默认 {default_name}): ").strip() or default_name
            try:
                path = _init_profile_interactive(
                    name,
                    brand=args.brand,
                    region=args.region,
                    env_name=args.env,
                    email=args.email,
                )
            except (ValueError, KeyboardInterrupt) as e:
                print(f"\n已取消：{e}", file=sys.stderr)
                return 2
            print(f"\n✅ profile 已创建：{path}", file=sys.stderr)
            # 重新加载配置，并继续执行造 PIR
            args.profile = name
            try:
                cfg, env_path = resolve_config(args)
            except ValueError as e:
                print(f"❌ 重新加载失败: {e}", file=sys.stderr)
                return 2
            if cfg.verbose:
                print(f"📄 加载 profile: {path}\n", file=sys.stderr)
        else:
            # 非 tty 或已有 profile 但没传 --profile
            script = sys.argv[0]
            existing = _list_profiles()
            if existing:
                print(
                    "❌ 找到了 profile 但没选择。用下列命令指定：\n",
                    file=sys.stderr,
                )
                for p in existing:
                    print(f"     python {script} --profile {p}", file=sys.stderr)
                print(file=sys.stderr)
            else:
                print(
                    "❌ 还没配置账号。首次使用请在交互终端跑：\n"
                    f"     python {script} --init-profile <名字>\n\n"
                    "  或通过环境变量 / CLI 提供凭证：\n"
                    f"     --email <> --password <> --device <>\n",
                    file=sys.stderr,
                )
            return 2

    # ───────────────────────────────────────────────────────────────────────
    # bird 一键造数据：--bird-species / --variety / --bulk 展开为造 PIR 计划
    # ───────────────────────────────────────────────────────────────────────
    BIRDS_DIR = TEST_IMAGES_DIR / "birds"
    pir_plans: list[dict] = []  # 每项 {image, species, label}
    if args.bird_species or args.variety:
        try:
            pir_plans = expand_bird_plans(
                BIRDS_DIR,
                bird_species=args.bird_species,
                variety=args.variety,
                bulk=args.bulk,
            )
        except BirdPlanError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 2
        # bird 类型自动设置：object-type=bird, firmware-preset=kf126（仅在用户未显式 CLI 指定时）
        apply_bird_auto_defaults(args, cfg)
        cfg.count = len(pir_plans)
        if cfg.verbose:
            chosen_species = sorted({p["species"] for p in pir_plans})
            bulk_used = args.bulk or 3
            print(
                f"📋 鸟一键造数据计划：{len(chosen_species)} 种 × {bulk_used} 条 = {cfg.count} 条 PIR",
                file=sys.stderr,
            )
            for p in pir_plans:
                print(f"   - {p['label']}", file=sys.stderr)

    # prod 环境写入前二次确认（--dry-run / --no-interactive 跳过）
    if cfg.env == "prod" and not args.dry_run and tty_ok:
        target_label = _prod_target_label(cfg)
        print(
            f"\n⚠️  你将在 **prod** 环境写入 {cfg.count} 条 PIR 事件到 {target_label}：\n"
            f"   - business_api: {cfg.business_api}\n"
            f"   - device_api  : {cfg.device_api}\n"
            f"   - device      : {cfg.serial_number}\n"
            "   这会产生真实的相册记录和推送通知。",
            file=sys.stderr,
        )
        confirm = input("   确认继续？输入 yes 执行，其他任意键取消：").strip().lower()
        if confirm != "yes":
            print("已取消。", file=sys.stderr)
            return 3

    results: list[dict] = []
    all_ok = True
    # 一键造数据模式：用 pir_plans 驱动；普通模式：按 cfg.count 重复
    if pir_plans:
        for i, plan in enumerate(pir_plans):
            cfg.image_path = plan["image"]
            # 切到下一条前清掉 video_path（避免上次自动生成的静帧视频被复用）
            cfg.video_path = ""
            if cfg.verbose:
                _log(cfg, f"━━━━━━━━━━ 第 {i+1}/{len(pir_plans)} 条: {plan['label']} ━━━━━━━━━━")
            try:
                res = create_one(cfg, dry_run=args.dry_run)
                res["bird_species"] = plan["species"]
                results.append(res)
                if cfg.verify_gallery and res.get("gallery_visible") is False:
                    all_ok = False
            except PirError as e:
                results.append({"error": str(e), "bird_species": plan["species"]})
                all_ok = False
            # 间隔避免 ACCOUNT_GET_KICKED
            if i < len(pir_plans) - 1:
                time.sleep(3)
    else:
        for i in range(cfg.count):
            if cfg.count > 1 and cfg.verbose:
                _log(cfg, f"━━━━━━━━━━ 第 {i+1}/{cfg.count} 条 ━━━━━━━━━━")
            try:
                res = create_one(cfg, dry_run=args.dry_run)
                results.append(res)
                if cfg.verify_gallery and res.get("gallery_visible") is False:
                    all_ok = False
            except PirError as e:
                results.append({"error": str(e)})
                all_ok = False
            except requests.HTTPError as e:
                body_preview = ""
                if e.response is not None:
                    body_preview = e.response.text[:300]
                results.append({"error": f"{e}: {body_preview}"})
                all_ok = False
            except requests.RequestException as e:
                results.append({"error": f"网络错误: {e}"})
                all_ok = False

    # ───────────────────────────────────────────────────────────────────────
    # --verify-bird-tab：造完后调 /app/birdTab/showInfo 验证 Bird Tab 卡片状态
    # ───────────────────────────────────────────────────────────────────────
    if args.verify_bird_tab and cfg.device_auth_only:
        _log(cfg, "ℹ️ 设备鉴权模式跳过 Bird Tab 账号查询，请用 trace_id 跟踪结果")
    elif args.verify_bird_tab and not args.dry_run:
        try:
            sess = Session()
            step_authenticate(cfg, sess)
            body = {"app": cfg.app_meta, "countryNo": "US", "language": "en",
                    "page": 1, "pageSize": 50}
            data = _post_json(
                f"{cfg.business_api}/app/birdTab/showInfo", body,
                headers={"Authorization": sess.app_token},
            )
            if data.get("result") == 0:
                bird_data = data.get("data") or {}
                birds = bird_data.get("list") or []
                print("\n📊 Bird Tab 验证（/app/birdTab/showInfo）", file=sys.stderr)
                print(f"   total={bird_data.get('total', 0)} 张鸟种卡片", file=sys.stderr)
                for bird in birds:
                    std = bird.get("birdStdName") or "?"
                    name = bird.get("birdName") or "(无本地名)"
                    visits = bird.get("visitedTimes", 0)
                    ks = bird.get("keyShotCount", 0)
                    print(
                        f"   • {std} ({name})  visits={visits}  keyShotCount={ks}"
                        f"{'  ✅有keyshot' if ks > 0 else '  ⏳ 等异步'}",
                        file=sys.stderr,
                    )
            else:
                print(f"⚠️ Bird Tab 验证失败: {data.get('msg')}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️ Bird Tab 验证跳过: {type(e).__name__}: {e}", file=sys.stderr)

    print(json.dumps({"ok": all_ok, "results": results}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
