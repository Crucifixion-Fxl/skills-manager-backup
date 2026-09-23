#!/usr/bin/env python3
"""check_argocd_application.py <directory>

Verifies every ArgoCD Application in <directory> has:
  1. metadata.finalizers contains 'resources-finalizer.argocd.argoproj.io'
  2. spec.project is set
  3. spec.syncPolicy.automated exists
  4. spec.syncPolicy.automated.selfHeal == true
  5. spec.syncPolicy.automated.prune == true, except the exact temporary
     state-machine staging-cn runtime bridge, which must remain false
  6. Opted-in ArgoCD Image Updater annotations use argocd write-back
  7. ArgoCD notifications annotations required by the deploy contract
  8. spec.source.kustomize.images contains a current recovery seed: a draft
     zero placeholder before CI exists, or a SHA-like tag for first sync / an
     existing Application
  9. Business Application names use {app}-{env-keyword}, except an active exact
     identity in the controlled legacy namespace registry may retain only its
     registered historical target suffix

Missing finalizer -> resources orphan on Application delete.
Missing automated -> manual sync forever.
Missing selfHeal/prune -> drift accumulates silently.
Partial or retired Git/MR-owned image automation -> first sync can stick on a
placeholder or image updates can stop. Applications with no Image Updater
annotations are not opted in and skip this contract.
Missing notifications -> deploy / degraded / sync-failed events do not reach
the team channel.
Ignoring ExternalSecret metadata annotations -> ArgoCD cannot honor sync-wave
changes on DB / secret resources.
Controlled target-suffix compatibility never adds a deployment route and does
not skip any other Application contract.

Exit codes: 0 pass, 1 fail, 2 dep/usage error.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Collection
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

from _namespace_legacy_exceptions import (
    KEBAB_CASE_IDENTITY_RE,
    LEGACY_EXCEPTIONS_YAML,
    ApplicationException,
    application_matches_exception,
    load_namespace_legacy_exceptions,
)
from _scan import ParseError, is_application, iter_docs

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_KEYWORDS_YAML = SCRIPT_DIR.parent / "references" / "data" / "env-keywords.yaml"


def load_env_keywords() -> set[str]:
    """Valid env-keyword suffixes from references/data/env-keywords.yaml (SSOT).

    Returns an empty set if the file cannot be read; callers then fall back to
    only requiring the {app}- prefix (cannot validate the suffix value).
    """
    try:
        data = yaml.safe_load(ENV_KEYWORDS_YAML.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    return set((data or {}).get("env_keywords") or {})


ENV_KEYWORDS = load_env_keywords()


PRUNE_DISABLED_BRIDGE_IDENTITIES = frozenset(
    {"tencent-100014919455-cn-main/state-machine-staging-cn"}
)


REQUIRED_FINALIZER = "resources-finalizer.argocd.argoproj.io"
AI_PREFIX = "argocd-image-updater.argoproj.io/"
PLATFORM_WRITEBACK_PREFIX = "image-writeback.addx.io/"
REQUIRED_IMAGE_UPDATER_GLOBAL = {
    "image-list": None,
    "write-back-method": "argocd",
}
REQUIRED_IMAGE_UPDATER_PER_ALIAS = {
    "update-strategy": "newest-build",
    "allow-tags": None,
    "kustomize.image-name": None,
    "platforms": None,
}
OPTIONAL_IMAGE_UPDATER_PER_ALIAS = {
    "force-update",
    "helm.image-name",
    "helm.image-spec",
    "helm.image-tag",
    "ignore-tags",
    "pull-secret",
}
IMAGE_UPDATER_ALIAS_SUFFIXES = tuple(
    sorted(
        set(REQUIRED_IMAGE_UPDATER_PER_ALIAS) | OPTIONAL_IMAGE_UPDATER_PER_ALIAS,
        key=len,
        reverse=True,
    )
)
REQUIRED_NOTIFICATIONS = [
    "notifications.argoproj.io/subscribe.on-deployed.feishu-ops",
    "notifications.argoproj.io/subscribe.on-health-degraded.feishu-ops",
    "notifications.argoproj.io/subscribe.on-sync-failed.feishu-ops",
]
ANNOTATION_PATH_PREFIX = "metadata.annotations"
IMAGE_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SHA_ALLOW_TAGS_RE = re.compile(
    r"^regexp:\^\[a-f0-9\]\{(?P<min>\d+)(?:,(?P<max>\d+))?\}\$$"
)
SHA_ALLOW_TAGS_LITERAL_RE = re.compile(
    r"^regexp:\^(?P<sha>[a-f0-9]{7,40})\$$"
)
BUSINESS_IMAGE_PATH_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?"
    r"/cicd/(dev|staging|pre|prod)-[a-z0-9-]+/[a-z0-9-]+$"
)
PLATFORM_IMAGE_PROJECTS = {"platform-ops-runtime"}
PLATFORM_IMAGE_PATH_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?"
    r"/cicd/[a-z0-9-]+/[a-z0-9-]+$"
)


def parse_image_ref(ref: str) -> tuple[str, str, str] | None:
    """Return (alias, image_path, tag) for alias=path:tag strings."""
    if "=" not in ref or ":" not in ref:
        return None
    alias, rest = ref.split("=", 1)
    path, tag = rest.rsplit(":", 1)
    if not alias or not path or not tag:
        return None
    return alias, path, tag


ARTIFACT_VARIANT_SUFFIXES = frozenset(
    {"active", "passive", "inactive", "dormant", "gray", "canary"}
)


def _artifact_variant_suffix(component: str, owner: str) -> bool:
    """True when component is an explicit artifact-variant path of owner.

    A variant is a different build mode of the SAME application (e.g.
    ``safepush-active``), not a business component (``my-app-server``), so
    only the declared artifact suffixes are accepted here.
    """
    if not component.startswith(f"{owner}-"):
        return False
    suffix = component[len(owner) + 1 :]
    return suffix in ARTIFACT_VARIANT_SUFFIXES


def allow_tags_min_len(value: str) -> int:
    match = re.search(r"\{(\d+),", value or "")
    if match:
        return int(match.group(1))
    match = re.search(r"\{(\d+)\}", value or "")
    if match:
        return int(match.group(1))
    return 7


def image_seed_is_allowed(tag: str, min_len: int) -> bool:
    """Allow draft zero placeholder or a SHA-like image override tag."""
    if re.fullmatch(r"0+", tag) and len(tag) >= min_len:
        return True
    if re.fullmatch(r"[a-f0-9]+", tag) and min_len <= len(tag) <= 40:
        return True
    return False


def sha_allow_tags_is_safe(value: str) -> bool:
    """Accept only regexes whose language is a safe subset of 7..40 hex chars."""
    if SHA_ALLOW_TAGS_LITERAL_RE.fullmatch(value):
        return True
    match = SHA_ALLOW_TAGS_RE.fullmatch(value)
    if match is None:
        return False
    minimum = int(match.group("min"))
    maximum = int(match.group("max") or minimum)
    return 7 <= minimum <= maximum <= 40


def iter_kustomize_images(spec: dict) -> list[str]:
    images = (((spec.get("source") or {}).get("kustomize") or {}).get("images")
              or [])
    refs: list[str] = []
    for item in images:
        if isinstance(item, str):
            refs.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            new_name = item.get("newName")
            new_tag = item.get("newTag")
            if name and new_name and new_tag:
                refs.append(f"{name}={new_name}:{new_tag}")
    return refs


def ignores_external_secret_annotations(spec: dict) -> bool:
    for item in spec.get("ignoreDifferences") or []:
        if not isinstance(item, dict):
            continue
        if item.get("group") != "external-secrets.io":
            continue
        if item.get("kind") != "ExternalSecret":
            continue
        pointers = item.get("jsonPointers") or []
        if "/metadata/annotations" in pointers:
            return True
    return False


def fail(file: Path, name: str, message: str) -> str:
    return f"FAIL: {file}: Application/{name} {message}"


def application_identity(file: Path, scan_root: Path, name: str) -> str:
    """Return the cluster/name identity for an Application under scan_root."""
    try:
        relative = file.resolve().relative_to(scan_root.resolve())
    except ValueError:
        return ""
    cluster = scan_root.name if len(relative.parts) == 1 else relative.parts[0]
    return f"{cluster}/{name}"


def validate_core_contracts(
    file: Path,
    scan_root: Path,
    name: str,
    metadata: dict,
    spec: dict,
) -> list[str]:
    failures: list[str] = []
    finalizers = metadata.get("finalizers") or []
    project = spec.get("project")
    automated = (spec.get("syncPolicy") or {}).get("automated")
    identity = application_identity(file, scan_root, name)
    expected_prune = identity not in PRUNE_DISABLED_BRIDGE_IDENTITIES

    if REQUIRED_FINALIZER not in (finalizers or []):
        failures.append(fail(file, name, f"missing finalizer [{REQUIRED_FINALIZER}]"))

    if not isinstance(project, str) or not project:
        failures.append(fail(file, name, "missing spec.project"))

    if not isinstance(automated, dict):
        failures.append(
            fail(file, name, "missing spec.syncPolicy.automated (no auto-sync)")
        )
    else:
        if automated.get("selfHeal") is not True:
            failures.append(fail(file, name, "spec.syncPolicy.automated.selfHeal != true"))

        if automated.get("prune") is not expected_prune:
            expected = str(expected_prune).lower()
            failures.append(
                fail(file, name, f"spec.syncPolicy.automated.prune != {expected}")
            )

    if ignores_external_secret_annotations(spec):
        failures.append(
            fail(
                file,
                name,
                "must not ignore ExternalSecret /metadata/annotations; "
                "sync-wave lives there",
            )
        )

    return failures


def validate_global_image_updater_annotations(
    file: Path,
    name: str,
    annotations: dict,
) -> list[str]:
    failures: list[str] = []
    for key, expected in REQUIRED_IMAGE_UPDATER_GLOBAL.items():
        full_key = f"{AI_PREFIX}{key}"
        value = annotations.get(full_key)
        if not isinstance(value, str) or not value.strip():
            failures.append(fail(file, name, f"missing metadata.annotations[{full_key}]"))
            continue
        if expected is not None and value != expected:
            failures.append(
                fail(file, name, f"metadata.annotations[{full_key}] != {expected!r}")
            )
    if f"{AI_PREFIX}git-branch" in annotations:
        failures.append(
            fail(
                file,
                name,
                f"metadata.annotations[{AI_PREFIX}git-branch] belongs to the "
                "retired Git write-back contract; remove it",
            )
        )
    return failures


def validate_per_alias_image_updater_annotations(
    file: Path,
    name: str,
    annotations: dict,
    entries: list[tuple[str, str]],
) -> list[str]:
    failures: list[str] = []
    for alias, _ in entries:
        for suffix, expected in REQUIRED_IMAGE_UPDATER_PER_ALIAS.items():
            full_key = f"{AI_PREFIX}{alias}.{suffix}"
            value = annotations.get(full_key)
            if not isinstance(value, str) or not value.strip():
                failures.append(
                    fail(file, name, f"missing metadata.annotations[{full_key}]")
                )
                continue
            if expected is not None and value != expected:
                failures.append(
                    fail(
                        file,
                        name,
                        f"metadata.annotations[{full_key}] != {expected!r}",
                    )
                )
            if suffix == "allow-tags" and not sha_allow_tags_is_safe(value):
                failures.append(
                    fail(
                        file,
                        name,
                        f"metadata.annotations[{full_key}] must be a regexp that "
                        "allows only 7..40 lowercase hex SHA characters",
                    )
                )
        force_key = f"{AI_PREFIX}{alias}.force-update"
        if force_key in annotations and annotations.get(force_key) != "true":
            failures.append(
                fail(
                    file,
                    name,
                    f"metadata.annotations[{force_key}] must be the string 'true' "
                    "when the status.summary.images exception is justified",
                )
            )
    return failures


def parse_image_updater_alias_annotation(full_key: object) -> tuple[str, str] | None:
    """Return ``(alias, suffix)`` for recognized per-alias annotations."""
    if not isinstance(full_key, str) or not full_key.startswith(AI_PREFIX):
        return None

    short_key = full_key[len(AI_PREFIX):]
    for suffix in IMAGE_UPDATER_ALIAS_SUFFIXES:
        marker = f".{suffix}"
        if short_key.endswith(marker):
            return short_key[: -len(marker)], suffix
    return None


def validate_image_updater_alias_references(
    file: Path,
    name: str,
    annotations: dict,
    entries: list[tuple[str, str]],
) -> list[str]:
    failures: list[str] = []
    image_aliases = {alias for alias, _ in entries}
    for full_key in annotations:
        parsed = parse_image_updater_alias_annotation(full_key)
        if parsed is None:
            continue
        alias, _ = parsed
        if not IMAGE_ALIAS_RE.fullmatch(alias):
            failures.append(
                fail(file, name, f"metadata.annotations[{full_key}] has invalid alias")
            )
        elif alias not in image_aliases:
            failures.append(
                fail(
                    file,
                    name,
                    f"metadata.annotations[{full_key}] references alias {alias!r} "
                    "which is absent from image-list",
                )
            )
    return failures


def validate_image_updater_annotations(
    file: Path,
    name: str,
    annotations: dict,
) -> tuple[list[str], list[tuple[str, str]]]:
    failures = validate_global_image_updater_annotations(file, name, annotations)
    image_list = annotations.get(f"{AI_PREFIX}image-list")
    entries: list[tuple[str, str]] = []
    if isinstance(image_list, str) and image_list.strip():
        entries, parse_errors = parse_image_list(image_list)
        failures.extend(
            fail(file, name, f"metadata.annotations[{AI_PREFIX}image-list] {error}")
            for error in parse_errors
        )

    failures.extend(
        validate_per_alias_image_updater_annotations(file, name, annotations, entries)
    )
    failures.extend(
        validate_image_updater_alias_references(file, name, annotations, entries)
    )
    return failures, entries


def parse_image_list(value: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse comma-separated ``<alias>=<image-path>`` Image Updater entries."""
    entries: list[tuple[str, str]] = []
    errors: list[str] = []
    aliases: set[str] = set()

    for position, raw_entry in enumerate(value.split(","), start=1):
        entry = raw_entry.strip()
        if not entry:
            errors.append(f"contains an empty entry at position {position}")
            continue
        if entry.count("=") != 1:
            errors.append(
                f"entry {position} must be '<alias>=<image-path>', got {entry!r}"
            )
            continue

        alias, image_path = entry.split("=", 1)
        if alias != alias.strip() or image_path != image_path.strip():
            errors.append(f"entry {position} must not contain whitespace around '='")
            continue
        if not alias or not IMAGE_ALIAS_RE.fullmatch(alias):
            errors.append(f"entry {position} has invalid alias {alias!r}")
            continue
        if not image_path or any(char.isspace() for char in image_path):
            errors.append(f"entry {position} has invalid image path {image_path!r}")
            continue
        if alias in aliases:
            errors.append(f"contains duplicate alias {alias!r}")
            continue

        aliases.add(alias)
        entries.append((alias, image_path))

    return entries, errors


def validate_notification_annotations(
    file: Path,
    name: str,
    annotations: dict,
) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_NOTIFICATIONS:
        annotation_path = f"{ANNOTATION_PATH_PREFIX}[{key}]"
        if key not in annotations:
            errors.append(fail(file, name, f"missing {annotation_path}"))
        elif annotations[key] != "":
            errors.append(
                fail(
                    file,
                    name,
                    f"{annotation_path} must be an empty string",
                )
            )
    return errors


def validate_retired_writeback_labels(
    file: Path,
    name: str,
    metadata: dict,
) -> list[str]:
    labels = metadata.get("labels") or {}
    retired = [
        key
        for key in labels
        if isinstance(key, str) and key.startswith(PLATFORM_WRITEBACK_PREFIX)
    ] if isinstance(labels, dict) else []
    if not retired:
        return []
    return [
        fail(
            file,
            name,
            "retired image-writeback.addx.io labels are forbidden; use "
            "argocd-image-updater.argoproj.io annotations with "
            "write-back-method=argocd",
        )
    ]


def matching_seed_refs(spec: dict, image_name: str) -> list[str]:
    refs: list[str] = []
    for ref in iter_kustomize_images(spec):
        parsed = parse_image_ref(ref)
        if parsed and parsed[0] == image_name:
            refs.append(ref)
    return refs


def validate_image_seed(
    file: Path,
    name: str,
    image_path: str,
    allow_tags: str,
    seed_ref: str,
) -> list[str]:
    parsed = parse_image_ref(seed_ref)
    if parsed is None:
        return []

    _, seed_path, seed_tag = parsed
    failures: list[str] = []
    if "$" in seed_path:
        failures.append(
            fail(
                file,
                name,
                "kustomize image newName must be a literal Harbor host; ${...} is "
                "not expanded and the pull 404s (hard-rules #25)",
            )
        )
    if seed_path != image_path:
        failures.append(fail(file, name, "override image path does not match image-list"))

    min_len = allow_tags_min_len(allow_tags)
    if not image_seed_is_allowed(seed_tag, min_len):
        failures.append(
            fail(
                file,
                name,
                f"image seed tag must be at least {min_len} zeroes for draft, "
                "or a SHA-like current tag",
            )
        )
    literal_match = SHA_ALLOW_TAGS_LITERAL_RE.fullmatch(allow_tags)
    if literal_match and seed_tag != literal_match.group("sha"):
        failures.append(
            fail(
                file,
                name,
                "image seed tag must match the exact SHA pinned by allow-tags",
            )
        )
    return failures


def validate_application_name(file: Path, name: str, image_path: str) -> list[str]:
    """Application metadata.name MUST be {app}-{env_keyword} (the per-target form,
    e.g. naturehood-api-staging-us): base app = last segment of the business image
    path; the suffix must be a real env-keyword from env-keywords.yaml (handles
    staging-cn-tke / prod-cn-restricted-admin etc.). Bare {app} is rejected."""
    if "/cicd/" not in image_path:
        return []
    base_app = image_path.rstrip("/").rsplit("/", 1)[-1]
    if not base_app:
        return []
    prefix = base_app + "-"
    suffix = name[len(prefix):] if name.startswith(prefix) else None
    if suffix is None or (ENV_KEYWORDS and suffix not in ENV_KEYWORDS):
        return [
            fail(
                file,
                name,
                f"name must be '{base_app}-<env-keyword>' (env-keyword from "
                f"env-keywords.yaml, e.g. '{base_app}-staging-us'), got {name!r}",
            )
        ]
    return []


def validate_application_target_name(
    file: Path,
    name: str,
    base_app: str | None,
    env_label: str | None,
) -> list[str]:
    """Validate dynamic/multi-image Application target naming.

    ``metadata.labels.app`` and ``metadata.labels.env`` are authoritative when
    both are present, including legacy target names not in env-keywords.yaml.
    Otherwise dynamic aliases do not reliably encode the owning app, so use the
    strongest available base/suffix check and leave unresolved ownership to review.
    """
    if base_app and env_label:
        expected = f"{base_app}-{env_label}"
        if name == expected:
            return []
        return [fail(file, name, f"name must be {expected!r} from metadata.labels")]

    if base_app:
        prefix = base_app + "-"
        suffix = name[len(prefix):] if name.startswith(prefix) else None
        if suffix is not None and (not ENV_KEYWORDS or suffix in ENV_KEYWORDS):
            return []
        return [
            fail(
                file,
                name,
                f"name must be '{base_app}-<env-keyword>' (env-keyword from "
                f"env-keywords.yaml), got {name!r}",
            )
        ]

    if not ENV_KEYWORDS or any(name.endswith(f"-{keyword}") for keyword in ENV_KEYWORDS):
        return []
    return [
        fail(
            file,
            name,
            "name must end with '-<env-keyword>' from env-keywords.yaml when "
            "a dynamic or multi-image alias does not identify one app base",
        )
    ]


def controlled_application_image_app(
    entries: list[tuple[str, str]],
    metadata: dict,
) -> str | None:
    """Resolve the image app dimension used by controlled Application identities."""
    if len(entries) == 1 and entries[0][0] == "app":
        image_path = entries[0][1]
        if "/cicd/" in image_path:
            image_app = image_path.rstrip("/").rsplit("/", 1)[-1]
            if image_app:
                return image_app

    labels = metadata.get("labels") or {}
    image_app = labels.get("app") if isinstance(labels, dict) else None
    if isinstance(image_app, str) and KEBAB_CASE_IDENTITY_RE.fullmatch(image_app):
        return image_app
    return None


def has_controlled_application_name_exception(
    file: Path,
    scan_root: Path,
    name: object,
    spec: dict,
    metadata: dict,
    entries: list[tuple[str, str]],
    controlled_applications: Collection[ApplicationException],
) -> bool:
    """Return true only for one active exact registry-managed Application identity."""
    namespace = (spec.get("destination") or {}).get("namespace")
    if not isinstance(name, str) or not isinstance(namespace, str):
        return False
    return application_matches_exception(
        file,
        scan_root,
        name,
        namespace,
        controlled_application_image_app(entries, metadata),
        controlled_applications,
        {"spec": spec},
    )


def validate_business_application_name(
    file: Path,
    scan_root: Path,
    name: str,
    spec: dict,
    metadata: dict,
    entries: list[tuple[str, str]],
    base_app: str | None,
    env_label: str | None,
    controlled_applications: Collection[ApplicationException],
) -> list[str]:
    """Prefer ordinary naming, then allow one exact controlled identity."""
    if len(entries) == 1 and entries[0][0] == "app":
        image_app = entries[0][1].rstrip("/").rsplit("/", 1)[-1]
        # A declared metadata.labels.app is authoritative. When the single
        # image path segment is that app or an explicit artifact-variant of it
        # (e.g. safepush-active for app safepush), validate the Application
        # name from the declared owner instead of the image segment, so
        # variant artifact paths do not rewrite the app identity. Business
        # component images (my-app-server) are NOT variants.
        if (
            isinstance(base_app, str)
            and base_app
            and image_app != base_app
            and _artifact_variant_suffix(image_app, base_app)
        ):
            failures = validate_application_target_name(file, name, base_app, env_label)
        else:
            failures = validate_application_name(file, name, entries[0][1])
    else:
        failures = validate_application_target_name(file, name, base_app, env_label)
    if not failures:
        return []
    if has_controlled_application_name_exception(
        file,
        scan_root,
        name,
        spec,
        metadata,
        entries,
        controlled_applications,
    ):
        return []
    return failures


def validate_image_entry_contract(
    file: Path,
    name: str,
    spec: dict,
    annotations: dict,
    alias: str,
    image_path: str,
    *,
    platform_image: bool,
) -> list[str]:
    failures: list[str] = []
    raw_image_name = annotations.get(f"{AI_PREFIX}{alias}.kustomize.image-name")
    image_name = raw_image_name if isinstance(raw_image_name, str) else ""
    raw_allow_tags = annotations.get(f"{AI_PREFIX}{alias}.allow-tags")
    allow_tags = raw_allow_tags if isinstance(raw_allow_tags, str) else ""

    if "/library/" in image_path:
        failures.append(
            fail(
                file,
                name,
                f"image-list alias {alias!r} uses library/; business images must use "
                "cicd/<env>-<region>/<app>",
            )
        )
    if "$" in image_path:
        failures.append(
            fail(
                file,
                name,
                f"image-list alias {alias!r} host must be a literal Harbor host; "
                "${...} is not expanded by Image Updater and the pull 404s "
                "(hard-rules #25)",
            )
        )
    expected_path_re = (
        PLATFORM_IMAGE_PATH_RE if platform_image else BUSINESS_IMAGE_PATH_RE
    )
    if not expected_path_re.search(image_path):
        expected_path = (
            "cicd/<platform-scope>/<app>"
            if platform_image
            else "cicd/<env>-<region>/<app>"
        )
        failures.append(
            fail(
                file,
                name,
                f"image-list alias {alias!r} must use a literal Harbor host and "
                f"{expected_path}; flat cicd/<app> is forbidden",
            )
        )

    if image_name.strip():
        seed_refs = matching_seed_refs(spec, image_name)
        if not seed_refs:
            failures.append(
                fail(
                    file,
                    name,
                    "missing spec.source.kustomize.images recovery seed for "
                    f"image-list alias {alias!r} (kustomize image name {image_name!r})",
                )
            )
        else:
            failures.extend(
                validate_image_seed(file, name, image_path, allow_tags, seed_refs[0])
            )
    return failures


def validate_image_contract(
    file: Path,
    scan_root: Path,
    name: str,
    spec: dict,
    metadata: dict,
    annotations: dict,
    entries: list[tuple[str, str]],
    controlled_applications: Collection[ApplicationException] = (),
) -> list[str]:
    failures: list[str] = []
    if "sources" in spec:
        failures.append(
            fail(
                file,
                name,
                "must not set spec.sources; Image Updater recovery is bound "
                "to the single spec.source",
            )
        )
    project = spec.get("project")
    platform_image = project in PLATFORM_IMAGE_PROJECTS
    labels = metadata.get("labels") or {}
    raw_app_label = labels.get("app") if isinstance(labels, dict) else None
    raw_env_label = labels.get("env") if isinstance(labels, dict) else None
    base_app = raw_app_label if isinstance(raw_app_label, str) and raw_app_label else None
    env_label = raw_env_label if isinstance(raw_env_label, str) and raw_env_label else None

    if platform_image:
        if not base_app or not env_label:
            failures.append(
                fail(
                    file,
                    name,
                    "platform-ops-runtime Image Updater apps require non-empty "
                    "metadata.labels.app and metadata.labels.env",
                )
            )
        else:
            failures.extend(
                validate_application_target_name(file, name, base_app, env_label)
            )
    else:
        failures.extend(
            validate_business_application_name(
                file,
                scan_root,
                name,
                spec,
                metadata,
                entries,
                base_app,
                env_label,
                controlled_applications,
            )
        )
    for alias, image_path in entries:
        failures.extend(
            validate_image_entry_contract(
                file,
                name,
                spec,
                annotations,
                alias,
                image_path,
                platform_image=platform_image,
            )
        )
    return failures


def application_failures(
    file: Path,
    scan_root: Path,
    doc: dict,
    controlled_applications: Collection[ApplicationException] = (),
) -> list[str]:
    metadata = doc.get("metadata") or {}
    spec = doc.get("spec") or {}
    raw_annotations = metadata.get("annotations") or {}
    annotations = raw_annotations if isinstance(raw_annotations, dict) else {}
    name = metadata.get("name", "<unnamed>")

    failures = validate_core_contracts(
        file,
        scan_root,
        name,
        metadata,
        spec,
    )
    failures.extend(validate_notification_annotations(file, name, annotations))
    failures.extend(validate_retired_writeback_labels(file, name, metadata))
    if any(
        isinstance(key, str) and key.startswith(AI_PREFIX)
        for key in annotations
    ):
        annotation_failures, entries = validate_image_updater_annotations(
            file,
            name,
            annotations,
        )
        failures.extend(annotation_failures)
        failures.extend(
            validate_image_contract(
                file,
                scan_root,
                name,
                spec,
                metadata,
                annotations,
                entries,
                controlled_applications,
            )
        )
    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_argocd_application.py <directory>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"FAIL: directory not found: {root}", file=sys.stderr)
        return 2

    controlled_applications, _ = load_namespace_legacy_exceptions(
        LEGACY_EXCEPTIONS_YAML
    )
    bad = 0
    total = 0

    for f, doc in iter_docs(root):
        if isinstance(doc, ParseError):
            print(f"FAIL: {f}: {doc.message}")
            bad += 1
            continue
        if not is_application(doc):
            continue
        total += 1
        failures = application_failures(f, root, doc, controlled_applications)
        for failure in failures:
            print(failure)
        bad += len(failures)

    if bad:
        print(f"FAIL: {bad} Application violation(s) across {total} Application(s)")
        return 1
    if total == 0:
        print(f"PASS: no ArgoCD Application found in {root} (nothing to check)")
        return 0
    print(f"PASS: {total} ArgoCD Application(s) checked, all conform")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
