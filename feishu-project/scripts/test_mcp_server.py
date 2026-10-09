import os
import unittest
from unittest.mock import patch

import mcp_server


class FakeClient:
    def get_project_detail(self, keys):
        return keys

    def search_work_items(self, project_key, types, **kwargs):
        return {"project_key": project_key, "types": types, "filters": kwargs}


class MCPServerTest(unittest.TestCase):
    def setUp(self):
        self.previous_client = mcp_server._client
        mcp_server._client = FakeClient()

    def tearDown(self):
        mcp_server._client = self.previous_client

    def test_project_is_fixed_by_host(self):
        with patch.dict(os.environ, {"FEISHU_PROJECT_KEY": "allowed", "FEISHU_PROJECT_MCP_ALLOW_WRITE": "1"}):
            self.assertEqual(mcp_server.call_tool("project_detail", {}), ["allowed"])
            with self.assertRaises(ValueError):
                mcp_server.call_tool("search_work_items", {
                    "work_item_type_keys": ["design"],
                    "filters": {"project_key": "other"},
                })

    def test_write_requires_explicit_host_gate(self):
        with patch.dict(os.environ, {"FEISHU_PROJECT_KEY": "allowed"}, clear=True):
            response = mcp_server.respond({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "create_work_item", "arguments": {
                    "work_item_type_key": "design", "template_id": 1, "fields": []}},
            })
            self.assertTrue(response["result"]["isError"])
            self.assertIn("disabled", response["result"]["content"][0]["text"])

    def test_read_call_uses_fixed_project(self):
        with patch.dict(os.environ, {"FEISHU_PROJECT_KEY": "allowed"}, clear=True):
            result = mcp_server.call_tool("search_work_items", {
                "work_item_type_keys": ["design"], "page_size": 20, "filters": {"name": "brief"},
            })
            self.assertEqual(result["project_key"], "allowed")
            self.assertEqual(result["filters"]["name"], "brief")


if __name__ == "__main__":
    unittest.main()
