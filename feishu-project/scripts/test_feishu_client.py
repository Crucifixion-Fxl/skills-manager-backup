"""feishu_client 离线冒烟测试（不依赖网络 / 真实凭证）

运行（需先 cd 到 skills/feishu-project/scripts/ 目录，因为 `feishu-project`
含连字符不是合法 Python 包名，无法从仓库根用 `-m unittest module.path` 调用）：

    cd skills/feishu-project/scripts && python -m unittest test_feishu_client

或直接：

    cd skills/feishu-project/scripts && python test_feishu_client.py
"""

import os
import unittest
from unittest import mock

from feishu_client import (
    FeishuApiError,
    FeishuAuthError,
    FeishuProjectClient,
    FeishuTimeoutError,
    _TypeCache,
    is_plugin_auth_available,
)


class ExceptionHierarchyTests(unittest.TestCase):
    def test_auth_error_is_api_error_subclass(self):
        self.assertTrue(issubclass(FeishuAuthError, FeishuApiError))

    def test_timeout_error_is_api_error_subclass(self):
        self.assertTrue(issubclass(FeishuTimeoutError, FeishuApiError))

    def test_callers_can_catch_generically(self):
        # 业务层想统一兜底时，单独 except FeishuApiError 就能抓到子类
        for cls in (FeishuAuthError, FeishuTimeoutError):
            with self.assertRaises(FeishuApiError):
                raise cls("boom")


class PluginAuthAvailabilityTests(unittest.TestCase):
    def test_missing_env_returns_false(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_plugin_auth_available())

    def test_all_present_returns_true(self):
        env = {
            "FEISHU_PLUGIN_ID": "x",
            "FEISHU_PLUGIN_SECRET": "y",
            "FEISHU_USER_KEY": "z",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(is_plugin_auth_available())

    def test_partial_env_returns_false(self):
        env = {"FEISHU_PLUGIN_ID": "x", "FEISHU_PLUGIN_SECRET": "y"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(is_plugin_auth_available())


class ClientInitTests(unittest.TestCase):
    def test_missing_env_raises_with_hint(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EnvironmentError) as ctx:
                FeishuProjectClient()
            msg = str(ctx.exception)
            self.assertIn("FEISHU_PLUGIN_ID", msg)
            self.assertIn("MCP OAuth", msg)  # 降级提示必须在


class ResolveTypeKeyTests(unittest.TestCase):
    def _make_client(self):
        env = {
            "FEISHU_PLUGIN_ID": "x",
            "FEISHU_PLUGIN_SECRET": "y",
            "FEISHU_USER_KEY": "z",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            return FeishuProjectClient()

    def test_api_name_resolves_to_type_key(self):
        client = self._make_client()
        client._type_cache["pk1"] = _TypeCache(
            known_keys={"story_1"},
            api_name_to_key={"story": "story_1"},
        )
        self.assertEqual(client._resolve_type_key("pk1", "story"), "story_1")

    def test_already_type_key_returns_unchanged(self):
        client = self._make_client()
        client._type_cache["pk1"] = _TypeCache(
            known_keys={"story_1"},
            api_name_to_key={"story": "story_1"},
        )
        self.assertEqual(client._resolve_type_key("pk1", "story_1"), "story_1")

    def test_unknown_key_returns_input(self):
        # 未知字符串直接返回；_post 调用时 API 会报错，由业务层处理
        client = self._make_client()
        client._type_cache["pk1"] = _TypeCache()
        self.assertEqual(client._resolve_type_key("pk1", "unknown"), "unknown")


class RequestErrorMappingTests(unittest.TestCase):
    def _make_client(self):
        env = {
            "FEISHU_PLUGIN_ID": "x",
            "FEISHU_PLUGIN_SECRET": "y",
            "FEISHU_USER_KEY": "z",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = FeishuProjectClient()
        # 绕过鉴权
        client._get_token = lambda: "fake-token"  # type: ignore[assignment]
        return client

    def test_timeout_maps_to_feishu_timeout_error(self):
        import requests as _requests

        client = self._make_client()
        with mock.patch(
            "feishu_client.requests.request",
            side_effect=_requests.exceptions.Timeout("read timed out"),
        ):
            with self.assertRaises(FeishuTimeoutError):
                client._request("GET", "/test")

    def test_401_maps_to_feishu_auth_error(self):
        client = self._make_client()
        resp = mock.Mock()
        resp.status_code = 401
        resp.text = "unauthorized"
        with mock.patch("feishu_client.requests.request", return_value=resp):
            with self.assertRaises(FeishuAuthError):
                client._request("GET", "/test")

    def test_err_code_nonzero_raises_api_error(self):
        client = self._make_client()
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {"err_code": 1234, "err_msg": "nope"}
        with mock.patch("feishu_client.requests.request", return_value=resp):
            with self.assertRaises(FeishuApiError) as ctx:
                client._request("GET", "/test")
            # 不应该被错分类为 Auth/Timeout
            self.assertNotIsInstance(ctx.exception, FeishuAuthError)
            self.assertNotIsInstance(ctx.exception, FeishuTimeoutError)

    def test_success_returns_data_field(self):
        client = self._make_client()
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {"err_code": 0, "data": [{"id": 1}]}
        with mock.patch("feishu_client.requests.request", return_value=resp):
            self.assertEqual(client._request("GET", "/test"), [{"id": 1}])


class UploadTimeoutTests(unittest.TestCase):
    """upload_attachment / upload_file 也走 FeishuTimeoutError，不能遗漏"""

    def setUp(self):
        import tempfile
        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(b"x")
        f.close()
        self.tmp_path = f.name

        env = {
            "FEISHU_PLUGIN_ID": "x",
            "FEISHU_PLUGIN_SECRET": "y",
            "FEISHU_USER_KEY": "z",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = FeishuProjectClient()
        client._get_token = lambda: "fake-token"  # type: ignore[assignment]
        client._type_cache["pk"] = _TypeCache(known_keys={"story"})
        self.client = client

    def tearDown(self):
        os.unlink(self.tmp_path)

    def test_upload_attachment_timeout_maps(self):
        import requests as _requests
        with mock.patch(
            "feishu_client.requests.post",
            side_effect=_requests.exceptions.Timeout("slow"),
        ):
            with self.assertRaises(FeishuTimeoutError):
                self.client.upload_attachment("pk", "story", "wid", "attach", self.tmp_path)

    def test_upload_file_timeout_maps(self):
        import requests as _requests
        with mock.patch(
            "feishu_client.requests.post",
            side_effect=_requests.exceptions.Timeout("slow"),
        ):
            with self.assertRaises(FeishuTimeoutError):
                self.client.upload_file("pk", self.tmp_path)


if __name__ == "__main__":
    unittest.main()
