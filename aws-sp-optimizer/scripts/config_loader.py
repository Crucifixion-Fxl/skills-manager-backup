"""Load and validate ~/.config/aws-sp-optimizer/orgs.yaml. See spec 4.1, 4.4."""

from __future__ import annotations

import os
from pathlib import Path

import jsonschema
import yaml

from scripts._common import ConfigError, OrgConfig, parse_window_end
from scripts.exclude_filter import (
    ExcludeFilter,
    TagExclusion,
    validate_account_id,
    validate_tag_key,
    validate_tag_value,
    validate_usage_type_pattern,
)

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "aws-sp-optimizer" / "orgs.yaml"


# Spec 4.1 lists the structural rules for orgs.yaml. We enforce them via
# jsonschema so the failure mode is a single structured error rather than
# a cascade of KeyErrors later in load_config.
_ORGS_YAML_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "orgs"],
    "properties": {
        "schema_version": {"const": 1},
        "orgs": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {
                "type": "object",
                "required": [
                    "profile",
                    "payer_account_id",
                    "org_id",
                    "cur_database",
                    "cur_table",
                    "athena_output",
                ],
                "properties": {
                    "description": {"type": ["string", "null"]},
                    "profile": {"type": "string", "minLength": 1},
                    "payer_account_id": {"type": "string", "pattern": r"^\d{12}$"},
                    "org_id": {"type": "string", "pattern": r"^o-[a-z0-9]{10,}$"},
                    "cur_database": {"type": "string", "minLength": 1},
                    "cur_table": {"type": "string", "minLength": 1},
                    "athena_output": {
                        "type": "string",
                        "pattern": r"^s3://.+/$",
                    },
                    "athena_workgroup": {"type": "string"},
                    "primary_region": {"type": ["string", "null"]},
                    "defaults": {
                        "type": "object",
                        "properties": {
                            "window_days": {"type": "integer", "minimum": 60, "maximum": 90},
                            "window_end": {"type": "string"},
                            "prefer": {
                                "type": "string",
                                "enum": ["freshness", "sample-size", "balanced"],
                            },
                        },
                    },
                },
            },
        },
    },
}


def _resolve_config_path() -> Path:
    override = os.environ.get("AWS_SP_OPTIMIZER_CONFIG_PATH")
    if override:
        return Path(override)
    return DEFAULT_CONFIG_PATH


def _require_list(field_name: str, raw) -> list:
    """Return raw as a list (or [] when None); raise TypeError tagged with
    field_name if it's neither. Centralises the 'list or nothing' check so the
    caller doesn't repeat the same isinstance + message per field."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"{field_name} must be a list, got {type(raw).__name__}")
    return raw


def _parse_tag_entry(entry) -> TagExclusion:
    """Parse a single {key, values} mapping into a TagExclusion, validating key
    and each value. Raises TypeError if the entry isn't a mapping; KeyError if
    'key' is missing."""
    if not isinstance(entry, dict):
        raise TypeError(f"each tag entry must be a mapping, got {type(entry).__name__}")
    k = entry["key"]
    vals_raw = _require_list(f"tag values for {k!r}", entry.get("values"))
    if not vals_raw:
        # Empty values would translate to `NOT IN ()` in Athena → syntax error
        # (or, worse, silent match-all on some engines).
        raise ValueError(
            f"tag exclusion for {k!r} has empty values list; "
            f"provide at least one value or remove the tag entry"
        )
    vals = tuple(vals_raw)
    validate_tag_key(k)
    for v in vals:
        validate_tag_value(v)
    return TagExclusion(key=k, values=vals)


def _parse_include_untagged(raw) -> bool:
    """Coerce include_untagged: missing → True; otherwise must be an exact bool
    (reject YAML strings like 'yes' that Python would treat as truthy)."""
    iu = raw if raw is not None else True
    if not isinstance(iu, bool):
        raise TypeError(
            f"include_untagged must be true/false (bool), got {type(iu).__name__}: {iu!r}"
        )
    return iu


def _parse_exclude(raw) -> ExcludeFilter:
    """Parse the `exclude:` block into an ExcludeFilter. Raises ConfigError on
    invalid types or values (must be mapping; usage_type_patterns / account_ids /
    tags must be lists; tag entries must be mappings; include_untagged must be bool).
    """
    if raw is None:
        return ExcludeFilter()
    if not isinstance(raw, dict):
        raise ConfigError(
            code="config_invalid",
            message=f"exclude block must be a mapping, got {type(raw).__name__}",
            context={"exclude_block": raw},
            llm_next_action="Fix the exclude block in orgs.yaml: it must be a YAML mapping",
            user_fix_options=["Wrap exclude entries under 'exclude:' as a mapping"],
        )
    try:
        patterns = tuple(_require_list("usage_type_patterns", raw.get("usage_type_patterns")))
        for p in patterns:
            validate_usage_type_pattern(p)
        account_ids = tuple(_require_list("account_ids", raw.get("account_ids")))
        for a in account_ids:
            validate_account_id(a)
        tag_entries = _require_list("tags", raw.get("tags"))
        tag_exclusions = tuple(_parse_tag_entry(entry) for entry in tag_entries)
        include_untagged = _parse_include_untagged(raw.get("include_untagged"))
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(
            code="config_invalid",
            message=f"invalid exclude block: {e}",
            context={"exclude_block": raw, "parse_error": str(e)},
            llm_next_action="Fix the exclude block in orgs.yaml",
            user_fix_options=[
                "Check usage_type_patterns is a list of strings matching [A-Za-z0-9._:-] (1-64 chars)",
                "Check account_ids is a list of 12-digit strings",
                "Check tags is a list of {key, values} mappings",
                "Check include_untagged is true or false (YAML bool, not string)",
            ],
        ) from e
    return ExcludeFilter(
        usage_type_patterns=patterns,
        account_ids=account_ids,
        tag_exclusions=tag_exclusions,
        include_untagged=include_untagged,
    )


def load_config(org_alias: str, cli_overrides: dict | None = None) -> OrgConfig:
    """Load the orgs.yaml entry for `org_alias` and return an OrgConfig.

    Raises ConfigError on missing file, invalid YAML, schema violation, or
    unknown alias. All error paths map to status='needs_setup' via the
    ConfigError base class.
    """
    path = _resolve_config_path()
    if not path.exists():
        raise ConfigError(
            code="config_missing",
            message=f"Config file not found at {path}",
            context={"config_path": str(path)},
            llm_next_action="READ references/first-run-setup.md",
            user_fix_options=[
                f"Create {path} by running aws-sp-optimizer-discover",
            ],
        )

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(
            code="config_invalid",
            message=f"Failed to parse {path}: {e}",
            context={"config_path": str(path), "parse_error": str(e)},
        ) from e

    if not isinstance(raw, dict):
        raise ConfigError(
            code="config_schema_invalid",
            message="orgs.yaml must be a mapping at the top level",
            context={"config_path": str(path)},
        )

    try:
        jsonschema.validate(raw, _ORGS_YAML_SCHEMA)
    except jsonschema.ValidationError as e:
        raise ConfigError(
            code="config_schema_invalid",
            message=f"orgs.yaml schema violation: {e.message}",
            context={
                "config_path": str(path),
                "path": list(e.absolute_path),
                "validator": e.validator,
            },
            llm_next_action="READ references/first-run-setup.md schema section",
            user_fix_options=[
                "Check the field listed in 'path' against the schema in spec 4.1",
            ],
        ) from e

    orgs = raw["orgs"]
    if org_alias not in orgs:
        raise ConfigError(
            code="org_alias_not_found",
            message=f"Alias {org_alias!r} not found in {path}",
            context={
                "config_path": str(path),
                "available_aliases": sorted(orgs.keys()),
            },
        )

    entry = orgs[org_alias]
    defaults = entry.get("defaults") or {}

    merged = {
        "alias": org_alias,
        "description": entry.get("description"),
        "profile": entry["profile"],
        "payer_account_id": entry["payer_account_id"],
        "org_id": entry["org_id"],
        "cur_database": entry["cur_database"],
        "cur_table": entry["cur_table"],
        "athena_output": entry["athena_output"],
        "athena_workgroup": entry.get("athena_workgroup", "primary"),
        "primary_region": entry.get("primary_region"),
        "window_days": defaults.get("window_days", 90),
        "window_end": defaults.get("window_end", "today"),
        "prefer": defaults.get("prefer", "balanced"),
        "exclude": _parse_exclude(entry.get("exclude")),
    }

    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None:
                merged[key] = value

    config = OrgConfig(**merged)

    # Early-validate window_end format so --validate-only catches bad formats
    # BEFORE validate_environment runs (spec 4.4 "Early window_end validation").
    _ = parse_window_end(config.window_end)

    return config
