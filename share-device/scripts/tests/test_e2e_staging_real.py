"""真依赖 E2E 测试：跑完整 list 链路打 staging API（默认跳过）。

回应 AI code-review P0 红线"已实现功能未提供自动化 E2E 测试资产"——
真实 prod E2E 因为需要双账号 + 真设备 + 修真分享关系，CI 不合适；
本 opt-in 测试默认跳过，提供 staging 凭证 env 时启用。

```bash
# 通过 PIR-style profile 启用（推荐，与 create-pir 互通）
PIR_E2E_STAGING_ADMIN_PROFILE=share-admin-staging \
PIR_E2E_STAGING_SHARER_PROFILE=share-sharer-staging \
  python3 -m pytest scripts/tests/test_e2e_staging_real.py -v

# 或显式给所有变量
PIR_E2E_STAGING_BUSINESS_API=https://api-staging-us.kiwibit.com \
PIR_E2E_STAGING_BRAND=kiwibit \
PIR_E2E_STAGING_ADMIN_EMAIL=test-admin@a4x.io \
PIR_E2E_STAGING_ADMIN_PASSWORD=xxx \
PIR_E2E_STAGING_SHARER_EMAIL=test-sharer@a4x.io \
PIR_E2E_STAGING_SHARER_PASSWORD=yyy \
PIR_E2E_STAGING_DEVICE=SN001 \
  python3 -m pytest scripts/tests/test_e2e_staging_real.py -v
```

为什么不在主 CI 跑：
  1. 真账号凭证不能进 CI secret store（合规风险）
  2. 写真分享关系到 staging 测试账号（即便能 reset，每次 CI 都跑会污染列表）
  3. 网络抖动 / staging 维护窗口 → flaky CI

本测试只跑 list (read-only) — share/unshare 修真状态的部分由 reviewer / maintainer
本机手工测试，trace 留在 CHANGELOG / SKILL.md 实证表里。

跑：
  PIR_E2E_STAGING_ADMIN_PROFILE=<n> python3 -m pytest scripts/tests/test_e2e_staging_real.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import share_device as sd  # noqa: E402


def _has_real_creds() -> bool:
    if os.getenv("PIR_E2E_STAGING_ADMIN_PROFILE"):
        return True
    required = [
        "PIR_E2E_STAGING_BUSINESS_API",
        "PIR_E2E_STAGING_ADMIN_EMAIL",
        "PIR_E2E_STAGING_ADMIN_PASSWORD",
        "PIR_E2E_STAGING_DEVICE",
    ]
    return all(os.getenv(k) for k in required)


pytestmark = pytest.mark.skipif(
    not _has_real_creds(),
    reason=(
        "默认跳过；设 PIR_E2E_STAGING_ADMIN_PROFILE=<name> 或 "
        "PIR_E2E_STAGING_{BUSINESS_API,ADMIN_EMAIL,ADMIN_PASSWORD,DEVICE} "
        "启用真依赖 staging E2E 测试"
    ),
)


def _build_real_cfg() -> sd.Config:
    profile = os.getenv("PIR_E2E_STAGING_ADMIN_PROFILE")
    sharer_profile = os.getenv("PIR_E2E_STAGING_SHARER_PROFILE")
    if profile:
        # 用 build_parser → resolve_config 走脚本自身的 profile 加载链路（最忠实）
        argv = ["--profile", profile, "--action", "list", "--quiet", "--no-verify"]
        if sharer_profile:
            argv.extend(["--sharer-profile", sharer_profile])
        args = sd.build_parser().parse_args(argv)
        cfg = sd.resolve_config(args)
        return cfg
    api_preset, app_meta = sd.resolve_preset(
        os.getenv("PIR_E2E_STAGING_BRAND", "kiwibit"),
        os.getenv("PIR_E2E_STAGING_REGION", "us"),
        "staging",
    )
    return sd.Config(
        brand=os.getenv("PIR_E2E_STAGING_BRAND", "kiwibit"),
        region=os.getenv("PIR_E2E_STAGING_REGION", "us"),
        env_name="staging",
        business_api=os.environ["PIR_E2E_STAGING_BUSINESS_API"],
        app_meta=app_meta,
        admin_email=os.environ["PIR_E2E_STAGING_ADMIN_EMAIL"],
        admin_password=os.environ["PIR_E2E_STAGING_ADMIN_PASSWORD"],
        sharer_email=os.getenv("PIR_E2E_STAGING_SHARER_EMAIL", ""),
        sharer_password=os.getenv("PIR_E2E_STAGING_SHARER_PASSWORD", ""),
        serial_number=os.environ["PIR_E2E_STAGING_DEVICE"],
        action="list",
        no_verify=True, verbose=False,
    )


def test_real_staging_list_admin_view():
    """跑真 staging API 的 admin 视角 list (read-only)。

    期望：登录 admin → sharestatus 返回结构合法。任何一步失败（凭证错、域名错、
    设备不属于 admin）都会抛 ShareError。
    """
    cfg = _build_real_cfg()
    sess = sd.Session()
    sd.step_login_admin(cfg, sess)
    assert sess.admin_token, "登录 admin 必须拿到 token"
    view = sd.step_admin_share_status(cfg, sess)
    assert "items" in view
    assert isinstance(view["items"], list)


def test_real_staging_signature_works_against_live_api():
    """信号性测试：admin 登录 + sharestatus 对真 staging 域名生效。

    专门隔离凭证 + 域名链路（最常见踩坑点）。
    """
    cfg = _build_real_cfg()
    sess = sd.Session()
    sd.step_login_admin(cfg, sess)
    assert sess.admin_token.startswith(("Bearer ", "eyJ", "ey")), \
        f"admin_token 看起来不像 JWT: {sess.admin_token[:40]}"
    assert sess.admin_user_id is not None, "JWT 必须能解析出 admin userId"
