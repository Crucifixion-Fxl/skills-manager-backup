#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Paprika (paprika.art) helper CLI. Python stdlib only; results are JSON on stdout, progress on stderr.

  paprika.py status                              balance, project quota, API-key check
  paprika.py quota --set 60.00                   set the project's total quota (CNY)
  paprika.py upload ROLE=path [ROLE=path ...]    upload reference media, print assetIds
  paprika.py gen spec.json out.mp4 [--dry-run]   reference-to-video via /v1/open/generations
  paprika.py resume TASK_ID out.mp4              keep polling a submitted task, then download

ROLE is REFERENCE_IMAGE | REFERENCE_VIDEO | REFERENCE_AUDIO. Upload files must be regular files (no
symlink/FIFO/device) within the API size limits; bytes go directly to storage only over https to an
allowed host with redirects refused, otherwise through the API's server-side upload.

Credentials come from environment variables only (never hard-code them):
  PAPRIKA_API_KEY, PAPRIKA_PROJECT_ID   open API (generation)
  PAPRIKA_USER, PAPRIKA_PASSWD          console API (upload, status, quota)
The console session token is cached (mode 600) under ${XDG_CACHE_HOME:-~/.cache}/paprika/.
"""
import argparse
import json
import os
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "https://api.paprika.art"
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "paprika")
TOKEN_FILE = os.path.join(CACHE_DIR, "token")
MODEL_ID = "model_minimax_h3_fl2va_prod"  # MiniMax-H3, code "minimax-h3"
MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime"}
KIND = {"audio": "AUDIO", "image": "IMAGE", "video": "VIDEO"}
ROLES = {"REFERENCE_IMAGE": "IMAGE", "REFERENCE_VIDEO": "VIDEO", "REFERENCE_AUDIO": "AUDIO"}
MAX_BYTES = {"IMAGE": 30 << 20, "VIDEO": 50 << 20, "AUDIO": 15 << 20}  # API input limits
UPLOAD_HOST_SUFFIXES = (".aliyuncs.com", ".paprika.art")  # presigned direct-upload targets we send bytes to
DONE = ("SUCCEEDED", "FAILED", "CANCELED", "CANCELLED")


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg):
    log("error: " + msg)
    sys.exit(1)


def env():
    keys = ("PAPRIKA_API_KEY", "PAPRIKA_PROJECT_ID", "PAPRIKA_USER", "PAPRIKA_PASSWD")
    return {k: os.environ[k] for k in keys if os.environ.get(k)}


def need(*keys):
    e = env()
    miss = [k for k in keys if not e.get(k)]
    if miss:
        die("missing environment variable(s): " + ", ".join(miss))
    return [e[k] for k in keys]


def http(method, path, body=None, auth=None, headers=None, raw=None, timeout=60):
    h = {"User-Agent": "paprika-cli/1.0"}
    if raw is None:
        h["Content-Type"] = "application/json"
    if auth:
        h["Authorization"] = auth
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, method=method, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as ex:
        txt = ex.read()
        try:
            return ex.code, json.loads(txt)
        except ValueError:
            return ex.code, txt.decode("utf-8", "replace")[:300]
    except urllib.error.URLError as ex:
        die(f"network error: {ex.reason}")


def cache_dir_ok():
    """The cache dir must be a real directory owned by us (never a symlink); its mode is forced to 0700."""
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    st = os.lstat(CACHE_DIR)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        die(f"refusing to use cache dir (symlink or not owned by you): {CACHE_DIR}")
    os.chmod(CACHE_DIR, 0o700)


def save_token(tok):
    """Atomic private write: a new 0600 temp file (O_EXCL) is renamed over TOKEN_FILE, so a symlink or a
    loose pre-existing file there is replaced, never followed, truncated or reused."""
    cache_dir_ok()
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=".token.")
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    os.replace(tmp, TOKEN_FILE)


def read_token():
    """Cached token, or None when absent, a symlink, not ours or readable by others (then we log in again)."""
    try:
        fd = os.open(TOKEN_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd) as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            return None
        return f.read().strip() or None


def login():
    user, pw = need("PAPRIKA_USER", "PAPRIKA_PASSWD")
    st, r = http("POST", "/v1/auth/login", {"email": user, "password": pw})
    if st != 200:
        die(f"login failed: http {st}")
    tok = r["data"]["accessToken"]
    save_token(tok)
    return tok


def console(method, path, body=None):
    """Console API (Bearer session token). Re-logs in once on 401."""
    st, r = 401, None
    for attempt in (0, 1):
        tok = (read_token() if attempt == 0 else None) or login()
        st, r = http(method, path, body, auth="Bearer " + tok)
        if st != 401:
            break
    return st, r


def key_auth():
    return "Key " + need("PAPRIKA_API_KEY")[0]


# ---------------------------------------------------------------- commands
def cmd_status(_):
    pid = env().get("PAPRIKA_PROJECT_ID")
    out = {}
    st, r = console("GET", "/v1/console/billing/account")
    if st == 200:
        d = r["data"]
        out["balance"] = {"available": d["availableBalance"], "frozen": d["frozenBalance"],
                          "currency": d["currency"]}
    else:
        out["balance"] = {"error": f"http {st}"}
    st, r = console("GET", "/v1/console/projects")
    out["projects"] = [{"projectId": p["projectId"], "name": p["name"], "status": p["status"],
                        "quota": p["quotaAmount"], "used": p["usedAmount"], "reserved": p["reservedAmount"],
                        "available": p["availableAmount"], "configured": p["projectId"] == pid}
                       for p in (r["data"]["items"] if st == 200 else [])]
    # Key probe on a non-existent task: 401/403 => bad key, anything else => key accepted.
    st, _ = http("GET", "/v1/open/generations/task_probe_does_not_exist", auth=key_auth())
    out["apiKey"] = "rejected" if st in (401, 403) else "accepted"
    emit(out)


def cmd_quota(a):
    pid = need("PAPRIKA_PROJECT_ID")[0]
    st, r = console("PATCH", f"/v1/console/projects/{pid}/quota", {"quotaAmount": a.set})
    if st != 200:
        die(f"quota update failed: http {st}")
    d = r["data"]
    emit({k: d.get(k) for k in ("quotaAmount", "usedAmount", "availableAmount")})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect would let a storage host bounce our bytes elsewhere


def upload_target(d):
    """Return (url, method) if the server-provided presigned target is safe to send bytes to, else None."""
    url, method = d.get("uploadUrl") or "", d.get("uploadMethod") or "PUT"
    u = urllib.parse.urlparse(url)
    if u.scheme == "https" and (u.hostname or "").endswith(UPLOAD_HOST_SUFFIXES) and method in ("PUT", "POST"):
        return url, method
    return None


def put_file(d, data, mime, pid):
    target = upload_target(d)
    if target:
        try:  # direct-to-storage presigned URL; redirects refused
            req = urllib.request.Request(target[0], data=data, method=target[1], headers=d.get("uploadHeaders") or {})
            urllib.request.build_opener(_NoRedirect).open(req, timeout=180).read()
            return
        except (urllib.error.URLError, OSError):
            log("direct upload failed; using server-side upload")
    else:
        log("upload URL rejected (need https on an allowed host); using server-side upload")
    tok = read_token() or login()
    st, _ = http("PUT", f"/v1/console/uploads/{d['assetId']}/content?projectId={pid}", raw=data,
                 auth="Bearer " + tok, headers={"Content-Type": mime}, timeout=180)
    if st not in (200, 201):
        die(f"upload failed: http {st}")


def check_file(path, kind):
    """Only regular files within the API size limit: rejects symlinks, FIFOs, devices, empty/oversized files."""
    try:
        st = os.lstat(path)
    except OSError as ex:
        die(f"cannot stat {path}: {ex}")
    if not stat.S_ISREG(st.st_mode):
        die(f"not a regular file (symlink/FIFO/device are refused): {path}")
    if st.st_size == 0 or st.st_size > MAX_BYTES[kind]:
        die(f"{path}: size {st.st_size} bytes outside 1..{MAX_BYTES[kind]} allowed for {kind}")
    return st.st_size


def cmd_upload(a):
    pid = need("PAPRIKA_PROJECT_ID")[0]
    for spec in a.items:
        role, sep, path = spec.partition("=")
        if not sep or role not in ROLES:
            die(f"expected ROLE=path (ROLE one of {', '.join(ROLES)}), got: {spec}")
        mime = MIME.get(os.path.splitext(path)[1].lower())
        kind = KIND[mime.split("/")[0]] if mime else None
        if kind != ROLES[role]:
            die(f"{path}: file type does not match role {role}")
        size = check_file(path, kind)
        st, r = console("POST", "/v1/console/uploads", {
            "projectId": pid, "fileName": os.path.basename(path), "mimeType": mime,
            "sizeBytes": size, "kind": kind, "role": role})
        if st not in (200, 201):
            die(f"upload init failed: http {st} {r}")
        d = r["data"]
        with open(path, "rb") as f:
            put_file(d, f.read(), mime, pid)
        st, r = console("POST", f"/v1/console/uploads/{d['assetId']}/complete", {"projectId": pid})
        if st not in (200, 201):
            die(f"upload complete failed: http {st} {r}")
        m = r["data"]
        emit({"file": os.path.basename(path), "assetId": m["assetId"], "role": role,
              "status": m.get("status"), "durationMs": m.get("durationMs"),
              "width": m.get("width"), "height": m.get("height")})


def build_body(spec, pid):
    return {"projectId": pid, "modelId": MODEL_ID, "capabilityType": "REFERENCE_TO_VIDEO",
            "taskMode": "OMNI_REFERENCE", "prompt": spec["prompt"],
            "duration": spec.get("duration", 5), "resolution": spec.get("resolution", "768P"),
            "aspectRatio": spec.get("aspect", "16:9"), "hasAudio": True, "quantity": 1, "storeIo": True,
            "inputAssets": [{"assetId": x["assetId"], "role": x["role"]} for x in spec["assets"]]}


def cmd_gen(a):
    try:
        spec = json.load(open(a.spec, encoding="utf-8"))
    except (OSError, ValueError) as ex:
        die(f"cannot read spec: {ex}")
    body = build_body(spec, need("PAPRIKA_PROJECT_ID")[0])
    if a.dry_run:
        emit(body)
        return
    idem = a.idempotency_key or "pk-" + uuid.uuid4().hex[:12]
    log(f"idempotency-key {idem} (if the submit result is unknown, retry with --idempotency-key {idem})")
    st, r = http("POST", "/v1/open/generations", body, auth=key_auth(), headers={"Idempotency-Key": idem})
    if st not in (200, 201, 202):
        die(f"submit failed: http {st} {json.dumps(r, ensure_ascii=False)[:400]}")
    d = r["data"]
    log(f"task {d['taskId']} price {d['priceQuote']['totalAmount']} {d['priceQuote']['currency']}")
    log(f"if interrupted DO NOT resubmit (double charge): paprika.py resume {d['taskId']} {a.out}")
    finish(d["taskId"], a.out, a.timeout, price=d["priceQuote"]["totalAmount"])


def finish(task_id, out, timeout=3600, price=None):
    t0, last, d = time.time(), None, None
    while time.time() - t0 < timeout:
        st, r = http("GET", f"/v1/open/generations/{task_id}", auth=key_auth())
        if st in (401, 403, 404):
            die(f"task lookup failed: http {st}")
        d = r.get("data") if isinstance(r, dict) else None
        cur = ((d or {}).get("status"), (d or {}).get("progress"))
        if cur != last:
            log(f"[{int(time.time() - t0):5d}s] {cur[0]} {cur[1]}%")
            last = cur
        if cur[0] in DONE:
            break
        time.sleep(10)
    else:
        die(f"still not done after {timeout}s: paprika.py resume {task_id} {out}")
    outputs = (d.get("result") or {}).get("outputs") or []
    vid = next((o for o in outputs if o.get("kind") == "VIDEO"), None)
    if not vid:
        die("no video: " + json.dumps(d.get("error") or d.get("result"), ensure_ascii=False)[:400])
    if not str(vid.get("url", "")).startswith("https://"):
        die("refusing to download: result URL is not https")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    urllib.request.urlretrieve(vid["url"], out)  # signed URL expires in ~10 min: download immediately
    emit({"taskId": task_id, "file": out, "price": price, "width": vid.get("width"),
          "height": vid.get("height"), "durationMs": vid.get("durationMs")})


def cmd_resume(a):
    finish(a.task_id, a.out, a.timeout)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="balance, project quota, API-key check").set_defaults(fn=cmd_status)
    q = sub.add_parser("quota", help="set the project's total quota")
    q.add_argument("--set", required=True, help='e.g. "60.00"')
    q.set_defaults(fn=cmd_quota)
    u = sub.add_parser("upload", help="upload reference media")
    u.add_argument("items", nargs="+", metavar="ROLE=path")
    u.set_defaults(fn=cmd_upload)
    g = sub.add_parser("gen", help="submit a reference-to-video job and download the result")
    g.add_argument("spec")
    g.add_argument("out")
    g.add_argument("--dry-run", action="store_true", help="print the request body, do not submit")
    g.add_argument("--timeout", type=int, default=3600)
    g.add_argument("--idempotency-key", help="reuse the key printed by an earlier attempt to avoid a double charge")
    g.set_defaults(fn=cmd_gen)
    r = sub.add_parser("resume", help="keep polling a submitted task and download it")
    r.add_argument("task_id")
    r.add_argument("out")
    r.add_argument("--timeout", type=int, default=3600)
    r.set_defaults(fn=cmd_resume)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
