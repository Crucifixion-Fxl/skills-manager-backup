# Authentication and environments

Read this reference before any `register-only`, `register-and-vip`,
`add-vip-to-existing`, `mock-vip-lifecycle`, or `set-vip-endtime` call.

## Contents

- [Environment selection](#environment-selection)
- [Staging token priority](#staging-token-priority)
- [API key configuration](#api-key-configuration)
- [JWT fallback](#jwt-fallback)
- [Authentication failures](#authentication-failures)
- [Contributor-only local override](#contributor-only-local-override)

## Environment selection

| Environment | Base URL | Authentication | Allowed use |
|---|---|---|---|
| Staging (default) | `https://qa-tools-staging.addx.live` | Bearer API key or SSO JWT | Operations 1–5 |
| Local override | `http://localhost:3082` | None | Explicit contributor testing of unmerged qa-tools changes |

Set staging variables without printing the credential:

```bash
export QA_TOOLS_URL='https://qa-tools-staging.addx.live'
# QA_TOOLS_TOKEN is loaded below; never echo it.
```

Only switch to local when the user explicitly asks for local/localhost or says
the qa-tools change is not yet deployed, and the user is a qa-tools contributor
with the local qa-tools and iot-service environments already available.

Free License does not use this environment fallback. Its staging/pre/prod rules
and `FREE_LICENSE_URL/FREE_LICENSE_TOKEN` variables are defined only in
[`free-license.md`](free-license.md).

## Staging token priority

qa-tools accepts either:

| Mode | Shape | Lifetime | Intended use |
|---|---|---|---|
| API key | `qatools_<32 hex>` | 30 days | CI, cron, long-running skill use |
| SSO JWT | `eyJ...` | A few hours | Interactive developer fallback |

Use the first non-empty value in this order and stop:

1. Existing `$QA_TOOLS_TOKEN` in the current process.
2. `${A4X_PASSWORD_FILE:-$HOME/.codex/password}` key
   `qatools.staging-api-key`.
3. The same file's `qatools.staging` JWT.
4. If none exists, stop and tell the user how to configure one. Do not ask for
   the plaintext in chat and do not attempt programmatic SSO login.

Expected file shape:

```yaml
qatools:
  staging-api-key: "qatools_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  staging: "eyJhbG..."
```

Load without displaying the value:

```bash
PASSWORD_FILE="${A4X_PASSWORD_FILE:-$HOME/.codex/password}"
if [ -z "${QA_TOOLS_TOKEN:-}" ] && [ -f "$PASSWORD_FILE" ]; then
  QA_TOOLS_TOKEN=$(awk '/^qatools:/{flag=1;next} /^[a-zA-Z]/{flag=0} flag && /staging-api-key:/{
    sub(/^[ \t]*staging-api-key:[ \t]*/,""); gsub(/^"|"$/,""); print; exit
  }' "$PASSWORD_FILE")
  if [ -z "$QA_TOOLS_TOKEN" ]; then
    QA_TOOLS_TOKEN=$(awk '/^qatools:/{flag=1;next} /^[a-zA-Z]/{flag=0} flag && /^[ \t]*staging:/{
      sub(/^[ \t]*staging:[ \t]*/,""); gsub(/^"|"$/,""); print; exit
    }' "$PASSWORD_FILE")
  fi
  export QA_TOOLS_TOKEN
fi
```

The JWT expression is anchored at the start of the key so it cannot accidentally
match `staging-api-key`.

## API key configuration

API keys are the recommended persistent path. An admin creates or refreshes one
at `https://qa-tools-staging.addx.live/api-keys`. Plaintext is shown once; the
user stores it under `qatools.staging-api-key` in the password file.

- Never copy the key into chat, logs, generated documentation, an MR, a commit,
  shell history, or an rc/profile file.
- Refreshing produces new plaintext and revokes the old value. Replace the
  existing password-file line rather than appending duplicate `qatools` blocks.
- A legacy `qa-tools:` password-file section must be renamed to `qatools:`;
  preserve the credential value.

If the user has no key, direct them to an authorized qa-tools admin. The skill
must not create its own credential or borrow another user's credential.

## JWT fallback

Use JWT only when no API key is configured or the user explicitly chooses it.
The token comes from the Feishu SSO platform entry, not the qa-tools service URL:

1. Open `https://micro-app-platform-us.addx.live/qa-tools-staging/` and sign in.
2. In browser DevTools, open Application/Local Storage for
   `micro-app-platform-us.addx.live`.
3. Copy the value stored as `token`, `access_token`, or `authorization`.
4. Store it under `qatools.staging` in the password file using a local editor or
   clipboard command. Do not paste it into the agent conversation.

For macOS, after the user has copied the token, this avoids terminal wrapping:

```bash
PASSWORD_FILE="${A4X_PASSWORD_FILE:-$HOME/.codex/password}"
mkdir -p "$(dirname "$PASSWORD_FILE")"
if ! grep -q '^qatools:' "$PASSWORD_FILE" 2>/dev/null; then
  printf 'qatools:\n  staging: "%s"\n' "$(pbpaste)" >> "$PASSWORD_FILE"
else
  echo "Edit the existing qatools.staging line; do not append a duplicate block."
fi
```

Linux users may replace `pbpaste` with an available clipboard reader. Do not use
a heredoc for a long JWT. The user may check the extracted value's length, but
the agent must not print the value itself.

## Authentication failures

Do not retry an invalid credential blindly and do not silently downgrade from
one credential mode to another.

| Error | Meaning | Required response |
|---|---|---|
| `API_KEY_INVALID` | Key hash not found | Stop; ask the admin to verify or issue a new key |
| `API_KEY_EXPIRED` | 30-day lifetime ended | Stop; ask the admin to refresh it |
| `API_KEY_REVOKED` | Key was revoked | Stop; ask the admin to issue a new key |
| `AUTH_TOKEN_EXPIRED` | JWT expired | Stop; have the user replace `qatools.staging` |
| `AUTH_TOKEN_INVALID` | JWT invalid | Stop; have the user reacquire and replace it |
| `AUTH_TOKEN_MISSING` | No usable token | Stop and follow the configuration flow above |

If a task genuinely cannot continue until the user updates credentials, ask once
and wait. Do not repeatedly request or expose credentials.

## Contributor-only local override

Local is permitted only when all are true:

1. The user is a qa-tools contributor.
2. They explicitly request local validation of an unmerged/undeployed change.
3. The local qa-tools checkout and full iot-service development environment exist.

Use:

```bash
export QA_TOOLS_URL='http://localhost:3082'
unset QA_TOOLS_TOKEN
export QA_TOOLS_PROJECT_DIR="${QA_TOOLS_PROJECT_DIR:-$HOME/Documents/qa-tools}"
```

Health-check first:

```bash
curl -sf "$QA_TOOLS_URL/api/health" >/dev/null 2>&1 && echo RUNNING || echo DOWN
```

If down, verify `$QA_TOOLS_PROJECT_DIR/package.json`. If the user explicitly
requested this local validation and the existing checkout is present, install
its pinned dependencies with `npm ci` only when `node_modules` is absent, then
start `npm run server:dev` in the background with a local log. Wait up to 30
seconds for `/api/health`; on failure inspect that log instead of retrying API
mutations. If the checkout is absent, ask for its actual path or stop; do not
silently clone a second copy.

Local qa-tools is a proxy. Its required dependencies include:

- iot-service on `localhost:7777`, Java 11, `appName=iot-service-cloud`, and
  profile `dev-local`;
- MySQL `camera.external_customer` and
  `device_manual.device_category` in the expected local schema;
- qa-tools `.env` with a Stripe test key (`sk_test_*`).

Local requests omit the Authorization header. A local 500/502 is dependency
evidence, not authorization to fall back to staging or mutate another environment.
