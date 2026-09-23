#!/usr/bin/env python3
"""Unified auth challenge: trigger → poll → return.

Zero-dependency (stdlib only). Used inside Sandbox for all auth flows:
- handle_407(body)           — API 407 proxy auth
- challenge_qr(domain, img)  — QR code login (screenshot → forward to user)
- challenge_otp(domain)      — Request OTP/MFA code from user
- challenge_vnc(domain, url) — Notify user to connect VNC for manual auth

All follow: POST /auth/challenge → poll /auth/status → return result.
"""
import json, os, time, urllib.request, urllib.error, uuid

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8000")


def _post(url: str, data: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _poll(auth_id: str, timeout: int = 300) -> dict:
    """Poll /auth/status/{auth_id} until completed/timeout/denied."""
    elapsed = 0
    while elapsed < timeout:
        time.sleep(5)
        elapsed += 5
        try:
            with urllib.request.urlopen(
                f"{GATEWAY_URL}/auth/status/{auth_id}", timeout=10
            ) as resp:
                status = json.loads(resp.read())
            s = status.get("status", "pending")
            if s in ("completed", "timeout", "denied"):
                return status
        except urllib.error.URLError:
            continue
    return {"status": "timeout", "message": f"等待超时（{timeout}秒）"}


def _challenge(data: dict, timeout: int = 300) -> dict:
    """POST /auth/challenge then poll."""
    auth_id = data.get("auth_id") or f"auth_{uuid.uuid4().hex[:8]}"
    data["auth_id"] = auth_id
    try:
        _post(f"{GATEWAY_URL}/auth/challenge", data)
    except urllib.error.URLError as e:
        return {"status": "error", "message": f"Gateway unreachable: {e}"}
    return _poll(auth_id, timeout)


# ── API 407 ──────────────────────────────────────────────────────────

def handle_407(response_body: str | dict) -> dict:
    """Parse 407 body → challenge → poll → return."""
    body = json.loads(response_body) if isinstance(response_body, str) else response_body
    if not body.get("auth_id"):
        return {"status": "error", "message": "407 missing auth_id"}
    return _challenge({
        "auth_id": body["auth_id"],
        "domain": body.get("domain", "unknown"),
        "type": body.get("type", "token"),
    }, timeout=min(body.get("timeout_seconds", 300), 300))


# ── QR Code ──────────────────────────────────────────────────────────

def challenge_qr(domain: str, screenshot_path: str) -> dict:
    """Screenshot QR code → base64 → send to user → poll for scan."""
    import base64
    with open(screenshot_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    return _challenge({
        "domain": domain,
        "type": "qr_code",
        "image_base64": img_b64,
    }, timeout=300)


# ── OTP / MFA ────────────────────────────────────────────────────────

def challenge_otp(domain: str) -> dict:
    """Ask user for OTP code → poll → return (code in result)."""
    return _challenge({
        "domain": domain,
        "type": "otp",
    }, timeout=120)


# ── VNC Fallback ─────────────────────────────────────────────────────

def challenge_vnc(domain: str, vnc_url: str) -> dict:
    """Notify user to connect VNC for manual auth → poll."""
    return _challenge({
        "domain": domain,
        "type": "vnc",
        "vnc_url": vnc_url,
    }, timeout=900)
