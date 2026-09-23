#!/usr/bin/env python3
"""
飞书 Webhook 机器人通知脚本
用法: python3 feishu_notify.py --title "标题" --content "markdown内容" --color red [--button-text "按钮" --button-url "URL"]
"""

import argparse
import hashlib
import hmac
import base64
import time
import json
import os
import sys
import urllib.request

_CONTENT_TYPE_JSON = "application/json"


def compute_sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_card(
    webhook_url: str,
    secret: str,
    title: str,
    content: str,
    color: str = "red",
    button_text: str = None,
    button_url: str = None,
):
    timestamp = str(int(time.time()))
    sign = compute_sign(timestamp, secret)

    elements = [{"tag": "markdown", "content": content}]

    if button_text and button_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": button_text},
                        "url": button_url,
                        "type": "primary",
                    }
                ],
            }
        )

    payload = {
        "timestamp": timestamp,
        "sign": sign,
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": _CONTENT_TYPE_JSON},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read().decode())
    return result


def send_multi_section_card(
    webhook_url: str,
    secret: str,
    title: str,
    sections: list,
    color: str = "red",
):
    """发送多段落卡片，每个 section 是 dict: {content, button_text?, button_url?}"""
    timestamp = str(int(time.time()))
    sign = compute_sign(timestamp, secret)

    elements = []
    for i, section in enumerate(sections):
        if i > 0:
            elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": section["content"]})
        if section.get("button_text") and section.get("button_url"):
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": section["button_text"],
                            },
                            "url": section["button_url"],
                            "type": "primary",
                        }
                    ],
                }
            )

    payload = {
        "timestamp": timestamp,
        "sign": sign,
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": _CONTENT_TYPE_JSON},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read().decode())
    return result


def _send_payload(webhook_url: str, secret: str, payload_card: dict):
    """内部函数：签名并发送卡片 payload。"""
    timestamp = str(int(time.time()))
    sign = compute_sign(timestamp, secret)
    payload = {
        "timestamp": timestamp,
        "sign": sign,
        "msg_type": "interactive",
        "card": payload_card,
    }
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": _CONTENT_TYPE_JSON},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode())


def make_table(columns, rows, row_height="high", page_size=20):
    """构造飞书卡片 table 元素。

    Args:
        columns: 列定义列表，每项为 dict:
            {"name": "col_key", "display_name": "显示名", "data_type": "text", "width": "300px"}
            width 支持 "auto" 或 "NNpx"（最小 80px），同一表格不可混用。
        rows: 行数据列表，每项为 dict，key 与 columns 的 name 对应。
        row_height: "low" 或 "high"（飞书仅支持这两个值）。
        page_size: 每页行数。
    """
    return {
        "tag": "table",
        "page_size": page_size,
        "row_height": row_height,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "text_color": "default",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": rows,
    }


def send_elements_card(
    webhook_url: str,
    secret: str,
    title: str,
    elements: list,
    color: str = "red",
    wide_screen_mode: bool = True,
):
    """发送混合元素卡片（支持 markdown + table + hr + action 等任意元素组合）。

    Args:
        elements: 卡片元素列表，每项为飞书卡片元素 dict，例如:
            {"tag": "markdown", "content": "**标题**"}
            {"tag": "hr"}
            make_table(columns, rows)  # table 元素
    """
    card = {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "body": {
            "elements": elements,
        },
    }
    if wide_screen_mode:
        card["config"] = {"wide_screen_mode": True}
    return _send_payload(webhook_url, secret, card)


def _get_webhook(debug_mode=False):
    if debug_mode:
        webhook_url = os.environ.get("ONCALL_FEISHU_WEBHOOK_URL_DEBUG")
        secret = os.environ.get("ONCALL_FEISHU_WEBHOOK_SECRET_DEBUG")
        env_names = "ONCALL_FEISHU_WEBHOOK_URL_DEBUG and ONCALL_FEISHU_WEBHOOK_SECRET_DEBUG"
    else:
        webhook_url = os.environ.get("ONCALL_FEISHU_WEBHOOK_URL")
        secret = os.environ.get("ONCALL_FEISHU_WEBHOOK_SECRET")
        env_names = "ONCALL_FEISHU_WEBHOOK_URL and ONCALL_FEISHU_WEBHOOK_SECRET"
    if not webhook_url or not secret:
        print(
            f"Error: {env_names} must be set",
            file=sys.stderr,
        )
        sys.exit(1)
    return webhook_url, secret


def main():
    # 判断是否使用子命令：第一个非 - 开头的参数是否为已知子命令
    known_commands = {"send-markdown", "send-elements"}
    use_subcommand = len(sys.argv) > 1 and sys.argv[1] in known_commands

    if not use_subcommand:
        # 旧用法: feishu_notify.py --title ... --content ...
        parser = argparse.ArgumentParser(description="飞书 Webhook 卡片通知")
        parser.add_argument("--title", required=True, help="卡片标题")
        parser.add_argument("--content", required=True, help="Markdown 内容")
        parser.add_argument(
            "--color", default="red",
            choices=["red", "yellow", "blue", "green", "grey"],
        )
        parser.add_argument("--button-text", help="按钮文字")
        parser.add_argument("--button-url", help="按钮链接")
        args = parser.parse_args()
        webhook_url, secret = _get_webhook()

        content = args.content.replace("\\n", "\n")
        result = send_card(
            webhook_url=webhook_url,
            secret=secret,
            title=args.title,
            content=content,
            color=args.color,
            button_text=args.button_text,
            button_url=args.button_url,
        )
    else:
        parser = argparse.ArgumentParser(description="飞书 Webhook 卡片通知")
        subparsers = parser.add_subparsers(dest="command")

        md_parser = subparsers.add_parser("send-markdown", help="发送 Markdown 卡片")
        md_parser.add_argument("--title", required=True)
        md_parser.add_argument("--content", required=True)
        md_parser.add_argument(
            "--color", default="red",
            choices=["red", "yellow", "blue", "green", "grey"],
        )
        md_parser.add_argument("--button-text")
        md_parser.add_argument("--button-url")

        el_parser = subparsers.add_parser("send-elements", help="发送混合元素卡片")
        el_parser.add_argument("--title", required=True)
        el_parser.add_argument("--elements-file", required=True)
        el_parser.add_argument(
            "--color", default="red",
            choices=["red", "yellow", "blue", "green", "grey"],
        )
        el_parser.add_argument("--no-wide-screen", action="store_true")

        args = parser.parse_args()
        webhook_url, secret = _get_webhook()

        if args.command == "send-markdown":
            content = args.content.replace("\\n", "\n")
            result = send_card(
                webhook_url=webhook_url,
                secret=secret,
                title=args.title,
                content=content,
                color=args.color,
                button_text=args.button_text,
                button_url=args.button_url,
            )
        elif args.command == "send-elements":
            with open(args.elements_file, "r", encoding="utf-8") as f:
                elements = json.load(f)
            result = send_elements_card(
                webhook_url=webhook_url,
                secret=secret,
                title=args.title,
                elements=elements,
                color=args.color,
                wide_screen_mode=not args.no_wide_screen,
            )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
