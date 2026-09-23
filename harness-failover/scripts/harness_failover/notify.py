"""Lark notifications: tell the human when a harness is exhausted, when a switch succeeded, and when anything failed.

Design rules
  * Message text comes from fixed templates plus values that passed a strict character filter — never from logs,
    error strings or channel content, so a notification can leak neither a secret nor attacker-controlled text.
  * Sending is best effort: a broken/absent lark-cli must never change the outcome or exit code of a failover.
  * De-duplicated by state transition: one-off events (exhaustion episode, each switch) fire once; things that need
    a human (stuck, failures) repeat at most every 6 hours.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass

from . import learn
from .retry import safe_label

UTC = dt.timezone.utc
HEAD = "[harness-failover]"
REMIND_HOURS = 6.0
KEEP = dt.timedelta(days=7)
MAX_NAMES = 8
OPEN_ID = re.compile(r"^ou_[A-Za-z0-9]{6,64}$")
IDENTITIES = ("bot", "user")
_SAFE_TEXT = re.compile(r"[^A-Za-z0-9._\[\]+\- ]")  # no ":" or "/": a value can never become a link
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,39}")


class NotifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Message:
    kind: str
    key: str
    text: str
    remind_hours: float | None = None  # None: one-off (the key is unique per event)


# ───────────────────────────── composing ─────────────────────────────
def _clean(value, limit=40) -> str:
    return _SAFE_TEXT.sub("", str(value))[:limit].strip()


def _until(until) -> str:
    return until.astimezone(UTC).strftime("%Y-%m-%d %H:%MZ") if until else "未知"


def _names(names) -> str:
    names = list(names)
    shown = [safe_label(n) for n in names[:MAX_NAMES]]
    more = f" 等共 {len(names)} 个" if len(names) > MAX_NAMES else ""
    return ", ".join(shown) + more


def _profile(p) -> str:
    return f"{safe_label(p.id)}（{_clean(p.model)}/{_clean(p.effort)}）"


def _tail(host) -> str:
    return f"主机：{_clean(host)}"


def compose_exhausted(current, until, target=None, agents=0, host="") -> Message:
    cur = safe_label(current)
    stamp = until.astimezone(UTC).strftime("%Y%m%dT%H%M") if until else "na"
    if target is None:
        text = "\n".join([f"{HEAD} 额度耗尽且没有可用备选：{cur}", f"恢复时间：{_until(until)}",
                          f"{int(agents)} 个 agent 正在失败、消息会被丢弃，需要人工介入（例如登录 codex-buzz，或等待额度恢复）。",
                          "未恢复前每 6 小时提醒一次。", _tail(host)])
        return Message("stuck", f"stuck:{cur}:{stamp}", text, REMIND_HOURS)
    text = "\n".join([f"{HEAD} 额度耗尽：{cur}", f"恢复时间：{_until(until)}",
                      f"正在把 {int(agents)} 个 agent 一起切到 {_profile(target)}…", _tail(host)])
    # Unknown reset time: the key cannot tell episodes apart, so let it repeat (at most every 6h) instead of being
    # suppressed for the whole retention window.
    return Message("exhausted", f"exhausted:{cur}:{stamp}", text, REMIND_HOURS if until is None else None)


def compose_switched(from_id, target, agents, retry=None, host="", now=None) -> Message:
    lines = [f"{HEAD} 切换成功：{safe_label(from_id)} → {_profile(target)}",
             f"{int(agents)} 个 agent 已确认加载新配置"]
    if retry is not None:
        line = f"重试：已提醒 {int(retry.get('sent', 0))} 条丢失的消息"
        if retry.get("manual"):
            line += f"，{int(retry['manual'])} 条需人工处理"
        if retry.get("errors"):
            line += f"，{int(retry['errors'])} 条发送失败（下一轮继续）"
        lines.append(line)
    lines.append(_tail(host))
    stamp = (now or dt.datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    return Message("switched", f"switched:{safe_label(target.id)}:{stamp}", "\n".join(lines), None)


def _abort_reason(reason: str) -> tuple[str, str]:
    """(headline, category). The raw reason is never echoed: it may contain paths or secrets."""
    r = str(reason).lower()
    if "rollback incomplete" in r:
        return "切换失败且回滚不完整，请按 .bak 文件手工恢复", "rollback-incomplete"
    if "0600" in r or "symlink" in r or "owner" in r:
        return "切换失败，已回滚，没有重启任何 agent（env 文件权限或符号链接不合规）", "permissions"
    if "launcher" in r:
        return "切换失败，已回滚，没有重启任何 agent（启动脚本不支持该 profile）", "launcher"
    if "verification" in r or "sourced" in r:
        return "切换失败，已回滚，没有重启任何 agent（env 验证不通过）", "verification"
    return "切换失败，已回滚，没有重启任何 agent（原因见日志）", "other"


def compose_failure(kind, host="", **facts) -> Message:
    if kind == "aborted":
        headline, detail = _abort_reason(facts.get("reason", ""))
    elif kind == "partial":
        names = list(facts.get("names", []))
        headline, detail = f"切换后有 {len(names)} 个 agent 未正常加载新配置：{_names(names)}（下一轮会自动修复）", _names(sorted(names))
    elif kind == "unidentified":
        headline, detail = "无法识别当前 harness，已拒绝切换（agent 的 env 与任何 profile 都对不上）", ""
    elif kind == "retry":
        manual, errors = int(facts.get("manual", 0)), int(facts.get("errors", 0))
        headline, detail = f"重试提醒需要人工处理：{manual} 条丢失消息读不到，{errors} 条发送失败", ""  # counts move every tick: not part of the key
    elif kind == "drift":
        names = list(facts.get("names", []))
        headline, detail = f"配置漂移修复未完成：{_names(names)}", _names(sorted(names))
    elif kind == "error":
        m = _IDENT.match(str(facts.get("error_type", "")))
        et = m.group(0) if m else "Exception"
        headline, detail = f"运行异常：{et}（详见 systemd 日志）", et
    else:
        headline, detail = "运行失败（详见 systemd 日志）", ""
    digest = hashlib.sha1(f"{kind}|{detail}".encode()).hexdigest()[:10]
    text = "\n".join([f"{HEAD} {headline}", "未恢复前每 6 小时提醒一次。", _tail(host)])
    return Message("failure", f"fail:{kind}:{digest}", text, REMIND_HOURS)


def compose_repaired(names, host="", now=None) -> Message:
    names = list(names)
    stamp = (now or dt.datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    text = "\n".join([f"{HEAD} 配置漂移已修复：重启了 {len(names)} 个用着旧配置的 agent：{_names(names)}", _tail(host)])
    return Message("repaired", f"repaired:{stamp}", text, None)


# ───────────────────────────── de-duplication ─────────────────────────────
def _parse(iso):
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


SKEW = dt.timedelta(minutes=5)


def _valid(when, now) -> bool:
    return when is not None and when <= now + SKEW  # a timestamp from the future is never trusted


def should_send(state, msg, now) -> bool:
    state = state if isinstance(state, dict) else {}
    last = _parse(state.get(msg.key))
    if not _valid(last, now):
        return True
    if msg.remind_hours is None:
        return False
    return now - last >= dt.timedelta(hours=msg.remind_hours)


def record(state, msg, now) -> dict:
    out = {}
    for key, iso in (state if isinstance(state, dict) else {}).items():
        when = _parse(iso)
        if _valid(when, now) and now - when <= KEEP:
            out[key] = iso
    out[msg.key] = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


# ───────────────────────────── sending ─────────────────────────────
def _one_line(text) -> str:
    return re.sub(r"\s+", " ", learn.redact(str(text or ""))).strip()[:100]


class LarkNotifier:
    """Sends through `lark-cli im +messages-send` as the app bot to your own open_id."""

    timeout = 30.0

    def __init__(self, home, which=None, host=None):
        self.home = home
        self.which = which or self._find_lark_cli
        self.host = host or socket.gethostname().split(".")[0]
        self.dir = f"{home}/.config/buzz/harness-failover"
        self.path = f"{self.dir}/notify.json"

    def _find_lark_cli(self):
        found = shutil.which("lark-cli")
        if found:
            return found
        candidates = sorted(glob.glob(f"{self.home}/.nvm/versions/node/*/bin/lark-cli"))
        return candidates[-1] if candidates else None

    def _config(self):
        try:
            return json.load(open(self.path))
        except (OSError, ValueError):
            return None

    def idempotency_key(self, msg) -> str:
        return "hf-" + hashlib.sha1(msg.key.encode()).hexdigest()[:24]

    def _env(self, exe) -> dict:
        # node lives next to lark-cli (nvm); a systemd timer's PATH does not include it. No secrets are passed.
        return {"HOME": self.home, "PATH": f"{os.path.dirname(exe)}:/usr/bin:/bin", "LANG": "C.UTF-8"}

    def _validate(self, cfg):
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            return "", None  # unconfigured / disabled: silent
        if not OPEN_ID.match(str(cfg.get("recipient_open_id", ""))):
            return "invalid recipient in notify.json", None
        if cfg.get("identity") not in IDENTITIES:
            return "invalid identity in notify.json", None
        exe = str(cfg.get("lark_cli", ""))
        if not exe.startswith("/"):
            return "lark_cli in notify.json must be an absolute path", None
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            return "lark-cli not found", None
        return None, cfg

    def status(self) -> dict:
        cfg = self._config()
        problem, ok = self._validate(cfg)
        if not cfg or not cfg.get("enabled"):
            return {"enabled": False, "reason": "not configured (run `notify --setup`)"}
        oid = str(cfg.get("recipient_open_id", ""))
        return {"enabled": ok is not None, "identity": cfg.get("identity"), "recipient": f"ou_…{oid[-3:]}",
                "lark_cli": cfg.get("lark_cli"), **({"problem": problem} if problem else {})}

    def setup(self) -> dict:
        exe = self.which()
        if not exe:
            raise NotifyError("lark-cli not found on PATH or under ~/.nvm")
        try:
            p = subprocess.run([exe, "auth", "status"], env=self._env(exe), capture_output=True, text=True,
                               timeout=self.timeout, stdin=subprocess.DEVNULL)
            user = json.JSONDecoder().raw_decode(p.stdout.lstrip())[0]["identities"]["user"]
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as e:
            raise NotifyError(f"could not read `lark-cli auth status` ({type(e).__name__})") from e
        oid = str(user.get("openId", ""))
        if user.get("status") != "ready" or not OPEN_ID.match(oid):
            raise NotifyError("lark-cli user identity is not ready — run `lark-cli auth login`")
        cfg = {"enabled": True, "recipient_open_id": oid, "identity": "bot", "lark_cli": exe}
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".notify.")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f)
        os.replace(tmp, self.path)
        return cfg

    def send(self, msg):
        """(ok, why). Never raises; an unconfigured notifier returns (False, "") so callers stay quiet."""
        problem, cfg = self._validate(self._config())
        if cfg is None:
            return False, problem or ""
        exe = cfg["lark_cli"]
        argv = [exe, "im", "+messages-send", "--user-id", cfg["recipient_open_id"], "--text", msg.text,
                "--as", cfg["identity"], "--idempotency-key", self.idempotency_key(msg)]
        try:
            p = subprocess.run(argv, env=self._env(exe), capture_output=True, text=True, timeout=self.timeout,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, "lark-cli timed out"
        except OSError as e:
            return False, f"lark-cli failed to start ({type(e).__name__})"
        if p.returncode != 0:
            return False, f"lark-cli exit {p.returncode}: {_one_line(p.stderr or p.stdout)}"
        try:
            out = json.JSONDecoder().raw_decode(p.stdout.lstrip())[0]
        except ValueError:
            out = None
        if isinstance(out, dict) and out.get("ok") is False:
            return False, f"lark-cli reported failure: {_one_line(out.get('error') or out.get('msg'))}"
        return True, ""
