#!/usr/bin/env python3
"""check_eso_pushsecret_bug.py <directory>

Two checks on every PushSecret:

  A. ESO 1.3.2 data-loss combination:
       updatePolicy=Replace + data has >= 7 entries +
       any data[*].remoteRef.property references attribute.password.
     ESO writes the secret then wipes it on next reconcile.
     Source: reusable ESO PushSecret failure pattern from prior validation.

  B. Missing explicit updatePolicy.
     Defaulting to Replace silently varies across ESO versions; workflow MUST
     set updatePolicy explicitly. The recommended value is IfNotExists for
     bootstrap credentials (RDS master password, etc.).

Exit codes: 0 pass, 1 fail, 2 dep/usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml  # noqa: F401  (kept so the dep-missing message stays consistent; _scan imports yaml)
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError, iter_files


def is_push_secret(doc) -> bool:
    return isinstance(doc, dict) and doc.get("kind") == "PushSecret"


def references_attribute_password(data_list) -> bool:
    if not isinstance(data_list, list):
        return False
    for entry in data_list:
        if not isinstance(entry, dict):
            continue
        # PushSecret data entries: each has .match.remoteRef OR .remoteRef
        for key in ("match", "remoteRef"):
            block = entry.get(key)
            if not isinstance(block, dict):
                continue
            if key == "match":
                rr = block.get("remoteRef")
            else:
                rr = block
            if not isinstance(rr, dict):
                continue
            prop = rr.get("property", "")
            if isinstance(prop, str) and "attribute.password" in prop:
                return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_eso_pushsecret_bug.py <directory>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return 2

    bad = 0
    total = 0

    for f, docs in iter_files(root):
        if isinstance(docs, ParseError):
            print(f"FAIL: {f}: {docs.message}")
            bad += 1
            continue
        for doc in docs:
            if not is_push_secret(doc):
                continue
            total += 1
            name = (doc.get("metadata") or {}).get("name", "<unnamed>")
            spec = doc.get("spec") or {}
            data = spec.get("data", [])
            data_count = len(data) if isinstance(data, list) else 0
            update_policy_present = "updatePolicy" in spec
            update_policy = spec.get("updatePolicy", "Replace")
            attr_pw = references_attribute_password(data)

            # A. Data-loss combination
            if update_policy == "Replace" and data_count >= 7 and attr_pw:
                print(
                    f"FAIL: {f}: PushSecret/{name} matches ESO 1.3.2 data-loss "
                    f"combination: updatePolicy=Replace + dataCount={data_count} "
                    f"+ attribute.password property present"
                )
                print(
                    f"      FIX: set spec.updatePolicy=IfNotExists, OR split into "
                    f"multiple PushSecrets with <=6 data entries each"
                )
                bad += 1

            # B. Missing explicit updatePolicy
            if not update_policy_present:
                print(
                    f"FAIL: {f}: PushSecret/{name} omits spec.updatePolicy "
                    f"(must set explicitly; IfNotExists recommended for bootstrap creds)"
                )
                bad += 1

    if bad:
        print(f"FAIL: {bad} PushSecret violation(s) across {total} PushSecret(s)")
        return 1
    if total == 0:
        print(f"PASS: no PushSecret found in {root} (nothing to check)")
        return 0
    print(f"PASS: {total} PushSecret(s) checked, none match the bug pattern")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
