#!/usr/bin/env python3
"""Phase 0 feasibility probes against a throwaway local relay (engineering/skills#186).

P0-13  the localstack relay (pinned image) and the 0.5.23 CLI come up
P0-12  Python websockets: NIP-42 AUTH, then REQ on #h (channel) and on authors; push latency
P0-10  which event can carry a "receipt" (Buzz event id <-> Feishu message id): custom tags on
       kind 9, kind 30078 with a d tag, and an unknown kind; who can read each back

Only localhost relays; all keys are generated here and never printed. Writes one redacted JSON
receipt to tests/fixtures/hostd/probes/relay.json and tears the containers down unless --keep.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent.parent
sys.path.insert(0, str(SKILL / "tests" / "localstack"))
import stack as st  # noqa: E402  (relay start/bootstrap, identities, raw CLI with guards)
import nostrkit as nk  # noqa: E402

import websockets  # noqa: E402

OUT = SKILL / "tests" / "fixtures" / "hostd" / "probes" / "relay.json"


def sign(ident: dict, kind: int, tags: list, content: str, created_at: int | None = None) -> dict:
    created_at = created_at or int(time.time())
    body = [0, ident["pubkey"], created_at, kind, tags, content]
    ser = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    import hashlib
    eid = hashlib.sha256(ser).digest()
    sig = nk.schnorr_sign(eid, bytes.fromhex(ident["secret"]), secrets.token_bytes(32))
    return {"id": eid.hex(), "pubkey": ident["pubkey"], "created_at": created_at, "kind": kind,
            "tags": tags, "content": content, "sig": sig.hex()}


class Conn:
    """A minimal NIP-42 client: answers AUTH, sends EVENT / REQ, collects frames."""

    def __init__(self, url: str, ident: dict):
        self.url, self.ident = st.guard_local(url), ident
        self.frames: asyncio.Queue = asyncio.Queue()
        self.ok: dict[str, tuple[bool, str]] = {}
        self.authed = asyncio.Event()

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url, open_timeout=15, max_size=2**22)
        self.reader = asyncio.create_task(self._read())
        await asyncio.wait_for(self.authed.wait(), 15)
        return self

    async def __aexit__(self, *exc):
        self.reader.cancel()
        await self.ws.close()

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if msg[0] == "AUTH" and isinstance(msg[1], str):
                tags = [["relay", self.url], ["challenge", msg[1]]]
                if self.ident.get("auth_tag"):
                    tags.append(json.loads(self.ident["auth_tag"]))
                await self.ws.send(json.dumps(["AUTH", sign(self.ident, 22242, tags, "")]))
            elif msg[0] == "OK":
                self.ok[msg[1]] = (bool(msg[2]), msg[3] if len(msg) > 3 else "")
                if not self.authed.is_set() and msg[2]:
                    self.authed.set()
            else:
                await self.frames.put(msg)

    async def publish(self, event: dict, timeout: float = 10) -> tuple[bool, str]:
        await self.ws.send(json.dumps(["EVENT", event]))
        end = time.monotonic() + timeout
        while event["id"] not in self.ok and time.monotonic() < end:
            await asyncio.sleep(0.02)
        return self.ok.get(event["id"], (False, "timeout"))

    async def req(self, sub: str, flt: dict) -> None:
        await self.ws.send(json.dumps(["REQ", sub, flt]))

    async def until(self, pred, timeout: float):
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            try:
                msg = await asyncio.wait_for(self.frames.get(), left)
            except asyncio.TimeoutError:
                return None
            if pred(msg):
                return msg

    async def fetch(self, flt: dict, timeout: float = 8) -> list[dict]:
        sub = "q" + secrets.token_hex(4)
        await self.req(sub, flt)
        got = []
        while True:
            msg = await self.until(lambda m: len(m) > 1 and m[1] == sub, timeout)
            if msg is None or msg[0] in ("EOSE", "CLOSED"):
                await self.ws.send(json.dumps(["CLOSE", sub]))
                return got
            if msg[0] == "EVENT":
                got.append(msg[2])


async def probe(relay: dict, ids: dict, channel: str, outsider_channel: str) -> dict:
    url = relay["ws_url"]
    r: dict = {}
    async with Conn(url, ids["mirror"]) as mirror, Conn(url, ids["desk"]) as desk, Conn(url, ids["outsider"]) as outsider:
        r["nip42_auth"] = True
        # P0-12: subscribe by channel (#h) and by author, then measure push latency
        await desk.req("byh", {"#h": [channel], "kinds": [9], "since": int(time.time()) - 5})
        await mirror.req("byauthor", {"authors": [ids["agent"]["pubkey"]], "kinds": [9, 7], "since": int(time.time()) - 5})
        await asyncio.sleep(0.5)
        async with Conn(url, ids["agent"]) as agent:
            lat = []
            for i in range(5):
                ev = sign(agent.ident, 9, [["h", channel]], f"hostd probe latency {i}")
                t0 = time.monotonic()
                ok = await agent.publish(ev)
                assert ok[0], ok
                a = await desk.until(lambda m: m[0] == "EVENT" and m[1] == "byh" and m[2]["id"] == ev["id"], 5)
                t1 = time.monotonic()
                b = await mirror.until(lambda m: m[0] == "EVENT" and m[1] == "byauthor" and m[2]["id"] == ev["id"], 5)
                t2 = time.monotonic()
                lat.append({"by_h_ms": None if a is None else round((t1 - t0) * 1000), "by_author_ms": None if b is None else round((t2 - t0) * 1000)})
            r["push_latency"] = lat

            # P0-10: receipt carriers
            target = sign(agent.ident, 9, [["h", channel]], "hostd probe target message")
            assert (await agent.publish(target))[0]
            fake_om = "om_" + "0" * 32
            cands = {
                "kind9_custom_tag": sign(mirror.ident, 9, [["h", channel], ["e", target["id"], "", "reply"], ["feishu-receipt", target["id"], fake_om]], ""),
                "kind7_reaction_tag": sign(mirror.ident, 7, [["h", channel], ["e", target["id"]], ["p", target["pubkey"]], ["feishu-receipt", target["id"], fake_om]], "+"),
                "kind30078_d_tag": sign(agent.ident, 30078, [["d", f"feishu-receipt:{target['id']}"], ["h", channel], ["feishu-receipt", target["id"], fake_om]], json.dumps({"om": fake_om})),
                "kind30078_no_h": sign(agent.ident, 30078, [["d", f"feishu-receipt2:{target['id']}"], ["feishu-receipt", target["id"], fake_om]], json.dumps({"om": fake_om})),
                "unknown_kind_31990": sign(mirror.ident, 31990, [["d", "x"], ["h", channel]], ""),
                "unknown_kind_4242": sign(mirror.ident, 4242, [["h", channel]], ""),
            }
            pubs = {}
            for name, ev in cands.items():
                who = agent if ev["pubkey"] == agent.ident["pubkey"] else mirror
                pubs[name] = await who.publish(ev)
            r["receipt_publish"] = {k: {"accepted": v[0], "reason": v[1][:120]} for k, v in pubs.items()}
            await asyncio.sleep(0.5)
            reads = {}
            for name, ev in cands.items():
                if not pubs[name][0]:
                    continue
                row = {}
                for who, conn in (("desk_member", desk), ("outsider_nonmember", outsider)):
                    got = await conn.fetch({"ids": [ev["id"]]})
                    row[who] = bool(got)
                    tagq = await conn.fetch({"#feishu-receipt": [target["id"]], "kinds": [ev["kind"]]})
                    row[who + "_by_custom_tag_filter"] = any(x["id"] == ev["id"] for x in tagq)
                reads[name] = row
            r["receipt_read"] = reads
            # can a single-letter tag carry the target? (#e filter is indexed by all relays)
            got = await desk.fetch({"#e": [target["id"]], "kinds": [9]})
            r["kind9_reply_findable_by_e_filter"] = any(x["id"] == cands["kind9_custom_tag"]["id"] for x in got)
            # does the custom tag survive storage byte-for-byte?
            back = await desk.fetch({"ids": [cands["kind9_custom_tag"]["id"]]})
            r["custom_tag_preserved"] = bool(back) and ["feishu-receipt", target["id"], fake_om] in back[0]["tags"]
            # outsider: a non-member reading the private channel at all
            r["outsider_can_read_private_channel"] = bool(await outsider.fetch({"#h": [channel], "kinds": [9], "limit": 5}))
            _ = outsider_channel
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="leave the containers running")
    args = ap.parse_args()
    st.private_dir(st.STATE_DIR); st.private_dir(st.SECRETS_DIR)
    if st.STATE_FILE.exists():
        print("refusing: a localstack state already exists in this worktree; run stack.py down first", file=sys.stderr)
        return 2
    image = st.DEFAULT_RELAY_IMAGE
    receipt: dict = {"probe": "hostd-relay", "relay_image": image, "relay_digest": st.PINNED_DIGESTS[image],
                     "cli": str(st.BUZZ_CLI.name) + " 0.5.23", "started_at": int(time.time())}
    st.ensure_image(image)
    ids = {n: st.new_identity(n) for n in ("owner", "relay", "mirror", "desk", "agent", "outsider")}
    for n in ("mirror", "desk", "agent"):
        ids[n]["auth_tag"] = st.attest(ids["owner"], ids[n])
    t0 = time.monotonic()
    try:
        relay = st.start_relay_stack(image, ids["owner"], ids["relay"])
        receipt["P0-13"] = {"relay_ready_s": round(time.monotonic() - t0, 1), "ws_url_is_loopback": relay["ws_url"].startswith("ws://127.0.0.1:")}
        for n in ("mirror", "desk", "agent", "outsider"):
            st.docker("exec", st.NAMES["relay"], "buzz-admin", "add-member", "--pubkey", ids[n]["pubkey"], timeout=60)
        http = relay["http_url"]
        ch = st.parse_json(st.buzz(ids["owner"], "channels", "create", "--name", f"hostd-probe-{secrets.token_hex(3)}",
                                   "--type", "stream", "--visibility", "private", relay_http=http).stdout, "create")["channel_id"]
        for n, role in (("mirror", "bot"), ("desk", "bot"), ("agent", "bot")):
            st.buzz(ids["owner"], "channels", "add-member", "--channel", ch, "--pubkey", ids[n]["pubkey"], "--role", role, relay_http=http)
        other = st.parse_json(st.buzz(ids["owner"], "channels", "create", "--name", f"hostd-probe-other-{secrets.token_hex(3)}",
                                      "--type", "stream", "--visibility", "private", relay_http=http).stdout, "create")["channel_id"]
        receipt["P0-13"]["cli_channel_ops"] = True
        r = asyncio.run(probe(relay, ids, ch, other))
        receipt["P0-12"] = {"nip42_auth": r["nip42_auth"], "push_latency": r["push_latency"]}
        receipt["P0-10"] = {k: r[k] for k in ("receipt_publish", "receipt_read", "kind9_reply_findable_by_e_filter",
                                              "custom_tag_preserved", "outsider_can_read_private_channel")}
        p10, p12 = receipt["P0-10"], receipt["P0-12"]
        checks = {
            "relay_ws_is_loopback": receipt["P0-13"]["ws_url_is_loopback"],
            "every_push_by_h_arrived": all(x["by_h_ms"] is not None for x in p12["push_latency"]),
            "push_by_h_under_1s": all((x["by_h_ms"] or 10**6) < 1000 for x in p12["push_latency"]),
            "unknown_kinds_rejected": not p10["receipt_publish"]["unknown_kind_31990"]["accepted"] and not p10["receipt_publish"]["unknown_kind_4242"]["accepted"],
            "kind9_custom_tag_accepted": p10["receipt_publish"]["kind9_custom_tag"]["accepted"],
            "kind9_custom_tag_preserved": p10["custom_tag_preserved"],
            "kind9_hidden_from_nonmember": not p10["receipt_read"]["kind9_custom_tag"]["outsider_nonmember"],
            "kind30078_visible_to_nonmember_as_expected": p10["receipt_read"].get("kind30078_d_tag", {}).get("outsider_nonmember") is True,
            "private_channel_hidden_from_nonmember": not p10["outsider_can_read_private_channel"],
        }
        receipt["failed_checks"] = sorted(k for k, v in checks.items() if not v)
        receipt["ok"] = not receipt["failed_checks"]
    except Exception as exc:  # report, then tear down
        receipt["ok"] = False
        receipt["error"] = st.redact(f"{type(exc).__name__}: {exc}")[:600]
        st.save_container_logs()
    finally:
        if not args.keep:
            receipt["teardown"] = st.remove_relay_stack()
    receipt["finished_at"] = int(time.time())
    OUT.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
