# lark-cli executable and instruction selection

Use the installed `@larksuite/cli` with the Node and CLI entry absolute paths
specified by the user's local `AGENTS.md` or equivalent policy. The user removed
fixed CLI version and version-dependent hash matching on 2026-10-02. Do not
compare the package version, package file tree, CLI entry, or built-in Skills
against pinned versions or hashes before using the CLI.

## Local checks

1. Confirm the specified Node and CLI entry files exist and the CLI entry is
   inside the specified `@larksuite/cli` package root. Confirm the package name
   in `package.json` is `@larksuite/cli`.
2. Invoke the CLI through those absolute paths. When local policy or the user
   authorizes an already installed native binary, invoke that binary by its
   verified absolute path inside the selected package. Do not use a PATH-resolved
   `lark-cli` executable or rely on the CLI entry's `/usr/bin/env node` shebang.
3. Read the relevant built-in Skill from the installed CLI before using its
   commands. Its current instructions define the command flags; no fixed
   Skill-version or output-hash comparison is required.
4. For user operations, verify the approved profile, App ID, user identity and
   token status with `whoami` or `auth status`. Keep explicit `--profile` and
   `--as user` on business commands. Confirm each write's target and read back
   the result with the same identity.

This reference controls executable selection and identity checks. It does not
grant write authorization; follow the user's task-specific instructions.

## Read-only executable and identity preflight

Before invoking an npm/Node launcher, inspect its source and verify the native
binary it delegates to already exists. A cached npm package is not proof that
the native runtime is installed: a launcher may download, verify, extract and
install the binary even for `--help` or `skills read`. When installation is not
in scope, stop before such a bootstrap. Do not invoke `npx`, postinstall,
`install` or `update` as a fallback. If an unexpected bootstrap occurs, disclose
the setup side effect and its known output path; do not remove shared caches or
claim an unobserved download host or complete cleanup.

For an authorized pure identity discovery, an unknown expected account/profile
is not a reason to refuse the CLI's current default `whoami` or local
`auth status`. Record the actual effective profile and user as discovery, then
compare with the intended identity before business access. Do not silently
switch profiles, alter config or equate cached user names with server proof.
Business and write commands retain their explicit approved-profile requirements.

Use the already installed, authorized native binary directly where permitted:

```bash
"$APPROVED_LARK_NATIVE" --help
"$APPROVED_LARK_NATIVE" auth status --help
"$APPROVED_LARK_NATIVE" whoami --help
LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1 \
  "$APPROVED_LARK_NATIVE" auth status --json
LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1 \
  "$APPROVED_LARK_NATIVE" whoami --as user
```

The variable denotes a verified local policy/user-authorized absolute native
binary path, not a runtime path fixed in this Skill. Read its current built-in
`lark-shared` identity and output references. Help declares these commands read;
management output may use a command-specific status shape rather than a business
API `ok` envelope, so preserve the actual shape and exit status.

Capture output privately and project only public user name, effective profile,
identity type, documented status and presence/expiry booleans. Omit token values,
open/user IDs, scopes, remediation hints, raw errors and full auth output. Expiry
booleans may be derived transiently from documented expiry fields; do not log the
raw credential object. An expired access state with a live refresh deadline is
not a refreshed session. Plain local status does not establish a server-verified
identity, resource permission or business-read PASS. `--verify`, login, refresh,
logout and profile/config mutations are separate actions; do not automatically
run them during a status-only task. Check each host's installed runtime separately
and report missing tooling without installing or copying credentials.
