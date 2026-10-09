# Fixed-revision hostd preparation and rollback

This installer prepares a candidate. It does not deploy, activate, stop, reload,
restart, enable, disable, or overwrite any live unit. `prepared` means packaging
and dependency/import readback passed; L3 migration and L4 acceptance remain separate.
Use the reviewed installer from the same reviewed release revision.

Process-local synchronous round capacity can be configured explicitly on the
`hostd run` invocation; defaults remain4 workers/1 live-reserved slot. See
[round concurrency](hostd-round-concurrency.md) for the8/2 example, bounds and
unchanged API rate limits. Preparing a release does not activate these flags.

## Operator input and review

Root supplies a private 0600 JSON **array of exactly 15 targets**, frozen from
actual configurations and service identities. Each entry has exactly these keys:

```json
{
  "name": "approved-binding-name",
  "channel_id": "actual-channel-uuid",
  "chat_id_hash": "64-lowercase-hex-sha256-of-exact-chat-id",
  "app_id": "cli_actual_sync_application",
  "service": "actual-original-sync.service",
  "timer": "actual-original-sync.timer",
  "legacy_state": "/absolute/private/binding/state.json"
}
```

Names and unit identities must be unique; channel/chat pairs must be unique.
Do not derive the target set from directory counts or stale L4 configurations.
The existing read-only inventory is useful evidence but is not this manifest:
its application field names and unit snapshots differ, and it omits source paths.
No chat identifier or application identifier is guessed. Original service/timer
states and unit-file hashes are freshly read from the user manager.

Choose a full reviewed lowercase 40-character commit SHA. The source is obtained
with `git archive COMMIT skills/agent-harness/buzz-agent-setup/scripts`, including the lockfile
from that commit. Unrelated repository integration symlinks are excluded;
symlinks anywhere in the runtime archive are rejected. Working-tree
edits are excluded. A commit without the runtime or hash lock fails preparation.
The release root must be outside all registered git worktrees and must have no
symlink ancestors. The new release root is private 0700. Existing releases are
never overwritten or removed. Database and status paths must differ and sit
outside the release root. Neither output may pass through a symlink.

```bash
python3 scripts/install_hostd.py --check \
  --repo /absolute/repository --revision FULL_40_CHARACTER_REVIEWED_SHA \
  --inventory /absolute/private/targets.json \
  --release-root /absolute/private/releases \
  --state-db /absolute/private/hostd.sqlite3 \
  --status-file /absolute/private/status.json
```

`--dry-run` is an alias for read-only checking. Review the resulting `digest`,
source archive/hash-lock hashes, desired unit, target identities, and original
manager states. Check creates no files. A busy sync service produces `busy`;
preparation is deferred. It never interrupts an Agent. Agent unit-template
migration (M-5) remains an independent inventory/review operation; this installer
preserves the original sync unit files and prepares only the hostd candidate.

Run the same arguments with `--apply --expect-plan-digest REVIEWED_DIGEST` to
prepare the candidate. All manager states, original files, legacy states, and
source hashes must still match. No CLI package version/hash gate is involved.

## Explicit onboarding startup and bot-reader migration

The default candidate has onboarding **pending** and bot-reader migration
**disabled**. It does not search for an owner configuration, startup JSON,
console/browser directory, or credentials. Existing arguments retain the
previous candidate behavior. A default candidate therefore does not enable
new onboarding cards by itself.

For a reviewed card-onboarding candidate, pass both an explicit protected JSON
path and its reviewed exact lowercase SHA256. To request verified legacy-reader
migration at startup, add the separate explicit switch:

```bash
python3 scripts/install_hostd.py --check \
  --repo /absolute/repository --revision FULL_40_CHARACTER_REVIEWED_SHA \
  --inventory /absolute/private/targets.json \
  --release-root /absolute/private/releases \
  --state-db /absolute/private/hostd.sqlite3 \
  --status-file /absolute/private/status.json \
  --onboarding-config /absolute/private/owner-reviewed-startup.json \
  --onboarding-config-sha256 REVIEWED_64_CHARACTER_SHA256 \
  --migrate-bot-readers
```

Use the same optional arguments for `--apply --expect-plan-digest DIGEST`.
A path without a pin, a pin without a path, a different hash, duplicate JSON
keys, unknown fields, symlinks, hardlinks, or ownership/mode other than the
current owner and exact 0600 fail. Check reads the startup JSON and inspects
referenced path safety; it does not read/decrypt owner keys, contact Feishu or a
relay, install dependencies, or create files. Public check output includes the
pin and candidate path, while omitting the protected source startup path and
all of its field values. The plan digest still covers the source path and pin.

The startup file uses the actual `hostd.onboarding_runtime.RuntimeConfig`
version-1 schema. The keys are exactly the following required fields, with
`trusted_relays` and `remote_link_base` optional:

```json
{
  "version": 1,
  "owner_env_file": "/absolute/private/reviewed-owner.env",
  "relay_url": "wss://owner-approved-relay.example.invalid",
  "relay_pubkey": "64-lowercase-hex-verified-relay-public-key",
  "template_config": "/absolute/private/reviewed-template.json",
  "binding_dir": "/absolute/private/bindings",
  "legacy_join_path": "/absolute/private/reviewed-legacy-joins.json",
  "catalog_path": "/absolute/private/reviewed-agent-catalog.json",
  "trusted_relays": [],
  "remote_link_base": "https://buzz-ui.example.invalid:8443"
}
```

These are placeholders. The relay must satisfy RuntimeConfig's approved-origin
policy, and the relay public key must be independently verified. The file
contains explicit references, not embedded owner keys, tokens, app secrets, or
card tokens. Review the owner's actual authorization, template/catalog scope,
legacy exclusions and dynamic binding destination before authorizing a cut.
`remote_link_base` is an optional HTTPS origin used by remote mapping. It must
match RuntimeConfig's origin policy: at most 2048 characters, a hostname,
optional valid port, no userinfo, path, query, fragment, percent escape,
backslash, or ASCII control/space. Omission and the explicit empty string keep
remote dispatch unconfigured. A nonempty reviewed origin makes normal daemon
startup construct and schedule `RemoteDispatch`. The origin creates no grant or
readiness: each send still requires the existing current signed approval, native
proof, grant, and revision checks. It adds no credentials and proves no live
permissions.

Preparation copies the exact reviewed bytes to the private
`releases/COMMIT/onboarding-config.json` (0600). The copied file is validated
through `RuntimeConfig.load` using the staged and final fixed-release Python
and source; referenced credentials are not loaded by this schema operation.
It keeps the source startup path and digest in the private rollback manifest.
The original file/hash joins the existing pre-prepare and final pre-publication
compare-and-swap checks. A change or disappearance invalidates the candidate;
failed preparation retains its private evidence.

The candidate unit uses the absolute fixed-release interpreter and source,
passes `--onboarding-config` with that release copy, and has an `ExecStartPre`
SHA256 guard using the same release's `hostd.safety.read_owned`. Altered copied
content fails before the main process starts. No protected source paths or
configuration contents are included in the unit. The optional
`--migrate-bot-readers` appears only when explicitly requested; it requests the
runtime's verified union-id/state migration and does not bypass its identity
proofs or clear acknowledged delivery/reaction responsibility.

A `prepared` receipt records `onboarding_status`, `onboarding_sha256` and
`migrate_bot_readers`. Without an explicit file it reports startup configuration
pending, rather than claiming that a startup schema was checked.
Owner authorization review, current-ledger rollback review, L3 cutover and
live/card acceptance remain pending. Review and recheck the startup hash,
referenced owner/template/catalog files and latest rollback responsibility
before any authorized unit publication or start. The installer does not perform
that cut, stop old writers, migrate live state, or open a browser console.

## Own-home admission and original pending records

This section describes own-home admission in a reviewed fixed release.
Candidate checks and focused tests do not prove deployment or native
acceptance.

A reviewed onboarding catalog enables periodic discovery of this machine's
verified own-bot agents. The daemon checks the protected local identity,
legacy-task exclusion and current runtime configuration before selecting an
agent. Each pass handles at most 32 agent public keys. This is a scheduling
budget, not approval to enable 32 agents. Original unresolved records are
examined first, including when an earlier attempt already changed the agent's
channel list. Other independently authorized agents remain separate.

Approved/applied JOIN recovery uses one serial writer, a minimum five-second
interval between retry starts, and a sixty-second cooldown for each request
after its last attempt finishes. Due requests rotate by request ID; failed
requests retain their cooldown. The monotonic timer wakes only approval
recovery, without accelerating periodic issuer/fallback recovery. Requested
cards retain their separate recovery cursor. For fourteen continuously due
requests with negligible I/O, the first pass spans 65 seconds; slow in-flight
I/O still delays the writer, so this is not an end-to-end latency guarantee.
Restart rebuilds the volatile schedule with the same five-second spacing;
durable authorization, original effect intents and UNKNOWN readback guards
remain authoritative. This interval bounds recovery starts, not the number of
API calls inside an attempt. Closing drains the existing attempt and starts no
new recovery work.

A discovered channel or an approved card does not mean the agent is connected
or ready to send. Local admission must verify the approved sources, protected
file changes, original and replacement process identities, and every proposed
channel subscription. Even a completed local admission still needs a fresh
normal sending authorization and actual target readback before delivery.

`RESERVED` and `UNKNOWN` retain the original operation ID and scope. After a
restart, the daemon only reads back that original attempt; it does not repeat
the file changes or restart, choose another invocation, or replace the source
or signature. A successful current observation cannot itself complete a lost
original restart response. Keep the original evidence for review. Do not
delete the pending record or repeatedly invoke restart to obtain a green state.

If an attempt remains pending, inspect the original operation and the current
local identity, approved sources and process through the reviewed readback
procedure. There is no new public retry command implied by this reference.
The human guidance is:

> 本机接入仍待核验。怎么解决：保留原始接入 ID，检查自己的受保护配置、旧任务排除、批准来源和实际进程；未知结果只继续读回，先完成原始结果审查再考虑新的操作。
>
> 复制给 AI：帮我核查 hostd 原始 RESERVED/UNKNOWN 接入、当前来源与进程读回；不要重复重启、修改账本或输出密钥、凭据和提示词正文。

Stopping the daemon refuses new work and joins already dispatched I/O and
cleanup while its database and shared clients remain open. The owning shutdown
then reaps its SDK children and closes its resources. The agent's separate user
unit is not stopped by closing hostd, and uncertainty is not erased. Wait for
the original owned work and locks to finish before closing its dependencies
or restoring an old writer.

Candidate checking and preparation remain packaging operations. Before an
authorized cut, review the owner's actual startup configuration, legacy
exclusions, process quiescence and current rollback responsibility. Preserve
separately reviewed private backups of the exact catalog, legacy configuration
and agent runtime files before allowing admission to change them. The fixed
15-binding state export below does not by itself restore these agent files or
onboarding ownership. Native admission, L3, pilot and L4 acceptance remain
separate gates.

## Explicit Node and CLI paths for media

The native HTTP candidate requires no Node or CLI installation for its converted
bot API requests. Without CLI options, the installer records
`cli_runtime.status=pending` and the prepared receipt says media CLI paths are
pending. It does not discover an installation from inherited `PATH`, `HOME`,
`HOSTD_*` values, or a personal Feishu profile. This candidate does not prove
media upload/download readiness.

To prepare a candidate with media CLI startup configured, add both options to
the same check and apply commands:

```bash
  --node-binary /absolute/reviewed/node \
  --lark-cli-entry /absolute/reviewed/lib/node_modules/@larksuite/cli/scripts/run.js
```

Use the current machine's reviewed absolute Node executable and the actual CLI
entry file. Inspect the current installation directly, including the real path
when a discovery command returns a symlink; the product does not assume a
particular home directory, Node version, npm prefix or CLI package version.
These example paths are placeholders. The installed `@larksuite/cli` is used
without pinned package versions or package/entry hash prerequisites.

The pair is explicit: one option alone, relative paths, symlinks in any path
component, nonregular files, files or ancestors owned by someone other than the
current owner or root, and group/other writable files or ancestors fail closed.
Node must be executable and the CLI entry readable. Check performs only
metadata validation through the shared `RuntimePaths.validate` contract; it
never executes Node or the CLI, reads credentials or contacts Feishu. Runtime
path failures include a fixed human remedy without copying private source
paths or command output into errors.

The reviewed plan records these paths and file identity/permission metadata,
not package versions or content hashes. Prepare compares the same metadata at
its initial and final publication checks. Replacement, modification or
permission changes require a fresh review digest. The fixed archived release
must contain `hostd.cli_runtime`; staged and final release Python import that
actual module and validate the explicit pair without invoking Node. The
candidate has a metadata-only `ExecStartPre` using the same fixed-release
Python and source, plus the exact environment values:

```ini
Environment="HOSTD_NODE_BINARY=/absolute/reviewed/node"
Environment="HOSTD_LARK_CLI_ENTRY=/absolute/reviewed/lib/node_modules/@larksuite/cli/scripts/run.js"
```

Those values keep media subprocess selection independent of a minimal manager
`PATH`. They configure executable selection only: the existing exact bot app,
protected profile/config/data directories and explicit bot identity still
apply. A configured-path receipt verifies offline file safety and import
readback, not actual media behavior, SaaS scopes, onboarding permission or live
service startup. CLI and Node files remain outside the release and may be
updated; the runtime revalidates safety before actual subprocess use. Preparation
never writes those files, installs a CLI, changes profiles or deploys the unit.

## Candidate contents and execution contract

`releases/COMMIT/source` holds archived source, read-only files/directories.
`venv` is an independent Python 3 environment, also frozen after installation.
The system Python remains a prerequisite; this is not a bundled interpreter.
Stdlib `venv --without-pip` works without Ubuntu's unavailable ensurepip package.
The installed absolute `$HOME/.local/bin/uv` installs the exact wheel-only,
SHA256-checked lock from public PyPI with inherited credentials/config removed.
The lock pins lark-oapi 1.7.3, websockets 15.0.1, cryptography 46.0.2 and all
transitive packages. Platform wheel availability and that bootstrap executable
must be verified on each host. No dependency installation changes the old venv.

The installer checks dependencies, reads back every locked package version,
and runs `python -m hostd --help` before and after venv relocation. It records a
source-file hash manifest and a private `receipt.json`. Failed attempts remain
as private `.prepare-*` directories or failed final releases for inspection;
there is no automatic cleanup or silent retry over them.

`candidate.service` is private 0600 and contains an absolute independent Python
path, `python -m hostd run`, exact frozen `--only` names, explicit state/status
paths, `UMask=0077`, and `KillMode=control-group`. It has no application secrets
or credentials. Python bytecode writes are disabled. It is not published into
the live unit directory. The original units, their enabled/active states, and
legacy state files are retained under private `rollback/`. A genuinely absent
legacy ledger (including an absent state directory) is recorded as JSON null in
`legacy_sha256`, rechecked as absent before preparation, and excluded from file
backups. Preparation never creates a source state file or invents an empty
ledger. Presence/absence changes invalidate the reviewed digest. Symlinks, FIFOs,
and unsafe files still fail. An existing database
is captured with SQLite backup, including committed WAL transactions.

Root performs the authorized L3 cutover after review. Verify actual command,
PID/children, loaded fragment, database path, application connections and all
15 binding identities. Packaging, syntax, and unit readback alone do not prove
runtime, Feishu permission, browser, or product acceptance.

## Console handoff after installation

After a successful installation or upgrade, give the owner a Console entry in
the delivery response or an explicitly authorized private message. Installation
alone does not prove browser access. If an authenticated entry has been verified
from the owner's access environment, provide its URL and access requirements.
Otherwise provide this copyable prompt:

> 打开本机 hostd Console。先确认运行版本、私有 runtime 目录和我能看到的桌面 DISPLAY；使用该版本的 `hostd.console_launcher --display <已确认的 DISPLAY>` 在隔离 Chrome 中打开，读回页面和 `/api/graph`。保持启动进程运行，并告诉我在哪个桌面查看。不要输出访问 token，不要把临时 localhost URL 当作远程入口。若从另一台电脑访问，使用已审核的 SSH 转发配置。

From the installed release's Python environment (with its `hostd` module on
`PYTHONPATH`), launch the local Console with:

```bash
python -m hostd.console_launcher --display "$DISPLAY"
```

Use the owner's confirmed desktop, not an arbitrary available virtual display.
The launcher defaults to `/run/user/<uid>/buzz-hostd` and the real Chrome ELF at
`/opt/google/chrome/chrome`; override `--runtime-dir`, `--chrome-binary`, or
`--xauthority` when needed. It validates the executable and derives its current
digest locally, creates a fresh private client/profile, and uses the existing
origin-scoped CDP authentication transport. The JSON receipt reports the
rendered page and graph readback, counts only, and a temporary URL scoped to the
currently owned browser. It does not contain credentials or graph contents.

Keep the launcher running while using the window. Ctrl-C closes its browser and
listener and removes its private client directory. `--verify-only` performs the
same read-only checks and then closes everything; its URL is historical evidence,
not an entry the owner can open afterward. An isolated virtual-display check
proves browser rendering on that host, not access from the owner's desktop.
Remote access still uses `hostd.console_access --ssh-plan <reviewed-plan>`;
the convenience launcher does not provision SSH or a public proxy.

The current Console uses a private Unix socket and an owned browser with
origin-scoped authentication headers. A temporary loopback URL is not a durable
or remotely accessible entry, and opening it in another browser does not carry
the authentication. Never include `console.token` in a URL or notification.
`remote_link_base` is for Buzz message mapping, not Console access; do not change
it to advertise the Console. Report delivery and browser verification separately.

## Open from a Mac through the existing SSH login

The standalone `scripts/hostd/console_macos.py` needs Python 3.9+ and Google
Chrome in `/Applications/Google Chrome.app`. It does not install dependencies or
change the server. Download the reviewed file over your already trusted SSH
connection to `jchen@192.168.20.24`, check its delivered SHA-256, and run it with
`python3 /absolute/path/console_macos.py`. The delivery receipt supplies the exact
server artifact path and digest; do not use an unfinished worktree copy.

SSH uses `StrictHostKeyChecking=yes`. Existing standard key files, the local SSH
agent, and interactive SSH authentication remain available. Custom SSH config is
not loaded (`-F /dev/null`) to avoid implicit commands, proxying or additional
forwards; if your existing key has a nonstandard path, pass
`--identity-file /absolute/path/to/your/existing/key`. The client only checks key
file metadata; OpenSSH performs authentication. Do not copy a key or Console token
to the server, disable host verification, or trust `ssh-keyscan` alone. A missing
trusted host entry must be verified using the normal SSH host-trust procedure.

One SSH connection forwards a private loopback TCP port to the existing remote
Console Unix socket. A fixed remote helper checks UID1009, runtime0700,
socket/token0600 and stable identities; the token travels only in captured SSH
stdout to memory. Continuous checks and per-request challenges invalidate access
on token/socket rotation or disconnect. The dedicated Chrome uses a new private
profile and CDP pipe. Every attached target is intercepted before it runs;
external HTTP requests/downloads are blocked (Chrome may preconnect TCP before
interception, but no HTTP bytes or bearer are released). Only exact-origin/route requests receive
the in-memory bearer. A second loopback listener validates the caller's bearer,
Host, Origin and CSRF boundary before rewriting backend Host/Origin; it does not
authenticate anonymous browsers. This adaptation is necessary because current
Chrome rejects a CDP override of the Host header.

Keep the terminal running. The JSON verification receipt contains only page/API
status, node/edge counts and a temporary URL usable by this dedicated window.
Closing the Console window or Ctrl-C closes the CDP/SSH channels normally. A
`cleanup_pending` receipt means cleanup is unconfirmed; the client does not kill
unverified PIDs or delete a still-used profile. `--verify-only` closes after the
readback and therefore does not leave an access window available.

A successful isolated Linux SSH/UDS/Chrome test is transport evidence only.
Actual macOS signature validation, visible window and page/graph readback require
the owner's Mac receipt. This is an SSH access workflow, not a LAN-wide public URL.

For Mac handoff, provide a self-contained prompt for the owner's Mac AI, including
the reviewed artifact path and SHA-256. Ask it to verify those bytes, use the
existing trusted SSH identity and installed Chrome, run the client, and return
only the metadata receipts and exit status. Do not ask it to bypass host trust,
codesign, path ownership, the HTTP origin boundary or process ownership checks.

Failures now include a fixed JSON `stage` and `code`, with a numeric
`process_exit` when a child exited. Client exit classes are: 10 Chrome validation,
20 SSH startup, 21 remote helper handshake, 30 loopback startup, 40 Chrome launch,
41 CDP pipe/setup, 50 page/graph readback, 60 cleanup, and 70 client runtime.
Raw SSH stderr, codesign output, HTTP bodies and CDP errors are never printed.
Recognized SSH text is classified in bounded process memory and then discarded;
an unrecognized failure remains generic rather than disclosing it.

The codesign requirement is an inline expression: its argv element after `-R`
must begin with `=`, followed by the Chrome identifier, Apple anchor and Google
team constraint. Native parser coverage uses Apple's `csreq` on macOS; Linux
transport tests cannot establish that native result.

For a permission failure, collect only ancestor indexes and numeric UID/GID/mode
metadata before considering any local repair. Keep the installed Chrome unchanged.
A `resource fork` / `Finder information` rejection can result from extended
attributes (Apple QA1940); it does not by itself establish tampering. The client
does not change permissions, clear attributes, re-sign bundles or bypass strict
verification. Mac diagnostics must not print attribute values or raw error text.

`chrome_validation/signature_rejected` is not permission to bypass signature
verification. `ssh_start/authentication_rejected` can differ from ordinary SSH
because this client deliberately ignores SSH config (`-F /dev/null`); the Mac AI
should compare which existing identity its normal trusted login uses and, when
needed, select that existing key using the supported identity-file option.
It must not copy private key material or enable arbitrary SSH config commands.
`remote_helper/invalid_handshake` means the fixed helper did not establish its
UID/socket/token proof; the code does not infer which remote check failed.
Both successful close receipts and a failed-stage receipt may occur: `closed`
proves child cleanup, not successful page access. `cleanup_pending` must remain
unconfirmed even when another error already identifies the startup failure.

## Rollback after hostd has processed work

The initial `rollback/` state is a pre-cutover baseline. Restoring it after new
work would discard responsibility. Use a **fresh ledger export**:

1. Record and stop hostd using the authorized migration procedure. Verify its
   main/control PIDs and cgroup task count are zero, and its state is inactive.
   Verify SDK children are reaped; `KillMode=control-group` is required. Check
   orphan processes separately if the prior unit did not use that policy.
2. Keep all 15 old timers and sync services stopped. Do not restore a timer
   while any hostd writer, child, or old sync writer remains alive.
3. Run the reviewed installer's export mode against the prepared release:

```bash
python3 scripts/install_hostd.py --export-rollback \
  --release /absolute/private/releases/FULL_40_CHARACTER_REVIEWED_SHA \
  --inventory /absolute/private/targets.json \
  --state-db /absolute/private/hostd.sqlite3 \
  --rollback-dir /absolute/private/new-rollback-operation
```

Export verifies native hostd and original-writer/timer quiescence before and
after a coherent latest-ledger snapshot. It uses the release's StateAdapter on
the private backup, checks each stored channel/chat hash/application identity,
and reads back all 15 exported private state files. Only `exported` with matching
identities and hashes is usable. Inactive services reporting native
`TasksCurrent=[not set]` are accepted only with empty cgroup and both PIDs zero.
If any proof fails, retain artifacts and resolve the failure before proceeding.

4. Review the saved original `rollback/manifest.json` and fresh export receipt.
   Restore each freshly exported state to its **saved exact original target path**, with
   nofollow path checks, 0600 permissions, fsync and hash readback. For an original
   null/absent ledger, preserve absence if no new ledger responsibility exists;
   when hostd has processed work, the verified fresh export carries that new
   responsibility and requires explicit reviewed restoration. Do not fabricate
   an initial state file or blindly delete a current ledger. Restore saved
   unit files only after comparing current files against the expected cutover
   version; retain conflicts rather than overwriting unreviewed changes.
5. Reload the user manager through the authorized migration procedure. Restore
   each saved timer's original UnitFileState and ActiveState individually:
   enabled vs disabled, enabled-runtime vs persistent enabled, active vs
   inactive. Do not blanket-enable/start all timers. Preserve original service
   startup policy; static services are not enabled. Verify each target state
   from the manager and verify that hostd remains stopped before declaring the
   rollback complete.

The installer intentionally does not execute steps 1, 4 or 5. Their authorized
live execution/readback belongs to the root migration task. Join timers and
unlisted units are outside this fixed 15-target operation. M-3 sender ownership,
M-4 onboarding/subscriptions, and M-5 Agent template reconciliation each require
their own saved state and verification; they are not implied by a hostd package.

## S7 delayed delivery notice for a bound topic

This procedure applies only to a reviewed fixed release that contains the
reviewed S7 coordinator and has passed current native and L3 acceptance. An
installer candidate, a database schema, an offline test, or a stored notice row
alone does not mean that a message was delivered or that S7 is active. Do not
add startup switches or change a binding to enable S7 unless the reviewed release
instructions explicitly require it.

The host that owns the protected channel binding uses that binding's own
sync-bot to watch for a complete, current, signed message from a publicly
attested foreign agent. The foreign host may be offline. The binding owner
resolves the sender from the current public signed owner policy and current
native group membership; it does not look up a foreign private configuration or
require a foreign-host lane. Only the binding's own sync-bot posts the notice.
The notice is about whether the original message has appeared in this exact
topic; it is not a receipt from the foreign agent and does not claim that its
host is retrying.

The 60-second timer starts when the binding owner first observes the valid
signed source locally. An older source found during reconnect or catch-up starts
a fresh 60-second interval from that first local observation. Missing,
ambiguous, stale, or incomplete signed source, public owner identity or policy,
current group membership, binding, or root-thread evidence cannot start a timer or authorize a
notice. The bot must use the original topic; it must not fall back to a new
chat-level message.

If a complete current native read already proves that the exact source message
is present before the notice is reserved, the pending notice is settled without
sending one. Otherwise, after 60 seconds, the sync bot may send one notice in
the original topic. Before treating it as sent, the reviewed runtime reads the
native message back and checks the bot, topic, root, marker, version, and full
card. Delivery recovery also requires the complete current signed source and
native card to match, including image order, media type, size, and bytes. A
resource-changing image edit that this release does not support stays pending;
it is not reported as recovered.

The notice says that delivery has not yet been confirmed and suggests checking
the console after five minutes. Five minutes is advice only: there is no second
notice and no automatic resend. Once delivery is fully verified, the runtime
updates that same notice in place. A reserved or unknown result after restart
is read back against the original notice. It never causes another POST, another
message, adoption of a different notice, or a repeated ambiguous edit. If the
original notice ID was not obtained, the runtime may resolve it only by the
reviewed exact readback procedure for that original operation; operators must
not create a replacement manually.

When a notice remains pending, retain it and check the responsible machine's
relay connection, current public signed owner identity and policy, native group
membership, protected binding, and original topic mapping. Do not edit protected
configuration or manually repost to clear the notice. Use the current
public agent and group display names in support requests; if either name is
unavailable, say “本群” or “本话题.” Do not paste internal IDs, keys, credentials,
message bodies, or private configuration into a ticket or chat.

Suggested notice copy uses `{agent_name}` only from the current public agent profile
and `{group_name}` only from the actual native group display name. Never use a
channel UUID, chat ID, application ID, or public key in either placeholder. If
the profile name is unavailable, say “该 agent”; if the group display name is
unavailable, use “本群.” The notice refers to the bound topic as “本话题.”

> 来自 {agent_name} 的这条消息尚未确认送达本话题。怎么解决：若 5 分钟后仍未恢复，请打开控制台检查负责机器和连接状态。复制给 AI：请检查 {group_name} 本话题中 {agent_name} 的消息投递和负责机器的 relay 连接；不要代发或重复发送，不要输出凭据。

After the exact original message is fully verified, update the same notice with:

> 刚才暂未送达的消息已经在本话题恢复，无需重复发送。

## Complete public policy discovery

Public managed-agent policies can exceed the ordinary signed reader's 256-event
capacity. Onboarding, remote target discovery and fallback discovery use the
dedicated `policy_snapshot` read: one owner-authenticated query for kind 30177,
fixed `limit: 1000`, with no caller-supplied filters or capacity. This matches
Relay 0.2.1's advertised NIP-11 `max_limit` and its
[`DEFAULT_MAX_PAGE_LIMIT`](https://github.com/block/buzz/blob/6e5c462ac524de60d7edb46c66130fd779cc9006/crates/buzz-db/src/store/event.rs).
A full 1000 rows is ambiguous and remains pending. All rows must pass signature,
shape, kind, time and unique-ID checks; the 1 MiB response and 10-second child
budget still apply. Ordinary signed queries retain their existing limit.

Fallback profile reads cover every discovered subject in batches of 64, with a
1000-subject total bound and the existing before/after authority checks. Never
truncate policies or profiles to infer that a competing claim is absent. If the
relay's configured page ceiling changes, verify that contract before changing
this fixed operation; increasing only the client limit cannot prove completeness.

## Browser Console names and navigation

The standalone `hostd.console_web` gateway can add names without restarting the
hostd backend. Pass `--metadata-file /home/jchen/.config/buzz-hostd/console-web/metadata.json`
with its existing `--runtime-dir`, `--password-file` and loopback `--port` options.
The directory must be owner-only 0700 and the regular snapshot file owner-only
0600, without symlinks or hard links. It is display metadata, never a credential
or an authority source. Backend operation admission and receipts are unchanged.

The version-1 snapshot contains `observed_at`, `bindings` (exact `binding_id`,
`channel_id`, full `chat_ref`, `chat_id`, `chat_name`, `channel_name`) and `agents`
(exact `pubkey`, `app_id`, configured `name`). Use verified local configuration
and native channel/chat metadata; never infer a name from message content.
Missing names are empty strings, not IDs or guessed aliases. Do not put tokens,
configuration paths or arbitrary extra fields in this file. Private source
provenance belongs in a separate receipt, not the browser response.

The gateway reads the snapshot once at startup and serves it only after its
normal Basic/same-origin checks. Missing, invalid or future metadata leaves names
explicitly unavailable; graph status and operations still work. More-than-24-hour-old
metadata remains readable as a display cache, with its time and a refresh reminder.
It never supplies current status or authorization. Refresh the verified snapshot
and restart only the standalone gateway to
pick up new names. Page refreshes perform no native/relay name lookups.

The UI names local nodes only when the complete group reference and current
binding/channel edges match; public registrations retain their privacy boundary.
Names use text-only DOM nodes, full values are in SVG tooltips, and identifiers
remain visible for checking. Detail links use fixed formats:
`https://applink.feishu.cn/client/chat/open?openChatId=<encoded-chat-id>` and
`buzz://channel/<canonical-UUID>`. The Buzz form is the desktop client's
`parse_channel_deep_link` contract; arbitrary schemes, hosts, query parameters
and fragments are never accepted from metadata. Links require a user's ordinary
click and use `noopener noreferrer`; snapshot data never triggers navigation.
