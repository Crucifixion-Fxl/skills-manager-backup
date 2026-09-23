#!/usr/bin/env python3
"""Generate a reviewable Labs feature key, SQL, GrowthBook, and i18n draft."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime


FEATURE_KEY_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
PE_FEATURE_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
CMS_KEY_RE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
CLIENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
MYSQL_SIGNED_INT_MAX = 2_147_483_647
UNSAFE_UNICODE_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}
MYSQL_TIMESTAMP_MIN = datetime(1970, 1, 1, 0, 0, 1)
MYSQL_TIMESTAMP_MAX = datetime(2038, 1, 19, 3, 14, 7)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print a review-only scaffold for a new AI Bird Labs feature."
    )
    parser.add_argument("--feature-key", required=True)
    parser.add_argument("--eligibility-key", required=True)
    parser.add_argument("--feature-type", choices=("free", "premium"), required=True)
    parser.add_argument(
        "--default-state",
        choices=("on", "off"),
        required=True,
        help="Default switch state for the reviewed GrowthBook environment.",
    )
    parser.add_argument("--sort-order", type=int, required=True)
    parser.add_argument(
        "--publish-time",
        required=True,
        help="Explicit UTC timestamp in YYYY-MM-DD HH:MM:SS format.",
    )
    parser.add_argument("--title-en", required=True)
    parser.add_argument("--description-en", required=True)
    parser.add_argument("--cms-content-key")
    parser.add_argument(
        "--client",
        action="append",
        default=[],
        help="Target client name; repeat for every App that supports this feature.",
    )
    return parser.parse_args()


def validate(args: argparse.Namespace) -> None:
    if not FEATURE_KEY_RE.fullmatch(args.feature_key) or len(args.feature_key) > 64:
        raise SystemExit(
            "--feature-key must be <=64 characters in lower snake_case"
        )
    if not PE_FEATURE_KEY_RE.fullmatch(args.eligibility_key):
        raise SystemExit(
            "--eligibility-key must match [A-Za-z0-9_.:-]{1,128}"
        )
    cms_key = args.cms_content_key or args.feature_key
    if not CMS_KEY_RE.fullmatch(cms_key) or len(cms_key) > 128:
        raise SystemExit("--cms-content-key must be <=128 lowercase key characters")
    if not 0 <= args.sort_order <= MYSQL_SIGNED_INT_MAX:
        raise SystemExit("--sort-order must be a non-negative signed MySQL INT")
    try:
        publish_time = datetime.strptime(args.publish_time, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise SystemExit(
            "--publish-time must use explicit UTC YYYY-MM-DD HH:MM:SS"
        ) from exc
    if not MYSQL_TIMESTAMP_MIN <= publish_time <= MYSQL_TIMESTAMP_MAX:
        raise SystemExit(
            "--publish-time must fit MySQL TIMESTAMP UTC range "
            "1970-01-01 00:00:01 through 2038-01-19 03:14:07"
        )
    validate_text("--title-en", args.title_en, max_length=200)
    validate_text("--description-en", args.description_en, max_length=2000)
    for client in args.client:
        if not CLIENT_RE.fullmatch(client):
            raise SystemExit(
                "--client must be 1-64 safe display characters without controls "
                "or Markdown delimiters"
            )


def validate_text(name: str, value: str, *, max_length: int) -> None:
    normalized = value.strip()
    if not normalized:
        raise SystemExit(f"{name} must not be blank")
    if len(normalized) > max_length:
        raise SystemExit(f"{name} must be <={max_length} characters")
    if any(
        unicodedata.category(char) in UNSAFE_UNICODE_CATEGORIES for char in value
    ):
        raise SystemExit(
            f"{name} must not contain control, format, line, or paragraph characters"
        )


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def pascal_case(feature_key: str) -> str:
    return "".join(part.capitalize() for part in feature_key.split("_"))


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def markdown_inline(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|")


def markdown_code_block(content: str, language: str) -> str:
    longest_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", content)),
        default=0,
    )
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{content}\n{fence}"


def growthbook_force_rule(
    description: str, condition: dict[str, object], value: dict[str, object]
) -> dict[str, object]:
    # GrowthBook's Feature API serializes JSON feature values and conditions as strings.
    return {
        "type": "force",
        "description": description,
        "value": compact_json(value),
        "condition": compact_json(condition),
        "savedGroups": [],
        "enabled": True,
        "coverage": 1,
        "savedGroupTargeting": [],
    }


def main() -> None:
    args = parse_args()
    validate(args)

    feature_key = args.feature_key
    cms_key = args.cms_content_key or feature_key
    policy_key = f"lab-{feature_key.replace('_', '-')}-policy"
    catalog_name_key = f"lab_feature.{feature_key}.name"
    catalog_description_key = f"lab_feature.{feature_key}.description"
    dart_prefix = f"labFeature{pascal_case(feature_key)}"
    feature_type = 0 if args.feature_type == "free" else 1

    active_default_off = {
        "version": 1,
        "lifecycle": "active",
        "runtimeEnabled": False,
        "allowsNewOptIn": True,
    }
    active_enabled = {**active_default_off, "runtimeEnabled": True}
    environment_default = {
        **active_default_off,
        "runtimeEnabled": args.default_state == "on",
    }
    disabled_user_condition = {
        "disabledLabFeatureKeys": {"$elemMatch": {"$eq": feature_key}}
    }
    enabled_user_condition = {
        "enabledLabFeatureKeys": {"$elemMatch": {"$eq": feature_key}}
    }
    environment_rules = [
        growthbook_force_rule(
            f"{feature_key}: user explicitly disabled the lab feature",
            disabled_user_condition,
            active_default_off,
        ),
        growthbook_force_rule(
            f"{feature_key}: user explicitly enabled the lab feature",
            enabled_user_condition,
            active_enabled,
        ),
        growthbook_force_rule(
            f"{feature_key}: reviewed environment default {args.default_state.upper()}",
            {},
            environment_default,
        ),
    ]

    sql = f"""INSERT INTO `lab_feature` (
  `feature_key`, `gb_feature_key`, `feature_type`, `status`,
  `crowdin_keys`, `cms_content_key`, `sort_order`, `publish_time`
) VALUES (
  {sql_string(feature_key)},
  {sql_string(args.eligibility_key)},
  {feature_type},
  1,
  JSON_OBJECT(
    'name', {sql_string(catalog_name_key)},
    'description', {sql_string(catalog_description_key)}
  ),
  {sql_string(cms_key)},
  {args.sort_order},
  {sql_string(args.publish_time)}
);"""

    print("# New Labs Feature Scaffold")
    print()
    print("> Review only. Do not execute SQL or change GrowthBook/CMS/Crowdin automatically.")
    print()
    print("## Key contract")
    print()
    print("| Item | Value |")
    print("|---|---|")
    print(f"| feature_key | `{feature_key}` |")
    print(f"| eligibility key | `{args.eligibility_key}` |")
    print(f"| policy key | `{policy_key}` |")
    print(f"| CMS content key | `{cms_key}` |")
    print(f"| feature type | `{args.feature_type}` (`{feature_type}`) |")
    print(f"| reviewed environment default | `{args.default_state.upper()}` |")
    print(f"| Crowdin name | `{catalog_name_key}` |")
    print(f"| Crowdin description | `{catalog_description_key}` |")
    print(f"| Flutter name getter | `{dart_prefix}Name` |")
    print(f"| Flutter description getter | `{dart_prefix}Description` |")
    print(
        "| target clients | "
        + (
            ", ".join(f"`{markdown_inline(client)}`" for client in args.client)
            if args.client
            else "`TBD`"
        )
        + " |"
    )
    print()
    print("## English source copy")
    print()
    print(
        markdown_code_block(
            json.dumps(
                {
                    "name": args.title_en.strip(),
                    "description": args.description_en.strip(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "json",
        )
    )
    print()
    print("## NH NineData DML")
    print()
    print("Confirm the NineData session timezone is UTC before executing this TIMESTAMP DML.")
    print()
    print("```sql")
    print(sql)
    print("```")
    print()
    print("A duplicate `feature_key` must fail. Update existing features with a")
    print("separate reviewed UPDATE task that includes before/after and rollback SQL.")
    print()
    print("## GrowthBook active policy draft")
    print()
    print(f"- Feature type: `json`; key: `{policy_key}`")
    print(f"- Feature/global fallback: `{compact_json(active_default_off)}`")
    print(f"- Reviewed environment default: `{args.default_state.upper()}`")
    print("- Ordered environment rules:")
    print()
    print("```json")
    print(json.dumps(environment_rules, ensure_ascii=False, indent=2))
    print("```")
    print()
    print("Create a separate boolean eligibility feature and define tenant/version/")
    print("membership rules only after reviewing current GrowthBook attributes.")
    if args.default_state == "on":
        print("The unconditional default ON rule enables every policy consumer not")
        print("matched by an earlier hard-deny rule; eligibility alone does not scope it.")
        print("Default ON still requires eligibility to let a user re-enable the")
        print("feature after explicitly turning it OFF.")
    print()
    print("## PE patch smoke-test bodies")
    print()
    for enabled in (True, False):
        body = {
            "user_id": 0,
            "setting_group": "lab",
            "feature_key": feature_key,
            "enabled": enabled,
        }
        state = "ON" if enabled else "OFF"
        print(f"- {state}: `{compact_json(body)}`")
    print("- Replace `user_id=0` with an approved numeric staging user ID, and set")
    print("  `X-User-ID` to the same value. Never send the placeholder request.")
    print()
    print("## Required review decisions")
    print()
    print("- Exact real feature entry and all backend/async capability boundaries")
    print("- Premium visibility: all users or current entitled members only")
    print("- Minimum client version and tenant eligibility")
    print("- Default ON audience and any policy hard-deny rules")
    print("- Per-environment default ON/OFF and the core three-rule policy order")
    print("- Per-client allowlist, source/target release, native entry, icon source, and analytics schema")
    print("- CMS SVG/iconDark/image requirements")
    print("- Staging target branches, rollout state, observability, and manual tests")


if __name__ == "__main__":
    main()
