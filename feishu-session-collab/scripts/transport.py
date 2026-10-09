# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Import-only CLI transport; use collab.py --help, which renders results as JSON."""
import json
from pathlib import Path
import subprocess
from core import build_request


class Lark:
    def __init__(self, config):
        if not isinstance(config, dict):
            raise ValueError("运行此命令需要 --config；请提供批准的 CLI 路径、profile、应用和主人标识")
        self.config = config
        for key in ("node", "entry", "profile", "app_id", "owner_id", "owner_name"):
            if not config.get(key):
                raise ValueError("CLI 配置缺少 " + key)
        if not all(Path(config[k]).is_absolute() for k in ("node", "entry")):
            raise ValueError("必须使用批准的 Node 和 CLI 入口绝对路径")

    def argv(self, args, identity):
        if identity not in {"user", "bot"}:
            raise ValueError("必须指定身份")
        return [self.config["node"], self.config["entry"], "--profile", self.config["profile"], *args, "--as", identity]

    def call(self, args, identity="user", stdin=None):
        result = subprocess.run(self.argv(args, identity), input=stdin, capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError("CLI 调用失败；检查指定 profile 的身份、权限和本机 strict-mode；不切换身份或应用重试")
        envelope = json.loads(result.stdout)
        if envelope.get("ok") is not True or envelope.get("identity") != identity:
            raise ValueError("CLI 成功信封或身份不匹配")
        data = envelope["data"]
        if isinstance(data, dict) and "code" in data:
            if data["code"] != 0:
                raise RuntimeError("OpenAPI 返回业务错误")
            data = data.get("data", {})
        return data

    def check_user(self, data):
        c = self.config
        expected = {"profile": c["profile"], "appId": c["app_id"], "identity": "user", "available": True, "tokenStatus": "ready"}
        if any(data.get(k) != v for k, v in expected.items()):
            raise ValueError("用户 profile、应用或登录态不匹配；停止个人身份操作")
        user = data.get("onBehalfOf", {})
        if user.get("openId") != c["owner_id"] or user.get("userName") != c["owner_name"]:
            raise ValueError("当前用户不是批准的主人")

    def verify_user(self):
        c = self.config
        result = subprocess.run([c["node"], c["entry"], "--profile", c["profile"], "whoami"], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("无法核验个人身份")
        self.check_user(json.loads(result.stdout))


def send_request(lark, plan):
    if plan.get("identity") != "user":
        raise ValueError("协作者通知只能使用个人身份")
    validated = build_request(**{key: plan[key] for key in ("record", "recipient", "context", "needed", "expected", "request_id", "deadline")})
    if plan != validated:
        raise ValueError("通知草稿被修改；请重新生成并核对具体收件人和内容")
    lark.verify_user()
    try:
        data = lark.call(["im", "+messages-send", "--user-id", plan["recipient"], "--text", "-", "--idempotency-key", plan["idempotency_key"]], "user", plan["text"])
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return {"status": "outcome_unknown", "hint": "发送未取得可靠回执；先核实结果，不自动重发"}
    message_id = data.get("message_id")
    if not message_id:
        return {"status": "outcome_unknown", "hint": "发送响应缺少消息 ID；不要直接重发，先核实发送结果"}
    try:
        readback = lark.call(["api", "GET", "/open-apis/im/v1/messages/" + message_id], "user")
        item = next(r for r in readback.get("items", []) if r.get("message_id") == message_id)
        if not data.get("chat_id") or item.get("chat_id") != data["chat_id"] or json.loads(item["body"]["content"]).get("text") != plan["text"]:
            raise ValueError("回读会话或正文不匹配")
    except (RuntimeError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired):
        return {"status": "sent_unverified", "message_id": message_id, "hint": "API 已确认发送，回读未完成；检查消息读取权限，不要重发"}
    return {"status": "read_back", "message_id": message_id, "readback": readback}
