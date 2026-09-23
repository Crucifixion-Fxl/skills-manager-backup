# Client Installation

## Configuration Location

Keep the runtime configuration outside the installed skill. From the skill root, initialize the default user configuration:

```bash
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/addx/ninedata"
mkdir -p "$config_dir"
cp config.example.json "$config_dir/config.json"
chmod 600 "$config_dir/config.json"
```

Then edit the copied file. Credentials may be stored in this external file or provided through `NINEDATA_API_KEY` and `NINEDATA_SECRET_KEY`; environment variables take precedence.

If an older installation has a real `config.json` in the skill root, move it to the path above before updating the skill. The client still accepts its existing fields, but environment variables take precedence for credentials.

Use `NINEDATA_SKILL_CONFIG=/absolute/path/config.json` when a different external location is required.

## Codex

Installation path example:

```text
~/.codex/skills/ninedata-skill/
```

Validation prompt:

```text
Use the NineData Skill to list my MySQL datasources.
```

## Claude Code

Installation path example:

```text
~/.claude/skills/ninedata-skill/
```

Validation prompt:

```text
Use ninedata-skill to list available NineData datasources.
```

## Cursor

Installation path example:

```text
~/.cursor/skills/ninedata-skill/
```

## Open Claw

Installation path example:

```text
~/.openclaw/skills/ninedata-skill/
```

## Hermes Agent

Installation path example:

```text
~/.hermes/skills/ninedata-skill/
```

## Qoder

Installation path example:

```text
~/.qoder/skills/ninedata-skill/
```

## Trae

Installation path example:

```text
~/.trae/skills/ninedata-skill/
```

## Open Code

Installation path example:

```text
~/.opencode/skills/ninedata-skill/
```

## Minimal Script Validation

```bash
scripts/validate-config.sh
scripts/list-datasource.sh --page-size 5
```
