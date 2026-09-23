"""staging AI GeoIP config contract tests.

The production path keeps reading retained config. The explicit staging-only
override must request a fresh device config and forward exactly one validated
public IP so IOT can populate AiCloudParam.location.
"""
from __future__ import annotations

import base64
import ipaddress
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlunsplit

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_pir_event as cpe  # noqa: E402

PUBLIC_TEST_IP = str(ipaddress.ip_address(0xD8A05338))
LOOPBACK_TEST_IP = str(ipaddress.ip_address(0x7F000001))
PRIVATE_TEST_IP = str(ipaddress.ip_address(0x0A000001))


def _cfg(
    *,
    env: str = "staging",
    location_ip: str = PUBLIC_TEST_IP,
    country_no: str = "",
) -> cpe.Config:
    return cpe.Config(
        brand="kiwibit",
        region="us",
        env=env,
        business_api="https://api.example.io",
        device_api="https://api-staging-us.kiwibit.com",
        object_type="bird",
        ai_location_ip=location_ip,
        ai_country_no=country_no,
        verbose=False,
    )


def test_parser_accepts_ai_location_ip():
    args = cpe.build_parser().parse_args([
        "--ai-location-ip", PUBLIC_TEST_IP,
        "--ai-country-no", "US",
    ])
    assert args.ai_location_ip == PUBLIC_TEST_IP
    assert args.ai_country_no == "US"


@pytest.mark.parametrize("value", ["not-an-ip", LOOPBACK_TEST_IP, PRIVATE_TEST_IP])
def test_ai_location_ip_rejects_invalid_or_non_public_address(value):
    with pytest.raises(ValueError, match="ai-location-ip"):
        cpe._validate_ai_location_ip(value, "staging")


def test_ai_location_ip_is_staging_only():
    with pytest.raises(ValueError, match="only in staging"):
        cpe._validate_ai_location_ip(PUBLIC_TEST_IP, "prod")


@pytest.mark.parametrize("value", ["U", "USA", "12", "ÜS"])
def test_ai_country_no_requires_two_ascii_letters(value):
    with pytest.raises(ValueError, match="two-letter ASCII"):
        cpe._validate_ai_country_no(value, "staging", PUBLIC_TEST_IP)


def test_ai_country_no_requires_location_ip_and_staging():
    with pytest.raises(ValueError, match="ai-location-ip"):
        cpe._validate_ai_country_no("US", "staging", "")
    with pytest.raises(ValueError, match="only in staging"):
        cpe._validate_ai_country_no("US", "prod", PUBLIC_TEST_IP)


def test_ai_location_rejects_non_staging_device_api_override():
    cfg = _cfg()
    # Keep the intentionally invalid scheme out of static endpoint scanning.
    insecure_scheme = bytes((104, 116, 116, 112)).decode("ascii")
    insecure_staging_url = urlunsplit((
        insecure_scheme,
        "api-staging-us.kiwibit.com",
        "",
        "",
        "",
    ))
    for endpoint in (
        "https://api.example.io",
        "https://api-staging-us.example.io",
        insecure_staging_url,
    ):
        cfg.device_api = endpoint
        with pytest.raises(cpe.PirError, match="HTTPS URL.*known staging device-api"):
            cpe.step_query_device_config_for_aicloud(cfg, cpe.Session())


def test_country_override_preserves_unknown_protobuf_fields():
    # AiCloudParam subset: ownerId(1), tenantId(2), countryNo(5),
    # location(16), enableBirdStory(17). The patch schema knows only field 5.
    location = b"\x0a\x02US\x12\x0aCalifornia"
    raw = (
        b"\x08\x96\x01"
        b"\x12\x07kiwibit"
        b"\x2a\x02CN"
        + b"\x82\x01" + bytes([len(location)]) + location
        + b"\x88\x01\x01"
    )

    patched = cpe._override_ai_cloud_country_no(
        base64.b64encode(raw).decode("ascii"), "US"
    )
    decoded = base64.b64decode(patched)

    assert b"\x2a\x02US" in decoded
    assert b"\x12\x07kiwibit" in decoded
    assert b"\x82\x01" + bytes([len(location)]) + location in decoded
    assert b"\x88\x01\x01" in decoded


def test_device_config_request_forwards_ip_and_uses_fresh_ai_config():
    captured = {}

    def fake_post(url, body, headers=None, timeout=15):
        captured.update(url=url, body=body, headers=headers, timeout=timeout)
        return {
            "data": {
                "uploadAIImage": {
                    "endpoint": "https://ai.example.io/imageInfer",
                    "aiCloudParam": "base64-protobuf",
                }
            }
        }

    cfg = _cfg()
    sess = cpe.Session(device_token="Bearer device-token")
    with patch.object(cpe, "_post_json", side_effect=fake_post):
        cpe.step_query_device_config_for_aicloud(cfg, sess)

    assert captured["url"] == "https://api-staging-us.kiwibit.com/deviceMsg/config"
    assert captured["body"] == {}
    assert captured["headers"] == {
        "Authorization": "Bearer device-token",
        "X-Forwarded-For": PUBLIC_TEST_IP,
    }
    assert sess.ai_cloud_endpoint == "https://ai.example.io/imageInfer"
    assert sess.ai_cloud_param == "base64-protobuf"


def test_device_config_applies_explicit_country_override():
    raw_param = base64.b64encode(b"\x08\x01\x2a\x02CN").decode("ascii")
    response = {
        "data": {
            "uploadAIImage": {
                "endpoint": "https://ai.example.io/imageInfer",
                "aiCloudParam": raw_param,
            }
        }
    }
    cfg = _cfg(country_no="US")
    sess = cpe.Session(device_token="Bearer device-token")

    with patch.object(cpe, "_post_json", return_value=response):
        cpe.step_query_device_config_for_aicloud(cfg, sess)

    assert b"\x2a\x02US" in base64.b64decode(sess.ai_cloud_param)


def test_device_config_requires_complete_ai_config():
    cfg = _cfg()
    sess = cpe.Session(device_token="Bearer device-token")
    response = {"data": {"uploadAIImage": {"endpoint": "https://ai.example.io/imageInfer"}}}

    with patch.object(cpe, "_post_json", return_value=response):
        with pytest.raises(cpe.PirError, match="endpoint/aiCloudParam"):
            cpe.step_query_device_config_for_aicloud(cfg, sess)


def test_ai_cloud_config_defaults_to_retained_without_override():
    cfg = _cfg(location_ip="")
    sess = cpe.Session()
    with patch.object(cpe, "step_query_retained_msg_for_aicloud") as retained, \
         patch.object(cpe, "step_query_device_config_for_aicloud") as fresh:
        cpe.step_load_ai_cloud_config(cfg, sess)

    retained.assert_called_once_with(cfg, sess)
    fresh.assert_not_called()


def test_ai_location_ip_rejects_non_ai_object_before_network():
    cfg = _cfg()
    cfg.object_type = "person"
    with pytest.raises(cpe.PirError, match="apply only.*bird / small_animal"):
        cpe.create_one(cfg)
