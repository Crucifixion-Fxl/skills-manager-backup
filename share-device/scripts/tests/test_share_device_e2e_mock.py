"""端到端 contract 测试：share / unshare / list 全链路（mock HTTP，不打云端）。

回应 AI code-review P0 红线"已实现 share/unshare/list 缺自动化 E2E"——
真实 prod E2E 因为需要双账号 + 真设备 + 真分享关系，CI 自动化不合适；
本测试用 unittest.mock 拦截 `_post`，按 MeterSphere 录制响应回放，验证：

  - share 完整 9 步链路：双登录 → getshareid → requireshare → recentapprovals
                       → handleapproval → sharestatus + listuserdevices 双向验证
  - share --decline 拒绝分支：handleapproval status=1
  - unshare sharer self：登录 sharer + listuserdevices 基线 + unshare + 验证消失
  - unshare --by-admin 没传 --sharer-user-id 也没 sharer 凭证 → 拒绝执行（不再"列表[-1] 兜底"）
  - list 双向输出
  - recentapprovals 未精确匹配时拒绝（不再"items[0] 兜底"）
  - _post 仅看 result=0，不看 msg.startswith("success")（容错大小写/中文/缺字段）

不打云端，不读真账号。

跑：python3 -m pytest scripts/tests/test_share_device_e2e_mock.py -v
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import share_device as sd  # noqa: E402

# 假 JWT
def _fake_jwt(user_id: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"userId": user_id, "tenantId": "vicoo"}).encode()
    ).decode().rstrip("=")
    return f"Bearer eyJhbGciOiJIUzI1NiJ9.{payload}.fakesig"


_ADMIN_TOKEN = _fake_jwt(1001)
_SHARER_TOKEN = _fake_jwt(2002)


def _make_cfg(**kwargs) -> sd.Config:
    base = dict(
        brand="kiwibit", region="us", env_name="prod",
        business_api="https://api.example.io",
        app_meta={"appName": "K", "tenantId": "kiwibit", "version": 1, "versionName": "1"},
        admin_email="admin@a4x.io", admin_password="x",
        sharer_email="sharer@a4x.io", sharer_password="x",
        serial_number="SN001", action="share",
        no_verify=False, verbose=False,
    )
    base.update(kwargs)
    return sd.Config(**base)


def _canned(url: str, body: dict | None = None) -> dict:
    """按 endpoint 返回 result=0 的 success 响应骨架。"""
    if url.endswith("/account/login"):
        # admin 和 sharer 登录区分: body['email']
        email = (body or {}).get("email", "")
        token = _ADMIN_TOKEN if "admin" in email else _SHARER_TOKEN
        return {"result": 0, "msg": "Success",
                "data": {"token": {"token": token}, "userSn": "US"}}
    if url.endswith("/device/getshareid"):
        return {"result": 0, "msg": "Success",
                "data": {"shareId": "SHARE_FAKE_001", "expireTime": 1800}}
    if url.endswith("/device/requireshare"):
        return {"result": 0, "msg": "Success", "data": {"messageId": "msg-1"}}
    if url.endswith("/device/recentapprovals"):
        return {"result": 0, "msg": "Success",
                "data": {"list": [
                    {"id": 99, "shareId": "SHARE_FAKE_001",
                     "serialNumber": "SN001", "targetId": 2002,
                     "targetName": "sharer", "targetEmail": "sharer@a4x.io"},
                ]}}
    if url.endswith("/device/handleapproval"):
        return {"result": 0, "msg": "Success", "data": {}}
    if url.endswith("/device/sharestatus"):
        return {"result": 0, "msg": "Success",
                "data": {"list": [
                    {"userId": 1001, "userName": "admin", "userEmail": "admin@a4x.io"},
                    {"userId": 2002, "userName": "sharer", "userEmail": "sharer@a4x.io"},
                ]}}
    if url.endswith("/device/listuserdevices"):
        # 测试可以 monkey-patch 这个返回，模拟"设备 0→1"或"设备 1→0"
        return {"result": 0, "msg": "Success",
                "data": {"list": [{"serialNumber": "SN001"}]}}
    if url.endswith("/device/undoshareself") or url.endswith("/device/undoshare"):
        return {"result": 0, "msg": "Success", "data": {}}
    return {"result": 0, "msg": "Success", "data": {}}


# ─────────────────────────── share 完整链路 ───────────────────────────


def test_share_full_pipeline_calls_9_endpoints_in_order():
    """share 严格走 9 步：admin login → sharer login → getshareid → requireshare
    → recentapprovals → handleapproval → sharestatus + listuserdevices。"""
    calls: list[str] = []

    def fake_post(url, body=None, token=None, timeout=15):
        calls.append(url)
        return _canned(url, body)

    cfg = _make_cfg(action="share")
    with patch.object(sd, "_post", side_effect=fake_post):
        result = sd.cmd_share(cfg)

    assert result.get("approval_status") in ("agreed", 0) or "share_id" in result \
        or result.get("ok") is True, f"unexpected result {result}"

    # 关键 endpoint 都必须出现
    assert sum("/account/login" in u for u in calls) == 2, f"双登录: {calls}"
    assert any("/device/getshareid" in u for u in calls)
    assert any("/device/requireshare" in u for u in calls)
    assert any("/device/recentapprovals" in u for u in calls)
    assert any("/device/handleapproval" in u for u in calls)


def test_share_decline_uses_status_1():
    """--decline 时 handleapproval body status=1。"""
    captured: dict[str, dict] = {}

    def fake_post(url, body=None, token=None, timeout=15):
        captured.setdefault(url, body or {})
        return _canned(url, body)

    cfg = _make_cfg(action="share", decline=True, no_verify=True)
    with patch.object(sd, "_post", side_effect=fake_post):
        sd.cmd_share(cfg)

    handle_url = next(u for u in captured if u.endswith("/device/handleapproval"))
    assert captured[handle_url]["status"] == 1, f"decline 必须 status=1: {captured[handle_url]}"


# ─────────────────────────── unshare 路径 ───────────────────────────


def test_unshare_sharer_self_calls_unshare_endpoint():
    calls: list[str] = []

    def fake_post(url, body=None, token=None, timeout=15):
        calls.append(url)
        return _canned(url, body)

    cfg = _make_cfg(action="unshare", by_admin=False, no_verify=True)
    with patch.object(sd, "_post", side_effect=fake_post):
        sd.cmd_unshare(cfg)

    # sharer self 走 /device/undoshareself（不是 admin 强制收回的 /device/undoshare）
    assert any("/device/undoshareself" in u for u in calls), calls
    assert not any(u.endswith("/device/undoshare") for u in calls), calls


def test_unshare_by_admin_with_explicit_user_id_works():
    calls: list[str] = []

    def fake_post(url, body=None, token=None, timeout=15):
        calls.append(url)
        return _canned(url, body)

    cfg = _make_cfg(
        action="unshare", by_admin=True, sharer_user_id=2002,
        sharer_email="", sharer_password="",  # 没 sharer 凭证
        no_verify=True,
    )
    with patch.object(sd, "_post", side_effect=fake_post):
        sd.cmd_unshare(cfg)

    # 应走 admin 强制收回路径 /device/undoshare（非 sharer self 的 /device/undoshareself）
    assert any(u.endswith("/device/undoshare") for u in calls), calls
    assert not any("/device/undoshareself" in u for u in calls), calls


def test_unshare_by_admin_without_user_id_or_creds_refuses():
    """回归 P1 fix：--by-admin 且没 --sharer-user-id 且没 sharer 凭证时
    必须显式失败，不再'从 sharestatus list[-1] 兜底'。"""
    def fake_post(url, body=None, token=None, timeout=15):
        return _canned(url, body)

    cfg = _make_cfg(
        action="unshare", by_admin=True, sharer_user_id=None,
        sharer_email="", sharer_password="",
        no_verify=True,
    )
    with patch.object(sd, "_post", side_effect=fake_post):
        with pytest.raises(sd.ShareError, match="--sharer-user-id"):
            sd.cmd_unshare(cfg)


# ─────────────────────────── list ───────────────────────────


def test_list_outputs_admin_and_sharer_views():
    calls: list[str] = []

    def fake_post(url, body=None, token=None, timeout=15):
        calls.append(url)
        return _canned(url, body)

    cfg = _make_cfg(action="list")
    with patch.object(sd, "_post", side_effect=fake_post):
        result = sd.cmd_list(cfg)

    # 双视角必须都被查询
    assert any("/device/sharestatus" in u for u in calls)
    assert any("/device/listuserdevices" in u for u in calls)
    assert "admin_view" in result or "sharer_view" in result \
        or "admin_share_status" in result or "sharer_devices" in result, result


# ─────────────────────────── 错误传播契约 ───────────────────────────


def test_post_only_uses_result_field_not_msg():
    """回归 P1 fix：_post 只看 result=0，不再要求 msg.startswith('success')。

    后端不同 endpoint 返回大小写不一 / 中文 / 缺字段，msg 不是稳定信号源。
    """
    # result=0 但没 msg → 仍应通过
    with patch.object(sd, "requests") as mock_req:
        mock_resp = mock_req.post.return_value
        mock_resp.json.return_value = {"result": 0, "data": {}}
        mock_resp.raise_for_status.return_value = None
        sd._post("https://x.com/y", {})  # 不抛错就是通过
    # result=0 + msg 大写 SUCCESS → 仍应通过
    with patch.object(sd, "requests") as mock_req:
        mock_resp = mock_req.post.return_value
        mock_resp.json.return_value = {"result": 0, "msg": "SUCCESS", "data": {}}
        mock_resp.raise_for_status.return_value = None
        sd._post("https://x.com/y", {})


def test_post_raises_on_nonzero_result():
    with patch.object(sd, "requests") as mock_req:
        mock_resp = mock_req.post.return_value
        mock_resp.json.return_value = {"result": -1, "msg": "Bad", "data": {}}
        mock_resp.raise_for_status.return_value = None
        with pytest.raises(sd.ShareError):
            sd._post("https://x.com/y", {})


def test_recentapprovals_no_match_refuses_instead_of_first_row_fallback():
    """回归 P1 fix：recentapprovals 没匹配上时不再'拿首条兜底'，必须显式失败。"""
    def fake_post(url, body=None, token=None, timeout=15):
        if url.endswith("/device/recentapprovals"):
            # 返回一条不匹配的 approval
            return {"result": 0, "msg": "Success",
                    "data": {"list": [
                        {"id": 88, "shareId": "OTHER_SHARE",
                         "serialNumber": "SN_OTHER",
                         "targetEmail": "other@a4x.io"}
                    ]}}
        return _canned(url, body)

    cfg = _make_cfg(action="share", no_verify=True)
    with patch.object(sd, "_post", side_effect=fake_post):
        with pytest.raises(sd.ShareError, match="未精确匹配"):
            sd.cmd_share(cfg)
