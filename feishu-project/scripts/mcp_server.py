#!/usr/bin/env python3
"""Small stdio MCP adapter for the Feishu Project Plugin Token client.

The host supplies FEISHU_PLUGIN_ID, FEISHU_PLUGIN_SECRET, FEISHU_USER_KEY and
FEISHU_PROJECT_KEY. The project key is fixed by the host, never by tool input.
No credentials or tokens are written to stdout or stderr.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from feishu_client import FeishuProjectClient


def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


TYPE = {"type": "string", "description": "Work item type key or API name"}
ID = {"type": "integer", "minimum": 1}
FIELDS = {"type": "array", "items": {"type": "object"}}
TOOLS = [
    tool("project_detail", "Read the configured Feishu Project space.", {}, []),
    tool("list_work_item_types", "List work item types in the configured space.", {}, []),
    tool("get_field_config", "Read field configuration for a work item type.", {"work_item_type_key": TYPE}, ["work_item_type_key"]),
    tool("get_work_item_meta", "Read templates and required fields before creating an item.", {"work_item_type_key": TYPE}, ["work_item_type_key"]),
    tool("search_work_items", "Search work items for deduplication; page through results.", {
        "work_item_type_keys": {"type": "array", "items": TYPE, "minItems": 1},
        "page_num": {"type": "integer", "minimum": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
        "filters": {"type": "object", "description": "Feishu Project filter API parameters"},
    }, ["work_item_type_keys"]),
    tool("get_work_item_detail", "Read exact work item IDs after search or write.", {
        "work_item_type_key": TYPE,
        "work_item_ids": {"type": "array", "items": ID, "minItems": 1, "maxItems": 50},
    }, ["work_item_type_key", "work_item_ids"]),
    tool("list_comments", "Read comments on a work item.", {"work_item_type_key": TYPE, "work_item_id": ID}, ["work_item_type_key", "work_item_id"]),
    tool("create_work_item", "Create an intake item after checking type, template, fields and duplicates. Read it back by ID.", {
        "work_item_type_key": TYPE,
        "template_id": ID,
        "fields": FIELDS,
    }, ["work_item_type_key", "template_id", "fields"]),
    tool("update_work_item", "Update fields on an existing item after reading its current value. Read it back by ID.", {
        "work_item_type_key": TYPE,
        "work_item_id": ID,
        "fields": FIELDS,
    }, ["work_item_type_key", "work_item_id", "fields"]),
    tool("add_comment", "Add an intake or handoff comment to an existing item.", {
        "work_item_type_key": TYPE,
        "work_item_id": ID,
        "content": {"type": "string", "minLength": 1},
    }, ["work_item_type_key", "work_item_id", "content"]),
]
TOOL_NAMES = {item["name"] for item in TOOLS}
WRITE_TOOLS = {"create_work_item", "update_work_item", "add_comment"}
_client: FeishuProjectClient | None = None


def _get_client() -> FeishuProjectClient:
    global _client
    if _client is None:
        _client = FeishuProjectClient()
    return _client


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if name not in TOOL_NAMES:
        raise ValueError("Unknown tool")
    if name in WRITE_TOOLS and os.environ.get("FEISHU_PROJECT_MCP_ALLOW_WRITE") != "1":
        raise PermissionError("Write tools are disabled by the host")
    project_key = os.environ.get("FEISHU_PROJECT_KEY")
    if not project_key:
        raise EnvironmentError("FEISHU_PROJECT_KEY is missing")
    client = _get_client()
    kind = args.get("work_item_type_key")
    item_id = args.get("work_item_id")
    if name == "project_detail":
        return client.get_project_detail([project_key])
    if name == "list_work_item_types":
        return client.get_work_item_types(project_key)
    if name == "get_field_config":
        return client.get_field_config(project_key, kind)
    if name == "get_work_item_meta":
        return client.get_work_item_meta(project_key, kind)
    if name == "search_work_items":
        page_size = args.get("page_size", 50)
        page_num = args.get("page_num", 1)
        if not isinstance(page_size, int) or not 1 <= page_size <= 50:
            raise ValueError("page_size must be between 1 and 50")
        if not isinstance(page_num, int) or page_num < 1:
            raise ValueError("page_num must be positive")
        filters = args.get("filters", {})
        if not isinstance(filters, dict) or any(k in filters for k in ("project_key", "work_item_type_keys", "page_size", "page_num")):
            raise ValueError("Invalid filters")
        return client.search_work_items(project_key, args["work_item_type_keys"], page_size=page_size, page_num=page_num, **filters)
    if name == "get_work_item_detail":
        ids = args["work_item_ids"]
        if not isinstance(ids, list) or not ids or len(ids) > 50 or any(not isinstance(i, int) or i <= 0 for i in ids):
            raise ValueError("work_item_ids must contain 1 to 50 positive integers")
        return client.get_work_item_detail(project_key, kind, ids)
    if name == "list_comments":
        return client.list_comments(project_key, kind, str(item_id))
    if name == "create_work_item":
        return client.create_work_item(project_key, kind, args["fields"], template_id=args["template_id"])
    if name == "update_work_item":
        return client.update_work_item(project_key, kind, str(item_id), args["fields"])
    if name == "add_comment":
        return client.add_comment(project_key, kind, str(item_id), args["content"])
    raise ValueError("Unknown tool")


def respond(request: dict[str, Any]) -> dict[str, Any] | None:
    ident = request.get("id")
    if ident is None:
        return None
    method = request.get("method")
    params = request.get("params") or {}
    try:
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "feishu-project-plugin", "version": "1.0.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            try:
                value = call_tool(name, args)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}]}
            except Exception as exc:
                result = {"content": [{"type": "text", "text": f"{type(exc).__name__}: {str(exc)[:1000]}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found"}}
        return {"jsonrpc": "2.0", "id": ident, "result": result}
    except Exception:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32603, "message": "Internal error"}}


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            response = respond(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except (ValueError, UnicodeError):
            continue


if __name__ == "__main__":
    main()
