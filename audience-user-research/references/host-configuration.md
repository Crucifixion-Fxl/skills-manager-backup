# Host configuration

## Runtime bootstrap

Use the one transport the host actually provides:

- Hermes and other tool-only hosts use their registered named Audience operations. The Hermes Skill bundle does not
  install this repository's `scripts/` or `src/`, so it must not attempt a Python CLI fallback.
- Any Agent running a complete canonical Skill clone can use the repository's standard-library Python client from any
  working directory:

```bash
python3 /absolute/path/to/skills/audience-user-research/scripts/preflight.py
python3 /absolute/path/to/skills/audience-user-research/scripts/api.py capabilities
```

The complete bundle includes `SKILL.md`, `references/`, `scripts/`, `src/` and `contracts/`. Do not install a second
client, construct raw HTTP, or ask for Typeform/Brevo credentials when either supported transport is available.
`AUDIENCE_PLATFORM_BASE_URL` is accepted only for `https://audience-workflow-api-prod-us.addx.live` and
`https://audience-workflow-api-staging-us.addx.live`. Any other HTTPS host fails with `origin_not_allowed` before the
Project key is placed on a request. HTTP is only for localhost when `AUDIENCE_ALLOW_LOCALHOST_HTTP_FOR_TESTS=1`.

## Project key

The host injects one Project Personal key as `AUDIENCE_API_KEY`. Credentials stay in environment or the
host secret store and never appear in prompts, argv, request JSON, logs or files.

Hermes and other tool-only hosts already bind the Audience origin; call the registered named operations and do not
invent, override or concatenate another host.

When the complete canonical clone must choose an origin (CLI or any Agent without a host-injected transport), the
Personal API HTTPS origin is `https://audience-workflow-api-prod-us.addx.live`. The stdlib client uses that origin
unless `AUDIENCE_PLATFORM_BASE_URL` is already set to that host or
`https://audience-workflow-api-staging-us.addx.live`. Do not call Audience Admin, in-cluster
`*.svc.cluster.local`, NocoDB, any other hostname, or `/api/admin/` routes. Literal paths stay
`/api/platform/v3/...` as listed in [platform-api.md](platform-api.md).

Run online self-context before the first Project operation. A tool-only host calls the registered
`get_project_personal_key_context` operation. In a complete clone, the equivalent CLI call is:

```bash
python3 scripts/api.py get_project_personal_key_context --request-stdin <<'JSON'
{"path":{},"query":{},"body":{}}
JSON
```

`GET /api/platform/v3/personal-key` takes no path/query/body selection. Its successful response is authoritative for
`project_id`, `binding_revision`, credential profile and `allowed_actions`. The legacy response has exactly these four
fields; `research_track` is an optional fifth field. When absent, use only the existing `materialized_audience`
workflow. When present, accept only `materialized_audience` or `questionnaire_only`; only an explicit
`questionnaire_only` selects that branch. Never derive Project, track, or additional grants from a token, Project
name, or credential profile. Local token shape or preflight only checks configuration; it does not authenticate the key.

If the host supplies several named keys, verify each independently and choose the key whose returned Project and
actions match the request. Do not combine grants across keys. A failed entry does not invalidate a successful one.
Continue every request/readback for one journey with the same key and returned Project.

Only after self-context, and only when preparing the `materialized_audience` selection path, read
`personal_research_readiness` and `personal_research_query_capabilities` as needed. A
`questionnaire_only` Project does not require warehouse readiness or query capabilities to create a form.
Permissions and readiness are separate: an allowed action may be disabled or unconfigured in the current deployment.

## Full-clone CLI transport

This section applies only when the complete canonical clone is present. Use a named operation plus one bounded JSON
object:

```bash
python3 scripts/api.py OPERATION --request-stdin <<'JSON'
{"path":{"project_id":"<self-context project>"},"query":{},"body":{}}
JSON
```

Path and body types follow the bundled OpenAPI. CLI query values are strings, including numeric pagination values.
The client uses literal routes, validates schemas and response Project binding, rejects redirects, and returns one
safe JSON object. CSV is a host attachment response: the host saves or forwards bytes outside model context; do not
print or base64 them into chat. Every JSON CLI result drops email, uid, profile, provider payload, survey URLs and
per-user links (a `uid` query or fragment, including percent-encoded forms, or a link item `url`). Binding-matched `form_url`, Draft URL and Admin detail URLs stay.
`response_id`, closed answers, match status and page totals stay. `AudienceClient.call()` is not projected, so journey
scripts can still verify link binding. Complete-clone downloads require the host to
inject an existing absolute directory as `AUDIENCE_ATTACHMENT_DIR`. `--output` is only a filename inside that
directory; reject absolute paths, `..`, separators, symlinks and special files. Missing sink is a capability gap, not a
reason to write elsewhere.

The complete-clone client uses `AUDIENCE_PLATFORM_TIMEOUT_SECONDS` for ordinary JSON calls (default 10 seconds)
and `AUDIENCE_PLATFORM_ATTACHMENT_TIMEOUT_SECONDS` for CSV and other attachment downloads (default 120 seconds).
Each accepts a positive value of at most 120 seconds. These settings apply to this Python client only; tool-only
hosts use their own transport timeouts.

## Environment truth

A contract in this checkout describes the intended client surface. Current availability comes from the deployed
self-context, readiness, capabilities and operation responses. A local green test or configured key does not prove an
environment has enabled Typeform, DATA sensors or Brevo.
