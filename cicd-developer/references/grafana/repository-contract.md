# Dashboard repository contract

The sole target repository is:

```text
https://gitlab.addx.ai/DEV/grafana-dashboards-as-code.git
```

Dashboard source is JSON only at:

```text
dashboards/<environment>/<namespace>/<service>.json
```

The repository owns policy and configuration. Read its `config/` files and run
its modules; do not duplicate allowlists or hard-code a data-source UID.

1. Resolve `environment + cluster + victoria-metrics-ref` with
   `scripts.datasource_catalog.expected_datasource_uid`.
2. Write the resolved UID to every panel `datasource` object.
3. Run `python -m scripts.validate_config` and
   `python -m scripts.validate_all --repo-root . --file <path>` from the
   isolated checkout.
4. Keep tags: `managed-by-grafana-skill`, `team-*`, `service-*`,
   `environment-*`, `namespace-*`, `cluster-*`, and
   `victoria-metrics-ref-*`.

`cluster-*` is source ownership metadata for the approved data-source route; it
is **not** a PromQL requirement. Preserve a developer-supplied expression
verbatim. Do not add, remove, validate, or compare a `cluster` label matcher.
The repository performs only basic source checks: each expression must be
non-empty and contain at least one non-empty, non-wildcard label matcher, without
assuming a fixed metric label name. An invalid expression must fail rather than
be rewritten. This local workflow does not prove the metric exists or its
returned value has the intended business meaning.

Default panels are `timeseries` with `drawStyle: line` and
`templating.list: []`. Do not add selector variables. A requested per-Pod view
uses a bounded query grouped by `pod` or `source_pod`, not a Dashboard variable.

For source-changing operations, the local Skill also renders an **output-only**
expected public URI from the stable Dashboard UID using
`target-repository.json -> dashboardUriTemplate`. It is not an API endpoint:
the Skill never reads `GRAFANA_URL`, sends a request to the URI, accepts a
user-provided base URL, or puts credentials in it. Return the URI after the MR
URL with the condition that the MR must merge and deploy/verify must succeed.
