#!/usr/bin/env python3
"""P0-12b: which live subscription shapes push an agent's events to a second connection (#186)."""
from __future__ import annotations
import asyncio, json, secrets, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "localstack"))
import stack as st  # noqa: E402
from relay_probe import Conn, sign  # noqa: E402

OUT = HERE.parent / "fixtures" / "hostd" / "probes" / "relay-authors.json"

async def run(relay, ids, ch, ch2):
    url = relay["ws_url"]; res = {}
    variants = {
        "authors_only": {"authors": [ids["agent"]["pubkey"]]},
        "authors_kinds": {"authors": [ids["agent"]["pubkey"]], "kinds": [9]},
        "authors_since": {"authors": [ids["agent"]["pubkey"]], "kinds": [9], "since": int(time.time()) - 5},
        "authors_and_h": {"authors": [ids["agent"]["pubkey"]], "#h": [ch], "kinds": [9]},
        "authors_and_h_two_channels": {"authors": [ids["agent"]["pubkey"]], "#h": [ch, ch2], "kinds": [9]},
        "h_two_channels": {"#h": [ch, ch2], "kinds": [9]},
    }
    for who in ("self", "other_member"):
        ident = ids["agent"] if who == "self" else ids["mirror"]
        async with Conn(url, ident) as sub, Conn(url, ids["agent"]) as pub:
            for name in variants: await sub.req(f"{who}-{name}", variants[name])
            await asyncio.sleep(0.6)
            for target_ch, label in ((ch, "ch1"), (ch2, "ch2")):
                ev = sign(pub.ident, 9, [["h", target_ch]], f"authors probe {label}")
                t0 = time.monotonic(); assert (await pub.publish(ev))[0]
                seen = {}
                end = time.monotonic() + 3
                while time.monotonic() < end and len(seen) < len(variants):
                    m = await sub.until(lambda m: m[0] == "EVENT" and m[2]["id"] == ev["id"], end - time.monotonic())
                    if m is None: break
                    seen[m[1].split("-", 1)[1]] = round((time.monotonic() - t0) * 1000)
                for name in variants:
                    res.setdefault(who, {}).setdefault(name, {})[label] = seen.get(name)
            closed = []
            while not sub.frames.empty():
                m = sub.frames.get_nowait()
                if m[0] == "CLOSED": closed.append([m[1], m[2][:120] if len(m) > 2 else ""])
            res[who + "_closed"] = closed
    return res

def main():
    st.private_dir(st.STATE_DIR)
    ids = {n: st.new_identity(n) for n in ("owner", "relay", "mirror", "agent")}
    for n in ("mirror", "agent"): ids[n]["auth_tag"] = st.attest(ids["owner"], ids[n])
    out = {"probe": "hostd-relay-authors", "relay_image": st.DEFAULT_RELAY_IMAGE}
    try:
        relay = st.start_relay_stack(st.DEFAULT_RELAY_IMAGE, ids["owner"], ids["relay"])
        for n in ("mirror", "agent"): st.docker("exec", st.NAMES["relay"], "buzz-admin", "add-member", "--pubkey", ids[n]["pubkey"], timeout=60)
        http = relay["http_url"]; chans = []
        for i in range(2):
            c = st.parse_json(st.buzz(ids["owner"], "channels", "create", "--name", f"hostd-a-{i}-{secrets.token_hex(2)}", "--type", "stream", "--visibility", "private", relay_http=http).stdout, "c")["channel_id"]
            for n in ("mirror", "agent"): st.buzz(ids["owner"], "channels", "add-member", "--channel", c, "--pubkey", ids[n]["pubkey"], "--role", "bot", relay_http=http)
            chans.append(c)
        res = out["result"] = asyncio.run(run(relay, ids, *chans))
        # The ADR-0026 claim: authors + a single #h gets a live push on that channel; this is what the sender relies on.
        # A run where nothing arrives at all is a broken harness, not a finding, so it fails too.
        got = lambda who, v, ch: res.get(who, {}).get(v, {}).get(ch) is not None
        checks = {f"{who}:authors_and_h_ch1_pushed": got(who, "authors_and_h", "ch1") for who in ("self", "other_member")}
        checks["self:authors_and_h_ch2_not_pushed"] = not got("self", "authors_and_h", "ch2")
        checks["harness_received_any_push"] = any(got(w, v, c) for w in ("self", "other_member") for v in res.get(w, {}) for c in ("ch1", "ch2"))
        out["observed_authors_only_pushes"] = any(got(w, "authors_only", c) for w in ("self", "other_member") for c in ("ch1", "ch2"))
        out["failed_checks"] = sorted(k for k, v in checks.items() if not v)
        out["ok"] = not out["failed_checks"]
    except Exception as e:
        out["ok"] = False; out["error"] = st.redact(f"{type(e).__name__}: {e}")[:500]
    finally:
        out["teardown"] = st.remove_relay_stack()
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n"); print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out.get("ok") else 1

if __name__ == "__main__":
    sys.exit(main())
