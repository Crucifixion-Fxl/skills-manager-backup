import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SECURITY_SKILL = REPO_ROOT / "skills/security/security-compliance-review/SKILL.md"
CODE_REVIEW_SKILL = REPO_ROOT / "skills/quality/code-review/SKILL.md"
REFERENCE = (
    REPO_ROOT
    / "skills/security/security-compliance-review/references/REFERENCE.md"
)
HUB_CONTENT = REPO_ROOT / ".config/skill-hub-content.json"


def test_r15_is_purpose_and_data_flow_based_not_permission_name_blocklist():
    security = SECURITY_SKILL.read_text(encoding="utf-8")
    review = CODE_REVIEW_SKILL.read_text(encoding="utf-8")

    assert "业务目的 + 实际数据流 + 权限范围" in security
    assert "仅出现权限声明、定位插件或 SSID/BSSID API 不得直接阻断" in security
    assert "申请任何形式的地理位置权限：直接判定" not in security
    assert "仅申请权限、新增定位插件或使用 SSID/BSSID API 不是红线" in review


def test_r15_documents_iot_wifi_and_webrtc_boundaries():
    security = SECURITY_SKILL.read_text(encoding="utf-8")
    reference = REFERENCE.read_text(encoding="utf-8")

    for required in (
        "IoT/智能硬件配网",
        "Home Wi-Fi",
        "SoftAP/Wi-Fi Direct",
        "SSID/BSSID",
        "WebRTC/直播",
        "经纬度",
        "第三方",
    ):
        assert required in security
        assert required in reference


def test_r15_preserves_misuse_lifecycle_and_denial_fallback_controls():
    security = SECURITY_SKILL.read_text(encoding="utf-8")
    reference = REFERENCE.read_text(encoding="utf-8")

    assert (
        "日志、埋点、上传、第三方可见性与保留周期必须有明确用途和生命周期"
        in security
    )
    assert (
        "用户拒绝时应按产品能力提供手动选网、系统 Wi-Fi 设置或 route 可达性等合理降级"
        in security
    )
    assert "未声明的位置推断、画像、跟踪、广告/第三方用途" in security
    assert "日志/埋点/网络/存储/第三方" in reference
    assert (
        "未声明的位置推断、画像、跨场景跟踪、广告或无关第三方用途"
        in reference
    )


def test_skill_hub_summary_does_not_restore_permission_name_blocking():
    hub = json.loads(HUB_CONTENT.read_text(encoding="utf-8"))
    intro = hub["skillGuides"]["security-compliance-review"]["intro"]

    assert "申请地理位置权限）直接判不通过" not in intro
    assert "业务目的、实际数据流和权限范围" in intro
    assert "越界的位置推断、画像、跟踪、广告或无关第三方用途" in intro
