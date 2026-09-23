# Workflow final-output summary card.
#
# Every workflow ends by printing exactly this card (filled in) plus the
# Ops Todo table (see ops-todo-table.md). No "thinking", no alternatives,
# no questions to the user — those go in earlier interview phase.
#
# Required slots:
#   {{intent}}             one of the routing-table intents from SKILL.md
#   {{app}}                kebab-case
#   {{domain}}             ops | builder
#   {{app_type}}           c-end | restricted-admin | builder | data | observability
#   {{workload_profile}}   optional finer workload profile: data | observability | n/a
#   {{targets}}            one or more env keywords (e.g. staging-us, prod-us-restricted-admin)
#   {{generated_files}}    list of file paths the workflow wrote, with one-line purpose each
#   {{validator_results}}  one line per validator: "<check>: PASS|FAIL <count>"
#   {{ops_todo_count}}     integer; if >0, also print the Ops Todo table below the card
#   {{next_actions}}       1-3 imperative lines the user / reviewer should do next

# === Summary card output (literal markdown the workflow writes to stdout) ===

## Deployment summary

- Intent: {{intent}}
- App: {{app}}
- Domain: {{domain}} / {{app_type}}
- Workload profile: {{workload_profile}}
- Targets: {{targets}}

### Files written

{{generated_files}}

### Validators

{{validator_results}}

### Ops todos: {{ops_todo_count}}

(if {{ops_todo_count}} > 0, the Ops Todo table follows)

### Next actions

{{next_actions}}
