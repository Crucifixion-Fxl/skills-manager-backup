#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Operator-owned GET-only RP proxy. Its credential file must be outside the agent namespace."""
import argparse
import datetime as dt
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from collect_failures import CollectionError, NoRedirect, redact

UPSTREAM = "https://reportportal.builder.addx.live"
PROJECT = "builder_prod_cn"
MAX_BYTES = 20 * 1024 * 1024


def allowed_path(target):
    url = urllib.parse.urlsplit(target)
    if url.scheme or url.netloc or url.fragment or "%" in url.path:
        return False
    route = re.fullmatch(r"/api/v1/" + PROJECT + r"/(launch|item|log)(?:/([0-9]+))?", url.path)
    binary = re.fullmatch(r"/api/v1/data/" + PROJECT + r"/[A-Za-z0-9_.-]{1,256}", url.path)
    if binary:
        return not url.query and url.path.rsplit("/", 1)[1] not in {".", ".."}
    if not route:
        return False
    params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
    permitted = {
        "launch": {"filter.eq.uuid"},
        "item": {"filter.eq.launchId", "filter.eq.uuid"},
        "log": {"filter.eq.item", "filter.ex.binaryContent", "filter.gte.logTime", "filter.lte.logTime"},
    }[route[1]] | {"page.page", "page.size"}
    if route[2] and params:
        return False
    if any(k not in permitted or len(v) != 1 for k, v in params.items()):
        return False
    for key, values in params.items():
        value = values[0]
        if key == "filter.eq.uuid":
            if not re.fullmatch(r"[a-fA-F0-9-]{36}", value):
                return False
        elif key == "filter.ex.binaryContent":
            if value != "true":
                return False
        elif key.endswith("logTime"):
            try:
                if not dt.datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo:
                    return False
            except ValueError:
                return False
        elif not value.isdigit() or int(value) < 1:
            return False
        elif key in {"page.page", "page.size"} and int(value) > 500:
            return False
    # Collection routes must remain bounded to a launch, item or exact UUID.
    if not route[2] and not (set(params) & (permitted - {"page.page", "page.size"})):
        return False
    return True


def authorized(config, token, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    expiry = dt.datetime.fromisoformat(config["expires_at"].replace("Z", "+00:00"))
    return now < expiry and hmac.compare_digest(token, "Bearer " + config["client_token"])


def handler(config):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No URL, credential or test content in access logs.

        def reject(self, status):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not authorized(config, self.headers.get("Authorization", "")):
                return self.reject(401)
            if not allowed_path(self.path):
                return self.reject(403)
            req = urllib.request.Request(UPSTREAM + self.path,
                headers={"Authorization": "Bearer " + config["rp_token"]})
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
                    mime = response.headers.get("Content-Type", "application/octet-stream")
                    body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    return self.reject(413)
                if "application/json" in mime:
                    body = json.dumps(redact(json.loads(body)), ensure_ascii=False).encode()
            except urllib.error.HTTPError as exc:
                return self.reject(exc.code if exc.code in {400, 401, 403, 404, 429} else 502)
            except (CollectionError, urllib.error.URLError, TimeoutError, ValueError):
                return self.reject(502)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.reject(405)

        do_PUT = do_PATCH = do_DELETE = do_POST
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9374)
    args = parser.parse_args()
    if args.config.is_symlink() or args.config.stat().st_mode & 0o077:
        parser.error("config must be a private non-symlink file outside the agent namespace")
    config = json.loads(args.config.read_text())
    if not authorized(config, "Bearer " + config["client_token"]):
        parser.error("proxy authorization expired")
    ThreadingHTTPServer(("127.0.0.1", args.port), handler(config)).serve_forever()


if __name__ == "__main__":
    main()
