#!/usr/bin/env python3
"""worktime-filing skill 登录脚本。

启本地随机端口等待 OAuth 回调，拿到 token 写入 ~/.claude/skills/worktime-filing/.env。
用法：
    python3 ~/.claude/skills/worktime-filing/login.py
"""
import http.server
import os
import secrets
import socketserver
import sys
import urllib.parse
import webbrowser
from pathlib import Path

BASE = os.environ.get('WORKTIME_BASE', 'https://emp.addx.live/worktime')
SKILL_DIR = Path(__file__).resolve().parent
ENV_FILE = SKILL_DIR / '.env'
TIMEOUT_SECONDS = 180

_state = secrets.token_urlsafe(16)
_result = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _write_html(self, code: int, body: str):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != '/callback':
            self._write_html(404, '<h1>404</h1>')
            return
        query = urllib.parse.parse_qs(parsed.query)
        got_state = (query.get('state') or [''])[0]
        got_token = (query.get('token') or [''])[0]
        got_name = (query.get('name') or [''])[0]

        if got_state != _state:
            self._write_html(400, '<h1>state 不匹配，拒绝回调</h1>')
            _result['error'] = 'state mismatch'
            return
        if not got_token.startswith('wktok_'):
            self._write_html(400, '<h1>token 格式非法</h1>')
            _result['error'] = 'invalid token format'
            return

        _result['token'] = got_token
        _result['name'] = got_name
        self._write_html(200, f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>登录成功</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;
             text-align:center;padding:60px 20px;background:#f5f5f5;">
  <div style="max-width:480px;margin:0 auto;background:#fff;padding:40px;
              border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
    <div style="font-size:48px;color:#52c41a;">✓</div>
    <h1 style="margin:16px 0 8px;font-size:22px;">登录成功</h1>
    <p style="color:#666;">欢迎，{urllib.parse.unquote(got_name) or '用户'}。</p>
    <p style="color:#999;font-size:13px;margin-top:20px;">
      Token 已写入 <code>{ENV_FILE}</code><br>可关闭此窗口。
    </p>
  </div>
</body></html>""")


def main():
    with socketserver.TCPServer(('127.0.0.1', 0), CallbackHandler) as srv:
        port = srv.server_address[1]
        auth_url = (
            f'{BASE}/auth/cli-redirect'
            f'?local_port={port}'
            f'&state={urllib.parse.quote(_state, safe="")}'
        )
        print(f'🚀 本地回调监听：http://127.0.0.1:{port}/callback')
        print(f'🔗 打开浏览器授权：{auth_url}')
        try:
            webbrowser.open(auth_url)
        except Exception:
            print('(未能自动打开浏览器，请手动复制上方 URL)')
        print(f'⏳ 等待授权（最长 {TIMEOUT_SECONDS} 秒）...')
        srv.timeout = TIMEOUT_SECONDS
        srv.handle_request()

    token = _result.get('token')
    if not token:
        err = _result.get('error', '未收到回调（超时）')
        print(f'❌ 登录失败：{err}')
        sys.exit(1)

    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(f'WORKTIME_TOKEN={token}\n', encoding='utf-8')
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
    name = urllib.parse.unquote(_result.get('name', ''))
    print(f'✅ 登录成功，token 已写入 {ENV_FILE}')
    if name:
        print(f'   用户：{name}')


if __name__ == '__main__':
    main()
