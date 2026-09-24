"""Buzz Channel ↔ 飞书群（engineering/skills#110，references/feishu-group-sync.md）。

L1 覆盖纯函数与 CLI 适配器；L2-1 用 FakeWorld 同时扮演 buzz CLI 和 lark-cli（按 argv 和
子进程 env 分派），走完整的命令入口。FakeWorld 按实测行为建模：buzz `messages get` 最多
200 条、最新在前、`--before` 含边界；lark-cli 错误 JSON 在 stderr、没有话题的消息返回
`not_found`、群列表按 `--start` 过滤、同一幂等键只发一次。重点断言两件事：每一次发送用的
是谁的身份，以及 @ 是否只按显式实体、只指向当前频道成员地转换。
"""

import base64
import contextlib
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from unittest import mock
from pathlib import Path


TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS.parent / "scripts" / "buzz_feishu_group_sync.py"
SPEC = importlib.util.spec_from_file_location("buzz_feishu_group_sync", SCRIPT)
FGS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = FGS
SPEC.loader.exec_module(FGS)

# 纯 Python 的 BIP-340 一次签名约 0.12 s、验签约 0.14 s，每轮都要一次；这些函数是纯函数，测试里加缓存、
# 把 nonce 随机数固定成全零（L1-FGS-001 单独换成 spy 验证真的每次要新随机数），整套测试才不会慢一个数量级。
_MEMO = []


def setUpModule():
    nk = FGS.sync.nk
    for name in ("pubkey_xonly", "schnorr_sign", "schnorr_verify"):
        _MEMO.append(mock.patch.object(nk, name, functools.lru_cache(maxsize=None)(getattr(nk, name))))
    _MEMO.append(mock.patch.object(FGS.secrets, "token_bytes", lambda n: bytes(n)))
    for patch in _MEMO:
        patch.start()


def tearDownModule():
    while _MEMO:
        _MEMO.pop().stop()


BUZZ_CLI = "/opt/buzz-0.5.23/usr/bin/buzz"  # adapter tests only; commands use Env.buzz_cli
LARK_CLI = "/opt/lark/bin/lark-cli"

CHANNEL = "7d10832a-0d23-4486-82db-7aa51b97e5f4"
CHAT = "oc_chat000000000000000000000000001"
API_ORIGIN = "https://bridge.example.test"
API_URL = f"{API_ORIGIN}/bind/api/channels/{CHANNEL}/people"

ALICE_PK = "11" * 32
BOB_PK = "22" * 32
CAROL_PK = "33" * 32  # 频道成员，但没有已验证的绑定
GUEST_PK = "55" * 32  # 频道 guest
AGENT_PK = "aa" * 32
AGENT2_KEY = "c5" * 32
AGENT2_PK = FGS.sync.nk.pubkey_xonly(bytes.fromhex(AGENT2_KEY)).hex()  # 频道里的 agent，但没有配置飞书 bot
OUTSIDER_PK = "44" * 32  # 有绑定，但不在频道里

MIRROR_KEY = "c1" * 32
OWNER_KEY = "c2" * 32  # 父进程环境里 owner 的 key，绝不能传给子进程
# owner 用这把 key 给 bridge 的接口做 NIP-98 签名，所以公钥必须是真的（不是随便一串 hex）
OWNER_PK = FGS.sync.nk.pubkey_xonly(bytes.fromhex(OWNER_KEY)).hex()
# The mirror signs its reactions in-process (ADR-0020), so its pubkey is the real one of its key, like the owner's.
MIRROR_PK = FGS.sync.nk.pubkey_xonly(bytes.fromhex(MIRROR_KEY)).hex()

OWNER_OPEN = "ou_owner00000000000000000000000001"
ALICE_OPEN = "ou_alice00000000000000000000000001"
BOB_OPEN = "ou_bob0000000000000000000000000001"
GUEST_OPEN = "ou_guest00000000000000000000000001"
OUTSIDER_OPEN = "ou_outsider000000000000000000000001"
EXTRA_OPEN = "ou_extra00000000000000000000000001"  # 群里有、频道里没有
EXTRA2_OPEN = "ou_extra00000000000000000000000002"
CAROL_OPEN = "ou_carol00000000000000000000000001"
CHAN_OWNER_OPEN = "ou_chanowner0000000000000000000001"  # 频道 owner 在飞书里绑定的人，不是登录 lark-cli 的那个 owner

OWNER_APP = "cli_owner00000000001"
OWNER_BOT_MEMBER = "ou_ownerbot0000000000000000000001"
AGENT_APP = "cli_agent00000000001"
AGENT_BOT_MEMBER = "ou_agentbot0000000000000000000001"
TEAM_BOT_APP = "cli_team000000000001"  # 群里原有的、不归我们管的 bot

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
T0 = int(NOW.timestamp()) - 60


def ts(dt):
    return int(dt.timestamp())


def feishu_time(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def write_owner_only(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def event(event_id, pubkey, content, *, kind=9, created_at=T0, tags=()):
    return {"id": event_id, "pubkey": pubkey, "kind": kind, "created_at": created_at,
            "content": content, "tags": [["h", CHANNEL], *[list(t) for t in tags]], "sig": "00" * 64}


def edit_event(n, pubkey, target, content, *, created_at=T0):
    """Buzz kind 40003 as the relay returns it: the replacement content and one bare e tag naming the original."""
    return event(eid(0xb000 + n), pubkey, content, kind=40003, created_at=created_at, tags=[("e", target)])


def eid(n):
    return f"{n:064x}"


def reaction_event(n, pubkey, target, emoji, *, created_at=T0):
    """kind 7 as the relay returns it: content is the emoji, one bare e tag, no channel tag."""
    return {"id": eid(0x9000 + n), "pubkey": pubkey, "kind": 7, "created_at": created_at, "content": emoji,
            "tags": [["e", target]], "sig": "00" * 64}


def deletion_event(n, pubkey, *targets, created_at=T0):
    """kind 5 (NIP-09): how Buzz withdraws a reaction."""
    return {"id": eid(0xa000 + n), "pubkey": pubkey, "kind": 5, "created_at": created_at, "content": "",
            "tags": [["e", t] for t in targets], "sig": "00" * 64}


def fmsg(message_id, sender_open, content, *, sender_type="user", mentions=None, thread_id=None,
         when=NOW - timedelta(minutes=1), msg_type="text", deleted=False, name="某人"):
    sender = {"id": sender_open, "id_type": "open_id" if sender_type == "user" else "app_id",
              "name": name, "sender_type": sender_type}
    m = {"chat_id": CHAT, "content": content, "create_time": feishu_time(when), "deleted": deleted,
         "message_id": message_id, "msg_type": msg_type, "sender": sender, "updated": False}
    if mentions is not None:
        m["mentions"] = mentions
    if thread_id is not None:
        m["thread_id"] = thread_id
    return m


def text_of(call):
    return call["args"][call["args"].index("--text") + 1]


def union_of(open_id):
    """同一个人在飞书里的 union_id：按开发者（租户）唯一，与哪个应用无关。这里由 owner 应用的 open_id 确定性地推出。"""
    return "on_" + hashlib.sha256(b"union:" + open_id.encode()).hexdigest()[:32]


def bridge_open_of(open_id):
    """同一个人在 bridge 生产应用里的 open_id：open_id 按应用隔离，所以和 owner 应用里的不一样。"""
    return "ou_" + hashlib.sha256(b"bridge:" + open_id.encode()).hexdigest()[:32]


# ---- 图片样本：各种格式最小的、结构合法的一份，带或不带元数据。供魔数嗅探、元数据剥离与 FakeWorld 的「中继」用 ----
MEDIA_ORIGIN = "https://relay.test"  # 镜像 env 里的 BUZZ_RELAY_URL：imeta 的 url 都在这个 origin 的 /media/ 下（实测形态）


def _jpeg(*segments, scan=b"\x00\x11\x22\xff\xd9"):
    out = b"\xff\xd8"
    for marker, payload in segments:
        out += bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload
    return out + b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00" + scan


JFIF = (0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
DQT = (0xDB, b"\x00" + bytes(range(64)))
JPEG_PLAIN = _jpeg(JFIF, DQT)
JPEG_META = _jpeg(JFIF, (0xE1, b"Exif\x00\x00GPS-latitude-secret"), (0xE2, b"ICC_PROFILE\x00icc"), DQT,
                  (0xED, b"Photoshop 3.0 iptc"), (0xEE, b"Adobe\x00d\x00\x00\x00\x00\x01"), (0xEF, b"app15"), (0xFE, b"made with a phone"))


def png_chunk(kind: bytes, data: bytes = b"") -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IHDR = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
PNG_IDAT = png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
PNG_IEND = png_chunk(b"IEND")
PNG_PLAIN = PNG_SIGNATURE + PNG_IHDR + PNG_IDAT + PNG_IEND
PNG_METADATA_CHUNKS = (png_chunk(b"iCCP", b"icc\x00\x00zz"), png_chunk(b"cICP", b"\x09\x10\x00\x01"),
                       png_chunk(b"eXIf", b"MM\x00*gps"), png_chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
                       png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x/>"), png_chunk(b"tEXt", b"Comment\x00hi"),
                       png_chunk(b"tIME", bytes(7)), png_chunk(b"gAMA", struct.pack(">I", 45455)))
PNG_META = PNG_SIGNATURE + PNG_IHDR + b"".join(PNG_METADATA_CHUNKS) + PNG_IDAT + PNG_IEND
PNG_TRNS = png_chunk(b"tRNS", b"\x00\x00\x00")
PNG_PLTE = png_chunk(b"PLTE", b"\x00\x00\x00\xff\xff\xff")
PNG_APNG = (png_chunk(b"acTL", struct.pack(">II", 1, 0)), png_chunk(b"fcTL", bytes(26)), png_chunk(b"fdAT", bytes(8)))


def _gif(*extensions, version=b"89a"):
    header = b"GIF" + version + struct.pack("<HHBBB", 1, 1, 0x80, 0, 0) + b"\x00\x00\x00\xff\xff\xff"
    frame = b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00"
    return header + b"".join(extensions) + frame + b"\x3b"


GIF_GCE = b"\x21\xf9\x04\x00\x0a\x00\x00\x00"
GIF_LOOP = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
GIF_COMMENT = b"\x21\xfe\x0amade by me\x00"
GIF_XMP = b"\x21\xff\x0bXMP DataXMP\x06<xmp/>\x00"
GIF_PLAIN = _gif(GIF_GCE)
GIF_META = _gif(GIF_COMMENT, GIF_XMP, GIF_LOOP, GIF_GCE)


def _webp(*chunks):
    body = b"WEBP"
    for fourcc, payload in chunks:
        body += fourcc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _vp8x(flags):
    return (b"VP8X", bytes([flags]) + bytes(3) + bytes(3) + bytes(3))


WEBP_VP8L = (b"VP8L", b"\x2f\x00\x00\x00\x00\x00\x00")
WEBP_PLAIN = _webp(WEBP_VP8L)
WEBP_META = _webp(_vp8x(0x3C), (b"ICCP", b"icc"), WEBP_VP8L, (b"EXIF", b"Exif\x00\x00gps!"), (b"XMP ", b"<xmp/>"))
BMP_PLAIN = b"BM" + struct.pack("<IHHI", 58, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 2835, 2835, 0, 0) + b"\x00\x00\x00\x00"
TIFF_PLAIN = b"II*\x00" + struct.pack("<I", 8) + b"\x00\x00\x00\x00\x00\x00"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><script>alert(1)</script></svg>'


def has_image_metadata(data: bytes) -> bool:
    """The fake relay's idea of "media contains metadata" (实测: the relay answers 422 to it). Written apart from the script's
    stripper on purpose: a comment, EXIF / XMP / ICC, or anything that is not the picture."""
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 4 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            if marker == 0xDA:
                return not data.endswith(b"\xff\xd9")  # bytes after the end of the image are not the picture either
            if marker == 0xFE or 0xE1 <= marker <= 0xEF:
                return True
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        return False
    if data.startswith(PNG_SIGNATURE):
        i = 8
        while i + 8 <= len(data):
            size = struct.unpack(">I", data[i:i + 4])[0]
            if data[i + 4:i + 8] in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"iCCP", b"tIME", b"cICP"):
                return True
            i += 12 + size
        return not data.endswith(PNG_IEND)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        i = 12
        while i + 8 <= len(data):
            size = struct.unpack("<I", data[i + 4:i + 8])[0]
            if data[i:i + 4] in (b"ICCP", b"EXIF", b"XMP "):
                return True
            i += 8 + size + (size & 1)
        return struct.unpack("<I", data[4:8])[0] + 8 != len(data)
    if data[:3] == b"GIF":
        return b"\x21\xfe" in data or b"XMP Data" in data or not data.endswith(b"\x3b")
    return False


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def imeta_tag(sha, ext=".jpg", *, size=1234, url=None, **overrides):
    """NIP-92 imeta as `buzz messages send --file` writes it (实测 buzz-sg 的一条): 每个字段是「键 值」字符串。"""
    fields = {"url": url or f"{MEDIA_ORIGIN}/media/{sha}{ext}", "m": "image/jpeg", "x": sha, "size": str(size),
              "dim": "1366x1200", "blurhash": "LVQvzYIT~q-;M{WCkAWBITWBx]of", "thumb": f"{MEDIA_ORIGIN}/media/{sha}.thumb.jpg"}
    fields.update(overrides)
    return ["imeta", *[f"{k} {v}" for k, v in fields.items() if v is not None]]


def image_event(n, pubkey, caption, shas, *, created_at=T0, tags=(), ext=".jpg", size=1234):
    """A message with attachments as the Buzz CLI publishes it: the caption, then one ![image](url) line per file, and an
    imeta tag per file (实测: the CLI appends the markdown itself)."""
    content = caption + "".join(f"\n![image]({MEDIA_ORIGIN}/media/{sha}{ext})" for sha in shas)
    return event(eid(n), pubkey, content, created_at=created_at,
                 tags=[*tags, *[imeta_tag(sha, ext, size=size) for sha in shas]])


AT_RE = re.compile(r'<at user_id="([^"]+)">([^<]*)</at>')


def fail(args, code, subtype="api_error", type_="api", message="x"):
    body = json.dumps({"ok": False, "error": {"type": type_, "subtype": subtype, "code": code, "message": message}})
    return subprocess.CompletedProcess(args, 1, "", body)


CARD_AT_RE = re.compile(r"<at (id|email)=([^>\s]*)></at>")
MAX_CARD_BYTES = 30 * 1024  # Feishu refuses an interactive message of this size or more


class FakeWorld:
    """同时扮演 buzz CLI 与 lark-cli。lark 身份由子进程 env 的 LARKSUITE_CLI_CONFIG_DIR 决定：
    没有设置就是 owner 的个人应用；设置成某个 agent 的目录就是那个 agent 的应用。"""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.clock = NOW
        self.agent_dirs = {str(tmp / "agent-cfg"): AGENT_APP}
        self.profiles = {OWNER_APP: OWNER_OPEN, AGENT_APP: ""}  # app -> user open_id in `auth status`
        self.owner_profile_app = OWNER_APP
        self.key_to_pubkey = {MIRROR_KEY: MIRROR_PK, OWNER_KEY: OWNER_PK}
        # Buzz
        self.members = [
            {"pubkey": OWNER_PK, "role": "owner"},
            {"pubkey": ALICE_PK, "role": "member"},
            {"pubkey": BOB_PK, "role": "member"},
            {"pubkey": CAROL_PK, "role": "member"},
            {"pubkey": AGENT_PK, "role": "bot"},
            {"pubkey": AGENT2_PK, "role": "bot"},
            {"pubkey": MIRROR_PK, "role": "bot"},
        ]
        self.display = {OWNER_PK: "Owner", ALICE_PK: "Alice", BOB_PK: "Bob", AGENT_PK: "helper-agent",
                        CAROL_PK: "Carol", MIRROR_PK: "feishu-mirror", GUEST_PK: "Gina"}
        self.events = []
        self.buzz_calls = []
        self.buzz_send_fail = []  # modes popped per send: network | bad_input | rejected
        # 飞书
        self.chat = {"chat_id": CHAT, "owner_id": OWNER_OPEN, "user_manager_id_list": [],
                     "bot_manager_id_list": [OWNER_APP], "add_member_permission": "all_members",
                     "moderation_permission": "all_members", "external": False, "chat_status": "normal",
                     "chat_mode": "group", "bot_count": 2, "user_count": 1}
        self.users = {OWNER_OPEN}
        self.bots = {OWNER_APP: OWNER_BOT_MEMBER, AGENT_APP: AGENT_BOT_MEMBER}
        self.bot_members = {OWNER_APP: OWNER_BOT_MEMBER, AGENT_APP: AGENT_BOT_MEMBER,
                            TEAM_BOT_APP: "ou_teambot000000000000000000000001"}
        # bridge 那边：已验证的绑定（pubkey -> open_id）。接口只会返回其中属于本频道现存成员的那部分。
        self.bindings = {OWNER_PK: OWNER_OPEN, ALICE_PK: ALICE_OPEN, BOB_PK: BOB_OPEN,
                         OUTSIDER_PK: OUTSIDER_OPEN, GUEST_PK: GUEST_OPEN}
        self.emails = {}  # pubkey -> [邮箱]：不写就按显示名推出 <名字>@a4x.io
        self.emails_raw = {}  # pubkey -> bridge 应答里原样给出的地址（可以不规范）；通讯录仍按上面的规范邮箱
        self.bridge_omit = set()  # 响应里缺席的键：union_ids | emails
        self.union_missing = set()  # 这些人的 union_id 还没回填（有绑定，但 union_ids 里没有）
        self.union_to_open = {}  # union_id -> owner 应用的 open_id：飞书认得的全部人（谁被转换过就记下谁）
        self.user_info = None  # owner 的 user_info 出问题的方式：mismatch | error | bad_union
        self.message_get_fail = []  # 每次单条消息 GET 弹出一个：network | api
        self.message_get_tamper = None  # callable(message_id, user_id_type, item) -> item 或 [item, …]：让飞书「说错话」
        self.message_get_gone = set()  # 列出来以后才被删掉的消息：单条 GET 报 not_found
        self.message_update_fail = []  # modes popped per PUT/PATCH: rate_limited | network
        self.chat_owner_is_app = False  # 群主是个应用（bot 建的群）：owner_id_type 是 app_id
        # 飞书的通讯录：每个有绑定的人，他的每个邮箱都是他在 owner 应用里的账号的企业邮箱（可覆盖）
        self.feishu_emails = {}  # owner 应用 open_id -> {"email", "enterprise_email"}
        self.directory_noise = []  # 搜索会顺带返回的别的账号（模糊匹配的产物）
        self.directory_hidden = set()  # 这些邮箱在飞书里找不到精确的账号
        self.directory_override = {}  # 邮箱 -> 实际持有它的 owner 应用 open_id
        self.search_fail = {}  # 查询 -> 依次弹出的失败方式：network | api
        self.api_requests = []
        self.api_fail = []  # 每次调用弹出一个：network | timeout | 401 | 404 | 500 | 503 | redirect | malformed | ...
        # relay 的 POST /query（ADR-0019：agent 的飞书 app_id 公开在 kind:30177）：能查到的 kind 0 / 30177 事件、每次查询、失败方式
        self.relay_events = []
        self.relay_queries = []  # {"filters", "signer"} per verified /query
        self.relay_fail = []  # modes popped per /query: network | 500 | 429 | malformed | not_list | bad_signature
        # relay 的 POST /events（ADR-0020：成员变动 9000/9001 由 signer key、表情 7/5 由镜像身份在进程内签名后发出）
        self.relay_writes = []  # 每条被接受的事件（原样）
        self.relay_write_requests = []  # 每次 POST /events 的事件，包括运输结果未知的请求
        self.relay_write_attempts = []  # 每次 POST /events：{"event", "signer", "auth_tag"}（先于任何拒绝记录）
        self.relay_write_fail = []  # modes popped per /events: network | 500 | reject
        self.add_policy = {}  # agent pubkey -> "owner_only" | "nobody"（缺省 anyone）
        self.agent_owners = {}  # agent pubkey -> 它的 owner（owner_only 时只有他能加）
        self.needs_auth_tag = {MIRROR_PK}  # 这些身份是 NIP-OA agent：没有 x-auth-tag 头就不是 relay 成员
        self.members_incomplete = False  # `+chat-members-list` 说还有下一页（成员没读全）
        self.member_listing_fault = None  # 缺失 / 错类型的完整性元数据（删人必须 fail closed）
        self.batch_reactions = {}  # 飞书消息 id -> [{"emoji_type", "operator": {"operator_id", "operator_type"}}]
        self.batch_reaction_fail = []  # modes popped per `im reactions batch_query`: api | message_failed
        self.batch_reaction_calls = []  # 每次查询的消息 id 列表
        self.messages = []
        self.threads = {}  # root message id -> replies
        self.message_updates = []  # in-place PUT/PATCH calls, with the app identity that made them
        self.lark_calls = []
        self.owner_in_scope = True
        self.contact_error = None
        self.bot_scopes = {"im:chat:create", "im:message:send_as_bot"}
        self.created = []
        self.lark_send_fail = []  # modes popped per send/reply: timeout | rate_limited
        self.reject_ids = set()
        self.member_error_once = False
        self.sent_keys = {}
        self.before_send = None
        self.listings = []
        self.sends = 0
        self.sent_at = {}
        self.members_list_error = False
        self.on_create = None
        self.thread_polls = []
        self.thread_errors = set()
        self.reactions = []  # {"id", "message_id", "emoji", "app"}: what Feishu shows
        self.reaction_fail = []  # modes popped per reactions create/delete: timeout | rate_limited | network_envelope
        self.reaction_reads_fail = False  # `messages get --kinds 7,5` fails
        self.relay_ignores_since = False  # `messages get` returns events older than --since too
        self.after_buzz_send = None  # called with the event a `messages send` has just added
        # 图片：中继上的 Blossom 媒体（sha256 → 字节）、飞书上传过的图片、以及每次调用的工作目录
        self.media = {}  # sha256 -> bytes：`buzz media get` 能取到的
        self.media_reads = []  # {"segment", "key", "dir"} per successful `buzz media get`
        self.media_fail = []  # modes popped per `media get`: timeout | network | None
        self.resources = {}  # (飞书消息 id, image_key) -> bytes：`im +messages-resources-download` 能取到的
        self.resource_downloads = []  # 每次成功的下载：{"as", "message_id", "key", "type", "output", "dir", "dir_mode"}
        self.resource_fail = []  # modes popped per download: network | timeout | None
        self.resource_ext = None  # 服务器给的扩展名（默认按内容类型推断；实测：--output 没写扩展名时 lark-cli 会补上）
        self.resource_saved_path = None  # 让应答里的 saved_path 撒谎
        self.resource_symlink_to = None  # 不写文件，改成写一个指向这里的符号链接
        self.buzz_uploads = []  # 每次 `messages send --file`：{"sha", "size", "name", "dir", "dir_mode"}
        self.image_sends = []  # 每次 --image 的上传：{"app", "target", "sha", "size", "name", "key", "cwd_mode", "message_id"}
        self.workdirs = set()  # 每个子进程的 cwd 与 `media get -o` 的目录：一轮结束后都必须不存在
        self.card_reject = []  # modes popped per interactive send: content | validation | auth | permission (Feishu says no to this card)
        self.channel_name = "naturehood"  # `channels get`
        self.channel_get_fail = False
        self.thread_fail = []  # modes popped per `messages thread`: network | not_found | garbage
        self.thread_tamper = None  # callable(events) -> events: lets `messages thread` answer wrongly
        self.n = 0

    # ---- runner -------------------------------------------------------------------------------
    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None, check=False, env=None, cwd=None):
        env = dict(env or {})
        if argv[0].endswith("/buzz-0.5.23/usr/bin/buzz"):
            out = self._buzz(argv[1:], input, env)
        elif argv[0] == LARK_CLI:
            out = self._lark(argv[1:], env, cwd)
        else:
            raise AssertionError(f"unexpected executable {argv[0]}")
        if isinstance(out, subprocess.CompletedProcess):
            return out
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    def _next(self, prefix):
        self.n += 1
        return f"{prefix}{self.n:06d}"

    @staticmethod
    def _opt(args, name, many=False):
        vals = [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]
        return vals if many else (vals[0] if vals else None)

    # ---- buzz ---------------------------------------------------------------------------------
    def _buzz(self, args, content, env):
        key = env.get("BUZZ_PRIVATE_KEY")
        self.buzz_calls.append({"args": list(args), "content": content, "key": key, "env": env})
        cmd = tuple(args[:2])
        if cmd == ("channels", "members"):
            return list(self.members)
        if cmd == ("channels", "get"):
            assert self._opt(args, "--channel") == CHANNEL
            if self.channel_get_fail:
                return subprocess.CompletedProcess(args, 2, "", '{"error":"relay"}')
            return {"id": CHANNEL, "name": self.channel_name}
        if cmd == ("users", "get"):
            pks = self._opt(args, "--pubkey", many=True)
            if not pks:
                me = self.key_to_pubkey.get(key)
                return [{"pubkey": me, "display_name": self.display.get(me, "")}] if me else []
            return [{"pubkey": p, "display_name": self.display[p]} for p in pks if p in self.display]
        if cmd == ("messages", "get"):
            since = int(self._opt(args, "--since") or 0)
            before = self._opt(args, "--before")
            kinds = {int(k) for k in (self._opt(args, "--kinds") or "9").split(",")}
            if self.reaction_reads_fail and kinds & {5, 7}:
                return subprocess.CompletedProcess(args, 2, "", '{"error":"relay"}')
            limit = min(int(self._opt(args, "--limit") or 50), 200)
            rows = [e for e in self.events if (e["created_at"] >= since or self.relay_ignores_since) and e["kind"] in kinds
                    and (before is None or e["created_at"] <= int(before))]
            return sorted(rows, key=lambda e: e["created_at"], reverse=True)[:limit]
        if cmd == ("messages", "thread"):
            return self._thread(args)
        if cmd == ("media", "get"):
            return self._media_get(args, key)
        if cmd == ("messages", "send"):
            assert self._opt(args, "--content") == "-", "content must go through stdin"
            if self.before_send:
                self.before_send("buzz", content)
            if self.buzz_send_fail:
                mode = self.buzz_send_fail.pop(0)
                if mode == "network":
                    return subprocess.CompletedProcess(args, 2, "", '{"error":"relay"}')
                if mode == "bad_input":
                    return subprocess.CompletedProcess(args, 1, "", '{"error":"input"}')
                return {"accepted": False}
            author = self.key_to_pubkey[key]
            tags = [["h", CHANNEL]]
            attached = ""
            for name in self._opt(args, "--file", many=True):
                # 实测：--file 上传并写 imeta tag，正文后面自己加 `![image](url)`；relay 对带元数据的媒体回 422（CLI 报 relay 错误、exit 2）。
                path = Path(name)
                if not path.is_file():
                    return subprocess.CompletedProcess(args, 1, "", '{"error":"user_error","message":"file not found"}')
                data = path.read_bytes()
                if has_image_metadata(data):
                    return subprocess.CompletedProcess(args, 2, "", '{"error":"relay_error","message":"relay error 422: media contains metadata","retryable":false}')
                sha = sha_of(data)
                self.media[sha] = data
                self.workdirs.add(str(path.parent))
                self.buzz_uploads.append({"sha": sha, "size": len(data), "name": path.name, "dir": str(path.parent),
                                          "dir_mode": os.stat(path.parent).st_mode & 0o777,
                                          "file_mode": os.stat(path).st_mode & 0o777, "dir_files": sorted(os.listdir(path.parent))})
                ext = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}.get(self._magic(data), "")
                tags.append(imeta_tag(sha, ext))
                attached += f"\n![image]({MEDIA_ORIGIN}/media/{sha}{ext})"
            reply_to = self._opt(args, "--reply-to")
            if reply_to:
                tags.append(["e", reply_to, "", "reply"])
            mentioned = list(self._opt(args, "--mention", many=True))
            # buzz 0.5.23 `messages send --help`: "uniquely resolved member names still notify" —
            # the CLI itself turns @Name text into a mention when Name is one channel member.
            member_pks = {m["pubkey"] for m in self.members}
            for token in re.findall(r"@(\S+)", content):
                owners = [pk for pk, name in self.display.items() if name == token and pk in member_pks]
                if len(owners) == 1:
                    mentioned.append(owners[0])
            for token in re.findall(r"nostr:(npub1\w+|[0-9a-f]{64})", content):
                mentioned.append(token)
            for p in dict.fromkeys(mentioned):
                tags.append(["p", p])
            new_id = eid(0x5000 + len(self.events))
            self.sends += 1  # real sends take time: about five per second
            self.events.append(event(new_id, author, content + attached, created_at=ts(self.clock) + self.sends // 5, tags=tags))
            if self.after_buzz_send:
                self.after_buzz_send(self.events[-1])
            return {"event_id": new_id, "accepted": True}
        raise AssertionError(f"unexpected buzz command {args}")

    def _thread(self, args):
        """buzz 0.5.23 `messages thread`，照 2026-09-20 对真实频道的只读实测建模：返回一个 JSON 数组，元素和 `messages get`
        的事件一模一样；第一个是话题根（没有 e tag），后面是这个话题里的回复，按 created_at 从旧到新；不管 --event 指的是根、
        直接回复还是嵌套回复，得到的都是整个话题。`--depth-limit N`：只留嵌套深度不超过 N 的回复（直接回复深度 1，0 就只有根）；
        `--limit N`：根加上最新的 N 条回复（所以可能不含被问的那条事件）。事件不存在退出码 1、stderr 里 `not_found`；
        事件不属于这个频道退出码 1、`user_error`；id 不是 64 位 hex 也是 `user_error`。"""
        mode = self.thread_fail.pop(0) if self.thread_fail else None
        if mode == "network":
            return subprocess.CompletedProcess(args, 2, "", '{"error":"relay","retryable":true}')
        if mode == "not_found":
            return subprocess.CompletedProcess(args, 1, "", '{"error":"not_found","retryable":false}')
        if mode == "garbage":
            return {"not": "a list"}
        channel, event_id = self._opt(args, "--channel"), self._opt(args, "--event")
        if not re.fullmatch(r"[0-9a-f]{64}", event_id or ""):
            return subprocess.CompletedProcess(args, 1, "", '{"error":"user_error","retryable":false}')
        by_id = {e["id"]: e for e in self.events}
        if event_id not in by_id:
            return subprocess.CompletedProcess(args, 1, "", '{"error":"not_found","retryable":false}')
        if channel != CHANNEL:
            return subprocess.CompletedProcess(args, 1, "", '{"error":"user_error","retryable":false}')

        def parents(e):
            return {t[3]: t[1] for t in e["tags"] if t[0] == "e" and len(t) >= 4}

        def chain(e):  # the ids from e up to the top-level message
            ids = [e["id"]]
            while parents(by_id[ids[-1]]).get("reply") in by_id:
                ids.append(parents(by_id[ids[-1]])["reply"])
            return ids

        root_id = chain(by_id[event_id])[-1]
        replies = [e for e in self.events if e["id"] != root_id and chain(e)[-1] == root_id]
        depth = self._opt(args, "--depth-limit")
        if depth is not None:
            replies = [e for e in replies if len(chain(e)) - 1 <= int(depth)]
        replies.sort(key=lambda e: (e["created_at"], e["id"]))
        limit = self._opt(args, "--limit")
        if limit is not None:
            replies = replies[max(len(replies) - int(limit), 0):] if int(limit) else []
        answer = [by_id[root_id], *replies]
        return self.thread_tamper(answer) if self.thread_tamper else answer

    def _resource_download(self, args, cwd, as_, call):
        """`im +messages-resources-download --message-id --file-key --type image --output <相对路径>`，照实测建模：输出路径必须相对当前目录
        （绝对路径和 .. 被拒）；--output 没写扩展名时服务器按内容类型补一个（`fimg0` 存成了 `fimg0.png`）；应答里的 saved_path 是绝对路径。"""
        mode = self.resource_fail.pop(0) if self.resource_fail else None
        if mode == "timeout":
            raise subprocess.TimeoutExpired(args, 90)
        if mode == "network":
            return subprocess.CompletedProcess(args, 1, "", json.dumps({"ok": False, "error": {"type": "network", "message": "connection reset"}}))
        message_id, key, kind, output = (self._opt(args, o) for o in ("--message-id", "--file-key", "--type", "--output"))
        self.workdirs.add(str(cwd))
        if output is None or os.path.isabs(output) or ".." in Path(output).parts or kind != "image" or as_ not in ("user", "bot"):
            body = {"ok": False, "error": {"type": "validation", "subtype": "invalid_argument", "message": "bad arguments"}}
            return subprocess.CompletedProcess(args, 2, "", json.dumps(body))
        blob = self.resources.get((message_id, key))
        if blob is None:
            return fail(args, 234003, "file_not_in_msg")
        ext = self.resource_ext if self.resource_ext is not None else {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "bmp": ".bmp", "tiff": ".tiff"}.get(self._magic(blob), ".bin")
        path = Path(cwd) / (output if Path(output).suffix else output + ext)
        if self.resource_symlink_to:
            path.symlink_to(self.resource_symlink_to)
        else:
            path.write_bytes(blob)
        self.resource_downloads.append({"as": as_, "message_id": message_id, "key": key, "type": kind, "output": output,
                                        "dir": str(cwd), "dir_mode": os.stat(cwd).st_mode & 0o777})
        return {"ok": True, "data": {"saved_path": self.resource_saved_path or str(path), "size_bytes": len(blob)}}

    def _media_get(self, args, key):
        """`buzz media get <sha256[.ext]> -o <路径>`，照实测建模：要鉴权（没有已知的 key 是 exit 3）；参数只认 sha256、sha256.ext、
        sha256.thumb.jpg（别的形状 exit 1，包括整个 url 指向别的主机）；中继没有这个 blob 是 404（exit 2）；给了 -o 就写文件、stdout 为空。"""
        mode = self.media_fail.pop(0) if self.media_fail else None
        if mode == "timeout":
            raise subprocess.TimeoutExpired(args, 120)
        if mode == "network":
            return subprocess.CompletedProcess(args, 2, "", '{"error":"relay_error","message":"connection reset"}')
        if key not in self.key_to_pubkey:
            return subprocess.CompletedProcess(args, 3, "", '{"error":"auth_error","message":"authentication failed"}')
        segment, out = args[2], self._opt(args, "-o")
        found = re.fullmatch(r"([0-9a-f]{64})(\.[a-z0-9]{1,5})?(\.thumb\.jpg)?", segment)
        if not found:
            return subprocess.CompletedProcess(args, 1, "", '{"error":"user_error","message":"media path must be sha256, sha256.ext, or sha256.thumb.jpg"}')
        blob = self.media.get(found.group(1))
        if blob is None:
            return subprocess.CompletedProcess(args, 2, "", '{"error":"relay_error","message":"relay error 404: {\\"error\\":\\"not found\\"}"}')
        assert out and out != "-", "the script always downloads to a file"
        self.workdirs.add(str(Path(out).parent))
        self.media_reads.append({"segment": segment, "key": key, "dir": str(Path(out).parent)})
        Path(out).write_bytes(blob)
        return subprocess.CompletedProcess(args, 0, "", "")

    @staticmethod
    def _magic(data):
        """The fake Feishu's own idea of an image (the formats its upload takes) — independent of the script's sniffing."""
        for prefix, kind in ((b"\xff\xd8\xff", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF8", "gif"), (b"BM", "bmp"),
                             (b"II*\x00", "tiff"), (b"MM\x00*", "tiff")):
            if data.startswith(prefix):
                return kind
        return "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None

    def _image_upload(self, args, app, cwd, key, target):
        """lark-cli 的 --image：路径必须是相对当前目录的（绝对路径和 .. 直接被拒，实测），文件读出来上传（飞书：≤ 10 MB 的
        jpg/png/webp/gif/bmp/tiff，否则接口报错，type=api）。返回 (image_key, 错误响应或 None)。"""
        name = self._opt(args, "--image")
        self.workdirs.add(str(cwd))  # None (the script never set one) shows up here and fails the round-level assertions

        def invalid(message):
            body = {"ok": False, "error": {"type": "validation", "subtype": "invalid_argument", "message": message}}
            return None, subprocess.CompletedProcess(args, 2, "", json.dumps(body))
        if os.path.isabs(name) or ".." in Path(name).parts:
            return invalid("--image: --file must be a relative path within the current directory")
        if len(key or "") > 50:
            return invalid("--idempotency-key: at most 50 characters")
        cwd = cwd or os.getcwd()  # lark-cli resolves the path against its own working directory
        path = Path(cwd) / name
        if not path.is_file():
            return invalid("--image: no such file")
        data = path.read_bytes()
        if len(data) > 10 * 1024 * 1024 or self._magic(data) is None:
            return None, fail(args, 234001, "invalid_image")
        sha = hashlib.sha256(data).hexdigest()
        self.image_sends.append({"app": app, "target": target, "sha": sha, "size": len(data), "name": name, "key": key,
                                 "cwd_mode": os.stat(cwd).st_mode & 0o777, "dir_files": sorted(os.listdir(cwd)), "message_id": None})
        return "img_v3_" + sha[:24], None

    # ---- bridge：GET /bind/api/channels/{channel}/people（infra/buzz-deploy ADR-0015）--------------
    def http_get(self, url, headers, timeout, body=None):
        """扮演 bridge：真的按 NIP-98 验签，语义与 Go 侧 VerifyNIP98 + web.handlePeople 一致。
        带 body 的 POST 到 relay 的 /query 由 _serve_relay_query 扮演（不计入 api_requests，也不消耗 api_fail）。"""
        if url == f"{MEDIA_ORIGIN}/query":
            return self._serve_relay_query(url, headers, body)
        if url == f"{MEDIA_ORIGIN}/events":
            return self._serve_relay_events(url, headers, body)
        if body is not None:
            raise AssertionError(f"unexpected POST to {url}")
        self.api_requests.append({"url": url, "headers": dict(headers), "timeout": timeout})
        mode = self.api_fail.pop(0) if self.api_fail else None
        if mode == "network":
            raise OSError("connection refused")
        if mode == "timeout":
            raise TimeoutError("timed out")
        if mode in ("401", "404", "500", "503"):
            return int(mode), b'{"error":"x"}'
        if mode == "redirect":
            return 302, b""
        if mode == "malformed":
            return 200, b"<html>not json</html>"
        if mode == "oversize":
            return 200, b"{" + b" " * (FGS.PEOPLE_API_MAX_BYTES + 1) + b"}"
        status, body = self._serve_people(url, headers)
        if status != 200 or mode is None:
            return status, body
        doc = json.loads(body)
        if mode == "wrong_channel":
            doc["channel"] = "00000000-0000-4000-8000-000000000000"
        elif mode == "bad_key":
            doc["people"]["XYZ"] = "ou_x"
        elif mode == "bad_open_id":
            doc["people"][ALICE_PK] = "alice@a4x.io"
        elif mode == "people_not_object":
            doc["people"] = [ALICE_PK]
        elif mode == "no_people":
            del doc["people"]
        elif mode == "union_not_object":
            doc["union_ids"] = [ALICE_PK]
        elif mode == "union_bad_key":
            doc["union_ids"]["XYZ"] = "on_x"
        elif mode == "union_bad_value":
            doc["union_ids"][ALICE_PK] = ALICE_OPEN  # 是 open_id，不是 union_id
        elif mode == "union_value_not_string":
            doc["union_ids"][ALICE_PK] = 7
        elif mode == "emails_not_object":
            doc["emails"] = [ALICE_PK]
        elif mode == "emails_bad_key":
            doc["emails"][("AB" * 32)] = ["a@a4x.io"]
        elif mode == "emails_value_not_list":
            doc["emails"][ALICE_PK] = "alice@a4x.io"
        elif mode == "emails_empty_list":
            doc["emails"][ALICE_PK] = []
        elif mode == "emails_bad_address":
            doc["emails"][ALICE_PK] = ["not-an-email"]
        elif mode == "emails_null":
            doc["emails"] = None
        elif mode == "not_object":
            doc = ["people"]
        else:
            raise AssertionError(f"unknown api failure mode {mode}")
        return 200, json.dumps(doc).encode()

    def _serve_people(self, url, headers):
        not_found = (404, b'{"error":"not_found"}')
        if url != API_URL:
            return not_found
        header = headers.get("Authorization", "")
        signer = self._verify_nip98(header, "GET", url)
        if signer is None:
            return 401, b'{"error":"invalid_signature"}'
        live = {m["pubkey"]: m["role"] for m in self.members}
        if live.get(signer) not in ("owner", "admin"):
            return not_found
        bound = [pk for pk in live if pk in self.bindings]
        doc = {"channel": CHANNEL, "as_of": self.clock.isoformat(),
               "people": {pk: bridge_open_of(self.bindings[pk]) for pk in bound},
               "union_ids": {pk: union_of(self.bindings[pk]) for pk in bound if pk not in self.union_missing},
               "emails": {pk: list(self.emails_raw.get(pk) or self.emails_of(pk)) for pk in bound}}
        for key in self.bridge_omit:  # 老版本的 bridge 没有 union_ids；没开 CHANNEL_PEOPLE_EMAILS_ENABLED 就没有 emails
            doc.pop(key, None)
        return 200, json.dumps(doc).encode()

    def _serve_relay_query(self, url, headers, body):
        """扮演 relay 的 POST /query（buzz-relay api/bridge.rs + buzz-auth nip98.rs）：kind 27235、u 与 method 各一个、±60 秒、
        带 payload 就必须等于 body 的 sha256；body 是 filters 的 JSON 数组，应答是事件的 JSON 数组。"""
        mode = self.relay_fail.pop(0) if self.relay_fail else None
        if mode == "network":
            raise OSError("connection refused")
        if mode in ("500", "429"):
            return int(mode), b'{"error":"x"}'
        signer = self._verify_relay_nip98(headers.get("Authorization", ""), url, body)
        if signer is None or mode == "bad_signature":
            return 401, b'{"error":"NIP-98"}'
        filters = json.loads(body)
        self.relay_queries.append({"filters": filters, "signer": signer})
        if mode == "malformed":
            return 200, b"<html>"
        if mode == "not_list":
            return 200, b'{"events":[]}'

        def match(ev, f):
            if "kinds" in f and ev["kind"] not in f["kinds"]:
                return False
            if "authors" in f and ev["pubkey"] not in f["authors"]:
                return False
            if "#d" in f and not any(t[0] == "d" and t[1] in f["#d"] for t in ev["tags"]):
                return False
            return True
        return 200, json.dumps([ev for ev in self.relay_events if any(match(ev, f) for f in filters)]).encode()

    def _serve_relay_events(self, url, headers, body):
        """扮演 relay 的 POST /events（buzz-relay api/bridge.rs submit_event + side_effects.rs），照源码建模：
        - NIP-98 同 /query（带 payload 就必须等于 body 的 sha256）；事件本身 id 与签名都要对，作者就是签名头的人；
        - NIP-OA agent（needs_auth_tag）没有 x-auth-tag 头就不是 relay 成员（403）；
        - 9000 / 9001：h 是本频道、签名者是 owner/admin；9000 还看被加者的 channel_add_policy（owner_only 只许它的 owner、nobody 谁都不许），
          拒绝文本就是源码里的 `policy:owner_only …` / `policy:nobody …`；role 标签决定角色（缺省 member）；
        - 7 / 5：签名者得是频道成员，7 的 e 目标得存在；接受后事件进入 events，之后 `messages get` 读得到。
        应答：200 {"event_id","accepted","message"}；拒绝是 400 {"error": …}。"""
        self.relay_write_requests.append(json.loads(body))
        mode = self.relay_write_fail.pop(0) if self.relay_write_fail else None
        if mode == "network":
            raise OSError("connection reset")
        if mode == "500":
            return 500, b'{"error":"x"}'
        if mode == "lost":  # stored, but the answer never arrives: the caller cannot tell it apart from "network"
            self._serve_relay_events(url, headers, body)
            raise OSError("connection reset after the request was sent")
        signer = self._verify_relay_nip98(headers.get("Authorization", ""), url, body)
        if signer is None:
            return 401, b'{"error":"NIP-98"}'
        ev = json.loads(body)
        self.relay_write_attempts.append({"event": ev, "signer": signer, "auth_tag": headers.get("x-auth-tag")})
        ser = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]], separators=(",", ":"),
                         ensure_ascii=False)
        if (hashlib.sha256(ser.encode()).hexdigest() != ev["id"] or ev["pubkey"] != signer
                or not FGS.sync.nk.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"]))):
            return 400, b'{"error":"invalid: bad event id or signature"}'
        if signer in self.needs_auth_tag and not headers.get("x-auth-tag"):
            return 403, b'{"error":"restricted: not a relay member"}'
        if mode == "reject":
            return 400, b'{"error":"invalid: rejected"}'
        roles = {m["pubkey"]: m["role"] for m in self.members}
        tags = {t[0]: t[1] for t in ev["tags"] if len(t) >= 2}
        if any(e["id"] == ev["id"] for e in self.events) or any(w["id"] == ev["id"] for w in self.relay_writes):
            return 200, json.dumps({"event_id": ev["id"], "accepted": True, "message": "duplicate:"}).encode()
        if ev["kind"] == 7 and any(e["kind"] == 7 and e["pubkey"] == signer and e["content"] == ev["content"]
                                   and [t[1] for t in e["tags"] if t[0] == "e"] == [tags.get("e")] for e in self.events):
            # relay-v0.2.1 ingest.rs: one reaction per (actor, target, emoji)
            return 200, json.dumps({"event_id": ev["id"], "accepted": False, "message": "duplicate: reaction already exists"}).encode()
        if ev["kind"] in (9000, 9001):
            if tags.get("h") != CHANNEL or roles.get(signer) not in ("owner", "admin"):
                return 400, b'{"error":"restricted: only an owner or admin can change members"}'
            target = tags["p"]
            if ev["kind"] == 9000:
                policy = self.add_policy.get(target)
                if target != signer and policy == "nobody":
                    return 400, json.dumps({"error": "policy:nobody — this agent has disabled external channel additions"}).encode()
                if target != signer and policy == "owner_only" and self.agent_owners.get(target) != signer:
                    return 400, json.dumps({"error": "policy:owner_only — only the agent owner can add this agent"}).encode()
                self.members = [m for m in self.members if m["pubkey"] != target] + [
                    {"pubkey": target, "role": tags.get("role", "member")}]
            else:
                self.members = [m for m in self.members if m["pubkey"] != target]
        elif ev["kind"] in (7, 5, 9, 40003):
            if signer not in roles:
                return 400, b'{"error":"restricted: not a channel member"}'
            if ev["kind"] == 7 and tags.get("e") not in {e["id"] for e in self.events}:
                return 400, b'{"error":"invalid: unknown target"}'
            if ev["kind"] == 40003:
                targets = [e for e in self.events if e["id"] == tags.get("e") and e["pubkey"] == signer]
                if len(targets) != 1:
                    return 400, b'{"error":"invalid: unknown edit target"}'
            if ev["kind"] == 5:  # the relay stops returning a deleted reaction
                gone = {t[1] for t in ev["tags"] if t[0] == "e"}
                self.events = [e for e in self.events if not (e["id"] in gone and e["pubkey"] == signer)]
            self.events.append(dict(ev))
        else:
            return 400, b'{"error":"invalid: unexpected kind"}'
        self.relay_writes.append(ev)
        return 200, json.dumps({"event_id": ev["id"], "accepted": True, "message": ""}).encode()

    def member_writes(self):
        """(kind, target pubkey, role) of every accepted 9000 / 9001."""
        return [(ev["kind"], next(t[1] for t in ev["tags"] if t[0] == "p"), next((t[1] for t in ev["tags"] if t[0] == "role"), None))
                for ev in self.relay_writes if ev["kind"] in (9000, 9001)]

    def _verify_relay_nip98(self, header, url, body):
        if not header.startswith("Nostr "):
            return None
        try:
            ev = json.loads(base64.b64decode(header[6:], validate=True))
            ser = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                             separators=(",", ":"), ensure_ascii=False)
            if hashlib.sha256(ser.encode()).hexdigest() != ev["id"]:
                return None
            if not FGS.sync.nk.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"])):
                return None
            us = [t[1] for t in ev["tags"] if t[0] == "u"]
            ms = [t[1] for t in ev["tags"] if t[0] == "method"]
            payload = [t[1] for t in ev["tags"] if t[0] == "payload"]
            if ev["kind"] != 27235 or us != [url] or [m.upper() for m in ms] != ["POST"]:
                return None
            if payload and payload != [hashlib.sha256(body).hexdigest()]:
                return None
            if abs(ev["created_at"] - ts(self.clock)) > 60:
                return None
            return ev["pubkey"]
        except (ValueError, KeyError, TypeError, IndexError):
            return None

    def emails_of(self, pubkey):
        """这个人在 bridge 里已验证的邮箱（小写、去重、排序）。"""
        return self.emails.get(pubkey) or [f"{self.display.get(pubkey, pubkey[:8]).lower()}@a4x.io"]

    def _verify_nip98(self, header, method, url):
        """Go 侧 VerifyNIP98 的要点：头严格、事件 id 与 BIP-340 签名、kind 27235、±60 秒、u 与 method 各一个。"""
        if not header.startswith("Nostr ") or len(header) > 8192 or any(c in header[6:] for c in " \t\r\n"):
            return None
        try:
            ev = json.loads(base64.b64decode(header[6:], validate=True))
            if set(ev) != {"id", "pubkey", "created_at", "kind", "tags", "content", "sig"}:
                return None
            body = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                              separators=(",", ":"), ensure_ascii=False)
            if hashlib.sha256(body.encode()).hexdigest() != ev["id"]:
                return None
            if not FGS.sync.nk.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"])):
                return None
            us = [t[1] for t in ev["tags"] if t[0] == "u"]
            ms = [t[1] for t in ev["tags"] if t[0] == "method"]
            payload = [t for t in ev["tags"] if t[0] == "payload"]
            if ev["kind"] != 27235 or us != [url] or [m.upper() for m in ms] != [method] or payload:
                return None
            if abs(ev["created_at"] - ts(self.clock)) > 60:
                return None
            return ev["pubkey"]
        except (ValueError, KeyError, TypeError, IndexError):
            return None

    # ---- lark ---------------------------------------------------------------------------------
    def _lark(self, args, env, cwd=None):
        cfg_dir = env.get("LARKSUITE_CLI_CONFIG_DIR")
        app = self.agent_dirs.get(cfg_dir) if cfg_dir else self.owner_profile_app
        as_ = self._opt(args, "--as")
        call = {"args": list(args), "app": app, "as": as_, "env": env, "cwd": cwd}
        self.lark_calls.append(call)
        head = tuple(args[:2])
        ok = lambda data: {"ok": True, "data": data}
        if head == ("auth", "status"):
            assert "--format" not in args
            return {"appId": app, "identities": {"user": {"openId": self.profiles.get(app, "")}}}
        if head == ("im", "+chat-members-list"):
            if self.members_list_error:
                return fail(args, 99991400)
            id_type = self._opt(args, "--member-id-type") or "open_id"
            kinds = self._opt(args, "--member-types", many=True) or ["user", "bot"]
            data = {"users": [{"member_id": self.conv(u, id_type), "name": u} for u in sorted(self.users)] if "user" in kinds else [],
                    "bots": [{"app_id": a, "member_id": self.conv(m, id_type), "name": a}
                             for a, m in sorted(self.bots.items())] if "bot" in kinds else []}
            data.update(has_more=False, truncations=[], user_total=len(data["users"]), bot_total=len(data["bots"]))
            if self.members_incomplete:  # 实测形态：has_more / truncations / *_total 说明没读全
                data.update(has_more=True, page_token="next", user_total=len(data["users"]) + 1)
            fault = self.member_listing_fault
            requested = kinds[0]
            if fault == "missing_has_more":
                data.pop("has_more")
            elif fault == "missing_truncations":
                data.pop("truncations")
            elif fault == "truncations_object":
                data["truncations"] = {}
            elif fault == "missing_total":
                data.pop(f"{requested}_total")
            elif fault == "bool_total":
                data[f"{requested}_total"] = False
            elif fault == "rows_object":
                data[f"{requested}s"] = {"not": "a list"}
                data[f"{requested}_total"] = 1
            return ok(data)
        if head == ("contact", "+search-user"):
            assert as_ == "user"
            query = self._opt(args, "--query")
            mode = self.search_fail.get(query, []).pop(0) if self.search_fail.get(query) else None
            if mode == "network":
                return subprocess.CompletedProcess(args, 1, "", json.dumps(
                    {"ok": False, "error": {"type": "network", "message": "connection reset"}}))
            if mode == "api":
                return fail(args, 99991400)
            found = []
            for pk, local in self.bindings.items():
                for email in self.emails_of(pk):
                    if email in self.directory_hidden:
                        continue
                    local = self.directory_override.get(email, local)
                    fields = self.feishu_emails.get(local) or {"email": "", "enterprise_email": email}
                    found.append({"open_id": local, "name": "某人", "is_cross_tenant": False, **fields})
            found += [dict(user) for user in self.directory_noise]
            # 真实的搜索是模糊的（不分大小写、子串）：返回的账号未必就是要找的人
            users = [u for u in found if query.lower() in (u.get("email", "") + "\n" + u.get("enterprise_email", "")).lower()]
            return ok({"users": users, "has_more": False})
        if head == ("im", "+chat-messages-list"):
            start = datetime.fromisoformat(self._opt(args, "--start")).replace(second=0, microsecond=0)
            rows = [m for m in self.messages if datetime.strptime(m["create_time"], "%Y-%m-%d %H:%M")
                    .replace(tzinfo=timezone.utc) >= start]
            rows = sorted(rows, key=lambda m: m["create_time"], reverse=self._opt(args, "--order") == "desc")
            cap = int(self._opt(args, "--page-limit") or 10) * 50  # lark-cli --page-all stops at --page-limit pages
            self.listings.append({"order": self._opt(args, "--order"), "start": start, "cap": cap})
            return ok({"messages": rows[:cap], "has_more": len(rows) > cap})
        if head == ("im", "+threads-messages-list"):
            root = self._opt(args, "--thread")
            self.thread_polls.append(root)
            if root in self.thread_errors:
                return fail(args, 99991400)
            if root not in self.threads:
                return fail(args, None, "not_found")
            rows = sorted(self.threads[root], key=lambda m: m["create_time"], reverse=self._opt(args, "--order") == "desc")
            cap = int(self._opt(args, "--page-limit") or 10) * 50
            return ok({"messages": rows[:cap], "has_more": len(rows) > cap, "thread_id": "omt_" + root})
        if head in (("im", "+messages-send"), ("im", "+messages-reply")):
            key = self._opt(args, "--idempotency-key")
            if self.before_send:
                self.before_send("lark", key)
            mode = self.lark_send_fail.pop(0) if self.lark_send_fail else None
            if mode == "timeout":
                raise subprocess.TimeoutExpired(args, 90)
            if mode == "rate_limited":
                return fail(args, 230020, "rate_limited")
            image_key = None
            if self._opt(args, "--image") is not None:  # 上传发生在发送之前，同一个幂等键重试时会再上传一次（实测：先上传、后 POST）
                target = ("reply", self._opt(args, "--message-id")) if head[1] == "+messages-reply" else ("chat", self._opt(args, "--chat-id"))
                image_key, refusal = self._image_upload(args, app, cwd, key, target)
                if refusal is not None:
                    return refusal
            interactive = self._opt(args, "--msg-type") == "interactive"
            if interactive:
                refusal = self._card_refusal(args)
                if refusal is not None:
                    return refusal
            dedupe = (head[1], key)  # Feishu dedupes one idempotency key per API for about an hour
            if dedupe in self.sent_keys and self.clock - self.sent_at[dedupe] < timedelta(hours=1):
                return ok({"message_id": self.sent_keys[dedupe], "chat_id": CHAT})
            if image_key:
                text_ = f"[Image: {image_key}]"
            else:
                text_ = self._opt(args, "--content") if interactive else self._opt(args, "--text")
            mid = self._next("om_sent")
            if image_key:
                self.image_sends[-1]["message_id"] = mid
            self.sent_keys[dedupe] = mid
            self.sent_keys[key] = mid
            self.sent_at[dedupe] = self.clock
            # <at user_id="on_…"> 直接认 union_id；发出后 mentions 里解析成 open_id（列表接口永远给 open_id）
            mentions = [{"id": self.local_of_union(i) or i, "key": f"@_user_{k}", "name": n}
                        for k, (i, n) in enumerate(AT_RE.findall(text_), 1)]
            if interactive:  # a card's <at id=ou_…> is a mention of that open_id; an address is resolved by Feishu
                mentions = [{"id": value, "key": f"@_user_{k}", "name": ""}
                            for k, (kind, value) in enumerate(CARD_AT_RE.findall(text_), 1) if kind == "id" and value != "all"]
                msg = fmsg(mid, app, text_, sender_type="app", mentions=mentions or None, when=self.clock, msg_type="interactive")
            else:
                msg = fmsg(mid, app, AT_RE.sub(lambda m: "@" + m.group(2), text_), sender_type="app",
                           mentions=mentions or None, when=self.clock, msg_type="image" if image_key else "text")
            if head[1] == "+messages-reply":
                parent = self._opt(args, "--message-id")
                self.threads.setdefault(parent, []).append(dict(msg, thread_id="omt_" + parent))
                for m in self.messages:
                    if m["message_id"] == parent:
                        m["thread_id"] = "omt_" + parent
            else:
                self.messages.append(msg)
            if mode == "network_envelope":  # delivered, but lark-cli reports a transport error
                return subprocess.CompletedProcess(args, 1, "", json.dumps(
                    {"ok": False, "error": {"type": "network", "message": "connection reset"}}))
            return ok({"message_id": mid, "chat_id": CHAT})
        if head == ("im", "+messages-resources-download"):
            return self._resource_download(args, cwd, as_, call)
        if head == ("im", "reactions"):
            return self._reactions(args, app)
        if head == ("im", "+chat-create"):
            self.created.append(call)
            if self.on_create:
                self.on_create()
            return ok({"chat_id": "oc_created0000000000000000000000001", "owner_id": self._opt(args, "--owner")})
        if args[0] == "api":
            method, path = args[1], args[2]
            params = json.loads(self._opt(args, "--params") or "{}")
            data = json.loads(self._opt(args, "--data") or "{}")
            if method in ("PUT", "PATCH") and path.startswith("/open-apis/im/v1/messages/"):
                mode = self.message_update_fail.pop(0) if self.message_update_fail else None
                if mode == "rate_limited":
                    return fail(args, 230020, "rate_limited")
                if mode == "network":
                    return subprocess.CompletedProcess(args, 1, "", json.dumps(
                        {"ok": False, "error": {"type": "network", "message": "connection reset"}}))
                mid = path.rsplit("/", 1)[1]
                msg = next((m for m in self.messages if m["message_id"] == mid), None)
                if msg is None:
                    for replies in self.threads.values():
                        msg = msg or next((m for m in replies if m["message_id"] == mid), None)
                if msg is None:
                    return fail(args, 230002, "not_found")
                if msg["sender"]["id"] != app:
                    return fail(args, 230071, "not_sender")
                if method == "PATCH":
                    old_card, new_card = json.loads(msg["content"]), json.loads(data.get("content") or "{}")
                    if not (old_card.get("config", {}).get("update_multi") is True
                            and new_card.get("config", {}).get("update_multi") is True):
                        return fail(args, 230099, "card_not_shared")
                    msg["content"] = data["content"]
                    msg["msg_type"] = "interactive"
                else:
                    if data.get("msg_type") != "text":
                        return fail(args, 230054, "unsupported_message_type")
                    msg["content"] = json.loads(data.get("content") or "{}").get("text", "")
                    msg["msg_type"] = "text"
                msg["updated"] = True
                msg["update_time"] = str(int(self.clock.timestamp() * 1000))
                self.message_updates.append({"method": method, "message_id": mid, "app": app, "data": data})
                return ok({})
            if method == "GET" and path == f"/open-apis/im/v1/chats/{CHAT}":
                id_type = params.get("user_id_type", "open_id")
                chat = dict(self.chat, owner_id=self.conv(self.chat["owner_id"], id_type), owner_id_type=id_type,
                            user_manager_id_list=[self.conv(m, id_type) for m in self.chat["user_manager_id_list"]])
                if self.chat_owner_is_app:
                    chat.update(owner_id=OWNER_APP, owner_id_type="app_id")
                return ok(chat)
            if method == "GET" and path == "/open-apis/authen/v1/user_info":
                assert as_ == "user"
                if self.user_info == "error":
                    return fail(args, 99991668)
                open_id = self.profiles[app] if self.user_info != "mismatch" else "ou_someoneelse000000000000000001"
                return ok({"open_id": open_id, "union_id": "not-a-union-id" if self.user_info == "bad_union" else union_of(open_id)})
            if method == "GET" and path.startswith("/open-apis/im/v1/messages/"):
                assert as_ == "user"
                mode = self.message_get_fail.pop(0) if self.message_get_fail else None
                if mode == "network":
                    return subprocess.CompletedProcess(args, 1, "", json.dumps(
                        {"ok": False, "error": {"type": "network", "message": "connection reset"}}))
                if mode == "api":
                    return fail(args, 99991400)
                mid, id_type = path.rsplit("/", 1)[1], params.get("user_id_type", "open_id")
                item = self._message_item(mid, id_type)
                if item is None or mid in self.message_get_gone:
                    return fail(args, 230002, "not_found")
                if self.message_get_tamper:
                    item = self.message_get_tamper(mid, id_type, item)
                return ok({"items": item if isinstance(item, list) else [] if item is None else [item]})
            if method == "GET" and path.startswith("/open-apis/im/v1/chats/"):
                return fail(args, 232011)
            if path == f"/open-apis/im/v1/chats/{CHAT}/members":
                if self.member_error_once:
                    self.member_error_once = False
                    return fail(args, 232024)
                ids = data["id_list"]
                if method == "POST":
                    assert params.get("succeed_type") == 1, "partial success must be allowed"
                rejected = [i for i in ids if i in self.reject_ids]
                unknown = []
                for i in ids:
                    if i in rejected:
                        continue
                    if params["member_id_type"] in ("open_id", "union_id"):
                        if params["member_id_type"] == "union_id":
                            local = self.local_of_union(i)
                            if local is None:  # 飞书不认得这个 union_id
                                unknown.append(i)
                                continue
                            i = local
                        (self.users.add if method == "POST" else self.users.discard)(i)
                    elif method == "POST":
                        if len(self.bots) >= 15:  # Feishu: at most 15 bots per chat
                            rejected.append(i)
                            continue
                        self.bots[i] = self.bot_members[i]
                    else:
                        self.bots.pop(i, None)
                return ok({"invalid_id_list": rejected, "not_existed_id_list": unknown})
            if method == "GET" and path.startswith("/open-apis/contact/v3/users/"):
                if self.contact_error:
                    return fail(args, self.contact_error)
                if self.owner_in_scope:
                    return ok({"user": {"open_id": path.rsplit("/", 1)[1]}})
                return fail(args, 41050)
            if method == "GET" and path == "/open-apis/application/v6/scopes":
                return ok({"scopes": [{"scope_name": s, "grant_status": 1} for s in sorted(self.bot_scopes)]})
        raise AssertionError(f"unexpected lark command {args}")

    def _card_refusal(self, args):
        """Feishu's answer to a card it will not take, or None. Modelled on what was seen: an interactive message needs
        --content, valid JSON (lark-cli refuses that itself: type validation); Feishu refuses a card of 30 KB or more, and an
        <at id=on_…> (a union_id: only open_id and `all` are known ids) — both as 230099, type api; <at id=ou_…> and
        <at email=…> are taken. `card_reject` makes it say no to a card that is fine (a tenant policy, a bad day)."""
        mode = self.card_reject.pop(0) if self.card_reject else None
        if mode == "content":
            return fail(args, 230099, "card_invalid", message="ErrCode: 11310 card content invalid")
        if mode == "validation":
            return fail(args, None, "invalid_content", type_="validation", message="card content is not accepted")
        if mode == "auth":
            return fail(args, 99991663, "token_invalid", type_="authentication")
        if mode == "permission":
            return fail(args, 99991672, "scope_missing", type_="permission")
        content = self._opt(args, "--content")
        if content is None:
            return fail(args, None, "missing_content", type_="validation", message="--content is required")
        try:
            card = json.loads(content)
        except ValueError:
            return fail(args, None, "invalid_json", type_="validation", message="--content is not valid JSON")
        if len(content.encode("utf-8")) >= MAX_CARD_BYTES:
            return fail(args, 230099, "card_too_large", message="ErrCode: 230025 the card exceeds the size limit")
        for kind, value in CARD_AT_RE.findall(content):
            if kind == "id" and value != "all" and not value.startswith("ou_"):
                return fail(args, 230099, "card_invalid",
                            message="ErrCode: 100290 there is an invalid user resource (at/person) in your card")
        shape = (isinstance(card, dict) and card.get("schema") == "2.0" and isinstance((card.get("header") or {}).get("title"), dict)
                 and (card["header"]["title"].get("content") or "").strip() and isinstance((card.get("body") or {}).get("elements"), list))
        if not shape:
            return fail(args, 230099, "card_invalid", message="ErrCode: 11310 card content invalid")
        return None

    def card_sends(self):
        """The interactive messages sent so far, in order: the call, the card parsed, and which endpoint."""
        out = []
        for c in self.lark_sends():
            if "--msg-type" in c["args"] and c["args"][c["args"].index("--msg-type") + 1] == "interactive":
                out.append({"call": c, "app": c["app"], "card": json.loads(c["args"][c["args"].index("--content") + 1]),
                            "endpoint": "reply" if c["args"][1] == "+messages-reply" else "send",
                            "key": c["args"][c["args"].index("--idempotency-key") + 1]})
        return out

    def channel_gets(self):
        return [c for c in self.buzz_calls if tuple(c["args"][:2]) == ("channels", "get")]

    VALID_EMOJI = {"GLANCE", "Typing", "DONE", "THUMBSUP", "OK", "THANKS", "MUSCLE", "OnIt", "Party", "CrossMark"}

    def _message_ids(self):
        ids = {m["message_id"] for m in self.messages}
        for replies in self.threads.values():
            ids |= {m["message_id"] for m in replies}
        return ids

    def _batch_reactions(self, args):
        """im reactions batch_query（2026-09-23 对真实消息只读实测）：user 身份；--params 的 user_id_type 决定人的 operator_id；
        page_size_per_message 最多 10；应答 success_msg_reaction_details[{message_id, has_more, page_token?, message_reaction_items[
        {action_time, emoji_type, operator{operator_id, operator_type}}]}]，没有 reaction_id；bot 打的 operator_type 是 app、id 是 app_id。"""
        assert self._opt(args, "--as") == "user"
        params = json.loads(self._opt(args, "--params") or "{}")
        data = json.loads(self._opt(args, "--data"))
        size = data.get("page_size_per_message", 10)
        assert 1 <= size <= 10, "the API takes at most 10 per message"
        self.batch_reaction_calls.append([q["message_id"] for q in data["queries"]])
        mode = self.batch_reaction_fail.pop(0) if self.batch_reaction_fail else None
        if mode == "api":
            return fail(args, 99991400)
        details, fails = [], []
        for q in data["queries"]:
            mid = q["message_id"]
            if mode == "message_failed":
                fails.append({"message_id": mid, "fail_reason": "no_permission"})
                continue
            items = [dict(r) for r in self.batch_reactions.get(mid, [])]
            items += [{"emoji_type": r["emoji"], "operator": {"operator_id": r["app"], "operator_type": "app"}}
                      for r in self.reactions if r["message_id"] == mid]
            start = int(q.get("page_token") or 0)
            page = items[start:start + size]
            row = {"message_id": mid, "has_more": start + size < len(items),
                   "message_reaction_items": [dict(i, action_time="1790000000") for i in page]}
            if row["has_more"]:
                row["page_token"] = str(start + size)
            details.append(row)
        assert params.get("user_id_type") in ("union_id", "open_id")
        return {"ok": True, "data": {"success_msg_reaction_details": details, "success_msg_reaction_counts": [],
                                     "fail_msg_reaction_details": fails}}

    def _reactions(self, args, app):
        """im reactions create|delete，照真实行为建模：调用者必须在群里、消息必须存在；同一个 (消息, 表情, 应用)
        重复添加是幂等的（同一个 reaction_id）；只能删自己加的。参数走 --params / --data（没有类型化 flag）。"""
        verb = args[2]
        if verb == "batch_query":
            return self._batch_reactions(args)
        assert self._opt(args, "--as") == "bot"
        params = json.loads(self._opt(args, "--params") or "{}")
        mode = self.reaction_fail.pop(0) if self.reaction_fail else None
        if mode == "timeout":
            raise subprocess.TimeoutExpired(args, 90)
        if mode == "rate_limited":
            return fail(args, 230020, "rate_limited")
        if app not in self.bots:
            return fail(args, 230002, "bot_not_in_chat")
        message_id = params["message_id"]
        if message_id not in self._message_ids():
            return fail(args, 231003, "not_found")
        if verb == "create":
            emoji = json.loads(self._opt(args, "--data"))["reaction_type"]["emoji_type"]
            if emoji not in self.VALID_EMOJI:
                return fail(args, 231001, "invalid_emoji")
            found = next((r for r in self.reactions if (r["message_id"], r["emoji"], r["app"]) == (message_id, emoji, app)), None)
            if found is None:
                found = {"id": self._next("rc"), "message_id": message_id, "emoji": emoji, "app": app}
                self.reactions.append(found)
            if mode == "network_envelope":  # delivered, but lark-cli reports a transport error
                return subprocess.CompletedProcess(args, 1, "", json.dumps(
                    {"ok": False, "error": {"type": "network", "message": "connection reset"}}))
            return {"ok": True, "data": {"reaction_id": found["id"], "reaction_type": {"emoji_type": emoji}}}
        assert verb == "delete", args
        found = next((r for r in self.reactions if r["message_id"] == message_id and r["id"] == params["reaction_id"]), None)
        if found is None or found["app"] != app:
            return fail(args, 231004, "not_found")
        self.reactions.remove(found)
        if mode == "network_envelope":
            return subprocess.CompletedProcess(args, 1, "", json.dumps(
                {"ok": False, "error": {"type": "network", "message": "connection reset"}}))
        return {"ok": True, "data": {}}

    # ---- 身份：同一个人在飞书里有三个 id（bridge 应用的 open_id、owner 应用的 open_id、union_id）------------
    def conv(self, local_id, id_type):
        """owner 应用里的 id（人的 open_id，或 bot 的成员 id）换成 id_type 要的形式。"""
        if id_type != "union_id" or not str(local_id).startswith("ou_"):
            return local_id
        union = union_of(local_id)
        self.union_to_open[union] = local_id
        return union

    def local_of_union(self, union):
        """飞书认得租户里所有人：绑定过的、在群里的、发过言的、群主，以及转换过的。"""
        known = set(self.bindings.values()) | self.users | {self.chat["owner_id"], OWNER_OPEN}
        known |= {m["sender"]["id"] for m in self.messages if m["sender"]["sender_type"] == "user"}
        return next((o for o in known if union_of(o) == union), self.union_to_open.get(union))

    def _message_item(self, mid, id_type):
        """GET /im/v1/messages/{id}：user_id_type 决定 sender.id 和 mentions[].id 的形式；key 不变，所以两种取法可以按 key 一一配对。"""
        msg = next((m for m in self.messages if m["message_id"] == mid), None)
        if msg is None:
            for replies in self.threads.values():
                msg = msg or next((m for m in replies if m["message_id"] == mid), None)
        if msg is None:
            return None
        sender = msg["sender"]
        item = {"message_id": mid, "chat_id": CHAT, "msg_type": msg["msg_type"],
                "sender": {"id": self.conv(sender["id"], id_type) if sender["sender_type"] == "user" else sender["id"],
                           "id_type": id_type if sender["sender_type"] == "user" else "app_id",
                           "sender_type": sender["sender_type"], "tenant_key": "tk"}}
        if msg.get("mentions"):
            item["mentions"] = [{"id": self.conv(m["id"], id_type), "id_type": id_type, "key": m["key"],
                                 "name": m.get("name", ""), "tenant_key": "tk"} for m in msg["mentions"]]
        return item

    # ---- 断言助手 ---------------------------------------------------------------------------------
    def message_gets(self):
        return [c for c in self.lark_calls if c["args"][:2] == ["api", "GET"]
                and c["args"][2].startswith("/open-apis/im/v1/messages/")]

    def searches(self):
        return [c["args"][c["args"].index("--query") + 1] for c in self.lark_calls
                if tuple(c["args"][:2]) == ("contact", "+search-user")]

    def member_lists(self):
        return [c for c in self.lark_calls if tuple(c["args"][:2]) == ("im", "+chat-members-list")]

    def reaction_set(self):
        return {(r["message_id"], r["emoji"], r["app"]) for r in self.reactions}

    def reaction_calls(self):
        """Reactions made or withdrawn (create / delete); reading them (batch_query) is not one."""
        return [c for c in self.lark_calls if tuple(c["args"][:2]) == ("im", "reactions") and c["args"][2] != "batch_query"]

    def buzz_sends(self):
        return [c for c in self.buzz_calls if tuple(c["args"][:2]) == ("messages", "send")]

    def thread_calls(self):
        return [c for c in self.buzz_calls if tuple(c["args"][:2]) == ("messages", "thread")]

    def lark_sends(self):
        return [c for c in self.lark_calls if tuple(c["args"][:2]) in (("im", "+messages-send"), ("im", "+messages-reply"))]

    def lark_updates(self):
        return [c for c in self.lark_calls if c["args"][:2] in (["api", "PUT"], ["api", "PATCH"])
                and c["args"][2].startswith("/open-apis/im/v1/messages/")]

    def member_ops(self):
        return [c for c in self.lark_calls if c["args"][0] == "api" and c["args"][2].endswith("/members")]

    def mirrored(self):
        return [e for e in self.events if e["pubkey"] == MIRROR_PK]


class Env:
    """一次命令调用所需的临时文件：配置、假 buzz 二进制、owner 签名用的 env、镜像 env、state 目录。"""

    def __init__(self, tmp: Path, **overrides):
        self.tmp = tmp
        for d in ("agent-cfg", "agent-data", "state"):
            (tmp / d).mkdir(mode=0o700, exist_ok=True)
        binary = tmp / "opt" / "buzz-0.5.23" / "usr" / "bin" / "buzz"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"\x7fELF-fake-buzz")
        os.chmod(binary, 0o755)
        self.buzz_cli = str(binary)
        # owner 的 env 只用来给「读绑定关系」这一类 GET 签名；它绝不能出现在任何子进程的环境里。
        self.signer_env = write_owner_only(tmp / "owner.env", f"BUZZ_PRIVATE_KEY={OWNER_KEY}\nBUZZ_RELAY_URL=https://relay.test\n")
        self.mirror_env = write_owner_only(tmp / "mirror.env", "\n".join([
            f"BUZZ_PRIVATE_KEY={MIRROR_KEY}", "BUZZ_RELAY_URL=https://relay.test", "IGNORED_TOKEN=nope", ""]))
        cfg = {
            "channel_id": CHANNEL, "chat_id": CHAT, "owner_open_id": OWNER_OPEN, "owner_app_id": OWNER_APP,
            "mirror_pubkey": MIRROR_PK, "mirror_env_file": str(self.mirror_env),
            "people_api": {"base_url": API_ORIGIN, "signer_env_file": str(self.signer_env)}, "remove_extras": True,
            "agents": {AGENT_PK: {"app_id": AGENT_APP, "lark_config_dir": str(tmp / "agent-cfg"),
                                  "lark_data_dir": str(tmp / "agent-data")}},
            "buzz_cli": self.buzz_cli, "buzz_cli_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "lark_cli": LARK_CLI,
            "desk_pubkey": AGENT_PK,
            "message_format": "text",  # most cases pin what is said as text; the card cases say "card"
        }
        cfg.update(overrides)
        self.config = write_owner_only(tmp / "config.json", json.dumps(cfg))
        self.state_dir = tmp / "state"
        self.base_env = {"HOME": str(tmp), "PATH": "/usr/bin", "BUZZ_PRIVATE_KEY": OWNER_KEY,
                         "GITLAB_TOKEN": "glpat-must-not-leak", "LARKSUITE_CLI_CONFIG_DIR": "/leaked/parent/dir",
                         "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus", "XDG_RUNTIME_DIR": "/run/user/1"}

    def round(self, world, now=NOW, allow_bulk_removal=False, skip_backlog=False):
        world.clock = now
        world.sends = 0
        extra = {"skip_backlog": True} if skip_backlog else {}
        return FGS.round_command(self.config, self.state_dir, base_env=self.base_env, runner=world, now=now,
                                 allow_bulk_removal=allow_bulk_removal, http=world.http_get, **extra)

    def state(self):
        return json.loads((self.state_dir / FGS.STATE_FILE).read_text())


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.chmod(self.tmp, 0o700)

    def tearDown(self):
        self._tmp.cleanup()


class DeskConfigMigration(TmpCase):
    def test_dry_run_validates_without_writing_then_apply_backs_up_and_is_idempotent(self):
        env = Env(self.tmp)
        old = json.loads(env.config.read_text())
        old.pop("desk_pubkey")
        original = json.dumps(old)
        write_owner_only(env.config, original)
        ready = FGS.migrate_desk_config(env.config, AGENT_PK)
        self.assertEqual((ready["status"], ready["applied"]), ("ready", False))
        self.assertEqual(env.config.read_text(), original)
        self.assertEqual(list(self.tmp.glob("config.json.bak.desk-*")), [])
        done = FGS.migrate_desk_config(env.config, AGENT_PK, apply=True, now=NOW)
        backup = Path(done["backup"])
        self.assertEqual((done["status"], done["applied"]), ("migrated", True))
        self.assertEqual(backup.read_text(), original)
        self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)
        self.assertEqual(FGS.load_config(env.config)["desk_pubkey"], AGENT_PK)
        self.assertEqual(FGS.migrate_desk_config(env.config, AGENT_PK, apply=True)["status"], "already_migrated")
        self.assertEqual(list(self.tmp.glob("config.json.bak.desk-*")), [backup])

    def test_missing_or_conflicting_desk_never_changes_the_config(self):
        env = Env(self.tmp)
        old = json.loads(env.config.read_text())
        old.pop("desk_pubkey")
        original = json.dumps(old)
        write_owner_only(env.config, original)
        with self.assertRaisesRegex(FGS.GroupSyncError, "desk_pubkey"):
            FGS.migrate_desk_config(env.config, AGENT2_PK, apply=True)
        self.assertEqual(env.config.read_text(), original)
        self.assertEqual(list(self.tmp.glob("config.json.bak.desk-*")), [])


# ================================ L1 ================================


class PeopleApi(TmpCase):
    """绑定关系不再来自静态导出文件，而是每轮向 bridge 的签名接口取（infra/buzz-deploy ADR-0015）。"""

    GOLDEN = "Nostr eyJpZCI6ImQzMGJlMDYxNDg3M2YyMTMwMGQ2ZGY1YTNiYmQ0ODkxNDhiZjRmZjEzMmU1OGU3ZmI5Yzk2ZDRiYWQyZWJkZWQiLCJwdWJrZXkiOiJlOTBmMjA4ZmIzY2YzYTI3NjQwNGI4MjEzYWY1OWZhMzBiZmYyYWExZmI5MmNjMmM3ZjQzM2E5ZjAzMzFkMTIzIiwiY3JlYXRlZF9hdCI6MTc4OTgxOTIwMCwia2luZCI6MjcyMzUsInRhZ3MiOltbInUiLCJodHRwczovL2JyaWRnZS5leGFtcGxlLnRlc3QvYmluZC9hcGkvY2hhbm5lbHMvN2QxMDgzMmEtMGQyMy00NDg2LTgyZGItN2FhNTFiOTdlNWY0L3Blb3BsZSJdLFsibWV0aG9kIiwiR0VUIl1dLCJjb250ZW50IjoiIiwic2lnIjoiYjE5ZmUxZDkzYzk1OWVkMWNkZmM5ZGJiZDU2YjJkMmMxNjg4ZGRmYzgyNjY0M2M4YTJkMGNkOThkZjhiYjhjNjIwNDUzMGU0NjIyMTI4NGZmNmI0YmJlOTQxYTYyMWJiZGE2MmUyNDhiMzM4ZThjOTY3ODc4OWUwMmQ2ZGE0M2MifQ=="

    def test_nip98_header_is_a_signed_event_for_exactly_this_request(self):
        """L1-FGS-001: NIP-98 头是 owner 签的 kind 27235 事件：u 与 method 各一个、created_at 是当前秒、事件 id 与 BIP-340 签名都对。"""
        header = FGS.nip98_header(OWNER_KEY, "GET", API_URL, NOW)
        self.assertTrue(header.startswith("Nostr "))
        ev = json.loads(base64.b64decode(header[6:], validate=True))
        self.assertEqual(set(ev), {"id", "pubkey", "created_at", "kind", "tags", "content", "sig"})
        self.assertEqual((ev["kind"], ev["pubkey"], ev["created_at"], ev["content"]), (27235, OWNER_PK, ts(NOW), ""))
        self.assertEqual(ev["tags"], [["u", API_URL], ["method", "GET"]])
        body = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]], separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), ev["id"])
        self.assertTrue(FGS.sync.nk.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"])))
        self.assertLess(len(header), 2048)
        # 每次签名向系统要 32 字节新的随机数（测试模块整体把它固定成全零以便缓存，这里换成两个不同的值来看）。
        with mock.patch.object(FGS.secrets, "token_bytes", side_effect=[b"\x01" * 32, b"\x02" * 32]) as fresh:
            first, second = (FGS.nip98_header(OWNER_KEY, "GET", API_URL, NOW) for _ in range(2))
        self.assertNotEqual(first, second)
        self.assertEqual([c.args for c in fresh.call_args_list], [(32,), (32,)])

    def test_nip98_header_matches_the_vector_the_bridge_verifies(self):
        """L1-FGS-002: 固定 key、时间、URL 与全零随机数时输出逐字节等于金标准头；同一个头由 bridge 的 Go 验签器（L1-FB-NOSTR-022）验过，两边协议不会各自漂移。"""
        self.assertEqual(FGS.nip98_header(OWNER_KEY, "GET", API_URL, NOW, aux=bytes(32)), self.GOLDEN)

    def test_nip98_header_refuses_a_bad_key_without_echoing_it(self):
        """L1-FGS-003: 私钥不是 64 位小写 hex、是 0、不小于曲线阶，都拒绝，错误里不含私钥。"""
        for bad in ("", "nsec1abc", "zz" * 32, "C2" * 32, "c2" * 31, "00" * 32, "ff" * 32):
            with self.assertRaises(FGS.GroupSyncError) as ctx:
                FGS.nip98_header(bad, "GET", API_URL, NOW)
            if bad:
                self.assertNotIn(bad, str(ctx.exception))

    def test_people_url_is_the_channel_endpoint_of_the_origin(self):
        """L1-FGS-004: 接口地址是 <origin>/bind/api/channels/<频道>/people；频道 id 必须是 uuid。"""
        self.assertEqual(FGS.people_url(API_ORIGIN, CHANNEL), API_URL)
        self.assertEqual(FGS.people_url(API_ORIGIN + ":8443", CHANNEL), f"{API_ORIGIN}:8443/bind/api/channels/{CHANNEL}/people")
        with self.assertRaises(FGS.GroupSyncError):
            FGS.people_url(API_ORIGIN, "not-a-uuid")

    def test_people_response_is_parsed_strictly(self):
        """L1-FGS-005: 只认 {channel, people:{64 位小写 hex 的 key: ou_ 开头的 open_id}}；频道对不上、格式不对、超大、不是 JSON 都拒绝；多出的顶层字段容忍。"""
        good = {"channel": CHANNEL, "as_of": "2026-09-19T12:00:00Z", "people": {ALICE_PK: ALICE_OPEN, BOB_PK: BOB_OPEN}}
        self.assertEqual(FGS.parse_people_response(json.dumps(good).encode(), CHANNEL).open_ids, {ALICE_PK: ALICE_OPEN, BOB_PK: BOB_OPEN})
        self.assertEqual(FGS.parse_people_response(json.dumps(dict(good, people={}, later_field=1)).encode(), CHANNEL).open_ids, {})
        # 同一个人绑了两个 key：两个 key 都映射
        twin = dict(good, people={ALICE_PK: ALICE_OPEN, GUEST_PK: ALICE_OPEN})
        self.assertEqual(len(FGS.parse_people_response(json.dumps(twin).encode(), CHANNEL).open_ids), 2)
        bad = {
            "not json": b"<html>", "not an object": b"[]", "no people": json.dumps({"channel": CHANNEL}).encode(),
            "people is a list": json.dumps(dict(good, people=[ALICE_PK])).encode(),
            "another channel": json.dumps(dict(good, channel="00000000-0000-4000-8000-000000000000")).encode(),
            "no channel": json.dumps({"people": {}}).encode(),
            "key not hex": json.dumps(dict(good, people={"XYZ": ALICE_OPEN})).encode(),
            "key in upper case": json.dumps(dict(good, people={("ab" * 32).upper(): ALICE_OPEN})).encode(),
            "open_id is an email": json.dumps(dict(good, people={ALICE_PK: "alice@a4x.io"})).encode(),
            "open_id is empty": json.dumps(dict(good, people={ALICE_PK: ""})).encode(),
            "open_id is not a string": json.dumps(dict(good, people={ALICE_PK: 7})).encode(),
            "larger than the cap": json.dumps(good).encode() + b" " * FGS.PEOPLE_API_MAX_BYTES,  # valid, only too big
        }
        for name, body in bad.items():
            with self.assertRaises(FGS.GroupSyncError, msg=name) as ctx:
                FGS.parse_people_response(body, CHANNEL)
            self.assertNotIn("alice@a4x.io", str(ctx.exception), name)

    def test_http_get_sends_only_what_it_is_given_and_never_follows_redirects(self):
        """L1-FGS-006: 真实的 HTTP 函数：带上给定的头、返回状态码和正文；3xx 不跟随（服务器只见到一次请求）；正文超过上限拒绝；超时抛 OSError；不是 http(s) 的地址拒绝。"""
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append((self.path, dict(self.headers)))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/ok")
                    self.end_headers()
                elif self.path == "/big":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"x" * (FGS.PEOPLE_API_MAX_BYTES + 10))
                elif self.path == "/slow":
                    import time
                    time.sleep(1.5)
                    self.send_response(200)
                    self.end_headers()
                elif self.path == "/gone":
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error":"not_found"}')
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        status, body = FGS._http_get(base + "/ok", {"Authorization": "Nostr abc"}, 5.0)
        self.assertEqual((status, body), (200, b'{"ok":true}'))
        # 环境里的代理设置不生效：请求（和它的签名头）不会绕到别处去。
        dead = {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": ""}
        with mock.patch.dict(os.environ, dead):
            self.assertEqual(FGS._http_get(base + "/ok", {}, 5.0), (200, b'{"ok":true}'))
        self.assertEqual(seen[0][1].get("Authorization"), "Nostr abc")
        self.assertEqual(FGS._http_get(base + "/gone", {}, 5.0), (404, b'{"error":"not_found"}'))
        seen.clear()
        self.assertEqual(FGS._http_get(base + "/redirect", {}, 5.0)[0], 302)
        self.assertEqual([path for path, _ in seen], ["/redirect"])
        with self.assertRaises(FGS.GroupSyncError):
            FGS._http_get(base + "/big", {}, 5.0)
        with self.assertRaises(OSError):
            FGS._http_get(base + "/slow", {}, 0.3)
        for bad in ("file:///etc/passwd", "ftp://127.0.0.1/x", "/relative"):
            with self.assertRaises(FGS.GroupSyncError, msg=bad):
                FGS._http_get(bad, {}, 1.0)

    def test_the_owner_key_is_read_from_a_private_env_file_only(self):
        """L1-FGS-007: 签名用的 key 只从 0600 的 env 文件里读；权限过松、符号链接、缺 BUZZ_PRIVATE_KEY 都拒绝；读出来的 key 不进任何错误信息。"""
        good = write_owner_only(self.tmp / "good.env", f"BUZZ_PRIVATE_KEY={OWNER_KEY}\n")
        self.assertEqual(FGS.load_signer_key(good), OWNER_KEY)
        # 真实的 ~/.config/buzz/env 里是 nsec：同一把 key 的 nsec 写法读出来也是同一个 hex，带引号与 export 也认。
        nsec = FGS.sync.nk.bech32_encode("nsec", bytes.fromhex(OWNER_KEY))
        for line in (f"BUZZ_PRIVATE_KEY={nsec}", f'export BUZZ_PRIVATE_KEY="{nsec}"', f"BUZZ_PRIVATE_KEY='{OWNER_KEY}'"):
            path = write_owner_only(self.tmp / "form.env", "BUZZ_RELAY_URL=x\n" + line + "\n")
            self.assertEqual(FGS.load_signer_key(path), OWNER_KEY, line)
        loose = self.tmp / "loose.env"
        loose.write_text(f"BUZZ_PRIVATE_KEY={OWNER_KEY}\n")
        os.chmod(loose, 0o644)
        link = self.tmp / "link.env"
        link.symlink_to(good)
        empty = write_owner_only(self.tmp / "empty.env", "BUZZ_RELAY_URL=https://relay.test\n")
        bad_nsec = write_owner_only(self.tmp / "nsec.env", "BUZZ_PRIVATE_KEY=nsec1qqqq\n")
        for path in (loose, link, empty, bad_nsec, self.tmp / "missing.env"):
            with self.assertRaises(FGS.GroupSyncError, msg=str(path)) as ctx:
                FGS.load_signer_key(path)
            self.assertNotIn(OWNER_KEY, str(ctx.exception))


class Preflight(unittest.TestCase):
    def chat(self, **kw):
        base = {"owner_id": OWNER_OPEN, "user_manager_id_list": [], "bot_manager_id_list": [],
                "add_member_permission": "all_members", "moderation_permission": "all_members",
                "external": False, "chat_status": "normal", "chat_mode": "group", "bot_count": 1}
        base.update(kw)
        return base

    def check(self, chat, *, bots_to_add=1, remove_extras=True):
        return FGS.preflight_existing_chat(chat, owner_open_id=OWNER_OPEN, bots_to_add=bots_to_add,
                                           remove_extras=remove_extras)

    def test_new_chat_needs_matching_profile_owner_in_app_scope_and_create_scope(self):
        """L1-FGS-010: 新建群：lark-cli profile 不是配置里的应用和 owner、owner 不在可用范围、bot 没有 im:chat:create，都阻断。"""
        self.assertTrue(FGS.preflight_new_chat(owner_in_scope=True, bot_scopes={"im:chat:create"}, profile_ok=True).ok)
        out = FGS.preflight_new_chat(owner_in_scope=False, bot_scopes={"im:chat:create"}, profile_ok=True)
        self.assertEqual(out.problems, ("owner_out_of_app_scope",))
        out = FGS.preflight_new_chat(owner_in_scope=True, bot_scopes=set(), profile_ok=True)
        self.assertEqual(out.problems, ("bot_missing_im:chat:create",))
        out = FGS.preflight_new_chat(owner_in_scope=True, bot_scopes={"im:chat:create"}, profile_ok=False)
        self.assertEqual(out.problems, ("owner_profile_mismatch",))

    def test_existing_chat_owner_passes(self):
        """L1-FGS-011: owner 是群主的普通内部群：通过，可以移出多余成员。"""
        out = self.check(self.chat())
        self.assertTrue(out.ok, out)
        self.assertTrue(out.can_remove)

    def test_existing_chat_blocks_external_dissolved_topic(self):
        """L1-FGS-012: 外部群、非 normal 状态、话题群都阻断。"""
        self.assertIn("external_chat", self.check(self.chat(external=True)).problems)
        self.assertIn("chat_not_normal", self.check(self.chat(chat_status="dissolved")).problems)
        self.assertIn("topic_mode_unsupported", self.check(self.chat(chat_mode="topic")).problems)

    def test_existing_chat_add_permission(self):
        """L1-FGS-013: 仅群主/管理员可加人时，owner 必须是群主或管理员。"""
        other = self.chat(owner_id="ou_someoneelse", add_member_permission="only_owner")
        self.assertIn("cannot_add_members", self.check(other).problems)
        manager = dict(other, user_manager_id_list=[OWNER_OPEN])
        self.assertNotIn("cannot_add_members", self.check(manager, remove_extras=False).problems)

    def test_existing_chat_remove_extras_needs_owner_or_manager(self):
        """L1-FGS-014: 要移出多余成员时，owner 不是群主/管理员就阻断；只加不减时改为警告且 can_remove=False。"""
        plain = self.chat(owner_id="ou_someoneelse")
        out = self.check(plain, remove_extras=True)
        self.assertIn("cannot_remove_members", out.problems)
        self.assertFalse(out.can_remove)
        out = self.check(plain, remove_extras=False)
        self.assertTrue(out.ok, out)
        self.assertFalse(out.can_remove)
        self.assertIn("extras_stay_and_see_channel_messages", out.warnings)

    def test_existing_chat_moderation_warns_and_bot_capacity_blocks(self):
        """L1-FGS-015: 限制发言是警告；现有 bot + 待加入 bot 超过 15 阻断。"""
        self.assertIn("moderation_restricted", self.check(self.chat(moderation_permission="only_owner")).warnings)
        self.assertTrue(self.check(self.chat(bot_count=14), bots_to_add=1).ok)
        self.assertIn("bot_limit", self.check(self.chat(bot_count=14), bots_to_add=2).problems)

    def test_existing_chat_unknown_mode_blocks(self):
        """L1-FGS-016: 既不是 group 也不是 topic 的群模式（如 meeting）也阻断。"""
        self.assertEqual(self.check(self.chat(chat_mode="meeting")).problems, ("chat_mode_unsupported",))

    def test_existing_chat_warns_when_outsiders_can_get_in(self):
        """L1-FGS-017: 任何人可以分享群名片、入群不需审批时给警告：外人在下一轮对账前能看到频道消息。"""
        out = self.check(self.chat(share_card_permission="allowed", membership_approval="no_approval_required"))
        self.assertTrue(out.ok)
        self.assertIn("share_card_allowed", out.warnings)
        self.assertIn("join_without_approval", out.warnings)
        quiet = self.check(self.chat(share_card_permission="not_allowed", membership_approval="approval_required"))
        self.assertEqual(quiet.warnings, ())


class Membership(unittest.TestCase):
    def plan(self, **kw):
        args = dict(desired_users={ALICE_OPEN}, desired_bots={AGENT_APP}, actual_users={OWNER_OPEN, EXTRA_OPEN},
                    actual_bots={OWNER_APP, TEAM_BOT_APP}, owner_open_id=OWNER_OPEN, owner_app_id=OWNER_APP,
                    managed_bots={AGENT_APP}, remove_extras=True)
        args.update(kw)
        return FGS.plan_membership(**args)

    def test_adds_missing_and_removes_extras_but_never_owner_or_foreign_bots(self):
        """L1-FGS-020: 加缺的人和 agent bot；移出多余的人；owner、owner 应用 bot、不归我们管的 bot 都不动。"""
        p = self.plan()
        self.assertEqual(p.add_users, (ALICE_OPEN,))
        self.assertEqual(p.remove_users, (EXTRA_OPEN,))
        self.assertEqual(p.add_bots, (AGENT_APP,))
        self.assertEqual(p.remove_bots, ())
        p = self.plan(actual_bots={TEAM_BOT_APP}, actual_users={EXTRA_OPEN})
        self.assertIn(OWNER_APP, p.add_bots)
        self.assertIn(OWNER_OPEN, p.add_users)

    def test_add_only_mode_removes_nothing(self):
        """L1-FGS-021: remove_extras=False 时不移出任何人或 bot。"""
        p = self.plan(remove_extras=False, actual_bots={OWNER_APP, AGENT_APP}, desired_bots=set())
        self.assertEqual((p.remove_users, p.remove_bots), ((), ()))

    def test_agent_bot_left_channel_is_removed(self):
        """L1-FGS-022: 登记过的 agent 离开频道后，它的 bot 被移出群。"""
        p = self.plan(actual_bots={OWNER_APP, AGENT_APP}, desired_bots=set())
        self.assertEqual(p.remove_bots, (AGENT_APP,))

    def test_bot_capacity_counts_foreign_bots_and_blocks_deterministically(self):
        """L1-FGS-023: 15 个 bot 上限算上 owner 应用 bot 和群里原有的 bot，超出的按 app_id 排序后阻断。"""
        foreign = {f"cli_foreign{i:08d}" for i in range(12)}
        agents = {"cli_agent_c", "cli_agent_a", "cli_agent_b"}
        p = self.plan(actual_bots={OWNER_APP, *foreign}, desired_bots=agents, managed_bots=agents)
        self.assertEqual(p.add_bots, ("cli_agent_a", "cli_agent_b"))
        self.assertEqual(p.blocked_bots, ("cli_agent_c",))

    def test_batches(self):
        """L1-FGS-024: 按上限分批（人 50、bot 5）。"""
        self.assertEqual(FGS.batches(list(range(7)), 5), [[0, 1, 2, 3, 4], [5, 6]])
        self.assertEqual(FGS.batches([], 5), [])

    def test_removal_guard(self):
        """L1-FGS-025: 有映射不到的频道成员时不移人（分不清他和外人，坏导出会让所有人都像外人）；
        一次要移出超过 10 个时整体暂缓，除非显式允许；移 bot 不受第一条影响。"""
        plan = FGS.MembershipPlan(remove_users=(EXTRA_OPEN,), remove_bots=(AGENT_APP,))
        guarded, reason = FGS.guard_removals(plan, unmapped=1, allow_bulk=False)
        self.assertEqual((guarded.remove_users, guarded.remove_bots, reason), ((), (AGENT_APP,), "unmapped_members"))
        self.assertEqual(FGS.guard_removals(plan, unmapped=0, allow_bulk=False), (plan, None))
        many = FGS.MembershipPlan(remove_users=tuple(f"ou_x{i:04d}" for i in range(11)))
        guarded, reason = FGS.guard_removals(many, unmapped=0, allow_bulk=False)
        self.assertEqual((guarded.remove_users, reason), ((), "bulk_removal"))
        self.assertEqual(FGS.guard_removals(many, unmapped=0, allow_bulk=True), (many, None))
        ten = FGS.MembershipPlan(remove_users=tuple(f"ou_x{i:04d}" for i in range(10)))
        self.assertEqual(FGS.guard_removals(ten, unmapped=0, allow_bulk=False), (ten, None))


class BuzzToFeishuRouting(unittest.TestCase):
    def route(self, ev, **kw):
        args = dict(mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP},
                    human_pubkeys={OWNER_PK, ALICE_PK, BOB_PK}, agent_pubkeys={AGENT_PK, AGENT2_PK},
                    names={ALICE_PK: "Alice", AGENT_PK: "helper-agent"},
                    mention_targets={ALICE_PK: (ALICE_OPEN, "Alice"), AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")})
        args.update(kw)
        return FGS.route_buzz_event(ev, **args)

    def test_human_message_goes_through_owner_bot_with_signature(self):
        """L1-FGS-030: 人的发言由 owner 应用 bot 发（via_app_id=None），正文带「名字（Buzz）：」。"""
        out = self.route(event(eid(1), ALICE_PK, "hello"))
        self.assertIsNone(out.via_app_id)
        self.assertEqual(out.text, "Alice（Buzz）：hello")

    def test_agent_message_goes_through_its_own_bot_without_signature(self):
        """L1-FGS-031: agent 的发言只经它自己的 bot 发，不加署名（ADR-0002 Buzz-first）。"""
        out = self.route(event(eid(2), AGENT_PK, "done"))
        self.assertEqual(out.via_app_id, AGENT_APP)
        self.assertEqual(out.text, "done")

    def test_agent_without_bot_is_not_proxied_by_owner_bot(self):
        """L1-FGS-032: agent 没有飞书 bot 时不投递，也绝不退回 owner bot 代发。"""
        self.assertEqual(self.route(event(eid(3), AGENT2_PK, "hi")), "agent_bot_unavailable")

    def test_echo_and_non_message_kinds_are_skipped(self):
        """L1-FGS-033: 镜像身份自己发的（回声）、非消息 kind、空正文都跳过。"""
        self.assertEqual(self.route(event(eid(4), MIRROR_PK, "[飞书] x：y")), "echo")
        self.assertEqual(self.route(event(eid(5), ALICE_PK, "diff", kind=40008)), "kind")
        self.assertEqual(self.route(edit_event(99, ALICE_PK, eid(5), "replacement")), "kind")
        self.assertEqual(self.route(event(eid(6), ALICE_PK, "  ")), "empty")

    def test_owner_bot_turns_p_tags_into_feishu_at(self):
        """L1-FGS-034: owner bot 发送时，p tag 转成飞书 <at>：人用 open_id，agent 用它 bot 的 member_id。"""
        ev = event(eid(7), BOB_PK, "@Alice @helper-agent 看下", tags=[("p", ALICE_PK), ("p", AGENT_PK), ("p", ALICE_PK)])
        out = self.route(ev, names={BOB_PK: "Bob"})
        self.assertEqual(out.text, "Bob（Buzz）：@Alice @helper-agent 看下 "
                         f'<at user_id="{ALICE_OPEN}">Alice</at> <at user_id="{AGENT_BOT_MEMBER}">helper-agent</at>')

    def test_owner_bot_skips_unmapped_mentions(self):
        """L1-FGS-035: 映射不到飞书的 p tag 不生成 <at>，也不猜。"""
        out = self.route(event(eid(8), BOB_PK, "@Carol", tags=[("p", CAROL_PK)]), names={BOB_PK: "Bob"})
        self.assertNotIn("<at", out.text)

    def test_agent_bot_never_carries_owner_namespace_ids(self):
        """L1-FGS-036: open_id 按应用隔离：agent 自己的 bot 发送时不带 owner 应用下的 <at>，只留正文里的 @名字。"""
        out = self.route(event(eid(9), AGENT_PK, "@Alice 已完成", tags=[("p", ALICE_PK)]))
        self.assertEqual(out.via_app_id, AGENT_APP)
        self.assertEqual(out.text, "@Alice 已完成")

    def test_reply_parent_prefers_reply_marker(self):
        """L1-FGS-037: 线程父事件取 reply 标记，没有就取 root。"""
        both = event(eid(10), ALICE_PK, "x", tags=[("e", eid(1), "", "root"), ("e", eid(2), "", "reply")])
        self.assertEqual(self.route(both).parent_event_id, eid(2))
        root_only = event(eid(11), ALICE_PK, "x", tags=[("e", eid(1), "", "root")])
        self.assertEqual(self.route(root_only).parent_event_id, eid(1))
        self.assertIsNone(self.route(event(eid(12), ALICE_PK, "x")).parent_event_id)

    def test_name_falls_back_to_pubkey_prefix(self):
        """L1-FGS-038: 没有 display_name 时署名用 pubkey 前 12 位。"""
        out = self.route(event(eid(13), BOB_PK, "x"), names={})
        self.assertEqual(out.text, f"{BOB_PK[:12]}（Buzz）：x")

    def test_literal_at_markup_in_buzz_content_is_neutralized(self):
        """L1-FGS-039: Buzz 正文里手写的 <at …> 会被飞书当成真 @：两条发送路径都中和掉；p tag 生成的 <at> 照常附加。"""
        raw = '<at user_id="ou_anyone">x</at> <AT user_id="all">所有人</AT> 看下'
        human = self.route(event(eid(14), BOB_PK, raw, tags=[("p", ALICE_PK)]), names={BOB_PK: "Bob"})
        self.assertNotIn('<at user_id="ou_anyone"', human.text)
        self.assertNotIn("<AT", human.text)
        self.assertTrue(human.text.endswith(f'<at user_id="{ALICE_OPEN}">Alice</at>'))
        agent = self.route(event(eid(15), AGENT_PK, raw))
        self.assertEqual(agent.text.lower().count("<at"), 0)

    def test_only_channel_humans_go_through_owner_bot(self):
        """L1-FGS-080: owner bot 只转述当前频道里的人：离开频道的作者、角色不是 bot 的「agent」都不走 owner bot。"""
        self.assertEqual(self.route(event(eid(16), OUTSIDER_PK, "hi")), "not_channel_human")
        self.assertEqual(self.route(event(eid(17), AGENT_PK, "hi"), agent_apps={}, agent_pubkeys=set()),
                         "not_channel_human")


class FeishuToBuzzRouting(unittest.TestCase):
    def route(self, msg, *, now=NOW, **kw):
        args = dict(open_id_to_pubkey={ALICE_OPEN: ALICE_PK, BOB_OPEN: BOB_PK, OWNER_OPEN: OWNER_PK},
                    bot_member_to_pubkey={AGENT_BOT_MEMBER: AGENT_PK},
                    channel_members={ALICE_PK, BOB_PK, OWNER_PK, AGENT_PK},
                    names={ALICE_PK: "Alice", BOB_PK: "Bob"}, now=now, unmapped_senders="skip")
        args.update(kw)
        return FGS.route_feishu_message(msg, **args)

    def test_user_message_is_signed_with_buzz_display_name(self):
        """L1-FGS-040: 人的飞书消息带「[飞书] 名字：」，名字取 Buzz display_name。"""
        out = self.route(fmsg("om_1", ALICE_OPEN, "hi", name="爱丽丝"))
        self.assertEqual(out.text, "[飞书] Alice：hi")
        self.assertEqual(out.sender_pubkey, ALICE_PK)

    def test_selected_mentions_map_to_agent_and_human_pubkeys(self):
        """L1-FGS-041: mentions 实体里的 agent bot → agent pubkey，人 → 人的 pubkey，按出现顺序去重。"""
        m = fmsg("om_2", ALICE_OPEN, "@helper-agent @Bob 看下", mentions=[
            {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"},
            {"id": BOB_OPEN, "key": "@_user_2", "name": "Bob"},
            {"id": AGENT_BOT_MEMBER, "key": "@_user_3", "name": "helper-agent"}])
        self.assertEqual(self.route(m).mentions, (AGENT_PK, BOB_PK))

    def test_typed_at_without_entity_mentions_nobody(self):
        """L1-FGS-042: 手打的「@名字」没有 mention 实体（LCV-09），不转成任何 @。"""
        self.assertEqual(self.route(fmsg("om_3", ALICE_OPEN, "@helper-agent 手打")).mentions, ())

    def test_ignored_mentions(self):
        """L1-FGS-043: @所有人、@owner 应用 bot、@自己、@不认识的人、@不在频道里的人都不转。"""
        m = fmsg("om_4", ALICE_OPEN, "x", mentions=[
            {"id": "all", "key": "@_all", "name": "所有人"},
            {"id": OWNER_BOT_MEMBER, "key": "@_user_1", "name": "飞书 CLI"},
            {"id": ALICE_OPEN, "key": "@_user_2", "name": "Alice"},
            {"id": "ou_stranger", "key": "@_user_3", "name": "?"},
            {"id": OWNER_OPEN, "key": "@_user_4", "name": "Owner"}])
        out = self.route(m, channel_members={ALICE_PK, BOB_PK, AGENT_PK})
        self.assertEqual(out.mentions, ())

    def test_skips(self):
        """L1-FGS-044: 已删除、系统消息、bot/app 发的、映射不到的发送者、空正文都跳过并给出原因。"""
        self.assertEqual(self.route(fmsg("om_5", ALICE_OPEN, "x", deleted=True)), "deleted")
        self.assertEqual(self.route(fmsg("om_6", ALICE_OPEN, "x", msg_type="system")), "system")
        self.assertEqual(self.route(fmsg("om_7", AGENT_APP, "x", sender_type="app")), "bot")
        self.assertEqual(self.route(fmsg("om_8", EXTRA_OPEN, "x")), "unmapped_sender")
        self.assertEqual(self.route(fmsg("om_9", ALICE_OPEN, " ")), "empty")

    def test_sender_who_left_the_channel_is_not_mirrored(self):
        """L1-FGS-045: 发送者映射得到，但已不在频道里，也不镜像（频道成员才是受众）。"""
        self.assertEqual(self.route(fmsg("om_10", BOB_OPEN, "x"), channel_members={ALICE_PK}), "sender_not_in_channel")

    def test_stale_message_gets_original_time(self):
        """L1-FGS-046: 超过 10 分钟的积压在正文末尾注明飞书原始时间。"""
        when = NOW - timedelta(minutes=30)
        out = self.route(fmsg("om_11", ALICE_OPEN, "old", when=when))
        self.assertEqual(out.text, f"[飞书] Alice：old（飞书 {feishu_time(when)}）")
        self.assertEqual(self.route(fmsg("om_12", ALICE_OPEN, "new")).text, "[飞书] Alice：new")

    def test_at_sign_in_content_cannot_become_a_buzz_mention(self):
        """L1-FGS-048: buzz CLI 会把正文里唯一解析的 @名字 变成通知，所以正文的 @ 换成全角 ＠；只有 mentions 实体经 --mention 通知。"""
        m = fmsg("om_15", ALICE_OPEN, "@Bob 手打 @helper-agent 选中", mentions=[
            {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"}])
        out = self.route(m)
        self.assertEqual(out.text, "[飞书] Alice：＠Bob 手打 ＠helper-agent 选中")
        self.assertNotIn("@", out.text)
        self.assertEqual(out.mentions, (AGENT_PK,))

    def test_nostr_uri_in_content_cannot_become_a_buzz_mention(self):
        """L1-FGS-074: buzz CLI 也会把 nostr:npub1… 解析成 @（与 gitlab_buzz_sync.neutralize 同一原因）：正文与名字里的 nostr: 都中和。"""
        out = self.route(fmsg("om_16", ALICE_OPEN, "看 nostr:npub1abcdef 和 NOSTR:npub1x"),
                         names={ALICE_PK: "nostr:npub1evil"})
        self.assertNotIn("nostr:", out.text.lower())


class DisplayNameInjection(unittest.TestCase):
    EVIL = '<at user_id="all">所有人</at>@Bob'

    def test_display_names_cannot_inject_mentions_either_way(self):
        """L1-FGS-070: 署名和 <at> 标签文字来自用户可改的 display name：飞书方向不能带出 <at>，Buzz 方向不能带出 @。"""
        out = FGS.route_buzz_event(
            event(eid(20), BOB_PK, "hi", tags=[("p", ALICE_PK)]), mirror_pubkey=MIRROR_PK, agent_apps={},
            human_pubkeys={BOB_PK, ALICE_PK}, agent_pubkeys=set(),
            names={BOB_PK: self.EVIL}, mention_targets={ALICE_PK: (ALICE_OPEN, '</at><at user_id="all">x')})
        self.assertEqual(out.text.lower().count("<at"), 1)  # 只剩 p tag 生成的那一个
        self.assertNotIn('user_id="all"', out.text)
        self.assertEqual(out.text.count("</at>"), 1)
        inbound = FGS.route_feishu_message(
            fmsg("om_20", ALICE_OPEN, "hi"), open_id_to_pubkey={ALICE_OPEN: ALICE_PK}, bot_member_to_pubkey={},
            channel_members={ALICE_PK}, names={ALICE_PK: self.EVIL}, now=NOW)
        self.assertNotIn("@", inbound.text)

    def test_line_breaks_in_names_cannot_forge_a_second_signed_line(self):
        """L1-FGS-071: 名字里的换行会伪造出「另一个人说的第二行」：两个方向都把换行换成空格。"""
        forged = "Bob\n[飞书] Owner：批准上线"
        inbound = FGS.route_feishu_message(
            fmsg("om_21", ALICE_OPEN, "hi"), open_id_to_pubkey={ALICE_OPEN: ALICE_PK}, bot_member_to_pubkey={},
            channel_members={ALICE_PK}, names={ALICE_PK: forged}, now=NOW)
        self.assertNotIn("\n", inbound.text)
        out = FGS.route_buzz_event(event(eid(21), BOB_PK, "hi"), mirror_pubkey=MIRROR_PK, agent_apps={},
                                   human_pubkeys={BOB_PK}, agent_pubkeys=set(),
                                   names={BOB_PK: "Bob\r\nOwner（Buzz）：批准"}, mention_targets={})
        self.assertNotIn("\n", out.text)
        self.assertNotIn("\r", out.text)

    def test_unicode_separators_and_format_characters_in_names(self):
        """L1-FGS-072: U+2028/U+2029/U+0085/VT/FF 也是换行；双向覆盖（U+202E）与零宽字符会伪装名字：全部去掉。"""
        name = "Bo b x\x85y\x0bz\x0cw ‮evil​"
        out = FGS.route_buzz_event(event(eid(22), BOB_PK, "hi"), mirror_pubkey=MIRROR_PK, agent_apps={},
                                   human_pubkeys={BOB_PK}, agent_pubkeys=set(), names={BOB_PK: name}, mention_targets={})
        signature = out.text.split("（Buzz）：")[0]
        for ch in "  \x85\x0b\x0c‮​":
            self.assertNotIn(ch, signature)
        self.assertEqual(signature, "Bo b x y z w evil")

    def test_forged_signature_lines_in_bodies_are_marked(self):
        """L1-FGS-073: 正文第二行起像对方署名格式的行（「[飞书] X：」「X（Buzz）：」）前加 ↳，看得出不是另一条镜像消息。"""
        inbound = FGS.route_feishu_message(
            fmsg("om_22", ALICE_OPEN, "ok\n[飞书] Owner：deploy prod now\n普通第三行"), open_id_to_pubkey={ALICE_OPEN: ALICE_PK},
            bot_member_to_pubkey={}, channel_members={ALICE_PK}, names={ALICE_PK: "Alice"}, now=NOW)
        self.assertEqual(inbound.text, "[飞书] Alice：ok\n↳ [飞书] Owner：deploy prod now\n普通第三行")
        out = FGS.route_buzz_event(event(eid(23), BOB_PK, "hi\nOwner（Buzz）：批准‮"), mirror_pubkey=MIRROR_PK,
                                   agent_apps={}, human_pubkeys={BOB_PK}, agent_pubkeys=set(), names={BOB_PK: "Bob"},
                                   mention_targets={})
        self.assertEqual(out.text, "Bob（Buzz）：hi\n↳ Owner（Buzz）：批准")


class ForgedLineBypass(unittest.TestCase):
    def test_zero_width_and_unicode_line_breaks_cannot_hide_a_forged_signature(self):
        """L1-FGS-075: 零宽字符、全角变体、U+2028/U+2029/U+0085/CR 分行都不能绕过伪造署名行的标记。"""
        body = ("ok\n\u200b[飞书] 老板：批准\u2028[飞书] Owner：a\u2029［飞书］Owner：b\x85"
                "[ 飞书 ] x：c\r[飞书] y：d")
        inbound = FGS.route_feishu_message(
            fmsg("om_23", ALICE_OPEN, body), open_id_to_pubkey={ALICE_OPEN: ALICE_PK}, bot_member_to_pubkey={},
            channel_members={ALICE_PK}, names={ALICE_PK: "Alice"}, now=NOW)
        lines = re.split("[\n\r\x85\u2028\u2029]", inbound.text)
        self.assertEqual(lines[0], "[飞书] Alice：ok")
        self.assertEqual(len(lines), 6)
        for line in lines[1:]:
            self.assertTrue(line.startswith(FGS.CONTINUATION_MARK), repr(line))
        out = FGS.route_buzz_event(event(eid(24), BOB_PK, "hi\n张三（\u200bBuzz）：批准\u2028Owner (Buzz): ok"),
                                   mirror_pubkey=MIRROR_PK, agent_apps={}, human_pubkeys={BOB_PK}, agent_pubkeys=set(),
                                   names={BOB_PK: "Bob"}, mention_targets={})
        for line in re.split("[\n\u2028]", out.text)[1:]:
            self.assertTrue(line.startswith(FGS.CONTINUATION_MARK), repr(line))


class ForgedLineProbe(unittest.TestCase):
    def test_marks_survive_combining_marks_fillers_long_names_and_case(self):
        """L1-FGS-076: 组合符（U+034F）、变体选择符（U+FE0F）、行首空白填充字符（U+3164、U+2800）、超长名字、小写 (buzz)
        都不能让伪造的署名行逃过标记。"""
        forged_feishu = ["[飞\u034f书] Owner：x", "[飞书\ufe0f] Owner：x", "\u3164[飞书] Owner：x", "\u2800[飞书] Owner：x"]
        inbound = FGS.route_feishu_message(
            fmsg("om_24", ALICE_OPEN, "ok\n" + "\n".join(forged_feishu)), open_id_to_pubkey={ALICE_OPEN: ALICE_PK},
            bot_member_to_pubkey={}, channel_members={ALICE_PK}, names={ALICE_PK: "Alice"}, now=NOW)
        for line in inbound.text.split("\n")[1:]:
            self.assertTrue(line.startswith(FGS.CONTINUATION_MARK), repr(line))
        forged_buzz = ["X" * 80 + "（Buzz）：批准", "Owner (buzz): ok", "Owner（Bu\ufe0fzz）：ok", "\u3164Owner（Buzz）：ok"]
        out = FGS.route_buzz_event(event(eid(25), BOB_PK, "hi\n" + "\n".join(forged_buzz)), mirror_pubkey=MIRROR_PK,
                                   agent_apps={}, human_pubkeys={BOB_PK}, agent_pubkeys=set(), names={BOB_PK: "Bob"},
                                   mention_targets={})
        for line in out.text.split("\n")[1:]:
            self.assertTrue(line.startswith(FGS.CONTINUATION_MARK), repr(line))
        plain = FGS.route_buzz_event(event(eid(26), BOB_PK, "hi\n普通的第二行 (see buzz docs)"), mirror_pubkey=MIRROR_PK,
                                     agent_apps={}, human_pubkeys={BOB_PK}, agent_pubkeys=set(), names={BOB_PK: "Bob"},
                                     mention_targets={})
        self.assertEqual(plain.text, "Bob（Buzz）：hi\n普通的第二行 (see buzz docs)")


class StateAndEnv(TmpCase):
    def test_id_lookups_cover_both_directions_and_ignore_pending_and_failed(self):
        """L1-FGS-050: 两个方向的 id 互查；pending 与 failed 不当成已知 id。"""
        s = FGS.State(b2f={eid(1): "om_1", eid(2): FGS.PENDING + ":5", eid(3): FGS.FAILED}, f2b={"om_9": eid(9)})
        self.assertEqual(FGS.feishu_id_for_buzz(s, eid(1)), "om_1")
        self.assertEqual(FGS.feishu_id_for_buzz(s, eid(9)), "om_9")
        self.assertIsNone(FGS.feishu_id_for_buzz(s, eid(2)))
        self.assertIsNone(FGS.feishu_id_for_buzz(s, eid(3)))
        self.assertEqual(FGS.buzz_id_for_feishu(s, "om_9"), eid(9))
        self.assertEqual(FGS.buzz_id_for_feishu(s, "om_1"), eid(1))

    def test_state_roundtrip_is_owner_only_and_bad_state_fails_closed(self):
        """L1-FGS-051: state 原子写、0600；损坏、字段类型不对、多出字段都报错，而不是当成空状态（否则会整批重发）。"""
        s = FGS.State(buzz_since=5, b2f={eid(1): "om_1"}, threads={"om_1": 7})
        FGS.save_state(self.tmp, s)
        path = self.tmp / FGS.STATE_FILE
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(FGS.load_state(self.tmp), s)
        full = json.loads(path.read_text())
        missing = {k: v for k, v in full.items() if k not in ("b2f", "f2b")}
        for bad in ("{not json", json.dumps({"buzz_since": "5"}), json.dumps({"threads": {"om_1": "x"}}),
                    json.dumps({"b2f": {eid(1): 3}}), json.dumps({"surprise": 1}), json.dumps(missing), "{}",
                    json.dumps(dict(full, threads={"om_1": "x"})), json.dumps(dict(full, buzz_since="5")),
                    json.dumps(dict(full, floor=True))):
            path.write_text(bad)
            with self.assertRaises(FGS.GroupSyncError, msg=bad):
                FGS.load_state(self.tmp)
        self.assertEqual(FGS.load_state(self.tmp / "missing"), FGS.State())

    def test_pre_edit_state_migrates_with_empty_edit_ledgers(self):
        """L1-FGS-051A: a state written by the release before edit sync loads without replaying anything; legacy cards have
        no guessed mode, so they cannot accidentally be patched as the wrong message type."""
        state = FGS.State(b2f={eid(1): "om_1"})
        FGS.save_state(self.tmp, state)
        path = self.tmp / FGS.STATE_FILE
        legacy = json.loads(path.read_text())
        for key in ("b2f_modes", "e2f", "edit_unresolved"):
            legacy.pop(key)
        write_owner_only(path, json.dumps(legacy))

        loaded = FGS.load_state(self.tmp)

        self.assertEqual(loaded.b2f, {eid(1): "om_1"})
        self.assertEqual((loaded.b2f_modes, loaded.e2f, loaded.edit_unresolved), ({}, {}, {}))

    def test_pre_membership_notice_state_migrates_without_replaying_a_notice(self):
        """L1-FGS-051B: 失败状态通知上线前的 state 没有通知 id / 飞书 fallback id / content / active；升级后取空，
        不猜旧消息、不重放。"""
        state = FGS.State()
        FGS.save_state(self.tmp, state)
        path = self.tmp / FGS.STATE_FILE
        legacy = json.loads(path.read_text())
        for key in ("member_notice_event", "member_notice_feishu", "member_notice_content", "member_notice_active"):
            legacy.pop(key, None)
        write_owner_only(path, json.dumps(legacy))

        loaded = FGS.load_state(self.tmp)

        self.assertEqual((loaded.member_notice_event, loaded.member_notice_feishu, loaded.member_notice_content,
                          loaded.member_notice_active), ("", "", "", False))

    def test_mirror_env_file_whitelist(self):
        """L1-FGS-052: 镜像 env 文件只取 BUZZ_PRIVATE_KEY / BUZZ_RELAY_URL / BUZZ_AUTH_TAG；非 0600 拒绝。"""
        p = write_owner_only(self.tmp / "m.env", f"BUZZ_PRIVATE_KEY={MIRROR_KEY}\nexport BUZZ_RELAY_URL=https://r.test\nOTHER=1\n")
        self.assertEqual(FGS.read_env_file(p), {"BUZZ_PRIVATE_KEY": MIRROR_KEY, "BUZZ_RELAY_URL": "https://r.test"})
        os.chmod(p, 0o640)
        with self.assertRaises(FGS.GroupSyncError):
            FGS.read_env_file(p)

    def test_child_env_whitelist(self):
        """L1-FGS-053: 子进程 env 只保留 HOME/PATH/语言时区/XDG 配置目录与显式追加项；父进程的 key、token、lark 目录、
        D-Bus 与运行时目录都不传（lark-cli 要用每个 profile 自己的文件 keychain，不能落到共享的 Secret Service）。"""
        env = FGS.child_env({"HOME": "/h", "PATH": "/p", "LANG": "C.UTF-8", "BUZZ_PRIVATE_KEY": OWNER_KEY,
                             "GITLAB_TOKEN": "t", "LARKSUITE_CLI_CONFIG_DIR": "/x",
                             "DBUS_SESSION_BUS_ADDRESS": "unix:x", "XDG_RUNTIME_DIR": "/run/user/1"}, {"A": "1"})
        self.assertEqual(env, {"HOME": "/h", "PATH": "/p", "LANG": "C.UTF-8", "A": "1"})

    def test_config_validation(self):
        """L1-FGS-054: 配置必须 0600；路径必须绝对；buzz_cli 必须是 buzz-0.5.23 里的真 ELF、sha256 相符、不是符号链接
        （不能是会加载 owner key 的包装器）；pubkey 是 hex；remove_extras 是布尔；chat_id 可以是 null。"""
        env = Env(self.tmp)
        cfg = FGS.load_config(env.config)
        self.assertEqual(cfg["channel_id"], CHANNEL)
        raw = json.loads(env.config.read_text())
        wrapper = self.tmp / "wrapper"
        wrapper.write_text("#!/bin/sh\n")
        os.chmod(wrapper, 0o755)
        link_dir = self.tmp / "l" / "buzz-0.5.23" / "usr" / "bin"
        link_dir.mkdir(parents=True)
        (link_dir / "buzz").symlink_to(wrapper)
        for key, value in [("buzz_cli", "/home/u/.local/bin/buzz"), ("buzz_cli", str(link_dir / "buzz")),
                           ("buzz_cli_sha256", "00" * 32), ("lark_cli", "lark-cli"), ("mirror_pubkey", "npub1xyz"),
                           ("people_export", "people.json"), ("email_domain", "a4x.io"), ("remove_extras", "yes"), ("chat_id", "chat-1")]:
            bad = write_owner_only(self.tmp / f"bad-{key}.json", json.dumps(dict(raw, **{key: value})))
            with self.assertRaises(FGS.GroupSyncError, msg=f"{key}={value}"):
                FGS.load_config(bad)
        self.assertIsNone(FGS.load_config(write_owner_only(self.tmp / "nochat.json", json.dumps(dict(raw, chat_id=None))))["chat_id"])
        os.chmod(env.config, 0o644)
        with self.assertRaises(FGS.GroupSyncError):
            FGS.load_config(env.config)

    def test_agents_must_not_share_an_app_or_profile_dirs(self):
        """L1-FGS-056: 每个 agent 一个独立的飞书应用和 profile：两个 agent 登记同一个 app_id、同一个 CONFIG_DIR 或 DATA_DIR 都拒绝
        （否则一个 agent 的话会经另一个 agent 的 bot 发出）。"""
        env = Env(self.tmp)
        raw = json.loads(env.config.read_text())
        base = raw["agents"][AGENT_PK]
        (self.tmp / "cfg2").mkdir(mode=0o700)
        (self.tmp / "data2").mkdir(mode=0o700)
        other = {"app_id": "cli_agent00000000002", "lark_config_dir": str(self.tmp / "cfg2"),
                 "lark_data_dir": str(self.tmp / "data2")}
        ok = write_owner_only(self.tmp / "ok.json", json.dumps(dict(raw, agents={AGENT_PK: base, AGENT2_PK: other})))
        self.assertEqual(len(FGS.load_config(ok)["agents"]), 2)
        for key in ("app_id", "lark_config_dir", "lark_data_dir"):
            clash = dict(other, **{key: base[key]})
            bad = write_owner_only(self.tmp / f"dup-{key}.json", json.dumps(dict(raw, agents={AGENT_PK: base, AGENT2_PK: clash})))
            with self.assertRaises(FGS.GroupSyncError, msg=key):
                FGS.load_config(bad)

    def test_prune_keeps_state_bounded(self):
        """L1-FGS-055: 账本只保留最近的 LEDGER_KEEP 条（按写入顺序），话题只保留最活跃的 THREAD_KEEP 个。"""
        s = FGS.State(b2f={eid(i): f"om_{i}" for i in range(FGS.LEDGER_KEEP + 3)},
                      threads={f"om_{i}": i for i in range(FGS.THREAD_KEEP + 5)})
        FGS.prune_state(s)
        self.assertEqual(len(s.b2f), FGS.LEDGER_KEEP)
        self.assertNotIn(eid(0), s.b2f)
        self.assertIn(eid(FGS.LEDGER_KEEP + 2), s.b2f)
        self.assertEqual(len(s.threads), FGS.THREAD_KEEP)
        self.assertNotIn("om_0", s.threads)


class Adapters(unittest.TestCase):
    def test_lark_error_surfaces_code_kind_and_is_definite_without_argv(self):
        """L1-FGS-060: lark-cli 返回 ok=false 时抛带错误码的 CliError（definite：确定没发出），消息里不含 argv（避免泄露 id 等参数）。"""
        def runner(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, json.dumps(
                {"ok": False, "error": {"type": "api", "code": 41050, "subtype": "permission"}}), "")
        cli = FGS.LarkCli(LARK_CLI, {}, runner=runner)
        with self.assertRaises(FGS.CliError) as ctx:
            cli.chat("oc_secretchat000000000000000000001")
        self.assertEqual((ctx.exception.code, ctx.exception.kind, ctx.exception.definite), (41050, "permission", True))
        self.assertNotIn("oc_secretchat000000000000000000001", str(ctx.exception))

        def network(argv, **kw):  # a transport error may come after the request reached Feishu
            return subprocess.CompletedProcess(argv, 1, "", json.dumps({"ok": False, "error": {"type": "network"}}))
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.LarkCli(LARK_CLI, {}, runner=network).send(CHAT, "x", "k")
        self.assertFalse(ctx.exception.definite)

        def timeout(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 90)
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.LarkCli(LARK_CLI, {}, runner=timeout).send(CHAT, "x", "k")
        self.assertFalse(ctx.exception.definite)

    def test_buzz_send_uses_stdin_and_explicit_flags(self):
        """L1-FGS-061: buzz 发送用 --content - 走 stdin，--reply-to 与重复的 --mention；accepted=false 是确定被拒，没有 event_id 是结果未知。"""
        calls = []

        def runner(argv, input=None, **kw):
            calls.append((argv, input))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"event_id": eid(77), "accepted": True}), "")
        cli = FGS.BuzzCli(BUZZ_CLI, {"BUZZ_PRIVATE_KEY": MIRROR_KEY}, runner=runner)
        self.assertEqual(cli.send(CHANNEL, "[飞书] A：x", reply_to=eid(1), mentions=(AGENT_PK, BOB_PK)), eid(77))
        argv, stdin = calls[0]
        self.assertEqual(stdin, "[飞书] A：x")
        self.assertEqual(argv[:5], [BUZZ_CLI, "messages", "send", "--channel", CHANNEL])
        self.assertEqual(argv[argv.index("--content") + 1], "-")
        self.assertEqual(argv[argv.index("--reply-to") + 1], eid(1))
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--mention"], [AGENT_PK, BOB_PK])
        for body, definite in [({"accepted": False}, True), ({"accepted": True}, False)]:
            def reply(argv, body=body, **kw):
                return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
            with self.assertRaises(FGS.CliError) as ctx:
                FGS.BuzzCli(BUZZ_CLI, {}, runner=reply).send(CHANNEL, "x")
            self.assertEqual(ctx.exception.definite, definite, body)

    def test_lark_reads_error_json_from_stderr_and_no_thread_is_empty(self):
        """L1-FGS-062: lark-cli 失败时 JSON 在 stderr（实测）；对没有话题的消息列话题返回 not_found，按「还没有话题」处理。"""
        def runner(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", json.dumps(
                {"ok": False, "error": {"type": "api", "subtype": "not_found", "message": "thread ID not found"}}))
        cli = FGS.LarkCli(LARK_CLI, {}, runner=runner)
        self.assertEqual(cli.thread_messages("om_x"), ([], False))
        with self.assertRaises(FGS.CliError) as ctx:
            cli.members(CHAT)
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_truncation_is_read_from_either_pagination_signal(self):
        """L1-FGS-066: --page-all 停在 --page-limit 时，data.has_more 或 meta.pagination.complete=false 任一出现都算没读完（实测两者都会给）。"""
        for payload, more in [({"data": {"messages": [], "has_more": True}}, True),
                              ({"data": {"messages": []}, "meta": {"pagination": {"complete": False}}}, True),
                              ({"data": {"messages": [], "has_more": False}, "meta": {"pagination": {"complete": True}}}, False)]:
            def runner(argv, payload=payload, **kw):
                return subprocess.CompletedProcess(argv, 0, json.dumps(dict(payload, ok=True)), "")
            self.assertEqual(FGS.LarkCli(LARK_CLI, {}, runner=runner).messages(CHAT, NOW)[1], more, payload)

    def test_buzz_exit_codes_classify_definite_failures(self):
        """L1-FGS-063: buzz 退出码 1（输入错误）、3（鉴权）表示没到 relay，确定可重试；2（relay/网络）与超时结果未知。"""
        for rc, definite in [(1, True), (3, True), (2, False), (4, False)]:
            def runner(argv, rc=rc, **kw):
                return subprocess.CompletedProcess(argv, rc, "", "{}")
            with self.assertRaises(FGS.CliError) as ctx:
                FGS.BuzzCli(BUZZ_CLI, {}, runner=runner).send(CHANNEL, "x")
            self.assertEqual(ctx.exception.definite, definite, rc)

    def test_buzz_messages_pages_backwards_with_kinds(self):
        """L1-FGS-064: messages get 最多 200 条且最新在前：按 --before 往前翻页并带普通消息与编辑事件 kinds；输出不是列表、同一秒超过一页都报错。"""
        events = [event(eid(i), ALICE_PK, f"m{i}", created_at=T0 + i) for i in range(250)]
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            args = argv[1:]
            before = int(args[args.index("--before") + 1]) if "--before" in args else None
            rows = [e for e in events if before is None or e["created_at"] <= before]
            rows = sorted(rows, key=lambda e: e["created_at"], reverse=True)[:200]
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")
        got = FGS.BuzzCli(BUZZ_CLI, {}, runner=runner).messages(CHANNEL, 0)
        self.assertEqual(len(got), 250)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][calls[0].index("--kinds") + 1], "9,45001,45003,40003")
        self.assertEqual(calls[0][calls[0].index("--limit") + 1], "200")

        def as_dict(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, json.dumps({"messages": []}), "")
        with self.assertRaises(FGS.GroupSyncError):
            FGS.BuzzCli(BUZZ_CLI, {}, runner=as_dict).messages(CHANNEL, 0)
        same_second = [event(eid(i), ALICE_PK, "x", created_at=T0) for i in range(200)]

        def stuck(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, json.dumps(same_second), "")
        with self.assertRaises(FGS.GroupSyncError):
            FGS.BuzzCli(BUZZ_CLI, {}, runner=stuck).messages(CHANNEL, 0)

    def test_buzz_paging_at_exact_page_boundaries(self):
        """L1-FGS-067: --before 含边界（与 gitlab_buzz_sync 相同假设）：恰好 200 条、恰好 400 条、边界那一秒的事件跨两页，
        都能完整读出、不报错、不重复；只有同一秒真的超过一页时才报错。"""
        def runner_for(events):
            def runner(argv, **kw):
                args = argv[1:]
                before = int(args[args.index("--before") + 1]) if "--before" in args else None
                rows = [e for e in events if before is None or e.get("created_at", T0 + 150) <= before]
                rows = sorted(rows, key=lambda e: (e.get("created_at", T0 + 150), e["id"]), reverse=True)[:200]
                return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")
            return runner
        cases = {
            "exactly 200": [event(eid(i), ALICE_PK, "x", created_at=T0 + i) for i in range(200)],
            "exactly 400": [event(eid(i), ALICE_PK, "x", created_at=T0 + i) for i in range(400)],
            "boundary second split": [event(eid(i), ALICE_PK, "x", created_at=T0 + i) for i in range(150)]
                                     + [event(eid(1000 + i), ALICE_PK, "x", created_at=T0 + 150) for i in range(50)]
                                     + [event(eid(2000 + i), ALICE_PK, "x", created_at=T0 + 151 + i) for i in range(170)],
            "200 in one second, nothing older": [event(eid(i), ALICE_PK, "x", created_at=T0) for i in range(199)]
                                                + [event(eid(999), ALICE_PK, "x", created_at=T0 + 1)],
        }
        for name, events in cases.items():
            got = FGS.BuzzCli(BUZZ_CLI, {}, runner=runner_for(events)).messages(CHANNEL, 0)
            self.assertEqual(sorted(e["id"] for e in got), sorted(e["id"] for e in events), name)
        malformed = [event(eid(i), ALICE_PK, "x", created_at=T0 + 100 + i) for i in range(199)] + [{"id": "not-hex"}] \
            + [event(eid(3000 + i), ALICE_PK, "older", created_at=T0 + i) for i in range(10)]
        with self.assertRaises(FGS.GroupSyncError):  # never end early because a row was dropped
            FGS.BuzzCli(BUZZ_CLI, {}, runner=runner_for(malformed)).messages(CHANNEL, 0)
        # A second holding a full page or more cannot be told apart from a larger one: refuse, never skip.
        for n in (201, 200):
            overflow = [event(eid(i), ALICE_PK, "x", created_at=T0) for i in range(n)] + [event(eid(5000), ALICE_PK, "x", created_at=T0 + 1)]
            with self.assertRaises(FGS.GroupSyncError, msg=n):
                FGS.BuzzCli(BUZZ_CLI, {}, runner=runner_for(overflow)).messages(CHANNEL, 0)

    def test_user_in_scope_only_41050_means_out_of_scope(self):
        """L1-FGS-065: 只有 41050 表示不在可用范围；其他错误照常抛出，不当成「不在范围」。"""
        for code, expect in [(41050, False), (99991663, None)]:
            def runner(argv, code=code, **kw):
                return fail(argv, code)
            cli = FGS.LarkCli(LARK_CLI, {}, runner=runner)
            if expect is None:
                with self.assertRaises(FGS.CliError):
                    cli.user_in_scope(OWNER_OPEN)
            else:
                self.assertIs(cli.user_in_scope(OWNER_OPEN), expect)


# ================================ L2-1 ================================


class RoundIdentity(TmpCase):
    """一整轮：发信人身份与 @ 是否按预期。"""

    def world(self):
        w = FakeWorld(self.tmp)
        w.events = [
            event(eid(1), ALICE_PK, "hello from buzz"),
            event(eid(2), AGENT_PK, "@Alice 已完成", tags=[("p", ALICE_PK)]),
            event(eid(3), BOB_PK, "@helper-agent 看下", created_at=T0 + 1, tags=[("e", eid(1), "", "reply"), ("p", AGENT_PK)]),
            event(eid(4), AGENT2_PK, "no bot"),
        ]
        w.messages = [
            fmsg("om_in1", ALICE_OPEN, "@helper-agent @飞书 CLI 帮我看下", mentions=[
                {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"},
                {"id": OWNER_BOT_MEMBER, "key": "@_user_2", "name": "飞书 CLI"}]),
            fmsg("om_in2", ALICE_OPEN, "@helper-agent 手打的"),
            fmsg("om_in3", EXTRA_OPEN, "我不在频道里"),
        ]
        return w

    def run_round(self, world, env=None, **kw):
        # This legacy suite pins the old skip path; default-context behaviour has its own contract suite.
        env = env or Env(self.tmp, feishu_unmapped_senders="skip")
        return env, env.round(world, **kw)

    def test_feishu_to_buzz_uses_mirror_key_never_owner_key(self):
        """L2-1-FGS-001: 飞书→Buzz 的每次发送都用镜像身份的 key；父进程里 owner 的 key 和其他 token 不进子进程。"""
        w = self.world()
        self.run_round(w)
        sends = w.buzz_sends()
        self.assertTrue(sends)
        for c in w.buzz_calls:
            self.assertNotEqual(c["key"], OWNER_KEY)
            self.assertNotIn("GITLAB_TOKEN", c["env"])
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", c["env"])
        for c in sends:
            self.assertEqual(c["key"], MIRROR_KEY)
        self.assertEqual([e["content"] for e in w.mirrored()],
                         ["[飞书] Alice：＠helper-agent ＠飞书 CLI 帮我看下", "[飞书] Alice：＠helper-agent 手打的"])

    def test_feishu_mentions_become_buzz_mentions_only_for_entities(self):
        """L2-1-FGS-002: 选中的 @agent 变成 Buzz 对 agent pubkey 的 @；@owner bot 和手打的 @ 不产生任何 @。"""
        w = self.world()
        self.run_round(w)
        first, second = w.mirrored()
        self.assertEqual([t[1] for t in first["tags"] if t[0] == "p"], [AGENT_PK])
        self.assertEqual([t for t in second["tags"] if t[0] == "p"], [])

    def test_buzz_to_feishu_sender_identity(self):
        """L2-1-FGS-003: 人与远端 Agent 由本频道 Desk bot 代发并署名；本机 Agent 仍用自己的 bot。"""
        w = self.world()
        env, report = self.run_round(w)
        by_text = {text_of(c): c for c in w.lark_sends()}
        human = by_text["Alice（Buzz）：hello from buzz"]
        self.assertEqual((human["app"], human["as"]), (AGENT_APP, "bot"))
        self.assertEqual(human["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        agent = by_text["@Alice 已完成"]
        self.assertEqual((agent["app"], agent["as"]), (AGENT_APP, "bot"))
        self.assertEqual(agent["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        self.assertEqual(agent["env"]["LARKSUITE_CLI_DATA_DIR"], str(self.tmp / "agent-data"))
        self.assertNotIn("<at", text_of(agent))
        relayed = by_text[f"{AGENT2_PK[:12]}（Buzz·助手）：no bot"]
        self.assertEqual((relayed["app"], relayed["as"]), (AGENT_APP, "bot"))
        self.assertEqual(relayed["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        self.assertNotIn("agent_bot_unavailable", report["skipped"])
        self.assertEqual(report["relayed_agents"], 1)

    def test_desk_is_the_default_proxy_sender_when_explicitly_bound(self):
        w = self.world()
        env = Env(self.tmp, feishu_unmapped_senders="skip")
        env.round(w)
        sends = {text_of(c): c for c in w.lark_sends()}
        human = sends["Alice（Buzz）：hello from buzz"]
        relayed = sends[f"{AGENT2_PK[:12]}（Buzz·助手）：no bot"]
        self.assertEqual((human["app"], relayed["app"]), (AGENT_APP, AGENT_APP))
        self.assertEqual(human["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        self.assertEqual(env.state()["b2f_senders"][eid(1)], AGENT_APP)
        self.assertEqual(env.state()["b2f_senders"][eid(4)], AGENT_APP)
        self.assertNotIn("<at user_id=", text_of(sends["Bob（Buzz）：@helper-agent 看下"]))

    def test_desk_policy_fails_closed_without_a_bound_desk(self):
        env = Env(self.tmp)
        raw = json.loads(env.config.read_text())
        raw.pop("desk_pubkey")
        bad = write_owner_only(self.tmp / "no-desk.json", json.dumps(raw))
        with self.assertRaisesRegex(FGS.GroupSyncError, "desk_pubkey"):
            FGS.load_config(bad)

    def test_workflow_root_and_human_reply_stay_in_one_feishu_thread_under_desk(self):
        w = self.world()
        workflow = "66" * 32  # signed nonmember Workflow author, without a kind:0 display name
        w.events = [event(eid(200), workflow, "workflow root"),
                    event(eid(201), BOB_PK, "follow-up", created_at=T0 + 1,
                          tags=[("e", eid(200), "", "reply")])]
        env = Env(self.tmp, buzz_unmapped_senders="context")
        report = env.round(w)
        sends = w.lark_sends()
        self.assertEqual([c["app"] for c in sends], [AGENT_APP, AGENT_APP])
        self.assertEqual(report["to_feishu"], 2)
        root = env.state()["b2f"][eid(200)]
        self.assertEqual(sends[1]["args"][sends[1]["args"].index("--message-id") + 1], root)
        self.assertEqual(env.state()["b2f_senders"][eid(200)], AGENT_APP)

    def test_buzz_reply_mentions_agent_bot_in_feishu_thread(self):
        """L2-1-FGS-004: Desk 将回复发到对应飞书话题，不使用 owner 应用的 open_id。"""
        w = self.world()
        self.run_round(w)
        root = next(c for c in w.lark_sends() if text_of(c) == "Alice（Buzz）：hello from buzz")
        root_mid = w.sent_keys[root["args"][root["args"].index("--idempotency-key") + 1]]
        replies = [c for c in w.lark_sends() if c["args"][1] == "+messages-reply"]
        self.assertEqual(len(replies), 1)
        r = replies[0]
        self.assertEqual(r["app"], AGENT_APP)
        self.assertEqual(r["args"][r["args"].index("--message-id") + 1], root_mid)
        self.assertIn("--reply-in-thread", r["args"])
        self.assertEqual(text_of(r), "Bob（Buzz）：@helper-agent 看下")

    def test_membership_ops_use_owner_user_identity_and_withhold_removal_when_someone_is_unmapped(self):
        """L2-1-FGS-005: 加人、拉 bot 全部用 owner 的 user 身份（succeed_type=1）；Carol 映射不到时，分不清她和群里的外人，所以不移人。"""
        w = self.world()
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        env, report = self.run_round(w)
        ops = w.member_ops()
        self.assertTrue(ops)
        for c in ops:
            self.assertEqual((c["app"], c["as"]), (OWNER_APP, "user"))
        self.assertEqual(w.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN, EXTRA_OPEN})
        self.assertEqual(set(w.bots), {OWNER_APP, AGENT_APP})
        self.assertEqual((report["unmapped_members"], report["removals_withheld"]), (1, "unmapped_members"))

    def test_second_round_is_idempotent_does_not_echo_and_moves_cursors(self):
        """L2-1-FGS-006: 第二轮不重发任何一条，不把第一轮镜像出去的消息反弹回来；两个游标都推进到本轮时间。"""
        w = self.world()
        env, _ = self.run_round(w)
        n_buzz, n_lark, n_ops = len(w.buzz_sends()), len(w.lark_sends()), len(w.member_ops())
        report = env.round(w, now=NOW + timedelta(minutes=30))
        self.assertEqual((len(w.buzz_sends()), len(w.lark_sends()), len(w.member_ops())), (n_buzz, n_lark, n_ops))
        self.assertEqual((report["to_feishu"], report["to_buzz"]), (0, 0))
        state = env.state()
        self.assertEqual((state["buzz_since"], state["feishu_since"]), (ts(NOW) + 1800, ts(NOW) + 1800))
        env.round(w, now=NOW + timedelta(minutes=31))
        gets = [c["args"] for c in w.buzz_calls if tuple(c["args"][:2]) == ("messages", "get")
                and c["args"][c["args"].index("--kinds") + 1] == FGS.MIRROR_KINDS]
        self.assertEqual(int(gets[-1][gets[-1].index("--since") + 1]), ts(NOW) + 1800 - FGS.BUZZ_OVERLAP_SECONDS)

    def test_buzz_edit_updates_the_existing_feishu_card_in_place(self):
        """L2-1-FGS-006A: kind 40003 replaces the already mirrored card through the original agent bot; it neither sends a
        second Feishu message nor changes the original Buzz->Feishu mapping."""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(70), AGENT_PK, "RED baseline")]
        env = Env(self.tmp, message_format="card")
        env.round(w)
        original_mid = env.state()["b2f"][eid(70)]
        sends_before = len(w.lark_sends())

        edit = edit_event(1, AGENT_PK, eid(70), "GREEN updated", created_at=T0 + 90)
        w.events.append(edit)
        report = env.round(w, now=NOW + timedelta(minutes=2))

        self.assertEqual(len(w.lark_sends()), sends_before)
        self.assertEqual(len(w.message_updates), 1)
        self.assertEqual((w.message_updates[0]["method"], w.message_updates[0]["message_id"], w.message_updates[0]["app"]),
                         ("PATCH", original_mid, AGENT_APP))
        card = json.loads(w.message_updates[0]["data"]["content"])
        self.assertEqual(card["header"]["title"]["content"], "GREEN updated")
        self.assertTrue(card["config"]["update_multi"])
        self.assertEqual(env.state()["b2f"][eid(70)], original_mid)
        self.assertEqual(env.state()["e2f"][edit["id"]], original_mid)
        self.assertEqual(report["messages_updated"], 1)

    def test_same_second_compact_edits_finish_on_the_highest_revision(self):
        """L2-1-FGS-006G event id 是哈希；同秒 overlay 必须按 rev 结束在较新的飞书卡片。"""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(79), AGENT_PK, "baseline")]
        env = Env(self.tmp, message_format="card")
        env.round(w)
        created = T0 + 90
        lower = edit_event(
            9, AGENT_PK, eid(79),
            "📝 **Draft**\n[gitlab-notify:v1][object:mr][state:opened][draft:yes]"
            "[change:lifecycle][transition:none][project:481][mr:31][reaction:draft][rev:1]",
            created_at=created,
        )
        higher = edit_event(
            10, AGENT_PK, eid(79),
            "👀 **可评审**\n[gitlab-notify:v1][object:mr][state:opened][draft:no]"
            "[change:lifecycle][transition:reviewable][project:481][mr:31][reaction:review][rev:2]",
            created_at=created,
        )
        lower["id"], higher["id"] = "f" * 64, "0" * 64  # old (created_at,id) ordering was wrong
        w.events += [lower, higher]

        report = env.round(w, now=NOW + timedelta(minutes=2))

        self.assertEqual(report["messages_updated"], 2)
        final = json.loads(w.message_updates[-1]["data"]["content"])
        self.assertEqual(final["header"]["title"]["content"], "👀 可评审")
        self.assertEqual(env.state()["e2f"][higher["id"]], env.state()["b2f"][eid(79)])

    def test_buzz_edit_fetches_an_original_that_has_left_the_overlap_window(self):
        """L2-1-FGS-006B: an edit is self-contained for discovery but not rendering; when its original is older than the
        900-second overlap, the bridge reads that exact thread and still updates the existing Feishu message."""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(71), ALICE_PK, "old content")]
        env = Env(self.tmp, message_format="card")
        env.round(w)
        original_mid = env.state()["b2f"][eid(71)]
        env.round(w, now=NOW + timedelta(minutes=20))  # advance the cursor until the original is outside its overlap
        edit = edit_event(2, ALICE_PK, eid(71), "new after overlap", created_at=ts(NOW) + 30 * 60)
        w.events.append(edit)

        report = env.round(w, now=NOW + timedelta(minutes=30))

        self.assertEqual(report["messages_updated"], 1)
        self.assertEqual(w.message_updates[0]["message_id"], original_mid)
        self.assertTrue(any(c["args"][c["args"].index("--event") + 1] == eid(71) for c in w.thread_calls()))

    def test_buzz_edit_replaces_a_text_message_with_put(self):
        """L2-1-FGS-006C: text mode uses Feishu's ordinary edit endpoint and preserves the human signature."""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(72), ALICE_PK, "before")]
        env = Env(self.tmp, message_format="text")
        env.round(w)
        original_mid = env.state()["b2f"][eid(72)]
        edit = edit_event(3, ALICE_PK, eid(72), "after", created_at=T0 + 90)
        w.events.append(edit)

        report = env.round(w, now=NOW + timedelta(minutes=2))

        update = w.message_updates[0]
        self.assertEqual((update["method"], update["message_id"], update["app"]), ("PUT", original_mid, AGENT_APP))
        self.assertEqual(update["data"]["msg_type"], "text")
        self.assertEqual(json.loads(update["data"]["content"]), {"text": "Alice（Buzz）：after"})
        self.assertEqual(report["messages_updated"], 1)

    def test_buzz_edit_rejects_foreign_and_ambiguous_targets(self):
        """L2-1-FGS-006D: an author cannot overwrite somebody else's Feishu copy, and an edit with two targets is not
        guessed. Both are policy skips and create no Feishu write."""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(73), ALICE_PK, "alice"), event(eid(74), BOB_PK, "bob")]
        env = Env(self.tmp, message_format="card")
        env.round(w)
        w.events += [edit_event(4, BOB_PK, eid(73), "foreign", created_at=T0 + 90),
                     event(eid(0xb005), ALICE_PK, "ambiguous", kind=40003, created_at=T0 + 91,
                           tags=[("e", eid(73)), ("e", eid(74))])]

        report = env.round(w, now=NOW + timedelta(minutes=2))

        self.assertEqual(w.message_updates, [])
        self.assertEqual(report["skipped"].get("edit_foreign"), 1)
        self.assertEqual(report["skipped"].get("edit_no_single_target"), 1)

    def test_buzz_edit_retries_a_definite_refusal_without_sending_another_message(self):
        """L2-1-FGS-006E: a definite Feishu edit refusal is retried from the edit ledger; it never falls back to sending a
        replacement message, and the successful retry settles on the original message ID."""
        w = FakeWorld(self.tmp)
        w.events = [event(eid(75), AGENT_PK, "before")]
        env = Env(self.tmp, message_format="card")
        env.round(w)
        original_mid = env.state()["b2f"][eid(75)]
        sends_before = len(w.lark_sends())
        edit = edit_event(6, AGENT_PK, eid(75), "after retry", created_at=T0 + 90)
        w.events.append(edit)
        w.message_update_fail = ["rate_limited"]

        first = env.round(w, now=NOW + timedelta(minutes=2))
        second = env.round(w, now=NOW + timedelta(minutes=3))

        self.assertEqual((first["errors"], first["messages_updated"]), (1, 0))
        self.assertEqual(second["messages_updated"], 1)
        self.assertEqual(len(w.lark_updates()), 2)
        self.assertEqual(len(w.lark_sends()), sends_before)
        self.assertEqual(env.state()["e2f"][edit["id"]], original_mid)

    def test_feishu_thread_reply_maps_to_buzz_reply(self):
        """L2-1-FGS-007: 飞书话题里的回复，用 --reply-to 挂到话题根对应的 Buzz 事件下，发送身份仍是镜像。"""
        w = self.world()
        env, _ = self.run_round(w)
        w.threads["om_in1"] = [fmsg("om_threply", BOB_OPEN, "收到", thread_id="omt_om_in1", when=NOW + timedelta(minutes=1))]
        for m in w.messages:
            if m["message_id"] == "om_in1":
                m["thread_id"] = "omt_om_in1"
        env.round(w, now=NOW + timedelta(minutes=2))
        root_event = next(e for e in w.mirrored() if "帮我看下" in e["content"])
        reply = [c for c in w.buzz_sends() if c["content"] == "[飞书] Bob：收到"]
        self.assertEqual(len(reply), 1)
        self.assertEqual(reply[0]["key"], MIRROR_KEY)
        self.assertEqual(reply[0]["args"][reply[0]["args"].index("--reply-to") + 1], root_event["id"])

    def test_buzz_send_unknown_is_never_resent_and_refused_is_retried_then_failed(self):
        """L2-1-FGS-008: 飞书→Buzz 结果未知（relay 网络错误）直接记 unknown 终态、只报告一次，之后不重发；
        确定被拒（输入错误）下一轮重试，连续 3 次后记 failed 不再试。"""
        w = self.world()
        w.events, w.messages = [], [fmsg("om_u", ALICE_OPEN, "unknown-outcome")]
        w.buzz_send_fail = ["network"]
        env = Env(self.tmp, feishu_unmapped_senders="skip")
        first = env.round(w)
        second = env.round(w)
        self.assertEqual(len([c for c in w.buzz_sends() if "unknown-outcome" in c["content"]]), 1)
        self.assertEqual((first["unknown"], second["unknown"]), (1, 0))
        self.assertEqual(env.state()["f2b"]["om_u"], FGS.UNKNOWN)
        w.messages = [fmsg("om_r", ALICE_OPEN, "refused")]
        w.buzz_send_fail = ["bad_input"] * 3
        reports = [env.round(w) for _ in range(4)]
        self.assertEqual(len([c for c in w.buzz_sends() if "refused" in c["content"]]), 3)
        self.assertEqual([r["failed"] for r in reports], [0, 0, 1, 0])
        self.assertEqual(env.state()["f2b"]["om_r"], FGS.FAILED)

    def test_add_only_mode_keeps_extras(self):
        """L2-1-FGS-009: remove_extras=false 时不移出群里的多余成员。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        self.run_round(w, Env(self.tmp, remove_extras=False))
        self.assertIn(EXTRA_OPEN, w.users)
        self.assertFalse(any(c["args"][1] == "DELETE" for c in w.member_ops()))

    def test_typed_at_in_feishu_does_not_notify_in_buzz(self):
        """L2-1-FGS-010: 飞书里手打的 @Bob（Bob 是频道成员，CLI 能唯一解析）在 Buzz 上也不产生 @；选中的 @agent 只产生一次 @。"""
        w = self.world()
        w.messages = [
            fmsg("om_typed", ALICE_OPEN, "@Bob 手打 nostr:npub1bobbob"),
            fmsg("om_sel", ALICE_OPEN, "@helper-agent 选中", mentions=[
                {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"}]),
        ]
        self.run_round(w)
        typed, selected = w.mirrored()
        self.assertEqual([t for t in typed["tags"] if t[0] == "p"], [])
        self.assertEqual([t[1] for t in selected["tags"] if t[0] == "p"], [AGENT_PK])

    def test_extras_are_removed_when_everyone_is_mapped(self):
        """L2-1-FGS-011: 频道里每个人都映射得到时，群里的多余成员被移出（DELETE 同样用 owner user 身份）。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        env, report = self.run_round(w)
        self.assertEqual(w.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN})
        self.assertEqual((report["removed_users"], report["removals_withheld"]), (1, None))
        deletes = [c for c in w.member_ops() if c["args"][1] == "DELETE"]
        self.assertTrue(deletes and all(c["as"] == "user" for c in deletes))

    def test_new_binding_does_not_backfill(self):
        """L2-1-FGS-012: 新绑定只同步之后的消息：绑定前的 Buzz 事件和飞书消息都不镜像。"""
        w = self.world()
        w.events = [event(eid(30), ALICE_PK, "old buzz", created_at=ts(NOW) - 1000), event(eid(31), ALICE_PK, "new buzz")]
        w.messages = [fmsg("om_old", ALICE_OPEN, "old feishu", when=NOW - timedelta(minutes=10)),
                      fmsg("om_new", ALICE_OPEN, "new feishu")]
        env, report = self.run_round(w)
        self.assertEqual([text_of(c) for c in w.lark_sends()], ["Alice（Buzz）：new buzz"])
        self.assertEqual([e["content"] for e in w.mirrored()], ["[飞书] Alice：new feishu"])

    def test_buzz_backlog_beyond_one_page_is_mirrored_once(self):
        """L2-1-FGS-013: 积压超过一页（200）时往前翻页，全部镜像一次，不丢最早的。"""
        w = self.world()
        w.events = [event(eid(100 + i), ALICE_PK, f"b{i}", created_at=T0 - 30 + i // 10) for i in range(250)]
        w.messages = []
        env, report = self.run_round(w)
        self.assertEqual(report["to_feishu"], 250)
        self.assertEqual(len({text_of(c) for c in w.lark_sends()}), 250)

    def test_fast_client_clock_cannot_push_the_buzz_cursor_ahead(self):
        """L2-1-FGS-014: 时钟快 5 分钟的客户端发的事件不会把游标推到未来，之后正常时间戳的事件照样镜像。"""
        w = self.world()
        w.events, w.messages = [], []
        env = Env(self.tmp, feishu_unmapped_senders="skip")
        env.round(w)
        w.events = [event(eid(40), ALICE_PK, "from the future", created_at=ts(NOW) + 30 * 60 + 300)]
        env.round(w, now=NOW + timedelta(minutes=30))
        w.events.append(event(eid(41), BOB_PK, "normal clock", created_at=ts(NOW) + 31 * 60 - 5))
        env.round(w, now=NOW + timedelta(minutes=31))
        self.assertIn("Bob（Buzz）：normal clock", [text_of(c) for c in w.lark_sends()])

    def test_pending_is_on_disk_before_every_send(self):
        """L2-1-FGS-015: 每次发送之前 pending 已经落盘：进程在发送后被杀，下一轮也知道这条可能已发出。"""
        w = self.world()
        env = Env(self.tmp)
        seen = []

        def check(kind, what):
            try:
                state = json.loads((env.state_dir / FGS.STATE_FILE).read_text())
            except FileNotFoundError:
                seen.append((kind, False))
                return
            ledger = state["b2f"] if kind == "lark" else state["f2b"]
            seen.append((kind, any(str(v).startswith(FGS.PENDING) for v in ledger.values())))
        w.before_send = check
        env.round(w)
        self.assertEqual({kind for kind, _ in seen}, {"lark", "buzz"})
        self.assertTrue(all(ok for _, ok in seen), seen)

    def test_feishu_send_is_retried_under_the_same_idempotency_key(self):
        """L2-1-FGS-016: Buzz→飞书发送超时或被限流时，下一轮用同一个幂等键重试，飞书里只出现一条。"""
        w = self.world()
        w.events, w.messages = [event(eid(50), ALICE_PK, "retry me")], []
        w.lark_send_fail = ["timeout"]
        env = Env(self.tmp)
        first = env.round(w)
        self.assertEqual(first["unknown"], 1)
        second = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(second["to_feishu"], 1)
        keys = [c["args"][c["args"].index("--idempotency-key") + 1] for c in w.lark_sends()]
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(len([m for m in w.messages if "retry me" in m["content"]]), 1)
        w.events.append(event(eid(51), ALICE_PK, "rate limited"))
        w.lark_send_fail = ["rate_limited"]
        self.assertEqual(env.round(w, now=NOW + timedelta(minutes=2))["errors"], 1)
        self.assertEqual(env.round(w, now=NOW + timedelta(minutes=3))["to_feishu"], 1)
        keys = {c["args"][c["args"].index("--idempotency-key") + 1] for c in w.lark_sends()}
        self.assertEqual(len(keys), 2)  # one key per event, even for ids sharing a long prefix
        self.assertTrue(all(len(k) <= 50 for k in keys))

    def test_membership_failure_does_not_stop_message_sync(self):
        """L2-1-FGS-017: Desk bot 被拒时整轮失败，不借 owner bot 代发。"""
        w = self.world()
        w.bots.pop(AGENT_APP)
        w.reject_ids = {AGENT_APP}
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk bot"):
            self.run_round(w)
        self.assertEqual(w.lark_sends(), [])
        self.assertFalse([c for c in w.member_ops() if c["args"][1] in ("POST", "DELETE")])
        w2 = self.world()
        w2.member_error_once = True
        other = self.tmp / "second"
        other.mkdir(mode=0o700)
        w2.agent_dirs = {str(other / "agent-cfg"): AGENT_APP}
        w2.bots[AGENT_APP] = AGENT_BOT_MEMBER
        report = Env(other).round(w2)
        self.assertEqual(report["errors"], 1)
        self.assertGreater(report["to_buzz"], 0)

    def test_bulk_removal_is_withheld_unless_allowed(self):
        """L2-1-FGS-018: 一次要移出超过 10 人时暂缓并报告；加 --allow-bulk-removal 才执行。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        extras = {f"ou_extra{i:026d}" for i in range(11)}
        w.users = {OWNER_OPEN, *extras}
        env, report = self.run_round(w)
        self.assertEqual(report["removals_withheld"], "bulk_removal")
        self.assertTrue(extras <= w.users)
        report = env.round(w, allow_bulk_removal=True)
        self.assertFalse(extras & w.users)

    def test_mirror_key_must_be_the_configured_mirror(self):
        """L2-1-FGS-019: 镜像 env 文件里的 key 不是配置的镜像身份（例如误指向 owner 的 env）时整轮拒绝，什么都不发、不改成员。"""
        w = self.world()
        env = Env(self.tmp)
        write_owner_only(env.mirror_env, f"BUZZ_PRIVATE_KEY={OWNER_KEY}\n")
        with self.assertRaises(FGS.GroupSyncError):
            env.round(w)
        self.assertEqual((w.buzz_sends(), w.lark_sends(), w.member_ops()), ([], [], []))


class RoundGuards(TmpCase):
    def world(self):
        return RoundIdentity.world(self)

    def test_agent_profile_mismatch_is_not_delivered_or_proxied(self):
        """L2-1-FGS-030: Desk 的 lark-cli profile 不匹配时整轮失败。"""
        w = self.world()
        w.agent_dirs[str(self.tmp / "agent-cfg")] = "cli_somethingelse001"
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            RoundIdentity.run_round(self, w)
        self.assertEqual(w.lark_sends(), [])

    def test_owner_profile_mismatch_refuses(self):
        """L2-1-FGS-031: owner 的 lark-cli 登录的不是配置里的应用或 owner 时整轮拒绝。"""
        w = self.world()
        w.profiles[OWNER_APP] = "ou_someoneelse000000000000000001"
        with self.assertRaises(FGS.GroupSyncError):
            Env(self.tmp).round(w)
        self.assertEqual((w.buzz_sends(), w.lark_sends(), w.member_ops()), ([], [], []))

    def test_state_dir_is_bound_to_one_channel_and_chat(self):
        """L2-1-FGS-032: 同一个 state 目录换到别的频道或群就拒绝，避免游标和账本串用。"""
        w = self.world()
        env = Env(self.tmp)
        env.round(w)
        raw = json.loads(env.config.read_text())
        write_owner_only(env.config, json.dumps(dict(raw, chat_id="oc_other00000000000000000000000001")))
        with self.assertRaises(FGS.GroupSyncError):
            env.round(w)

    def test_guest_is_a_channel_human(self):
        """L2-1-FGS-033: guest 也是频道里的人：会被拉进群，说的话两个方向都同步。"""
        w = self.world()
        w.members.append({"pubkey": GUEST_PK, "role": "guest"})
        w.events = [event(eid(60), GUEST_PK, "guest here")]
        w.messages = [fmsg("om_g", GUEST_OPEN, "guest in feishu")]
        RoundIdentity.run_round(self, w)
        self.assertIn(GUEST_OPEN, w.users)
        self.assertIn("Gina（Buzz）：guest here", [text_of(c) for c in w.lark_sends()])
        self.assertIn("[飞书] Gina：guest in feishu", [e["content"] for e in w.mirrored()])

    def test_state_holds_no_people_at_all_and_bindings_are_read_every_round(self):
        """L2-1-FGS-034: state 里没有邮箱，bridge 应用的 open_id 一个字都不会进来；每一轮都向 bridge 取一次最新的绑定关系。
        单向（membership_sync "buzz_to_feishu"）时也没有任何 pubkey→id 的对应（谁是谁不落盘，只有「本应用 open_id → union_id」这种 id 对）。
        双向（缺省，ADR-0020）要把离开频道的人移出群、把被拉回群的老成员认出来，只为**本频道的成员**（现在的和 30 天内的）记下 pubkey ↔ 飞书 id，
        见 L2-1-FGS-034b；频道外的人（OUTSIDER 有绑定、不在频道）照样不进 state。"""
        w = self.world()
        env, _ = RoundIdentity.run_round(self, w, env=Env(self.tmp, membership_sync="buzz_to_feishu"))
        raw = (env.state_dir / FGS.STATE_FILE).read_text()
        self.assertNotIn("@a4x.io", raw)
        for gone in ("open_ids", "misses", "people_digest", ALICE_PK, BOB_PK, OWNER_PK,
                     bridge_open_of(ALICE_OPEN), bridge_open_of(BOB_OPEN), bridge_open_of(OWNER_OPEN)):
            self.assertNotIn(gone, raw)
        self.assertEqual(len(w.api_requests), 1)
        env.round(w)
        env.round(w)
        self.assertEqual(len(w.api_requests), 3)

    def test_two_way_state_keeps_only_this_channels_members(self):
        """L2-1-FGS-034b: 双向（缺省）的 state 里，pubkey 只出现在成员快照（buzz_seen）和本频道见过的人（people_seen）里，而且只有本频道的成员；
        群成员快照（feishu_seen）只有飞书 id，没有 pubkey；邮箱和 bridge 应用的 open_id 照样没有；绑定关系照样每轮重取。"""
        w = self.world()
        w.users.add(OUTSIDER_OPEN)
        env, _ = RoundIdentity.run_round(self, w)
        env.round(w)
        state = env.state()
        raw = json.dumps(state)
        self.assertNotIn("@a4x.io", raw)
        for gone in (OUTSIDER_PK, bridge_open_of(ALICE_OPEN), bridge_open_of(OUTSIDER_OPEN)):
            self.assertNotIn(gone, raw)
        self.assertEqual(set(state["feishu_seen"].values()), {""})
        rest = {k: v for k, v in state.items() if k not in ("buzz_seen", "people_seen")}
        for pk in (ALICE_PK, BOB_PK, OWNER_PK):
            self.assertNotIn(pk, json.dumps(rest))
        self.assertIn(ALICE_PK, state["buzz_seen"])
        self.assertEqual(len(w.api_requests), 2)

    def test_blocked_bots_are_reported(self):
        """L2-1-FGS-035: 群已满且 Desk bot 进不去时整轮失败。"""
        w = self.world()
        w.bots.pop(AGENT_APP)
        for i in range(14):
            app = f"cli_foreign{i:08d}"
            w.bot_members[app] = f"ou_foreignbot{i:020d}"
            w.bots[app] = w.bot_members[app]
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk bot"):
            RoundIdentity.run_round(self, w)

    def test_mirror_must_be_a_bot_member(self):
        """L2-1-FGS-036: 镜像身份不是频道的 bot 成员时整轮拒绝。"""
        w = self.world()
        w.members = [m for m in w.members if m["pubkey"] != MIRROR_PK]
        with self.assertRaises(FGS.GroupSyncError):
            Env(self.tmp).round(w)

    def test_concurrent_round_is_refused(self):
        """L2-1-FGS-037: 同一 state 目录已有一轮在跑（持有锁）时，第二轮直接拒绝，不会重复发送。"""
        import fcntl
        w = self.world()
        env = Env(self.tmp)
        fd = os.open(env.state_dir / FGS.LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(FGS.GroupSyncError):
                env.round(w)
        self.assertEqual(w.lark_sends(), [])

    def test_state_dir_and_lock_must_be_owner_only(self):
        """L2-1-FGS-061: state 目录必须是本人所有、不是符号链接、组和其他人不可访问；锁文件以 0600 创建、不跟随符号链接。
        不满足时整轮拒绝，不读写 state、不发送。"""
        w = self.world()
        env = Env(self.tmp)
        env.round(w)
        self.assertEqual(os.stat(env.state_dir / FGS.LOCK_FILE).st_mode & 0o777, 0o600)
        os.chmod(env.state_dir, 0o777)
        sends = len(w.lark_sends())
        w.events.append(event(eid(120), ALICE_PK, "must not go out", created_at=ts(NOW) + 30))
        with self.assertRaises(FGS.GroupSyncError):
            env.round(w, now=NOW + timedelta(minutes=1))
        os.chmod(env.state_dir, 0o700)
        (env.state_dir / FGS.LOCK_FILE).unlink()
        (env.state_dir / FGS.LOCK_FILE).symlink_to(self.tmp / "elsewhere")
        with self.assertRaises(FGS.GroupSyncError):
            env.round(w, now=NOW + timedelta(minutes=1))
        (env.state_dir / FGS.LOCK_FILE).unlink()
        linked = self.tmp / "linked-state"
        linked.symlink_to(env.state_dir)
        with self.assertRaises(FGS.GroupSyncError):
            FGS.round_command(env.config, linked, base_env=env.base_env, runner=w, now=NOW + timedelta(minutes=1))
        self.assertEqual(len(w.lark_sends()), sends)
        self.assertFalse((self.tmp / "elsewhere").exists())

    def test_a_leaving_agent_bot_makes_room_for_a_new_one_in_the_same_round(self):
        """L2-1-FGS-064: 配置的 Desk 离开频道时拒绝切换，不能借另一个 Agent 的 bot。"""
        w = self.world()
        (self.tmp / "agent2-cfg").mkdir(mode=0o700)
        (self.tmp / "agent2-data").mkdir(mode=0o700)
        agent2_app = "cli_agent00000000002"
        w.agent_dirs[str(self.tmp / "agent2-cfg")] = agent2_app
        w.bot_members[agent2_app] = "ou_agent2bot000000000000000000001"
        for i in range(13):
            app = f"cli_foreign{i:08d}"
            w.bot_members[app] = f"ou_foreignbot{i:020d}"
            w.bots[app] = w.bot_members[app]
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER  # 15 bots: owner + 13 foreign + agent 1
        w.members = [m for m in w.members if m["pubkey"] != AGENT_PK]  # agent 1 left, agent 2 is a bot member
        env = Env(self.tmp)
        raw = json.loads(env.config.read_text())
        raw["agents"][AGENT2_PK] = {"app_id": agent2_app, "lark_config_dir": str(self.tmp / "agent2-cfg"),
                                    "lark_data_dir": str(self.tmp / "agent2-data")}
        write_owner_only(env.config, json.dumps(raw))
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            env.round(w)
        self.assertEqual(w.lark_sends(), [])

    def test_new_bot_is_blocked_not_failed_when_removals_are_withheld(self):
        """L2-1-FGS-065: 已指定的 Desk 离开 Buzz Channel 时，即使成员对账有待处理也拒绝发送。"""
        w = self.world()
        (self.tmp / "agent2-cfg").mkdir(mode=0o700)
        (self.tmp / "agent2-data").mkdir(mode=0o700)
        agent2_app = "cli_agent00000000002"
        w.agent_dirs[str(self.tmp / "agent2-cfg")] = agent2_app
        w.bot_members[agent2_app] = "ou_agent2bot000000000000000000001"
        for i in range(13):
            app = f"cli_foreign{i:08d}"
            w.bot_members[app] = f"ou_foreignbot{i:020d}"
            w.bots[app] = w.bot_members[app]
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.members = [m for m in w.members if m["pubkey"] not in (AGENT_PK, CAROL_PK)]
        w.users = {OWNER_OPEN, *{f"ou_extra{i:026d}" for i in range(11)}}
        env = Env(self.tmp)
        raw = json.loads(env.config.read_text())
        raw["agents"][AGENT2_PK] = {"app_id": agent2_app, "lark_config_dir": str(self.tmp / "agent2-cfg"),
                                    "lark_data_dir": str(self.tmp / "agent2-data")}
        write_owner_only(env.config, json.dumps(raw))
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            env.round(w)
        self.assertEqual(w.lark_sends(), [])


class RoundThreads(TmpCase):
    def base(self):
        w = FakeWorld(self.tmp)
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        return w

    def test_buzz_reply_to_an_old_feishu_message_opens_a_polled_thread(self):
        """L2-1-FGS-038: Buzz 上回复一条早已出了发现窗口（6 小时）的飞书消息：回复发进飞书话题后，这个话题会被轮询，之后话题里的回复回到 Buzz。"""
        w = self.base()
        w.messages = [fmsg("om_root", ALICE_OPEN, "question")]
        env = Env(self.tmp)
        env.round(w)
        root_event = w.mirrored()[0]["id"]
        later = NOW + timedelta(hours=7)
        env.round(w, now=later - timedelta(minutes=5))  # the cursor has long left the root behind
        w.events = [event(eid(70), BOB_PK, "answer", created_at=ts(later) - 10, tags=[("e", root_event, "", "reply")])]
        env.round(w, now=later)
        self.assertEqual([c["args"][c["args"].index("--message-id") + 1] for c in w.lark_sends()
                          if c["args"][1] == "+messages-reply"], ["om_root"])
        w.threads["om_root"].append(fmsg("om_followup", ALICE_OPEN, "thanks", thread_id="omt_om_root",
                                         when=later + timedelta(minutes=1)))
        env.round(w, now=later + timedelta(minutes=2))
        follow = [c for c in w.buzz_sends() if c["content"] == "[飞书] Alice：thanks"]
        self.assertEqual(len(follow), 1)
        self.assertEqual(follow[0]["args"][follow[0]["args"].index("--reply-to") + 1], root_event)

    def test_old_thread_reply_is_not_backfilled_later(self):
        """L2-1-FGS-039: 当时因发送者映射不到而跳过的话题回复，之后映射上了也不补发（新绑定不回填，跳过的也不回填）。"""
        w = self.base()
        del w.bindings[BOB_PK]
        w.messages = [fmsg("om_q", ALICE_OPEN, "q", thread_id="omt_om_q")]
        w.threads["om_q"] = [fmsg("om_bob_old", BOB_OPEN, "old answer", thread_id="omt_om_q")]
        env = Env(self.tmp, feishu_unmapped_senders="skip")
        first = env.round(w)
        self.assertEqual(first["skipped"].get("unmapped_sender"), 1)
        env.round(w, now=NOW + timedelta(minutes=10))
        w.bindings[BOB_PK] = BOB_OPEN
        env.round(w, now=NOW + timedelta(hours=2))
        self.assertFalse(any("old answer" in c["content"] for c in w.buzz_sends()))

    def test_several_active_threads_are_all_polled(self):
        """L2-1-FGS-040: 多个活跃话题都会被轮询（不只最新的一个）。"""
        w = self.base()
        w.messages = [fmsg("om_a", ALICE_OPEN, "a", thread_id="omt_om_a"), fmsg("om_b", ALICE_OPEN, "b", thread_id="omt_om_b")]
        w.threads = {"om_a": [fmsg("om_ra", BOB_OPEN, "ra", thread_id="omt_om_a")],
                     "om_b": [fmsg("om_rb", BOB_OPEN, "rb", thread_id="omt_om_b")]}
        Env(self.tmp).round(w)
        contents = [c["content"] for c in w.buzz_sends()]
        self.assertIn("[飞书] Bob：ra", contents)
        self.assertIn("[飞书] Bob：rb", contents)

    def test_new_thread_on_an_older_root_is_discovered(self):
        """L2-1-FGS-041: 有人在一小时前的消息上开了新话题：根消息早已出了游标窗口，但在发现窗口（6 小时）里，话题回复照样回到 Buzz。"""
        w = self.base()
        env = Env(self.tmp)
        env.round(w)
        t1 = NOW + timedelta(hours=1)
        w.messages = [fmsg("om_mid", ALICE_OPEN, "status?", when=t1)]
        env.round(w, now=t1 + timedelta(minutes=1))
        t3 = NOW + timedelta(hours=2)
        env.round(w, now=t3 - timedelta(minutes=5))
        for m in w.messages:
            m["thread_id"] = "omt_om_mid"
        w.threads["om_mid"] = [fmsg("om_late", BOB_OPEN, "late reply", thread_id="omt_om_mid", when=t3 - timedelta(minutes=1))]
        env.round(w, now=t3)
        root_event = next(e["id"] for e in w.mirrored() if "status?" in e["content"])
        late = [c for c in w.buzz_sends() if c["content"] == "[飞书] Bob：late reply"]
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["args"][late[0]["args"].index("--reply-to") + 1], root_event)


class RoundRetriesAndScale(TmpCase):
    def world(self):
        w = FakeWorld(self.tmp)
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        return w

    def test_refused_feishu_message_is_retried_until_delivered_at_normal_cadence(self):
        """L2-1-FGS-042: 飞书→Buzz 被确定拒绝两次、第三次成功：两次重试之间消息已经滑出游标窗口，也要送达一次。"""
        w = self.world()
        w.messages = [fmsg("om_retry", ALICE_OPEN, "please retry")]
        w.buzz_send_fail = ["bad_input", "bad_input"]
        env = Env(self.tmp)
        reports = [env.round(w, now=NOW + timedelta(minutes=2 * i)) for i in range(4)]
        self.assertEqual(len([e for e in w.mirrored() if "please retry" in e["content"]]), 1)
        self.assertEqual(sum(r["failed"] for r in reports), 0)

    def test_feishu_timeouts_are_retried_for_the_whole_idempotency_window_then_reported_once(self):
        """L2-1-FGS-043: Buzz→飞书连续超时 20 分钟后恢复，仍用同一幂等键送达一次；超过 50 分钟仍未成功时
        标为 unknown 终态并报告一次，之后不再重试。"""
        w = self.world()
        w.events = [event(eid(80), ALICE_PK, "slow feishu")]
        w.lark_send_fail = ["timeout"] * 20
        env = Env(self.tmp)
        for i in range(22):
            env.round(w, now=NOW + timedelta(minutes=i))
        self.assertEqual(len([m for m in w.messages if "slow feishu" in m["content"]]), 1)
        w2 = FakeWorld(self.tmp / "b")
        (self.tmp / "b").mkdir(mode=0o700)
        w2.members = [m for m in w2.members if m["pubkey"] != CAROL_PK]
        w2.agent_dirs = {str(self.tmp / "b" / "agent-cfg"): AGENT_APP}
        w2.events = [event(eid(81), ALICE_PK, "never")]
        w2.lark_send_fail = ["timeout"] * 100
        env2 = Env(self.tmp / "b")
        reports = [env2.round(w2, now=NOW + timedelta(minutes=5 * i)) for i in range(14)]
        expired_at = [i for i, r in enumerate(reports) if env2.state()["b2f"].get(eid(81)) == FGS.UNKNOWN]
        self.assertTrue(expired_at, [r["unknown"] for r in reports])
        first = expired_at[0]
        self.assertEqual(reports[first]["unknown"], 1)
        sends_after = len(w2.lark_sends())
        env2.round(w2, now=NOW + timedelta(minutes=5 * 15))
        self.assertEqual(len(w2.lark_sends()), sends_after)

    def test_busy_group_is_not_truncated_silently(self):
        """L2-1-FGS-044: 群里一分钟内有 600 条消息：全部镜像（不止前 500 条）；更多到一轮读不完时整轮报错而不是静默截断；
        owner 确认后用 --skip-backlog 放弃这段积压，之后的新消息照常镜像。"""
        w = self.world()
        w.messages = [fmsg(f"om_busy{i:04d}", ALICE_OPEN, f"busy {i}") for i in range(600)]
        env = Env(self.tmp)
        report = env.round(w)
        self.assertEqual(report["to_buzz"], 600)
        w.messages += [fmsg(f"om_flood{i:05d}", ALICE_OPEN, "flood", when=NOW + timedelta(minutes=1)) for i in range(2100)]
        with self.assertRaises(FGS.GroupSyncError):
            env.round(w, now=NOW + timedelta(minutes=2))
        report = env.round(w, now=NOW + timedelta(minutes=2), skip_backlog=True)
        self.assertEqual(report["backlog_skipped"], ["feishu"])
        self.assertEqual(report["to_buzz"], 0)
        w.messages.append(fmsg("om_after_flood", ALICE_OPEN, "after flood", when=NOW + timedelta(minutes=3)))
        self.assertEqual(env.round(w, now=NOW + timedelta(minutes=4))["to_buzz"], 1)

    def test_threads_beyond_the_hot_set_are_still_polled(self):
        """L2-1-FGS-045: 登记了 45 个话题时，最冷的那个也会轮到；它在两次轮询之间收到的回复，即使全局游标早已越过，也不会被滤掉。"""
        w = self.world()
        w.messages = [fmsg(f"om_t{i:02d}", ALICE_OPEN, f"topic {i}", thread_id=f"omt_om_t{i:02d}") for i in range(45)]
        w.threads = {f"om_t{i:02d}": [] for i in range(45)}
        env = Env(self.tmp)
        env.round(w)
        cold = "om_t44"
        w.threads[cold].append(fmsg("om_cold_reply", BOB_OPEN, "late in a cold thread", thread_id="omt_om_t44",
                                    when=NOW + timedelta(minutes=1)))
        for i in range(1, 7):
            env.round(w, now=NOW + timedelta(minutes=2 * i))
        got = [c for c in w.buzz_sends() if c["content"] == "[飞书] Bob：late in a cold thread"]
        self.assertEqual(len(got), 1)

    def test_agent_profile_failure_leaves_its_bot_alone(self):
        """L2-1-FGS-047: Desk profile 核对失败时整轮失败，成员对账和消息发送均无副作用。"""
        w = self.world()
        w.bots[AGENT_APP] = AGENT_BOT_MEMBER
        w.agent_dirs[str(self.tmp / "agent-cfg")] = "cli_somethingelse001"
        w.events = [event(eid(90), AGENT_PK, "agent says")]
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        env = Env(self.tmp)
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            env.round(w)
        self.assertIn(AGENT_APP, w.bots)
        self.assertEqual(w.lark_sends(), [])
        self.assertFalse([c for c in w.member_ops() if c["args"][1] in ("POST", "DELETE")])
        self.assertEqual(env.state()["binding"], "")

    def test_registered_agent_with_a_human_role_is_not_a_human(self):
        """L2-1-FGS-048: Desk 在频道里失去 bot 角色时整轮失败。"""
        w = self.world()
        for m in w.members:
            if m["pubkey"] == AGENT_PK:
                m["role"] = "member"
        w.events = [event(eid(91), AGENT_PK, "i am an agent")]
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            Env(self.tmp).round(w)
        self.assertEqual(w.lark_sends(), [])

    def test_one_failing_thread_does_not_stop_the_round(self):
        """L2-1-FGS-049: 某个话题持续报错只计 errors，其余话题与两个方向照常同步。"""
        w = self.world()
        w.messages = [fmsg("om_bad", ALICE_OPEN, "bad", thread_id="omt_om_bad"),
                      fmsg("om_good", ALICE_OPEN, "good", thread_id="omt_om_good")]
        w.threads = {"om_bad": [], "om_good": [fmsg("om_gr", BOB_OPEN, "good reply", thread_id="omt_om_good")]}
        w.thread_errors = {"om_bad"}
        w.events = [event(eid(92), ALICE_PK, "still flows")]
        report = Env(self.tmp).round(w)
        self.assertGreaterEqual(report["errors"], 1)
        self.assertIn("Alice（Buzz）：still flows", [text_of(c) for c in w.lark_sends()])
        self.assertIn("[飞书] Alice：good", [e["content"] for e in w.mirrored()])

    def test_a_failed_first_round_does_not_fix_the_binding_start(self):
        """L2-1-FGS-050: 首轮因身份不符失败时不定下绑定起点；修好配置后的第一轮才是起点，之前的消息不回填。"""
        w = self.world()
        w.profiles[OWNER_APP] = "ou_someoneelse000000000000000001"
        env = Env(self.tmp)
        with self.assertRaises(FGS.GroupSyncError):
            env.round(w)
        w.profiles[OWNER_APP] = OWNER_OPEN
        later = NOW + timedelta(hours=2)
        w.events = [event(eid(93), ALICE_PK, "between", created_at=ts(NOW) + 3600)]
        w.messages = [fmsg("om_between", ALICE_OPEN, "between", when=NOW + timedelta(hours=1))]
        env.round(w, now=later)
        self.assertEqual((w.lark_sends(), w.mirrored()), ([], []))

    def test_huge_buzz_backlog_refuses_unless_skipped_explicitly(self):
        """L2-1-FGS-051: Buzz 积压超过翻页上限时整轮拒绝（不静默跳过）；owner 确认后用 --skip-backlog 跳过这段积压，
        报告里标明，飞书方向照常。"""
        w = self.world()
        env = Env(self.tmp)
        env.round(w)
        later = NOW + timedelta(hours=1)
        w.events = [event(eid(2000 + i), ALICE_PK, f"x{i}", created_at=ts(later) - 800 + i) for i in range(401)]
        w.messages = [fmsg("om_after", ALICE_OPEN, "after", when=later)]
        old_max = FGS.BUZZ_PAGE_MAX
        FGS.BUZZ_PAGE_MAX = 2
        try:
            with self.assertRaises(FGS.GroupSyncError):
                env.round(w, now=later)
            report = env.round(w, now=later, skip_backlog=True)
            w.events.append(event(eid(3000), BOB_PK, "after the skip", created_at=ts(later) + 30))
            nxt = env.round(w, now=later + timedelta(minutes=1))  # the dropped backlog is not read again
        finally:
            FGS.BUZZ_PAGE_MAX = old_max
        self.assertEqual(nxt["backlog_skipped"], [])
        self.assertIn("Bob（Buzz）：after the skip", [text_of(c) for c in w.lark_sends()])
        self.assertFalse(any(text_of(c).startswith("Alice（Buzz）：x") for c in w.lark_sends()))
        self.assertEqual(report["backlog_skipped"], ["buzz"])
        self.assertTrue(FGS.needs_attention(report))
        self.assertIn("[飞书] Alice：after", [e["content"] for e in w.mirrored()])

    def test_unresolved_items_are_closed_out_after_six_hours(self):
        """L2-1-FGS-052: 结果未知的发送，如果之后再也读不回来（例如 Buzz 上的原事件被删了），6 小时后记为 unknown 并报告一次，
        不再让读取窗口一直往回拉。"""
        w = self.world()
        w.events = [event(eid(99), ALICE_PK, "vanishing")]
        w.lark_send_fail = ["timeout"]
        env = Env(self.tmp)
        env.round(w)
        w.events = []
        env.round(w, now=NOW + timedelta(hours=1))
        report = env.round(w, now=NOW + timedelta(hours=7))
        self.assertEqual(report["unknown"], 1)
        self.assertEqual(env.state()["b2f"][eid(99)], FGS.UNKNOWN)
        self.assertEqual(env.state()["unresolved"], {})
        later = env.round(w, now=NOW + timedelta(hours=8))
        self.assertEqual(later["unknown"], 0)
        gets = [c["args"] for c in w.buzz_calls if tuple(c["args"][:2]) == ("messages", "get")]
        self.assertEqual(int(gets[-1][gets[-1].index("--since") + 1]), ts(NOW) + 7 * 3600 - FGS.BUZZ_OVERLAP_SECONDS)

    def test_buzz_reply_does_not_move_the_thread_cursor_past_earlier_feishu_replies(self):
        """L2-1-FGS-053: 同一轮里 Buzz 侧回复登记了话题根，话题游标仍从上一轮算起：两轮之间飞书上已有的话题回复不会丢。"""
        w = self.world()
        w.messages = [fmsg("om_q", ALICE_OPEN, "question")]
        env = Env(self.tmp)
        env.round(w)
        root_event = w.mirrored()[0]["id"]
        w.threads["om_q"] = [fmsg("om_bob_early", BOB_OPEN, "early answer", thread_id="omt_om_q",
                                  when=NOW + timedelta(minutes=1))]
        for m in w.messages:
            if m["message_id"] == "om_q":
                m["thread_id"] = "omt_om_q"
        w.events = [event(eid(95), ALICE_PK, "thanks", created_at=ts(NOW) + 230, tags=[("e", root_event, "", "reply")])]
        env.round(w, now=NOW + timedelta(minutes=4))
        self.assertIn("[飞书] Bob：early answer", [c["content"] for c in w.buzz_sends()])

    def test_delivered_send_reported_as_network_error_is_not_resent_after_the_window(self):
        """L2-1-FGS-054: lark-cli 报 network 错误但消息其实已送达：按结果未知处理，窗口内只用同一键重试；
        之后一个多小时各轮都失败，恢复时已超出幂等窗口，不再重发（飞书群里不会出现第二条）。"""
        w = self.world()
        w.events = [event(eid(96), ALICE_PK, "delivered once")]
        w.lark_send_fail = ["network_envelope"]
        env = Env(self.tmp)
        first = env.round(w)
        self.assertEqual((first["unknown"], first["errors"]), (1, 0))
        w.members_list_error = True
        for minutes in range(5, 75, 5):
            with self.assertRaises(FGS.GroupSyncError):
                env.round(w, now=NOW + timedelta(minutes=minutes))
        w.members_list_error = False
        report = env.round(w, now=NOW + timedelta(minutes=75))
        self.assertEqual(len([m for m in w.messages if "delivered once" in m["content"]]), 1)
        self.assertEqual(env.state()["b2f"][eid(96)], FGS.UNKNOWN)
        self.assertEqual(report["unknown"], 1)

    def test_retry_keeps_the_first_send_mode(self):
        """L2-1-FGS-058: 首次按普通消息发出（父消息是没有飞书 bot 的 agent 发的，飞书里补不出话题根），结果未知；之后父消息在账本里
        有了对应，重试仍按普通消息、同一键发，不改成回复（飞书的幂等按接口计），群里只有一条。"""
        w = self.world()
        w.events = [event(eid(97), AGENT2_PK, "parent"),
                    event(eid(98), BOB_PK, "child", created_at=T0 + 1, tags=[("e", eid(97), "", "reply")])]
        w.lark_send_fail = ["timeout", "network_envelope"]
        env = Env(self.tmp, buzz_unmanaged_agents="skip")  # AGENT2 stands for a parent that is never mirrored (ADR-0019 relays by default)
        env.round(w)
        state = env.state()
        state["b2f"][eid(97)] = "om_parent_arrived_later"
        write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
        env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(len([m for m in w.messages if "child" in m["content"]]), 1)
        self.assertFalse(any("child" in r["content"] for replies in w.threads.values() for r in replies))

    def test_long_thread_new_replies_are_read_newest_first(self):
        """L2-1-FGS-055: 话题里已有 600 条回复时，新回复照样镜像（倒序读最新的 500 条，而不是最旧的 500 条）；
        新回复多到一次读不完时报错，不静默丢。"""
        w = self.world()
        w.messages = [fmsg("om_long", ALICE_OPEN, "long", thread_id="omt_om_long")]
        w.threads["om_long"] = [fmsg(f"om_old{i:04d}", BOB_OPEN, f"old {i}", thread_id="omt_om_long",
                                     when=NOW - timedelta(minutes=30)) for i in range(600)]
        env = Env(self.tmp)
        env.round(w)
        w.threads["om_long"].append(fmsg("om_newest", BOB_OPEN, "newest", thread_id="omt_om_long",
                                         when=NOW + timedelta(minutes=1)))
        env.round(w, now=NOW + timedelta(minutes=2))
        self.assertIn("[飞书] Bob：newest", [c["content"] for c in w.buzz_sends()])
        w.threads["om_long"] += [fmsg(f"om_burst{i:04d}", BOB_OPEN, "burst", thread_id="omt_om_long",
                                      when=NOW + timedelta(minutes=3)) for i in range(520)]
        report = env.round(w, now=NOW + timedelta(minutes=4))
        self.assertGreaterEqual(report["errors"], 1)

    def test_failing_threads_do_not_starve_the_rotation(self):
        """L2-1-FGS-056: 一批持续报错的话题不会一直占着轮换名额，其余冷话题照样轮到。"""
        w = self.world()
        w.messages = [fmsg(f"om_r{i:02d}", ALICE_OPEN, f"r{i}", thread_id=f"omt_om_r{i:02d}") for i in range(25)]
        w.threads = {f"om_r{i:02d}": [] for i in range(25)}
        w.thread_errors = {f"om_r{i:02d}" for i in range(10, 22)}
        env = Env(self.tmp)
        env.round(w)
        w.threads["om_r24"].append(fmsg("om_r24_reply", BOB_OPEN, "finally", thread_id="omt_om_r24",
                                        when=NOW + timedelta(minutes=1)))
        for i in range(1, 4):
            env.round(w, now=NOW + timedelta(minutes=2 * i))
        self.assertIn("[飞书] Bob：finally", [c["content"] for c in w.buzz_sends()])

    def test_retry_that_can_no_longer_be_routed_is_closed_at_once(self):
        """L2-1-FGS-057: 等待重试的消息，如果之后路由到「跳过」（发送者离开了频道），立即记 failed 并报告，读取窗口不再为它往回拉。"""
        w = self.world()
        w.messages = [fmsg("om_left", BOB_OPEN, "from bob")]
        w.buzz_send_fail = ["bad_input"]
        env = Env(self.tmp, feishu_unmapped_senders="skip")
        env.round(w)
        w.members = [m for m in w.members if m["pubkey"] != BOB_PK]
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(report["failed"], 1)
        self.assertEqual(env.state()["f2b"]["om_left"], FGS.FAILED)
        self.assertEqual(env.state()["f_unresolved"], {})

    def test_a_refusal_after_an_unknown_send_keeps_the_first_attempt_window(self):
        """L2-1-FGS-059: 首次发送结果未知（其实已送达），20 分钟后重试被限流；62 分钟后恢复时已超出从首次尝试算起的窗口，
        不再发送（记 failed），群里只有一条。"""
        w = self.world()
        w.events = [event(eid(110), ALICE_PK, "sent then refused")]
        w.lark_send_fail = ["network_envelope", "rate_limited"]
        env = Env(self.tmp)
        env.round(w)
        env.round(w, now=NOW + timedelta(minutes=20))
        sends = len(w.lark_sends())
        report = env.round(w, now=NOW + timedelta(minutes=62))
        self.assertEqual(len(w.lark_sends()), sends)
        self.assertEqual(len([m for m in w.messages if "sent then refused" in m["content"]]), 1)
        self.assertEqual((env.state()["b2f"][eid(110)], report["failed"]), (FGS.FAILED, 1))

    def test_open_buzz_send_that_can_no_longer_be_routed_is_closed_at_once(self):
        """L2-1-FGS-060: 结果未知、等待重试的 Buzz→飞书发送，如果作者随后离开了频道，下一轮立即记 unknown 并报告，不再挂着。"""
        w = self.world()
        w.events = [event(eid(111), ALICE_PK, "then alice left")]
        w.lark_send_fail = ["timeout"]
        env = Env(self.tmp)
        env.round(w)
        w.members = [m for m in w.members if m["pubkey"] != ALICE_PK]
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((env.state()["b2f"][eid(111)], report["unknown"]), (FGS.UNKNOWN, 1))
        self.assertEqual(env.state()["unresolved"], {})


class RoundPeopleApi(TmpCase):
    """每一轮向 bridge 取绑定关系：请求怎么签、谁能取、取不到时怎么办、绑定变了下一轮怎么变。"""

    def world(self):
        w = RoundIdentity.world(self)
        return w

    def test_people_come_from_the_signed_endpoint_and_nothing_else(self):
        """L2-1-FGS-070: 一轮恰好一次 GET，地址是 <origin>/bind/api/channels/<频道>/people，头是 owner 签的合法 NIP-98（bridge 那一侧真的验签）；成员据此按 open_id 进群；不再有任何邮箱或 contact 查询，也没有邮箱进 lark 的参数。"""
        w = self.world()
        env, report = RoundIdentity.run_round(self, w)
        self.assertEqual(len(w.api_requests), 1)
        req = w.api_requests[0]
        self.assertEqual(req["url"], API_URL)
        self.assertEqual(set(req["headers"]), {"Authorization", "Accept"})
        self.assertEqual(w._verify_nip98(req["headers"]["Authorization"], "GET", API_URL), OWNER_PK)
        self.assertLessEqual(req["timeout"], 30)
        self.assertLessEqual({ALICE_OPEN, BOB_OPEN}, w.users)
        self.assertNotIn(OUTSIDER_OPEN, w.users)  # 有绑定但不在频道里：接口不会给，也就不会被拉进群
        self.assertEqual(report["unmapped_members"], 1)  # Carol：频道成员，没有已验证的绑定
        self.assertEqual([c for c in w.lark_calls if c["args"][0] == "contact" and c["args"][1] != "v3"], [])
        blob = json.dumps(w.lark_calls) + json.dumps(report) + (env.state_dir / FGS.STATE_FILE).read_text()
        self.assertNotIn("@a4x.io", blob)

    def test_a_binding_that_changes_shows_in_the_next_round(self):
        """L2-1-FGS-071: 绑定会变，这正是不用静态文件的原因：Bob 解绑后下一轮他就映射不到（群里不删他：有人映射不到时不移人）；新同事绑定并进频道后，下一轮就被拉进群。"""
        w = self.world()
        # 「有人映射不到时不移人」这道闸只在单向对账里有用武之地：双向（ADR-0020）首轮之后本来就不清理「多余」成员
        env, _ = RoundIdentity.run_round(self, w, env=Env(self.tmp, membership_sync="buzz_to_feishu"))
        self.assertIn(BOB_OPEN, w.users)
        del w.bindings[BOB_PK]
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((report["unmapped_members"], report["removals_withheld"]), (2, "unmapped_members"))
        self.assertIn(BOB_OPEN, w.users)
        frank_pk, frank_open = "66" * 32, "ou_frank0000000000000000000000001"
        w.members.append({"pubkey": frank_pk, "role": "member"})
        w.display[frank_pk] = "Frank"
        w.bindings[frank_pk] = frank_open
        env.round(w, now=NOW + timedelta(minutes=2))
        self.assertIn(frank_open, w.users)
        self.assertEqual(len(w.api_requests), 3)

    def test_an_api_that_cannot_answer_fails_the_round_and_changes_nothing(self):
        """L2-1-FGS-072: 接口不可用（网络错误、超时、401、404、500、503、重定向、不是 JSON、频道对不上、key 或 open_id 格式不对、超大）时整轮报错：不发任何消息、不动群成员、state 不落盘；错误里没有响应正文。"""
        modes = ["network", "timeout", "401", "404", "500", "503", "redirect", "malformed", "wrong_channel", "bad_key",
                 "bad_open_id", "people_not_object", "no_people", "not_object", "oversize"]
        for mode in modes:
            with self.subTest(mode=mode):
                w = self.world()
                w.api_fail = [mode]
                env = Env(self.tmp)
                with self.assertRaises(FGS.GroupSyncError) as ctx:
                    env.round(w)
                self.assertNotIn("not_found", str(ctx.exception))
                self.assertNotIn("<html>", str(ctx.exception))
                self.assertEqual((w.lark_sends(), w.mirrored(), w.member_ops()), ([], [], []), mode)
                self.assertFalse((env.state_dir / FGS.STATE_FILE).exists(), mode)
                # 好了以后的下一轮照常工作
                env.round(w, now=NOW + timedelta(minutes=1))
                self.assertEqual(len(w.api_requests), 2, mode)
                self.assertIn(ALICE_OPEN, w.users, mode)
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)

    def test_the_signer_must_be_an_owner_or_admin_of_the_channel(self):
        """L2-1-FGS-073: 接口只认频道 owner / admin：签名者在频道里不是这两种角色时，脚本自己先报清楚的错、不发请求（而不是收到一个看不出原因的 404）；admin 可以。"""
        w = self.world()
        w.members = [dict(m, role="member") if m["pubkey"] == OWNER_PK else m for m in w.members]
        with self.assertRaises(FGS.GroupSyncError) as ctx:
            Env(self.tmp).round(w)
        self.assertIn("owner or admin", str(ctx.exception))
        self.assertEqual(w.api_requests, [])
        w2 = self.world()
        w2.members = [dict(m, role="admin") if m["pubkey"] == OWNER_PK else m for m in w2.members]
        self.assertEqual(Env(self.tmp).round(w2)["errors"], 0)
        self.assertEqual(len(w2.api_requests), 1)

    def test_the_owner_key_never_reaches_a_child_process_or_the_output(self):
        """L2-1-FGS-074: 签名用的 owner key 只在脚本进程内存里：不进 buzz 或 lark 子进程的环境，不进任何输出，也不进 state。"""
        w = self.world()
        env, report = RoundIdentity.run_round(self, w)
        for call in w.buzz_calls + w.lark_calls:
            self.assertNotIn(OWNER_KEY, json.dumps(call.get("env", {})))
            self.assertNotIn(OWNER_KEY, json.dumps(call.get("args", [])))
        self.assertNotIn(OWNER_KEY, json.dumps(report))
        self.assertNotIn(OWNER_KEY, (env.state_dir / FGS.STATE_FILE).read_text())
        for req in w.api_requests:
            self.assertNotIn(OWNER_KEY, json.dumps(req))

    def test_config_asks_for_an_https_origin_and_a_private_signer_file(self):
        """L2-1-FGS-075: people_api 必须恰好是 {base_url: https://主机[:端口]（没有路径、查询、userinfo、结尾斜杠）, signer_env_file: 绝对路径}；旧的 people_export 和 email_domain 不再被接受。"""
        env = Env(self.tmp)
        raw = json.loads(env.config.read_text())
        good_api = raw["people_api"]
        self.assertEqual(FGS.load_config(env.config)["people_api"], good_api)
        self.assertEqual(FGS.load_config(write_owner_only(self.tmp / "port.json", json.dumps(
            dict(raw, people_api=dict(good_api, base_url="https://bridge.example.test:8443")))))["people_api"]["base_url"],
            "https://bridge.example.test:8443")
        bad_apis = {
            "http": dict(good_api, base_url="http://bridge.example.test"),
            "path": dict(good_api, base_url="https://bridge.example.test/bind"),
            "trailing slash": dict(good_api, base_url="https://bridge.example.test/"),
            "port out of range": dict(good_api, base_url="https://bridge.example.test:99999"),
            "port zero": dict(good_api, base_url="https://bridge.example.test:0"),
            "port not a number": dict(good_api, base_url="https://bridge.example.test:abc"),
            "empty port": dict(good_api, base_url="https://bridge.example.test:"),
            "query": dict(good_api, base_url="https://bridge.example.test?x=1"),
            "userinfo": dict(good_api, base_url="https://admin@bridge.example.test"),
            "no host": dict(good_api, base_url="https://"),
            "not a string": dict(good_api, base_url=7),
            "relative signer file": dict(good_api, signer_env_file="owner.env"),
            "extra key": dict(good_api, token="x"),
            "missing base_url": {"signer_env_file": good_api["signer_env_file"]},
            "missing signer": {"base_url": good_api["base_url"]},
            "not an object": "https://bridge.example.test",
        }
        for name, api in bad_apis.items():
            bad = write_owner_only(self.tmp / "bad.json", json.dumps(dict(raw, people_api=api)))
            with self.assertRaises(FGS.GroupSyncError, msg=name):
                FGS.load_config(bad)
        for old in ({"people_export": "/x/people.json"}, {"email_domain": "a4x.io"}):
            with self.assertRaises(FGS.GroupSyncError, msg=str(old)):
                FGS.load_config(write_owner_only(self.tmp / "old.json", json.dumps(dict(raw, **old))))
        no_api = {k: v for k, v in raw.items() if k != "people_api"}
        with self.assertRaises(FGS.GroupSyncError):
            FGS.load_config(write_owner_only(self.tmp / "noapi.json", json.dumps(no_api)))


class IdentityContract(TmpCase):
    """人的身份映射模式（union_id 默认 / email 可选）的契约层：配置、bridge 应答、state 新字段。
    open_id 按飞书应用隔离，bridge 生产应用的 open_id 在 owner 个人应用里认不出人，所以本地要另有一条路。"""

    ANSWER = {"channel": CHANNEL, "as_of": "2026-09-19T12:00:00Z",
              "people": {ALICE_PK: bridge_open_of(ALICE_OPEN), BOB_PK: bridge_open_of(BOB_OPEN)},
              "union_ids": {ALICE_PK: union_of(ALICE_OPEN), BOB_PK: union_of(BOB_OPEN)},
              "emails": {ALICE_PK: ["alice@a4x.io"], BOB_PK: ["bob@a4x.io", "bob@example.com"]}}

    def parse(self, **changes):
        doc = dict(self.ANSWER, **changes)
        for key in [k for k, v in changes.items() if v is ...]:
            del doc[key]
        return FGS.parse_people_response(json.dumps(doc).encode(), CHANNEL)

    def test_people_answer_carries_the_three_id_kinds_and_tolerates_absent_optional_ones(self):
        """L1-FGS-100: bridge 的应答除了老的 people（bridge 应用的 open_id，本地认不出人）还有 union_ids 和 emails；
        缺席的 union_ids / emails 解析成 None（由所选模式决定缺了要不要紧），有就原样给出。"""
        got = self.parse()
        self.assertEqual(got.open_ids, self.ANSWER["people"])
        self.assertEqual(got.union_ids, self.ANSWER["union_ids"])
        self.assertEqual(got.emails, self.ANSWER["emails"])
        no_union = self.parse(union_ids=...)
        self.assertEqual((no_union.union_ids, no_union.emails), (None, self.ANSWER["emails"]))
        no_emails = self.parse(emails=...)
        self.assertEqual((no_emails.union_ids, no_emails.emails), (self.ANSWER["union_ids"], None))
        empty = self.parse(union_ids={}, emails={})
        self.assertEqual((empty.union_ids, empty.emails), ({}, {}))

    def test_people_answer_new_fields_are_parsed_strictly_and_never_quoted(self):
        """L1-FGS-101: union_ids 的值必须是 on_ 开头的字符串；emails 的值必须是非空的字符串列表，每个地址形状合法（一个 @、
        没有空白和括号引号、小写、不超长）；两者的 key 都必须是 64 位小写 hex。哪怕所选模式用不到它，格式不对也整轮拒绝；
        错误信息里没有邮箱、union_id 或 open_id 的值。"""
        bad = {
            "union_ids not an object": {"union_ids": [ALICE_PK]},
            "union_ids null": {"union_ids": None},
            "union key not hex": {"union_ids": {"XYZ": union_of(ALICE_OPEN)}},
            "union key upper case": {"union_ids": {("AB" * 32): union_of(ALICE_OPEN)}},
            "union value is an open_id": {"union_ids": {ALICE_PK: ALICE_OPEN}},
            "union value is an email": {"union_ids": {ALICE_PK: "alice@a4x.io"}},
            "union value empty": {"union_ids": {ALICE_PK: ""}},
            "union value not a string": {"union_ids": {ALICE_PK: 7}},
            "union value is bare prefix": {"union_ids": {ALICE_PK: "on_"}},
            "emails not an object": {"emails": [ALICE_PK]},
            "emails null": {"emails": None},
            "emails key not hex": {"emails": {"XYZ": ["a@a4x.io"]}},
            "emails value is a string": {"emails": {ALICE_PK: "alice@a4x.io"}},
            "emails value is an empty list": {"emails": {ALICE_PK: []}},
            "emails value has a non string": {"emails": {ALICE_PK: ["alice@a4x.io", 7]}},
            "emails value has an empty string": {"emails": {ALICE_PK: ["alice@a4x.io", ""]}},
            "email without at": {"emails": {ALICE_PK: ["alice.a4x.io"]}},
            "email with two ats": {"emails": {ALICE_PK: ["a@b@a4x.io"]}},
            "email with a space": {"emails": {ALICE_PK: ["alice smith@a4x.io"]}},
            "email with angle brackets": {"emails": {ALICE_PK: ["<alice@a4x.io>"]}},
            "email with a newline": {"emails": {ALICE_PK: ["alice@a4x.io\n"]}},
            "email without local part": {"emails": {ALICE_PK: ["@a4x.io"]}},
            "email without host": {"emails": {ALICE_PK: ["alice@"]}},
            "email too long": {"emails": {ALICE_PK: ["a" * 250 + "@a4x.io"]}},
            "too many emails for one person": {"emails": {ALICE_PK: [f"a{i}@a4x.io" for i in range(21)]}},
        }
        for name, change in bad.items():
            with self.assertRaises(FGS.GroupSyncError, msg=name) as ctx:
                self.parse(**change)
            for secret in ("alice@a4x.io", "a4x.io", union_of(ALICE_OPEN), ALICE_OPEN, bridge_open_of(ALICE_OPEN)):
                self.assertNotIn(secret, str(ctx.exception), name)

    def test_addresses_the_contract_says_are_lower_case_are_tolerated_and_normalised(self):
        """L1-FGS-112: 契约说 emails 是小写、去重、排序的，但大小写不同、首尾有空格的地址不算畸形（取得到的还是同一个邮箱，
        gitlab_buzz_people_generate 对同一个应答也是宽松处理）：解析后给出的是规范形式（去掉首尾空格、转小写）；首尾的制表符 / 换行
        这类控制字符和中间的空白仍然是畸形。"""
        got = self.parse(emails={ALICE_PK: [" Alice@A4X.io "], BOB_PK: ["Bob@A4x.IO", "bob@example.com"]})
        self.assertEqual(got.emails, {ALICE_PK: ["alice@a4x.io"], BOB_PK: ["bob@a4x.io", "bob@example.com"]})
        for bad in ("\talice@a4x.io", "alice@a4x.io\n", "alice @a4x.io", "  "):
            with self.assertRaises(FGS.GroupSyncError, msg=repr(bad)):
                self.parse(emails={ALICE_PK: [bad]})

    def test_identity_is_union_id_unless_the_config_says_email(self):
        """L1-FGS-102: 配置的 identity 可选，缺省是 union_id；只认 "union_id" 和 "email"，别的值（含大小写不同、空串、null、
        非字符串）一律拒绝，不会悄悄退回默认。"""
        env = Env(self.tmp)
        raw = json.loads(env.config.read_text())
        self.assertNotIn("identity", raw)
        self.assertEqual(FGS.identity_mode(FGS.load_config(env.config)), "union_id")
        for value in ("union_id", "email"):
            path = write_owner_only(self.tmp / f"{value}.json", json.dumps(dict(raw, identity=value)))
            self.assertEqual(FGS.identity_mode(FGS.load_config(path)), value)
        for value in ("open_id", "Email", "UNION_ID", "", None, 5, ["email"], {"email": True}, True):
            path = write_owner_only(self.tmp / "bad.json", json.dumps(dict(raw, identity=value)))
            with self.assertRaises(FGS.GroupSyncError, msg=repr(value)) as ctx:
                FGS.load_config(path)
            self.assertIn("identity", str(ctx.exception))

    def test_state_has_two_id_caches_and_old_states_still_load(self):
        """L1-FGS-103: state 新增两个只存 id 的缓存：idmap（本应用 open_id → union_id）与 emailmap（sha256(应用 id + 邮箱) → open_id，
        或 miss:<时间>）。升级前写的 state（连 r2f 都没有的、或有 r2f 没有这两个字段的）照样能读，缓存为空；格式不对、
        只缺一个、多出未知字段都按「关闭失败」拒绝。"""
        self.assertEqual((FGS.State().idmap, FGS.State().emailmap), ({}, {}))
        digest = hashlib.sha256(b"cli_x\0a@a4x.io").hexdigest()
        state = FGS.State(binding="b", idmap={ALICE_OPEN: union_of(ALICE_OPEN)},
                          emailmap={digest: BOB_OPEN, "ab" * 32: "miss:1700000000"})
        FGS.save_state(self.tmp, state)
        path = self.tmp / FGS.STATE_FILE
        self.assertEqual(FGS.load_state(self.tmp), state)
        full = json.loads(path.read_text())
        self.assertNotIn("a@a4x.io", path.read_text())
        after_reactions = {k: v for k, v in full.items() if k not in ("idmap", "emailmap")}
        write_owner_only(path, json.dumps(after_reactions))
        loaded = FGS.load_state(self.tmp)
        self.assertEqual((loaded.idmap, loaded.emailmap, loaded.r2f), ({}, {}, {}))
        before_reactions = {k: v for k, v in after_reactions.items() if k not in ("r2f", "react_since")}
        write_owner_only(path, json.dumps(before_reactions))
        loaded = FGS.load_state(self.tmp)
        self.assertEqual((loaded.idmap, loaded.emailmap), ({}, {}))
        bad = {
            "only idmap": {k: v for k, v in full.items() if k != "emailmap"},
            "only emailmap": {k: v for k, v in full.items() if k != "idmap"},
            "idmap is a list": dict(full, idmap=[]),
            "idmap value not a string": dict(full, idmap={ALICE_OPEN: 5}),
            "idmap key is not an open_id": dict(full, idmap={"alice": union_of(ALICE_OPEN)}),
            "idmap value is not a union_id": dict(full, idmap={ALICE_OPEN: BOB_OPEN}),
            "emailmap is a list": dict(full, emailmap=[]),
            "emailmap key is an email": dict(full, emailmap={"a@a4x.io": ALICE_OPEN}),
            "emailmap value is an email": dict(full, emailmap={digest: "a@a4x.io"}),
            "emailmap value is a union_id": dict(full, emailmap={digest: union_of(ALICE_OPEN)}),
            "emailmap miss without a time": dict(full, emailmap={digest: "miss:soon"}),
            "unknown field": dict(full, surprise=1),
        }
        for name, data in bad.items():
            write_owner_only(path, json.dumps(data))
            with self.assertRaises(FGS.GroupSyncError, msg=name):
                FGS.load_state(self.tmp)

    def test_id_caches_are_bounded_and_the_report_counts_identity_conflicts(self):
        """L1-FGS-104: 两个缓存都只留最近写入的 IDMAP_KEEP 条；报告多一个 identity_conflicts 计数（两个身份来源互相矛盾的次数），
        非零就需要关注（退出码 3），和 errors 一样要人看一眼。"""
        opens = [f"ou_{i:032x}" for i in range(FGS.IDMAP_KEEP + 3)]
        s = FGS.State(idmap={o: union_of(o) for o in opens},
                      emailmap={hashlib.sha256(o.encode()).hexdigest(): o for o in opens})
        FGS.prune_state(s)
        self.assertEqual((len(s.idmap), len(s.emailmap)), (FGS.IDMAP_KEEP, FGS.IDMAP_KEEP))
        self.assertNotIn(opens[0], s.idmap)
        self.assertIn(opens[-1], s.idmap)
        report = FGS._new_report()
        self.assertEqual(report["identity_conflicts"], 0)
        self.assertEqual(set(report), {  # 文档里列出的全部字段：多一个或少一个都要改文档
            "added_users", "removed_users", "added_bots", "removed_bots", "blocked_bots", "member_failures", "removals_withheld",
            "unmapped_members", "identity_conflicts", "backlog_skipped", "to_feishu", "to_buzz", "messages_updated",
            "unknown", "failed", "errors",
            "skipped", "reactions_added", "reactions_removed", "reactions_failed",
            "images_to_feishu", "images_to_buzz", "images_failed", "images_skipped", "thread_roots_backfilled",
            "thread_root_unavailable", "thread_root_failed", "thread_roots_deferred", "cards_sent", "cards_fallback_text",
            "relayed_agents", "directory_agents", "directory_failed", "directory_conflicts",
            "members_to_buzz", "members_removed_from_buzz", "members_refused", "members_protected", "members_unresolved",
            "people_cache_failed", "member_events_blocked", "reactions_to_buzz", "reactions_withdrawn_in_buzz",
            "approvals_to_buzz", "agent_intros_sent", "agent_intros_relayed", "agent_intro_failures",
            "agent_intro_unknown", "agent_intro_stopped"})
        self.assertFalse(FGS.needs_attention(report))
        self.assertTrue(FGS.needs_attention(dict(report, identity_conflicts=1)))


class PairingUnits(unittest.TestCase):
    """union 模式认人的纯函数：同一条消息取两次（列表里的 open_id、单条视图里的 union_id），只按 mentions[].key 配对。"""

    U = {o: union_of(o) for o in (ALICE_OPEN, BOB_OPEN, CAROL_OPEN)}

    @staticmethod
    def row(sender=ALICE_OPEN, *, sender_type="user", mentions=(), mid="om_x1"):
        return {"message_id": mid, "sender": {"id": sender, "id_type": "open_id", "sender_type": sender_type},
                "mentions": [{"id": i, "key": k, "name": "n"} for i, k in mentions]}

    @staticmethod
    def view(sender=None, *, mentions=(), mid="om_x1", id_type="union_id", sender_type="user", mention_type="union_id"):
        return {"message_id": mid, "sender": {"id": sender or union_of(ALICE_OPEN), "id_type": id_type, "sender_type": sender_type},
                "mentions": [{"id": i, "key": k, "id_type": mention_type, "name": "n"} for i, k in mentions]}

    def test_only_the_message_itself_pairs_an_open_id_with_a_union_id(self):
        """L1-FGS-107: 发信人与视图里的发信人配对，被 @ 的人与视图里 key 相同的 mention 配对：视图里顺序不同照样对；bot、@所有人（id 不是
        ou_）不是人；视图缺失、是别的消息、发信人或 mention 的 id_type 不是 union_id、id 不是 on_、发信人类型不是 user、key 为空 / 在任一边
        不唯一 / 只在一边有 → 配不上（绝不按位置或名字凑）；发信人本身不是 user（行里）也不配。"""
        A, B, C = (union_of(o) for o in (ALICE_OPEN, BOB_OPEN, CAROL_OPEN))
        bot = "ou_botbot0000000000000000000000001"
        row = self.row(mentions=[(BOB_OPEN, "@_user_1"), (bot, "@_user_2"), ("all", "@_all"), (CAROL_OPEN, "@_user_3")])
        good = self.view(mentions=[(C, "@_user_3"), (B, "@_user_1"), ("on_00000000000000000000000000000009", "@_user_2")])
        self.assertEqual(FGS.pair_ids(row, good, {bot}), ({ALICE_OPEN: A, BOB_OPEN: B, CAROL_OPEN: C}, set()))
        one = self.row(mentions=[(BOB_OPEN, "@_user_1")])
        cases = {
            "no view": (one, None, {}),
            "another message": (one, self.view(mentions=[(B, "@_user_1")], mid="om_other"), {}),
            "sender id type": (one, self.view(mentions=[(B, "@_user_1")], id_type="open_id"), {BOB_OPEN: B}),
            "mention id type": (one, self.view(mentions=[(B, "@_user_1")], mention_type="open_id"), {ALICE_OPEN: A}),
            "sender not a union id": (one, self.view(ALICE_OPEN, mentions=[(B, "@_user_1")]), {BOB_OPEN: B}),
            "mention not a union id": (one, self.view(mentions=[(BOB_OPEN, "@_user_1")]), {ALICE_OPEN: A}),
            "view sender is an app": (one, self.view(mentions=[(B, "@_user_1")], sender_type="app"), {BOB_OPEN: B}),
            "row sender is an app": (self.row(sender_type="app", mentions=[(BOB_OPEN, "@_user_1")]), self.view(mentions=[(B, "@_user_1")]), {BOB_OPEN: B}),
            "row sender is not an open id": (self.row("alice", mentions=[(BOB_OPEN, "@_user_1")]), self.view(mentions=[(B, "@_user_1")]), {BOB_OPEN: B}),
            "row sender is not an object": (dict(one, sender="alice"), self.view(mentions=[(B, "@_user_1")]), {BOB_OPEN: B}),
            "key only in the row": (one, self.view(mentions=[(B, "@_user_9")]), {ALICE_OPEN: A}),
            "key only in the view": (self.row(), self.view(mentions=[(B, "@_user_1")]), {ALICE_OPEN: A}),
            "key twice in the row": (self.row(mentions=[(BOB_OPEN, "@_user_1"), (CAROL_OPEN, "@_user_1")]),
                                     self.view(mentions=[(B, "@_user_1")]), {ALICE_OPEN: A}),
            "key twice in the view": (one, self.view(mentions=[(B, "@_user_1"), (C, "@_user_1")]), {ALICE_OPEN: A}),
            "empty key": (self.row(mentions=[(BOB_OPEN, "")]), self.view(mentions=[(B, "")]), {ALICE_OPEN: A}),
            "positions do not count": (self.row(mentions=[(BOB_OPEN, "@_user_1")]), self.view(mentions=[(C, "@_user_7"), (B, "@_user_8")]), {ALICE_OPEN: A}),
        }
        for name, (row_, view_, want) in cases.items():
            self.assertEqual(FGS.pair_ids(row_, view_, set()), (want, set()), name)

    def test_an_answer_that_contradicts_itself_vouches_for_nobody_it_contradicts(self):
        """L1-FGS-108: 同一个人在答案里两个 union_id（发信人自己又被 @，两处不同），或两个不同的 open_id 对着同一个 union_id：
        这些人都算「答案自相矛盾」，不出现在配对结果里，单独返回。"""
        A, B = union_of(ALICE_OPEN), union_of(BOB_OPEN)
        row = self.row(mentions=[(ALICE_OPEN, "@_user_1")])
        self.assertEqual(FGS.pair_ids(row, self.view(mentions=[(B, "@_user_1")]), set()), ({}, {ALICE_OPEN}))
        row = self.row(mentions=[(BOB_OPEN, "@_user_1")])
        self.assertEqual(FGS.pair_ids(row, self.view(mentions=[(A, "@_user_1")]), set()), ({}, {ALICE_OPEN, BOB_OPEN}))
        row = self.row(mentions=[(ALICE_OPEN, "@_user_1")])
        self.assertEqual(FGS.pair_ids(row, self.view(mentions=[(A, "@_user_1")]), set()), ({ALICE_OPEN: A}, set()))  # 一致就不矛盾

    def test_the_resolver_asks_feishu_at_most_once_and_only_about_people(self):
        """L1-FGS-109: 缓存里有的直接答，不调飞书；机器人、@所有人、格式不对的 id 不是人，答 ""，不为它们调飞书；要查时每条消息只调一次
        （发信人和被 @ 的人一起配对）；调用失败标 error、什么都不缓存、也不重试第二次。"""
        calls = []

        class Owner:
            def message_view(self_, mid, id_type):
                calls.append((mid, id_type))
                if fail_next:
                    raise FGS.CliError("message get", 1, "network", definite=False)
                return PairingUnits.view(mentions=[(union_of(BOB_OPEN), "@_user_1")])
        fail_next = False
        bot = "ou_botbot0000000000000000000000001"
        row = self.row(mentions=[(BOB_OPEN, "@_user_1"), (bot, "@_user_2"), ("all", "@_all")])
        state = FGS.State(idmap={CAROL_OPEN: union_of(CAROL_OPEN)})
        report = FGS._new_report()
        resolver = FGS.IdResolver(Owner(), state, report, row, {bot})
        self.assertEqual(resolver(CAROL_OPEN), union_of(CAROL_OPEN))
        for not_a_person in (bot, "all", "alice", ""):
            self.assertEqual(resolver(not_a_person), "")
        self.assertEqual(calls, [])
        self.assertEqual(resolver(ALICE_OPEN), union_of(ALICE_OPEN))
        self.assertEqual(resolver(BOB_OPEN), union_of(BOB_OPEN))
        self.assertEqual(calls, [("om_x1", "union_id")])
        self.assertEqual(state.idmap, {CAROL_OPEN: union_of(CAROL_OPEN), ALICE_OPEN: union_of(ALICE_OPEN), BOB_OPEN: union_of(BOB_OPEN)})
        self.assertEqual((resolver.error, resolver.conflict, resolver.unpaired), (False, False, set()))
        calls.clear()
        fail_next = True
        state2 = FGS.State()
        broken = FGS.IdResolver(Owner(), state2, FGS._new_report(), row, {bot})
        self.assertEqual((broken(ALICE_OPEN), broken(BOB_OPEN)), ("", ""))
        self.assertEqual((broken.error, state2.idmap, len(calls)), (True, {}, 1))

    def test_email_shape_has_a_length_limit_and_no_control_characters(self):
        """L1-FGS-110: 整个邮箱不超过 254 个字符（本地部分与域名各自的上限加起来会更长）；控制字符（NUL、退格、DEL……）不合法；
        刚好 254 的合法。"""
        limit = "a" * 64 + "@" + "b" * (FGS.EMAIL_MAX_LENGTH - 65)
        self.assertEqual(len(limit), FGS.EMAIL_MAX_LENGTH)
        base = {"channel": CHANNEL, "people": {}, "emails": {ALICE_PK: [limit]}}
        self.assertEqual(FGS.parse_people_response(json.dumps(base).encode(), CHANNEL).emails, {ALICE_PK: [limit]})
        for bad in (limit + "b", "a" * 64 + "@" + "b" * 200 + ".io", "ali\x00ce@a4x.io", "ali\x08ce@a4x.io", "ali\x1bce@a4x.io",
                    "ali\x7fce@a4x.io", "alice@a4\x01x.io"):
            with self.assertRaises(FGS.GroupSyncError, msg=repr(bad)):
                FGS.parse_people_response(json.dumps(dict(base, emails={ALICE_PK: [bad]})).encode(), CHANNEL)


class EmailLookup(TmpCase):
    """email 模式：邮箱经 contact +search-user 换成本应用的 open_id。这个搜索是模糊的，所以只认「邮箱逐字相等」。"""

    def test_only_a_verbatim_address_of_one_account_identifies_anyone(self):
        """L1-FGS-105: 搜索结果里，只有 email 或 enterprise_email 与查询值逐字相等、且恰好指向一个 open_id 的账号才算；
        前缀 / 后缀 / 子串、大小写不同、带空白、外部租户的账号、open_id 格式不对、不是对象的行都不算；两个不同账号都相等（歧义）不算。"""
        pick = FGS.pick_open_id
        A, B = "ou_aaaa0000000000000000000000000001", "ou_bbbb0000000000000000000000000001"
        want = "alice@a4x.io"
        self.assertEqual(pick(want, [{"open_id": A, "email": want, "enterprise_email": ""}]), A)
        self.assertEqual(pick(want, [{"open_id": A, "email": "", "enterprise_email": want}]), A)
        self.assertEqual(pick(want, [{"open_id": A, "email": want, "enterprise_email": want}]), A)  # 同一个账号两处都相等
        self.assertEqual(pick(want, [{"open_id": B, "email": "xalice@a4x.io"}, {"open_id": A, "email": want}]), A)
        self.assertEqual(pick(want, [{"open_id": A, "email": want}, {"open_id": A, "enterprise_email": want}]), A)
        for name, rows in {
            "empty": [],
            "prefix": [{"open_id": A, "email": "xalice@a4x.io"}],
            "suffix": [{"open_id": A, "enterprise_email": "alice@a4x.io.cn"}],
            "case": [{"open_id": A, "email": "Alice@a4x.io"}],
            "space": [{"open_id": A, "email": " alice@a4x.io"}, {"open_id": A, "enterprise_email": "alice@a4x.io\n"}],
            "cross tenant": [{"open_id": A, "email": want, "is_cross_tenant": True}],
            "open_id malformed": [{"open_id": "alice", "email": want}, {"open_id": None, "email": want}, {"email": want}],
            "not an object": ["alice@a4x.io", None, 7, [want]],
            "two accounts": [{"open_id": A, "email": want}, {"open_id": B, "enterprise_email": want}],
        }.items():
            self.assertIsNone(pick(want, rows), name)
        self.assertIsNone(pick("", [{"open_id": A, "email": ""}]))

    def test_the_search_uses_the_owner_user_token_and_never_echoes_the_address(self):
        """L1-FGS-106: search_user 是 `contact +search-user --as user --query <邮箱> --exclude-external-users`（json 输出），返回 users 列表；
        出错抛 CliError，错误信息里没有邮箱（argv 和服务端的话都不回显）；users 不是列表当成没有。"""
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {"users": [{"open_id": ALICE_OPEN}, 7]}}), "")
        cli = FGS.LarkCli(LARK_CLI, {}, runner=runner)
        self.assertEqual(cli.search_user("alice@a4x.io"), [{"open_id": ALICE_OPEN}])
        self.assertEqual(calls[0], [LARK_CLI, "contact", "+search-user", "--as", "user", "--query", "alice@a4x.io",
                                    "--exclude-external-users", "--format", "json"])

        def broken(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", json.dumps(
                {"ok": False, "error": {"type": "api", "code": 99991400, "message": "no such user alice@a4x.io"}}))
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.LarkCli(LARK_CLI, {}, runner=broken).search_user("alice@a4x.io")
        self.assertNotIn("alice", str(ctx.exception))

        def odd(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {"users": "none"}}), "")
        self.assertEqual(FGS.LarkCli(LARK_CLI, {}, runner=odd).search_user("alice@a4x.io"), [])


class IdentityPeopleAnswer(TmpCase):
    """所选模式需要的字段 bridge 没给：整轮中止、不写 state、不动群，并说清是 bridge 那边缺什么。"""

    def world(self):
        return RoundIdentity.world(self)

    def attempt(self, w, env):
        with self.assertRaises(FGS.GroupSyncError) as ctx:
            env.round(w)
        self.assertEqual((w.lark_sends(), w.mirrored(), w.member_ops()), ([], [], []))
        self.assertFalse((env.state_dir / FGS.STATE_FILE).exists())
        return str(ctx.exception)

    def test_union_mode_needs_union_ids_and_says_what_the_bridge_lacks(self):
        """L2-1-FGS-110: 默认（union_id）模式下，应答没有 union_ids（bridge 还没部署 union_id 回填）整轮中止：不发消息、不动群成员、
        state 不落盘；错误里点明缺 union_ids；bridge 补上以后的下一轮照常工作。缺 emails 无所谓。"""
        w = self.world()
        w.bridge_omit = {"union_ids"}
        env = Env(self.tmp)
        self.assertIn("union_ids", self.attempt(w, env))
        w.bridge_omit = {"emails"}
        self.assertEqual(env.round(w, now=NOW + timedelta(minutes=1))["errors"], 0)
        self.assertTrue(w.lark_sends())

    def test_email_mode_needs_emails_and_names_the_bridge_switch(self):
        """L2-1-FGS-111: email 模式下，应答没有 emails 整轮中止，错误明说「bridge 没开 CHANNEL_PEOPLE_EMAILS_ENABLED」；
        不动群、不写 state。缺 union_ids 无所谓（老 bridge 也能跑 email 模式）。"""
        w = self.world()
        w.bridge_omit = {"emails"}
        env = Env(self.tmp, identity="email")
        message = self.attempt(w, env)
        self.assertIn("CHANNEL_PEOPLE_EMAILS_ENABLED", message)
        self.assertNotIn("@a4x.io", message)
        w.bridge_omit = {"union_ids"}
        self.assertEqual(env.round(w, now=NOW + timedelta(minutes=1))["errors"], 0)
        self.assertTrue(w.lark_sends())

    def test_malformed_new_fields_end_the_round_in_either_mode(self):
        """L2-1-FGS-112: union_ids / emails 格式不对（不是对象、key 不是 64 位小写 hex、union_id 不是 on_、邮箱列表为空或形状不对）
        在两种模式下都整轮中止、不落盘、不动群，好了以后的下一轮照常工作；错误里没有响应正文。"""
        modes = ["union_not_object", "union_bad_key", "union_bad_value", "union_value_not_string", "emails_not_object",
                 "emails_bad_key", "emails_value_not_list", "emails_empty_list", "emails_bad_address", "emails_null"]
        for identity in ("default", "email"):  # 不写 identity 就是 union_id
            for mode in modes:
                with self.subTest(identity=identity, mode=mode):
                    w = self.world()
                    w.api_fail = [mode]
                    env = Env(self.tmp, **({} if identity == "default" else {"identity": identity}))
                    message = self.attempt(w, env)
                    for secret in ("@a4x.io", union_of(ALICE_OPEN), ALICE_OPEN, bridge_open_of(ALICE_OPEN)):
                        self.assertNotIn(secret, message)
                    env.round(w, now=NOW + timedelta(minutes=1))
                    self.assertEqual(len(w.api_requests), 2)
                    (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)


class UnionMode(TmpCase):
    """union_id 模式（默认）：飞书里同一个人在 bridge 应用、owner 应用里的 open_id 各不相同，只有 union_id 是共通的。
    FakeWorld 里每个人有三个 id；拿 bridge 的 open_id 当本应用的 open_id 用，飞书那边就不认得这个人。"""

    def world(self):
        return RoundIdentity.world(self)

    def go(self, w, env=None, **kw):
        env = env or Env(self.tmp, feishu_unmapped_senders="skip")
        return env, env.round(w, **kw)

    @staticmethod
    def params(call):
        return json.loads(call["args"][call["args"].index("--params") + 1])

    def test_members_are_listed_and_changed_by_union_id_and_the_bridge_open_ids_never_appear(self):
        """L2-1-FGS-113: 成员对账全在 union_id 空间里：用户列表带 --member-id-type union_id、只要用户（bot 另用 open_id 单独列，
        拉 bot 靠的是 app_id）；加人、移人的 member_id_type 是 union_id，id 是 bridge 给的 union_ids；多余的人被移出、缺的被拉进来；
        bridge 应用的 open_id 一个字节也不进任何 lark 调用。"""
        w = self.world()
        w.bindings[CAROL_PK] = CAROL_OPEN  # 人人有映射，所以能移人
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        env, report = self.go(w)
        self.assertEqual(w.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN, CAROL_OPEN})
        self.assertEqual((report["added_users"], report["removed_users"], report["unmapped_members"]), (3, 1, 0))
        lists = w.member_lists()
        self.assertTrue(lists)
        for call in lists:
            args = call["args"]
            kind = args[args.index("--member-types") + 1]
            self.assertEqual(args[args.index("--member-id-type") + 1], {"user": "union_id", "bot": "open_id"}[kind])
        user_ops = {(c["args"][1], self.params(c)["member_id_type"]): json.loads(c["args"][c["args"].index("--data") + 1])["id_list"]
                    for c in w.member_ops() if self.params(c)["member_id_type"] != "app_id"}
        self.assertEqual(set(user_ops), {("DELETE", "union_id"), ("POST", "union_id")})
        self.assertEqual(user_ops[("DELETE", "union_id")], [union_of(EXTRA_OPEN)])
        self.assertEqual(sorted(user_ops[("POST", "union_id")]), sorted(union_of(o) for o in (ALICE_OPEN, BOB_OPEN, CAROL_OPEN)))
        blob = json.dumps(w.lark_calls) + json.dumps(report)
        for pk, local in ((ALICE_PK, ALICE_OPEN), (BOB_PK, BOB_OPEN), (OWNER_PK, OWNER_OPEN)):
            self.assertNotIn(bridge_open_of(local), blob)

    def test_the_login_owner_and_the_group_owner_are_never_removed(self):
        """L2-1-FGS-114: 登录 lark-cli 的 owner 的 union_id 取自 owner 自己的 user_info（并核对 open_id 就是配置的 owner_open_id）；
        群主的 union_id 取自群信息（user_id_type=union_id）。这两个人哪怕不在 bridge 的映射里（这里：频道 owner 绑的是另一个人、
        群主是频道外的人），也不会被移出；别的多余成员照常移出。"""
        w = self.world()
        w.bindings[OWNER_PK] = CHAN_OWNER_OPEN
        w.bindings[CAROL_PK] = CAROL_OPEN
        w.chat["owner_id"] = EXTRA_OPEN
        w.users = {OWNER_OPEN, EXTRA_OPEN, EXTRA2_OPEN}
        env, report = self.go(w)
        self.assertEqual(w.users, {OWNER_OPEN, EXTRA_OPEN, CHAN_OWNER_OPEN, ALICE_OPEN, BOB_OPEN, CAROL_OPEN})
        self.assertEqual((report["removed_users"], report["removals_withheld"]), (1, None))
        gets = [c for c in w.lark_calls if c["args"][:3] == ["api", "GET", f"/open-apis/im/v1/chats/{CHAT}"]]
        self.assertIn("union_id", [self.params(c)["user_id_type"] for c in gets])
        self.assertTrue(any(c["args"][:3] == ["api", "GET", "/open-apis/authen/v1/user_info"] and c["as"] == "user" for c in w.lark_calls))

    def test_a_round_that_cannot_establish_the_owners_union_id_changes_nothing(self):
        """L2-1-FGS-115: owner 的 user_info 读不到、或 open_id 不是配置的 owner_open_id、或 union_id 格式不对：整轮中止（不知道群里谁是
        owner，宁可什么都不动）；不发消息、不动群成员、不写 state；错误里没有任何 id；恢复后下一轮照常。"""
        for mode in ("error", "mismatch", "bad_union"):
            with self.subTest(mode=mode):
                w = self.world()
                w.user_info = mode
                env = Env(self.tmp)
                with self.assertRaises(FGS.GroupSyncError) as ctx:
                    env.round(w)
                self.assertIn("owner", str(ctx.exception))
                for secret in (OWNER_OPEN, union_of(OWNER_OPEN), "someoneelse", "not-a-union-id"):
                    self.assertNotIn(secret, str(ctx.exception))
                self.assertEqual((w.lark_sends(), w.mirrored(), w.member_ops()), ([], [], []))
                self.assertFalse((env.state_dir / FGS.STATE_FILE).exists())
                w.user_info = None
                env.round(w, now=NOW + timedelta(minutes=1))
                self.assertIn(ALICE_OPEN, w.users)
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)

    def test_at_mentions_toward_feishu_name_people_by_union_id_and_agent_bots_by_their_member_id(self):
        """L2-1-FGS-116: Buzz→Feishu 的 @：人用 <at user_id="on_…">（飞书直接认 union_id），agent 的 bot 仍用群里的 bot 成员 id；
        映射不到的人（Carol）不产生 @；发出去的文本里没有 bridge 的 open_id，也没有 owner 应用的人的 open_id。"""
        w = self.world()
        w.events.append(event(eid(5), ALICE_PK, "cc @Bob @Carol @helper-agent", created_at=T0 + 2,
                              tags=[("p", BOB_PK), ("p", CAROL_PK), ("p", AGENT_PK)]))
        self.go(w)
        texts = [text_of(c) for c in w.lark_sends()]
        want = "Alice（Buzz）：cc @Bob @Carol @helper-agent"
        self.assertIn(want, texts)
        for text in texts:
            for pk, local in ((ALICE_PK, ALICE_OPEN), (BOB_PK, BOB_OPEN)):
                self.assertNotIn(bridge_open_of(local), text)
                self.assertNotIn(local, text)
        sent = next(m for m in w.messages if m["sender"]["sender_type"] == "app" and "cc @Bob" in m["content"])
        self.assertEqual(sent.get("mentions", []), [])  # owner 应用的 id 不带进 Desk 发出的消息

    def test_senders_and_mentions_from_feishu_are_paired_by_the_message_itself(self):
        """L2-1-FGS-117: 飞书消息列表里发信人和被 @ 的人是 owner 应用的 open_id（列表接口忽略 user_id_type）。不在缓存里的，就对
        这一条消息再取一次 ?user_id_type=union_id（owner 的 user 身份），按 mentions[].key 和发信人一一配对：
        发信人认出来、@Bob 变成 Buzz 对 Bob 的 @、@agent bot 仍按 bot；频道外的人也配对（缓存），但不镜像。
        每条最多取一次，已缓存的人不再取；state 里的 idmap 只有 id 对。"""
        w = self.world()
        w.messages.append(fmsg("om_in4", ALICE_OPEN, "@Bob 看下", mentions=[
            {"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}, {"id": "all", "key": "@_all", "name": "所有人"}]))
        env, report = self.go(w)
        mirrored = {e["content"]: [t[1] for t in e["tags"] if t[0] == "p"] for e in w.mirrored()}
        self.assertEqual(mirrored["[飞书] Alice：＠Bob 看下"], [BOB_PK])
        self.assertEqual(mirrored["[飞书] Alice：＠helper-agent ＠飞书 CLI 帮我看下"], [AGENT_PK])
        self.assertEqual(report["skipped"].get("unmapped_sender"), 1)  # EXTRA：配对了，但不在频道里
        gets = w.message_gets()
        self.assertEqual(len(gets), 3)  # Alice 一次、EXTRA 一次、Bob（只在 @ 里出现）一次
        for call in gets:
            self.assertEqual((call["app"], call["as"]), (OWNER_APP, "user"))
            self.assertEqual(self.params(call), {"user_id_type": "union_id"})
        self.assertEqual(sorted(c["args"][2].rsplit("/", 1)[1] for c in gets), ["om_in1", "om_in3", "om_in4"])
        self.assertEqual(env.state()["idmap"], {o: union_of(o) for o in (ALICE_OPEN, EXTRA_OPEN, BOB_OPEN)})
        self.assertNotIn("Alice", json.dumps(env.state()["idmap"]))

    def test_a_cached_pair_is_not_looked_up_again(self):
        """L2-1-FGS-118: 配对存进 state 以后，同一个人（发信人或被 @）的后续消息不再调飞书：第一轮取发信人，第二轮只为没见过的
        被 @ 的人（Bob）取一次，第三轮什么都不用取。"""
        w = FakeWorld(self.tmp)
        w.messages = [fmsg("om_a1", ALICE_OPEN, "第一条")]
        env = Env(self.tmp)
        env.round(w)
        self.assertEqual(len(w.message_gets()), 1)
        mention = [{"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}]
        w.messages.append(fmsg("om_a2", ALICE_OPEN, "第二条", mentions=mention, when=NOW + timedelta(seconds=40)))
        env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(len(w.message_gets()), 2)
        w.messages.append(fmsg("om_a3", ALICE_OPEN, "第三条", mentions=mention, when=NOW + timedelta(minutes=1, seconds=40)))
        env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual(len(w.message_gets()), 2)
        contents = {e["content"]: [t[1] for t in e["tags"] if t[0] == "p"] for e in w.mirrored()}
        self.assertEqual(contents, {"[飞书] Alice：第一条": [], "[飞书] Alice：第二条": [BOB_PK], "[飞书] Alice：第三条": [BOB_PK]})

    def test_messages_that_will_be_skipped_anyway_cost_no_lookup(self):
        """L2-1-FGS-119: 已删除、系统消息、app 发的、空内容、绑定之前的消息本来就不镜像，所以不为它们调飞书取 union_id。"""
        w = FakeWorld(self.tmp)
        w.messages = [
            fmsg("om_del", ALICE_OPEN, "删了的", deleted=True),
            fmsg("om_sys", ALICE_OPEN, "Bob 加入了群", msg_type="system"),
            fmsg("om_app", AGENT_BOT_MEMBER, "bot 说的", sender_type="app"),
            fmsg("om_empty", ALICE_OPEN, "   "),
            fmsg("om_old", ALICE_OPEN, "很早以前", when=NOW - timedelta(hours=3)),
        ]
        env, report = self.go(w)
        self.assertEqual(w.message_gets(), [])
        self.assertEqual((report["to_buzz"], report["errors"], env.state()["idmap"]), (0, 0, {}))

    def test_when_the_pairing_cannot_be_made_nobody_is_guessed(self):
        """L2-1-FGS-120: 飞书的回答配不上（mentions 的 key 对不上、id_type 不是 union_id、id 不是 on_、答案里没有这条消息或是别的消息、
        同一个 key 出现两次）：该发信人的这条消息不镜像、计入 sender_unpaired；@ 配不上的不产生 @（mention_unpaired），发信人认得
        的消息照发；绝不按名字、顺序去猜；配不上的不进缓存；这不算错误。mentions 顺序不同但 key 对得上，照样配对。"""
        cases = {
            "mention key changed": (lambda mid, t, item: dict(item, mentions=[dict(m, key="@_user_9") for m in item["mentions"]]),
                                    {"to_buzz": 1, "skipped": {"mention_unpaired": 1}, "tags": [AGENT_PK]}),
            "mention id not a union id": (lambda mid, t, item: dict(item, mentions=[dict(m, id=BOB_OPEN) for m in item["mentions"]]),
                                          {"to_buzz": 1, "skipped": {"mention_unpaired": 1}, "tags": [AGENT_PK]}),
            "duplicate mention key": (lambda mid, t, item: dict(item, mentions=item["mentions"] + item["mentions"]),
                                      {"to_buzz": 1, "skipped": {"mention_unpaired": 1}, "tags": [AGENT_PK]}),
            "mentions in another order": (lambda mid, t, item: dict(item, mentions=list(reversed(item["mentions"]))),
                                          {"to_buzz": 1, "skipped": {}, "tags": [AGENT_PK, BOB_PK]}),
            "sender id type is open_id": (lambda mid, t, item: dict(item, sender=dict(item["sender"], id_type="open_id")),
                                          {"to_buzz": 0, "skipped": {"sender_unpaired": 1}}),
            "sender id is not a union id": (lambda mid, t, item: dict(item, sender=dict(item["sender"], id=ALICE_OPEN)),
                                            {"to_buzz": 0, "skipped": {"sender_unpaired": 1}}),
            "sender is an app": (lambda mid, t, item: dict(item, sender=dict(item["sender"], sender_type="app")),
                                 {"to_buzz": 0, "skipped": {"sender_unpaired": 1}}),
            "no such message in the answer": (lambda mid, t, item: None, {"to_buzz": 0, "skipped": {"sender_unpaired": 1}}),
            "another message": (lambda mid, t, item: dict(item, message_id="om_other"),
                                {"to_buzz": 0, "skipped": {"sender_unpaired": 1}}),
        }
        for name, (tamper, want) in cases.items():
            with self.subTest(name):
                w = FakeWorld(self.tmp)
                w.messages = [fmsg("om_x", ALICE_OPEN, "看下", mentions=[
                    {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"},
                    {"id": BOB_OPEN, "key": "@_user_2", "name": "Bob"}])]
                w.message_get_tamper = tamper
                env = Env(self.tmp)
                report = env.round(w)
                self.assertEqual(report["to_buzz"], want["to_buzz"], name)
                self.assertEqual(report["skipped"].get("mention_unpaired", 0), want["skipped"].get("mention_unpaired", 0), name)
                self.assertEqual(report["skipped"].get("sender_unpaired", 0), want["skipped"].get("sender_unpaired", 0), name)
                self.assertEqual((report["errors"], report["identity_conflicts"], report["failed"], report["unknown"]), (0, 0, 0, 0), name)
                if "tags" in want:
                    self.assertEqual(sorted(t[1] for t in w.mirrored()[0]["tags"] if t[0] == "p"), sorted(want["tags"]), name)
                if "sender_unpaired" in want["skipped"]:
                    self.assertNotIn(ALICE_OPEN, env.state()["idmap"], name)
                if "mention_unpaired" in want["skipped"]:
                    self.assertNotIn(BOB_OPEN, env.state()["idmap"], name)
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)

    def test_a_failed_lookup_is_retried_like_a_refused_send_and_then_given_up(self):
        """L2-1-FGS-121: 飞书那次取 union_id 的调用本身失败了（网络错误、接口报错）：这条消息还没有发到 Buzz，按「确定没发出」处理——
        记一次 errors、留在重试里，下一轮取成功就镜像一次且只有一次；连续三次都失败就记 failed，不再试，也不镜像。"""
        w = FakeWorld(self.tmp)
        w.messages = [fmsg("om_x", ALICE_OPEN, "看下")]
        w.message_get_fail = ["network"]
        env = Env(self.tmp)
        report = env.round(w)
        self.assertEqual((report["errors"], report["to_buzz"], w.buzz_sends()), (1, 0, []))
        self.assertTrue(FGS.needs_attention(report))
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((report["errors"], report["to_buzz"], len(w.buzz_sends())), (0, 1, 1))
        env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual(len(w.buzz_sends()), 1)
        w2 = FakeWorld(self.tmp)
        w2.messages = [fmsg("om_y", ALICE_OPEN, "看下")]
        w2.message_get_fail = ["api", "network", "api", "api"]
        (env.state_dir / FGS.STATE_FILE).unlink()
        reports = []
        for i in range(3):
            reports.append(env.round(w2, now=NOW + timedelta(minutes=i)))
            if i < 2:  # 记的是首次尝试的时间，重试不改它
                self.assertEqual(env.state()["f2b"]["om_y"], f"retry:{ts(NOW)}")
        self.assertEqual([r["errors"] for r in reports], [1, 1, 1])
        self.assertEqual([r["failed"] for r in reports], [0, 0, 1])
        env.round(w2, now=NOW + timedelta(minutes=3))
        self.assertEqual((w2.buzz_sends(), env.state()["f2b"]["om_y"]), ([], FGS.FAILED))

    def test_a_cached_pair_that_new_evidence_contradicts_is_dropped_and_reported(self):
        """L2-1-FGS-122: 缓存里的旧配对和新取到的证据矛盾（同一个 open_id 对应了别的 union_id，或同一个 union_id 对应了别的
        open_id）：两边都不信——矛盾的缓存项删掉、新证据这次也不采信（缓存里不留这次答案的任何配对），这条消息不镜像
        （skipped identity_conflict），报告 identity_conflicts，需要关注；下一轮重新配对，消息照常镜像、缓存恢复正确。"""
        twin = "ou_alicetwin0000000000000000000001"
        bob = [{"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}]
        # 第一种：Alice 在缓存里，配对是错的（成了 Carol 的 union_id，所以消息被认成 Carol 发的）；要靠这条消息里一个没见过的
        #        @（Bob）才会去取，取回来的发信人配对和缓存矛盾——只有「同一个 open_id、另一个 union_id」这一条能发现
        # 第二种：Alice 在缓存里，配对是 Bob 的 union_id：既是同一个 open_id 另一个 union_id，也是 Bob 的 union_id 被别人占着
        # 第三种：缓存里只有另一个 open_id 对着 Alice 的 union_id；Alice 自己没缓存，取回来的配对和它矛盾（只有「同一个 union_id、
        #        另一个 open_id」这一条能发现）
        scenarios = {
            "same open_id, Carol's union_id": ({ALICE_OPEN: union_of(CAROL_OPEN)}, bob),
            "same open_id, Bob's union_id": ({ALICE_OPEN: union_of(BOB_OPEN)}, bob),
            "same union_id, other open_id": ({twin: union_of(ALICE_OPEN)}, None),
        }
        for name, (seeded, mentions) in scenarios.items():
            with self.subTest(name):
                w = FakeWorld(self.tmp)
                w.bindings[CAROL_PK] = CAROL_OPEN
                w.messages = [fmsg("om_a1", ALICE_OPEN, "第一条")]
                env = Env(self.tmp)
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                env.round(w)
                self.assertEqual(env.state()["idmap"], {ALICE_OPEN: union_of(ALICE_OPEN)})
                state = env.state()
                state["idmap"] = seeded  # 一个旧的、错的缓存
                write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
                w.messages.append(fmsg("om_a2", ALICE_OPEN, "第二条", when=NOW + timedelta(seconds=40), mentions=mentions))
                report = env.round(w, now=NOW + timedelta(minutes=1))
                self.assertEqual((report["identity_conflicts"], report["skipped"].get("identity_conflict")), (1, 1))
                self.assertEqual([e["content"] for e in w.mirrored()], ["[飞书] Alice：第一条"])
                self.assertTrue(FGS.needs_attention(report))
                self.assertEqual(env.state()["idmap"], {})  # 矛盾的项没了，这次答案里的配对也没有采信
                report = env.round(w, now=NOW + timedelta(minutes=2))
                self.assertEqual(report["identity_conflicts"], 0)
                self.assertEqual(env.state()["idmap"][ALICE_OPEN], union_of(ALICE_OPEN))
                self.assertEqual([e["content"] for e in w.mirrored()], ["[飞书] Alice：第一条", "[飞书] Alice：第二条"])
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)

    def test_a_member_whose_union_id_is_not_there_yet_is_unmapped_not_guessed_from_people(self):
        """L2-1-FGS-123: bridge 有 Bob 的绑定（people 里有他的 open_id）但 union_ids 里还没有（回填没跑完）：Bob 算映射不到——
        不会拿 people 里的 open_id 当身份用，所以他不会被拉进群、他在飞书的发言也不镜像；因为有人映射不到，不移人。"""
        w = self.world()
        w.union_missing = {BOB_PK}
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        w.messages.append(fmsg("om_bob", BOB_OPEN, "我是 Bob"))
        env, report = self.go(w)
        self.assertNotIn(BOB_OPEN, w.users)
        self.assertIn(ALICE_OPEN, w.users)
        self.assertEqual((report["unmapped_members"], report["removals_withheld"]), (2, "unmapped_members"))
        self.assertEqual(report["skipped"].get("unmapped_sender"), 2)  # Bob 与 EXTRA
        self.assertNotIn("[飞书] Bob：我是 Bob", [e["content"] for e in w.mirrored()])

    def test_thread_replies_are_paired_too(self):
        """L2-1-FGS-124: 话题里的回复也一样：发信人先配对，再用 --reply-to 挂到话题根对应的 Buzz 事件下。"""
        w = FakeWorld(self.tmp)
        w.messages = [fmsg("om_root", ALICE_OPEN, "根", thread_id="omt_om_root")]
        w.threads["om_root"] = [fmsg("om_rep", BOB_OPEN, "回复", thread_id="omt_om_root")]
        env, report = self.go(w)
        contents = {e["content"]: e for e in w.mirrored()}
        self.assertEqual(set(contents), {"[飞书] Alice：根", "[飞书] Bob：回复"})
        send = next(c for c in w.buzz_sends() if c["content"] == "[飞书] Bob：回复")
        self.assertEqual(send["args"][send["args"].index("--reply-to") + 1], contents["[飞书] Alice：根"]["id"])
        self.assertEqual(sorted(c["args"][2].rsplit("/", 1)[1] for c in w.message_gets()), ["om_rep", "om_root"])
        self.assertEqual(env.state()["idmap"], {ALICE_OPEN: union_of(ALICE_OPEN), BOB_OPEN: union_of(BOB_OPEN)})

    def test_a_state_from_before_the_id_caches_works_and_fills_them(self):
        """L2-1-FGS-125: 升级前的 state（没有 idmap / emailmap）能直接接着跑，缓存从空开始按需补上；identity 写明 union_id 与不写完全一样。"""
        w = FakeWorld(self.tmp)
        w.messages = [fmsg("om_a1", ALICE_OPEN, "第一条")]
        env = Env(self.tmp)
        env.round(w)
        state = {k: v for k, v in env.state().items() if k not in ("idmap", "emailmap")}
        write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
        w.messages.append(fmsg("om_a2", ALICE_OPEN, "第二条", when=NOW + timedelta(seconds=40)))
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((report["to_buzz"], report["errors"]), (1, 0))
        self.assertEqual(env.state()["idmap"], {ALICE_OPEN: union_of(ALICE_OPEN)})
        w2 = self.world()
        env2 = Env(self.tmp, identity="union_id")
        (env2.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
        env2.round(w2)
        self.assertEqual(w2.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN})


    # ---- 变异检查补的用例 -----------------------------------------------------------------------------

    def cached_alice(self, w=None):
        w = w or FakeWorld(self.tmp)
        w.messages = [fmsg("om_a1", ALICE_OPEN, "第一条")]
        env = Env(self.tmp)
        env.round(w)
        return w, env

    def test_a_failed_lookup_for_a_mention_holds_the_message_back_even_when_the_sender_is_known(self):
        """L2-1-FGS-137: 发信人已在缓存里、只有被 @ 的人要取：取失败时这条消息不发（否则会丢掉那个 @），按「确定没发出」重试，
        下一轮取成功后带着 @ 镜像一次。"""
        w, env = self.cached_alice()
        w.messages.append(fmsg("om_a2", ALICE_OPEN, "看下", when=NOW + timedelta(seconds=40),
                               mentions=[{"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}]))
        w.message_get_fail = ["network"]
        sends = len(w.buzz_sends())
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((report["errors"], report["to_buzz"], len(w.mirrored())), (1, 0, 1))
        self.assertEqual(len(w.buzz_sends()), sends)  # 发信人认得，但没有发出去：发出去就丢了 @Bob
        self.assertTrue(env.state()["f2b"]["om_a2"].startswith(FGS.RETRY + ":"))
        report = env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual((report["errors"], report["to_buzz"]), (0, 1))
        self.assertEqual(len(w.buzz_sends()), sends + 1)
        self.assertEqual([t[1] for t in w.mirrored()[-1]["tags"] if t[0] == "p"], [BOB_PK])

    def test_a_message_deleted_after_it_was_listed_or_an_answer_with_two_messages_pairs_nobody(self):
        """L2-1-FGS-138: 列出来以后消息被删了（单条 GET 报 not_found）、或答案里有两条消息：都不是「调用失败」，也配不上——
        这条不镜像、计 sender_unpaired，不记 errors、不重试。"""
        for name in ("gone", "two items"):
            with self.subTest(name):
                w = FakeWorld(self.tmp)
                w.messages = [fmsg("om_x", ALICE_OPEN, "看下")]
                if name == "gone":
                    w.message_get_gone = {"om_x"}
                else:
                    w.message_get_tamper = lambda mid, t, item: [item, dict(item, message_id="om_y")]
                env = Env(self.tmp)
                report = env.round(w)
                self.assertEqual((report["errors"], report["failed"], report["to_buzz"]), (0, 0, 0))
                self.assertEqual(report["skipped"].get("sender_unpaired"), 1)
                self.assertEqual(env.state()["f2b"], {})
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)

    def test_a_group_owned_by_an_app_protects_no_person(self):
        """L2-1-FGS-139: 群主是个应用（owner_id_type 是 app_id，bot 建的群）：它不是人的 id，不进「保留」名单——不会有一个应用 id 被当成
        用户去拉；别的多余成员照常移出。两种模式都一样。"""
        for identity in ("default", "email"):
            with self.subTest(identity=identity):
                w = self.world()
                w.bindings[CAROL_PK] = CAROL_OPEN
                w.chat_owner_is_app = True
                w.users = {OWNER_OPEN, EXTRA_OPEN}
                env = Env(self.tmp, **({} if identity == "default" else {"identity": identity}))
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                report = env.round(w)
                self.assertEqual(w.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN, CAROL_OPEN})
                for op in w.member_ops():
                    if self.params(op)["member_id_type"] != "app_id":
                        self.assertNotIn(OWNER_APP, json.loads(op["args"][op["args"].index("--data") + 1])["id_list"])
                self.assertEqual(report["member_failures"], 0)

    def test_one_lookup_covers_everyone_in_a_message_and_only_people_cost_one(self):
        """L2-1-FGS-140: 发信人和被 @ 的人都不在缓存里：这条消息只取一次；发信人已缓存、被 @ 的只有 bot 和 @所有人：一次也不取。"""
        w = FakeWorld(self.tmp)
        w.messages = [fmsg("om_a1", ALICE_OPEN, "一起", mentions=[{"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}])]
        env = Env(self.tmp)
        env.round(w)
        self.assertEqual(len(w.message_gets()), 1)
        self.assertEqual(set(env.state()["idmap"]), {ALICE_OPEN, BOB_OPEN})
        w.messages.append(fmsg("om_a2", ALICE_OPEN, "@bot @所有人", when=NOW + timedelta(seconds=40), mentions=[
            {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"}, {"id": OWNER_BOT_MEMBER, "key": "@_user_2", "name": "飞书 CLI"},
            {"id": "all", "key": "@_all", "name": "所有人"}]))
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(len(w.message_gets()), 1)
        self.assertEqual(report["to_buzz"], 1)

    def test_an_answer_that_contradicts_itself_drops_the_cache_it_contradicts(self):
        """L2-1-FGS-141: 同一个人在这一份答案里有两个 union_id（发信人 Alice 自己又被 @，两处对不上）：本身就是矛盾——Alice 的缓存项
        也删掉，这份答案的配对一个都不采信，这条消息本轮不镜像，identity_conflicts 加一。"""
        w, env = self.cached_alice()
        w.messages.append(fmsg("om_a2", ALICE_OPEN, "看下", when=NOW + timedelta(seconds=40), mentions=[
            {"id": ALICE_OPEN, "key": "@_user_1", "name": "Alice"}, {"id": BOB_OPEN, "key": "@_user_2", "name": "Bob"}]))

        def tamper(mid, id_type, item):
            item["mentions"][0]["id"] = union_of(CAROL_OPEN)  # 「Alice」这一处成了 Carol 的 union_id
            return item
        w.message_get_tamper = tamper
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((report["identity_conflicts"], report["skipped"].get("identity_conflict")), (1, 1))
        self.assertEqual(env.state()["idmap"], {})
        self.assertEqual(len(w.mirrored()), 1)

    def test_a_person_already_in_the_cache_is_not_reported_unpaired_when_the_view_does_not_vouch(self):
        """L2-1-FGS-142: 被 @ 的 Bob 已在缓存里：这一份视图没给他配对（key 对不上）也没关系，他的 @ 照常，不计 mention_unpaired；
        没见过的 Carol 配对成功，进缓存。"""
        w = FakeWorld(self.tmp)
        w.messages = [fmsg("om_a1", ALICE_OPEN, "第一条"), fmsg("om_b1", BOB_OPEN, "第二条")]
        env = Env(self.tmp)
        env.round(w)
        self.assertEqual(set(env.state()["idmap"]), {ALICE_OPEN, BOB_OPEN})
        w.messages.append(fmsg("om_a2", ALICE_OPEN, "看下", when=NOW + timedelta(seconds=40), mentions=[
            {"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}, {"id": CAROL_OPEN, "key": "@_user_2", "name": "Carol"}]))

        def tamper(mid, id_type, item):
            item["mentions"][0]["key"] = "@_user_9"  # Bob 这一处配不上
            return item
        w.message_get_tamper = tamper
        report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((report["skipped"].get("mention_unpaired", 0), report["to_buzz"]), (0, 1))
        self.assertEqual([t[1] for t in w.mirrored()[-1]["tags"] if t[0] == "p"], [BOB_PK])
        self.assertEqual(env.state()["idmap"][CAROL_OPEN], union_of(CAROL_OPEN))


class EmailMode(TmpCase):
    """email 模式（identity: "email"）：内部身份空间是本应用的 open_id。bridge 给每个成员已验证的邮箱（要 bridge 开了
    CHANNEL_PEOPLE_EMAILS_ENABLED），本地经通讯录搜索换成 open_id：只认逐字相等，缓存「sha256(应用 id + 邮箱) → open_id」，
    state 里没有明文邮箱。"""

    def world(self):
        return RoundIdentity.world(self)

    def go(self, w, env=None, **kw):
        env = env or Env(self.tmp, identity="email", feishu_unmapped_senders="skip")
        return env, env.round(w, **kw)

    @staticmethod
    def key(email, app=OWNER_APP):
        return hashlib.sha256(f"{app}\0{email}".encode()).hexdigest()

    def test_a_full_round_in_the_app_open_id_space_with_addresses_searched_and_cached(self):
        """L2-1-FGS-126: 每个成员的每个邮箱经通讯录搜索（owner 的 user 身份）换成本应用的 open_id；成员对账、@、发信人识别全在 open_id 空间：
        member_id_type 是 open_id、@ 用 <at user_id="ou_…">、飞书发信人直接按 open_id 认（不再取单条消息）；不调 user_info；
        bridge 应用的 open_id 和 union_id 一个字节都不进 lark 调用；state 里只有 sha256 键，没有明文邮箱。"""
        w = self.world()
        w.bindings[CAROL_PK] = CAROL_OPEN
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        w.events.append(event(eid(5), ALICE_PK, "cc @Bob", created_at=T0 + 2, tags=[("p", BOB_PK)]))
        w.messages.append(fmsg("om_in4", ALICE_OPEN, "@Bob 看下", mentions=[{"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}]))
        env, report = self.go(w)
        self.assertEqual(w.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN, CAROL_OPEN})
        self.assertEqual((report["unmapped_members"], report["removed_users"], report["errors"]), (0, 1, 0))
        self.assertEqual(sorted(w.searches()), ["alice@a4x.io", "bob@a4x.io", "carol@a4x.io", "owner@a4x.io"])
        for call in w.lark_calls:
            if tuple(call["args"][:2]) == ("contact", "+search-user"):
                self.assertEqual((call["app"], call["as"]), (OWNER_APP, "user"))
                self.assertIn("--exclude-external-users", call["args"])
        for op in w.member_ops():
            self.assertIn(json.loads(op["args"][op["args"].index("--params") + 1])["member_id_type"], ("open_id", "app_id"))
        for call in w.member_lists():  # 一次调用同时列出用户和 bot，和以前一样（不带 id 类型和成员类型）
            self.assertNotIn("--member-types", call["args"])
            self.assertNotIn("--member-id-type", call["args"])
        self.assertEqual((w.message_gets(), [c for c in w.lark_calls if c["args"][:3] == ["api", "GET", "/open-apis/authen/v1/user_info"]]), ([], []))
        texts = [text_of(c) for c in w.lark_sends()]
        self.assertIn("Alice（Buzz）：cc @Bob", texts)
        self.assertEqual({e["content"]: [t[1] for t in e["tags"] if t[0] == "p"] for e in w.mirrored()}["[飞书] Alice：＠Bob 看下"], [BOB_PK])
        blob = json.dumps([c["args"] for c in w.lark_calls if tuple(c["args"][:2]) != ("contact", "+search-user")])
        for local in (ALICE_OPEN, BOB_OPEN, OWNER_OPEN):
            self.assertNotIn(bridge_open_of(local), blob)
            self.assertNotIn(union_of(local), blob)
        state = env.state()
        self.assertEqual(state["emailmap"], {self.key(e): o for e, o in (("owner@a4x.io", OWNER_OPEN), ("alice@a4x.io", ALICE_OPEN),
                                                                        ("bob@a4x.io", BOB_OPEN), ("carol@a4x.io", CAROL_OPEN))})
        self.assertEqual(state["idmap"], {})
        self.assertNotIn("a4x.io", (env.state_dir / FGS.STATE_FILE).read_text())

    def test_an_address_that_is_not_in_canonical_form_is_searched_and_cached_in_it(self):
        """L2-1-FGS-151: bridge 给的地址大小写不同或首尾有空格（违反契约的小写约定，但不算畸形）：按规范形式（去空格、转小写）搜索和缓存，
        不带着空格去搜；仍然要飞书返回的 email / enterprise_email 与这个查询值逐字相等才认。"""
        w = self.world()
        w.emails_raw[ALICE_PK] = [" Alice@A4X.io "]  # bridge 这样给；飞书里的邮箱是小写的
        env, report = self.go(w)
        self.assertIn("alice@a4x.io", w.searches())
        self.assertNotIn(" Alice@A4X.io ", w.searches())
        self.assertIn(self.key("alice@a4x.io"), env.state()["emailmap"])
        self.assertNotIn(self.key(" Alice@A4X.io "), env.state()["emailmap"])
        self.assertIn(ALICE_OPEN, w.users)

    def test_a_resolved_address_is_not_searched_again(self):
        """L2-1-FGS-127: 缓存命中就不再查：第二轮、第三轮没有任何搜索；某人换了绑定的邮箱，只为新邮箱搜一次。"""
        w = self.world()
        env, _ = self.go(w)
        first = len(w.searches())
        self.assertEqual(first, 3)  # owner、Alice、Bob（Carol 没有绑定，bridge 没给她的邮箱）
        env.round(w, now=NOW + timedelta(minutes=1))
        env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual(len(w.searches()), first)
        w.emails[BOB_PK] = ["robert@a4x.io"]
        env.round(w, now=NOW + timedelta(minutes=3))
        self.assertEqual(w.searches()[first:], ["robert@a4x.io"])
        self.assertIn(BOB_OPEN, w.users)

    def test_the_search_is_fuzzy_but_only_a_verbatim_address_is_believed(self):
        """L2-1-FGS-128: 通讯录搜索是模糊的（子串、不分大小写）：返回的近似账号一概不认；email 字段或 enterprise_email 字段逐字相等都行；
        大小写不同、同一地址有两个账号（歧义）的人映射不到，绝不选一个凑数；映射不到的人不进群，也不会因此拉进别的账号。"""
        dave_pk, dave_open = "77" * 32, "ou_dave00000000000000000000000001"
        w = self.world()
        w.members.append({"pubkey": dave_pk, "role": "member"})
        w.display[dave_pk] = "Dave"
        w.bindings.update({CAROL_PK: CAROL_OPEN, dave_pk: dave_open})
        w.directory_noise = [
            {"open_id": "ou_noise0000000000000000000000001", "email": "xalice@a4x.io", "enterprise_email": ""},
            {"open_id": "ou_noise0000000000000000000000002", "email": "", "enterprise_email": "alice@a4x.io.cn"},
            {"open_id": "ou_dave1000000000000000000000001", "email": "", "enterprise_email": "dave@a4x.io"},
            {"open_id": "ou_dave2000000000000000000000001", "email": "dave@a4x.io", "enterprise_email": ""},
        ]
        w.directory_hidden = {"dave@a4x.io"}
        w.feishu_emails[BOB_OPEN] = {"email": "bob@a4x.io", "enterprise_email": ""}  # 逐字相等的是 email 字段
        w.feishu_emails[CAROL_OPEN] = {"email": "", "enterprise_email": "Carol@a4x.io"}  # 大小写不同
        env, report = self.go(w)
        self.assertEqual(w.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN})
        self.assertEqual((report["unmapped_members"], report["removals_withheld"]), (2, None))  # 群里本来就没有别人，没有要移的
        self.assertEqual((report["identity_conflicts"], report["errors"]), (0, 0))

    def test_a_person_with_several_addresses_is_one_person_or_nobody(self):
        """L2-1-FGS-129: 一个人有多个邮箱时：都指向同一个 open_id → 映射；有的邮箱在飞书里找不到、别的能找到 → 映射（找不到的那个记
        「没找到」）；指向不同的 open_id → 该成员不映射、计入 identity_conflicts（需要关注），也不拉他指向的任何一个账号进群；
        某个邮箱的搜索出错 → 这一轮不映射他（不知道有没有矛盾）、记 errors、不缓存，下一轮再查。"""
        for name in ("consistent", "one unknown", "conflict", "a search fails"):
            with self.subTest(name):
                w = self.world()
                w.emails[BOB_PK] = ["bob@a4x.io", "bob@example.com"]
                if name == "one unknown":
                    w.directory_hidden = {"bob@example.com"}
                elif name == "conflict":
                    w.directory_override["bob@example.com"] = CAROL_OPEN
                elif name == "a search fails":
                    w.search_fail = {"bob@a4x.io": ["network"]}
                env = Env(self.tmp, identity="email")
                report = env.round(w)
                if name in ("consistent", "one unknown"):
                    self.assertIn(BOB_OPEN, w.users)
                    self.assertEqual((report["errors"], report["identity_conflicts"]), (0, 0))
                    self.assertEqual(sorted(w.searches()), ["alice@a4x.io", "bob@a4x.io", "bob@example.com", "owner@a4x.io"])
                elif name == "conflict":
                    self.assertNotIn(BOB_OPEN, w.users)
                    self.assertNotIn(CAROL_OPEN, w.users)
                    self.assertEqual((report["identity_conflicts"], report["errors"]), (1, 0))
                    self.assertEqual(report["unmapped_members"], 2)  # Carol（没绑定）与 Bob
                    self.assertTrue(FGS.needs_attention(report))
                else:
                    self.assertNotIn(BOB_OPEN, w.users)
                    self.assertEqual((report["errors"], report["identity_conflicts"]), (1, 0))
                    self.assertNotIn(self.key("bob@a4x.io"), env.state()["emailmap"])
                    report = env.round(w, now=NOW + timedelta(minutes=1))
                    self.assertIn(BOB_OPEN, w.users)
                    self.assertEqual(report["errors"], 0)
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)

    def test_an_address_feishu_cannot_place_is_asked_again_only_after_a_while(self):
        """L2-1-FGS-130: 在飞书里找不到的邮箱（比如同事还没有飞书账号）记「没找到」和时间：EMAIL_MISS_RECHECK_SECONDS 之内不再搜（否则
        每分钟一次搜索永远打下去），过了才再搜；这时找到了就换成 open_id。"""
        w = self.world()
        w.directory_hidden = {"bob@a4x.io"}
        env, report = self.go(w)
        self.assertEqual(w.searches().count("bob@a4x.io"), 1)
        self.assertEqual(env.state()["emailmap"][self.key("bob@a4x.io")], f"miss:{ts(NOW)}")
        self.assertNotIn(BOB_OPEN, w.users)
        env.round(w, now=NOW + timedelta(seconds=FGS.EMAIL_MISS_RECHECK_SECONDS - 60))
        self.assertEqual(w.searches().count("bob@a4x.io"), 1)
        w.directory_hidden = set()
        env.round(w, now=NOW + timedelta(seconds=FGS.EMAIL_MISS_RECHECK_SECONDS))
        self.assertEqual(w.searches().count("bob@a4x.io"), 2)
        self.assertEqual(env.state()["emailmap"][self.key("bob@a4x.io")], BOB_OPEN)
        self.assertIn(BOB_OPEN, w.users)

    def test_the_cache_key_includes_the_app_and_a_hit_is_trusted(self):
        """L2-1-FGS-131: 缓存键是 sha256(owner 应用 id + NUL + 邮箱)：同一个邮箱在不同应用里是不同的 open_id，所以换应用不会读到旧应用的
        缓存；命中的缓存直接用、不再搜索；没带应用 id 的旧键（或别的应用的键）不认。"""
        w = self.world()
        env, _ = self.go(w)
        state = env.state()
        state["emailmap"][self.key("alice@a4x.io")] = EXTRA_OPEN  # 缓存说 Alice 是 EXTRA_OPEN：命中就用
        write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
        before = len(w.searches())
        env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(len(w.searches()), before)
        self.assertIn(EXTRA_OPEN, w.users)
        for other in (hashlib.sha256(b"alice@a4x.io").hexdigest(), self.key("alice@a4x.io", app="cli_other0000000001")):
            w2 = self.world()
            env2 = Env(self.tmp, identity="email")
            (env2.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
            env2.round(w2)
            state = env2.state()
            state["emailmap"] = {other: EXTRA_OPEN}  # 只有别人的键：对我们等于没有缓存
            write_owner_only(env2.state_dir / FGS.STATE_FILE, json.dumps(state))
            before = len(w2.searches())
            env2.round(w2, now=NOW + timedelta(minutes=1))
            self.assertIn("alice@a4x.io", w2.searches()[before:])
            self.assertNotIn(EXTRA_OPEN, w2.users)
            self.assertIn(ALICE_OPEN, w2.users)

    def test_searches_per_round_are_capped_and_the_rest_waits_unmapped(self):
        """L2-1-FGS-132: 一轮最多 EMAIL_LOOKUPS_PER_ROUND 次搜索（第一轮面对很多没缓存的成员时不会一次打满通讯录接口）：超出的成员这一轮
        映射不到（计入 unmapped_members，所以不移人），已查到的存进 state，下一轮接着查，直到查完。"""
        w = self.world()
        env = Env(self.tmp, identity="email")
        with mock.patch.object(FGS, "EMAIL_LOOKUPS_PER_ROUND", 2):
            report = env.round(w)
            self.assertEqual((len(w.searches()), report["unmapped_members"]), (2, 2))  # 第三个待查的 + Carol（没绑定）
            self.assertEqual(len(env.state()["emailmap"]), 2)
            report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((len(w.searches()), report["unmapped_members"]), (3, 1))
        self.assertEqual(w.users, {OWNER_OPEN, ALICE_OPEN, BOB_OPEN})

    def test_the_group_owner_is_never_removed_in_email_mode_either(self):
        """L2-1-FGS-133: 群主（不管是不是频道的人）在 email 模式里也永不移出，用 open_id 读群信息；别的多余成员照常移出。"""
        w = self.world()
        w.bindings[CAROL_PK] = CAROL_OPEN
        w.chat["owner_id"] = EXTRA_OPEN
        w.users = {OWNER_OPEN, EXTRA_OPEN, EXTRA2_OPEN}
        env, report = self.go(w)
        self.assertEqual(w.users, {OWNER_OPEN, EXTRA_OPEN, ALICE_OPEN, BOB_OPEN, CAROL_OPEN})
        self.assertEqual(report["removed_users"], 1)


    # ---- 变异检查补的用例 -----------------------------------------------------------------------------

    def test_a_person_with_an_address_still_to_be_looked_up_is_not_mapped_on_the_others_alone(self):
        """L2-1-FGS-143: 一个人的邮箱这一轮没查完（超出搜索上限）：不能只凭已查到的那个映射他——没查的那个可能指向别的账号；这一轮不映射，
        下一轮查完，发现矛盾就报 identity_conflicts。"""
        w = self.world()
        w.emails[BOB_PK] = ["bob@a4x.io", "bob@example.com"]
        w.directory_override["bob@example.com"] = CAROL_OPEN
        env = Env(self.tmp, identity="email")
        with mock.patch.object(FGS, "EMAIL_LOOKUPS_PER_ROUND", 2):
            env.round(w)
            self.assertNotIn(BOB_OPEN, w.users)
            self.assertNotIn("bob@example.com", w.searches())
            report = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertIn("bob@example.com", w.searches())
        self.assertEqual(report["identity_conflicts"], 1)
        self.assertNotIn(BOB_OPEN, w.users)

    def test_a_failed_search_leaves_the_person_undecided_and_ends_that_persons_lookups(self):
        """L2-1-FGS-144: 一个人的某个邮箱搜索出错：不管别的邮箱查到了什么，这一轮都不映射他（没查到的那个可能指向别的账号）；
        出错之后他剩下的邮箱这一轮不再搜；出错的不缓存，下一轮再查。"""
        w = self.world()
        w.emails[BOB_PK] = ["bob@a4x.io", "bob@example.com"]
        w.search_fail = {"bob@example.com": ["network"]}  # 第二个邮箱出错，第一个已经查到
        env = Env(self.tmp, identity="email")
        report = env.round(w)
        self.assertEqual((report["errors"], report["identity_conflicts"]), (1, 0))
        self.assertNotIn(BOB_OPEN, w.users)
        self.assertNotIn(self.key("bob@example.com"), env.state()["emailmap"])
        self.assertEqual(env.round(w, now=NOW + timedelta(minutes=1))["errors"], 0)
        self.assertIn(BOB_OPEN, w.users)
        w2 = self.world()
        w2.emails[BOB_PK] = ["bob@a4x.io", "bob@example.com"]
        w2.search_fail = {"bob@a4x.io": ["api"]}  # 第一个出错：第二个这一轮不搜
        (env.state_dir / FGS.STATE_FILE).unlink()
        env.round(w2)
        self.assertIn("bob@a4x.io", w2.searches())
        self.assertNotIn("bob@example.com", w2.searches())
        self.assertNotIn(BOB_OPEN, w2.users)

    def test_the_search_budget_goes_to_people_in_a_fixed_order(self):
        """L2-1-FGS-145: 搜索预算不够时先查谁是确定的（按 pubkey 排序）：否则每轮先查的人不一样，某些人可能一直排不到。"""
        w = FakeWorld(self.tmp)
        w.members = [m for m in w.members if m["pubkey"] in (OWNER_PK, AGENT_PK, MIRROR_PK)]
        pks = [f"{i:02x}" * 32 for i in (0xa1, 0x3c, 0x77, 0xe4, 0x05, 0x9b, 0x52)]
        for i, pk in enumerate(pks):
            w.members.append({"pubkey": pk, "role": "member"})
            w.display[pk] = f"P{i}"
            w.bindings[pk] = f"ou_p{i}0000000000000000000000000001"
        w.bindings = {pk: o for pk, o in w.bindings.items() if pk in pks or pk == OWNER_PK}
        order = sorted(pks + [OWNER_PK])
        env = Env(self.tmp, identity="email")
        with mock.patch.object(FGS, "EMAIL_LOOKUPS_PER_ROUND", 3):
            env.round(w)
        self.assertEqual(w.searches(), [w.emails_of(pk)[0] for pk in order[:3]])

    def test_a_refreshed_cache_entry_counts_as_new_when_the_cache_is_pruned(self):
        """L2-1-FGS-146: 缓存满了裁剪最早写入的：「没找到」过期后重查得到的结果是新写入的，不能还排在最前面被先裁掉。"""
        w = self.world()
        env = Env(self.tmp, identity="email")
        env.round(w)
        state = env.state()
        state["emailmap"] = {self.key("alice@a4x.io"): "miss:1", "aa" * 32: EXTRA_OPEN, "bb" * 32: EXTRA2_OPEN}
        write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
        with mock.patch.object(FGS, "IDMAP_KEEP", 3):
            env.round(w, now=NOW + timedelta(hours=1))
        self.assertEqual(env.state()["emailmap"][self.key("alice@a4x.io")], ALICE_OPEN)
        self.assertEqual(len(env.state()["emailmap"]), 3)


class IdentityLeaks(TmpCase):
    """两种模式下：邮箱、union_id、open_id 的完整值不出现在报告、stderr、错误里；owner 的 key 只在脚本进程里签名。"""

    ALL_IDS = [ALICE_OPEN, BOB_OPEN, OWNER_OPEN, EXTRA_OPEN, CAROL_OPEN]

    def run_main(self, w, env, now=NOW):
        import contextlib
        out, err = self.tmp / "out.json", self.tmp / "err.txt"
        args = ["round", "--config", str(env.config), "--state-dir", str(env.state_dir)]
        w.clock = now
        with open(out, "w") as fh, open(err, "w") as eh, contextlib.redirect_stderr(eh):
            code = FGS.main(args, base_env=env.base_env, runner=w, stdout=fh, now=now, http=w.http_get)
        return code, out.read_text(), err.read_text()

    def secrets(self):
        values = list(self.ALL_IDS) + [union_of(o) for o in self.ALL_IDS] + [bridge_open_of(o) for o in self.ALL_IDS]
        values += ["alice@a4x.io", "bob@a4x.io", "carol@a4x.io", "owner@a4x.io", "@a4x.io", "bob@example.com"]
        return values

    def world(self):
        w = RoundIdentity.world(self)
        w.emails[BOB_PK] = ["bob@a4x.io", "bob@example.com"]
        w.messages.append(fmsg("om_in4", ALICE_OPEN, "@Bob 看下", mentions=[{"id": BOB_OPEN, "key": "@_user_1", "name": "Bob"}]))
        return w

    def test_reports_and_errors_never_carry_an_address_or_an_id(self):
        """L2-1-FGS-134: 两种模式下，一轮正常、有身份矛盾、搜索或取 union_id 出错、bridge 接口出错、owner user_info 对不上时，stdout 的报告和
        stderr 里都没有邮箱、union_id、open_id（bridge 应用的、本应用的）的完整值；报告里只有计数，跳过原因只有名字。"""
        scenarios = {
            "clean": lambda w: None,
            "conflict": lambda w: w.directory_override.update({"bob@example.com": CAROL_OPEN}),
            "search fails": lambda w: w.search_fail.update({"alice@a4x.io": ["network"]}),
            "lookup fails": lambda w: w.message_get_fail.extend(["network", "api"]),
            "unpaired": lambda w: setattr(w, "message_get_tamper", lambda mid, t, item: dict(item, sender=dict(item["sender"], id_type="open_id"))),
            "bridge down": lambda w: w.api_fail.append("500"),
            "bad answer": lambda w: w.api_fail.append("emails_bad_address"),
            "owner mismatch": lambda w: setattr(w, "user_info", "mismatch"),
        }
        for identity in ("default", "email"):
            for name, arrange in scenarios.items():
                with self.subTest(identity=identity, scenario=name):
                    w = self.world()
                    arrange(w)
                    env = Env(self.tmp, **({} if identity == "default" else {"identity": identity}))
                    (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                    code, out, err = self.run_main(w, env)
                    self.assertIn(code, (FGS.EXIT_OK, FGS.EXIT_ATTENTION, FGS.EXIT_ERROR))
                    for secret in self.secrets():
                        self.assertNotIn(secret, out, secret)
                        self.assertNotIn(secret, err, secret)
        w = self.world()
        w.directory_override["bob@example.com"] = CAROL_OPEN
        env = Env(self.tmp, identity="email")
        (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
        self.assertEqual(self.run_main(w, env)[0], FGS.EXIT_ATTENTION)  # identity_conflicts 需要关注

    def test_addresses_appear_only_as_the_directory_search_query(self):
        """L2-1-FGS-135: 邮箱明文只出现在通讯录搜索的 --query 里（这是 lark-cli 没有别的传法）：不进 buzz 调用（参数、内容、环境）、别的
        lark 调用、state、报告；union 模式根本不用邮箱，任何调用里都没有。"""
        for identity in ("default", "email"):
            with self.subTest(identity=identity):
                w = self.world()
                env = Env(self.tmp, **({} if identity == "default" else {"identity": identity}))
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                report = env.round(w)
                env.round(w, now=NOW + timedelta(minutes=1))
                elsewhere = [c["args"] for c in w.lark_calls if tuple(c["args"][:2]) != ("contact", "+search-user")]
                blob = json.dumps(elsewhere) + json.dumps(w.buzz_calls) + json.dumps(report) + (env.state_dir / FGS.STATE_FILE).read_text()
                self.assertNotIn("@a4x.io", blob)
                self.assertNotIn("@example.com", blob)
                if identity == "default":
                    self.assertEqual(w.searches(), [])
                else:
                    self.assertTrue(w.searches())

    def test_the_owner_key_never_reaches_a_child_process_in_either_mode(self):
        """L2-1-FGS-136: 两种模式下，owner 的 key 只用来签 bridge 那一个 GET：不进 buzz 或 lark 子进程的环境和参数（搜索、取单条消息、user_info 也
        一样），不进报告、state、stderr；子进程环境仍是白名单。"""
        for identity in ("default", "email"):
            with self.subTest(identity=identity):
                w = self.world()
                env = Env(self.tmp, **({} if identity == "default" else {"identity": identity}))
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                code, out, err = self.run_main(w, env)
                self.assertTrue(w.message_gets() if identity == "default" else w.searches())
                for call in w.buzz_calls + w.lark_calls:
                    self.assertNotIn(OWNER_KEY, json.dumps(call.get("env", {})))
                    self.assertNotIn(OWNER_KEY, json.dumps(call.get("args", [])))
                    self.assertNotIn("GITLAB_TOKEN", call["env"])
                    self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", call["env"])
                for text in (out, err, (env.state_dir / FGS.STATE_FILE).read_text()):
                    self.assertNotIn(OWNER_KEY, text)


class SharedIdentity(TmpCase):
    """一个 union_id / open_id 只能对应一个 pubkey：bridge 里两个 pubkey 绑到了同一个飞书账号时，谁是谁分不清——都算映射不到
    （fail-closed），不再是后一个悄悄覆盖前一个。"""

    ALICE2_PK = "66" * 32

    def world(self):
        w = RoundIdentity.world(self)
        w.members.append({"pubkey": self.ALICE2_PK, "role": "member"})
        w.display[self.ALICE2_PK] = "Alice2"
        w.bindings[self.ALICE2_PK] = ALICE_OPEN  # 和 Alice 绑到同一个飞书账号
        w.events.append(event(eid(5), self.ALICE2_PK, "我是另一把 key", created_at=T0 + 3,
                              tags=[("p", ALICE_PK), ("p", BOB_PK)]))
        w.users = {OWNER_OPEN, EXTRA_OPEN}
        w.messages.append(fmsg("om_dup", ALICE_OPEN, "谁说的？"))
        return w

    def check(self, w, env, report):
        self.assertNotIn(ALICE_OPEN, w.users)  # 分不清是哪个 key：不拉进群
        self.assertIn(BOB_OPEN, w.users)  # 不受牵连
        self.assertEqual(report["identity_conflicts"], 1)  # 一个账号一次
        self.assertEqual(report["unmapped_members"], 3)  # 两把 key 加上 Carol
        self.assertEqual(report["removals_withheld"], "unmapped_members")
        self.assertIn(EXTRA_OPEN, w.users)
        self.assertTrue(FGS.needs_attention(report))
        self.assertNotIn("[飞书] Alice：谁说的？", [e["content"] for e in w.mirrored()])  # 不猜是哪个 key 说的
        self.assertNotIn("[飞书] Alice2：谁说的？", [e["content"] for e in w.mirrored()])
        self.assertEqual(report["skipped"].get("unmapped_sender"), 4)  # Alice 的三条（含这条）和 EXTRA 的那条
        texts = [text_of(c) for c in w.lark_sends()]
        towards = next(t for t in texts if t.startswith("Alice2（Buzz）："))
        self.assertNotIn(ALICE_OPEN, towards)
        self.assertEqual(towards.count("<at "), 0)  # owner 应用的 id 不交给 Desk

    def test_two_keys_bound_to_one_union_id_are_both_unmapped(self):
        """L2-1-FGS-147: union_id 模式：两个 pubkey 的 union_id 相同（绑到同一个飞书账号）：都算映射不到——不进群、不认他在飞书里的发言
        （不猜是哪把 key）、@ 他们的 p tag 不产生 <at>；计一次 identity_conflicts（需要关注），有人映射不到所以不移人；别的人不受牵连。"""
        w = self.world()
        env = Env(self.tmp, feishu_unmapped_senders="skip")
        report = env.round(w)
        self.check(w, env, report)

    def test_two_keys_whose_addresses_lead_to_one_account_are_both_unmapped(self):
        """L2-1-FGS-148: email 模式同理：两个 pubkey 的邮箱经通讯录换出的是同一个 open_id：都算映射不到，计一次 identity_conflicts。"""
        w = self.world()
        w.directory_override["alice2@a4x.io"] = ALICE_OPEN
        env = Env(self.tmp, identity="email", feishu_unmapped_senders="skip")
        report = env.round(w)
        self.check(w, env, report)

    def test_the_conflict_ends_when_one_binding_is_gone(self):
        """L2-1-FGS-149: bridge 里解绑其中一把 key 以后，下一轮剩下的那把 key 映射正常：拉进群、认他的发言，identity_conflicts 回到 0。"""
        for identity in ("default", "email"):
            with self.subTest(identity=identity):
                w = self.world()
                env = Env(self.tmp, feishu_unmapped_senders="skip",
                          **({} if identity == "default" else {"identity": identity}))
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                env.round(w)
                self.assertNotIn(ALICE_OPEN, w.users)
                del w.bindings[self.ALICE2_PK]
                report = env.round(w, now=NOW + timedelta(minutes=1))
                self.assertEqual(report["identity_conflicts"], 0)
                self.assertIn(ALICE_OPEN, w.users)
                self.assertIn("[飞书] Alice：谁说的？", [e["content"] for e in w.mirrored()])

    def test_only_channel_humans_are_mapped_whatever_the_bridge_lists(self):
        """L2-1-FGS-150: bridge 应答里出现的 agent / 镜像身份的 pubkey（不该有，但万一有）不是人：不进群、不认那个飞书账号的发言，
        不算映射不到，也不和别人的身份凑成「一个账号两把 key」的冲突。两种模式一样。"""
        agent_owner = "ou_agentowner0000000000000000000001"
        for identity in ("default", "email"):
            with self.subTest(identity=identity):
                w = self.world()
                w.members = [m for m in w.members if m["pubkey"] != self.ALICE2_PK]
                w.bindings = {pk: o for pk, o in w.bindings.items() if pk != self.ALICE2_PK}
                w.bindings[AGENT_PK] = agent_owner
                w.bindings[MIRROR_PK] = ALICE_OPEN  # 甚至和 Alice 是同一个账号：不能因此让 Alice 冲突
                w.messages.append(fmsg("om_agent_owner", agent_owner, "我是 agent 的主人"))
                env = Env(self.tmp, **({} if identity == "default" else {"identity": identity}))
                (env.state_dir / FGS.STATE_FILE).unlink(missing_ok=True)
                report = env.round(w)
                self.assertNotIn(agent_owner, w.users)
                self.assertIn(ALICE_OPEN, w.users)
                self.assertEqual((report["identity_conflicts"], report["unmapped_members"]), (0, 1))  # 只有 Carol
                self.assertNotIn("[飞书] 某人：我是 agent 的主人", [e["content"] for e in w.mirrored()])

    def test_one_pubkey_per_id_is_decided_by_the_ids_alone(self):
        """L1-FGS-111: one_pubkey_per_id：每个 id 只有一个 pubkey 才认；两个及以上的 id 整个排除并列出是哪些 pubkey；不看 pubkey 的顺序、
        排序或谁在前。"""
        ok, conflicts = FGS.one_pubkey_per_id({"a" * 64: "on_1", "b" * 64: "on_2", "c" * 64: "on_1", "d" * 64: "on_3"})
        self.assertEqual(ok, {"on_2": "b" * 64, "on_3": "d" * 64})
        self.assertEqual(conflicts, {"on_1": ["a" * 64, "c" * 64]})
        self.assertEqual(FGS.one_pubkey_per_id({}), ({}, {}))
        ok, conflicts = FGS.one_pubkey_per_id({"c" * 64: "on_1", "a" * 64: "on_1", "b" * 64: "on_1"})
        self.assertEqual((ok, conflicts), ({}, {"on_1": ["a" * 64, "b" * 64, "c" * 64]}))


class Commands(TmpCase):
    def test_preflight_existing_reports_problems_with_exit_2(self):
        """L2-1-FGS-020: preflight --mode existing 读群信息判定；有阻断项退出码 2，输出 JSON 不含邮箱。"""
        w = FakeWorld(self.tmp)
        w.chat.update(owner_id="ou_someoneelse", add_member_permission="only_owner")
        env = Env(self.tmp)
        out = FGS.preflight_command(env.config, "existing", CHAT, base_env=env.base_env, runner=w)
        self.assertFalse(out["ok"])
        self.assertIn("cannot_add_members", out["problems"])
        self.assertNotIn("@a4x.io", json.dumps(out))
        self.assertIn(["auth", "status"], [c["args"][:2] for c in w.lark_calls])  # identity read first, no --as
        for c in w.lark_calls:
            if c["args"][:2] != ["auth", "status"]:
                self.assertEqual(c["as"], "user")
        code = FGS.main(["preflight", "--config", str(env.config), "--mode", "existing", "--chat-id", CHAT],
                        base_env=env.base_env, runner=w, stdout=open(os.devnull, "w"))
        self.assertEqual(code, FGS.EXIT_BLOCKED)

    def test_preflight_existing_when_chat_is_unreadable(self):
        """L2-1-FGS-021: 读不到群信息（不在群里、chat_id 错或登录失效）时，阻断项是 chat_unreadable 并带错误码。"""
        w = FakeWorld(self.tmp)
        env = Env(self.tmp)
        out = FGS.preflight_command(env.config, "existing", "oc_other00000000000000000000000001", base_env=env.base_env, runner=w)
        self.assertEqual((out["problems"], out["error_code"]), (["chat_unreadable"], 232011))

    def test_create_chat_owner_is_group_owner_and_config_updated(self):
        """L2-1-FGS-022: create-chat 由 owner 应用 bot 建私有群，群主是 owner、bot 自设管理员；chat_id 原子写回配置且保持 0600。"""
        w = FakeWorld(self.tmp)
        env = Env(self.tmp, chat_id=None)
        out = FGS.create_chat_command(env.config, "测试群", base_env=env.base_env, runner=w)
        call = w.created[0]
        self.assertEqual((call["app"], call["as"]), (OWNER_APP, "bot"))
        a = call["args"]
        self.assertEqual(a[a.index("--owner") + 1], OWNER_OPEN)
        self.assertIn("--set-bot-manager", a)
        self.assertEqual(a[a.index("--type") + 1], "private")
        self.assertEqual(json.loads(env.config.read_text())["chat_id"], out["chat_id"])
        self.assertEqual(out["warnings"], [])
        self.assertEqual(os.stat(env.config).st_mode & 0o777, 0o600)

    def test_create_chat_refused_when_owner_out_of_scope_or_profile_differs(self):
        """L2-1-FGS-023: owner 不在个人应用可用范围内、或 lark-cli 登录的不是配置里的应用时 create-chat 拒绝，不发建群请求、不改配置。"""
        for tweak in ("scope", "profile"):
            w = FakeWorld(self.tmp)
            if tweak == "scope":
                w.owner_in_scope = False
            else:
                w.owner_profile_app = "cli_other000000000001"
            env = Env(self.tmp, chat_id=None)
            before = env.config.read_text()
            with self.assertRaises(FGS.GroupSyncError, msg=tweak):
                FGS.create_chat_command(env.config, "测试群", base_env=env.base_env, runner=w)
            self.assertEqual(w.created, [])
            self.assertEqual(env.config.read_text(), before)

    def test_bind_refused_on_problems_and_writes_on_success_with_warnings(self):
        """L2-1-FGS-024: bind 先跑预检：有阻断项不写配置；通过后写入 chat_id，并把警告带出来。"""
        w = FakeWorld(self.tmp)
        env = Env(self.tmp, chat_id=None)
        w.chat["external"] = True
        with self.assertRaises(FGS.GroupSyncError):
            FGS.bind_command(env.config, CHAT, base_env=env.base_env, runner=w)
        self.assertIsNone(json.loads(env.config.read_text())["chat_id"])
        w.chat.update(external=False, share_card_permission="allowed")
        out = FGS.bind_command(env.config, CHAT, base_env=env.base_env, runner=w)
        self.assertEqual(json.loads(env.config.read_text())["chat_id"], CHAT)
        self.assertEqual(out["warnings"], ["share_card_allowed"])

    def test_bound_config_refuses_another_chat(self):
        """L2-1-FGS-025: 已绑定的配置不能 bind 到别的群（即使那个群本身预检能过），也不能再 create-chat。"""
        w = FakeWorld(self.tmp)
        env = Env(self.tmp, chat_id="oc_other00000000000000000000000001")
        before = env.config.read_text()
        with self.assertRaises(FGS.GroupSyncError):
            FGS.bind_command(env.config, CHAT, base_env=env.base_env, runner=w)
        self.assertEqual(env.config.read_text(), before)
        with self.assertRaises(FGS.GroupSyncError):
            FGS.create_chat_command(env.config, "again", base_env=env.base_env, runner=w)
        self.assertEqual(w.created, [])

    def test_round_exit_codes_and_partial_report(self):
        """L2-1-FGS-026: round 干净时退出 0；有需要关注的事（未知结果、失败、暂缓移人、bot 进不去）退出 3；
        出错退出 1，仍输出已完成部分的报告，登录失效时提示重新登录。"""
        w = RoundIdentity.world(self)
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        env = Env(self.tmp)
        args = ["round", "--config", str(env.config), "--state-dir", str(env.state_dir)]
        out = self.tmp / "out.json"
        with open(out, "w") as fh:
            self.assertEqual(FGS.main(args, base_env=env.base_env, runner=w, stdout=fh, now=NOW, http=w.http_get), FGS.EXIT_OK)
        w.messages.append(fmsg("om_net", ALICE_OPEN, "net"))
        w.buzz_send_fail = ["network"]
        with open(out, "w") as fh:
            self.assertEqual(FGS.main(args, base_env=env.base_env, runner=w, stdout=fh, now=NOW, http=w.http_get), FGS.EXIT_ATTENTION)
        self.assertEqual(json.loads(out.read_text())["unknown"], 1)

        def expired(argv, **kw):
            if argv[0] == LARK_CLI and argv[1:3] == ["im", "+chat-members-list"]:
                return subprocess.CompletedProcess(argv, 1, "", json.dumps(
                    {"ok": False, "error": {"type": "authentication", "message": "token expired"}}))
            return w(argv, **kw)
        err = self.tmp / "err.txt"
        import contextlib
        with open(out, "w") as fh, open(err, "w") as eh, contextlib.redirect_stderr(eh):
            self.assertEqual(FGS.main(args, base_env=env.base_env, runner=expired, stdout=fh, now=NOW, http=w.http_get), FGS.EXIT_ERROR)
        self.assertIn("unmapped_members", json.loads(out.read_text()))
        self.assertIn("lark-cli auth login", err.read_text())

    def test_existing_preflight_and_bind_check_the_owner_profile(self):
        """L2-1-FGS-062: 关联已有群的预检与 bind 也先核对 lark-cli 登录的是配置里的应用和 owner；不符时阻断，不写配置。"""
        w = FakeWorld(self.tmp)
        w.profiles[OWNER_APP] = "ou_someoneelse000000000000000001"
        env = Env(self.tmp, chat_id=None)
        out = FGS.preflight_command(env.config, "existing", CHAT, base_env=env.base_env, runner=w)
        self.assertIn("owner_profile_mismatch", out["problems"])
        with self.assertRaises(FGS.GroupSyncError):
            FGS.bind_command(env.config, CHAT, base_env=env.base_env, runner=w)
        self.assertIsNone(json.loads(env.config.read_text())["chat_id"])

    def test_create_chat_is_not_repeated_after_an_unrecorded_success(self):
        """L2-1-FGS-063: 建群成功但写回配置失败时，再跑 create-chat 不会再建一个群：先落盘建群意图，残留意图时拒绝并提示用 bind；
        正常完成后意图文件被清掉。"""
        w = FakeWorld(self.tmp)
        env = Env(self.tmp, chat_id=None)
        # The config cannot be rewritten after the chat exists (disk full, killed...). Patched rather
        # than chmod-ed: CI runs as root, which ignores directory permissions.
        with mock.patch.object(FGS, "_write_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                FGS.create_chat_command(env.config, "测试群", base_env=env.base_env, runner=w)
        with self.assertRaises(FGS.GroupSyncError) as ctx:
            FGS.create_chat_command(env.config, "测试群", base_env=env.base_env, runner=w)
        self.assertIn("bind", str(ctx.exception))
        self.assertEqual(len(w.created), 1)
        intent = env.config.with_name(env.config.name + FGS.CREATE_INTENT_SUFFIX)
        intent.unlink()
        FGS.create_chat_command(env.config, "测试群", base_env=env.base_env, runner=w)
        self.assertFalse(intent.exists())
        self.assertEqual(len(w.created), 2)


# ================================ 飞书 reaction：agent 在 Buzz 上的 reaction 同步成飞书表情 ================================


class ReactionRouting(unittest.TestCase):
    """纯函数：谁的 reaction、哪个表情、落在哪条消息上。"""

    def route(self, ev, **kw):
        args = dict(agent_apps={AGENT_PK: AGENT_APP}, human_pubkeys={ALICE_PK, BOB_PK},
                    agent_pubkeys={AGENT_PK, AGENT2_PK}, reaction_map=FGS.DEFAULT_REACTION_MAP)
        args.update(kw)
        return FGS.route_buzz_reaction(ev, **args)

    def test_agent_emoji_map_to_feishu_emoji_types(self):
        """L1-FGS-081: agent 的 reaction 经它自己的应用 bot 落成相近的飞书表情：👀→GLANCE、💬→Typing（agent 收到请求时打的两个）、
        ✅→DONE、👍 与 + →THUMBSUP、👌→OK、🙏→THANKS、💪→MUSCLE；ADR-0020 加了 ❌→CrossMark（回答入群申请用，与 ✅ 成对）。"""
        expected = {"👀": "GLANCE", "💬": "Typing", "✅": "DONE", "👍": "THUMBSUP", "+": "THUMBSUP", "👌": "OK",
                    "🙏": "THANKS", "💪": "MUSCLE", "❌": "CrossMark"}
        self.assertEqual(FGS.DEFAULT_REACTION_MAP, expected)
        for emoji, emoji_type in expected.items():
            self.assertEqual(self.route(reaction_event(1, AGENT_PK, eid(1), emoji)), (AGENT_APP, emoji_type), emoji)

    def test_variation_selectors_and_padding_do_not_hide_an_emoji(self):
        """L1-FGS-082: 客户端可能给 emoji 补 U+FE0F / U+FE0E 或空白，不影响查表；空内容、只有选择符的内容不映射。"""
        for content in ("👍️", " 👍 ", "👍︎"):
            self.assertEqual(self.route(reaction_event(1, AGENT_PK, eid(1), content)), (AGENT_APP, "THUMBSUP"), repr(content))
        for content in ("", " ", "️"):
            self.assertEqual(self.route(reaction_event(1, AGENT_PK, eid(1), content)), "reaction_emoji_unmapped", repr(content))

    def test_only_agents_with_a_bot_react_and_each_skip_has_its_own_reason(self):
        """L1-FGS-083: 人的 reaction 不同步（决定：只同步 agent 的）；镜像身份、频道外的人、没有飞书 bot 的 agent 都不代打；
        不是 kind 7、没有目标、表情没映射也各有原因。"""
        self.assertEqual(self.route(reaction_event(1, ALICE_PK, eid(1), "👍")), "reaction_human")
        self.assertEqual(self.route(reaction_event(1, MIRROR_PK, eid(1), "👍")), "reaction_not_agent")
        self.assertEqual(self.route(reaction_event(1, OUTSIDER_PK, eid(1), "👍")), "reaction_not_agent")
        self.assertEqual(self.route(reaction_event(1, AGENT2_PK, eid(1), "👍")), "agent_bot_unavailable")
        self.assertEqual(self.route(event(eid(2), AGENT_PK, "👀", kind=9)), "kind")
        self.assertEqual(self.route(dict(reaction_event(1, AGENT_PK, eid(1), "👀"), tags=[])), "reaction_no_target")
        self.assertEqual(self.route(reaction_event(1, AGENT_PK, eid(1), "🚀")), "reaction_emoji_unmapped")

    def test_configured_map_overrides_and_extends_the_default(self):
        """L1-FGS-084: 配置里的 reaction_map 只覆盖或补充它写到的键，其余仍用默认表。"""
        merged = FGS.merged_reaction_map({"🎉": "Party", "👀": "OnIt"})
        self.assertEqual(merged["🎉"], "Party")
        self.assertEqual(merged["👀"], "OnIt")
        self.assertEqual(merged["💬"], "Typing")
        self.assertEqual(FGS.merged_reaction_map({}), FGS.DEFAULT_REACTION_MAP)
        self.assertEqual(FGS.DEFAULT_REACTION_MAP["👀"], "GLANCE", "the default table must not be mutated")

    def test_reaction_target_is_the_first_valid_e_tag(self):
        """L1-FGS-086: kind 7 只带一个指向目标事件的 e tag；取第一个 64 位小写 hex 的 e tag，别的形状都不算。"""
        self.assertEqual(FGS.reaction_target(reaction_event(1, AGENT_PK, eid(7), "👀")), eid(7))
        ev = dict(reaction_event(1, AGENT_PK, eid(7), "👀"), tags=[["p", ALICE_PK], ["e", "xyz"], ["e", eid(8)], ["e", eid(9)]])
        self.assertEqual(FGS.reaction_target(ev), eid(8))
        self.assertIsNone(FGS.reaction_target(dict(ev, tags=[["e"], ["e", eid(0xabc).upper()], "e"])))
        self.assertIsNone(FGS.reaction_target(dict(ev, tags=None)))


class ReactionPlumbing(TmpCase):
    def test_config_reaction_map_is_optional_and_strict(self):
        """L1-FGS-085: reaction_map 可以不写；写了就必须是 {emoji: 飞书 emoji_type}（emoji_type 只含字母数字下划线、≤40 位，
        emoji 非空、≤16 个字符）。其他未知键仍被拒。"""
        env = Env(self.tmp)
        raw = json.loads(env.config.read_text())
        self.assertNotIn("reaction_map", FGS.load_config(env.config))
        ok = write_owner_only(self.tmp / "ok.json", json.dumps(dict(raw, reaction_map={"🎉": "Party"})))
        self.assertEqual(FGS.load_config(ok)["reaction_map"], {"🎉": "Party"})
        ok_empty = write_owner_only(self.tmp / "ok-empty.json", json.dumps(dict(raw, reaction_map={})))
        self.assertEqual(FGS.load_config(ok_empty)["reaction_map"], {})
        for bad in ([], "GLANCE", {"": "Party"}, {"🎉": ""}, {"🎉": "no spaces"}, {"🎉": "Par-ty"}, {"🎉": 5}, {"🎉": ["Party"]},
                    {"🎉": "P" * 41}, {"x" * 17: "Party"}, {"🎉": None}):
            path = write_owner_only(self.tmp / "bad.json", json.dumps(dict(raw, reaction_map=bad)))
            with self.assertRaises(FGS.GroupSyncError, msg=repr(bad)):
                FGS.load_config(path)

    def test_state_from_before_reactions_loads_without_backfilling_old_reactions(self):
        """L1-FGS-087: 升级前写的 state（没有 r2f 和 react_since）仍然能读；react_since 从 buzz_since 起算，所以升级不会把历史 reaction
        全补发一遍。新字段格式坏了、多出未知字段照样按「关闭失败」拒绝。"""
        state = FGS.State(binding="b", floor=100, buzz_since=5000, feishu_since=5000)
        (self.tmp / "state").mkdir(mode=0o700, exist_ok=True)
        FGS.save_state(self.tmp / "state", state)
        path = self.tmp / "state" / FGS.STATE_FILE
        data = json.loads(path.read_text())
        self.assertEqual((data["r2f"], data["react_since"]), ({}, 0))
        legacy = {k: v for k, v in data.items() if k not in ("r2f", "react_since")}
        write_owner_only(path, json.dumps(legacy))
        loaded = FGS.load_state(self.tmp / "state")
        self.assertEqual((loaded.r2f, loaded.react_since), ({}, 5000))
        for change in ({"r2f": {"a": 5}}, {"r2f": []}, {"react_since": "1"}, {"react_since": True}, {"nope": 1}):
            write_owner_only(path, json.dumps(dict(data, **change)))
            with self.assertRaises(FGS.GroupSyncError, msg=str(change)):
                FGS.load_state(self.tmp / "state")
        write_owner_only(path, json.dumps({k: v for k, v in data.items() if k != "buzz_since"}))
        with self.assertRaises(FGS.GroupSyncError):
            FGS.load_state(self.tmp / "state")

    def test_lark_react_and_unreact_use_raw_params_and_the_bot_identity(self):
        """L1-FGS-088: 表情走 im reactions create / delete，身份 bot，消息 id 放 --params、emoji_type 放 --data（这两个原始接口没有
        类型化 flag）；返回 reaction_id。没有 reaction_id、服务端拒绝、传输错误分别是「不确定」「明确被拒」「不确定」。"""
        calls = []

        def runner(argv, **kw):
            calls.append(argv[1:])
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {"reaction_id": "rid1"}}), "")
        lark = FGS.LarkCli(LARK_CLI, {}, runner=runner)
        self.assertEqual(lark.react("om_1", "GLANCE"), "rid1")
        args = calls[0]
        self.assertEqual(args[:3], ["im", "reactions", "create"])
        self.assertEqual(args[args.index("--as") + 1], "bot")
        self.assertEqual(json.loads(args[args.index("--params") + 1]), {"message_id": "om_1"})
        self.assertEqual(json.loads(args[args.index("--data") + 1]), {"reaction_type": {"emoji_type": "GLANCE"}})
        lark.unreact("om_1", "rid1")
        args = calls[1]
        self.assertEqual(args[:3], ["im", "reactions", "delete"])
        self.assertEqual(args[args.index("--as") + 1], "bot")
        self.assertEqual(json.loads(args[args.index("--params") + 1]), {"message_id": "om_1", "reaction_id": "rid1"})

        def no_id(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {}}), "")
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.LarkCli(LARK_CLI, {}, runner=no_id).react("om_1", "GLANCE")
        self.assertFalse(ctx.exception.definite)

        def refused(argv, **kw):
            return fail(argv[1:], 231001, "invalid_emoji")
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.LarkCli(LARK_CLI, {}, runner=refused).react("om_1", "GLANCE")
        self.assertTrue(ctx.exception.definite)
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.LarkCli(LARK_CLI, {}, runner=refused).unreact("om_1", "rid1")
        self.assertTrue(ctx.exception.definite)

    def test_buzz_messages_can_read_other_kinds(self):
        """L1-FGS-089: BuzzCli.messages 默认读普通消息和 40003 编辑；读 reaction 时传 kinds=7,5，其余不变。"""
        seen = []

        def runner(argv, **kw):
            seen.append(argv[1:])
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        buzz = FGS.BuzzCli(BUZZ_CLI, {}, runner=runner)
        buzz.messages(CHANNEL, 7)
        buzz.messages(CHANNEL, 7, kinds=FGS.REACTION_KINDS)
        self.assertEqual(seen[0][seen[0].index("--kinds") + 1], "9,45001,45003,40003")
        self.assertEqual(seen[1][seen[1].index("--kinds") + 1], "7,5")
        self.assertEqual(FGS.REACTION_KINDS, "7,5")
        for args in seen:
            self.assertEqual(args[args.index("--since") + 1], "7")
            self.assertEqual(args[args.index("--limit") + 1], "200")

    def test_reaction_counters_are_reported_but_never_page_anyone(self):
        """L1-FGS-090: 报告里有 reactions_added / reactions_removed / reactions_failed 三个计数；表情是装饰，失败不触发 needs_attention
        （消息同步的失败才触发）。"""
        report = FGS._new_report()
        for key in ("reactions_added", "reactions_removed", "reactions_failed"):
            self.assertEqual(report[key], 0)
        report.update(reactions_added=3, reactions_removed=1, reactions_failed=2)
        self.assertFalse(FGS.needs_attention(report))
        self.assertTrue(FGS.needs_attention(dict(report, errors=1)))
        self.assertTrue(FGS.needs_attention(dict(report, backlog_skipped=["reactions"])))

    def test_prune_bounds_the_reaction_ledger_too(self):
        """L1-FGS-091: 表情账本 r2f 和其他账本一样有界：只留最近写入的 LEDGER_KEEP 条，最早写入的先丢。（丢掉的只会是早已过了
        重读窗口的条目；万一窗口里的 reaction 被重读，最多重打一次，飞书对同一表情的创建是幂等的。）"""
        s = FGS.State(r2f={eid(i): f"{AGENT_PK}|om_{i}|GLANCE|rc{i}" for i in range(FGS.LEDGER_KEEP + 3)},
                      attempts={f"r2f:{eid(i)}": 1 for i in range(FGS.LEDGER_KEEP + 2)})
        FGS.prune_state(s)
        self.assertEqual(len(s.r2f), FGS.LEDGER_KEEP)
        self.assertEqual([k for k in (eid(0), eid(2), eid(3)) if k in s.r2f], [eid(3)])
        self.assertIn(eid(FGS.LEDGER_KEEP + 2), s.r2f)
        self.assertEqual(len(s.attempts), FGS.LEDGER_KEEP)


class ReactionSync(TmpCase):
    """一整轮：agent 在 Buzz 上的 reaction 落到飞书里对应那条消息上。"""

    def world(self):
        w = FakeWorld(self.tmp)
        w.events = [event(eid(1), BOB_PK, "Buzz 里的话", created_at=T0)]
        w.messages = [fmsg("om_in1", ALICE_OPEN, "@helper-agent 帮我看下", mentions=[
            {"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"}])]
        return w

    def start(self, w=None, **cfg):
        """第一轮：镜像两边的消息，agent 的 bot 进群。返回 (w, env, 飞书消息在 Buzz 里的事件 id, Buzz 消息在飞书里的 id)。"""
        w = w or self.world()
        env = Env(self.tmp, **cfg)
        env.round(w)
        return w, env, w.mirrored()[0]["id"], env.state()["b2f"][eid(1)]

    def at(self, minutes):
        return NOW + timedelta(minutes=minutes)

    def test_agent_reactions_on_a_feishu_message_appear_from_the_agents_own_bot(self):
        """L2-1-FGS-076: 飞书里有人 @agent，agent 在 Buzz 上打 👀 和 💬 → 那条飞书消息上出现 GLANCE 和 Typing，由 agent 自己的应用 bot
        打出来（它的 CONFIG_DIR 与 DATA_DIR），不是 owner 的应用；owner 的 key 不进子进程。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     reaction_event(2, AGENT_PK, target, "💬", created_at=ts(NOW) + 21)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP), ("om_in1", "Typing", AGENT_APP)})
        calls = w.reaction_calls()
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual((c["app"], c["as"]), (AGENT_APP, "bot"))
            self.assertEqual(c["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
            self.assertEqual(c["env"]["LARKSUITE_CLI_DATA_DIR"], str(self.tmp / "agent-data"))
            self.assertNotIn("BUZZ_PRIVATE_KEY", c["env"])
        self.assertEqual((report["reactions_added"], report["reactions_failed"]), (2, 0))
        self.assertFalse(FGS.needs_attention(report))

    def test_agent_reaction_on_a_buzz_message_lands_on_its_feishu_copy(self):
        """L2-1-FGS-077: Buzz 里的消息已被镜像到飞书（owner bot 发的那条）；agent 对它打 👀 → 表情打在飞书那条副本上。"""
        w, env, _, copy = self.start()
        w.events.append(reaction_event(1, AGENT_PK, eid(1), "👀", created_at=ts(NOW) + 20))
        env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_set(), {(copy, "GLANCE", AGENT_APP)})

    def test_a_synced_reaction_is_never_repeated(self):
        """L2-1-FGS-078: 同一条 reaction 在重读窗口里被读到多次，也只打一次（账本记着它）；后续轮次没有任何 reactions 调用。"""
        w, env, target, _ = self.start()
        w.events.append(reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20))
        env.round(w, now=self.at(5))
        n = len(w.reaction_calls())
        self.assertEqual(n, 1)
        env.round(w, now=self.at(10))
        env.round(w, now=self.at(15))
        self.assertEqual(len(w.reaction_calls()), n)
        self.assertEqual(len(env.state()["r2f"]), 1)

    def test_humans_reactions_are_not_synced(self):
        """L2-1-FGS-079: `reaction_sync: "agents_only"`（ADR-0020 之前的行为）：人在 Buzz 上打的 reaction 不同步：没有任何飞书 reactions
        调用，计数记在 skipped.reaction_human 里。缺省的双向见 test_buzz_feishu_group_sync_two_way_reactions.py。"""
        w, env, target, copy = self.start(reaction_sync="agents_only")
        w.events += [reaction_event(1, ALICE_PK, eid(1), "👍", created_at=ts(NOW) + 20),
                     reaction_event(2, BOB_PK, target, "👀", created_at=ts(NOW) + 21)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_calls(), [])
        self.assertEqual(report["skipped"].get("reaction_human"), 2)
        self.assertEqual(report["reactions_added"], 0)

    def test_unmapped_emoji_is_skipped_and_counted_without_blocking_the_rest(self):
        """L2-1-FGS-080: agent 打了没有对应飞书表情的 🚀：跳过并计数，同一轮里映射得上的 👀 照常同步。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "🚀", created_at=ts(NOW) + 20),
                     reaction_event(2, AGENT_PK, target, "👀", created_at=ts(NOW) + 21)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP)})
        self.assertEqual(report["skipped"].get("reaction_emoji_unmapped"), 1)
        self.assertEqual(report["reactions_failed"], 0)

    def test_withdrawn_reaction_is_removed_from_feishu_and_stays_removed(self):
        """L2-1-FGS-081: agent 在 Buzz 上撤销 reaction（kind 5 指向那条 kind 7）→ 飞书里对应的表情被删掉（用 agent 自己的 bot），
        之后不再补回。不论中继撤销后还留着那条 kind 7（👀）还是把它删了（💬）都一样。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     reaction_event(2, AGENT_PK, target, "💬", created_at=ts(NOW) + 21)]
        env.round(w, now=self.at(5))
        self.assertEqual(len(w.reaction_set()), 2)
        w.events.append(deletion_event(1, AGENT_PK, eid(0x9001), eid(0x9002), created_at=ts(NOW) + 400))
        w.events = [e for e in w.events if e["id"] != eid(0x9002)]  # this relay drops the kind 7 of a deletion
        report = env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_set(), set())
        self.assertEqual(report["reactions_removed"], 2)
        deletes = [c for c in w.reaction_calls() if c["args"][2] == "delete"]
        self.assertEqual({(c["app"], c["as"]) for c in deletes}, {(AGENT_APP, "bot")})
        n = len(w.reaction_calls())
        env.round(w, now=self.at(15))
        env.round(w, now=self.at(20))
        self.assertEqual(len(w.reaction_calls()), n)
        self.assertEqual(w.reaction_set(), set())

    def test_same_second_status_reaction_removes_old_before_adding_new(self):
        """L2-1-FGS-099 同秒旧 reaction 撤销与新 reaction 添加按 delete→add，最终表情不被旧删除抹掉。"""
        w, env, target, _ = self.start()
        old = reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20)
        w.events.append(old)
        env.round(w, now=self.at(5))
        created = ts(NOW) + 400
        deletion = deletion_event(1, AGENT_PK, old["id"], created_at=created)
        replacement = reaction_event(2, AGENT_PK, target, "👀", created_at=created)
        deletion["id"], replacement["id"] = "f" * 64, "0" * 64
        w.events += [deletion, replacement]

        report = env.round(w, now=self.at(10))

        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP)})
        calls = [call["args"][2] for call in w.reaction_calls()[-2:]]
        self.assertEqual(calls, ["delete", "create"])
        self.assertEqual((report["reactions_removed"], report["reactions_added"]), (1, 1))

    def test_reaction_added_and_withdrawn_before_a_round_never_appears(self):
        """L2-1-FGS-082: 同一批里既有 reaction 又有撤销它的 kind 5：飞书上根本不出现（不闪一下），也不产生任何 reactions 调用。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     deletion_event(1, AGENT_PK, eid(0x9001), created_at=ts(NOW) + 30)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_calls(), [])
        self.assertEqual(report["skipped"].get("reaction_withdrawn"), 1)
        env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_calls(), [])

    def test_only_the_reactions_own_author_can_withdraw_it(self):
        """L2-1-FGS-083: 别人发的 kind 5 指向 agent 的 reaction，不能让飞书上的表情消失；指向不相干事件（比如一条消息）的 kind 5
        也不动任何东西、不算错误。"""
        w, env, target, _ = self.start()
        w.events.append(reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20))
        env.round(w, now=self.at(5))
        w.events += [deletion_event(1, ALICE_PK, eid(0x9001), created_at=ts(NOW) + 400),
                     deletion_event(2, AGENT_PK, eid(1), created_at=ts(NOW) + 401)]
        report = env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP)})
        self.assertEqual(report["skipped"].get("reaction_delete_foreign"), 1)
        self.assertEqual((report["reactions_removed"], report["errors"], report["reactions_failed"]), (0, 0, 0))

    def test_reaction_on_a_message_not_yet_in_feishu_is_retried_once_it_is(self):
        """L2-1-FGS-084: 目标消息因为飞书限流还没镜像出去：这一轮跳过（skipped.reaction_target_unmirrored），下一轮消息补发成功后
        reaction 在重读窗口里被补上。"""
        w = self.world()
        w.lark_send_fail = ["rate_limited"]  # the first mirror send (eid(1)) is refused
        env = Env(self.tmp)
        w.events.append(reaction_event(1, AGENT_PK, eid(1), "👀", created_at=ts(NOW) + 10))
        report = env.round(w)
        self.assertEqual(w.reaction_calls(), [])
        self.assertEqual(report["skipped"].get("reaction_target_unmirrored"), 1)
        env.round(w, now=self.at(5))
        copy = env.state()["b2f"][eid(1)]
        self.assertTrue(copy.startswith("om_"))
        self.assertEqual(w.reaction_set(), {(copy, "GLANCE", AGENT_APP)})

    def test_reactions_before_the_binding_are_not_synced(self):
        """L2-1-FGS-085: 绑定起点之前的 reaction 不补：读取窗口不早于起点（--since >= floor），即使那条 reaction 还在中继里，
        也不产生任何飞书调用、不进账本。"""
        w = self.world()
        env = Env(self.tmp)
        w.events.append(reaction_event(1, AGENT_PK, eid(1), "👀", created_at=ts(NOW) - 3600))
        report = env.round(w)
        env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_calls(), [])
        self.assertEqual((report["reactions_added"], env.state()["r2f"]), (0, {}))
        floor = env.state()["floor"]
        for c in w.buzz_calls:
            if tuple(c["args"][:2]) == ("messages", "get") and c["args"][c["args"].index("--kinds") + 1] == FGS.REACTION_KINDS:
                self.assertGreaterEqual(int(c["args"][c["args"].index("--since") + 1]), floor)

    def test_refused_reaction_is_retried_then_given_up_once(self):
        """L2-1-FGS-086: 飞书明确拒绝（限流）→ 下一轮重试；连拒 3 次后放弃并只记一次 reactions_failed，账本标 failed，之后不再有
        任何调用。表情失败不触发 needs_attention。"""
        w, env, target, _ = self.start()
        w.events.append(reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20))
        w.reaction_fail = ["rate_limited"] * 3
        reports = [env.round(w, now=self.at(m)) for m in (5, 10, 15)]
        self.assertEqual([r["reactions_failed"] for r in reports], [0, 0, 1])
        self.assertEqual(w.reaction_set(), set())
        self.assertEqual(env.state()["r2f"][eid(0x9001)], FGS.FAILED)
        self.assertFalse(any(FGS.needs_attention(r) for r in reports))
        n = len(w.reaction_calls())
        env.round(w, now=self.at(20))
        self.assertEqual(len(w.reaction_calls()), n)

    def test_uncertain_outcomes_are_retried_and_never_duplicate(self):
        """L2-1-FGS-087: 超时、以及「已送达但 lark-cli 报传输错误」都按不确定处理：下一轮重试，飞书上每种表情恰好一个（创建是幂等的），
        恢复了就不记失败。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     reaction_event(2, AGENT_PK, target, "💬", created_at=ts(NOW) + 21)]
        w.reaction_fail = ["timeout", "network_envelope"]
        env.round(w, now=self.at(5))
        self.assertLessEqual(len(w.reaction_set()), 1)
        report = env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP), ("om_in1", "Typing", AGENT_APP)})
        self.assertEqual(len(w.reactions), 2)
        self.assertEqual(report["reactions_failed"], 0)

    def test_failed_removal_is_retried_then_given_up_once(self):
        """L2-1-FGS-088: 删表情被拒 3 次后放弃：记一次 reactions_failed，账本标 removed，不再调用（表情留在飞书上，报告里能看到）。"""
        w, env, target, _ = self.start()
        w.events.append(reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20))
        env.round(w, now=self.at(5))
        w.events.append(deletion_event(1, AGENT_PK, eid(0x9001), created_at=ts(NOW) + 400))
        w.reaction_fail = ["rate_limited"] * 3
        reports = [env.round(w, now=self.at(m)) for m in (10, 15, 20)]
        self.assertEqual([r["reactions_failed"] for r in reports], [0, 0, 1])
        self.assertEqual(env.state()["r2f"][eid(0x9001)], FGS.REMOVED)
        n = len(w.reaction_calls())
        env.round(w, now=self.at(25))
        self.assertEqual(len(w.reaction_calls()), n)

    def test_a_round_syncs_at_most_the_cap_and_loses_none_of_the_rest(self):
        """L2-1-FGS-089: 一轮最多处理 REACTIONS_PER_ROUND 个飞书调用；超出的留到下一轮（游标不前进），一个不丢、一个不重。"""
        w = self.world()
        total = FGS.REACTIONS_PER_ROUND + 10
        w.events += [event(eid(100 + i), BOB_PK, f"m{i}", created_at=T0 + i // 10) for i in range(total)]
        env = Env(self.tmp)
        env.round(w)
        state = env.state()
        w.events += [reaction_event(i, AGENT_PK, eid(100 + i), "👀", created_at=ts(NOW) + 20 + i // 10) for i in range(total)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(len(w.reaction_set()), FGS.REACTIONS_PER_ROUND)
        self.assertEqual(report["reactions_added"], FGS.REACTIONS_PER_ROUND)
        self.assertEqual(env.state()["react_since"], state["react_since"])
        env.round(w, now=self.at(10))
        self.assertEqual(len(w.reaction_set()), total)
        self.assertEqual(len(w.reactions), total)
        self.assertEqual(env.state()["react_since"], ts(self.at(10)))

    def test_agent_without_a_bot_in_the_chat_does_not_react_by_proxy(self):
        """L2-1-FGS-090: `reaction_sync: "agents_only"` 时，没配飞书应用的 agent 在 Buzz 上打的 reaction 不由任何别的 bot 代打：跳过，
        计入 agent_bot_unavailable。缺省的双向（ADR-0020）由 owner bot 代打，见 L2-1-FGS-872。"""
        w, env, target, _ = self.start(reaction_sync="agents_only")
        w.events.append(reaction_event(1, AGENT2_PK, target, "👀", created_at=ts(NOW) + 20))
        report = env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_calls(), [])
        self.assertEqual(report["skipped"].get("agent_bot_unavailable"), 1)

    def test_upgrading_does_not_backfill_old_reactions(self):
        """L2-1-FGS-091: 升级前的 state（没有 r2f / react_since）：react_since 从 buzz_since 起算，升级前的历史 reaction 不补发；
        升级时刻附近（重读窗口内）的照常同步。"""
        w, env, target, _ = self.start()
        env.round(w, now=self.at(120))
        state = env.state()
        for key in ("r2f", "react_since"):
            del state[key]
        write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 1800),
                     reaction_event(2, AGENT_PK, target, "💬", created_at=ts(self.at(120)) - 100)]
        env.round(w, now=self.at(125))
        self.assertEqual(w.reaction_set(), {("om_in1", "Typing", AGENT_APP)})

    def test_a_reaction_flood_is_dropped_loudly_and_never_blocks_messages(self):
        """L2-1-FGS-092: 一次读不完的 reaction 积压（超过翻页上限）：丢掉、游标前进、报告 backlog_skipped 含 reactions（需要关注），
        消息同步照常，下一轮恢复正常同步新的 reaction。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(i, AGENT_PK, target, "👀", created_at=ts(NOW) + 20) for i in range(1, 251)]
        w.events.append(event(eid(2), BOB_PK, "又一条", created_at=ts(NOW) + 30))
        with mock.patch.object(FGS, "BUZZ_PAGE_MAX", 1):
            report = env.round(w, now=self.at(5))
        self.assertEqual(report["backlog_skipped"], ["reactions"])
        self.assertEqual(report["to_feishu"], 1)
        self.assertTrue(FGS.needs_attention(report))
        self.assertEqual(w.reaction_calls(), [])
        self.assertEqual(env.state()["react_since"], ts(self.at(5)))
        w.events = [e for e in w.events if e["kind"] != 7]
        w.events.append(reaction_event(400, AGENT_PK, target, "💬", created_at=ts(self.at(5)) + 30))
        env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_set(), {("om_in1", "Typing", AGENT_APP)})

    def test_unreadable_reactions_fail_the_round_after_messages_are_done_and_saved(self):
        """L2-1-FGS-093: 读不到 reaction（中继错误）时整轮报错，但消息已经先同步并落盘：下一轮不重发。"""
        w, env, target, _ = self.start()
        w.events.append(event(eid(2), BOB_PK, "第二条", created_at=ts(NOW) + 30))
        w.reaction_reads_fail = True
        with self.assertRaises(FGS.GroupSyncError):
            env.round(w, now=self.at(5))
        self.assertIn(eid(2), env.state()["b2f"])
        sends = len(w.lark_sends())
        w.reaction_reads_fail = False
        env.round(w, now=self.at(10))
        self.assertEqual(len(w.lark_sends()), sends)

    def test_configured_map_changes_what_the_agent_bot_sends(self):
        """L2-1-FGS-094: 配置的 reaction_map 生效：新增 🎉→Party，覆盖 👀→OnIt，💬 仍是默认 Typing。"""
        w, env, target, _ = self.start(reaction_map={"🎉": "Party", "👀": "OnIt"})
        w.events += [reaction_event(1, AGENT_PK, target, "🎉", created_at=ts(NOW) + 20),
                     reaction_event(2, AGENT_PK, target, "👀", created_at=ts(NOW) + 21),
                     reaction_event(3, AGENT_PK, target, "💬", created_at=ts(NOW) + 22)]
        env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_set(), {("om_in1", "Party", AGENT_APP), ("om_in1", "OnIt", AGENT_APP),
                                            ("om_in1", "Typing", AGENT_APP)})

    def test_reactions_are_read_with_their_own_cursor_and_the_mirror_identity(self):
        """L2-1-FGS-095: reaction 单独用 kinds 7,5 读一次，游标是自己的 react_since（本轮结束时推进到当前时间，重读窗口 900 秒），
        读的身份是镜像身份，不是 owner。"""
        w, env, target, _ = self.start()
        self.assertEqual(env.state()["react_since"], ts(NOW))
        env.round(w, now=self.at(30))
        env.round(w, now=self.at(31))
        gets = [c for c in w.buzz_calls if tuple(c["args"][:2]) == ("messages", "get")
                and c["args"][c["args"].index("--kinds") + 1] == FGS.REACTION_KINDS]
        self.assertTrue(gets)
        args = gets[-1]["args"]
        self.assertEqual(int(args[args.index("--since") + 1]), ts(self.at(30)) - FGS.BUZZ_OVERLAP_SECONDS)
        self.assertEqual(args[args.index("--channel") + 1], CHANNEL)
        for c in gets:
            self.assertEqual(c["key"], MIRROR_KEY)


    # ---- 变异检查补的用例：每一条都在被测代码的对应位置做过变异，确认会红 -----------------------------

    def test_someone_elses_deletion_in_the_same_batch_does_not_hide_a_reaction(self):
        """L2-1-FGS-096: 同一批里，别人发的 kind 5 指向 agent 的 reaction：不算撤销，reaction 照常同步（只认 reaction 作者自己的 kind 5）。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     deletion_event(1, ALICE_PK, eid(0x9001), created_at=ts(NOW) + 30)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP)})
        self.assertEqual(report["reactions_added"], 1)
        self.assertNotIn("reaction_withdrawn", report["skipped"])

    def test_a_reaction_seen_withdrawn_is_settled_in_the_ledger_for_good(self):
        """L2-1-FGS-097: 第一次读到就已被撤销的 reaction 记进账本（removed）：之后不再重复计数；即使中继以后不再返回那条 kind 5，
        这条 kind 7 也不会被当成新的补打上去。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     deletion_event(1, AGENT_PK, eid(0x9001), created_at=ts(NOW) + 30)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(report["skipped"].get("reaction_withdrawn"), 1)
        self.assertEqual(env.state()["r2f"], {eid(0x9001): FGS.REMOVED})
        w.events = [e for e in w.events if e["kind"] != 5]  # the relay no longer returns the deletion
        report = env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_calls(), [])
        self.assertNotIn("reaction_withdrawn", report["skipped"])

    def test_a_reaction_on_a_reaction_is_not_a_withdrawal(self):
        """L2-1-FGS-098: 撤销只看 kind 5：同一个 agent 对自己那条 reaction 再打的 kind 7（e tag 指向它）不算撤销，
        原来的表情照常同步；那条 kind 7 的目标不是已镜像的消息，只是跳过。"""
        w, env, target, _ = self.start()
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     reaction_event(2, AGENT_PK, eid(0x9001), "💬", created_at=ts(NOW) + 21)]
        report = env.round(w, now=self.at(5))
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP)})
        self.assertEqual(report["skipped"].get("reaction_target_unmirrored"), 1)

    def test_withdrawing_and_reacting_again_with_the_same_emoji_keeps_the_emoji(self):
        """L2-1-FGS-099: 撤销后又打回同一个表情，两件事落在同一批里：按发生的先后处理（先删再加）。飞书对同一（消息、表情、应用）只有
        一个 reaction，先加会拿到旧 reaction 的 id，再删就把刚打的表情删没了。最后飞书上有表情，账本指向的是它现在的 id，
        之后再撤销也删得掉。"""
        w, env, target, _ = self.start()
        w.events.append(reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20))
        env.round(w, now=self.at(5))
        w.events += [deletion_event(1, AGENT_PK, eid(0x9001), created_at=ts(NOW) + 400),
                     reaction_event(2, AGENT_PK, target, "👀", created_at=ts(NOW) + 410)]
        report = env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP)})
        self.assertEqual((report["reactions_removed"], report["reactions_added"]), (1, 1))
        self.assertEqual(env.state()["r2f"][eid(0x9002)].split("|")[3], w.reactions[0]["id"])
        w.events.append(deletion_event(2, AGENT_PK, eid(0x9002), created_at=ts(NOW) + 800))
        env.round(w, now=self.at(15))
        self.assertEqual(w.reaction_set(), set())

    def test_the_cap_holds_for_withdrawals_too(self):
        """L2-1-FGS-100: 一轮最多 REACTIONS_PER_ROUND 次飞书调用，撤销也算：一批撤销超出的部分留到下一轮（游标不前进），一个不丢。"""
        w = self.world()
        total = FGS.REACTIONS_PER_ROUND + 10
        w.events += [event(eid(100 + i), BOB_PK, f"m{i}", created_at=T0 + i // 10) for i in range(total)]
        env = Env(self.tmp)
        env.round(w)
        w.events += [reaction_event(i, AGENT_PK, eid(100 + i), "👀", created_at=ts(NOW) + 20 + i // 10) for i in range(total)]
        env.round(w, now=self.at(5))
        env.round(w, now=self.at(10))
        self.assertEqual(len(w.reactions), total)
        w.events += [deletion_event(i, AGENT_PK, eid(0x9000 + i), created_at=ts(NOW) + 400) for i in range(total)]
        report = env.round(w, now=self.at(15))
        self.assertEqual(report["reactions_removed"], FGS.REACTIONS_PER_ROUND)
        self.assertEqual(len(w.reactions), total - FGS.REACTIONS_PER_ROUND)
        self.assertEqual(env.state()["react_since"], ts(self.at(10)))
        report = env.round(w, now=self.at(20))
        self.assertEqual(report["reactions_removed"], 10)
        self.assertEqual(w.reactions, [])
        self.assertEqual(env.state()["react_since"], ts(self.at(20)))

    def test_withdrawal_by_an_agent_that_left_the_config_is_settled_without_any_call(self):
        """L2-1-FGS-101: Desk 被删出配置时配置直接拒绝，不由 owner bot 处理旧表情。"""
        w, env, target, _ = self.start()
        w.events.append(reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20))
        env.round(w, now=self.at(5))
        calls = len(w.reaction_calls())
        gone = Env(self.tmp, agents={})
        w.events.append(deletion_event(1, AGENT_PK, eid(0x9001), created_at=ts(NOW) + 400))
        with self.assertRaisesRegex(FGS.GroupSyncError, "desk_pubkey"):
            gone.round(w, now=self.at(10))
        self.assertEqual(len(w.reaction_calls()), calls)
        self.assertEqual(w.reaction_set(), {("om_in1", "GLANCE", AGENT_APP)})

    def test_retry_counters_are_gone_once_a_reaction_is_settled(self):
        """L2-1-FGS-102: 重试计数只记还在进行中的：飞书拒了一两次、之后成功 → 计数清掉；拒满三次放弃 → 计数清掉，只留账本终态。
        撤销同理。（否则 attempts 里会留下永远不会再用到的条目。）"""
        w, env, target, _ = self.start()
        both = lambda prefix: {prefix + eid(0x9001): 1, prefix + eid(0x9002): 1}
        w.events += [reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 20),
                     reaction_event(2, AGENT_PK, target, "💬", created_at=ts(NOW) + 21)]
        w.reaction_fail = ["rate_limited"] * 2  # both are refused once ...
        env.round(w, now=self.at(5))
        self.assertEqual(env.state()["attempts"], both("r2f:"))
        env.round(w, now=self.at(10))  # ... and go through the second time
        self.assertEqual(len(w.reaction_set()), 2)
        self.assertEqual(env.state()["attempts"], {})
        w.events.append(reaction_event(3, AGENT_PK, target, "✅", created_at=ts(NOW) + 900))
        w.reaction_fail = ["rate_limited"] * 3  # refused for good
        for m in (20, 25, 30):
            env.round(w, now=self.at(m))
        self.assertEqual(env.state()["r2f"][eid(0x9003)], FGS.FAILED)
        self.assertEqual(env.state()["attempts"], {})
        w.events += [deletion_event(1, AGENT_PK, eid(0x9001), created_at=ts(NOW) + 2000),
                     deletion_event(2, AGENT_PK, eid(0x9002), created_at=ts(NOW) + 2001)]
        w.reaction_fail = ["rate_limited"] * 2  # both withdrawals are refused once, then go through
        env.round(w, now=self.at(35))
        self.assertEqual(env.state()["attempts"], both("r2f-del:"))
        env.round(w, now=self.at(40))
        self.assertEqual(w.reaction_set(), set())
        self.assertEqual(env.state()["attempts"], {})

    def test_a_reaction_is_synced_in_the_round_that_mirrors_its_target(self):
        """L2-1-FGS-103: reaction 和它的目标消息在同一轮里出现：这一轮就打上，不用多等一轮。两个方向都是——Buzz 里的消息被发到飞书
        （agent 早就对它打了 👀），飞书里的消息被镜像进 Buzz（agent 在镜像刚发出来时就打了 💬，镜像过程中 agent 已经在反应）。
        所以 reaction 这一步排在两个消息方向之后。"""
        w = self.world()
        w.events.append(reaction_event(1, AGENT_PK, eid(1), "👀", created_at=ts(NOW) - 30))
        w.after_buzz_send = lambda ev: w.events.append(reaction_event(2, AGENT_PK, ev["id"], "💬", created_at=ev["created_at"]))
        env = Env(self.tmp)
        report = env.round(w)
        copy = env.state()["b2f"][eid(1)]
        self.assertEqual(w.reaction_set(), {(copy, "GLANCE", AGENT_APP), ("om_in1", "Typing", AGENT_APP)})
        self.assertEqual((report["to_feishu"], report["to_buzz"], report["reactions_added"]), (1, 1, 2))

    def test_reactions_are_not_read_from_before_a_dropped_buzz_backlog(self):
        """L2-1-FGS-104: owner 用 --skip-backlog 放弃了 Buzz 积压（buzz_floor 定在当前时间）：更早的 reaction 也不再读，读取起点不早于
        buzz_floor，放弃点之前的不补打；之后新的照常同步。"""
        w, env, target, _ = self.start()
        w.events += [event(eid(200 + i), BOB_PK, f"flood {i}", created_at=ts(NOW) + 20) for i in range(250)]
        w.events.append(reaction_event(1, AGENT_PK, target, "👀", created_at=ts(NOW) + 30))
        with mock.patch.object(FGS, "BUZZ_PAGE_MAX", 1):
            report = env.round(w, now=self.at(5), skip_backlog=True)
        self.assertEqual(report["backlog_skipped"], ["buzz"])
        self.assertEqual(env.state()["buzz_floor"], ts(self.at(5)))
        self.assertEqual(w.reaction_calls(), [])
        gets = [c["args"] for c in w.buzz_calls if tuple(c["args"][:2]) == ("messages", "get")
                and c["args"][c["args"].index("--kinds") + 1] == FGS.REACTION_KINDS]
        self.assertGreaterEqual(int(gets[-1][gets[-1].index("--since") + 1]), ts(self.at(5)))
        w.events.append(reaction_event(2, AGENT_PK, target, "💬", created_at=ts(self.at(5)) + 30))
        env.round(w, now=self.at(10))
        self.assertEqual(w.reaction_set(), {("om_in1", "Typing", AGENT_APP)})

    def test_a_relay_that_returns_older_events_still_cannot_sync_pre_binding_reactions(self):
        """L2-1-FGS-105: 即使中继不守 --since、把绑定之前的 reaction 也返回了，也不同步（逐条再按绑定起点过滤，计入
        skipped.before_binding），不产生飞书调用、不进账本。"""
        w = self.world()
        w.relay_ignores_since = True
        w.events.append(reaction_event(1, AGENT_PK, eid(1), "👀", created_at=ts(NOW) - 3600))
        env = Env(self.tmp)
        report = env.round(w)
        self.assertEqual(w.reaction_calls(), [])
        self.assertEqual(report["skipped"].get("before_binding"), 1)
        self.assertEqual(env.state()["r2f"], {})

    def test_all_three_cursors_start_at_the_binding_when_the_first_round_dies_early(self):
        """L2-1-FGS-106: 首轮在读 reaction 之前就出错（Buzz 积压读不完）：state 已落盘，三个读取游标（消息两个方向 + reaction）都在绑定
        起点，没有哪个停在 0。"""
        w = self.world()
        w.events += [event(eid(200 + i), BOB_PK, f"flood {i}", created_at=T0) for i in range(250)]
        env = Env(self.tmp)
        with mock.patch.object(FGS, "BUZZ_PAGE_MAX", 1):
            with self.assertRaises(FGS.GroupSyncError):
                env.round(w)
        state = env.state()
        self.assertGreater(state["floor"], 0)
        self.assertEqual((state["buzz_since"], state["feishu_since"], state["react_since"]), (state["floor"],) * 3)



class ThreadRootUnits(unittest.TestCase):
    """话题根补发的纯函数与 buzz CLI 适配器（skills#110）。"""

    def cli(self, runner):
        return FGS.BuzzCli(BUZZ_CLI, {"BUZZ_PRIVATE_KEY": MIRROR_KEY}, runner=runner)

    def test_the_root_marker_is_read_on_its_own(self):
        """L1-FGS-300: `_buzz_root` 只认 root 标记里的合法事件 id：reply-only 没有根（要问 Buzz），格式不对的、没有标记的 e tag 不算；
        和 `_buzz_parent`（reply 优先）互不影响。"""
        self.assertEqual(FGS._buzz_root(event(eid(10), ALICE_PK, "x", tags=[("e", eid(1), "", "root"), ("e", eid(2), "", "reply")])), eid(1))
        self.assertEqual(FGS._buzz_root(event(eid(11), ALICE_PK, "x", tags=[("e", eid(1), "", "root")])), eid(1))
        self.assertIsNone(FGS._buzz_root(event(eid(12), ALICE_PK, "x", tags=[("e", eid(2), "", "reply")])))
        self.assertIsNone(FGS._buzz_root(event(eid(13), ALICE_PK, "x")))
        self.assertIsNone(FGS._buzz_root(event(eid(14), ALICE_PK, "x", tags=[("e", "abc", "", "root"), ("e", eid(2)), ("e", eid(3), "", "mention")])))
        both = event(eid(15), ALICE_PK, "x", tags=[("e", eid(1), "", "root"), ("e", eid(2), "", "reply")])
        self.assertEqual(FGS._buzz_parent(both), eid(2))

    def test_thread_root_asks_for_the_root_only_and_returns_the_top_level_event(self):
        """L1-FGS-301: `messages thread --channel <频道> --event <id> --depth-limit 0`（镜像身份的 env；0 就只要话题根，实测如此）；
        返回的数组里没有 e tag 的那一个就是根，数组里还有回复、顺序随意也一样。"""
        root = event(eid(1), ALICE_PK, "话题根")
        reply = event(eid(2), BOB_PK, "回复", tags=[("e", eid(1), "", "reply")])
        seen = []

        def runner(argv, input=None, env=None, **kw):
            seen.append((argv, env))
            return subprocess.CompletedProcess(argv, 0, json.dumps([reply, root]), "")
        self.assertEqual(self.cli(runner).thread_root(CHANNEL, eid(2)), root)
        argv, env = seen[0]
        self.assertEqual(argv, [BUZZ_CLI, "messages", "thread", "--channel", CHANNEL, "--event", eid(2), "--depth-limit", "0"])
        self.assertEqual(env, {"BUZZ_PRIVATE_KEY": MIRROR_KEY})
        self.assertEqual(self.cli(runner).thread_root(CHANNEL, eid(1), expect=eid(1)), root)

    def test_thread_root_refuses_anything_that_is_not_exactly_one_root(self):
        """L1-FGS-302: 不是数组、元素不是带合法 id 的对象、没有根或有两个根、根不是期望的那个：都报 GroupSyncError；
        buzz 自己报错（退出码 1 的 not_found 等）是 CliError。错误信息里没有正文，也没有事件 id。"""
        root = event(eid(1), ALICE_PK, "机密正文")
        other = event(eid(9), BOB_PK, "机密正文")
        reply = event(eid(2), BOB_PK, "机密正文", tags=[("e", eid(1), "", "reply")])
        bad = [{"not": "a list"}, [root, "oops"], [dict(root, id="xyz")], [reply], [root, other], []]
        for body in bad:
            with self.subTest(body=str(body)[:30]):
                runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
                with self.assertRaises(FGS.GroupSyncError) as ctx:
                    self.cli(runner).thread_root(CHANNEL, eid(2))
                self.assertNotIn("机密正文", str(ctx.exception))
                self.assertNotIn(eid(1), str(ctx.exception))
        with self.assertRaises(FGS.GroupSyncError):
            self.cli(lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps([root]), "")).thread_root(
                CHANNEL, eid(2), expect=eid(7))
        with self.assertRaises(FGS.GroupSyncError):
            self.cli(lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "not json", "")).thread_root(CHANNEL, eid(2))
        missing = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", '{"error":"not_found","message":"event x not found"}')
        with self.assertRaises(FGS.CliError) as ctx:
            self.cli(missing).thread_root(CHANNEL, eid(2))
        self.assertNotIn(eid(2), str(ctx.exception))

    def test_the_report_has_four_thread_counters_and_only_errors_need_attention(self):
        """L1-FGS-303: 报告多四个计数：thread_roots_backfilled（补发的根）、thread_root_unavailable（根发不了、回复退回顶层）、
        thread_root_failed（取话题失败的次数）、thread_roots_deferred（超过单轮上限、留到下一轮的回复）。全部从 0 起，
        它们自己不触发「需要关注」（失败会另计 errors，那个才触发）。"""
        report = FGS._new_report()
        names = ("thread_roots_backfilled", "thread_root_unavailable", "thread_root_failed", "thread_roots_deferred")
        self.assertEqual([report[n] for n in names], [0, 0, 0, 0])
        self.assertFalse(FGS.needs_attention(dict(report, **{n: 3 for n in names})))
        self.assertEqual(FGS.THREAD_ROOT_LOOKUPS_PER_ROUND, 20)


class RoundThreadRoots(TmpCase):
    """回复在飞书上没有可挂的父消息时，先把话题根补发到飞书，再把回复发在它下面（skills#110）。"""
    # AGENT2（频道里本机没配置的 agent）在这组用例里代表「发言不会镜像的作者」：ADR-0019 起缺省代发，所以这里显式写 "skip"。
    SKIP = {"buzz_unmanaged_agents": "skip"}
    OLD = ts(NOW) - 3600  # 比绑定起点（首轮时间 - 120 秒）早

    def world(self, sub=None):
        w = FakeWorld(sub or self.tmp)
        w.members = [m for m in w.members if m["pubkey"] != CAROL_PK]
        return w

    def pair(self, name):
        """另起一个世界和 state（subTest 用）。"""
        sub = self.tmp / name
        sub.mkdir(mode=0o700)
        return self.world(sub), Env(sub, **self.SKIP)

    @staticmethod
    def key_of(event_id):
        return "b2f-" + hashlib.sha256(event_id.encode()).hexdigest()[:40]

    def root(self, n, pk=ALICE_PK, content="话题根", created_at=None, **kw):
        return event(eid(n), pk, content, created_at=self.OLD if created_at is None else created_at, **kw)

    def reply(self, n, pk, content, parent, *, root=None, at=T0 + 100):
        tags = [("e", parent, "", "reply")]
        if root:
            tags.insert(0, ("e", root, "", "root"))
        return event(eid(n), pk, content, created_at=at, tags=tags)

    def sent(self, w):
        out = []
        for c in w.lark_sends():
            a = c["args"]
            out.append({"verb": a[1], "app": c["app"], "text": text_of(c), "key": a[a.index("--idempotency-key") + 1],
                        "parent": a[a.index("--message-id") + 1] if "--message-id" in a else None, "args": a, "call": c})
        return out

    def start(self, w, env=None):
        env = env or Env(self.tmp, feishu_unmapped_senders="skip", **self.SKIP)
        env.round(w)  # the binding starts here
        return env

    def go(self, env, w, minutes=2):
        return env.round(w, now=NOW + timedelta(minutes=minutes))

    def thread_args(self, w):
        return [c["args"] for c in w.thread_calls()]

    def test_a_root_that_is_already_mirrored_takes_the_reply_directly(self):
        """L2-1-FGS-300: 话题根已经在账本里（更早的一轮镜像过）：回复直接发在它下面，不问 Buzz 要话题，也不补发任何东西。"""
        w = self.world()
        w.events = [self.root(1, created_at=T0)]
        env = self.start(w)
        w.events.append(self.reply(2, BOB_PK, "回复", eid(1), at=ts(NOW) + 30))
        report = self.go(env, w)
        self.assertEqual(w.thread_calls(), [])
        sent = self.sent(w)
        self.assertEqual([s["verb"] for s in sent], ["+messages-send", "+messages-reply"])
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(1))])
        self.assertEqual((report["to_feishu"], report["thread_roots_backfilled"]), (1, 0))

    def test_a_reply_to_an_unmirrored_root_first_sends_the_root_then_the_reply_under_it(self):
        """L2-1-FGS-301: 根不在账本里、也不在这一轮读到的事件里：问 Buzz 取话题根，先像普通镜像那样把根发到飞书（同一个幂等键规则、
        同一个 owner bot），再把回复作为话题回复发在根下面；账本两条都有；回复所在的话题登记进 threads / polled，飞书里的后续回复会
        被轮询；只补这一个根，报告计数对得上。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复一", eid(1))]
        report = self.go(env, w)
        self.assertEqual(self.thread_args(w), [["messages", "thread", "--channel", CHANNEL, "--event", eid(1), "--depth-limit", "0"]])
        sent = self.sent(w)
        self.assertEqual([s["verb"] for s in sent], ["+messages-send", "+messages-reply"])
        self.assertEqual((sent[0]["text"], sent[0]["key"], sent[0]["parent"]), ("Alice（Buzz）：话题根", self.key_of(eid(1)), None))
        root_mid = w.sent_keys[self.key_of(eid(1))]
        self.assertEqual((sent[1]["text"], sent[1]["key"], sent[1]["parent"]), ("Bob（Buzz）：回复一", self.key_of(eid(2)), root_mid))
        self.assertIn("--reply-in-thread", sent[1]["args"])
        self.assertEqual([c["app"] for c in w.lark_sends()], [AGENT_APP, AGENT_APP])
        state = env.state()
        self.assertEqual(state["b2f"][eid(1)], root_mid)
        self.assertEqual(state["b2f"][eid(2)], w.sent_keys[self.key_of(eid(2))])
        self.assertIn(root_mid, state["threads"])
        self.assertIn(root_mid, state["polled"])
        self.assertEqual(state["unresolved"], {})
        self.assertEqual((report["to_feishu"], report["thread_roots_backfilled"], report["errors"]), (2, 1, 0))
        self.assertFalse(FGS.needs_attention(report))

    def test_an_agent_root_goes_out_through_its_own_bot(self):
        """L2-1-FGS-302: 补发的根和普通镜像用同一个路由：agent 写的根用它自己的 lark profile 发（正文不带署名），人写的回复仍走 owner bot。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1, AGENT_PK, "构建完成"), self.reply(2, BOB_PK, "收到", eid(1))]
        self.go(env, w)
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["app"], s["text"]) for s in sent],
                         [("+messages-send", AGENT_APP, "构建完成"), ("+messages-reply", AGENT_APP, "Bob（Buzz）：收到")])
        self.assertEqual(sent[0]["call"]["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(1))])

    def test_a_human_root_and_an_agent_reply(self):
        """L2-1-FGS-303: 反过来：人写的根走 owner bot（带「名字（Buzz）：」），agent 的回复用它自己的 profile 回复在根下面。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1, ALICE_PK, "请看下"), self.reply(2, AGENT_PK, "已处理", eid(1))]
        self.go(env, w)
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["app"], s["text"]) for s in sent],
                         [("+messages-send", AGENT_APP, "Alice（Buzz）：请看下"), ("+messages-reply", AGENT_APP, "已处理")])
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(1))])

    def test_a_mirrored_intermediate_parent_wins_over_the_root(self):
        """L2-1-FGS-304: 回复带 root 和 reply 两个标记，直接父消息（reply）已经镜像过：挂在直接父下面，不挂根；这时不再问 Buzz。
        （根是没有飞书 bot 的 agent 发的，所以直接父当初是作为顶层消息发出去的。）"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1, AGENT2_PK, "根"), self.reply(2, ALICE_PK, "中间", eid(1))]
        self.go(env, w)
        middle_mid = env.state()["b2f"][eid(2)]
        self.assertEqual(len(w.thread_calls()), 1)
        w.events.append(self.reply(3, BOB_PK, "再回复", eid(2), root=eid(1), at=ts(NOW) + 150))
        report = self.go(env, w, 4)
        self.assertEqual(len(w.thread_calls()), 1)
        last = self.sent(w)[-1]
        self.assertEqual((last["verb"], last["text"], last["parent"]), ("+messages-reply", "Bob（Buzz）：再回复", middle_mid))
        self.assertEqual(report["thread_root_unavailable"], 0)

    def test_replies_of_one_thread_fetch_and_send_the_root_once(self):
        """L2-1-FGS-305: 同一个话题的几条回复（同一轮里）：话题只取一次、根只发一次；嵌套回复（root + reply 标记）挂在已镜像的直接父下面，
        其余挂在根下面。之后同一话题的新回复直接在账本里找到根，不再取话题、不再补发。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "一", eid(1), at=T0 + 100), self.reply(3, ALICE_PK, "二", eid(1), at=T0 + 101),
                    self.reply(4, BOB_PK, "三", eid(2), root=eid(1), at=T0 + 102)]
        report = self.go(env, w)
        self.assertEqual(len(w.thread_calls()), 1)
        sent = self.sent(w)
        self.assertEqual([s["text"] for s in sent], ["Alice（Buzz）：话题根", "Bob（Buzz）：一", "Alice（Buzz）：二", "Bob（Buzz）：三"])
        mid = lambda n: w.sent_keys[self.key_of(eid(n))]
        self.assertEqual([s["parent"] for s in sent], [None, mid(1), mid(1), mid(2)])
        self.assertEqual((report["to_feishu"], report["thread_roots_backfilled"]), (4, 1))
        w.events.append(self.reply(5, ALICE_PK, "四", eid(1), at=ts(NOW) + 200))
        report = self.go(env, w, 5)
        self.assertEqual(len(w.thread_calls()), 1)
        self.assertEqual((self.sent(w)[-1]["parent"], report["thread_roots_backfilled"]), (mid(1), 0))
        self.assertEqual(len(self.sent(w)), 5)

    def test_a_root_that_cannot_be_mirrored_lets_the_reply_go_out_at_top_level(self):
        """L2-1-FGS-306: 根本来就不会镜像（agent 没有飞书 bot、作者不是频道里的人、镜像身份自己的回声、不是消息类事件、空内容）：
        回复退回现在的行为——作为顶层消息发出，不丢；报告里 thread_root_unavailable 计一次、跳过原因照样进 skipped；不算错误。"""
        cases = [("agent_bot_unavailable", AGENT2_PK, "根", 9), ("not_channel_human", OUTSIDER_PK, "根", 9),
                 ("echo", MIRROR_PK, "根", 9), ("kind", ALICE_PK, "根", 1), ("empty", ALICE_PK, "  ", 9)]
        for i, (reason, pk, content, kind) in enumerate(cases):
            with self.subTest(reason=reason):
                w, env = self.pair(f"case{i}")
                self.start(w, env)
                w.events = [self.root(1, pk, content, kind=kind), self.reply(2, BOB_PK, "回复", eid(1))]
                report = self.go(env, w)
                sent = self.sent(w)
                self.assertEqual([(s["verb"], s["text"], s["parent"]) for s in sent], [("+messages-send", "Bob（Buzz）：回复", None)])
                self.assertEqual((report["thread_root_unavailable"], report["skipped"].get(reason)), (1, 1))
                self.assertEqual((report["errors"], report["failed"], report["unknown"], report["to_feishu"]), (0, 0, 0, 1))
                self.assertFalse(FGS.needs_attention(report))
                state = env.state()
                self.assertNotIn(eid(1), state["b2f"])
                self.assertEqual(state["b2f"][eid(2)], w.sent_keys[self.key_of(eid(2))])
                self.assertEqual(state["unresolved"], {})

    def test_a_root_refused_three_times_is_given_up_and_the_reply_goes_out_at_top_level(self):
        """L2-1-FGS-307: 根被飞书明确拒绝：回复等着，根每轮按同一个幂等键重试；连续 3 次被拒后根记 failed，回复在这一轮退回顶层发出，
        群里只有这一条回复；根没有发出去。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
        w.lark_send_fail = ["rate_limited"] * 3
        reports = [self.go(env, w, 2 + i) for i in range(4)]
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["key"], s["text"]) for s in sent],
                         [("+messages-send", self.key_of(eid(1)), "Alice（Buzz）：话题根")] * 3
                         + [("+messages-send", self.key_of(eid(2)), "Bob（Buzz）：回复")])
        self.assertEqual([m["content"] for m in w.messages], ["Bob（Buzz）：回复"])
        self.assertEqual(len(w.thread_calls()), 3)
        self.assertEqual([r["errors"] for r in reports], [1, 1, 1, 0])
        self.assertEqual([r["failed"] for r in reports], [0, 0, 1, 0])
        self.assertEqual([r["to_feishu"] for r in reports], [0, 0, 1, 0])
        self.assertEqual(sum(r["thread_root_unavailable"] for r in reports), 1)
        state = env.state()
        self.assertEqual(state["b2f"][eid(1)], FGS.FAILED)
        self.assertEqual(state["b2f"][eid(2)], w.sent_keys[self.key_of(eid(2))])

    def test_a_root_refused_once_holds_the_reply_and_both_go_out_next_round(self):
        """L2-1-FGS-308: 根被拒一次：这一轮回复不发（不当成顶层），下一轮根用同一个键重发成功，回复接着发在根下面。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
        w.lark_send_fail = ["rate_limited"]
        first = self.go(env, w)
        self.assertEqual([m["content"] for m in w.messages] + list(w.threads), [])
        self.assertEqual((first["errors"], first["to_feishu"]), (1, 0))
        state = env.state()
        self.assertNotIn(eid(2), state["b2f"])
        self.assertIn(eid(2), state["unresolved"])
        second = self.go(env, w, 3)
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["key"]) for s in sent], [("+messages-send", self.key_of(eid(1)))] * 2
                         + [("+messages-reply", self.key_of(eid(2)))])
        self.assertEqual(sent[2]["parent"], w.sent_keys[self.key_of(eid(1))])
        self.assertEqual((second["to_feishu"], second["failed"], second["thread_root_unavailable"]), (2, 0, 0))
        self.assertEqual(env.state()["unresolved"], {})

    def test_a_root_with_an_unknown_outcome_holds_the_reply_and_is_retried_under_the_same_key(self):
        """L2-1-FGS-309: 根发出去结果不确定（超时、或送达了但 lark-cli 报网络错误）：回复必须等，不能先当顶层发；下一轮根按同一个键
        重试（飞书不会发出第二条），回复接着发在这一条根下面；根和回复在飞书里各只有一条。"""
        for i, mode in enumerate(("timeout", "network_envelope")):
            with self.subTest(mode=mode):
                w, env = self.pair(f"mode{i}")
                self.start(w, env)
                w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
                w.lark_send_fail = [mode]
                first = self.go(env, w)
                self.assertEqual(first["unknown"], 1)
                self.assertEqual(list(w.threads), [])
                self.assertNotIn("Bob（Buzz）：回复", [m["content"] for m in w.messages])
                self.assertEqual([s["text"] for s in self.sent(w)], ["Alice（Buzz）：话题根"])
                state = env.state()
                self.assertTrue(state["b2f"][eid(1)].startswith(FGS.PENDING))
                self.assertNotIn(eid(2), state["b2f"])
                self.assertIn(eid(2), state["unresolved"])
                self.go(env, w, 3)
                sent = self.sent(w)
                self.assertEqual([(s["verb"], s["key"]) for s in sent], [("+messages-send", self.key_of(eid(1)))] * 2
                                 + [("+messages-reply", self.key_of(eid(2)))])
                root_mid = w.sent_keys[self.key_of(eid(1))]
                self.assertEqual(sent[2]["parent"], root_mid)
                self.assertEqual([m["content"] for m in w.messages], ["Alice（Buzz）：话题根"])
                self.assertEqual([m["content"] for m in w.threads[root_mid]], ["Bob（Buzz）：回复"])
                self.assertEqual(env.state()["b2f"][eid(1)], root_mid)

    def test_a_root_that_stays_unknown_past_the_window_lets_the_reply_go_out_at_top_level(self):
        """L2-1-FGS-310: 根一直结果不确定：从首次尝试起 45 分钟内每轮同一个键重试，回复一直等；超过以后根记 unknown（报告一次，之后不再试），
        回复这时才退回顶层发出，只发一次。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
        w.lark_send_fail = ["timeout"] * 10
        for i in range(10):
            self.go(env, w, 2 + 5 * i)
            self.assertEqual([m["content"] for m in w.messages], [], i)
        self.go(env, w, 2 + 50)
        state = env.state()
        self.assertEqual(state["b2f"].get(eid(1)), FGS.UNKNOWN)
        self.assertEqual([m["content"] for m in w.messages], ["Bob（Buzz）：回复"])
        sent_before = len(self.sent(w))
        self.go(env, w, 2 + 55)
        self.assertEqual(len(self.sent(w)), sent_before)
        self.assertEqual(state["unresolved"], {})

    def test_a_failing_thread_read_holds_the_reply_and_recovers(self):
        """L2-1-FGS-311: 取话题失败（buzz 报错）：这一轮回复不发、记一次 errors 和 thread_root_failed，不动账本；下一轮取到了，
        根和回复照常发出；回复自己的取话题重试计数清掉。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
        w.thread_fail = ["network"]
        first = self.go(env, w)
        self.assertEqual((first["errors"], first["thread_root_failed"], first["to_feishu"]), (1, 1, 0))
        self.assertEqual(self.sent(w), [])
        self.assertTrue(FGS.needs_attention(first))
        self.assertIn(eid(2), env.state()["unresolved"])
        second = self.go(env, w, 3)
        self.assertEqual([s["verb"] for s in self.sent(w)], ["+messages-send", "+messages-reply"])
        self.assertEqual((second["to_feishu"], second["thread_roots_backfilled"], second["errors"]), (2, 1, 0))
        state = env.state()
        self.assertEqual(state["unresolved"], {})
        self.assertEqual([k for k in state["attempts"] if "thread" in k], [])

    def test_a_thread_read_that_keeps_failing_is_given_up_after_three_tries(self):
        """L2-1-FGS-312: 连续 3 次取不到话题（网络错误、话题根找不到、返回的不是数组）：第 3 次之后回复退回顶层发出，只发一次；
        之后不再取；thread_root_failed 每次失败计一次（共 3），退回顶层不另计 thread_root_unavailable。"""
        for i, mode in enumerate(("network", "not_found", "garbage")):
            with self.subTest(mode=mode):
                w, env = self.pair(f"mode{i}")
                self.start(w, env)
                w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
                w.thread_fail = [mode] * 3
                reports = [self.go(env, w, 2 + k) for k in range(4)]
                self.assertEqual([r["thread_root_failed"] for r in reports], [1, 1, 1, 0])
                self.assertEqual([r["to_feishu"] for r in reports], [0, 0, 1, 0])
                self.assertEqual([(s["verb"], s["text"], s["parent"]) for s in self.sent(w)],
                                 [("+messages-send", "Bob（Buzz）：回复", None)])
                self.assertEqual(len(w.thread_calls()), 3)
                self.assertEqual(sum(r["thread_root_unavailable"] for r in reports), 0)
                self.assertEqual(env.state()["unresolved"], {})

    def test_a_fallback_to_top_level_stays_top_level_when_it_is_retried(self):
        """L2-1-FGS-313: 回复退回顶层发出后结果不确定：重试仍是顶层、同一个键，不再重新取话题，也不改成回复（飞书的幂等按接口计）。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1, AGENT2_PK, "根"), self.reply(2, BOB_PK, "回复", eid(1))]
        w.lark_send_fail = ["timeout"]
        self.go(env, w)
        self.assertEqual(env.state()["b2f"][eid(2)], f"pending:{ts(NOW + timedelta(minutes=2))}:-")
        self.go(env, w, 3)
        self.assertEqual([(s["verb"], s["key"]) for s in self.sent(w)], [("+messages-send", self.key_of(eid(2)))] * 2)
        self.assertEqual(len(w.thread_calls()), 1)
        self.assertEqual([m["content"] for m in w.messages], ["Bob（Buzz）：回复"])

    def test_only_a_bounded_number_of_threads_are_read_per_round_and_nothing_is_lost(self):
        """L2-1-FGS-314: 一轮最多为 THREAD_ROOT_LOOKUPS_PER_ROUND 个话题取根；超出的回复这一轮不发、留到下一轮（thread_roots_deferred 计数），
        这一轮的消息游标不前进；下一轮补完。每个根、每条回复都只发一次，一条不丢。"""
        limit, total = FGS.THREAD_ROOT_LOOKUPS_PER_ROUND, FGS.THREAD_ROOT_LOOKUPS_PER_ROUND + 5
        w = self.world()
        env = self.start(w)
        cursor = env.state()["buzz_since"]
        w.events = [self.root(1000 + i, content=f"根{i}") for i in range(total)]
        w.events += [self.reply(2000 + i, BOB_PK, f"回复{i}", eid(1000 + i)) for i in range(total)]
        first = self.go(env, w)
        self.assertEqual(len(w.thread_calls()), limit)
        self.assertEqual((first["to_feishu"], first["thread_roots_deferred"], first["thread_roots_backfilled"]), (2 * limit, 5, limit))
        state = env.state()
        self.assertEqual(state["buzz_since"], cursor)
        self.assertEqual(sorted(k for k in state["unresolved"] if int(k, 16) >= 2000), [eid(2000 + i) for i in range(limit, total)])
        second = self.go(env, w, 3)
        self.assertEqual((second["to_feishu"], second["thread_roots_deferred"]), (10, 0))
        self.assertEqual(len(w.thread_calls()), total)
        texts = [s["text"] for s in self.sent(w)]
        self.assertEqual(len(texts), 2 * total)
        self.assertEqual(len(set(texts)), 2 * total)
        self.assertEqual(env.state()["buzz_since"], ts(NOW + timedelta(minutes=3)))
        self.assertEqual(env.state()["unresolved"], {})
        roots = {w.sent_keys[self.key_of(eid(1000 + i))] for i in range(total)}
        self.assertEqual({s["parent"] for s in self.sent(w) if s["parent"]}, roots)

    def test_a_root_older_than_the_binding_is_still_backfilled(self):
        """L2-1-FGS-315: 根比绑定起点早得多（三天前）也补发：这是给回复补上下文，不是回填历史——只有有新回复的话题的根才会补；
        它自己不算 before_binding 跳过；同一话题里没人回复的其他旧消息一条也不补。"""
        w = self.world()
        env = self.start(w)
        floor = env.state()["floor"]
        w.events = [self.root(1, created_at=ts(NOW) - 3 * 86400), self.root(2, content="没人回复的旧消息", created_at=ts(NOW) - 86400),
                    self.reply(3, BOB_PK, "回复", eid(1))]
        report = self.go(env, w)
        self.assertLess(w.events[0]["created_at"], floor)
        self.assertEqual([s["text"] for s in self.sent(w)], ["Alice（Buzz）：话题根", "Bob（Buzz）：回复"])
        self.assertNotIn("before_binding", report["skipped"])
        self.assertNotIn(eid(2), env.state()["b2f"])
        self.assertEqual(env.state()["floor"], floor)

    def test_state_written_before_this_change_keeps_working(self):
        """L2-1-FGS-316: 旧版未记录发送应用的未决回复停止重试，新回复仍补发根并进入话题。"""
        legacy = ["binding", "floor", "buzz_since", "feishu_since", "buzz_floor", "feishu_floor", "b2f", "f2b", "attempts", "threads",
                  "polled", "tried", "unresolved", "f_unresolved", "r2f", "react_since", "idmap", "emailmap",
                  "images", "img_unresolved",  # 这两个是图片同步加的，话题根补发本身没有新增字段
                  "b2f_modes", "b2f_senders", "e2f", "edit_unresolved",  # 编辑同步与发送身份账本
                  # ADR-0020 的成员快照与表情账本：旧 state 缺它们时按「还没记过基线」读入（L1-FGS-802）
                  "members_synced", "feishu_seen", "buzz_seen", "member_notes", "member_events", "member_event_stream",
                  "member_event_seq", "member_event_blocks", "people_seen", "rwatch", "f2r",
                  # 成员同步失败状态的 Buzz 根、飞书 fallback、最近正文和 active 标志（L1-FGS-051B）
                  "member_notice_event", "member_notice_feishu", "member_notice_sender", "member_notice_content", "member_notice_active",
                  # Agent 入群介绍：升级基线和每个 binding 只发一次的幂等账本
                  "agent_intros_initialized", "agent_intros", "agent_intro_senders"]
        w = self.world()
        env = self.start(w)
        state = env.state()
        self.assertEqual(sorted(state), sorted(legacy))
        self.assertEqual(sorted(FGS.State.__dataclass_fields__), sorted(legacy))
        old_reply = self.reply(2, BOB_PK, "旧回复", eid(1), at=ts(NOW) + 20)
        state["b2f"][eid(2)] = f"pending:{ts(NOW)}:-"
        state["unresolved"][eid(2)] = ts(NOW) + 20
        write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
        w.events = [self.root(1), old_reply, self.reply(3, ALICE_PK, "新回复", eid(1), at=ts(NOW) + 30)]
        self.go(env, w)
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["text"]) for s in sent],
                         [("+messages-send", "Alice（Buzz）：话题根"),
                          ("+messages-reply", "Alice（Buzz）：新回复")])
        self.assertEqual(len(w.thread_calls()), 1)
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(1))])
        self.assertEqual(env.state()["b2f"][eid(2)], FGS.UNKNOWN)

    def test_reports_and_stderr_carry_no_message_text_or_event_ids(self):
        """L2-1-FGS-317: 取话题失败也好、补发根成功也好：报告只有计数，stderr 里没有正文，也没有事件 id。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1, content="机密正文-根"), self.reply(2, BOB_PK, "机密正文-回复", eid(1))]
        w.thread_fail = ["network"]
        argv = ["round", "--config", str(env.config), "--state-dir", str(env.state_dir)]
        for minutes in (2, 3):
            out, err = io.StringIO(), io.StringIO()
            w.clock, w.sends = NOW + timedelta(minutes=minutes), 0
            with contextlib.redirect_stderr(err):
                self.assertEqual(FGS.main(argv, base_env=env.base_env, runner=w, stdout=out, now=w.clock, http=w.http_get),
                                 FGS.EXIT_ATTENTION if minutes == 2 else FGS.EXIT_OK)
            report = json.loads(out.getvalue())
            self.assertEqual(set(report), set(FGS._new_report()))
            for text in (out.getvalue(), err.getvalue()):
                self.assertNotIn("机密正文", text)
                self.assertNotIn(eid(1), text)
                self.assertNotIn(eid(2), text)
        self.assertEqual([s["text"] for s in self.sent(w)], ["Alice（Buzz）：机密正文-根", "Bob（Buzz）：机密正文-回复"])

    def test_a_reaction_on_a_backfilled_root_finds_its_target(self):
        """L2-1-FGS-318: 补发的根进了账本，所以 agent 打在它上面的 reaction 能找到目标：同一轮里就落到飞书里那条根上（GLANCE），
        不再是 reaction_target_unmirrored。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1)),
                    reaction_event(1, AGENT_PK, eid(1), "👀", created_at=ts(NOW) + 60)]
        report = self.go(env, w)
        root_mid = w.sent_keys.get(self.key_of(eid(1)))
        self.assertIsNotNone(root_mid)
        self.assertEqual(w.reaction_set(), {(root_mid, "GLANCE", AGENT_APP)})
        self.assertNotIn("reaction_target_unmirrored", report["skipped"])
        self.assertEqual(report["reactions_added"], 1)

    def test_a_reply_that_is_skipped_anyway_costs_no_thread_read(self):
        """L2-1-FGS-319: 回复自己就不会镜像（作者不是频道里的人、agent 没有飞书 bot、绑定之前的）：不为它取话题，也不补发根。"""
        w = self.world()
        w.relay_ignores_since = True
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, OUTSIDER_PK, "外人的回复", eid(1)), self.reply(3, AGENT2_PK, "没 bot 的回复", eid(1)),
                    self.reply(4, BOB_PK, "太早的回复", eid(1), at=self.OLD + 5)]
        report = self.go(env, w)
        self.assertEqual(w.thread_calls(), [])
        self.assertEqual(self.sent(w), [])
        self.assertEqual(report["skipped"], {"not_channel_human": 1, "agent_bot_unavailable": 1, "before_binding": 2})

    def test_a_root_in_the_same_window_goes_first_even_when_the_ids_sort_the_other_way(self):
        """L2-1-FGS-320: 根和回复在同一秒、回复的 id 排在前面：读到的事件里就有根，先发根再发回复，不为它取话题，根只发一次，
        回复不会被当成顶层。"""
        w = self.world()
        w.events = [event(eid(0x50), ALICE_PK, "话题根", created_at=T0),
                    event(eid(0x10), BOB_PK, "回复", created_at=T0, tags=[("e", eid(0x50), "", "reply")])]
        env = Env(self.tmp, **self.SKIP)
        report = env.round(w)
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["text"]) for s in sent], [("+messages-send", "Alice（Buzz）：话题根"), ("+messages-reply", "Bob（Buzz）：回复")])
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(0x50))])
        self.assertEqual(w.thread_calls(), [])
        self.assertEqual((report["to_feishu"], report["thread_roots_backfilled"]), (2, 0))
        env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual(len(self.sent(w)), 2)

    def test_a_reply_only_to_a_reply_finds_the_top_of_the_thread_through_buzz(self):
        """L2-1-FGS-321: 回复只有 reply 标记、直接父消息自己也是回复（而且没镜像）：问 Buzz 取直接父所在的话题，得到根，先发根再把回复
        挂在根下面。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, AGENT2_PK, "没 bot 的中间回复", eid(1), at=T0 + 50), self.reply(3, BOB_PK, "回复", eid(2))]
        report = self.go(env, w)
        self.assertEqual(self.thread_args(w), [["messages", "thread", "--channel", CHANNEL, "--event", eid(2), "--depth-limit", "0"]])
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["text"]) for s in sent], [("+messages-send", "Alice（Buzz）：话题根"), ("+messages-reply", "Bob（Buzz）：回复")])
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(1))])
        self.assertEqual(report["skipped"], {"agent_bot_unavailable": 1})

    def test_the_root_is_on_disk_as_pending_before_it_is_sent(self):
        """L2-1-FGS-322: 补发的根和普通消息一样：发送之前 pending 标记（带首次尝试时间）已经落盘，进程在发送后被杀也知道它可能已发出。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
        seen = {}

        def check(kind, key):
            state = json.loads((env.state_dir / FGS.STATE_FILE).read_text())
            seen[key] = (state["b2f"].get(eid(1)), state["b2f"].get(eid(2)))
        w.before_send = check
        self.go(env, w)
        self.assertEqual(set(seen), {self.key_of(eid(1)), self.key_of(eid(2))})
        root_seen, reply_seen = seen[self.key_of(eid(1))], seen[self.key_of(eid(2))]
        self.assertEqual(root_seen[0], f"pending:{ts(NOW + timedelta(minutes=2))}:-")
        self.assertIsNone(root_seen[1])
        self.assertEqual(reply_seen[1], f"pending:{ts(NOW + timedelta(minutes=2))}:{w.sent_keys[self.key_of(eid(1))]}")

    def test_the_thread_is_read_with_the_mirror_identity_only(self):
        """L2-1-FGS-323: 取话题用镜像身份读（和读频道消息一样），owner 的 key 和其他 token 不进子进程；参数里没有正文。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1, content="机密正文"), self.reply(2, BOB_PK, "回复", eid(1))]
        self.go(env, w)
        calls = w.thread_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["key"], MIRROR_KEY)
        self.assertNotIn("GITLAB_TOKEN", calls[0]["env"])
        self.assertNotIn("机密正文", " ".join(calls[0]["args"]))


    def test_a_reply_that_names_its_root_asks_for_the_root_itself(self):
        """L2-1-FGS-324: 回复自己带 root 标记时，取话题就问根本身（`--event <根 id>`），不绕道直接父；直接父没镜像（agent 没有飞书 bot），
        根补发出去以后回复挂在根下面。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, AGENT2_PK, "没 bot 的中间回复", eid(1), at=T0 + 50),
                    self.reply(3, BOB_PK, "嵌套回复", eid(2), root=eid(1))]
        self.go(env, w)
        self.assertEqual(self.thread_args(w), [["messages", "thread", "--channel", CHANNEL, "--event", eid(1), "--depth-limit", "0"]])
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["text"]) for s in sent],
                         [("+messages-send", "Alice（Buzz）：话题根"), ("+messages-reply", "Bob（Buzz）：嵌套回复")])
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(1))])

    def test_a_mirrored_root_takes_the_reply_when_the_direct_parent_has_no_copy(self):
        """L2-1-FGS-325: 直接父没有飞书副本、但回复自己的 root 标记指向的根已经在账本里：直接挂在根下面，不问 Buzz 要话题，不算退回顶层。"""
        w = self.world()
        w.events = [self.root(1, created_at=T0)]
        env = self.start(w)
        root_mid = env.state()["b2f"][eid(1)]
        w.events += [self.reply(2, AGENT2_PK, "没 bot 的中间回复", eid(1), at=ts(NOW) + 20),
                     self.reply(3, BOB_PK, "嵌套回复", eid(2), root=eid(1), at=ts(NOW) + 30)]
        report = self.go(env, w)
        self.assertEqual(w.thread_calls(), [])
        last = self.sent(w)[-1]
        self.assertEqual((last["verb"], last["text"], last["parent"]), ("+messages-reply", "Bob（Buzz）：嵌套回复", root_mid))
        self.assertEqual((report["thread_root_unavailable"], report["thread_roots_backfilled"]), (0, 0))

    def test_a_chain_read_in_one_window_in_the_wrong_order_still_sends_the_root_first_and_once(self):
        """L2-1-FGS-326: 根、没镜像的中间回复、只有 reply 标记的回复在同一秒读到，id 顺序正好相反（回复最先处理）：为它问 Buzz 取话题
        （`--event <中间回复>`）得到根，根就在这一轮读到的事件里，所以直接用读到的这份、挪到回复前面，只发一次；不算补发（没有读旧事件）。"""
        w = self.world()
        w.events = [event(eid(0x30), ALICE_PK, "话题根", created_at=T0),
                    event(eid(0x20), AGENT2_PK, "没 bot 的中间回复", created_at=T0, tags=[("e", eid(0x30), "", "reply")]),
                    event(eid(0x10), BOB_PK, "回复", created_at=T0, tags=[("e", eid(0x20), "", "reply")])]
        env = Env(self.tmp, **self.SKIP)
        report = env.round(w)
        self.assertEqual(self.thread_args(w), [["messages", "thread", "--channel", CHANNEL, "--event", eid(0x20), "--depth-limit", "0"]])
        sent = self.sent(w)
        self.assertEqual([(s["verb"], s["text"]) for s in sent],
                         [("+messages-send", "Alice（Buzz）：话题根"), ("+messages-reply", "Bob（Buzz）：回复")])
        self.assertEqual(sent[1]["parent"], w.sent_keys[self.key_of(eid(0x30))])
        self.assertEqual((report["to_feishu"], report["thread_roots_backfilled"]), (2, 0))

    def test_a_root_read_in_the_same_window_is_not_sent_twice_when_its_outcome_is_unknown(self):
        """L2-1-FGS-327: 根和回复在同一秒、回复排在前面，先发根、结果不确定（超时）：这一轮根只发一次（不会因为它还排在队列后面再发一遍），
        回复等着，不当顶层发。"""
        w = self.world()
        w.events = [event(eid(0x50), ALICE_PK, "话题根", created_at=T0),
                    event(eid(0x10), BOB_PK, "回复", created_at=T0, tags=[("e", eid(0x50), "", "reply")])]
        w.lark_send_fail = ["timeout"]
        report = Env(self.tmp, **self.SKIP).round(w)
        self.assertEqual([(s["verb"], s["text"]) for s in self.sent(w)], [("+messages-send", "Alice（Buzz）：话题根")])
        self.assertEqual((report["unknown"], report["to_feishu"]), (1, 0))

    def test_replies_of_a_thread_that_cannot_be_read_share_one_failed_read(self):
        """L2-1-FGS-328: 同一个话题的两条回复、话题读不到：这一轮只读一次（失败也算），errors 和 thread_root_failed 各一次；两条回复各自等着，
        各记一次重试。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "一", eid(1), at=T0 + 100), self.reply(3, ALICE_PK, "二", eid(1), at=T0 + 101)]
        w.thread_fail = ["network", "network"]
        report = self.go(env, w)
        self.assertEqual(len(w.thread_calls()), 1)
        self.assertEqual((report["errors"], report["thread_root_failed"], report["to_feishu"]), (1, 1, 0))
        state = env.state()
        self.assertEqual({k: v for k, v in state["attempts"].items() if "thread" in k},
                         {"b2f-thread:" + eid(2): 1, "b2f-thread:" + eid(3): 1})
        self.assertEqual(sorted(state["unresolved"]), [eid(2), eid(3)])

    def test_a_thread_that_answers_with_another_root_than_the_one_named_is_a_failed_read(self):
        """L2-1-FGS-329: 回复点名了根，Buzz 返回的话题根却是另一条：不信、不补发那一条，按取话题失败处理（等、最多 3 次、再退回顶层）。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.root(2, content="别的根"), self.reply(3, BOB_PK, "回复", eid(1), root=eid(1))]
        w.thread_tamper = lambda events: [e for e in w.events if e["id"] == eid(2)]
        reports = [self.go(env, w, 2 + k) for k in range(3)]
        self.assertEqual([r["thread_root_failed"] for r in reports], [1, 1, 1])
        self.assertEqual([(s["verb"], s["text"], s["parent"]) for s in self.sent(w)], [("+messages-send", "Bob（Buzz）：回复", None)])
        self.assertNotIn(eid(2), env.state()["b2f"])

    def test_the_read_counter_is_cleared_when_the_reply_goes_out_alone_with_an_unknown_outcome(self):
        """L2-1-FGS-330: 取话题失败 3 次后回复退回顶层发出，这次发送结果不确定：重试计数已经清掉（不留 b2f-thread 项），重试仍是顶层、不再取话题。"""
        w = self.world()
        env = self.start(w)
        w.events = [self.root(1), self.reply(2, BOB_PK, "回复", eid(1))]
        w.thread_fail = ["network"] * 3
        w.lark_send_fail = ["timeout"]
        for k in range(3):
            self.go(env, w, 2 + k)
        state = env.state()
        self.assertEqual([k for k in state["attempts"] if "thread" in k], [])
        self.assertTrue(state["b2f"][eid(2)].startswith(FGS.PENDING))
        self.go(env, w, 6)
        self.assertEqual(len(w.thread_calls()), 3)
        self.assertEqual([m["content"] for m in w.messages], ["Bob（Buzz）：回复"])


# ================================ 图片同步 S1：纯函数（魔数、imeta、markdown、文件读取） ================================


class ImageSniffing(unittest.TestCase):
    def test_the_format_comes_from_the_magic_number_alone(self):
        """L1-FGS-113: 图片格式只看文件头的魔数（jpeg / png / gif87a·89a / webp / bmp / tiff 两种字节序）；文件名、声明的类型都不算。
        svg（能带脚本）、html、pdf、别的 RIFF（wav / avi）、被截断的头、BMP 魔数后面不是合法 DIB 头的，都不是图片。"""
        for data, kind in [(JPEG_PLAIN, "jpeg"), (PNG_PLAIN, "png"), (GIF_PLAIN, "gif"), (_gif(version=b"87a"), "gif"),
                           (WEBP_PLAIN, "webp"), (BMP_PLAIN, "bmp"), (TIFF_PLAIN, "tiff"), (b"MM\x00*" + bytes(8), "tiff")]:
            self.assertEqual(FGS.sniff_image(data), kind, data[:8])
        for data in (b"", b"\xff\xd8", b"\xff\xd8\x00", SVG, b"<!doctype html><html></html>", b"%PDF-1.7\n",
                     b"RIFF\x04\x00\x00\x00WAVE", b"RIFF\x04\x00\x00\x00AVI ", b"RIFF", b"BM" + bytes(40), b"GIF90a" + bytes(20),
                     PNG_SIGNATURE[:6], b"II*", b"MZ\x90\x00", b"\x00" * 64):
            self.assertIsNone(FGS.sniff_image(data), data[:12])


class ImageAttachments(unittest.TestCase):
    A, B, C = "ab" * 32, "cd" * 32, "ef" * 32

    def images(self, *tags):
        return FGS.event_images(event(eid(1), AGENT_PK, "x", tags=tags))

    def test_an_imeta_tag_becomes_a_relay_media_reference(self):
        """L1-FGS-114: imeta（NIP-92：每个字段是「键 值」字符串）里的 url 必须是 <http(s)://主机>/media/<64 位小写 hex>[.扩展名]，
        取 <hex>[.扩展名] 作为 `buzz media get` 的参数；x 如果有必须与 url 里的 sha256 一致；size 只是声明（不是数字就当没有）。
        每个 imeta 一项、按 tag 顺序；同一个 sha256 只留第一次；不认的形状（外部地址、缩略图、带查询、大写、x 对不上、没有 url）
        各占一项、原因是 bad_imeta，声明超过 10 MB 的是 too_large（不去下载）。url 原样收集起来，给正文里的 markdown 去重用。"""
        A, B = self.A, self.B
        images, over, urls = self.images(imeta_tag(A), imeta_tag(B, ".png", size=99), ["p", A])
        self.assertEqual(images, (FGS.ImageRef(f"{A}.jpg", A, 1234), FGS.ImageRef(f"{B}.png", B, 99)))
        self.assertEqual((over, urls), (0, frozenset({f"{MEDIA_ORIGIN}/media/{A}.jpg", f"{MEDIA_ORIGIN}/media/{B}.png"})))
        self.assertEqual(self.images(imeta_tag(A, ""))[0], (FGS.ImageRef(A, A, 1234),))
        self.assertEqual(self.images(imeta_tag(A, x=None, size="lots"))[0], (FGS.ImageRef(f"{A}.jpg", A, None),))
        self.assertEqual(self.images(imeta_tag(A, size="-5"))[0], (FGS.ImageRef(f"{A}.jpg", A, None),))
        bad = {
            "external host, not media": imeta_tag(A, url="https://example.test/pic.jpg"),
            "thumbnail": imeta_tag(A, url=f"{MEDIA_ORIGIN}/media/{A}.thumb.jpg"),
            "query": imeta_tag(A, url=f"{MEDIA_ORIGIN}/media/{A}.jpg?x=1"),
            "upper case": imeta_tag(A, url=f"{MEDIA_ORIGIN}/media/{A.upper()}.jpg"),
            "short hash": imeta_tag(A, url=f"{MEDIA_ORIGIN}/media/abcd.jpg"),
            "not http": imeta_tag(A, url=f"ftp://relay.test/media/{A}.jpg"),
            "path traversal": imeta_tag(A, url=f"{MEDIA_ORIGIN}/media/../media/{A}.jpg"),
            "x is another hash": imeta_tag(A, x=B),
            "x is not a hash": imeta_tag(A, x="zz"),
            "no url": ["imeta", "m image/jpeg", f"x {A}"],
            "nothing": ["imeta"],
            "not strings": ["imeta", 5, None],
        }
        for name, tag in bad.items():
            self.assertEqual(self.images(tag)[0], ("bad_imeta",), name)
        self.assertEqual(self.images(imeta_tag(A, size=FGS.IMAGE_MAX_BYTES + 1))[0], ("too_large",))
        self.assertEqual(self.images(imeta_tag(A, size=FGS.IMAGE_MAX_BYTES))[0], (FGS.ImageRef(f"{A}.jpg", A, FGS.IMAGE_MAX_BYTES),))
        images, _, urls = self.images(imeta_tag(A, url="https://example.test/pic.jpg"), imeta_tag(B))
        self.assertEqual(images, ("bad_imeta", FGS.ImageRef(f"{B}.jpg", B, 1234)))
        self.assertIn("https://example.test/pic.jpg", urls)
        same = self.images(imeta_tag(A), imeta_tag(A, ".png"), imeta_tag(B))[0]
        self.assertEqual(same, (FGS.ImageRef(f"{A}.jpg", A, 1234), FGS.ImageRef(f"{B}.jpg", B, 1234)))
        self.assertEqual(self.images()[0], ())
        self.assertEqual(FGS.event_images(dict(event(eid(1), AGENT_PK, "x"), tags=None))[0], ())

    def test_at_most_nine_attachments_are_kept_and_the_rest_is_counted(self):
        """L1-FGS-115: 一个事件最多 9 项（去重之后、含不认的形状），多出来的只计数（over），不在列表里。"""
        tags = [imeta_tag(f"{i:064x}") for i in range(12)]
        images, over, _ = self.images(*tags)
        self.assertEqual((len(images), over), (FGS.IMAGES_PER_EVENT, 3))
        self.assertEqual(FGS.IMAGES_PER_EVENT, 9)
        self.assertEqual([i.sha256 for i in images], [f"{i:064x}" for i in range(9)])
        images, over, _ = self.images(*tags[:9])
        self.assertEqual((len(images), over), (9, 0))


class ImageRouting(unittest.TestCase):
    """正文里的图片：imeta 附件转成图片（正文里对应的 markdown 去掉），别的 markdown 图片语法不再原样发成一串文字。"""

    A, B = "ab" * 32, "cd" * 32

    def route(self, ev, **kw):
        args = dict(mirror_pubkey=MIRROR_PK, agent_apps={AGENT_PK: AGENT_APP},
                    human_pubkeys={OWNER_PK, ALICE_PK, BOB_PK}, agent_pubkeys={AGENT_PK, AGENT2_PK},
                    names={ALICE_PK: "Alice", AGENT_PK: "helper-agent"},
                    mention_targets={ALICE_PK: (ALICE_OPEN, "Alice"), AGENT_PK: (AGENT_BOT_MEMBER, "helper-agent")})
        args.update(kw)
        return FGS.route_buzz_event(ev, **args)

    def test_attachments_are_sent_as_images_and_their_markdown_leaves_the_text(self):
        """L1-FGS-116: Buzz CLI 发带附件的消息时自己在正文后面加 `![image](url)`（实测）：这些行与 imeta 重复，从文字里去掉（图作为图发），
        文字只剩说明；发送身份不变（agent 用自己的 bot、人用 owner bot 并带署名）；附件按 imeta 顺序放进 images。"""
        A, B = self.A, self.B
        agent = self.route(image_event(1, AGENT_PK, "证据图：页面 https://neopace.com/ ", [A, B]))
        self.assertEqual((agent.via_app_id, agent.text), (AGENT_APP, "证据图：页面 https://neopace.com/"))
        self.assertEqual(agent.images, (FGS.ImageRef(f"{A}.jpg", A, 1234), FGS.ImageRef(f"{B}.jpg", B, 1234)))
        human = self.route(image_event(2, ALICE_PK, "看这个", [A]))
        self.assertEqual((human.via_app_id, human.text), (None, "Alice（Buzz）：看这个"))
        self.assertEqual(human.images, (FGS.ImageRef(f"{A}.jpg", A, 1234),))
        self.assertEqual(agent.images_over, 0)

    def test_markdown_in_the_middle_of_a_text_closes_the_gap_it_leaves(self):
        """L1-FGS-117: 去掉了附件的图片行以后，文字里三个以上的连续换行收成一个空行，首尾空白去掉；一个附件的图片行都没去掉的文字，
        一个字符都不动（末尾空白、连续空行照旧）。"""
        A = self.A
        url = f"{MEDIA_ORIGIN}/media/{A}.jpg"
        ev = event(eid(1), AGENT_PK, f"第一段\n\n![image]({url})\n\n第二段\n\n\n\n第三段  \n", tags=[imeta_tag(A)])
        self.assertEqual(self.route(ev).text, "第一段\n\n第二段\n\n第三段")
        plain = event(eid(2), AGENT_PK, "a\n\n\n\nb  \n", tags=[])
        self.assertEqual(self.route(plain).text, "a\n\n\n\nb  \n")
        elsewhere = event(eid(3), AGENT_PK, f"![image](https://example.test/x.png)\n\n\n\n![image]({url})", tags=[imeta_tag(A)])
        self.assertEqual(self.route(elsewhere).text, "[图片] https://example.test/x.png")

    def test_markdown_images_that_are_not_attachments_become_a_readable_marker(self):
        """L1-FGS-118: 没有对应 imeta 的 `![alt](url)`（agent 手写的、外部图片）不下载（不去抓任意地址），也不原样发成一串 markdown：
        改成「[图片：alt] 地址」（没有 alt、或 alt 只是 CLI 自己的占位 image 就「[图片] 地址」）；地址不是 http(s)（data:、相对路径）只留「[图片]」，不带地址。
        转换发生在中和之前：alt 里手写的 <at …> 一样被中和，不会变成飞书里的真 @。"""
        out = self.route(event(eid(1), AGENT_PK, "见\n![图表](https://example.test/chart.png)\n![](https://example.test/b.png)\n"
                                                 "![x](data:image/png;base64,AAAA) ![y](a/b.png)"))
        self.assertEqual(out.text, "见\n[图片：图表] https://example.test/chart.png\n[图片] https://example.test/b.png\n[图片：x] [图片：y]")
        self.assertEqual(out.images, ())
        raw = self.route(event(eid(2), AGENT_PK, '![<at user_id="all">x</at>](https://example.test/c.png)'))
        self.assertNotIn("<at", raw.text.lower())
        titled = self.route(event(eid(3), AGENT_PK, '![t](https://example.test/t.png "标题")'))
        self.assertEqual(titled.text, "[图片：t] https://example.test/t.png")

    def test_an_attachment_with_no_caption_still_says_who_sent_it(self):
        """L1-FGS-119: 只有图片、没有说明文字时，文字消息是占位「[图片]」（人的仍带署名）：文字消息是这个事件在飞书里的「本体」（回复话题、
        账本都挂在它上面），图作为它之后的图片消息发出。图片形状全都不认（bad_imeta）时也一样，原因留在 images 里。没有文字也没有附件仍是 empty。"""
        A = self.A
        self.assertEqual(self.route(image_event(1, AGENT_PK, "", [A])).text, "[图片]")
        self.assertEqual(self.route(image_event(2, ALICE_PK, "", [A])).text, "Alice（Buzz）：[图片]")
        broken = self.route(event(eid(3), AGENT_PK, "", tags=[["imeta", "m image/jpeg"]]))
        self.assertEqual((broken.text, broken.images), ("[图片]", ("bad_imeta",)))
        self.assertEqual(self.route(event(eid(4), AGENT_PK, "  \n", tags=[["p", ALICE_PK]])), "empty")

    def test_skips_are_decided_before_any_image_is_looked_at(self):
        """L1-FGS-120: 回声、别的 kind、没有 bot 的 agent、不在频道里的作者照旧原样跳过，不因为带了图片而改变。"""
        A = self.A
        self.assertEqual(self.route(image_event(1, MIRROR_PK, "x", [A])), "echo")
        self.assertEqual(self.route(dict(image_event(2, ALICE_PK, "x", [A]), kind=40008)), "kind")
        self.assertEqual(self.route(image_event(3, AGENT2_PK, "x", [A])), "agent_bot_unavailable")
        self.assertEqual(self.route(image_event(4, OUTSIDER_PK, "x", [A])), "not_channel_human")

    def test_more_than_nine_attachments_are_counted_in_the_outbound(self):
        """L1-FGS-121: 超过 9 个附件时 Outbound.images 只有 9 项，images_over 是多出来的个数。"""
        out = self.route(image_event(1, AGENT_PK, "x", [f"{i:064x}" for i in range(11)]))
        self.assertEqual((len(out.images), out.images_over), (9, 2))


class ImageFiles(TmpCase):
    def write(self, name, data):
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_a_downloaded_file_is_read_bounded_and_checked_by_its_content(self):
        """L1-FGS-122: 下载下来的文件按内容判断：魔数认不出（html、svg、空文件、改了扩展名的 exe）是 not_image；超过 10 MB 是 too_large
        （刚好等于上限可以）；格式不在允许集合里是 unsupported_format；符号链接、目录、不存在的路径是 unreadable，不跟随。"""
        allowed = FGS.IMAGE_FEISHU_FORMATS
        self.assertEqual(FGS.read_image(self.write("a.jpg", JPEG_PLAIN), allowed=allowed), (JPEG_PLAIN, "jpeg"))
        self.assertEqual(FGS.read_image(self.write("no-extension", PNG_PLAIN), allowed=allowed), (PNG_PLAIN, "png"))
        for name, data, reason in [("h.jpg", b"<html>hi</html>", "not_image"), ("s.png", SVG, "not_image"), ("e.jpg", b"", "not_image"),
                                   ("x.jpg", b"MZ\x90\x00" + bytes(100), "not_image")]:
            with self.assertRaises(FGS.ImageSkip, msg=name) as ctx:
                FGS.read_image(self.write(name, data), allowed=allowed)
            self.assertEqual(ctx.exception.reason, reason, name)
        with self.assertRaises(FGS.ImageSkip) as ctx:
            FGS.read_image(self.write("b.png", PNG_PLAIN), allowed=frozenset({"jpeg"}))
        self.assertEqual(ctx.exception.reason, "unsupported_format")
        with mock.patch.object(FGS, "IMAGE_MAX_BYTES", len(JPEG_PLAIN)):
            self.assertEqual(FGS.read_image(self.write("edge.jpg", JPEG_PLAIN), allowed=allowed)[1], "jpeg")
            with self.assertRaises(FGS.ImageSkip) as ctx:
                FGS.read_image(self.write("big.jpg", JPEG_PLAIN + b"\x00"), allowed=allowed)
            self.assertEqual(ctx.exception.reason, "too_large")
        link = self.tmp / "link.jpg"
        link.symlink_to(self.tmp / "a.jpg")
        (self.tmp / "dir.jpg").mkdir()
        for path in (link, self.tmp / "dir.jpg", self.tmp / "missing.jpg"):
            with self.assertRaises(FGS.ImageSkip, msg=path.name) as ctx:
                FGS.read_image(path, allowed=allowed)
            self.assertEqual(ctx.exception.reason, "unreadable", path.name)

    def test_a_huge_file_is_refused_without_reading_it(self):
        """L1-FGS-123: 一个比上限大得多的文件（稀疏的 3 GB）直接判 too_large，不把它读进内存（峰值内存远小于上限）。"""
        import tracemalloc
        path = self.tmp / "huge.jpg"
        with open(path, "wb") as fh:
            fh.write(JPEG_PLAIN)
            fh.truncate(3 * 1024 ** 3)
        tracemalloc.start()
        try:
            with self.assertRaises(FGS.ImageSkip) as ctx:
                FGS.read_image(path, allowed=FGS.IMAGE_FEISHU_FORMATS)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(ctx.exception.reason, "too_large")
        self.assertLess(peak, FGS.IMAGE_MAX_BYTES)


class ImageReport(unittest.TestCase):
    def test_a_lost_image_needs_attention_but_a_policy_skip_does_not(self):
        """L1-FGS-124: 报告里图片有四个字段：images_to_feishu / images_to_buzz（发出去的张数）、images_failed（下载或发送失败、放弃）、
        images_skipped（按原因计数：超过张数、太大、格式不支持……这是策略，不是故障）。images_failed 非零要人看一眼（退出码 3）——图是
        报告的重点，丢了不能悄悄；images_skipped 和发出去的张数不触发。"""
        report = FGS._new_report()
        self.assertEqual({k: report.get(k, "缺") for k in ("images_to_feishu", "images_to_buzz", "images_failed", "images_skipped")},
                         {"images_to_feishu": 0, "images_to_buzz": 0, "images_failed": 0, "images_skipped": {}})
        self.assertFalse(FGS.needs_attention(dict(report, images_to_feishu=3, images_to_buzz=2, images_skipped={"too_large": 1})))
        self.assertTrue(FGS.needs_attention(dict(report, images_failed=1)))


# ================================ 图片同步 S2：Buzz → 飞书（一轮里的行为） ================================


def photo(n):
    """一张与别的都不同的 jpeg（中继上的一个 blob）。"""
    return _jpeg(JFIF, DQT, scan=bytes([n, 0x11, 0x22]) + b"\xff\xd9")


class ImagesToFeishu(TmpCase):
    """Buzz 事件的图片附件：先发文字（照旧），再用同一个身份把每张图作为图片消息发出，话题里的事件图也进同一个话题。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    def put(self, data):
        sha = sha_of(data)
        self.w.media[sha] = data
        return sha

    def image_messages(self):
        every = [*self.w.messages, *[m for replies in self.w.threads.values() for m in replies]]
        return [m for m in every if m["msg_type"] == "image"]

    def assert_clean(self):
        """一轮结束后，所有临时目录都不在了（发完立刻删）。"""
        self.assertTrue(self.w.workdirs)
        for path in self.w.workdirs:
            self.assertFalse(os.path.exists(path), path)

    def test_an_agents_image_follows_its_caption_through_its_own_bot(self):
        """L2-1-FGS-152: agent 的事件带一张图：先发文字（说明，去掉与 imeta 重复的 markdown），再用**同一个 agent 的 bot** 把图作为图片消息发到群里。
        图用镜像身份（不是 owner 的 key）经 `buzz media get <sha256>.<ext>` 下载；发送用相对当前目录的文件名（lark-cli 拒绝绝对路径）；
        幂等键是 b2f-img-<sha256(事件 id:序号)[:36]>（≤ 50 字符）；账本记这张图的飞书消息 id，没有悬挂的未决项。"""
        w = self.w
        sha = self.put(photo(1))
        w.events = [image_event(1, AGENT_PK, "证据图：页面 https://neopace.com/", [sha])]
        report = self.env.round(w)
        self.assertEqual(len(w.lark_sends()), 2)
        text, image = w.lark_sends()
        self.assertEqual(text_of(text), "证据图：页面 https://neopace.com/")
        self.assertEqual((image["app"], image["as"], image["args"][:2]), (AGENT_APP, "bot", ["im", "+messages-send"]))
        self.assertEqual(image["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        self.assertEqual(image["args"][image["args"].index("--chat-id") + 1], CHAT)
        self.assertNotIn("--text", image["args"])
        [up] = w.image_sends
        self.assertEqual((up["sha"], up["size"], up["target"], up["app"]), (sha, len(photo(1)), ("chat", CHAT), AGENT_APP))
        self.assertEqual(up["key"], "b2f-img-" + hashlib.sha256(f"{eid(1)}:0".encode()).hexdigest()[:36])
        self.assertLessEqual(len(up["key"]), 50)
        self.assertRegex(up["name"], r"^[a-z0-9._-]+\.jpg$")
        self.assertEqual([(r["segment"], r["key"]) for r in w.media_reads], [(f"{sha}.jpg", MIRROR_KEY)])
        self.assertEqual((report["to_feishu"], report["images_to_feishu"], report["images_failed"], report["images_skipped"],
                          report["errors"], report["unknown"]), (1, 1, 0, {}, 0, 0))
        self.assertFalse(FGS.needs_attention(report))
        state = self.env.state()
        self.assertEqual(state["images"], {f"{eid(1)}:0": up["message_id"], f"{eid(1)}:thread": "-"})  # 文字发在哪里（"-"：没有话题）也记着
        self.assertEqual(state["img_unresolved"], {})
        self.assertEqual(len(self.image_messages()), 1)
        self.assert_clean()

    def test_a_humans_image_goes_through_the_owner_bot_and_the_caption_carries_the_signature(self):
        """L2-1-FGS-153: 人的事件：说明文字带「名字（Buzz）：」署名由 owner 应用 bot 发；图也由 owner bot 发（不带 agent 的目录），图本身不需要署名。"""
        w = self.w
        sha = self.put(photo(2))
        w.events = [image_event(1, ALICE_PK, "看这个", [sha])]
        self.env.round(w)
        self.assertEqual(len(w.lark_sends()), 2)
        text, image = w.lark_sends()
        self.assertEqual(text_of(text), "Alice（Buzz）：看这个")
        self.assertEqual((image["app"], image["as"]), (AGENT_APP, "bot"))
        self.assertEqual(image["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(self.tmp / "agent-cfg"))
        self.assertEqual(w.image_sends[0]["app"], AGENT_APP)

    def test_an_image_of_a_reply_goes_into_the_same_thread(self):
        """L2-1-FGS-154: 文字是话题回复时，图也是回复（同一个父消息、--reply-in-thread），落在同一个话题里；话题登记为要轮询的。"""
        w = self.w
        sha = self.put(photo(3))
        w.events = [event(eid(1), ALICE_PK, "root"),
                    image_event(2, AGENT_PK, "证据", [sha], created_at=T0 + 1, tags=[("e", eid(1), "", "reply")])]
        self.env.round(w)
        root_mid = next(m["message_id"] for m in w.messages if "root" in m["content"])
        replies = [c for c in w.lark_sends() if c["args"][1] == "+messages-reply"]
        self.assertEqual(len(replies), 2)
        reply_text, reply_image = replies
        self.assertEqual(text_of(reply_text), "证据")
        self.assertEqual(reply_image["args"][reply_image["args"].index("--message-id") + 1], root_mid)
        self.assertIn("--reply-in-thread", reply_image["args"])
        self.assertEqual(reply_image["app"], AGENT_APP)
        self.assertEqual(w.image_sends[0]["target"], ("reply", root_mid))
        self.assertEqual([m["msg_type"] for m in w.threads[root_mid]], ["text", "image"])
        self.assertIn(root_mid, self.env.state()["threads"])

    def test_several_images_go_in_order_each_under_its_own_key_and_never_twice(self):
        """L2-1-FGS-155: 两张图按 imeta 顺序发出，各有自己的幂等键；之后每一轮（含还在 900 秒重读窗口里的）都不再下载、不再发文字或图。"""
        w = self.w
        a, b = self.put(photo(4)), self.put(photo(5))
        w.events = [image_event(1, AGENT_PK, "两张", [a, b])]
        self.env.round(w)
        self.assertEqual([u["sha"] for u in w.image_sends], [a, b])
        self.assertEqual(len({u["key"] for u in w.image_sends}), 2)
        self.assertEqual(sorted(self.env.state()["images"]), [f"{eid(1)}:0", f"{eid(1)}:1", f"{eid(1)}:thread"])
        sends, reads = len(w.lark_sends()), len(w.media_reads)
        for minutes in (1, 3, 20):
            report = self.env.round(w, now=NOW + timedelta(minutes=minutes))
            self.assertEqual((len(w.lark_sends()), len(w.media_reads)), (sends, reads))
            self.assertEqual((report["to_feishu"], report["images_to_feishu"]), (0, 0))
        self.assertEqual(len(self.image_messages()), 2)

    def test_the_uploaded_file_gets_a_generated_name_and_the_extension_of_what_it_really_is(self):
        """L2-1-FGS-156: 上传的文件名是脚本生成的（不来自事件里的任何字段），扩展名按魔数认出的格式，不信 url 或 m 声明的：
        url 写 .jpg、字节其实是 png，上传的就是 .png。"""
        w = self.w
        sha = self.put(PNG_PLAIN)
        w.events = [image_event(1, AGENT_PK, "其实是 png", [sha], ext=".jpg")]
        self.env.round(w)
        self.assertEqual([r["segment"] for r in w.media_reads], [f"{sha}.jpg"])
        self.assertRegex(w.image_sends[0]["name"], r"^[a-z0-9._-]+\.png$")

    def test_the_temporary_directory_is_private_and_removed_even_when_the_send_fails(self):
        """L2-1-FGS-157: 下载和发送用的目录是 0700 的临时目录，发送时（lark-cli 的 cwd）已经是它；发送被拒也一样清理；账本里没有任何本地路径。"""
        w = self.w
        sha = self.put(photo(6))
        w.events = [image_event(1, AGENT_PK, "会被拒", [sha])]
        w.lark_send_fail = [None, "rate_limited"]  # 文字成功、图被拒
        report = self.env.round(w)
        self.assertEqual([u["cwd_mode"] for u in w.image_sends], [])  # 被拒发生在上传之前
        self.assertEqual(report["errors"], 1)
        self.assert_clean()
        w.lark_send_fail = []
        self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual([u["cwd_mode"] for u in w.image_sends], [0o700])
        self.assert_clean()
        self.assertNotIn(str(self.tmp), (self.env.state_dir / FGS.STATE_FILE).read_text())

    def test_markdown_that_is_not_an_attachment_is_text_and_fetches_nothing(self):
        """L2-1-FGS-158: 没有 imeta 的 `![alt](https://…)` 只变成文字里的「[图片：alt] 地址」，不下载、不发图片（不去抓任意地址）。"""
        w = self.w
        w.events = [event(eid(1), AGENT_PK, "见 ![图表](https://example.test/chart.png)")]
        self.env.round(w)
        self.assertEqual([text_of(c) for c in w.lark_sends()], ["见 [图片：图表] https://example.test/chart.png"])
        self.assertEqual((w.media_reads, w.image_sends), ([], []))

    def test_an_event_with_only_an_image_still_sends_a_body_and_then_the_image(self):
        """L2-1-FGS-159: 只有图片的事件：文字消息是占位「[图片]」（人的带署名），图跟在后面。"""
        w = self.w
        w.events = [image_event(1, AGENT_PK, "", [self.put(photo(7))]),
                    image_event(2, ALICE_PK, "", [self.put(photo(8))], created_at=T0 + 1)]
        self.env.round(w)
        self.assertEqual([text_of(c) for c in w.lark_sends() if "--text" in c["args"]], ["[图片]", "Alice（Buzz）：[图片]"])
        self.assertEqual(len(self.image_messages()), 2)

    def test_images_of_events_that_are_not_delivered_are_never_downloaded(self):
        """L2-1-FGS-160: 事件本身不投递（没有飞书 bot 的 agent、已离开频道的人、镜像身份自己发的回声）时，它的图片一张也不下载、不发。"""
        w = self.w
        env = Env(self.tmp, buzz_unmanaged_agents="skip")  # the agent case needs the old default: ADR-0019 relays it
        w.events = [image_event(1, AGENT2_PK, "没有 bot", [self.put(photo(9))]),
                    image_event(2, OUTSIDER_PK, "不在频道里", [self.put(photo(10))], created_at=T0 + 1),
                    image_event(3, MIRROR_PK, "[飞书] 张三：回声", [self.put(photo(11))], created_at=T0 + 2)]
        report = env.round(w)
        self.assertEqual((w.lark_sends(), w.media_reads, w.image_sends), ([], [], []))
        self.assertEqual(report["skipped"], {"agent_bot_unavailable": 1, "not_channel_human": 1, "echo": 1})
        self.assertEqual((env.state()["images"], report["images_skipped"], report["images_to_feishu"]), ({}, {}, 0))


# ================================ 图片同步 S3：Buzz → 飞书的失败、重试、跳过与状态兼容 ================================


class ImagesToFeishuFailures(TmpCase):
    """一张图出问题（下载、校验、发送）只影响它自己：文字和别的图照发，不会造成文字重发；每种结局只计数一次。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    put = ImagesToFeishu.put
    image_messages = ImagesToFeishu.image_messages

    def item(self, n=1, i=0):
        return f"{eid(n)}:{i}"

    def test_images_that_policy_refuses_are_counted_once_by_reason_and_never_block_the_rest(self):
        """L2-1-FGS-161: 不该发的图按原因计数、只计一次：声明超过 10 MB（根本不下载）、下载后超限、内容不是图片（html、svg——不看 m 与扩展名）、
        字节的 sha256 不是事件写的那个 blob、imeta 形状不认。文字和好的那张照发；账本记 skipped，之后的每一轮既不重新下载也不重新计数；
        这是策略不是故障，不触发「需要关注」。"""
        w = self.w
        good = self.put(photo(20))
        html_sha, svg_sha, swapped, big, declared = sha_of(b"h"), sha_of(b"s"), "ab" * 32, sha_of(b"b"), sha_of(b"d")
        w.media.update({html_sha: b"<html>login required</html>", svg_sha: SVG, swapped: photo(21), big: photo(22) + bytes(400)})
        w.media[declared] = photo(23)
        w.events = [event(eid(1), AGENT_PK, "一张好的，六张不该发的", tags=[
            imeta_tag(good, size=100), imeta_tag(html_sha, size=100), imeta_tag(svg_sha, size=100), imeta_tag(swapped, size=100),
            imeta_tag(big, size=50), imeta_tag(declared, size=301), ["imeta", "m image/jpeg"]])]
        with mock.patch.object(FGS, "IMAGE_MAX_BYTES", 300):
            report = self.env.round(w)
            for minutes in (1, 20):
                again = self.env.round(w, now=NOW + timedelta(minutes=minutes))
                self.assertEqual((again["images_skipped"], again["images_to_feishu"], again["images_failed"]), ({}, 0, 0))
        self.assertEqual(report["images_skipped"], {"not_image": 2, "hash_mismatch": 1, "too_large": 2, "bad_imeta": 1})
        self.assertEqual((report["images_to_feishu"], report["images_failed"], report["errors"], report["to_feishu"]), (1, 0, 0, 1))
        self.assertFalse(FGS.needs_attention(report))
        self.assertEqual(sorted(r["segment"] for r in w.media_reads), sorted(f"{x}.jpg" for x in (good, html_sha, svg_sha, swapped, big)))
        ledger = self.env.state()["images"]
        self.assertEqual([ledger[self.item(1, i)] for i in range(7)], [w.image_sends[0]["message_id"]] + ["skipped"] * 6)
        self.assertEqual(self.env.state()["img_unresolved"], {})
        self.assertEqual(len(w.image_sends), 1)

    def test_more_than_nine_attachments_send_nine_and_count_the_rest_once(self):
        """L2-1-FGS-162: 一个事件 11 张图：前 9 张发出，多出的 2 张计入 images_skipped.over_limit，只计一次（账本记这件事），后面的轮次不再计。"""
        w = self.w
        shas = [self.put(photo(30 + i)) for i in range(11)]
        w.events = [image_event(1, AGENT_PK, "很多张", shas)]
        report = self.env.round(w)
        self.assertEqual((report["images_to_feishu"], report["images_skipped"]), (9, {"over_limit": 2}))
        self.assertEqual([u["sha"] for u in w.image_sends], shas[:9])
        again = self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((again["images_to_feishu"], again["images_skipped"]), (0, {}))
        self.assertEqual(len(w.media_reads), 9)

    def test_a_download_that_fails_is_retried_and_given_up_after_three_tries_without_touching_the_text(self):
        """L2-1-FGS-163: relay 暂时取不到（网络错误、超时）：这张图下一轮重试，别的图和文字照发、文字不重发；连续 3 次都不行就放弃，
        images_failed 加一（需要关注）；成功的那次之后不再有任何动作。"""
        w = self.w
        a, b = self.put(photo(40)), self.put(photo(41))
        w.events = [image_event(1, AGENT_PK, "两张", [a, b])]
        w.media_fail = [None, "network", "timeout", "network"]  # a 顺利；b 依次失败三次
        first = self.env.round(w)
        self.assertEqual((first["images_to_feishu"], first["errors"], first["images_failed"], first["to_feishu"]), (1, 1, 0, 1))
        self.assertTrue(FGS._is_retry(self.env.state()["images"][self.item(1, 1)]))
        self.env.round(w, now=NOW + timedelta(minutes=1))
        last = self.env.round(w, now=NOW + timedelta(minutes=2))
        self.assertEqual((last["images_failed"], last["errors"]), (1, 1))
        self.assertTrue(FGS.needs_attention(last))
        self.assertEqual(self.env.state()["images"][self.item(1, 1)], "failed")
        self.assertEqual(self.env.state()["img_unresolved"], {})
        self.env.round(w, now=NOW + timedelta(minutes=3))
        self.assertEqual((len(w.media_reads), len(self.image_messages()), len([c for c in w.lark_sends() if "--text" in c["args"]])), (1, 1, 1))

    def test_a_download_that_recovers_delivers_the_missing_image_only(self):
        """L2-1-FGS-164: 第二次尝试成功：只补这一张（同一个幂等键、发到同一个地方），文字与第一张不重发，账本记消息 id，没有遗留的未决项。"""
        w = self.w
        a, b = self.put(photo(42)), self.put(photo(43))
        w.events = [image_event(1, AGENT_PK, "两张", [a, b])]
        w.media_fail = [None, "network"]
        self.env.round(w)
        self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual([u["sha"] for u in w.image_sends], [a, b])
        self.assertEqual(len(w.lark_sends()), 3)
        state = self.env.state()
        self.assertEqual(state["images"][self.item(1, 1)], w.image_sends[1]["message_id"])
        self.assertEqual((state["img_unresolved"], state["attempts"]), ({}, {}))

    def test_an_image_send_of_unknown_outcome_is_retried_under_the_same_key_and_never_doubled(self):
        """L2-1-FGS-165: 发送超时、或飞书已经发出但 lark-cli 报网络错误（结果不确定）：不当成失败，下一轮同一个键同一个接口重试，
        飞书按键去重，所以群里只有一张图；每次不确定计入 unknown。"""
        for mode in ("timeout", "network_envelope"):
            sub = self.tmp / mode
            sub.mkdir(mode=0o700)
            w, env = FakeWorld(sub), Env(sub)
            sha = sha_of(photo(50))
            w.media[sha] = photo(50)
            w.events = [image_event(1, AGENT_PK, "x", [sha])]
            w.lark_send_fail = [None, mode]
            first = env.round(w)
            self.assertEqual((first["unknown"], first["images_to_feishu"], first["images_failed"]), (1, 0, 0), mode)
            self.assertTrue(FGS._is_pending(env.state()["images"][self.item(1, 0)]), mode)
            second = env.round(w, now=NOW + timedelta(minutes=1))
            self.assertEqual((second["images_to_feishu"], second["unknown"]), (1, 0), mode)
            self.assertEqual(len({u["key"] for u in w.image_sends}), 1, mode)
            self.assertEqual(len([m for m in w.messages if m["msg_type"] == "image"]), 1, mode)
            self.assertEqual(len([c for c in w.lark_sends() if "--text" in c["args"]]), 1, mode)

    def test_a_pending_image_is_still_read_after_its_event_leaves_the_overlap_window(self):
        """L2-1-FGS-166: 图片一直是「结果不确定」时，事件早已超出 900 秒的重读窗口也要继续读到它（未决的图把读取起点往回拉，和未决的文字一样），
        飞书恢复以后补发成功，仍然只有一张。"""
        w = self.w
        sha = self.put(photo(51))
        w.events = [image_event(1, AGENT_PK, "慢", [sha])]
        w.lark_send_fail = [None] + ["timeout"] * 9  # 文字成功；之后每轮的图都超时，直到 16 分钟时的那一轮
        for minutes in range(0, 18, 2):
            self.env.round(w, now=NOW + timedelta(minutes=minutes))
        self.assertEqual(len(self.image_messages()), 0)
        last = self.env.round(w, now=NOW + timedelta(minutes=18))  # 事件 T0 已经在 NOW-60，读取起点 = 16 分钟 - 900 秒 > T0
        self.assertEqual((last["images_to_feishu"], len(self.image_messages())), (1, 1))
        self.assertEqual(self.env.state()["img_unresolved"], {})

    def test_an_image_open_for_longer_than_the_idempotency_window_is_closed_not_resent(self):
        """L2-1-FGS-167: 和文字一样：结果不确定的图超过首次尝试起 45 分钟，记 unknown（报告一次，永不重发）；被拒后等待重试的图超过 45 分钟，
        记 failed（images_failed 加一）。之后不再有任何上传或发送。"""
        for mode, ledger, counter in (("timeout", "unknown", "unknown"), ("rate_limited", "failed", "images_failed")):
            sub = self.tmp / mode
            sub.mkdir(mode=0o700)
            w, env = FakeWorld(sub), Env(sub)
            sha = sha_of(photo(52))
            w.media[sha] = photo(52)
            w.events = [image_event(1, AGENT_PK, "x", [sha])]
            w.lark_send_fail = [None, mode]
            env.round(w)
            uploads = len(w.image_sends)
            report = env.round(w, now=NOW + timedelta(minutes=50))
            self.assertEqual(env.state()["images"][self.item(1, 0)], ledger, mode)
            self.assertEqual(report[counter], 1, mode)
            self.assertEqual(env.state()["img_unresolved"], {}, mode)
            later = env.round(w, now=NOW + timedelta(minutes=52))
            self.assertEqual((len(w.image_sends), later[counter]), (uploads, 0), mode)
            self.assertEqual(len([m for m in w.messages if m["msg_type"] == "image"]), 0, mode)

    def test_open_images_of_an_event_that_can_no_longer_be_read_are_closed_after_six_hours(self):
        """L2-1-FGS-168: 事件已经读不到（被删了）而图还悬着：6 小时后和别的未决项一样关掉，只报告一次——结果不确定的记 unknown，被拒等重试的记 failed。"""
        for mode, ledger, counter in (("timeout", "unknown", "unknown"), ("rate_limited", "failed", "images_failed")):
            sub = self.tmp / mode
            sub.mkdir(mode=0o700)
            w, env = FakeWorld(sub), Env(sub)
            sha = sha_of(photo(53))
            w.media[sha] = photo(53)
            w.events = [image_event(1, AGENT_PK, "x", [sha])]
            w.lark_send_fail = [None, mode]
            env.round(w)
            w.events = []
            report = env.round(w, now=NOW + timedelta(hours=7))
            self.assertEqual((env.state()["images"][self.item(1, 0)], report[counter]), (ledger, 1), mode)
            self.assertEqual(env.state()["img_unresolved"], {}, mode)

    def test_when_the_text_never_went_out_the_images_are_not_sent_either(self):
        """L2-1-FGS-169: 文字被拒三次放弃（failed）的事件，图不发（没有说明、人的图没有署名）：不下载，放弃文字的那一轮就计入
        images_skipped.text_not_sent，账本记 skipped，只计一次。"""
        w = self.w
        sha = self.put(photo(54))
        w.events = [image_event(1, AGENT_PK, "文字发不出去", [sha])]
        w.lark_send_fail = ["rate_limited"] * 3
        for minutes in range(3):
            report = self.env.round(w, now=NOW + timedelta(minutes=minutes))
        self.assertEqual(report["images_skipped"], {"text_not_sent": 1})  # 放弃文字的那一轮就计，不等下一次读到
        again = self.env.round(w, now=NOW + timedelta(minutes=3))
        self.assertEqual(again["images_skipped"], {})
        self.assertEqual(self.env.state()["images"], {self.item(1, 0): "skipped"})
        self.assertEqual((w.media_reads, w.image_sends), ([], []))

    def test_an_image_left_over_when_the_sender_is_gone_is_skipped_once(self):
        """L2-1-FGS-170: Desk profile 失效时整轮停止，未发图片不借 owner bot 补发。"""
        w = self.w
        sha = self.put(photo(55))
        w.events = [image_event(1, AGENT_PK, "x", [sha])]
        w.media_fail = ["network"]
        self.env.round(w)
        w.agent_dirs[str(self.tmp / "agent-cfg")] = "cli_somethingelse001"
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((len(w.media_reads), w.image_sends), (0, []))

    def test_dropping_a_buzz_backlog_on_purpose_closes_open_images_too(self):
        """L2-1-FGS-171: `--skip-backlog` 丢弃 Buzz 积压时，悬着的图和悬着的文字一样关掉：结果不确定的记 unknown，等重试的记 failed，
        不再被读取、不再上传。"""
        w = self.w
        sha = self.put(photo(56))
        w.events = [image_event(1, AGENT_PK, "x", [sha])]
        w.lark_send_fail = [None, "timeout"]
        self.env.round(w)
        later = NOW + timedelta(hours=1)
        w.events += [event(eid(2000 + i), ALICE_PK, f"x{i}", created_at=ts(later) - 800 + i) for i in range(401)]
        uploads = len(w.image_sends)
        with mock.patch.object(FGS, "BUZZ_PAGE_MAX", 2):
            with self.assertRaises(FGS.GroupSyncError):
                self.env.round(w, now=later)
            report = self.env.round(w, now=later, skip_backlog=True)
        self.assertEqual(report["backlog_skipped"], ["buzz"])
        self.assertEqual(self.env.state()["images"][self.item(1, 0)], "unknown")
        self.assertEqual(self.env.state()["img_unresolved"], {})
        self.assertEqual(len(w.image_sends), uploads)

    def test_a_state_written_before_images_still_works(self):
        """L2-1-FGS-172: 升级前写的 state（没有 images 和 img_unresolved）照常读取、照常同步，之后写出的 state 有这两个字段。"""
        w = self.w
        w.events = [event(eid(1), ALICE_PK, "before")]
        self.env.round(w)
        path = self.env.state_dir / FGS.STATE_FILE
        legacy = {k: v for k, v in json.loads(path.read_text()).items() if k not in ("images", "img_unresolved")}
        write_owner_only(path, json.dumps(legacy))
        sha = self.put(photo(57))
        w.events.append(image_event(2, AGENT_PK, "after", [sha], created_at=T0 + 30))
        report = self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((report["to_feishu"], report["images_to_feishu"], report["errors"]), (1, 1, 0))
        self.assertEqual(set(self.env.state()["images"]), {self.item(2, 0), f"{eid(2)}:thread"})
        self.assertIn("img_unresolved", self.env.state())


class ImageStateLedger(TmpCase):
    def test_the_state_has_an_image_ledger_and_old_states_still_load(self):
        """L1-FGS-125: state 新增 images（`<事件 id>:<序号>` → 飞书消息 id / pending / retry / failed / unknown / skipped）与 img_unresolved
        （同样的键 → 事件时间）。升级前写的 state（两个字段都没有，或连 r2f、idmap 也没有）照常读取、账本为空；只缺一个、类型不对、多出未知字段
        都按「关闭失败」拒绝；images 和别的账本一样有界。"""
        state = FGS.State(binding="b", images={f"{eid(1)}:0": "om_1", f"{eid(1)}:1": "skipped"}, img_unresolved={f"{eid(2)}:0": 1700000000})
        FGS.save_state(self.tmp, state)
        path = self.tmp / FGS.STATE_FILE
        self.assertEqual(FGS.load_state(self.tmp), state)
        full = json.loads(path.read_text())
        legacy = {k: v for k, v in full.items() if k not in ("images", "img_unresolved")}
        write_owner_only(path, json.dumps(legacy))
        loaded = FGS.load_state(self.tmp)
        self.assertEqual((loaded.images, loaded.img_unresolved), ({}, {}))
        oldest = {k: v for k, v in legacy.items() if k not in ("r2f", "react_since", "idmap", "emailmap")}
        write_owner_only(path, json.dumps(oldest))
        loaded = FGS.load_state(self.tmp)
        self.assertEqual((loaded.images, loaded.img_unresolved, loaded.r2f), ({}, {}, {}))
        bad = {
            "only images": {k: v for k, v in full.items() if k != "img_unresolved"},
            "only img_unresolved": {k: v for k, v in full.items() if k != "images"},
            "images is a list": dict(full, images=[]),
            "images value not a string": dict(full, images={f"{eid(1)}:0": 5}),
            "img_unresolved is a list": dict(full, img_unresolved=[]),
            "img_unresolved value not an int": dict(full, img_unresolved={f"{eid(2)}:0": "1"}),
            "img_unresolved value is a bool": dict(full, img_unresolved={f"{eid(2)}:0": True}),
            "unknown field": dict(full, surprise=1),
        }
        for name, data in bad.items():
            write_owner_only(path, json.dumps(data))
            with self.assertRaises(FGS.GroupSyncError, msg=name):
                FGS.load_state(self.tmp)
        big = FGS.State(images={f"{eid(i)}:0": "skipped" for i in range(FGS.LEDGER_KEEP + 3)})
        FGS.prune_state(big)
        self.assertEqual(len(big.images), FGS.LEDGER_KEEP)
        self.assertNotIn(f"{eid(0)}:0", big.images)
        self.assertIn(f"{eid(FGS.LEDGER_KEEP + 2)}:0", big.images)


# ================================ 图片同步 S4：飞书 → Buzz 的纯函数（去元数据、消息里的图片） ================================


class MetadataStripping(unittest.TestCase):
    """Buzz 的 relay 会拒绝带元数据的媒体（422 media contains metadata）：镜像到 Buzz 的图先无损去掉 EXIF / ICC / XMP / 注释和文件尾的多余字节。"""

    def test_jpeg_loses_its_metadata_segments_and_nothing_else(self):
        """L1-FGS-126: jpeg 去掉 APP1–APP15（EXIF、ICC、IPTC、XMP 等）和 COM 注释，保留 JFIF（APP0）、量化表等其余段，图像数据一个字节都不动、
        不重新编码；EOI 之后的多余字节（手机常在后面附加数据）也去掉；没有元数据的文件原样不变；扫描数据里出现的 FF00 与 RST 标记不误判。"""
        self.assertTrue(has_image_metadata(JPEG_META))
        out = FGS.strip_metadata(JPEG_META, "jpeg")
        self.assertEqual(out, JPEG_PLAIN)
        self.assertFalse(has_image_metadata(out))
        self.assertEqual(FGS.strip_metadata(JPEG_PLAIN, "jpeg"), JPEG_PLAIN)
        scan = b"\x00\xff\x00\x12\xff\xd0\x34\xff\xd7\x56\xff\xd9"
        self.assertEqual(FGS.strip_metadata(_jpeg(JFIF, (0xE1, b"Exif\x00\x00x"), DQT, scan=scan), "jpeg"), _jpeg(JFIF, DQT, scan=scan))
        self.assertEqual(FGS.strip_metadata(JPEG_PLAIN + b"MPF-trailer-with-gps", "jpeg"), JPEG_PLAIN)
        progressive = JPEG_PLAIN[:-2] + b"\xff\xfe\x00\x05abc" + b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00" + b"\x77\xff\xd9"
        self.assertEqual(FGS.strip_metadata(progressive, "jpeg"), JPEG_PLAIN[:-2] + b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00" + b"\x77\xff\xd9")

    def test_png_keeps_only_the_chunks_that_draw_the_picture(self):
        """L1-FGS-127: png 只留画图需要的块：IHDR、PLTE、tRNS、IDAT、IEND，以及 APNG 动画块（acTL、fcTL、fdAT）；iCCP、cICP、eXIf、pHYs、iTXt、tEXt、
        tIME、gAMA 等辅助块都去掉，块按原样（含 CRC）复制；IEND 之后的字节去掉。"""
        self.assertTrue(has_image_metadata(PNG_META))
        self.assertEqual(FGS.strip_metadata(PNG_META, "png"), PNG_PLAIN)
        self.assertEqual(FGS.strip_metadata(PNG_PLAIN, "png"), PNG_PLAIN)
        rich = lambda *between: PNG_SIGNATURE + PNG_IHDR + b"".join(between) + PNG_IDAT + PNG_IEND
        drawing = (PNG_PLTE, PNG_TRNS, *PNG_APNG)
        noisy = (PNG_METADATA_CHUNKS[0], PNG_PLTE, PNG_METADATA_CHUNKS[2], PNG_TRNS, PNG_METADATA_CHUNKS[4], *PNG_APNG, PNG_METADATA_CHUNKS[5])
        self.assertEqual(FGS.strip_metadata(rich(*noisy), "png"), rich(*drawing))
        self.assertEqual(FGS.strip_metadata(PNG_PLAIN + b"appended secret", "png"), PNG_PLAIN)

    def test_webp_drops_icc_exif_and_xmp_and_fixes_the_header_that_announced_them(self):
        """L1-FGS-128: webp 去掉 ICCP、EXIF、XMP 块，VP8X 里宣告它们的标志位（ICC 0x20、EXIF 0x08、XMP 0x04）清零、别的标志（如 alpha 0x10）保留，
        RIFF 总长度重算；图像块（VP8 / VP8L / ALPH / ANIM / ANMF）不动；RIFF 之后的多余字节去掉；没有元数据的文件原样不变。"""
        self.assertTrue(has_image_metadata(WEBP_META))
        out = FGS.strip_metadata(WEBP_META, "webp")
        self.assertEqual(out, _webp(_vp8x(0x10), WEBP_VP8L))
        self.assertFalse(has_image_metadata(out))
        self.assertEqual(FGS.strip_metadata(WEBP_PLAIN, "webp"), WEBP_PLAIN)
        animated = _webp(_vp8x(0x02 | 0x20), (b"ICCP", b"icc"), (b"ANIM", bytes(6)), (b"ANMF", bytes(17)), (b"EXIF", b"e!!"))
        self.assertEqual(FGS.strip_metadata(animated, "webp"), _webp(_vp8x(0x02), (b"ANIM", bytes(6)), (b"ANMF", bytes(17))))
        self.assertEqual(FGS.strip_metadata(WEBP_PLAIN + b"tail", "webp"), WEBP_PLAIN)
        odd = _webp((b"VP8 ", b"abc"), (b"XMP ", b"<x/>"))  # 奇数长度的块要补一个字节
        self.assertEqual(FGS.strip_metadata(odd, "webp"), _webp((b"VP8 ", b"abc")))

    def test_gif_drops_comments_and_application_data_but_keeps_the_loop_and_the_frames(self):
        """L1-FGS-129: gif 去掉注释扩展、纯文本扩展和除动画循环（NETSCAPE2.0、ANIMEXTS1.0）以外的应用扩展（XMP 等）；图形控制扩展与图像数据不动；
        结束符 0x3B 之后的字节去掉。"""
        self.assertTrue(has_image_metadata(GIF_META))
        self.assertEqual(FGS.strip_metadata(GIF_META, "gif"), _gif(GIF_LOOP, GIF_GCE))
        self.assertEqual(FGS.strip_metadata(GIF_PLAIN, "gif"), GIF_PLAIN)
        self.assertEqual(FGS.strip_metadata(GIF_PLAIN + b"trailer", "gif"), GIF_PLAIN)
        self.assertEqual(FGS.strip_metadata(_gif(b"\x21\x01\x0cxxxxxxxxxxxx\x00", GIF_GCE), "gif"), _gif(GIF_GCE))
        self.assertEqual(FGS.strip_metadata(_gif(version=b"87a"), "gif"), _gif(version=b"87a"))

    def test_a_file_that_is_not_well_formed_or_a_format_that_cannot_be_cleaned_is_refused(self):
        """L1-FGS-130: 结构不对（被截断、长度越界、缺结束标记、签名后面不是该有的块）一律 ValueError，不猜、不带病放行；bmp、tiff 等这里没法
        可靠去元数据的格式也拒绝，认不出的格式名同样。"""
        broken = {
            "jpeg": [b"\xff\xd8\xff", b"\xff\xd8" + b"\x00\x00", _jpeg(JFIF)[:-8], b"\xff\xd8\xff\xe1\xff\xff" + bytes(10),
                     JPEG_PLAIN[:-2]],
            "png": [PNG_SIGNATURE, PNG_SIGNATURE + PNG_IHDR, PNG_SIGNATURE + PNG_IDAT + PNG_IEND, (PNG_PLAIN)[:-5],
                    PNG_SIGNATURE + PNG_IHDR + struct.pack(">I", 1 << 30) + b"IDAT" + bytes(8), b"\x89PNG\r\n\x1a\n" + bytes(3)],
            "webp": [b"RIFF\x04\x00\x00\x00WEBP", WEBP_PLAIN[:-4], b"RIFF" + struct.pack("<I", 1 << 30) + b"WEBP" + bytes(20),
                     _webp((b"VP8L", b"abc"))[:-1] + b"", b"RIFF\x00\x00\x00\x00WEBP"],
            "gif": [b"GIF89a", GIF_PLAIN[:-1], GIF_PLAIN[:20], GIF_PLAIN[:19] + b"\x21\xfe\x05abcde"],
        }
        for kind, samples in broken.items():
            for sample in samples:
                with self.assertRaises(ValueError, msg=f"{kind} {sample[:12]!r}"):
                    FGS.strip_metadata(sample, kind)
        for kind, data in (("bmp", BMP_PLAIN), ("tiff", TIFF_PLAIN), ("svg", SVG), ("", JPEG_PLAIN), ("jpeg", PNG_PLAIN),
                           ("png", JPEG_PLAIN), ("gif", WEBP_PLAIN), ("webp", GIF_PLAIN)):
            with self.assertRaises(ValueError, msg=kind):
                FGS.strip_metadata(data, kind)


class FeishuImageRouting(unittest.TestCase):
    """飞书消息里的图片：image 消息渲染成 `[Image: img_…]`，富文本 post 里是 `![Image](img_…)`（实测）；文件、音频、视频不转。"""

    def route(self, msg, *, now=NOW, **kw):
        return FeishuToBuzzRouting.route(self, msg, now=now, **kw)

    def test_an_image_message_carries_its_key_and_says_so_in_the_text(self):
        """L1-FGS-131: image 消息（内容是 `[Image: img_…]`）：image_keys 是这个 key，文字是署名加占位「[图片]」（署名与其余规则不变）。
        没有图片的消息 image_keys 为空。"""
        out = self.route(fmsg("om_i1", ALICE_OPEN, "[Image: img_v3_0215n_f98f2434-1db5-48c6-9314-e259af64453g]", msg_type="image"))
        self.assertEqual((out.text, out.image_keys), ("[飞书] Alice：[图片]", ("img_v3_0215n_f98f2434-1db5-48c6-9314-e259af64453g",)))
        self.assertEqual(self.route(fmsg("om_i2", ALICE_OPEN, "hi")).image_keys, ())

    def test_a_post_keeps_its_text_and_loses_the_image_markers(self):
        """L1-FGS-132: 富文本里的 `![Image](img_…)` 从文字里去掉（图作为附件），前后的空白与多出的空行收掉；key 按出现顺序去重；
        文字里别的 markdown 图片（不是飞书 key）、不合规的 key、文件与视频标记都原样留作文字，不当成图片。"""
        post = lambda content: self.route(fmsg("om_p", ALICE_OPEN, content, msg_type="post"))
        out = post("@孙晓路 无问题\n![Image](img_v3_a)")
        self.assertEqual((out.text, out.image_keys), ("[飞书] Alice：＠孙晓路 无问题", ("img_v3_a",)))
        out = post("![Image](img_v3_a)\n这个错了")
        self.assertEqual((out.text, out.image_keys), ("[飞书] Alice：这个错了", ("img_v3_a",)))
        out = post("第一段\n\n![Image](img_v3_a)\n\n第二段\n![Image](img_v3_b)\n![x](img_v3_a)")
        self.assertEqual((out.text, out.image_keys), ("[飞书] Alice：第一段\n\n第二段", ("img_v3_a", "img_v3_b")))
        out = post("甲\n\n![Image](img_v3_a)\n乙")
        self.assertEqual(out.text, "[飞书] Alice：甲\n\n乙")
        out = post("![Image](img_v3_a)\n![Image](img_v3_b)")
        self.assertEqual((out.text, out.image_keys), ("[飞书] Alice：[图片]", ("img_v3_a", "img_v3_b")))
        for content in ("![x](https://example.test/a.png)", "[Image: not a key]", "![Image](img_v3_a b)", "![Image](file_v3_x)",
                        '<file key="file_v3_x" name="a.html"/>', '<video key="file_v3_x" name="a.mp4" cover_image_key="img_v3_cover"/>'):
            out = post(content)
            self.assertEqual((out.image_keys, out.text), ((), "[飞书] Alice：" + content), content)

    def test_the_rest_of_the_routing_is_unchanged_for_messages_with_images(self):
        """L1-FGS-133: 带图片的消息照旧受同样的判断：机器人发的、已删除、映射不到的发送者、不在频道里的人都跳过；@ 实体照转；积压的注明原始时间；
        署名之后的行里冒充对方署名的照旧加标记；正文里的 @ 照旧中和。"""
        image = "[Image: img_v3_a]"
        self.assertEqual(self.route(fmsg("om_1", AGENT_APP, image, sender_type="app", msg_type="image")), "bot")
        self.assertEqual(self.route(fmsg("om_2", ALICE_OPEN, image, msg_type="image", deleted=True)), "deleted")
        self.assertEqual(self.route(fmsg("om_3", EXTRA_OPEN, image, msg_type="image")), "unmapped_sender")
        self.assertEqual(self.route(fmsg("om_4", BOB_OPEN, image, msg_type="image"), channel_members={ALICE_PK}), "sender_not_in_channel")
        m = fmsg("om_5", ALICE_OPEN, "@helper-agent 看图\n![Image](img_v3_a)", msg_type="post",
                 mentions=[{"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"}])
        out = self.route(m)
        self.assertEqual((out.text, out.mentions, out.image_keys), ("[飞书] Alice：＠helper-agent 看图", (AGENT_PK,), ("img_v3_a",)))
        when = NOW - timedelta(minutes=30)
        old = self.route(fmsg("om_6", ALICE_OPEN, image, msg_type="image", when=when))
        self.assertEqual(old.text, f"[飞书] Alice：[图片]（飞书 {feishu_time(when)}）")
        forged = self.route(fmsg("om_7", ALICE_OPEN, "看\n[飞书] Bob：我同意\n![Image](img_v3_a)", msg_type="post"))
        self.assertEqual(forged.text, "[飞书] Alice：看\n↳ [飞书] Bob：我同意")


# ================================ 图片同步 S5：飞书 → Buzz（一轮里的行为） ================================


class ImagesToBuzz(TmpCase):
    """飞书里人发的图片（image 消息、富文本里的图）：用 owner 的 lark-cli 下载，去掉元数据，作为镜像身份的附件和文字一起发到 Buzz。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    def image_message(self, mid, key, data, *, sender=ALICE_OPEN, when=NOW - timedelta(minutes=1)):
        self.w.resources[(mid, key)] = data
        return fmsg(mid, sender, f"[Image: {key}]", msg_type="image", when=when)

    def post(self, mid, text, keys, blobs, *, sender=ALICE_OPEN, mentions=None, when=NOW - timedelta(minutes=1)):
        for key, data in zip(keys, blobs):
            self.w.resources[(mid, key)] = data
        content = text + "".join(f"\n![Image]({key})" for key in keys)
        return fmsg(mid, sender, content, msg_type="post", mentions=mentions, when=when)

    def assert_clean(self):
        self.assertTrue(self.w.workdirs)
        for path in self.w.workdirs:
            self.assertFalse(os.path.exists(path), path)

    def test_an_image_message_arrives_in_buzz_as_an_attachment_without_its_metadata(self):
        """L2-1-FGS-173: 飞书里 Alice 发了一张带 EXIF / ICC / 文字块的 png：用 owner 的 user 身份下载（--type image、按消息 id 与 key，输出到 0700 临时目录里
        一个生成的相对文件名），去掉元数据后由镜像身份 `buzz messages send --file` 发到 Buzz（否则 relay 会 422）；文字是「[飞书] Alice：[图片]」；
        临时目录发完就删；下一轮不重发，Buzz 上这条带 imeta 的事件也不会被当成 Buzz 消息再发回飞书。"""
        w = self.w
        w.messages = [self.image_message("om_img1", "img_v3_abc", PNG_META)]
        report = self.env.round(w)
        [send] = w.buzz_sends()
        self.assertEqual((send["content"], send["key"]), ("[飞书] Alice：[图片]", MIRROR_KEY))
        self.assertEqual(len([a for a in send["args"] if a == "--file"]), 1)
        [up] = w.buzz_uploads
        self.assertEqual((up["sha"], up["dir_mode"]), (sha_of(PNG_PLAIN), 0o700))
        self.assertRegex(up["name"], r"^[a-z0-9._-]+\.png$")
        [down] = w.resource_downloads
        self.assertEqual((down["as"], down["message_id"], down["key"], down["type"]), ("user", "om_img1", "img_v3_abc", "image"))
        self.assertEqual(down["dir_mode"], 0o700)
        self.assertNotIn("/", down["output"])
        self.assertNotIn("img_v3_abc", down["output"])
        [mirrored] = w.mirrored()
        [imeta] = [t for t in mirrored["tags"] if t[0] == "imeta"]
        self.assertEqual(dict(f.split(" ", 1) for f in imeta[1:])["x"], sha_of(PNG_PLAIN))
        self.assertEqual((report["to_buzz"], report["images_to_buzz"], report["images_failed"], report["images_skipped"], report["errors"]),
                         (1, 1, 0, {}, 0))
        self.assertEqual(self.env.state()["f2b"], {"om_img1": mirrored["id"]})
        self.assert_clean()
        for minutes in (1, 3):
            again = self.env.round(w, now=NOW + timedelta(minutes=minutes))
            self.assertEqual((again["to_buzz"], again["images_to_buzz"], again["to_feishu"], again["images_to_feishu"]), (0, 0, 0, 0))
        self.assertEqual((len(w.buzz_sends()), len(w.resource_downloads), w.lark_sends(), w.image_sends), (1, 1, [], []))

    def test_a_post_with_text_mentions_and_several_images_is_one_buzz_message(self):
        """L2-1-FGS-174: 富文本里的文字 + @agent 实体 + 两张图（一张带 EXIF 的 jpeg、一张带注释与 XMP 的 gif）：一条 Buzz 消息，两个 --file 按出现顺序，
        文字里没有图片标记，@ 实体照转成 --mention，附件上传的都是去掉元数据的字节；话题里的回复照旧用 --reply-to 挂在根消息的事件下。"""
        w = self.w
        mention = [{"id": AGENT_BOT_MEMBER, "key": "@_user_1", "name": "helper-agent"}]
        w.messages = [self.post("om_p1", "@helper-agent 看图", ["img_v3_a", "img_v3_b"], [JPEG_META, GIF_META], mentions=mention)]
        self.env.round(w)
        [send] = w.buzz_sends()
        self.assertEqual(send["content"], "[飞书] Alice：＠helper-agent 看图")
        self.assertEqual([send["args"][i + 1] for i, a in enumerate(send["args"]) if a == "--mention"], [AGENT_PK])
        self.assertEqual([u["sha"] for u in w.buzz_uploads], [sha_of(JPEG_PLAIN), sha_of(_gif(GIF_LOOP, GIF_GCE))])
        for up in w.buzz_uploads:
            self.assertFalse(has_image_metadata(w.media[up["sha"]]))
        self.assertEqual([d["key"] for d in w.resource_downloads], ["img_v3_a", "img_v3_b"])
        self.assertEqual(len(w.mirrored()), 1)
        w.threads["om_p1"] = [self.image_message("om_t1", "img_v3_c", PNG_PLAIN, sender=BOB_OPEN, when=NOW + timedelta(minutes=1))]
        w.threads["om_p1"][0]["thread_id"] = "omt_om_p1"
        w.messages[0]["thread_id"] = "omt_om_p1"
        self.env.round(w, now=NOW + timedelta(minutes=2))
        reply = w.buzz_sends()[1]
        root_event = w.mirrored()[0]
        self.assertEqual((reply["content"], reply["args"][reply["args"].index("--reply-to") + 1]), ("[飞书] Bob：[图片]", root_event["id"]))
        self.assertEqual(len([a for a in reply["args"] if a == "--file"]), 1)
        self.assert_clean()

    def test_what_is_not_a_usable_image_is_counted_once_by_reason_and_the_text_still_goes(self):
        """L2-1-FGS-175: 不能发的图按原因计数、只计一次：内容不是图片（html、svg）、格式 Buzz 这边没法去元数据（bmp）、魔数对但结构坏了（被截断的 jpeg）、
        超过大小上限；好的那张和文字照发（没有一张能发时文字是占位「[图片]」）。一条消息超过 9 张，多出的计入 over_limit。"""
        w = self.w
        keys = ["img_v3_ok", "img_v3_html", "img_v3_svg", "img_v3_bmp", "img_v3_cut", "img_v3_big"]
        blobs = [JPEG_PLAIN, b"<html>login</html>", SVG, BMP_PLAIN, JPEG_PLAIN[:-2], JPEG_PLAIN + bytes(400)]
        w.messages = [self.post("om_p2", "混着发", keys, blobs)]
        many = [f"img_v3_{i:02d}" for i in range(11)]
        w.messages.append(self.post("om_p3", "很多张", many, [PNG_PLAIN + bytes(0)] * 11, when=NOW - timedelta(seconds=30)))
        with mock.patch.object(FGS, "IMAGE_MAX_BYTES", 300):
            report = self.env.round(w)
            self.assertEqual(report["images_skipped"], {"not_image": 2, "unsupported_format": 1, "bad_image": 1, "too_large": 1, "over_limit": 2})
            self.assertEqual((report["images_to_buzz"], report["images_failed"], report["to_buzz"], report["errors"]), (10, 0, 2, 0))
            self.assertFalse(FGS.needs_attention(report))
            for minutes in (1, 3):
                again = self.env.round(w, now=NOW + timedelta(minutes=minutes))
                self.assertEqual((again["images_skipped"], again["images_to_buzz"], again["to_buzz"]), ({}, 0, 0))
        first, second = w.buzz_sends()
        self.assertEqual([first["content"], second["content"]], ["[飞书] Alice：混着发", "[飞书] Alice：很多张"])
        self.assertEqual((len([a for a in first["args"] if a == "--file"]), len([a for a in second["args"] if a == "--file"])), (1, 9))
        self.assertEqual(len(w.resource_downloads), 6 + 9)

    def test_a_message_none_of_whose_images_can_be_sent_still_says_an_image_was_there(self):
        """L2-1-FGS-176: 一条只有图片的消息，图片都不能发（不是图片）：Buzz 上是「[飞书] Alice：[图片]」，没有附件——读的人至少知道飞书那边有张图；计一次。"""
        w = self.w
        w.messages = [self.image_message("om_img2", "img_v3_x", b"MZ\x90\x00not an image")]
        report = self.env.round(w)
        [send] = w.buzz_sends()
        self.assertEqual(send["content"], "[飞书] Alice：[图片]")
        self.assertNotIn("--file", send["args"])
        self.assertEqual((report["images_skipped"], report["images_to_buzz"], report["to_buzz"]), ({"not_image": 1}, 0, 1))

    def test_a_download_that_fails_costs_that_image_only_never_the_text_or_the_other_images(self):
        """L2-1-FGS-177: 飞书下载出错（网络、超时、飞书报错）只影响那一张图：同一轮里文字和别的图照发（一条 Buzz 消息、没有延迟），那张图计入
        images_failed（需要关注，不悄悄丢）；只有一张图又下载失败时文字是占位「[图片]」；不重试（飞书 → Buzz 的发送没有幂等键，事后补发只能是另一条消息）；
        同一轮里别的消息不受影响，之后的轮次不再下载。"""
        w = self.w
        w.messages = [self.post("om_img3", "两张图", ["img_v3_bad", "img_v3_ok"], [JPEG_META, PNG_META], when=NOW - timedelta(minutes=1)),
                      self.image_message("om_img5", "img_v3_only", JPEG_META, when=NOW - timedelta(seconds=55)),
                      fmsg("om_plain", BOB_OPEN, "旁边的普通消息", when=NOW - timedelta(seconds=50))]
        w.resource_fail = ["network", None, "timeout"]  # 第一张图、第二张图（成功）、第三条消息里唯一的图
        report = self.env.round(w)
        self.assertEqual([c["content"] for c in w.buzz_sends()], ["[飞书] Alice：两张图", "[飞书] Alice：[图片]", "[飞书] Bob：旁边的普通消息"])
        self.assertEqual([len([a for a in c["args"] if a == "--file"]) for c in w.buzz_sends()], [1, 0, 0])
        self.assertEqual([u["sha"] for u in w.buzz_uploads], [sha_of(PNG_PLAIN)])
        self.assertEqual((report["to_buzz"], report["images_to_buzz"], report["images_failed"], report["errors"]), (3, 1, 2, 0))
        self.assertTrue(FGS.needs_attention(report))
        self.assertEqual({k for k, v in self.env.state()["f2b"].items() if v.startswith("om_") or len(v) == 64}, {"om_img3", "om_img5", "om_plain"})
        self.assertEqual(self.env.state()["attempts"], {})
        for minutes in (1, 3):
            again = self.env.round(w, now=NOW + timedelta(minutes=minutes))
            self.assertEqual((again["to_buzz"], again["images_failed"], again["errors"]), (0, 0, 0))
        self.assertEqual(len(w.resource_downloads), 1)

    def test_nothing_the_server_says_about_names_or_paths_is_trusted(self):
        """L2-1-FGS-178: 下载时不信服务器给的任何文件名或路径：--output 是脚本生成的相对名（不含 key、消息 id、目录）；应答里的 saved_path 撒谎（指向别处）也不读，
        读的是临时目录里那唯一的常规文件；扩展名被服务器写成 .exe 也按魔数认、上传时用生成的名字；文件其实是指向别处（另一张图）的符号链接，不跟随、
        不上传，计为 unreadable。"""
        sub = self.tmp / "names"
        sub.mkdir(mode=0o700)
        w, env = FakeWorld(sub), Env(sub)
        decoy = sub / "decoy.jpg"
        decoy.write_bytes(_jpeg(JFIF, DQT, scan=b"DECOY\xff\xd9"))
        w.resource_saved_path = str(decoy)
        w.resource_ext = ".exe"
        w.resources[("om_n1", "img_v3_n")] = PNG_PLAIN
        w.messages = [fmsg("om_n1", ALICE_OPEN, "[Image: img_v3_n]", msg_type="image")]
        env.round(w)
        self.assertEqual([u["sha"] for u in w.buzz_uploads], [sha_of(PNG_PLAIN)])
        self.assertRegex(w.buzz_uploads[0]["name"], r"^[a-z0-9._-]+\.png$")
        self.assertRegex(w.resource_downloads[0]["output"], r"^[a-z0-9._-]+$")
        sub2 = self.tmp / "links"
        sub2.mkdir(mode=0o700)
        w2, env2 = FakeWorld(sub2), Env(sub2)
        secret = sub2 / "elsewhere.jpg"
        secret.write_bytes(JPEG_PLAIN)
        w2.resource_symlink_to = str(secret)
        w2.resources[("om_n2", "img_v3_l")] = PNG_PLAIN
        w2.messages = [fmsg("om_n2", ALICE_OPEN, "[Image: img_v3_l]", msg_type="image")]
        report = env2.round(w2)
        self.assertEqual((w2.buzz_uploads, report["images_skipped"], report["to_buzz"]), ([], {"unreadable": 1}, 1))

    def test_only_images_are_forwarded_never_files_audio_or_video(self):
        """L2-1-FGS-179: 文件、音频、视频（以及视频的封面图）照旧只按 lark-cli 渲染出来的文字镜像：不下载、不上传、没有附件。"""
        w = self.w
        w.messages = [fmsg("om_f1", ALICE_OPEN, '<file key="file_v3_x" name="ama-script.html"/>', msg_type="file"),
                      fmsg("om_f2", ALICE_OPEN, '<video key="file_v3_v" name="a.mp4" duration="12s" cover_image_key="img_v3_cover"/>',
                           msg_type="media", when=NOW - timedelta(seconds=50))]
        w.resources[("om_f2", "img_v3_cover")] = JPEG_PLAIN
        report = self.env.round(w)
        self.assertEqual(report["to_buzz"], 2)
        self.assertEqual((w.resource_downloads, w.buzz_uploads), ([], []))
        for send in w.buzz_sends():
            self.assertNotIn("--file", send["args"])
        self.assertIn("cover_image_key", w.buzz_sends()[1]["content"])

    def test_a_send_with_an_image_keeps_the_never_resend_rules(self):
        """L2-1-FGS-180: 带图的消息发送结果不确定（relay 网络错误）：记 unknown、之后永不重发（不再下载）；确定被拒（exit 1）：下一轮重试（重新下载、
        重新去元数据），三次都被拒就放弃。任何一种结局临时目录都已删除。"""
        w = self.w
        w.messages = [self.image_message("om_u1", "img_v3_u", JPEG_META)]
        w.buzz_send_fail = ["network"]
        report = self.env.round(w)
        self.assertEqual((report["unknown"], report["to_buzz"]), (1, 0))
        self.assertEqual(self.env.state()["f2b"]["om_u1"], "unknown")
        self.assert_clean()
        for minutes in (1, 2):
            self.env.round(w, now=NOW + timedelta(minutes=minutes))
        self.assertEqual((len(w.resource_downloads), len(w.buzz_sends())), (1, 1))
        sub = self.tmp / "refused"
        sub.mkdir(mode=0o700)
        w2, env2 = FakeWorld(sub), Env(sub)
        w2.resources[("om_u2", "img_v3_r")] = JPEG_META
        w2.messages = [fmsg("om_u2", ALICE_OPEN, "[Image: img_v3_r]", msg_type="image")]
        w2.buzz_send_fail = ["bad_input", "bad_input"]
        env2.round(w2)
        report = env2.round(w2, now=NOW + timedelta(minutes=1))
        self.assertEqual(report["to_buzz"], 0)
        report = env2.round(w2, now=NOW + timedelta(minutes=2))
        self.assertEqual((report["to_buzz"], report["images_to_buzz"], len(w2.resource_downloads)), (1, 1, 3))
        self.assertEqual(len(w2.mirrored()), 1)


# ================================ 图片同步：适配器与变异检查补的用例 ================================


class ImageAdapters(unittest.TestCase):
    """图片相关的 lark-cli / buzz 调用：参数、工作目录、什么算「确定没发出」。"""

    def runner_for(self, calls, *, out="", err="", code=0):
        def runner(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, code, out, err)
        return runner

    def test_buzz_media_download_takes_only_a_relay_path_segment_and_classifies_failures(self):
        """L1-FGS-134: `buzz media get <sha256[.ext]> -o <路径>`：只收 <64 位小写 hex>[.扩展名] 这一段（整个 url、路径、大写、空串都在调用之前就拒绝，
        CLI 只会跟自己配置的 relay 说话）；退出码 1（输入）、3（鉴权）是确定没取到，2（relay / 网络）、别的退出码与超时不确定。"""
        sha = "ab" * 32
        calls = []
        cli = FGS.BuzzCli(BUZZ_CLI, {"BUZZ_PRIVATE_KEY": MIRROR_KEY}, runner=self.runner_for(calls))
        cli.download_media(f"{sha}.jpg", Path("/tmp/x/dl-0"))
        cli.download_media(sha, Path("/tmp/x/dl-1"))
        self.assertEqual([c[0] for c in calls], [[BUZZ_CLI, "media", "get", f"{sha}.jpg", "-o", "/tmp/x/dl-0"],
                                                 [BUZZ_CLI, "media", "get", sha, "-o", "/tmp/x/dl-1"]])
        self.assertEqual(calls[0][1]["env"], {"BUZZ_PRIVATE_KEY": MIRROR_KEY})
        for bad in (f"https://relay.test/media/{sha}.jpg", f"../{sha}", sha.upper(), "", f"{sha}.thumb.jpg", f"{sha[:-1]}", f"{sha}.jpg\n"):
            with self.assertRaises(FGS.CliError, msg=repr(bad)) as ctx:
                cli.download_media(bad, Path("/tmp/x/dl"))
            self.assertTrue(ctx.exception.definite, repr(bad))
        self.assertEqual(len(calls), 2, "a refused segment never reaches the CLI")
        for code, definite in [(1, True), (3, True), (2, False), (4, False)]:
            with self.assertRaises(FGS.CliError) as ctx:
                FGS.BuzzCli(BUZZ_CLI, {}, runner=self.runner_for([], code=code)).download_media(sha, Path("/tmp/x/dl"))
            self.assertEqual(ctx.exception.definite, definite, code)

        def timeout(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 120)
        with self.assertRaises(FGS.CliError) as ctx:
            FGS.BuzzCli(BUZZ_CLI, {}, runner=timeout).download_media(sha, Path("/tmp/x/dl"))
        self.assertFalse(ctx.exception.definite)

    def test_buzz_send_attaches_each_file_with_its_own_flag(self):
        """L1-FGS-135: `messages send` 的每个附件一个 `--file <路径>`，按给定顺序，不给就没有 --file；正文仍走 stdin。"""
        calls = []
        cli = FGS.BuzzCli(BUZZ_CLI, {}, runner=self.runner_for(calls, out=json.dumps({"event_id": eid(77), "accepted": True})))
        cli.send(CHANNEL, "x", files=("/w/up-0.png", "/w/up-1.jpg"))
        cli.send(CHANNEL, "y")
        first, second = calls[0][0], calls[1][0]
        self.assertEqual([first[i + 1] for i, a in enumerate(first) if a == "--file"], ["/w/up-0.png", "/w/up-1.jpg"])
        self.assertNotIn("--file", second)
        self.assertEqual(first[first.index("--content") + 1], "-")

    def test_lark_image_sends_run_in_the_given_directory_with_a_relative_name(self):
        """L1-FGS-136: 发图：`+messages-send --as bot --chat-id … --image <名字> --idempotency-key …` 或 `+messages-reply --as bot --message-id …
        --image <名字> --reply-in-thread --idempotency-key …`，子进程的 cwd 是给定的目录（lark-cli 只收相对当前目录的文件）；返回飞书消息 id；
        其他调用不传 cwd；确定被拒的（type=api）是 definite，网络错误与没有消息 id 的应答不是。"""
        calls = []
        reply = json.dumps({"ok": True, "data": {"message_id": "om_img1", "chat_id": CHAT}})
        cli = FGS.LarkCli(LARK_CLI, {"TZ": "UTC"}, runner=self.runner_for(calls, out=reply))
        self.assertEqual(cli.send_image(CHAT, "img-0.jpg", "k1", cwd=Path("/w")), "om_img1")
        self.assertEqual(cli.reply_image("om_root", "img-1.png", "k2", cwd=Path("/w")), "om_img1")
        self.assertEqual(calls[0][0], [LARK_CLI, "im", "+messages-send", "--as", "bot", "--chat-id", CHAT, "--image", "img-0.jpg",
                                       "--idempotency-key", "k1", "--format", "json"])
        self.assertEqual(calls[1][0], [LARK_CLI, "im", "+messages-reply", "--as", "bot", "--message-id", "om_root", "--image", "img-1.png",
                                       "--reply-in-thread", "--idempotency-key", "k2", "--format", "json"])
        self.assertEqual([c[1]["cwd"] for c in calls], ["/w", "/w"])
        calls.clear()
        cli.send(CHAT, "text", "k3")
        self.assertNotIn("cwd", calls[0][1])
        for out, definite in [(json.dumps({"ok": False, "error": {"type": "api", "code": 234001}}), True),
                              (json.dumps({"ok": False, "error": {"type": "network"}}), False),
                              (json.dumps({"ok": True, "data": {}}), False)]:
            with self.assertRaises(FGS.CliError, msg=out) as ctx:
                FGS.LarkCli(LARK_CLI, {}, runner=self.runner_for([], out=out, code=1)).send_image(CHAT, "a.jpg", "k", cwd=Path("/w"))
            self.assertEqual(ctx.exception.definite, definite, out)

    def test_lark_image_download_returns_the_one_file_that_ended_up_in_the_directory(self):
        """L1-FGS-137: 下载消息里的图：`+messages-resources-download --as user --message-id … --file-key … --type image --output download`，
        cwd 是给定的空目录；返回目录里唯一的那个文件（lark-cli 会给 --output 补扩展名，应答里的 saved_path 不用）；目录里没有文件或不止一个文件时报错（不确定）；
        消息 id 或 key 不合规（含空格、斜杠、.. 等）在调用之前就拒绝。"""
        tmp = Path(tempfile.mkdtemp())
        try:
            calls = []

            def runner(argv, **kw):
                calls.append((argv, kw))
                (Path(kw["cwd"]) / "download.png").write_bytes(b"x")
                return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {"saved_path": "/etc/passwd", "size_bytes": 1}}), "")
            cli = FGS.LarkCli(LARK_CLI, {}, runner=runner)
            found = cli.download_image("om_x100b65cd3ea2b0acb14486c2a2b325f", "img_v3_0215n_f98f2434-1db5-48c6-9314-e259af64453g", tmp)
            self.assertEqual(found, tmp / "download.png")
            self.assertEqual(calls[0][0], [LARK_CLI, "im", "+messages-resources-download", "--as", "user", "--message-id",
                                           "om_x100b65cd3ea2b0acb14486c2a2b325f", "--file-key", "img_v3_0215n_f98f2434-1db5-48c6-9314-e259af64453g",
                                           "--type", "image", "--output", "download", "--format", "json"])
            self.assertEqual(calls[0][1]["cwd"], str(tmp))
            calls.clear()
            for message_id, key in (("om_x", "img_v3 a"), ("om_x", "img_v3/a"), ("om_x", "../img_v3_a"), ("om_x", "file_v3_a"), ("bad id", "img_v3_a"),
                                    ("om_x/..", "img_v3_a"), ("", "img_v3_a"), ("om_x", "img_v3_" + "a" * 130)):
                with self.assertRaises(FGS.CliError, msg=(message_id, key)) as ctx:
                    cli.download_image(message_id, key, tmp)
                self.assertTrue(ctx.exception.definite)
            self.assertEqual(calls, [])
            empty, two = Path(tempfile.mkdtemp(dir=tmp)), Path(tempfile.mkdtemp(dir=tmp))
            (two / "a").write_bytes(b"1")
            (two / "b").write_bytes(b"2")
            nothing = FGS.LarkCli(LARK_CLI, {}, runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {}}), ""))
            for where in (empty, two):
                with self.assertRaises(FGS.CliError, msg=str(where)) as ctx:
                    nothing.download_image("om_x", "img_v3_a", where)
                self.assertFalse(ctx.exception.definite)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ImageRoundGaps(TmpCase):
    """变异检查发现没人抓的行为，补上：每个用例都对应一个存活过的变异。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    put = ImagesToFeishu.put
    image_messages = ImagesToFeishu.image_messages
    item = ImagesToFeishuFailures.item

    def test_a_retried_image_goes_to_the_same_thread_under_the_same_key(self):
        """L2-1-FGS-181: 话题回复里的图第一次被拒，重试时仍然是对同一个父消息的回复（不是退成发到群里），幂等键不变，只在话题里出现一张。"""
        w = self.w
        sha = self.put(photo(60))
        w.events = [event(eid(1), ALICE_PK, "root"),
                    image_event(2, AGENT_PK, "证据", [sha], created_at=T0 + 1, tags=[("e", eid(1), "", "reply")])]
        w.lark_send_fail = [None, None, "rate_limited"]  # 根消息、话题里的文字成功；图被拒
        self.env.round(w)
        self.assertEqual(w.image_sends, [])
        self.env.round(w, now=NOW + timedelta(minutes=1))
        [up] = w.image_sends
        root_mid = next(m["message_id"] for m in w.messages if "root" in m["content"])
        self.assertEqual(up["target"], ("reply", root_mid))
        self.assertEqual(up["key"], "b2f-img-" + hashlib.sha256(f"{eid(2)}:0".encode()).hexdigest()[:36])
        self.assertEqual([m["msg_type"] for m in w.threads[root_mid]], ["text", "image"])

    def test_the_images_of_an_event_whose_text_failed_are_all_counted_including_those_over_the_limit(self):
        """L2-1-FGS-182: 文字发不出去的事件有 11 张图：9 张加多出的 2 张都按 text_not_sent 计（共 11），只计一次；账本里超出部分也记 skipped。"""
        w = self.w
        w.events = [image_event(1, AGENT_PK, "文字发不出去", [self.put(photo(70 + i)) for i in range(11)])]
        w.lark_send_fail = ["rate_limited"] * 3
        for minutes in range(3):
            report = self.env.round(w, now=NOW + timedelta(minutes=minutes))
        self.assertEqual(report["images_skipped"], {"text_not_sent": 11})  # 放弃文字的那一轮
        self.assertEqual(self.env.round(w, now=NOW + timedelta(minutes=3))["images_skipped"], {})
        self.assertEqual(self.env.state()["images"][f"{eid(1)}:over"], "skipped")
        self.assertEqual((w.media_reads, w.image_sends), ([], []))

    def test_dropping_the_rest_of_an_event_keeps_the_images_that_already_went_out(self):
        """L2-1-FGS-183: Desk 失效时旧图账本保留，剩余图片不再发送。"""
        w = self.w
        a, b = self.put(photo(80)), self.put(photo(81))
        w.events = [image_event(1, AGENT_PK, "两张", [a, b])]
        w.media_fail = [None, "network"]
        self.env.round(w)
        sent = self.env.state()["images"][self.item(1, 0)]
        self.assertTrue(sent.startswith("om_"))
        w.agent_dirs[str(self.tmp / "agent-cfg")] = "cli_somethingelse001"
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(self.env.state()["images"][self.item(1, 0)], sent)
        self.assertEqual(w.image_sends[0]["app"], AGENT_APP)

    def test_every_format_feishu_takes_goes_out_with_its_own_extension(self):
        """L2-1-FGS-184: 飞书接受的六种格式都能发（含 Buzz 那边没法去元数据的 bmp、tiff）：内容是什么就用什么扩展名上传，jpeg → .jpg、tiff → .tiff。"""
        w = self.w
        blobs = [(JPEG_PLAIN + b"1", ".jpg"), (PNG_PLAIN + b"2", ".png"), (GIF_PLAIN + b"3", ".gif"), (WEBP_PLAIN + b"4", ".webp"),
                 (BMP_PLAIN + b"5", ".bmp"), (TIFF_PLAIN + b"6", ".tiff")]
        w.events = [image_event(1, AGENT_PK, "六种", [self.put(data) for data, _ in blobs])]
        report = self.env.round(w)
        self.assertEqual((report["images_to_feishu"], report["images_skipped"]), (6, {}))
        self.assertEqual([Path(u["name"]).suffix for u in w.image_sends], [ext for _, ext in blobs])

    def test_the_pending_mark_is_on_disk_before_each_image_send_and_only_one_file_is_ever_there(self):
        """L2-1-FGS-185: 每张图发送之前，「pending」已经写进磁盘上的 state（不确定的结果只能用同一个键重试）；发送时临时目录里只有这一张图的文件——
        下载的原始文件已改名，上一张发完就删了。"""
        w = self.w
        a, b = self.put(photo(82)), self.put(photo(83))
        w.events = [image_event(1, AGENT_PK, "两张", [a, b])]
        seen = []

        def before(kind, key):
            if key.startswith("b2f-img-"):
                seen.append(json.loads((self.env.state_dir / FGS.STATE_FILE).read_text())["images"])
        w.before_send = before
        self.env.round(w)
        self.assertEqual(len(seen), 2)
        self.assertTrue(FGS._is_pending(seen[0][self.item(1, 0)]))
        self.assertTrue(FGS._is_pending(seen[1][self.item(1, 1)]))
        self.assertTrue(w.image_sends[0]["message_id"].startswith("om_") and self.item(1, 0) in seen[1])
        self.assertEqual([len(u["dir_files"]) for u in w.image_sends], [1, 1])
        self.assertEqual([u["dir_files"] for u in w.image_sends], [[u["name"]] for u in w.image_sends])

    def test_a_three_line_gap_is_closed_where_an_attachment_line_was_removed(self):
        """L2-1-FGS-186: 图片行去掉之后留下恰好三个换行（一个空行加一个换行）也收成一个空行；两个换行本来就正常，不动。"""
        w = self.w
        sha = self.put(photo(84))
        url = f"{MEDIA_ORIGIN}/media/{sha}.jpg"
        w.events = [event(eid(1), AGENT_PK, f"甲\n\n![image]({url})\n乙\n![image]({url})丙", tags=[imeta_tag(sha)])]
        self.env.round(w)
        self.assertEqual(text_of(w.lark_sends()[0]), "甲\n\n乙\n丙")


class ImageRoundGapsToBuzz(TmpCase):
    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    def test_the_upload_file_is_private_and_a_maybe_delivered_message_counts_what_is_certain(self):
        """L2-1-FGS-187: 上传给 Buzz 的文件是 0600、在 0700 目录里；结果不确定的消息里，已经确定的跳过计数照记（消息可能已送达，这次尝试就是最终结果）。"""
        w = self.w
        w.resources[("om_g1", "img_v3_g")] = JPEG_META
        w.messages = [fmsg("om_g1", ALICE_OPEN, "[Image: img_v3_g]", msg_type="image")]
        self.env.round(w)
        [up] = w.buzz_uploads
        self.assertEqual((up["file_mode"], up["dir_mode"]), (0o600, 0o700))
        sub = self.tmp / "unknown"
        sub.mkdir(mode=0o700)
        w2, env2 = FakeWorld(sub), Env(sub)
        w2.resources[("om_g2", "img_v3_h")] = JPEG_PLAIN
        w2.resources[("om_g2", "img_v3_html")] = b"<html/>"
        w2.messages = [fmsg("om_g2", ALICE_OPEN, "看\n![Image](img_v3_h)\n![Image](img_v3_html)", msg_type="post")]
        w2.buzz_send_fail = ["network"]
        report = env2.round(w2)
        self.assertEqual((report["unknown"], report["images_skipped"], report["images_to_buzz"]), (1, {"not_image": 1}, 0))


class StripEdgeCases(unittest.TestCase):
    def test_edge_cases_of_the_walkers(self):
        """L1-FGS-138: 边角：jpeg 扫描数据里的填充字节 FF FF 原样保留、之后的 EOI 照常认出；gif 的动画循环 ANIMEXTS1.0 也保留；全局色表与局部色表按 3×2^(n+1) 字节跳过
        （n 来自 packed 的低三位），色表大小不同的 gif 也能去掉注释而不破坏帧。"""
        fill = _jpeg(JFIF, DQT, scan=b"\x11\xff\xff\xd9")
        self.assertEqual(FGS.strip_metadata(fill + b"junk", "jpeg"), fill)
        animexts = _gif(b"\x21\xff\x0bANIMEXTS1.0\x03\x01\x00\x00\x00", GIF_GCE)
        self.assertEqual(FGS.strip_metadata(animexts, "gif"), animexts)

        def gif(*extensions):  # 全局色表 4 项（packed 0x81 → 12 字节），帧带 4 项局部色表
            header = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x81, 0, 0) + bytes(12)
            frame = b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0x81) + bytes(12) + b"\x02\x02\x44\x01\x00"
            return header + b"".join(extensions) + frame + b"\x3b"
        self.assertEqual(FGS.strip_metadata(gif(GIF_COMMENT, GIF_GCE), "gif"), gif(GIF_GCE))
        big = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x87, 0, 0) + bytes(3 * 256) + b"\x21\xfe\x02hi\x00" + b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00\x3b"
        self.assertEqual(FGS.strip_metadata(big, "gif"), big.replace(b"\x21\xfe\x02hi\x00", b""))


class ImageSurvivors(TmpCase):
    """变异检查第一轮活下来的变异逐个补的用例（等价变异不补，见提交说明）。"""

    A = "ab" * 32

    def test_more_shapes_of_bmp_and_of_attachment_urls(self):
        """L1-FGS-139: bmp 的 DIB 头有多种合法大小（12、40、124 …），头不完整的 `BM` 不是图片（不报错）；imeta 的 url 允许 http；主机部分不能带查询串；
        同一个键出现两次时取第一个（url、x 都是）。"""
        def bmp(dib):
            return b"BM" + struct.pack("<IHHI", 14 + dib, 0, 0, 14 + dib) + struct.pack("<I", dib) + bytes(max(dib - 4, 0))
        for size in (12, 40, 52, 56, 64, 108, 124):
            self.assertEqual(FGS.sniff_image(bmp(size)), "bmp", size)
        for short in (b"BM", b"BM" + bytes(10), b"BM" + bytes(15), bmp(41)):
            self.assertIsNone(FGS.sniff_image(short), short)
        A, B = self.A, "cd" * 32
        images = lambda *tags: FGS.event_images(event(eid(1), AGENT_PK, "x", tags=tags))[0]
        self.assertEqual(images(imeta_tag(A, url=f"http://localhost:3000/media/{A}.jpg")), (FGS.ImageRef(f"{A}.jpg", A, 1234),))
        for url in (f"https://relay.test?x=1/media/{A}.jpg", f"https://relay.test#x/media/{A}.jpg", f"https://re lay.test/media/{A}.jpg",
                    f"https:///media/{A}.jpg"):
            self.assertEqual(images(imeta_tag(A, url=url)), ("bad_imeta",), url)
        twice = ["imeta", f"url {MEDIA_ORIGIN}/media/{A}.jpg", "url https://example.test/other.jpg", f"x {A}", f"x {B}", "size 7", "size 999"]
        self.assertEqual(images(twice), (FGS.ImageRef(f"{A}.jpg", A, 7),))

    def test_more_shapes_of_markdown_images(self):
        """L1-FGS-140: 占位 alt 不分大小写（`Image`、`IMAGE` 也算没有 alt）；地址的 scheme 不分大小写（`HTTPS://…` 照样带地址）。"""
        route = ImageRouting.route
        for alt in ("Image", "IMAGE", " image "):
            out = route(self, event(eid(1), AGENT_PK, f"![{alt}](https://example.test/a.png)"))
            self.assertEqual(out.text, "[图片] https://example.test/a.png", alt)
        out = route(self, event(eid(2), AGENT_PK, "![图](HTTPS://Example.test/A.png)"))
        self.assertEqual(out.text, "[图片：图] HTTPS://Example.test/A.png")

    def test_devices_are_not_images_and_a_file_that_grows_is_not_read_whole(self):
        """L1-FGS-141: 不是普通文件的东西（设备文件）不当图片读，报 unreadable；fstat 说文件不大、实际却很大（读的过程中变大）时，
        也只读到上限加一字节就判 too_large，不把它整个读进内存。"""
        import tracemalloc
        with self.assertRaises(FGS.ImageSkip) as ctx:
            FGS.read_image(Path("/dev/null"), allowed=FGS.IMAGE_FEISHU_FORMATS)
        self.assertEqual(ctx.exception.reason, "unreadable")
        path = self.tmp / "grow.jpg"
        with open(path, "wb") as fh:
            fh.write(JPEG_PLAIN)
            fh.truncate(200 * 1024 * 1024)
        real = os.fstat

        def lying(fd):
            m = real(fd)
            return os.stat_result((m.st_mode, m.st_ino, m.st_dev, m.st_nlink, m.st_uid, m.st_gid, 10, 0, 0, 0))
        tracemalloc.start()
        try:
            with mock.patch.object(FGS, "IMAGE_MAX_BYTES", 1024 * 1024), mock.patch.object(FGS.os, "fstat", lying):
                with self.assertRaises(FGS.ImageSkip) as ctx:
                    FGS.read_image(path, allowed=FGS.IMAGE_FEISHU_FORMATS)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(ctx.exception.reason, "too_large")
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_more_shapes_of_jpeg_png_webp_and_gif(self):
        """L1-FGS-142: 去元数据的边角：jpeg 头部的填充字节 FF、无长度的标记（TEM、RST、第二个 SOI）照走，APP14（Adobe）与 APP15 也去掉，长度为 0 的段是坏文件；
        png 的签名必须对；webp 必须是 WEBP 不是别的 RIFF，块的声明长度不能超出、VP8X 至少 10 字节，ALPH 保留；gif 的头必须对、块与块之间不能有杂字节。"""
        drop = JPEG_PLAIN[:2] + b"\xff" + b"\xff\xe1" + struct.pack(">H", 8) + b"Exif\x00\x00" + JPEG_PLAIN[2:]
        self.assertEqual(FGS.strip_metadata(drop, "jpeg"), JPEG_PLAIN)
        for standalone in (b"\xff\x01", b"\xff\xd0", b"\xff\xd8"):
            kept = JPEG_PLAIN[:2] + standalone + JPEG_PLAIN[2:]
            self.assertEqual(FGS.strip_metadata(kept, "jpeg"), kept, standalone)
        with self.assertRaises(ValueError):
            FGS.strip_metadata(b"\xff\xd8\xff\xe1\x00\x00" + JPEG_PLAIN[2:], "jpeg")
        with self.assertRaises(ValueError):
            FGS.strip_metadata(b"\x00" * 8 + PNG_IHDR + PNG_IDAT + PNG_IEND, "png")
        body = b"WAVE" + WEBP_VP8L[0] + struct.pack("<I", 7) + WEBP_VP8L[1] + b"\x00"
        inflated = b"WEBP" + b"VP8L" + struct.pack("<I", 1000) + bytes(8)
        for name, data in {"wave": b"RIFF" + struct.pack("<I", len(body)) + body,
                           "chunk longer than the file": b"RIFF" + struct.pack("<I", len(inflated)) + inflated,
                           "short VP8X": _webp((b"VP8X", b"\x10"), WEBP_VP8L)}.items():
            with self.assertRaises(ValueError, msg=name):
                FGS.strip_metadata(data, "webp")
        lossy = _webp(_vp8x(0x10), (b"ALPH", b"\x00\x01"), (b"VP8 ", bytes(10)))
        self.assertEqual(FGS.strip_metadata(lossy, "webp"), lossy)
        for name, data in {"bad header": b"NOTGIF" + GIF_PLAIN[6:], "stray byte": GIF_PLAIN[:19] + b"\x00" + GIF_PLAIN[19:]}.items():
            with self.assertRaises(ValueError, msg=name):
                FGS.strip_metadata(data, "gif")

    def test_whatever_a_damaged_file_looks_like_the_cleaner_only_ever_says_valueerror(self):
        """L1-FGS-143: 每种格式的样本在任意位置截断、任意一个字节取反之后，strip_metadata 要么给出结果、要么抛 ValueError——不会抛别的异常
        （IndexError、struct.error 之类会让一张坏图弄崩整轮）。"""
        for kind, sample in (("jpeg", JPEG_META), ("png", PNG_META), ("webp", WEBP_META), ("gif", GIF_META)):
            for n in range(len(sample) + 1):
                try:
                    FGS.strip_metadata(sample[:n], kind)
                except ValueError:
                    pass
            for i in range(len(sample)):
                try:
                    FGS.strip_metadata(sample[:i] + bytes([sample[i] ^ 0xFF]) + sample[i + 1:], kind)
                except ValueError:
                    pass


class ImageSurvivorRounds(TmpCase):
    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    put = ImagesToFeishu.put
    image_messages = ImagesToFeishu.image_messages
    item = ImagesToFeishuFailures.item

    def sub(self, name):
        path = self.tmp / name
        path.mkdir(mode=0o700)
        return FakeWorld(path), Env(path)

    def nested_reply_world(self):
        """根 R（人发的）、A（没有飞书 bot 的 agent 回复 R，不镜像）、B（agent 对 A 的嵌套回复，带一张图）：B 的直接父 A 在飞书上没有副本，
        文字挂在话题根 R 的副本下面（话题根补发的规则）；图必须跟着文字进同一个话题。
        A 不镜像靠的是 `buzz_unmanaged_agents: "skip"`（ADR-0019 起缺省代发）。"""
        self.env = Env(self.tmp, buzz_unmanaged_agents="skip")
        w = self.w
        sha = self.put(photo(90))
        w.events = [event(eid(1), ALICE_PK, "根"),
                    event(eid(2), AGENT2_PK, "A 回复根", created_at=T0 + 1, tags=[("e", eid(1), "", "reply")]),
                    image_event(3, AGENT_PK, "B 的图", [sha], created_at=T0 + 2,
                                tags=[("e", eid(1), "", "root"), ("e", eid(2), "", "reply")])]
        return w

    def test_the_image_of_a_reply_follows_its_text_to_the_thread_root(self):
        """L2-1-FGS-195: 嵌套回复的直接父消息在飞书上没有副本、文字挂在话题根的副本下面时，图也发到这个话题根下面（不能因为直接父没有副本就把图
        发到群里、离开话题）；幂等键仍是这张图自己的键。"""
        w = self.nested_reply_world()
        self.env.round(w)
        root_mid = next(m["message_id"] for m in w.messages if "根" in m["content"])
        text = next(c for c in w.lark_sends() if "--text" in c["args"] and text_of(c) == "B 的图")
        self.assertEqual(text["args"][text["args"].index("--message-id") + 1], root_mid)
        [up] = w.image_sends
        self.assertEqual(up["target"], ("reply", root_mid))
        self.assertEqual(up["key"], "b2f-img-" + hashlib.sha256(f"{eid(3)}:0".encode()).hexdigest()[:36])

    def test_a_late_image_still_goes_where_its_text_went(self):
        """L2-1-FGS-196: 文字已经发在话题根下面，图第一次没发出去（下载失败）、下一轮才发：仍然进同一个话题——文字去了哪里记在账本里，
        不靠下一轮重新推算（直接父没有副本，重新推算得到的是「没有话题」）。重试时是同一个接口、同一个键。"""
        w = self.nested_reply_world()
        w.media_fail = ["network"]
        self.env.round(w)
        self.assertEqual(w.image_sends, [])
        self.env.round(w, now=NOW + timedelta(minutes=1))
        root_mid = next(m["message_id"] for m in w.messages if "根" in m["content"])
        [up] = w.image_sends
        self.assertEqual(up["target"], ("reply", root_mid))
        self.assertEqual(self.env.state()["images"][f"{eid(3)}:0"], up["message_id"])

    def test_a_thread_root_that_is_backfilled_brings_its_images_along(self):
        """L2-1-FGS-197: 回复的话题根比绑定起点还早、飞书里没有副本，会先补发根（话题根补发）：根带的图片也一起补发（跟在根的文字后面），
        然后回复发在根的话题下面。补发的根按它自己的规则发（人的根走 owner bot 带署名），图是根所在位置（顶层）的图片消息。"""
        w = self.w
        sha = self.put(photo(95))
        w.events = [image_event(1, ALICE_PK, "老根", [sha], created_at=ts(NOW) - 3600),
                    event(eid(2), BOB_PK, "新回复", created_at=T0, tags=[("e", eid(1), "", "reply")])]
        report = self.env.round(w)
        sends = w.lark_sends()
        self.assertEqual([(c["args"][1], text_of(c) if "--text" in c["args"] else "<image>") for c in sends],
                         [("+messages-send", "Alice（Buzz）：老根"), ("+messages-send", "<image>"), ("+messages-reply", "Bob（Buzz）：新回复")])
        self.assertEqual(w.image_sends[0]["target"], ("chat", CHAT))
        self.assertEqual((report["thread_roots_backfilled"], report["images_to_feishu"], report["to_feishu"]), (1, 1, 2))
        root_mid = next(m["message_id"] for m in w.messages if "老根" in m["content"])
        self.assertEqual(sends[2]["args"][sends[2]["args"].index("--message-id") + 1], root_mid)

    def test_the_forty_five_minute_window_is_counted_from_the_first_attempt(self):
        """L2-1-FGS-189: 首次尝试起恰好 45 分钟还在重试（超过才关掉）：结果不确定的图在第 45 分钟整那一轮补发成功；晚一秒就记 unknown。"""
        for seconds, delivered in ((2700, True), (2701, False)):
            w, env = self.sub(f"s{seconds}")
            sha = sha_of(photo(91))
            w.media[sha] = photo(91)
            w.events = [image_event(1, AGENT_PK, "x", [sha])]
            w.lark_send_fail = [None, "timeout"]
            env.round(w)
            report = env.round(w, now=NOW + timedelta(seconds=seconds))
            self.assertEqual((report["images_to_feishu"], report["unknown"]), (1, 0) if delivered else (0, 1), seconds)

    def test_images_of_an_event_whose_text_may_have_been_delivered_are_not_sent_either(self):
        """L2-1-FGS-190: 文字结果不确定、超过 45 分钟记成 unknown（可能已经发出）：图也不发（说明有没有发出去不确定），在文字被关掉的那一轮计入
        text_not_sent（这个事件之后不会再被读到，不能指望下一轮），只计一次，账本记 skipped。"""
        w = self.w
        sha = self.put(photo(92))
        w.events = [image_event(1, AGENT_PK, "文字超时", [sha])]
        w.lark_send_fail = ["timeout"]
        self.env.round(w)
        closed = self.env.round(w, now=NOW + timedelta(minutes=50))
        self.assertEqual((closed["unknown"], closed["images_skipped"]), (1, {"text_not_sent": 1}))  # 事件此后不会再被读到
        later = self.env.round(w, now=NOW + timedelta(minutes=51))
        self.assertEqual(later["images_skipped"], {})
        self.assertEqual(self.env.state()["images"], {self.item(1, 0): "skipped"})
        self.assertEqual((w.media_reads, w.image_sends), ([], []))

    def test_an_image_whose_download_failed_is_closed_after_six_hours_when_the_event_is_gone(self):
        """L2-1-FGS-191: 下载失败的图（还没发过、只有重试标记）在事件读不到之后，6 小时到了也关掉，记 failed、images_failed 加一，而不是永远悬着。"""
        w = self.w
        sha = self.put(photo(93))
        w.events = [image_event(1, AGENT_PK, "x", [sha])]
        w.media_fail = ["network"]
        self.env.round(w)
        self.assertTrue(FGS._is_retry(self.env.state()["images"][self.item(1, 0)]))
        w.events = []
        report = self.env.round(w, now=NOW + timedelta(hours=7))
        self.assertEqual((self.env.state()["images"][self.item(1, 0)], report["images_failed"]), ("failed", 1))
        self.assertEqual(self.env.state()["img_unresolved"], {})

    def test_an_image_that_turns_out_to_be_a_policy_skip_after_a_retry_leaves_nothing_open(self):
        """L2-1-FGS-192: 第一次下载失败（重试中），第二次取到的是 html（策略跳过）：账本记 skipped、没有悬着的未决项——否则 6 小时后会被当成失败再报一次。"""
        w = self.w
        sha = sha_of(b"page")
        w.media[sha] = b"<html>sorry</html>"
        w.events = [image_event(1, AGENT_PK, "x", [sha])]
        w.media_fail = ["network"]
        self.env.round(w)
        report = self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual(report["images_skipped"], {"not_image": 1})
        self.assertEqual((self.env.state()["images"][self.item(1, 0)], self.env.state()["img_unresolved"]), ("skipped", {}))
        w.events = []
        later = self.env.round(w, now=NOW + timedelta(hours=7))
        self.assertEqual((later["images_failed"], later["unknown"]), (0, 0))

    def test_only_the_stripped_files_are_in_the_directory_when_the_message_is_sent(self):
        """L2-1-FGS-193: 发给 Buzz 时临时目录里只有要上传的（去掉元数据的）文件，每张图下载用的子目录已经删了。"""
        w = self.w
        w.resources[("om_s1", "img_v3_1")] = JPEG_META
        w.resources[("om_s1", "img_v3_2")] = PNG_META
        w.messages = [fmsg("om_s1", ALICE_OPEN, "两张\n![Image](img_v3_1)\n![Image](img_v3_2)", msg_type="post")]
        self.env.round(w)
        self.assertEqual([u["dir_files"] for u in w.buzz_uploads], [["up-0.jpg", "up-1.png"]] * 2)


class ImagesWithCards(TmpCase):
    """卡片模式（Buzz → 飞书默认）：卡片先发，图片作为随后的独立图片消息；卡片里没有外链图片语法；卡片的重试沿用首次的方式，图片等文字发出以后才跟上。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp, message_format="card")

    put = ImagesToFeishu.put
    image_messages = ImagesToFeishu.image_messages

    def test_the_card_goes_first_and_the_images_follow_as_their_own_messages(self):
        """L2-1-FGS-198: 卡片模式：agent 的事件带一张图——先发一张卡片（agent 自己的 bot；卡片里没有 `![image](…)` 也没有 relay 的媒体地址），
        再发图片消息（同一个 bot、同一个话题位置）；报告里 cards_sent 是 1、images_to_feishu 是 1。"""
        w = self.w
        sha = self.put(photo(100))
        w.events = [image_event(1, AGENT_PK, "证据图：页面 https://neopace.com/", [sha])]
        report = self.env.round(w)
        [card] = w.card_sends()
        self.assertEqual((card["app"], card["card"]["header"]["title"]["content"]), (AGENT_APP, "证据图：页面 https://neopace.com/"))  # 标题是第一行（#127）
        self.assertTrue(card["card"]["config"]["summary"]["content"].startswith("helper-agent"))  # the byline starts the summary
        raw = json.dumps(card["card"], ensure_ascii=False)
        self.assertIn("证据图：页面 https://neopace.com/", raw)
        self.assertNotIn("![", raw)
        self.assertNotIn("/media/", raw)
        sends = w.lark_sends()
        self.assertEqual([("--image" in c["args"]) for c in sends], [False, True])
        self.assertEqual((sends[1]["app"], w.image_sends[0]["target"]), (AGENT_APP, ("chat", CHAT)))
        self.assertEqual((report["cards_sent"], report["images_to_feishu"], report["to_feishu"]), (1, 1, 1))

    def test_no_markdown_image_syntax_reaches_the_card_in_any_part_of_it(self):
        """L2-1-FGS-199: 卡片里任何地方（预览、消息列表里的一行、折叠的全文）都没有 markdown 图片语法：手写的 `![图表](https://…)` 变成
        「[图片：图表] 地址」，和文字模式的规则一致；只有图片时卡片正文是「[图片]」。"""
        w = self.w
        long_text = "很长的说明\n" + "\n".join(f"第 {i} 行 ![图](https://example.test/{i}.png)" for i in range(12))
        w.events = [event(eid(1), AGENT_PK, long_text), image_event(2, AGENT_PK, "", [self.put(photo(101))], created_at=T0 + 1)]
        self.env.round(w)
        first, second = w.card_sends()
        raw = json.dumps(first["card"], ensure_ascii=False)
        self.assertNotIn("![", raw)
        self.assertIn("[图片：图] https://example.test/0.png", raw)
        self.assertIn("展开全文", raw)  # 全文折叠面板里同样处理过
        self.assertEqual(second["card"]["header"]["title"]["content"], "[图片]")  # 只有图片：标题就是「[图片]」（#127）
        self.assertNotIn("![", json.dumps(second["card"], ensure_ascii=False))
        self.assertEqual(len(self.image_messages()), 1)

    def test_a_reply_card_and_its_images_share_a_thread(self):
        """L2-1-FGS-200: 话题回复：卡片用 reply_card 挂在父消息下，图也是回复（同一个父消息）；话题根补发的根也是卡片，根带的图跟在根的卡片后面，
        回复卡片在根的话题下面；一条不多。"""
        w = self.w
        w.events = [image_event(1, ALICE_PK, "老根", [self.put(photo(102))], created_at=ts(NOW) - 3600),
                    image_event(2, AGENT_PK, "新回复", [self.put(photo(103))], created_at=T0, tags=[("e", eid(1), "", "reply")])]
        report = self.env.round(w)
        sends = w.lark_sends()
        self.assertEqual([(c["args"][1], "--image" in c["args"]) for c in sends],
                         [("+messages-send", False), ("+messages-send", True), ("+messages-reply", False), ("+messages-reply", True)])
        root_mid = next(m["message_id"] for m in w.messages if m["msg_type"] == "interactive")
        self.assertEqual([c["args"][c["args"].index("--message-id") + 1] for c in sends[2:]], [root_mid, root_mid])
        self.assertEqual([u["target"] for u in w.image_sends], [("chat", CHAT), ("reply", root_mid)])
        self.assertEqual((report["thread_roots_backfilled"], report["cards_sent"], report["images_to_feishu"]), (1, 2, 2))

    def test_a_card_feishu_refuses_is_said_as_text_and_the_images_still_follow(self):
        """L2-1-FGS-201: 飞书拒绝卡片内容时回退成文字（`,text` 账本标记不变）：文字没有图片语法，图片照发在文字之后、同一个发送者；重试标记与图片账本各管各的。"""
        w = self.w
        sha = self.put(photo(104))
        w.events = [image_event(1, AGENT_PK, "会被拒的卡片", [sha])]
        w.card_reject = ["content"]
        report = self.env.round(w)
        self.assertEqual((report["cards_fallback_text"], report["cards_sent"], report["images_to_feishu"]), (1, 0, 1))
        sends = w.lark_sends()
        self.assertEqual([("--text" in c["args"], "--image" in c["args"]) for c in sends if "--msg-type" not in c["args"]],
                         [(True, False), (False, True)])
        self.assertEqual(text_of(next(c for c in sends if "--text" in c["args"])), "会被拒的卡片")
        self.assertTrue(self.env.state()["b2f"][eid(1)].startswith("om_"))

    def test_a_card_that_is_retried_keeps_its_way_of_sending_and_the_images_wait_for_it(self):
        """L2-1-FGS-202: 卡片结果不确定（超时），图片不发（文字还没确认）；下一轮即使 message_format 已改成 text，卡片仍按首次的方式（卡片、同一个键）重试，
        成功以后图片才跟上；群里只有一张卡片、一张图，没有文字版。"""
        w = self.w
        sha = self.put(photo(105))
        w.events = [image_event(1, AGENT_PK, "慢的卡片", [sha])]
        w.lark_send_fail = ["timeout"]
        first = self.env.round(w)
        self.assertEqual((first["unknown"], w.image_sends, w.media_reads), (1, [], []))
        self.assertTrue(self.env.state()["b2f"][eid(1)].endswith(",card"))
        cfg = json.loads(self.env.config.read_text())
        write_owner_only(self.env.config, json.dumps(dict(cfg, message_format="text")))
        second = self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((second["cards_sent"], second["images_to_feishu"]), (1, 1))
        self.assertEqual(len([m for m in w.messages if m["msg_type"] == "interactive"]), 1)
        self.assertEqual(len({c["key"] for c in w.card_sends()}), 1)  # 两次尝试，同一个键
        self.assertEqual([c for c in w.lark_sends() if "--text" in c["args"]], [])
        self.assertEqual(len(self.image_messages()), 1)


class ImageSurvivorRoundsTwo(TmpCase):
    """变异检查第二轮活下来的变异逐个补的用例（等价变异不补，见提交说明）。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    put = ImagesToFeishu.put
    item = ImagesToFeishuFailures.item

    def sub(self, name):
        path = self.tmp / name
        path.mkdir(mode=0o700)
        return FakeWorld(path), Env(path)

    def test_the_overflow_is_counted_once_however_often_the_rest_is_dropped(self):
        """L2-1-FGS-203: Desk 失效时即使积压图片也整轮失败。"""
        w = self.w
        w.events = [image_event(1, AGENT_PK, "很多张", [self.put(photo(110 + i)) for i in range(11)])]
        w.media_fail = ["network"] * 20
        self.env.round(w)
        state = self.env.state()  # 文字已发出，图片账本还是空的（图片同步之前发出的文字）
        state.update(images={}, img_unresolved={}, attempts={})
        write_owner_only(self.env.state_dir / FGS.STATE_FILE, json.dumps(state))
        w.agent_dirs[str(self.tmp / "agent-cfg")] = "cli_somethingelse001"
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            self.env.round(w, now=NOW + timedelta(minutes=1))

    def test_a_human_event_with_more_than_nine_images_counts_the_overflow_too(self):
        """L2-1-FGS-204: 人发的事件超过 9 张也一样：前 9 张发出，多出的按 over_limit 计（人与 agent 走同一条判断，不因为发送者不同而丢掉这个数）。"""
        w = self.w
        w.events = [image_event(1, ALICE_PK, "很多张", [self.put(photo(130 + i)) for i in range(11)])]
        report = self.env.round(w)
        self.assertEqual((report["images_to_feishu"], report["images_skipped"]), (9, {"over_limit": 2}))

    def test_an_image_whose_text_was_sent_before_images_existed_follows_the_direct_parent(self):
        """L2-1-FGS-205: 账本里没有「文字发在哪里」（图片同步之前发出的文字）时，图片退回「直接父消息的副本」：回复的图仍发到父消息的话题里。"""
        w = self.w
        sha = self.put(photo(140))
        w.events = [event(eid(1), ALICE_PK, "父消息"),
                    image_event(2, AGENT_PK, "回复", [sha], created_at=T0 + 1, tags=[("e", eid(1), "", "reply")])]
        w.media_fail = ["network"]
        self.env.round(w)
        state = self.env.state()
        self.assertEqual(state["images"].pop(f"{eid(2)}:thread"), state["b2f"][eid(1)])
        write_owner_only(self.env.state_dir / FGS.STATE_FILE, json.dumps(state))
        self.env.round(w, now=NOW + timedelta(minutes=1))
        [up] = w.image_sends
        self.assertEqual(up["target"], ("reply", self.env.state()["b2f"][eid(1)]))

    def test_the_idempotency_window_of_an_image_counts_from_its_first_attempt_through_every_retry(self):
        """L2-1-FGS-206: 45 分钟的窗口从首次尝试算起，之后每次重试都不重置：被拒后 30 分钟再次被拒，第 50 分钟就已经过了窗口——直接关掉记 failed，
        不再第三次尝试；被拒后 30 分钟重试时结果不确定（pending），第 50 分钟同样关掉记 unknown、不再重发。"""
        for second, counter, ledger in (("rate_limited", "images_failed", "failed"), ("timeout", "unknown", "unknown")):
            w, env = self.sub(second)
            sha = sha_of(photo(150))
            w.media[sha] = photo(150)
            w.events = [image_event(1, AGENT_PK, "x", [sha])]
            w.lark_send_fail = [None, "rate_limited", second]
            env.round(w)
            env.round(w, now=NOW + timedelta(minutes=30))
            report = env.round(w, now=NOW + timedelta(minutes=50))
            self.assertEqual((report[counter], report["errors"], report["images_to_feishu"]), (1, 0, 0), second)
            self.assertEqual(env.state()["images"][self.item(1, 0)], ledger, second)
            self.assertEqual(w.lark_send_fail, [], second)

    def test_images_of_an_event_whose_text_can_no_longer_be_routed_are_dropped_with_it(self):
        """L2-1-FGS-207: Desk 失效时待重试文字和图片都暂停，不能回退 owner bot。"""
        w = self.w
        sha = self.put(photo(160))
        w.events = [image_event(1, AGENT_PK, "被拒的文字", [sha])]
        w.lark_send_fail = ["rate_limited"]
        self.env.round(w)
        w.agent_dirs[str(self.tmp / "agent-cfg")] = "cli_somethingelse001"
        with self.assertRaisesRegex(FGS.GroupSyncError, "Desk"):
            self.env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((w.media_reads, w.image_sends), ([], []))


class ImagesAndTheSenderAllowlist(TmpCase):
    """配置 feishu_sender_allowlist（!954）挡下的发言，图片也不能借道：下载、上传都在路由放行（返回 Inbound）之后。"""

    def test_a_sender_outside_the_allowlist_gets_no_image_downloaded_uploaded_or_sent(self):
        """L2-1-FGS-208: 名单外的发送者（已映射、在频道里，但不在 feishu_sender_allowlist 里）发的图片消息和富文本里的图：不下载（lark-cli 一次
        `+messages-resources-download` 都没有）、不上传、不发送，不写任何账本（f2b、attempts、images 都是空的），不建临时目录；计 sender_not_allowed。
        名单里的人发的图照常镜像（对照）。"""
        w = FakeWorld(self.tmp)
        env = Env(self.tmp, feishu_sender_allowlist=[BOB_PK])
        w.resources[("om_a1", "img_v3_a")] = JPEG_META
        w.resources[("om_a2", "img_v3_b")] = PNG_META
        w.messages = [fmsg("om_a1", ALICE_OPEN, "[Image: img_v3_a]", msg_type="image", when=NOW - timedelta(minutes=1)),
                      fmsg("om_a2", ALICE_OPEN, "看图\n![Image](img_v3_b)", msg_type="post", when=NOW - timedelta(seconds=50))]
        report = env.round(w)
        self.assertEqual(report["skipped"].get("sender_not_allowed"), 2)
        self.assertEqual((w.buzz_sends(), w.buzz_uploads, w.resource_downloads), ([], [], []))
        self.assertEqual([c for c in w.lark_calls if c["args"][:2] == ["im", "+messages-resources-download"]], [])
        state = env.state()
        self.assertEqual((state["f2b"], state["f_unresolved"], state["attempts"], state["images"]), ({}, {}, {}, {}))
        self.assertEqual((report["images_to_buzz"], report["images_failed"], report["images_skipped"], report["to_buzz"]), (0, 0, {}, 0))
        self.assertEqual(w.workdirs, set())
        w.resources[("om_b1", "img_v3_c")] = PNG_META
        w.messages.append(fmsg("om_b1", BOB_OPEN, "[Image: img_v3_c]", msg_type="image", when=NOW - timedelta(seconds=40)))
        later = env.round(w, now=NOW + timedelta(minutes=1))
        self.assertEqual((later["to_buzz"], later["images_to_buzz"], [d["message_id"] for d in w.resource_downloads]), (1, 1, ["om_b1"]))


class ImageReviewFindings(TmpCase):
    """!957 的 code-review 红线（F1–F5）与它们的边角：先有复现测试。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    put = ImagesToFeishu.put
    item = ImagesToFeishuFailures.item

    def sub(self, name):
        path = self.tmp / name
        path.mkdir(mode=0o700)
        return FakeWorld(path), Env(path)

    def test_a_terminal_text_settles_the_images_once_even_when_it_was_closed_somewhere_else(self):
        """L2-1-FGS-209（F1）：文字已经是终态（unknown：可能已送达但没有确认；failed：确定没发出）、图片还没有任何账本时——不管文字是在哪条路径上被关掉的
        （超过窗口、放弃积压、旧版本留下的 state ……）——事件再被读到时，图片不发、不下载，按 text_not_sent 计一次、账本记 skipped、没有悬着的未决项；
        再下一轮不重复计。"""
        for terminal in ("unknown", "failed"):
            w, env = self.sub(terminal)
            sha = sha_of(photo(170))
            w.media[sha] = photo(170)
            w.events = [image_event(1, AGENT_PK, "文字终态", [sha, sha_of(b"other")])]
            w.lark_send_fail = ["timeout"]
            env.round(w)  # 文字结果不确定（pending），图片没动
            state = env.state()
            state["b2f"][eid(1)] = terminal
            state["unresolved"].pop(eid(1), None)
            write_owner_only(env.state_dir / FGS.STATE_FILE, json.dumps(state))
            report = env.round(w, now=NOW + timedelta(minutes=1))
            self.assertEqual(report["images_skipped"], {"text_not_sent": 2}, terminal)
            self.assertEqual((env.state()["images"], env.state()["img_unresolved"]),
                             ({f"{eid(1)}:0": "skipped", f"{eid(1)}:1": "skipped"}, {}), terminal)
            self.assertEqual((w.media_reads, w.image_sends), ([], []), terminal)
            self.assertEqual(env.round(w, now=NOW + timedelta(minutes=2))["images_skipped"], {}, terminal)

    def test_pruning_never_cuts_the_ledger_of_an_image_that_is_still_open(self):
        """L1-FGS-210（F2）：images 账本和别的账本一样最多留 20000 条，但**不裁掉还有未决项的图片**（pending / retry：45 分钟窗口和重试次数就在它的值里），
        也不裁掉同一个事件的「文字发在哪里」与「超出张数」记录；裁的是最早的、已经有结果的。高消息量下（多出很多条）未决的图与它的 unresolved 项始终成对。"""
        open_item = f"{eid(0)}:0"
        images = {open_item: FGS._mark(FGS.PENDING, 100), f"{eid(0)}:thread": "om_root", f"{eid(0)}:over": "skipped"}
        images.update({f"{eid(i)}:0": "skipped" for i in range(1, FGS.LEDGER_KEEP + 50)})
        state = FGS.State(images=images, img_unresolved={open_item: 100})
        FGS.prune_state(state)
        self.assertEqual(len(state.images), FGS.LEDGER_KEEP)
        for key in (open_item, f"{eid(0)}:thread", f"{eid(0)}:over"):
            self.assertIn(key, state.images)
        self.assertNotIn(f"{eid(1)}:0", state.images)
        self.assertIn(f"{eid(FGS.LEDGER_KEEP + 49)}:0", state.images)
        self.assertEqual(set(state.img_unresolved), {open_item})
        many = FGS.State(images={f"{eid(i)}:0": FGS._mark(FGS.RETRY, 100) for i in range(FGS.LEDGER_KEEP + 5)},
                         img_unresolved={f"{eid(i)}:0": 100 for i in range(FGS.LEDGER_KEEP + 5)})
        FGS.prune_state(many)
        self.assertEqual(set(many.images), set(many.img_unresolved))  # 全是未决项：一个也不裁

    def test_a_message_given_up_after_refusals_still_settles_what_is_known_about_its_images(self):
        """L2-1-FGS-211（F4）：Buzz 三次确定拒绝、消息放弃（failed）时，图片这边已知的结果一并结算：被拒重试的中途不计（每次尝试都会重新下载，会重复计），
        放弃的那一次计一次——images_skipped 里的原因、下载出错的 images_failed。"""
        w = self.w
        w.resources[("om_f1", "img_v3_html")] = b"<html/>"
        w.resources[("om_f1", "img_v3_gone")] = PNG_PLAIN
        w.messages = [fmsg("om_f1", ALICE_OPEN, "看\n![Image](img_v3_html)\n![Image](img_v3_gone)", msg_type="post", when=NOW - timedelta(minutes=1))]
        w.buzz_send_fail = ["bad_input"] * 3
        w.resource_fail = [None, "network", None, "network", None, "network"]
        reports = [self.env.round(w, now=NOW + timedelta(minutes=m)) for m in range(3)]
        self.assertEqual([(r["images_skipped"], r["images_failed"]) for r in reports], [({}, 0), ({}, 0), ({"not_image": 1}, 1)])
        self.assertEqual((reports[2]["failed"], reports[2]["to_buzz"]), (1, 0))
        self.assertEqual(self.env.state()["f2b"]["om_f1"], "failed")

    def test_a_full_disk_costs_the_image_and_never_the_round_toward_feishu(self):
        """L2-1-FGS-212（F5，Buzz → 飞书）：本地临时目录建不出来、或改名 / 写文件出 OSError（磁盘满、权限）：这张图按下载失败处理（重试，errors 加一），
        文字和别的事件照发，整轮不中止；磁盘恢复后下一轮补上这张图，只有一张。"""
        for what in ("mkdtemp", "replace"):
            w, env = self.sub(what)
            sha = sha_of(photo(171))
            w.media[sha] = photo(171)
            w.events = [image_event(1, AGENT_PK, "文字", [sha]), event(eid(2), ALICE_PK, "别的事件", created_at=T0 + 1)]
            real_replace = os.replace

            def failing(name):
                def replace(src, dst, *a, **kw):  # 只有下载文件的改名失败；state 自己的原子写（也是 os.replace）不受影响
                    if name == "replace" and Path(str(src)).name.startswith("dl-"):
                        raise OSError(28, "No space left on device")
                    return real_replace(src, dst, *a, **kw)
                return replace
            patches = ([mock.patch.object(FGS.tempfile, "mkdtemp", side_effect=OSError(28, "No space left on device"))] if what == "mkdtemp"
                       else [mock.patch.object(FGS.os, "replace", failing(what))])
            with patches[0]:
                first = env.round(w)
            self.assertEqual(sorted(text_of(c) for c in w.lark_sends()), ["Alice（Buzz）：别的事件", "文字"], what)
            self.assertEqual((first["errors"], first["images_to_feishu"], first["to_feishu"]), (1, 0, 2), what)
            self.assertTrue(FGS._is_retry(env.state()["images"][self.item(1, 0)]), what)
            second = env.round(w, now=NOW + timedelta(minutes=1))
            self.assertEqual((second["images_to_feishu"], len(w.image_sends)), (1, 1), what)

    def test_a_full_disk_costs_the_image_and_never_the_round_toward_buzz(self):
        """L2-1-FGS-213（F5，飞书 → Buzz）：建不出临时目录、或写上传文件出 OSError：这张图计入 images_failed，文字照发（占位「[图片]」），
        整轮不中止；只有写文件出错的那张图受影响，同一条消息里别的图照发。"""
        w = self.w
        w.resources[("om_d1", "img_v3_only")] = JPEG_META
        w.messages = [fmsg("om_d1", ALICE_OPEN, "[Image: img_v3_only]", msg_type="image", when=NOW - timedelta(minutes=1))]
        with mock.patch.object(FGS.tempfile, "mkdtemp", side_effect=OSError(28, "No space left on device")):
            report = self.env.round(w)
        self.assertEqual([c["content"] for c in w.buzz_sends()], ["[飞书] Alice：[图片]"])
        self.assertEqual((report["to_buzz"], report["images_failed"], report["images_to_buzz"]), (1, 1, 0))
        sub_w, sub_env = self.sub("write")
        sub_w.resources[("om_d2", "img_v3_a")] = JPEG_META
        sub_w.resources[("om_d2", "img_v3_b")] = PNG_META
        sub_w.messages = [fmsg("om_d2", ALICE_OPEN, "两张\n![Image](img_v3_a)\n![Image](img_v3_b)", msg_type="post", when=NOW - timedelta(minutes=1))]
        real_open, seen = os.open, []

        def full_disk(path, flags, *a, **kw):
            if flags & os.O_EXCL:  # only the creation of an upload file
                seen.append(path)
                if len(seen) == 1:
                    raise OSError(28, "No space left on device")
            return real_open(path, flags, *a, **kw)
        with mock.patch.object(FGS.os, "open", full_disk):
            report = sub_env.round(sub_w)
        self.assertEqual([u["sha"] for u in sub_w.buzz_uploads], [sha_of(PNG_PLAIN)])
        self.assertEqual((report["to_buzz"], report["images_to_buzz"], report["images_failed"]), (1, 1, 1))


class ImageReviewFindingsMutants(TmpCase):
    """F5 的变异检查补测。"""

    def setUp(self):
        super().setUp()
        self.w = FakeWorld(self.tmp)
        self.env = Env(self.tmp)

    def test_a_download_directory_that_cannot_be_made_costs_only_that_image(self):
        """L2-1-FGS-214（F5）：飞书 → Buzz：某张图的下载子目录建不出来（OSError）：只有这张图计入 images_failed，同一条消息里别的图和文字照发。"""
        w = self.w
        w.resources[("om_m1", "img_v3_a")] = JPEG_META
        w.resources[("om_m1", "img_v3_b")] = PNG_META
        w.messages = [fmsg("om_m1", ALICE_OPEN, "两张\n![Image](img_v3_a)\n![Image](img_v3_b)", msg_type="post", when=NOW - timedelta(minutes=1))]
        real_mkdir = Path.mkdir

        def mkdir(self, *a, **kw):
            if self.name == "in-0":
                raise OSError(28, "No space left on device")
            return real_mkdir(self, *a, **kw)
        with mock.patch.object(Path, "mkdir", mkdir):
            report = self.env.round(w)
        self.assertEqual([u["sha"] for u in w.buzz_uploads], [sha_of(PNG_PLAIN)])
        self.assertEqual((report["to_buzz"], report["images_to_buzz"], report["images_failed"]), (1, 1, 1))

    def test_a_file_that_cannot_be_removed_right_after_the_send_does_not_stop_the_round(self):
        """L2-1-FGS-215（F5）：Buzz → 飞书：图片发出去以后删本地文件出 OSError（权限）：不抛出、图算已发出（账本记消息 id）、整个目录最后照样清理。"""
        w = self.w
        sha = sha_of(photo(172))
        w.media[sha] = photo(172)
        w.events = [image_event(1, AGENT_PK, "文字", [sha])]
        real_unlink = Path.unlink

        def unlink(self, *a, **kw):
            if self.name.startswith("img-"):
                raise PermissionError(13, "Permission denied")
            return real_unlink(self, *a, **kw)
        with mock.patch.object(Path, "unlink", unlink):
            report = self.env.round(w)
        self.assertEqual((report["images_to_feishu"], report["errors"]), (1, 0))
        self.assertTrue(self.env.state()["images"][f"{eid(1)}:0"].startswith("om_"))
        for path in w.workdirs:
            self.assertFalse(os.path.exists(path), path)


if __name__ == "__main__":
    unittest.main()
