---
name: zendesk-admin-config
description: Plan, review, apply, and verify NeoPace Zendesk Admin ticket groups, custom fields, forms, and triggers through the official REST API. Use for Zendesk business configuration, field-catalog rollout, configuration drift, or determining which Customer Care decisions remain unresolved. Do not use for Help Center article content or Mobile SDK/JWT infrastructure.
---

# Zendesk Admin Config

Safely manage `neopace.zendesk.com` ticket configuration as reviewed JSON. This skill is separate from `zendesk-helpcenter` (article content) and from Golf Mobile SDK/APISIX/Vault deployment.

## Description

Use this skill for:

- ticket groups and forms;
- custom ticket fields and dropdown options;
- routing triggers after Customer Care approves their rules;
- read-only inventory, drift detection, and post-change verification.

The desired NeoPace catalog is [assets/neopace-support-config.json](assets/neopace-support-config.json). Business decisions and field-source notes are in [references/neopace-field-catalog.md](references/neopace-field-catalog.md).

## Rules

1. Authentication is OAuth 2.0 client credentials. Credentials come only from `ZENDESK_OAUTH_CLIENT_ID`, `ZENDESK_OAUTH_CLIENT_SECRET`, and `ZENDESK_SUBDOMAIN=neopace`; a short-lived `ZENDESK_OAUTH_ACCESS_TOKEN` is allowed for one-off validation. Never enable deprecated API-token access for this skill, and never request or print credentials in chat, arguments, plans, logs, or repository files.
   Request the global `read write` scopes. Zendesk does not expose resource-specific OAuth scopes for Groups, Ticket Fields, or Ticket Forms; the skill compensates with an explicit allowlist, no delete operations, plan hashing, and mandatory confirmation before writes.
2. Run `snapshot` and `plan` before every write. Review the full plan file and quote its SHA when requesting approval.
3. `apply` requires the exact `--confirm-plan-sha`; never infer approval from an earlier task or environment change.
4. This tool never deletes resources. It creates or updates named resources and appends managed fields to a form without removing existing system fields.
5. A resource with `enabled:false`, `decision_required:true`, or `__REQUIRED__` is not writable. Do not invent Group members, Owner Agent, Contributor Agent, tag rules, or integration ownership.
6. Keep snapshot, plan, and backup files outside the repository. They are mode `0600` and can contain tenant metadata.
7. After apply, run `verify`; nonzero drift blocks completion. Then perform a Zendesk sandbox ticket UAT before production use.
8. Do not use raw `curl` for Zendesk writes. Use the bundled CLI so tenant pinning, timeouts, backups, and fail-closed checks remain active.
9. Dropdown options are fully managed by the desired field entry. Review option removals in `before`/`after`; field type changes require a separate migration and are refused.

## Workflow

```bash
export ZENDESK_SUBDOMAIN=neopace
export ZENDESK_OAUTH_CLIENT_ID='neopace_zendesk_admin_config'
read -rs ZENDESK_OAUTH_CLIENT_SECRET && export ZENDESK_OAUTH_CLIENT_SECRET

python3 skills/zendesk-admin-config/scripts/zendesk_admin.py check-auth \
  --config skills/zendesk-admin-config/assets/neopace-support-config.json

work_dir=$(mktemp -d)
python3 skills/zendesk-admin-config/scripts/zendesk_admin.py snapshot \
  --config skills/zendesk-admin-config/assets/neopace-support-config.json \
  --output "$work_dir/snapshot.json"
python3 skills/zendesk-admin-config/scripts/zendesk_admin.py plan \
  --config skills/zendesk-admin-config/assets/neopace-support-config.json \
  --output "$work_dir/plan.json"
```

Stop here and review `$work_dir/plan.json`. After the user explicitly confirms the displayed plan SHA:

```bash
python3 skills/zendesk-admin-config/scripts/zendesk_admin.py apply \
  --config skills/zendesk-admin-config/assets/neopace-support-config.json \
  --plan "$work_dir/plan.json" \
  --confirm-plan-sha '<exact SHA from reviewed plan>' \
  --backup-dir "$work_dir/backups"
python3 skills/zendesk-admin-config/scripts/zendesk_admin.py verify \
  --config skills/zendesk-admin-config/assets/neopace-support-config.json
```

## Examples

### Bad

```bash
# Unsafe: deprecated API token enters shell history and write bypasses review.
curl -u 'admin@example.com/token:DEPRECATED_TOKEN' -X POST \
  https://neopace.zendesk.com/api/v2/ticket_fields.json
```

### Good

```text
Plan 8ab… proposes 11 creates, 1 form update, and no deletes.
Four Customer Care decisions remain disabled. Please confirm this exact plan SHA
before apply; no online configuration has been changed yet.
```
