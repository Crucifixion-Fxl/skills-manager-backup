---
name: clawplex-remote-auth
description: Handle all authentication in ClawPlex Sandbox — API 407 proxy auth challenges AND browser-interactive login flows (password forms, OTP/MFA, QR code scanning, VNC CAPTCHA fallback, CLI OAuth). Uses vercel/agent-browser for browser automation and Gateway /auth/challenge API for user credential relay. Use this skill whenever you encounter a 407 or 401 response, see "proxy_auth_required", need to log into any website, detect a login page, password field, MFA prompt, QR code, CAPTCHA, or Cloudflare challenge. Also use when curl/fetch/requests gets blocked by authentication, or when a CLI tool needs OAuth login.
---

# ClawPlex Remote Auth

## Description

Handle **all authentication** in Sandbox — both API token challenges and browser login flows. These are complementary: browser login often produces cookies/tokens that enable subsequent API calls.

The Sandbox runs in a Zero Trust environment. You **never** see real credentials — they live in Gateway + Vault. This protects users because even if Sandbox code is compromised, credentials can't leak. You work with **placeholders** that Gateway swaps at the network layer.

### Session Isolation

Sandbox is per-Workspace — multiple conversations share one Pod. Extract `conversation_id` from your prompt context and use it as the browser session name. This keeps parallel conversations isolated while preserving login state across messages in the same conversation.

```bash
# Your prompt contains <conversation_id>conv-abc123</conversation_id>
agent-browser --session "conv-abc123" open https://example.com
```

### Credential Placeholders

| Placeholder | Purpose | Example |
|-------------|---------|---------|
| `vault:{domain}` | Password | `vault:amazon.com` |
| `vault:otp:{domain}` | OTP/MFA code | `vault:otp:amazon.com` |

---

## Rules

1. **Never use `agent-browser auth save/login`** — the built-in Auth Vault is disabled because it would store credentials inside the Sandbox, violating Zero Trust.
2. **Never ask users for passwords** — use `vault:{domain}` or `handle_407()`. Users submit credentials via Feishu cards, never through chat.
3. **Use `--headed` for visual flows** (QR, OAuth, CAPTCHA) — this enables the VNC session that users connect to for manual intervention.
4. **Every challenge has a hard TTL** — Sandbox must never block indefinitely. If the user doesn't respond, inform them and move on.
5. **Retry original request unchanged after 407** — Gateway injects the credential automatically.
6. **Login method priority** (when multiple options on page): Feishu SSO > Username+Password > Other enterprise SSO > Social Login (VNC fallback).
7. **Cookies persist** — Cookie Proxy stores session cookies in Vault. After first login, subsequent visits to the same domain auto-authenticate.

### Challenge TTLs

| Challenge | TTL | Rationale |
|-----------|-----|-----------|
| API token (407) | 5 min | API workflow should not stall long |
| QR scan | 5 min | QR codes expire quickly |
| OTP/MFA code | 2 min | OTP codes are time-sensitive |
| VNC manual auth | 15 min | Complex manual flows need more time |

### Detecting Auth Needs

Don't wait for a 407. Proactively detect authentication requirements:

- **HTTP signals:** `WWW-Authenticate: Bearer` -> API token. `401`/`403` with "unauthorized" -> credential needed. `Location` header to OAuth URL -> OAuth flow.
- **Page signals:** `type="password"` input -> fill `vault:{domain}`. "verification code" placeholder -> fill `vault:otp:{domain}`. Large QR canvas -> screenshot and forward. "I'm not a robot" -> VNC fallback.

---

## API 407 Challenges

When an HTTP request lacks credentials, Gateway returns 407 with an `auth_id`. Use the Gateway `/auth/challenge` API to trigger a Feishu card to the user, poll until they submit credentials, then retry.

```python
import json, os, time, urllib.request, uuid

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8000")

def handle_407(response_body):
    """Parse 407 body -> challenge -> poll -> return."""
    body = json.loads(response_body) if isinstance(response_body, str) else response_body
    auth_id = body.get("auth_id")
    if not auth_id:
        return {"status": "error", "message": "407 missing auth_id"}
    # POST /auth/challenge
    data = json.dumps({"auth_id": auth_id, "domain": body.get("domain", "unknown"), "type": body.get("type", "token")}).encode()
    req = urllib.request.Request(f"{GATEWAY_URL}/auth/challenge", data=data, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10)
    # Poll /auth/status/{auth_id}
    for _ in range(60):
        time.sleep(5)
        with urllib.request.urlopen(f"{GATEWAY_URL}/auth/status/{auth_id}", timeout=10) as resp:
            status = json.loads(resp.read())
        if status.get("status") in ("completed", "timeout", "denied"):
            return status
    return {"status": "timeout"}
```

After completion, **retry the original request unchanged** — Gateway injects the credential automatically.

## Browser Authentication

Use [vercel/agent-browser](https://github.com/vercel/agent-browser) CLI for browser login flows.

### Password Login

```bash
agent-browser open https://sellercentral.amazon.com/login
agent-browser snapshot -i
agent-browser fill @e1 "seller@company.com"
agent-browser fill @e2 "vault:amazon.com"       # Gateway replaces with real password
agent-browser click @e3
agent-browser wait --load networkidle
agent-browser snapshot -i                        # verify success
```

### OTP / MFA

```python
# POST /auth/challenge with type=otp, then poll
def challenge_otp(domain):
    auth_id = f"auth_{uuid.uuid4().hex[:8]}"
    data = json.dumps({"auth_id": auth_id, "domain": domain, "type": "otp"}).encode()
    req = urllib.request.Request(f"{GATEWAY_URL}/auth/challenge", data=data, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10)
    # poll same as handle_407...
```

```bash
agent-browser fill @e1 "vault:otp:amazon.com"    # Gateway replaces with submitted code
agent-browser click @e2
```

### QR Code Login

```bash
agent-browser --headed open https://app.corp.com/login
agent-browser screenshot /tmp/qr.png
# POST /auth/challenge with type=qr_code and image_base64, then poll
```

### VNC Fallback (CAPTCHA / Complex Auth)

When browser automation can't handle it (Cloudflare, reCAPTCHA), hand off to the user via VNC:

```python
# POST /auth/challenge with type=vnc and vnc_url
def challenge_vnc(domain, vnc_url):
    auth_id = f"auth_{uuid.uuid4().hex[:8]}"
    data = json.dumps({"auth_id": auth_id, "domain": domain, "type": "vnc", "vnc_url": vnc_url}).encode()
    req = urllib.request.Request(f"{GATEWAY_URL}/auth/challenge", data=data, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10)
    # poll with 900s timeout for VNC
```

### CLI OAuth

When a CLI tool prints an OAuth URL, open it in agent-browser. The CLI's localhost callback and agent-browser share the same container, so the redirect completes naturally.

```bash
agent-browser --headed open "https://github.com/login/oauth/authorize?..."
agent-browser fill @e1 "user@company.com"
agent-browser fill @e2 "vault:github.com"
agent-browser click @e3
agent-browser wait --load networkidle
# CLI receives localhost callback -> auth complete
```

---

## Examples

### Bad Example

```python
# BAD: Asking user for password directly
password = input("Please enter your Sentry password: ")
requests.post(url, auth=("user", password))
```

```python
# BAD: Hardcoding credentials
headers = {"Authorization": "Bearer sk-live-abc123xyz"}
```

```bash
# BAD: Using agent-browser auth save (stores creds in Sandbox)
agent-browser auth save --domain sentry.io --token "real-token-here"
```

### Good Example

```python
# GOOD: Let Gateway handle 407 auth challenge
response = requests.get("https://sentry.io/api/0/projects/")
if response.status_code == 407:
    result = handle_407(response.text)
    if result["status"] == "completed":
        response = requests.get("https://sentry.io/api/0/projects/")  # retry unchanged
```

```bash
# GOOD: Using vault placeholder for browser login
agent-browser fill @password "vault:sentry.io"   # Gateway swaps at network layer
```

```python
# GOOD: VNC fallback for CAPTCHA
if "captcha" in page_content.lower():
    result = challenge_vnc("accounts.google.com", os.environ.get("SANDBOX_VNC_URL", ""))
```

## API Reference

For full `/auth/challenge` and `/auth/status` endpoint schemas, read `references/auth_challenge_api.md`.
