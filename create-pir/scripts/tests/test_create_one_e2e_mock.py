"""端到端 contract 测试：create_one(--dry-run) 完整 4 步链路（mock HTTP）。

覆盖 AI code-review P0 红线"缺真实依赖 E2E 自动化覆盖"——
真实 prod E2E 因为需要真账号、真设备、写真数据到用户相册，无法在 CI 自动化；
本测试用 unittest.mock 拦截 `_post_json`，按真实 MeterSphere 录制响应回放，验证：

  - dry_run 链路 4 步严格契约：login → extract_uid → wakeupDevice → httpToken
  - 各 step 请求 payload 关键字段（serialNumber / signature / time）
  - signature 真按 HMAC-SHA1(secret, serial+ts) 算，不是占位
  - JWT app_token 解析提取 user_id 真生效
  - HTTP 错误（result≠0 / msg!=success）正确传播为 PirError，不静默吞错

注意：完整造 PIR 链路（report → uploadImage → AI infer → uploadComplete）
状态依赖太重（S3 SigV4 签名、ts 切片、ai-cloud 多帧推理），mock 会过度
拟合而失去契约价值。dry_run 链路 = 用户文档明示的 prod 预检路径，覆盖
登录/签名/JWT 三大关键安全路径，是契约可验证的核心面。

不打云端，不读真账号，无需 ffmpeg。

跑：python3 -m pytest scripts/tests/test_create_one_e2e_mock.py -v
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_pir_event as cpe  # noqa: E402

# ── 测试用的 fake JWT（payload userId=12345）───────────────────────────────
_FAKE_JWT_PAYLOAD = base64.urlsafe_b64encode(
    json.dumps({"userId": 12345, "tenantId": "vicoo"}).encode()
).decode().rstrip("=")
_FAKE_APP_TOKEN = f"Bearer eyJhbGciOiJIUzI1NiJ9.{_FAKE_JWT_PAYLOAD}.fakesig"
_FAKE_DEVICE_TOKEN = "Bearer fake-device-jwt-token"
_FAKE_SECRET_B64 = "ZmFrZXNlY3JldA=="  # base64('fakesecret')


@pytest.fixture
def fake_secret(monkeypatch):
    monkeypatch.setenv("PIR_SIGN_SECRET", _FAKE_SECRET_B64)
    cpe.DEVICE_SIGN_SECRET_B64 = _FAKE_SECRET_B64


def _make_cfg():
    return cpe.Config(
        brand="vicohome", region="us", env="prod",
        business_api="https://api.example.io",
        device_api="https://device.example.io",
        email="t@a4x.io", password="x", serial_number="SN001", user_sn="US001",
        count=1, verify_gallery=False, verbose=False,
        app_meta={"appBuild": "1", "version": 1, "versionName": "1", "env": "prod"},
        object_type="", image_path="", video_path="",
        device_firmware_preset="cx-cq121c",
    )


def _canned_dry_run_response(url: str) -> dict:
    """按 URL 返回 dry_run 4 步真实 MeterSphere 录制下来的响应骨架。"""
    if url.endswith("/account/login"):
        return {
            "msg": "success",
            "data": {"token": {"token": _FAKE_APP_TOKEN}, "userSn": "US001"},
            "result": 0,
        }
    if url.endswith("/wakeupDevice"):
        return {"msg": "success", "data": {}, "result": 0}
    if url.endswith("/deviceMsg/httpToken"):
        return {
            "msg": "success",
            "data": {"value": {"token": _FAKE_DEVICE_TOKEN}},
            "result": 0,
        }
    return {"msg": "success", "data": {}, "result": 0}


# ─────────────────────────── 链路顺序契约 ───────────────────────────


def test_dry_run_calls_exactly_four_endpoints_in_order(fake_secret):
    """dry_run 严格走 login → wakeupDevice → httpToken；其余步骤不许触发。"""
    calls: list[tuple[str, dict]] = []

    def fake_post(url, body=None, headers=None, timeout=15):
        calls.append((url, body or {}))
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    with patch.object(cpe, "_post_json", side_effect=fake_post):
        result = cpe.create_one(cfg, dry_run=True)

    assert result == {
        "dry_run": True,
        "login_ok": True,
        "device_token_ok": True,
        "object_type": None,
        "elapsed_sec": pytest.approx(result["elapsed_sec"]),  # 取自身
    }

    urls = [c[0] for c in calls]
    # 顺序必须严格：login 先、wakeupDevice 次之、httpToken 末
    login_idx = next(i for i, u in enumerate(urls) if u.endswith("/account/login"))
    wakeup_idx = next(i for i, u in enumerate(urls) if u.endswith("/wakeupDevice"))
    httptoken_idx = next(i for i, u in enumerate(urls) if u.endswith("/deviceMsg/httpToken"))
    assert login_idx < wakeup_idx < httptoken_idx

    # 必须没调到任何 dry_run 之后的 step
    forbidden = ["/deviceMsg/wakeup", "/pir/event/report", "/video/sliceReport",
                 "/uploadImage", "/uploadComplete", "/imageInfer"]
    for u in urls:
        assert not any(f in u for f in forbidden), f"dry_run 不该触发 {u}"


def test_existing_app_token_skips_account_login(fake_secret):
    """Push validation reuses the active App session without rewriting login state."""
    calls: list[str] = []

    def fake_post(url, body=None, headers=None, timeout=15):
        calls.append(url)
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    cfg.app_token = _FAKE_APP_TOKEN
    with patch.object(cpe, "_post_json", side_effect=fake_post), \
         patch.object(cpe, "step_login") as login:
        result = cpe.create_one(cfg, dry_run=True)

    login.assert_not_called()
    assert result["login_ok"] is True
    assert not any(url.endswith("/account/login") for url in calls)
    assert any(url.endswith("/wakeupDevice") for url in calls)
    assert any(url.endswith("/deviceMsg/httpToken") for url in calls)


def test_device_auth_only_skips_account_login_and_app_wakeup(fake_secret):
    """Synthetic push validation can authenticate only as the device."""
    calls: list[str] = []

    def fake_post(url, body=None, headers=None, timeout=15):
        calls.append(url)
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    cfg.password = ""
    cfg.user_sn = "12345"
    cfg.device_auth_only = True
    with patch.object(cpe, "_post_json", side_effect=fake_post), \
         patch.object(cpe, "step_login") as login:
        result = cpe.create_one(cfg, dry_run=True)

    login.assert_not_called()
    assert result["login_ok"] is False
    assert calls == ["https://device.example.io/deviceMsg/httpToken"]


def test_device_auth_only_non_dry_run_uses_device_token_for_write_apis(fake_secret):
    """Every write after device authentication uses the returned device token."""
    calls: list[tuple[str, dict | None]] = []

    def fake_post(url, body=None, headers=None, timeout=15):
        calls.append((url, headers))
        if url.endswith("/deviceMsg/httpToken"):
            return {
                "result": 0,
                "data": {"value": {"token": _FAKE_DEVICE_TOKEN}},
            }
        if url.endswith("/deviceMsg/wakeup"):
            return {"result": 0, "data": {"value": {"traceId": "trace-123"}}}
        if url.endswith("/deviceMsg/pir"):
            return {
                "result": 0,
                "data": {"value": {"traceId": "trace-123", "serialNumber": "SN001"}},
            }
        return {"result": 0, "data": {}}

    cfg = _make_cfg()
    cfg.password = ""
    cfg.user_sn = "12345"
    cfg.device_auth_only = True

    with patch.object(cpe, "_post_json", side_effect=fake_post), \
         patch.object(cpe, "step_login") as login:
        result = cpe.create_one(cfg)

    login.assert_not_called()
    assert result["trace_id"] == "trace-123"
    assert [url for url, _ in calls] == [
        "https://device.example.io/deviceMsg/httpToken",
        "https://device.example.io/deviceMsg/wakeup",
        "https://device.example.io/deviceMsg/pir",
        "https://api.example.io/video/sliceReport",
        "https://api.example.io/video/uploadComplete",
    ]
    assert calls[0][1] is None
    for _, headers in calls[1:]:
        assert headers == {"Authorization": _FAKE_DEVICE_TOKEN}


def test_device_auth_only_bird_path_uses_device_identity(fake_secret):
    """The documented Bird path skips account checks and keeps device auth for uploads."""
    observed_auth: list[tuple[str, str]] = []

    def fake_device_token(_cfg, sess):
        sess.device_token = _FAKE_DEVICE_TOKEN

    def fake_device_wakeup(_cfg, sess):
        sess.trace_id = "trace-bird"

    def record_auth(_cfg, sess, *args, **kwargs):
        observed_auth.append((sess.app_token, sess.device_token))

    cfg = _make_cfg()
    cfg.password = ""
    cfg.user_sn = "12345"
    cfg.device_auth_only = True
    cfg.object_type = "bird"

    with patch.object(cpe, "step_get_device_token", side_effect=fake_device_token), \
         patch.object(cpe, "step_device_msg_wakeup", side_effect=fake_device_wakeup), \
         patch.object(cpe, "step_report_pir"), \
         patch.object(cpe, "step_check_ai_switches") as check_ai_switches, \
         patch.object(cpe, "step_upload_image_to_storage"), \
         patch.object(cpe, "step_load_ai_cloud_config"), \
         patch.object(cpe, "step_ai_cloud_infer", side_effect=record_auth) as ai_infer, \
         patch.object(cpe, "step_upload_ts_segments"), \
         patch.object(cpe, "step_video_upload_complete_real", side_effect=record_auth), \
         patch.object(cpe, "step_login") as login:
        result = cpe.create_one(cfg)

    login.assert_not_called()
    check_ai_switches.assert_not_called()
    assert result["trace_id"] == "trace-bird"
    assert ai_infer.call_count == 3
    assert observed_auth == [(_FAKE_DEVICE_TOKEN, _FAKE_DEVICE_TOKEN)] * 4


def test_device_auth_only_requires_numeric_user_id(fake_secret):
    cfg = _make_cfg()
    cfg.device_auth_only = True
    cfg.user_sn = "US001"

    with pytest.raises(cpe.PirError, match="数字 --user-sn"):
        cpe.create_one(cfg, dry_run=True)


def test_main_device_auth_only_rejects_user_id_from_profile_or_env(monkeypatch, capsys):
    """A stored user ID cannot silently select the push recipient."""
    cfg = _make_cfg()
    cfg.password = ""
    cfg.user_sn = "12345"
    cfg.device_auth_only = True

    monkeypatch.setattr(cpe, "resolve_config", lambda _args: (cfg, None))
    with patch.object(cpe, "create_one") as create_one:
        exit_code = cpe.main(["--device-auth-only", "--no-interactive", "--quiet"])

    assert exit_code == 2
    assert "本次命令显式提供数字 --user-sn" in capsys.readouterr().err
    create_one.assert_not_called()


def test_device_auth_only_marks_gallery_as_unverified(fake_secret):
    cfg = _make_cfg()
    cfg.device_auth_only = True
    cfg.user_sn = "12345"
    cfg.verify_gallery = True

    def fake_device_token(_cfg, sess):
        sess.device_token = _FAKE_DEVICE_TOKEN

    def fake_device_wakeup(_cfg, sess):
        sess.trace_id = "trace-123"

    with patch.object(cpe, "step_get_device_token", side_effect=fake_device_token), \
         patch.object(cpe, "step_device_msg_wakeup", side_effect=fake_device_wakeup), \
         patch.object(cpe, "step_report_pir"), \
         patch.object(cpe, "step_video_slice_report"), \
         patch.object(cpe, "step_video_upload_complete"), \
         patch.object(cpe, "step_verify_gallery") as verify_gallery:
        result = cpe.create_one(cfg)

    verify_gallery.assert_not_called()
    assert result["gallery_visible"] is None


def test_prod_target_label_matches_authentication_source():
    cfg = _make_cfg()
    assert cpe._prod_target_label(cfg) == "t@a4x.io 账号"

    cfg.app_token = _FAKE_APP_TOKEN
    assert cpe._prod_target_label(cfg) == "PIR_APP_TOKEN 所属账号"

    cfg.device_auth_only = True
    cfg.user_sn = "12345"
    assert cpe._prod_target_label(cfg) == "user_sn=12345"


# ─────────────────────────── payload 字段契约 ───────────────────────────


def test_dry_run_login_payload_has_required_fields(fake_secret):
    payloads: dict[str, dict] = {}

    def fake_post(url, body=None, headers=None, timeout=15):
        payloads.setdefault(url, body or {})
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    with patch.object(cpe, "_post_json", side_effect=fake_post):
        cpe.create_one(cfg, dry_run=True)

    login_url = next(u for u in payloads if u.endswith("/account/login"))
    body = payloads[login_url]
    assert body.get("email") == "t@a4x.io"
    assert body.get("password") == "x"
    assert "app" in body and isinstance(body["app"], dict)


def test_dry_run_signature_is_real_hmac_sha1(fake_secret):
    """httpToken 请求体里的 signature 必须是 HMAC-SHA1(secret, serial+time) base64-urlsafe，
    不能是占位字符串。回归 PIR_SIGN_SECRET 注入链路。"""
    payloads: dict[str, dict] = {}

    def fake_post(url, body=None, headers=None, timeout=15):
        payloads.setdefault(url, body or {})
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    with patch.object(cpe, "_post_json", side_effect=fake_post):
        cpe.create_one(cfg, dry_run=True)

    httptoken_url = next(u for u in payloads if u.endswith("/deviceMsg/httpToken"))
    body = payloads[httptoken_url]
    serial, ts, sig = body["serialNumber"], body["time"], body["signature"]

    # 复算同一个 signature；脚本和测试必须一致
    expected = hmac.new(
        base64.b64decode(_FAKE_SECRET_B64),
        f"{serial}{ts}".encode(),
        hashlib.sha1,
    ).digest()
    expected_b64 = base64.b64encode(expected).decode("ascii").replace("+", "-").replace("/", "_")
    assert sig == expected_b64, f"签名不匹配：脚本={sig} 期望={expected_b64}"
    assert body["name"] == "httpToken"
    assert body["id"] == 4


def test_dry_run_app_token_jwt_user_id_extracted(fake_secret):
    """JWT app_token 解析后 sess.user_id 必须从 payload 拿到 12345。"""
    captured = {}

    def fake_post(url, body=None, headers=None, timeout=15):
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    real_extract = cpe._extract_user_id_from_app_token

    def spy_extract(cfg_arg, sess_arg):
        real_extract(cfg_arg, sess_arg)
        captured["user_id"] = getattr(sess_arg, "user_id", None)

    with patch.object(cpe, "_post_json", side_effect=fake_post), \
         patch.object(cpe, "_extract_user_id_from_app_token", side_effect=spy_extract):
        cpe.create_one(cfg, dry_run=True)

    assert captured.get("user_id") == "12345"


# ─────────────────────────── HTTP 错误传播契约 ───────────────────────────


def test_login_failure_raises_pir_error(fake_secret):
    """登录返回 msg!=success → PirError，不静默吞错。"""
    def fake_post(url, body=None, headers=None, timeout=15):
        if url.endswith("/account/login"):
            return {"msg": "fail", "data": {"errorCode": "INVALID_PASSWORD"}, "result": -1}
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    with patch.object(cpe, "_post_json", side_effect=fake_post):
        with pytest.raises(cpe.PirError, match="登录失败"):
            cpe.create_one(cfg, dry_run=True)


def test_device_token_invalid_signature_raises_pir_error(fake_secret):
    """httpToken result≠0 → PirError（最常见的 invalid signature）。"""
    def fake_post(url, body=None, headers=None, timeout=15):
        if url.endswith("/deviceMsg/httpToken"):
            return {"result": -1, "msg": "invalid signature"}
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    with patch.object(cpe, "_post_json", side_effect=fake_post):
        with pytest.raises(cpe.PirError, match="httpToken 失败"):
            cpe.create_one(cfg, dry_run=True)


def test_dry_run_aborts_when_pir_sign_secret_missing(monkeypatch):
    """没设 PIR_SIGN_SECRET 时，签名步骤抛 PirError 含明确指引。"""
    monkeypatch.delenv("PIR_SIGN_SECRET", raising=False)
    cpe.DEVICE_SIGN_SECRET_B64 = ""

    def fake_post(url, body=None, headers=None, timeout=15):
        return _canned_dry_run_response(url)

    cfg = _make_cfg()
    with patch.object(cpe, "_post_json", side_effect=fake_post):
        with pytest.raises(cpe.PirError, match="PIR_SIGN_SECRET"):
            cpe.create_one(cfg, dry_run=True)
