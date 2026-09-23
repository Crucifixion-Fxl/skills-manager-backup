# docs/deployment/cd-requirements.md — discussion-of-record for a deployment.
#
# This file is created (or updated) by workflows/interview-cd-requirements.md
# BEFORE any manifest gets generated. It captures the user's intent in a way
# that can be reviewed by ops / SRE before the workflow proceeds.
#
# Required slots: substitute every {{slot}} below. If a slot is unknown,
# WRITE THE QUESTION in place of the value and STOP — do not invent.
#
# Sections marked OPTIONAL: keep the heading and write "(n/a)" if not relevant.

# {{app}} deployment requirements

## Purpose

{{one_paragraph_description_of_what_this_service_does}}

## Domain and app type

- Domain: {{ops_or_builder}}
- App type: {{app_type}}
- Workload profile: {{optional_data_or_observability_or_na}}
- Justification: {{one_line_reasoning}}

## Targets

| Env keyword | Cluster | Namespace | Branch / target revision |
|---|---|---|---|
| {{env_keyword_1}} | (from references/data/env-keywords.yaml lookup) | (from namespace_pattern) | {{branch_1}} |
| ... | ... | ... | ... |

## Workload shape

- Stateless / stateful / cronjob: {{shape}}
- Runtime profile (`service` / `static-web`): {{runtime_profile}}
- Language or build toolchain: {{language_or_build_toolchain}}
- Production runtime start command (`service` only): {{runtime_start_command_or_na}}
- Replicas (staging / prod): {{replicas_staging}} / {{replicas_prod}}
- Container port: {{port}}
- Health check path: {{health_path}}
- Resource footprint (requests/limits): {{cpu_mem}}
- Build mode: {{dual_arch_or_single_arch}}

### Static web contract

Write `(n/a — service profile)` when this is not a static web application.

| Target | Exact package.json build script | Explicit output directory | Reviewed non-secret bundle config source | Target-specific image |
|---|---|---|---|---|
{{static_web_target_rows_or_na}}

- SPA routing (`yes` => `/index.html` fallback; `no` => `=404`): {{spa_routing_or_na}}
- Pinned exact Node build semver: {{node_build_version_or_na}}
- Pinned exact Nginx runtime semver: {{nginx_version_or_na}}
- Audited credential-free static verification runner tag:
  {{static_verify_runner_tag_or_na}}
- `package.json` / `package-lock.json` are tracked and
  `npm ci --ignore-scripts --dry-run` passes: {{static_lockfile_verified_or_na}}
- Bundle-time inputs contain no credentials or secrets: {{static_bundle_has_no_secrets_or_na}}
- Nginx runtime has no Secret env/envFrom/volume or generated frontend config:
  {{static_runtime_has_no_secrets_or_na}}
- Port compatibility (`1024..65535` non-root preferred; root-master port 80 only for a
  confirmed source contract with explicit migration-owner approval):
  {{static_port_compatibility_or_na}}

### Scheduled job contract

Write `(n/a — not a cronjob)` for every value when the workload is not periodic.

- Standard five-field schedule: {{cron_schedule_or_na}}
- `spec.timeZone` decision and target API/repository evidence: {{cron_timezone_decision_or_na}}
- `concurrencyPolicy`: {{cron_concurrency_policy_or_na}}
- Missed-run `startingDeadlineSeconds`: {{cron_starting_deadline_seconds_or_na}}
- Per-run `activeDeadlineSeconds`: {{cron_active_deadline_seconds_or_na}}
- Per-run `backoffLimit`: {{cron_backoff_limit_or_na}}
- Successful / failed Job history limits: {{cron_history_limits_or_na}}
- Immutable tag plus digest image: {{cron_image_digest_or_na}}
- Explicit non-empty command / args: {{cron_command_args_or_na}}
- Explicit env/envFrom and credential owner/resolver decision: {{cron_env_contract_or_na}}
- Resource, security, placement, and ServiceAccount decision: {{cron_runtime_controls_or_na}}
- Registration remains `spec.suspend: true`; activation uses a separate reviewed MR:
  {{cron_activation_contract_or_na}}

## Resources required

For each, justify why it's needed and reference cost-tiering.yaml for sizing:

### Datastores

(omit if none)

- {{resource_kind}}: {{purpose}}
  - staging: {{cost_tiering_staging_summary}}
  - prod: {{cost_tiering_prod_summary}}
  - Monthly cost order-of-magnitude: {{cost_estimate}}

### Caching

(omit if none)

### Message queues / streaming

(omit if none)

### Object storage

(omit if none)

## External dependencies

- Outbound third-party APIs: {{list}}
- Inbound webhook / OAuth callbacks: {{list}}
- DNS (public / internal): {{list}}

## Secrets

For each secret, list the Vault path (resolved via references/vault-paths/resolver.md) and how it's populated:

| Vault path | Populated by | Consumed by |
|---|---|---|
| (e.g. secret/staging/rds/application/{{app}}/database) | Crossplane PushSecret (automatic) | app via ExternalSecret |
| (e.g. secret/staging/app/{{app}}/stripe-webhook) | Maintainer+ writes via Vault UI | app via ExternalSecret |

## Network exposure

- Public ingress required: {{yes_or_no}}
- If yes:
  - Hostname: {{hostname}}
  - SG (from-office / WAF only / open): {{sg_choice}}
  - WAF: {{required_or_not}}
  - SSO / auth in front of app: {{yes_or_no}}

## Observability

- Logs: pod templates have `app` and `env` labels => fluent-bit-base auto-routes. Confirm: {{yes}}
- Metrics: app exports `/metrics` for Prometheus scrape? {{yes_or_no}}
- Tracing: {{yes_or_no_and_protocol}}
- Sentry project: {{existing_or_to_be_onboarded}}

## Reviewer checklist

- [ ] Domain + app type matches references/data/env-keywords.yaml routing
- [ ] All declared resources sized per cost-tiering.yaml (staging-min / prod-prod)
- [ ] Every Vault path resolved via references/vault-paths/resolver.md (no `secret/cicd/*` for business creds)
- [ ] Public ingress (if any) is not naked: has SG OR WAF OR app-level auth
- [ ] Prod targets pass prod_self_check items from cost-tiering.yaml
- [ ] Runtime profile is explicit; Node static-web is not treated as a long-running Node service
- [ ] Static-web lockfile is tracked, synchronized, and suitable for the exact `npm ci` contract
- [ ] Static-web exact build script, output directory, SPA routing, health path, and per-target public bundle config are explicit
- [ ] Static-web exact build has proven production semantics; Vite effective `NODE_ENV=production` and no development-mode override exists
- [ ] Static-web CI uses an audited credential-free runner and Harbor base Node image; it runs exact `npm ci` plus each target build script and verifies non-empty output for merge requests and target-branch pushes
- [ ] Static-web bundle/runtime inputs contain no Vault, ExternalSecret, Kubernetes Secret, CI secret variable, Docker secret ARG/ENV, or generated secret config
- [ ] Each static-web target builds its own image artifact; free-form config evidence is not interpolated into Dockerfile/Nginx comments
- [ ] CronJob schedule/timezone, concurrency, missed-run/job deadlines, backoff, history, immutable tag plus digest, command/args, env, resources, security, placement, and ServiceAccount are explicit
- [ ] New CronJob source is suspended; activation is a separate reviewed MR and no Service/Ingress is created implicitly
