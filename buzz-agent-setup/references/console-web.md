# Standard browser Console

Open `http://127.0.0.1:18481/` in an ordinary browser. The native login dialog uses
username `owner` and the independent Console password provisioned privately by
its owner AI. No special Chrome, browser profile, CDP client or credential URL is
needed. Basic credentials may remain in the browser cache; this entry does not
claim application logout or session idle expiry.

The Linux gateway binds only `127.0.0.1` (default port 18481; `--port` may select
another fixed port). A trusted SSH tunnel forwards the same Mac loopback port to
that Linux port. Never expose this HTTP listener on a LAN address: Basic is not
encryption; the cross-machine transport is SSH. The tunnel must use strict host
trust, a loopback-only local bind and failure on occupied forwarding ports.

Deployment owner freezes a complete gateway package under
`~/.local/opt/buzz-hostd-console/releases/<SHA>/source`, uses the validated hostd
2e Python environment without modifying it, and creates the independent user
unit `buzz-hostd-console-web.service`. Its working directory is the package's
`skills/agent-harness/buzz-agent-setup/scripts`. Module `hostd.console_web` accepts
`--runtime-dir`, `--password-file` and optional `--port`; no password argv exists.
The existing hostd daemon/Store/worker processes need no restart or migration.

Expected live paths: runtime `/run/user/1009/buzz-hostd`; password
`~/.config/buzz-hostd/console-web/password`. The password's containing directory
must be owned 0700; the password is an owned, single-link, non-symlink 0600 file
with an independently generated 43-character URL-safe random value (32 random
bytes), optionally followed by one newline. It must differ from `console.token`.
Create and transfer it only through the existing trusted channel to private local
storage; never log it, display it in chat or place it in a URL, argument or shell
history. No app/SSH password is reused. Password bytes exist only in the private
file and bounded gateway process memory.

The gateway serves its own immutable `console_ui.html`, including same-origin
browser credentials and the existing hashed CSP. It proxies only the existing
API/SSE routes into the protected Unix Console. Thus a current 2e backend with an
older HTML asset works without rewriting that asset or restarting hostd.

All requests require Basic authentication before any backend connection. Exact
Host/Origin, same-origin fetch metadata, JSON and X-Hostd-Request remain required
for POST; Basic alone is not CSRF protection. Internal Bearer and peer-UID checks
remain intact. Actions retain the original idempotency key and backend principal;
no HTTP POST is automatically retried after an uncertain response.

Credential, runtime, token or socket replacement fails closed. The gateway
rechecks before/after backend connect and checks active connections every half
second; rotation closes SSE and stops the listener. Restart the independent
gateway with the freshly checked private configuration after deliberate rotation.
Browser closure disconnects its SSE; this server never manages browser processes.

Owner handoff is a prompt: prepare the private credential and trusted transparent
SSH tunnel, verify the reviewed package/hash, and return the fixed URL. Read back
page, graph and a real SSE snapshot using normal browser login. Do not send user
command lists or restore the old CDP workflow. Linux automated browser fixtures
are not a substitute for the owner's Mac Safari/Chrome receipt.

## Views and filtering

The default **概览** tab shows one Feishu group ↔ Buzz channel relationship per
row, with names, full IDs and deep links. Agent lists stay collapsed until opened.
The **高级视图** tab shows the graph and supports group and Agent selectors; using
both shows their intersection. Shared host/app nodes do not expand the selection
into unrelated groups. A removed selection remains an empty result until the user
selects again or clears the filters. Switching tabs and receiving SSE snapshots
preserve selections; tabs support arrow keys, Home and End. Filtering is local
presentation and does not grant membership or change operation permissions.
