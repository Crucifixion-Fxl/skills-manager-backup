#!/usr/bin/env python3
"""P0-11: does a mirror-signed kind 9 with the agent's p tag wake a real buzz-acp 0.5.23,
and are the agent's own events dropped (ignore_self)? engineering/skills#186."""
from __future__ import annotations
import asyncio, hashlib, json, os, secrets, shutil, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "localstack"))
import stack as st  # noqa: E402
from relay_probe import Conn, sign  # noqa: E402
OUT = HERE.parent / "fixtures" / "hostd" / "probes" / "wake.json"

def prompts(d: Path) -> list:
    p = d / "prompts.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

async def say(url, ident, ch, text, tags=()):
    async with Conn(url, ident) as c:
        ev = sign(ident, 9, [["h", ch], *tags], text); ok = await c.publish(ev); assert ok[0], ok; return ev["id"]

def wait_prompts(d, n, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if len(prompts(d)) >= n: return True
        time.sleep(0.5)
    return False

def main():
    st.private_dir(st.STATE_DIR)
    work = st.STATE_DIR / "wake-probe"
    shutil.rmtree(work, ignore_errors=True)  # never read prompts.jsonl left by an earlier run
    work = st.private_dir(work)
    ids = {n: st.new_identity(n) for n in ("owner", "relay", "mirror", "agent", "human")}
    for n in ("mirror", "agent"): ids[n]["auth_tag"] = st.attest(ids["owner"], ids[n])
    out = {"probe": "hostd-wake", "acp": "buzz-acp 0.5.23", "relay_image": st.DEFAULT_RELAY_IMAGE}
    proc = None
    try:
        relay = st.start_relay_stack(st.DEFAULT_RELAY_IMAGE, ids["owner"], ids["relay"])
        for n in ("mirror", "agent", "human"): st.docker("exec", st.NAMES["relay"], "buzz-admin", "add-member", "--pubkey", ids[n]["pubkey"], timeout=60)
        http, url = relay["http_url"], relay["ws_url"]
        ch = st.parse_json(st.buzz(ids["owner"], "channels", "create", "--name", f"hostd-wake-{secrets.token_hex(2)}", "--type", "stream", "--visibility", "private", relay_http=http).stdout, "c")["channel_id"]
        for n, role in (("mirror", "bot"), ("agent", "bot"), ("human", "member")):
            st.buzz(ids["owner"], "channels", "add-member", "--channel", ch, "--pubkey", ids[n]["pubkey"], "--role", role, relay_http=http)
        rec = work / "acp"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "BUZZ_RELAY_URL": url, "BUZZ_PRIVATE_KEY": ids["agent"]["secret"],
               "BUZZ_AUTH_TAG": ids["agent"]["auth_tag"], "BUZZ_ACP_AGENT_OWNER": ids["owner"]["pubkey"],
               "BUZZ_ACP_AGENT_COMMAND": "/usr/bin/python3", "BUZZ_ACP_AGENT_ARGS": f"{HERE / 'acp_record_double.py'},{rec}",
               "BUZZ_ACP_CHANNELS": ch, "BUZZ_ACP_RESPOND_TO": "anyone", "BUZZ_ACP_SESSION_POLICY": "thread", "BUZZ_ACP_AGENTS": "1"}
        log = (work / "acp.log").open("w")
        proc = subprocess.Popen([str(st.BUZZ_ACP)], env=env, stdout=log, stderr=subprocess.STDOUT)
        time.sleep(6)
        out["acp_running"] = proc.poll() is None
        p_agent = ["p", ids["agent"]["pubkey"]]
        # 1: mirror says "[飞书] name: @agent ..." with the agent's p tag
        e1 = asyncio.run(say(url, ids["mirror"], ch, "[飞书] 林可：@agent 看一下这个", [p_agent]))
        out["mirror_p_tag_wakes_agent"] = wait_prompts(rec, 1, 30)
        if out["mirror_p_tag_wakes_agent"]:
            txt = json.dumps(prompts(rec)[0]["prompt"], ensure_ascii=False)
            out["prompt_contains_mirror_text"] = "看一下这个" in txt
        # 2: mirror message without p tag must not wake
        n0 = len(prompts(rec)); asyncio.run(say(url, ids["mirror"], ch, "[飞书] 林可：普通发言"))
        out["mirror_without_p_tag_wakes"] = wait_prompts(rec, n0 + 1, 10)
        # 3: the agent's own event with its own p tag must be dropped (ignore_self)
        n0 = len(prompts(rec)); asyncio.run(say(url, ids["agent"], ch, "self mention", [p_agent]))
        out["self_authored_wakes"] = wait_prompts(rec, n0 + 1, 10)
        # 4: control: a human member mention wakes it
        n0 = len(prompts(rec)); asyncio.run(say(url, ids["human"], ch, "human @agent", [p_agent]))
        out["human_p_tag_wakes_agent_control"] = wait_prompts(rec, n0 + 1, 30)
        # every positive AND negative expectation must hold, otherwise the receipt is a failure
        checks = {"acp_running": out["acp_running"], "mirror_p_tag_wakes_agent": out["mirror_p_tag_wakes_agent"],
                  "prompt_contains_mirror_text": out.get("prompt_contains_mirror_text", False),
                  "mirror_without_p_tag_does_not_wake": not out["mirror_without_p_tag_wakes"],
                  "self_authored_does_not_wake": not out["self_authored_wakes"],
                  "human_p_tag_wakes_agent_control": out["human_p_tag_wakes_agent_control"]}
        out["failed_checks"] = sorted(k for k, v in checks.items() if not v)
        out["ok"] = not out["failed_checks"]
    except Exception as e:
        out["ok"] = False; out["error"] = st.redact(f"{type(e).__name__}: {e}")[:500]
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(10)
            except subprocess.TimeoutExpired: proc.kill()
        out["teardown"] = st.remove_relay_stack()
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n"); print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if out.get("ok") else 1

if __name__ == "__main__":
    sys.exit(main())
