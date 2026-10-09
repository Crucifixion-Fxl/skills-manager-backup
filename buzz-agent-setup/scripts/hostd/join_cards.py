"""Human-readable interactive cards sent only by the emitting agent's bot."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
try:
    from .onboarding import NOTICE, CardRecovery, _id, _request
    from .async_io import thread_call
except ImportError:
    from onboarding import NOTICE, CardRecovery, _id, _request
    from async_io import thread_call


def _chat_link(row):
    _id(row['chat_id'], 'oc_')
    return 'https://applink.feishu.cn/client/chat/open?openChatId=' + row['chat_id']


def card(row, generation, state="requested"):
    _request(row["request_id"])
    descriptions = {
        "requested": "邀请已收到，等待 Agent owner 审批。同意后才会开通本群工作权限；未绑定的群会自动接入一个新的 private Buzz 频道。",
        "superseded": "这张卡片已失效。怎么解决：下一步请看同一申请的新卡片。复制给 AI：帮我核查接入申请当前卡片，不要输出凭据。",
        "approved": "owner 已同意，正在后台开通；完成核验后会更新这张卡片。",
        "blocked": "owner 已同意，但开通结果尚未核验。" + NOTICE,
        "done": "接入已完成，后台已读回核验该 agent 与本群的开通结果。",
        "denied": "owner 已拒绝，此申请已结束。退出结果仍需核验。怎么解决：下一步若 agent 或 bot 未退出，请检查本机接入任务。复制给 AI：帮我核查已拒绝申请的退出结果，不要输出凭据。",
        "expired": "7 天内未处理，此申请已过期。退出结果仍需核验。怎么解决：下一步需要接入时请重新邀请 bot；若未退出，请检查本机接入任务。复制给 AI：帮我检查过期接入申请与 bot 退出结果，不要输出凭据。",
    }
    if state not in descriptions or type(generation) is not int or generation < 1:
        raise ValueError(NOTICE)
    labels = {"requested": ("待 owner 审批", "orange"), "approved": ("开通中", "blue"),
              "blocked": ("待排查", "red"), "done": ("已开通", "green"),
              "denied": ("已拒绝", "red"), "expired": ("已过期", "grey"),
              "superseded": ("已被新卡片替代", "grey")}
    label, color = labels[state]
    elements = [{"tag": "div", "text": {"tag": "plain_text", "content": descriptions[state]}},
                {"tag": "div", "text": {"tag": "plain_text", "content": "为什么需要 owner 审批：Agent 会使用其已获授的工具和系统权限处理群内任务。这些权限由 Agent owner 负责，因此必须由 owner 批准后，Agent 才能在本群工作。拉进群只是发起申请，不代表已获授权，也不会新增它原本没有的权限。"}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": f"申请 {row['request_id']} · 卡片 {generation}"}]},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"[打开申请所在群]({_chat_link(row)})"}}]
    if state == "requested":
        elements.append({"tag": "action", "actions": [
            {"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": kind,
             "value": {"request_id": row["request_id"], "generation": generation, "decision": decision}}
            for label, kind, decision in (("同意", "primary", "approve"), ("拒绝", "default", "deny"))]})
    return {"config": {"wide_screen_mode": True, "update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": "Agent 接入申请 · " + label}, "template": color}, "elements": elements}


def _key(request_id, suffix):
    return hashlib.sha256(f"hostd-join:v1:{request_id}:{suffix}".encode()).hexdigest()[:32]


class BotCards:
    def __init__(self, clients):
        self.clients = clients

    def _client(self, row):
        client = self.clients.get(row["callback_app_id"])
        if client is None or client.app_id != row["callback_app_id"]:
            raise ValueError(NOTICE)
        _id(row["chat_id"], "oc_")
        return client

    async def send(self, row, generation):
        client = self._client(row)
        payload = json.dumps(card(row, generation), ensure_ascii=False, separators=(",", ":"))
        message = await thread_call(client.send_card, row["chat_id"], payload, _key(row["request_id"], f"card:{generation}"))
        _id(message, "om_")
        view = await thread_call(client.message_view, message, "union_id")
        if not self._matches(view, row, generation):
            raise ValueError(NOTICE)
        return message

    @staticmethod
    def _matches(view, row, generation):
        if not isinstance(view, dict) or view.get("chat_id") != row["chat_id"]:
            return False
        sender = view.get("sender")
        if not isinstance(sender, dict) or (sender.get("id"), sender.get("id_type"), sender.get("sender_type")) != (row["callback_app_id"], "app_id", "app"):
            return False
        raw = (view.get("body") or {}).get("content")
        try:
            content = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            return False
        expected = f"申请 {row['request_id']} · 卡片 {generation}"
        def texts(value):
            if isinstance(value, dict):
                # Native Card 1.0 readback normalizes note plain_text to
                # lark_md. Match the literal marker; never render/strip markup.
                if ((value.get("tag") in ("plain_text", "lark_md") and value.get("content") == expected)
                        or (value.get("tag") == "text" and value.get("text") == expected)):
                    return True
                return any(texts(v) for v in value.values())
            return isinstance(value, list) and any(texts(v) for v in value)
        def notes(value):
            if isinstance(value, dict):
                return ((value.get('tag') == 'note' and texts(value.get('elements')))
                        or any(notes(v) for v in value.values()))
            return isinstance(value, list) and any(notes(v) for v in value)
        return isinstance(content, dict) and notes(content.get('elements'))

    async def recover(self, row, generation):
        client = self._client(row)
        start = datetime.fromtimestamp(row["created_at"], timezone.utc)
        rows, truncated = await thread_call(client.messages, row["chat_id"], start)
        if truncated:
            return CardRecovery(generation, "", False)
        matches = [view for view in rows if self._matches(view, row, generation)]
        if len(matches) > 1:
            return CardRecovery(generation, "", False)
        if not matches:
            # A flattened/missing own-bot body could be the successful unknown
            # send. Complete pagination alone cannot prove that card absent.
            if any(not isinstance(view.get("sender"), dict) or view["sender"].get("id") == row["callback_app_id"] for view in rows):
                return CardRecovery(generation, "", False)
            return CardRecovery(generation, "", True)
        message = matches[0].get("message_id")
        _id(message, "om_")
        view = await thread_call(client.message_view, message, "union_id")
        return CardRecovery(generation, message, self._matches(view, row, generation))

    async def update(self, row, message_id, state):
        client = self._client(row)
        _id(message_id, "om_")
        payload = json.dumps(card(row, max(1, row.get("card_generation", 1)), state), ensure_ascii=False, separators=(",", ":"))
        await thread_call(client.update_card, message_id, payload)

    async def dm(self, row, union_id):
        client = self._client(row)
        _id(union_id, "on_")
        # App-scoped union_id receiver, bot-only. Stable UUID bounds ambiguous retries.
        text = (f"接入申请 {row['request_id']} 等待您审批。\n"
                f"申请所在群：{_chat_link(row)}\n"
                "请在群内最新卡片选择同意或拒绝；此提醒不表示接入失败。")
        data = {"receive_id": union_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False),
                "uuid": _key(row["request_id"], "owner-dm")}
        result = await thread_call(client.call, "join owner reminder", ["api", "POST", "/open-apis/im/v1/messages",
            "--params", json.dumps({"receive_id_type": "union_id"}), "--data", json.dumps(data, ensure_ascii=False), "--as", "bot"])
        if not isinstance(result, dict):
            raise ValueError(NOTICE)
        _id(result.get("message_id"), "om_")

    async def refuse(self, row, union_id, event_id, reason):
        client = self._client(row)
        _id(union_id, "on_")
        reasons = {"owner": "只有该 agent 的 owner 能决定，此次点击没有改变申请。",
                   "identity": "身份核验查询未完成，此次未记录审批。请待核验恢复后点击当前申请卡片。",
                   "card": "当前申请或卡片已变化，此次点击没有改变申请。",
                   "expired": "该申请已经过期，此次点击没有改变申请。"}
        if reason not in reasons:
            raise ValueError(NOTICE)
        text = f"接入申请 {row['request_id']}：{reasons[reason]}\n申请所在群：{_chat_link(row)}\n{NOTICE}"
        data = {"receive_id": union_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False),
                "uuid": _key(row["request_id"], f"refusal:{row['callback_app_id']}:{event_id}")}
        result = await thread_call(client.call, "join private refusal", ["api", "POST", "/open-apis/im/v1/messages",
            "--params", json.dumps({"receive_id_type": "union_id"}), "--data", json.dumps(data, ensure_ascii=False), "--as", "bot"])
        message = result.get("message_id") if isinstance(result, dict) else None
        _id(message, "om_")
        view = await thread_call(client.message_view, message, "union_id")
        if not isinstance(view, dict) or view.get("message_id") != message or view.get("sender") != result.get("sender"):
            raise ValueError(NOTICE)
        sender = view.get("sender") or {}
        if (sender.get("id"), sender.get("id_type"), sender.get("sender_type")) != (row["callback_app_id"], "app_id", "app"):
            raise ValueError(NOTICE)
        try:
            observed = json.loads((view.get("body") or {}).get("content", ""))
        except (TypeError, ValueError):
            raise ValueError(NOTICE) from None
        if not isinstance(observed, dict) or observed.get("text") != text or view.get("chat_id") != result.get("chat_id"):
            raise ValueError(NOTICE)
