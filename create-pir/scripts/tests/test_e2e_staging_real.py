"""真依赖 E2E 测试：跑完整 dry_run 链路打 staging API（默认跳过）。

直接回应 AI code-review P0 红线"缺真实依赖 E2E 自动化覆盖"。

**默认跳过** —— CI 不会跑这套。需要时通过环境变量启用：

```bash
# 用已配好的 staging profile 跑
PIR_E2E_STAGING_PROFILE=my-vh-staging \
  python3 -m pytest scripts/tests/test_e2e_staging_real.py -v

# 或显式给所有变量（profile 没配时）
PIR_E2E_STAGING_BUSINESS_API=https://api-staging-us.vicohome.io \
PIR_E2E_STAGING_DEVICE_API=https://api-staging-us.vicohome.io \
PIR_E2E_STAGING_EMAIL=test@a4x.io \
PIR_E2E_STAGING_PASSWORD=xxx \
PIR_E2E_STAGING_DEVICE=SN001 \
PIR_E2E_STAGING_USER_SN=US001 \
PIR_E2E_STAGING_SIGN_SECRET=base64xx \
  python3 -m pytest scripts/tests/test_e2e_staging_real.py -v
```

为什么不在主 CI 跑：
  1. 真账号凭证不能进 CI secret store（合规风险）
  2. 每次跑都污染 staging 测试账号相册（虽然 dry_run 不写事件，但产生登录日志）
  3. 网络抖动 / staging 维护窗口 → flaky CI

为什么提供这个测试：
  1. **回归门禁**：每次脚本改 prod 写链路前，开发者本机跑这套验证 staging 真链路通
  2. **契约证据**：reviewer 可以本机跑这套确认链路真在工作
  3. **释放路径**：将来 staging 凭证可以进单独 manual job（`when: manual` GitLab CI），
     由 maintainer 手工触发回归

跑：
  PIR_E2E_STAGING_PROFILE=my-vh-staging python3 -m pytest scripts/tests/test_e2e_staging_real.py -v
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_pir_event as cpe  # noqa: E402


def _has_real_creds() -> bool:
    """检测是否提供了 staging 凭证：profile 形式或 PIR_E2E_STAGING_* 全套。"""
    if os.getenv("PIR_E2E_STAGING_PROFILE"):
        return True
    required = [
        "PIR_E2E_STAGING_BUSINESS_API",
        "PIR_E2E_STAGING_DEVICE_API",
        "PIR_E2E_STAGING_EMAIL",
        "PIR_E2E_STAGING_PASSWORD",
        "PIR_E2E_STAGING_DEVICE",
        "PIR_E2E_STAGING_SIGN_SECRET",
    ]
    return all(os.getenv(k) for k in required)


pytestmark = pytest.mark.skipif(
    not _has_real_creds(),
    reason=(
        "默认跳过；设 PIR_E2E_STAGING_PROFILE=<name> 或 "
        "PIR_E2E_STAGING_{BUSINESS_API,DEVICE_API,EMAIL,PASSWORD,DEVICE,SIGN_SECRET} "
        "启用真依赖 staging E2E 测试"
    ),
)


def _build_real_cfg() -> cpe.Config:
    """从环境变量构造真凭证 cfg；优先 profile 形式。"""
    profile = os.getenv("PIR_E2E_STAGING_PROFILE")
    if profile:
        # 从 profile 文件加载（脚本 _discover_env_file 已支持）
        args = cpe.build_parser().parse_args([
            "--profile", profile, "--quiet",
            "--no-verify", "--no-interactive",
        ])
        cfg, _ = cpe.resolve_config(args)
        return cfg
    # 直接显式
    cfg = cpe.Config(
        brand=os.getenv("PIR_E2E_STAGING_BRAND", "vicohome"),
        region=os.getenv("PIR_E2E_STAGING_REGION", "us"),
        env="staging",
        business_api=os.environ["PIR_E2E_STAGING_BUSINESS_API"],
        device_api=os.environ["PIR_E2E_STAGING_DEVICE_API"],
        email=os.environ["PIR_E2E_STAGING_EMAIL"],
        password=os.environ["PIR_E2E_STAGING_PASSWORD"],
        serial_number=os.environ["PIR_E2E_STAGING_DEVICE"],
        user_sn=os.getenv("PIR_E2E_STAGING_USER_SN", ""),
        verify_gallery=False,
        verbose=False,
        app_meta={"appBuild": "1", "version": 1, "versionName": "1", "env": "staging"},
        device_firmware_preset="cx-cq121c",
    )
    cpe.DEVICE_SIGN_SECRET_B64 = os.environ["PIR_E2E_STAGING_SIGN_SECRET"]
    return cfg


# ─────────────────────────── 真依赖 dry_run E2E ───────────────────────────


def test_real_staging_dry_run_full_chain():
    """跑真 staging API 的 dry_run（4 步只读）：login → wakeupDevice → httpToken。

    期望：返回 dry_run=True / login_ok=True / device_token_ok=True，无异常。
    任何一步失败（凭证错、签名错、域名错、网络错）都会抛 PirError。
    """
    cfg = _build_real_cfg()
    result = cpe.create_one(cfg, dry_run=True)
    assert result["dry_run"] is True
    assert result["login_ok"] is True
    assert result["device_token_ok"] is True
    assert result["elapsed_sec"] > 0


def test_real_staging_signature_works_against_live_device_api():
    """信号性测试：单独验证签名 + httpToken 这一步对真 staging 域名生效。

    专门隔离签名链路（最常见踩坑点：secret 错 / device_api 错 / 域名跨租户）。
    """
    cfg = _build_real_cfg()
    sess = cpe.Session()
    cpe.step_login(cfg, sess)
    cpe._extract_user_id_from_app_token(cfg, sess)
    cpe.step_wakeup_device(cfg, sess)
    cpe.step_get_device_token(cfg, sess)
    assert sess.app_token, "登录后必须拿到 app_token"
    assert sess.device_token, "签名 + httpToken 后必须拿到 device_token"
    assert sess.device_token.startswith(("Bearer ", "eyJ", "ey")), \
        f"device_token 看起来不像 JWT: {sess.device_token[:40]}"
