#!/usr/bin/env python3
"""check_cloudfront_oac.py <directory>

Validates the supported CloudFront pattern:
  - cloudfront.aws.m.upbound.io/v1beta1 Distribution + OriginAccessControl
  - private S3 origin accessed through OAC
  - app-scoped ClusterProviderConfig
  - CloudFront bucket policy has a non-public service principal and a source condition

The validator allows SourceAccount only for first-sync bootstrap. When a
Distribution manifest already pins crossplane.io/external-name, the matching
BucketPolicy must use SourceArn.

Exit codes: 0 pass, 1 fail, 2 dep/usage error.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # noqa: F401  (kept so the dep-missing message stays consistent)
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _scan import ParseError
from _scan import iter_docs as _iter_docs


CF_API = "cloudfront.aws.m.upbound.io/v1beta1"
S3_API = "s3.aws.m.upbound.io/v1beta1"
DIST_RE = re.compile(r"^E[A-Z0-9]{8,}$")
SOURCE_ARN_RE = re.compile(r"^arn:aws:cloudfront::\d{12}:distribution/E[A-Z0-9]{8,}$")


def iter_docs(root: Path):
    # Walk/parse is shared via _scan.iter_docs; adapt its (path, doc|ParseError)
    # output to this validator's (path, dict-or-None, error-or-None) contract.
    for path, doc in _iter_docs(root):
        if isinstance(doc, ParseError):
            yield path, None, doc.message
        elif isinstance(doc, dict):
            yield path, doc, None


def ref_name(doc: dict[str, Any]) -> str:
    return (((doc.get("spec") or {}).get("providerConfigRef") or {}).get("name") or "")


def ref_kind(doc: dict[str, Any]) -> str:
    return (((doc.get("spec") or {}).get("providerConfigRef") or {}).get("kind") or "")


def metadata_name(doc: dict[str, Any]) -> str:
    return ((doc.get("metadata") or {}).get("name") or "<unnamed>")


def external_name(doc: dict[str, Any]) -> str | None:
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    value = annotations.get("crossplane.io/external-name")
    return value if isinstance(value, str) else None


def listify(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def validate_provider(path: Path, doc: dict[str, Any], bad: list[str]) -> None:
    name = metadata_name(doc)
    if ref_kind(doc) != "ClusterProviderConfig":
        bad.append(f"{path}: {doc.get('kind')}/{name} providerConfigRef.kind must be ClusterProviderConfig")
    if not ref_name(doc) or ref_name(doc) == "default":
        bad.append(f"{path}: {doc.get('kind')}/{name} providerConfigRef.name must be the app-scoped provider, not default")


def validate_oac(path: Path, doc: dict[str, Any], bad: list[str]) -> None:
    name = metadata_name(doc)
    spec = doc.get("spec") or {}
    fp = spec.get("forProvider") or {}
    validate_provider(path, doc, bad)
    if "region" in fp:
        bad.append(f"{path}: OriginAccessControl/{name} must not set forProvider.region; CloudFront CRD is global")
    if fp.get("originAccessControlOriginType") != "s3":
        bad.append(f"{path}: OriginAccessControl/{name} origin type must be s3")
    if fp.get("signingBehavior") != "always":
        bad.append(f"{path}: OriginAccessControl/{name} signingBehavior must be always")
    if fp.get("signingProtocol") != "sigv4":
        bad.append(f"{path}: OriginAccessControl/{name} signingProtocol must be sigv4")


def validate_distribution_defaults(path: Path, name: str, fp: dict[str, Any], provider: str, bad: list[str]) -> None:
    defaults = {
        "httpVersion": "http2",
        "priceClass": "PriceClass_All",
        "waitForDeployment": True,
    }
    for key, expected in defaults.items():
        if fp.get(key) != expected:
            bad.append(f"{path}: Distribution/{name} must set {key}: {expected} to avoid provider-default drift")

    tags = fp.get("tags") or {}
    expected_tags = {
        "crossplane-kind": "distribution.cloudfront.aws.m.upbound.io",
        "crossplane-name": name,
        "crossplane-providerconfig": provider,
    }
    for key, expected in expected_tags.items():
        if not isinstance(tags, dict) or tags.get(key) != expected:
            bad.append(f"{path}: Distribution/{name} tags.{key} must be {expected!r} to avoid provider-default drift")


def validate_distribution_origin(path: Path, name: str, origin: dict[str, Any], bad: list[str]) -> None:
    if origin.get("originAccessControlId") == "":
        bad.append(f"{path}: Distribution/{name} must not set originAccessControlId: \"\"")
    ref = origin.get("originAccessControlIdRef") or {}
    if not isinstance(ref, dict) or not ref.get("name"):
        bad.append(f"{path}: Distribution/{name} S3 origin must use originAccessControlIdRef.name")
    s3_origin = origin.get("s3OriginConfig")
    if not isinstance(s3_origin, dict):
        bad.append(f"{path}: Distribution/{name} OAC S3 origin must set s3OriginConfig: {{}}")
    elif s3_origin.get("originAccessIdentity"):
        bad.append(f"{path}: Distribution/{name} OAC S3 origin must not use legacy originAccessIdentity")


def validate_distribution(path: Path, doc: dict[str, Any], bad: list[str]) -> bool:
    name = metadata_name(doc)
    fp = (doc.get("spec") or {}).get("forProvider") or {}
    validate_provider(path, doc, bad)
    pinned_external_name = external_name(doc)
    if pinned_external_name == "":
        bad.append(f"{path}: Distribution/{name} external-name must be omitted or a real Distribution ID, never empty")
    if pinned_external_name and not DIST_RE.match(pinned_external_name):
        bad.append(f"{path}: Distribution/{name} external-name is not a CloudFront Distribution ID")
    if "region" in fp:
        bad.append(f"{path}: Distribution/{name} must not set forProvider.region; CloudFront CRD is global")
    if fp.get("enabled") is not True:
        bad.append(f"{path}: Distribution/{name} must set enabled: true")
    validate_distribution_defaults(path, name, fp, ref_name(doc), bad)

    for origin in listify(fp.get("origin")):
        if not isinstance(origin, dict):
            continue
        if "s3OriginConfig" not in origin:
            continue
        validate_distribution_origin(path, name, origin, bad)

    behavior = fp.get("defaultCacheBehavior") or {}
    if behavior.get("viewerProtocolPolicy") not in {"redirect-to-https", "https-only"}:
        bad.append(f"{path}: Distribution/{name} viewerProtocolPolicy must force HTTPS")
    methods = set(listify(behavior.get("allowedMethods")))
    if methods & {"POST", "PUT", "PATCH", "DELETE"}:
        bad.append(f"{path}: Distribution/{name} allowedMethods must not include mutating methods")
    if not {"GET", "HEAD"} <= methods:
        bad.append(f"{path}: Distribution/{name} allowedMethods must include GET and HEAD")

    aliases = listify(fp.get("aliases"))
    cert = fp.get("viewerCertificate") or {}
    if aliases:
        if cert.get("cloudfrontDefaultCertificate") is True:
            bad.append(f"{path}: Distribution/{name} aliases require an ACM certificate, not default cert")
        if not cert.get("acmCertificateArn"):
            bad.append(f"{path}: Distribution/{name} aliases require viewerCertificate.acmCertificateArn")
    elif cert.get("cloudfrontDefaultCertificate") is not True:
        bad.append(f"{path}: Distribution/{name} without aliases must use cloudfrontDefaultCertificate: true")

    return bool(pinned_external_name)


def parse_policy(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def validate_bucket_policy(path: Path, doc: dict[str, Any], final_required: bool, bad: list[str]) -> None:
    name = metadata_name(doc)
    spec = doc.get("spec") or {}
    fp = spec.get("forProvider") or {}
    validate_provider(path, doc, bad)
    policy = parse_policy(fp.get("policy"))
    if policy is None:
        bad.append(f"{path}: BucketPolicy/{name} policy must be valid JSON")
        return
    for statement in listify(policy.get("Statement")):
        if not isinstance(statement, dict):
            continue
        principal = statement.get("Principal")
        if principal == "*":
            bad.append(f"{path}: BucketPolicy/{name} must not use Principal '*'")
            continue
        if not (isinstance(principal, dict) and principal.get("Service") == "cloudfront.amazonaws.com"):
            continue
        actions = statement.get("Action")
        action_set = set(actions if isinstance(actions, list) else [actions])
        if action_set != {"s3:GetObject"}:
            bad.append(f"{path}: BucketPolicy/{name} CloudFront statement must only allow s3:GetObject")
        condition = statement.get("Condition") or {}
        equals = condition.get("StringEquals") if isinstance(condition, dict) else None
        if not isinstance(equals, dict):
            bad.append(f"{path}: BucketPolicy/{name} CloudFront statement needs StringEquals condition")
            continue
        source_arn = equals.get("AWS:SourceArn")
        source_account = equals.get("AWS:SourceAccount")
        if final_required:
            if not isinstance(source_arn, str) or not SOURCE_ARN_RE.match(source_arn):
                bad.append(f"{path}: BucketPolicy/{name} final policy must use AWS:SourceArn with a Distribution ARN")
        elif not source_arn and not source_account:
            bad.append(f"{path}: BucketPolicy/{name} bootstrap policy must use SourceAccount or SourceArn")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_cloudfront_oac.py <directory>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return 2

    bad: list[str] = []
    distribution_count = 0
    oac_count = 0
    bucket_policy_count = 0
    final_required = False

    docs: list[tuple[Path, dict[str, Any]]] = []
    for path, doc, error in iter_docs(root):
        if error:
            bad.append(f"{path}: {error}")
        elif doc is not None:
            docs.append((path, doc))

    for path, doc in docs:
        api = doc.get("apiVersion")
        kind = doc.get("kind")
        if api == CF_API and kind == "Distribution":
            distribution_count += 1
            final_required = validate_distribution(path, doc, bad) or final_required
        elif api == CF_API and kind == "OriginAccessControl":
            oac_count += 1
            validate_oac(path, doc, bad)

    for path, doc in docs:
        if doc.get("apiVersion") == S3_API and doc.get("kind") == "BucketPolicy":
            raw_policy = (((doc.get("spec") or {}).get("forProvider") or {}).get("policy"))
            if isinstance(raw_policy, str) and "cloudfront.amazonaws.com" in raw_policy:
                bucket_policy_count += 1
                validate_bucket_policy(path, doc, final_required, bad)

    if bad:
        for item in bad:
            print(f"FAIL: {item}")
        print(f"FAIL: {len(bad)} CloudFront violation(s)")
        return 1
    if distribution_count + oac_count + bucket_policy_count == 0:
        print(f"PASS: no CloudFront OAC resources found in {root} (nothing to check)")
        return 0
    print(
        "PASS: CloudFront OAC resources checked "
        f"({distribution_count} Distribution, {oac_count} OAC, {bucket_policy_count} bucket policy)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
