"""First-run read-only probe of AWS profiles to help build orgs.yaml.

See spec 4.2, 4.2b.
"""

from __future__ import annotations

import json
import logging
import sys

from botocore.exceptions import ClientError, NoCredentialsError

from scripts._common import (
    Organization,
    base_output,
    organizations_describe_organization,
    organizations_list_accounts,
    sts_get_caller_identity,
)

logger = logging.getLogger(__name__)


def list_profiles_from_aws_cli_config() -> list[str]:
    """Return all profile names known to the AWS CLI. Empty list on failure."""
    import configparser
    from pathlib import Path

    config_path = Path.home() / ".aws" / "config"
    credentials_path = Path.home() / ".aws" / "credentials"
    profiles: set[str] = set()

    for path in (config_path, credentials_path):
        if not path.exists():
            continue
        cp = configparser.ConfigParser()
        try:
            cp.read(path)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to read %s: %s", path, e)
            continue
        for section in cp.sections():
            # ~/.aws/config uses 'profile <name>' except for 'default'
            if section.startswith("profile "):
                profiles.add(section[len("profile ") :])
            else:
                profiles.add(section)

    return sorted(profiles)


def probe_profile(profile: str) -> dict:
    """Probe one profile: STS identity, Org membership, CUR candidates.

    Returns a dict matching DiscoveryOutput.discovered_orgs[] entry shape.
    Never raises — all failures are folded into the result dict.
    """
    try:
        caller = sts_get_caller_identity(profile)
    except (ClientError, NoCredentialsError):
        return {"profile": profile, "can_use": False, "reason": "auth_failed"}

    try:
        org = organizations_describe_organization(profile)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "AWSOrganizationsNotInUseException":
            return {
                "profile": profile,
                "account_id": caller.account_id,
                "can_use": False,
                "reason": "not_in_organization",
            }
        return {
            "profile": profile,
            "account_id": caller.account_id,
            "can_use": False,
            "reason": "org_describe_failed",
        }

    if caller.account_id != org.master_account_id:
        return {
            "profile": profile,
            "account_id": caller.account_id,
            "can_use": False,
            "reason": "not_payer",
        }

    # Full probe: count members, suggest alias.
    try:
        members = organizations_list_accounts(profile)
        member_count = len(members)
    except ClientError:
        members = []
        member_count = 0

    suggested_alias = _generate_alias_from_profile(profile, org)

    return {
        "profile": profile,
        "account_id": caller.account_id,
        "can_use": True,
        "suggested_alias": suggested_alias,
        "sanity_check_fields": {
            "payer_account_id": org.master_account_id,
            "org_id": org.id,
            "master_account_email": org.master_account_email,
        },
        "member_accounts_count": member_count,
        "cur_candidates": [],  # populated later by Glue probe in a follow-up task
        "athena": {},
        "athena_output_suggestions": [],
        "default_region_from_profile": _get_profile_region(profile),
    }


def _generate_alias_from_profile(profile: str, org: Organization) -> str:
    """Heuristic alias generator. See spec 4.2 table."""
    try:
        email_domain = org.master_account_email.split("@")[1]
        return email_domain.split(".")[0]
    except (IndexError, AttributeError):
        pass
    segments = profile.split("-")
    if len(segments) >= 2:
        return segments[1]
    return profile


def _get_profile_region(profile: str) -> str:
    import boto3

    try:
        session = boto3.Session(profile_name=profile)
        region: str = session.region_name or "us-east-1"
        return region
    except Exception:  # noqa: BLE001
        return "us-east-1"


def discover_all() -> dict:
    """Iterate over all locally-configured AWS profiles and probe each."""
    profiles = list_profiles_from_aws_cli_config()
    discovered = []
    errors = []
    for profile in profiles:
        try:
            entry = probe_profile(profile)
            if entry:
                discovered.append(entry)
        except Exception as e:  # noqa: BLE001
            errors.append(
                {
                    "profile": profile,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
            )
    return {
        "profiles_checked": len(profiles),
        "profiles_with_errors": errors,
        "discovered_orgs": discovered,
    }


def main() -> None:
    """discover_orgs.py entry point. Emits a single DiscoveryOutput JSON
    object on stdout. Per spec 4.2b.

    Only catastrophic failures (e.g. ~/.aws/config unreadable) escape
    discover_all and become script_bug.
    """
    try:
        result = discover_all()
        output = {
            **base_output(),
            "status": "discovery_result",
            "profiles_checked": result["profiles_checked"],
            "profiles_with_errors": result["profiles_with_errors"],
            "discovered_orgs": result["discovered_orgs"],
            "instruction_for_llm": (
                "Build orgs.yaml from discovered_orgs entries where can_use=True. "
                "For each, ask user to confirm alias and athena_output. Write to "
                "~/.config/aws-sp-optimizer/orgs.yaml after user confirms all fields."
            ),
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("discover_orgs uncaught exception")
        output = {
            **base_output(),
            "status": "script_bug",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback_hint": "See stderr for full traceback",
            "llm_next_action": (
                "discover_orgs.py crashed. This is a bug in the discovery probe. "
                "Collect stderr and file an issue."
            ),
        }
        print(json.dumps(output), flush=True)
        sys.exit(1)

    print(json.dumps(output), flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
