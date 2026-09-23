"""Strict loader and exact matchers for controlled legacy namespace identities."""

from __future__ import annotations

import re
import sys
from collections.abc import Collection
from configparser import Error as ConfigError
from configparser import RawConfigParser
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

FLUENT_BIT_PREFIXES = ("staging-", "pre-", "prod-", "canary-", "test-")
KUSTOMIZATION_FILE = "kustomization.yaml"
LEGACY_EXCEPTIONS_YAML = (
    Path(__file__).resolve().parent.parent
    / "references"
    / "data"
    / "namespace-legacy-exceptions.yaml"
)
KEBAB_CASE_IDENTITY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TRACKING_REF_RE = re.compile(
    r"^https://gitlab\.addx\.ai/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/-/"
    r"(?:issues|merge_requests)/[1-9][0-9]*$"
)
CONTROL_FIELDS = {
    "owner",
    "tracking_ref",
    "expires_on",
    "reason",
    "exit_strategy",
    "compensating_controls",
}
APPLICATION_EXCEPTION_FIELDS = {
    "cluster_dir",
    "application_name",
    "namespace",
    "image_app",
    "exception_id",
}
APPLICATION_REPOSITORY_BINDING_FIELDS = {
    "manifest_repository",
    "source_repository",
    "source_path",
}
BOUND_APPLICATION_EXCEPTION_FIELDS = (
    APPLICATION_EXCEPTION_FIELDS | APPLICATION_REPOSITORY_BINDING_FIELDS
)
KUSTOMIZATION_EXCEPTION_FIELDS = {
    "path_suffix",
    "namespace",
    "image_app",
    "exception_id",
}
KUSTOMIZATION_REPOSITORY_BINDING_FIELDS = {"repository"}
BOUND_KUSTOMIZATION_EXCEPTION_FIELDS = (
    KUSTOMIZATION_EXCEPTION_FIELDS | KUSTOMIZATION_REPOSITORY_BINDING_FIELDS
)
REGISTRY_FIELDS = {"controls", "applications", "kustomizations"}
LegacyApplicationException = tuple[str, str, str, str]
BoundApplicationException = tuple[str, str, str, str, str, str, str]
ApplicationException = LegacyApplicationException | BoundApplicationException
LegacyKustomizationException = tuple[tuple[str, ...], str, str]
BoundKustomizationException = tuple[tuple[str, ...], str, str, str]
KustomizationException = LegacyKustomizationException | BoundKustomizationException
REGISTRY_REPOSITORY_RE = re.compile(
    r"^https://gitlab\.addx\.ai/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.git$"
)
SCP_GIT_REPOSITORY_RE = re.compile(
    r"^(?:(?P<user>[^@/:]+)@)?(?P<host>[A-Za-z0-9.-]+):(?P<path>[^?#]+)$"
)
GIT_CONFIG_SECTION_RE = re.compile(
    r'^(?P<keyword>[A-Za-z][A-Za-z0-9-]*)(?:\s+"(?P<subsection>[^"\\]*)")?$'
)
MAX_GIT_POINTER_BYTES = 4096
MAX_GIT_CONFIG_BYTES = 1_048_576
MAX_GIT_ORIGIN_URL_BYTES = 4096
MAX_REGISTRY_BYTES = 262144
MAX_REGISTRY_NODES = 10000
MAX_REGISTRY_DEPTH = 64
MAX_REGISTRY_COLLECTION_ENTRIES = 4096
MAX_REGISTRY_STRING_BYTES = 16384
MAX_REGISTRY_ERROR_BYTES = 1024
REGISTRY_ERROR_TRUNCATION_SUFFIX = "...[truncated]"
REGISTRY_STRUCTURAL_LIMIT_ERROR = "registry YAML exceeds structural limits"


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping depth."""


def construct_unique_mapping(
    loader: UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict:
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


def bounded_registry_error(message: str) -> str:
    encoded = message.encode("utf-8", errors="replace")
    output_budget = MAX_REGISTRY_ERROR_BYTES - 1
    if len(encoded) <= output_budget:
        return encoded.decode("utf-8")
    suffix = REGISTRY_ERROR_TRUNCATION_SUFFIX.encode("utf-8")
    prefix = encoded[: output_budget - len(suffix)].decode("utf-8", errors="ignore")
    return prefix + REGISTRY_ERROR_TRUNCATION_SUFFIX


def fail_registry(path: Path, problem: str) -> None:
    print(bounded_registry_error(f"FAIL: {path}: {problem}"), file=sys.stderr)
    raise SystemExit(2)


def require_exact_fields(
    path: Path,
    item: object,
    required: set[str],
    context: str,
) -> dict:
    if not isinstance(item, dict):
        fail_registry(path, f"{context} must be a mapping")
    non_string_key_count = sum(not isinstance(key, str) for key in item)
    if non_string_key_count:
        fail_registry(
            path,
            f"{context} has {non_string_key_count} non-string field name(s)",
        )
    item_fields = set(item)
    missing = sorted(required - item_fields)
    if missing:
        fail_registry(
            path,
            f"{context} missing required field(s): {', '.join(missing)}",
        )
    unexpected = sorted(item_fields - required)
    if unexpected:
        fail_registry(
            path,
            f"{context} has {len(unexpected)} unexpected field(s)",
        )
    return item


def require_non_empty_string(
    path: Path,
    item: dict,
    field: str,
    context: str,
) -> str:
    value = item[field]
    if not isinstance(value, str) or not value.strip():
        fail_registry(path, f"{context}.{field} must be a non-empty string")
    return value


def require_exact_identity_string(
    path: Path,
    item: dict,
    field: str,
    context: str,
) -> str:
    value = require_non_empty_string(path, item, field, context)
    if "*" in value:
        fail_registry(path, f"{context}.{field} must not contain a wildcard")
    return value


def registry_item_fields(
    path: Path,
    raw_item: object,
    base_fields: set[str],
    binding_fields: set[str],
    context: str,
) -> dict:
    if not isinstance(raw_item, dict):
        fail_registry(path, f"{context} must be a mapping")
    required_fields = (
        base_fields | binding_fields
        if set(raw_item) & binding_fields
        else base_fields
    )
    return require_exact_fields(path, raw_item, required_fields, context)


def require_registry_repository(
    path: Path,
    item: dict,
    field: str,
    context: str,
) -> str:
    repository = require_exact_identity_string(path, item, field, context)
    if not REGISTRY_REPOSITORY_RE.fullmatch(repository):
        fail_registry(
            path,
            f"{context}.{field} must be an exact credential-free HTTPS "
            "gitlab.addx.ai repository ending in .git",
        )
    return repository


def require_source_path(
    path: Path,
    item: dict,
    field: str,
    context: str,
) -> str:
    value = require_exact_identity_string(path, item, field, context)
    parts = value.split("/")
    if (
        value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or any(marker in value for marker in ("?", "[", "]", "{", "}"))
        or any(part in {"", ".", ".."} for part in parts)
    ):
        fail_registry(
            path,
            f"{context}.{field} must be one canonical relative Git path",
        )
    return value


def safe_repository_text(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if (
        any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(character.isspace() for character in value)
        or "\\" in value
        or "%" in value
    ):
        return None
    return value


def scp_repository_parts(value: str) -> tuple[str, str] | None:
    match = SCP_GIT_REPOSITORY_RE.fullmatch(value)
    if match is None or match.group("user") != "git":
        return None
    repository_path = match.group("path")
    if (
        repository_path.startswith("/")
        or repository_path.endswith("/")
        or "//" in repository_path
    ):
        return None
    return match.group("host").lower(), repository_path


def url_repository_parts(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
        password = parsed.password
        username = parsed.username
    except ValueError:
        return None
    if (
        parsed.scheme not in {"https", "ssh"}
        or not hostname
        or password is not None
        or (parsed.scheme == "https" and username is not None)
        or (parsed.scheme == "ssh" and username != "git")
        or port not in {None, 443 if parsed.scheme == "https" else 22}
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or parsed.path.endswith("/")
        or "//" in parsed.path
    ):
        return None
    return hostname.lower(), parsed.path[1:]


def canonical_repository(value: object) -> tuple[str, str] | None:
    repository = safe_repository_text(value)
    if repository is None:
        return None
    parts = (
        url_repository_parts(repository)
        if "://" in repository
        else scp_repository_parts(repository)
    )
    if parts is None:
        return None
    host, repository_path = parts
    if repository_path.endswith(".git"):
        repository_path = repository_path[:-4]
    path_parts = repository_path.split("/")
    if len(path_parts) < 2 or any(part in {"", ".", ".."} for part in path_parts):
        return None
    return host, "/".join(path_parts)


def same_repository(left: object, right: object) -> bool:
    left_repository = canonical_repository(left)
    return (
        left_repository is not None
        and left_repository == canonical_repository(right)
    )


def read_small_text(path: Path, max_bytes: int) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except (MemoryError, OSError):
        return None
    if len(raw) > max_bytes:
        return None
    try:
        return raw.decode("utf-8")
    except (MemoryError, UnicodeDecodeError):
        return None


def git_directories(worktree_root: Path) -> tuple[Path, Path] | None:
    """Resolve the per-worktree and common Git directories without Git execution."""
    marker = worktree_root / ".git"
    if marker.is_dir() and not marker.is_symlink():
        git_directory = marker.resolve()
    else:
        marker_text = read_small_text(marker, MAX_GIT_POINTER_BYTES)
        if marker_text is None or not marker_text.startswith("gitdir: "):
            return None
        git_path = marker_text.removeprefix("gitdir: ").strip()
        git_directory = (worktree_root / git_path).resolve()
    if not git_directory.is_dir():
        return None
    common_file = git_directory / "commondir"
    if not common_file.exists():
        return git_directory, git_directory
    common_pointer = read_small_text(
        common_file,
        MAX_GIT_POINTER_BYTES,
    )
    if common_pointer is None:
        return None
    common_path = common_pointer.strip()
    if not common_path:
        return None
    common_directory = (git_directory / common_path).resolve()
    if not common_directory.is_dir():
        return None
    return git_directory, common_directory


def git_common_directory(worktree_root: Path) -> Path | None:
    directories = git_directories(worktree_root)
    return None if directories is None else directories[1]


def section_has_option(
    parser: RawConfigParser,
    section: str,
    forbidden: set[str],
) -> bool:
    return any(option.casefold() in forbidden for option in parser.options(section))


def config_section_is_safe(
    parser: RawConfigParser,
    section: str,
    match: re.Match[str] | None,
) -> bool:
    legacy_section = section.casefold()
    has_rewrite = section_has_option(
        parser,
        section,
        {"insteadof", "pushinsteadof"},
    )
    if legacy_section.startswith("remote."):
        return False
    if legacy_section.startswith("url.") and has_rewrite:
        return False
    if match is None:
        return True
    keyword = match.group("keyword").casefold()
    if keyword in {"include", "includeif"}:
        return False
    if keyword == "url" and has_rewrite:
        return False
    return not (
        keyword == "extensions"
        and section_has_option(parser, section, {"worktreeconfig"})
    )


def exact_origin_section(match: re.Match[str] | None) -> tuple[bool, bool]:
    if match is None:
        return True, False
    if match.group("keyword").casefold() != "remote":
        return True, False
    subsection = match.group("subsection")
    if subsection is None or subsection.casefold() != "origin":
        return True, False
    return subsection == "origin", subsection == "origin"


def local_origin_sections(parser: RawConfigParser) -> list[str] | None:
    origin_sections: list[str] = []
    for section in parser.sections():
        match = GIT_CONFIG_SECTION_RE.fullmatch(section)
        if not config_section_is_safe(parser, section, match):
            return None
        safe_origin, is_origin = exact_origin_section(match)
        if not safe_origin:
            return None
        if is_origin:
            origin_sections.append(section)
    return origin_sections


def parse_config_origin(config_text: str) -> object:
    parser = RawConfigParser(interpolation=None, strict=True)
    parser.read_string(config_text)
    parser.defaults().clear()
    sections = local_origin_sections(parser)
    if sections is None or len(sections) != 1:
        return None
    return parser.get(sections[0], "url", raw=True, fallback=None)


def parse_local_origin(config_text: str) -> str | None:
    """Read one exact local origin URL from a bounded, inert Git config."""
    try:
        origin = parse_config_origin(config_text)
    except (ConfigError, MemoryError, RecursionError, ValueError):
        return None
    if not isinstance(origin, str):
        return None
    try:
        origin_size = len(origin.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if origin_size > MAX_GIT_ORIGIN_URL_BYTES:
        return None
    return origin if canonical_repository(origin) is not None else None


def repository_origin(worktree_root: Path) -> str | None:
    directories = git_directories(worktree_root)
    if directories is None:
        return None
    git_directory, common_directory = directories
    worktree_configs = {
        git_directory / "config.worktree",
        common_directory / "config.worktree",
    }
    if any(path.is_symlink() or path.exists() for path in worktree_configs):
        return None
    config_text = read_small_text(
        common_directory / "config",
        MAX_GIT_CONFIG_BYTES,
    )
    return None if config_text is None else parse_local_origin(config_text)


def containing_worktree(file: Path) -> tuple[Path, Path] | None:
    try:
        resolved_file = file.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    candidates = (
        (resolved_file, *resolved_file.parents)
        if resolved_file.is_dir()
        else resolved_file.parents
    )
    worktree_root = next(
        (
            parent
            for parent in candidates
            if (parent / ".git").is_dir() or (parent / ".git").is_file()
        ),
        None,
    )
    if worktree_root is None:
        return None
    return resolved_file, worktree_root


def registry_value_problem(value: object) -> str | None:
    if isinstance(value, (dict, list)):
        if len(value) > MAX_REGISTRY_COLLECTION_ENTRIES:
            return REGISTRY_STRUCTURAL_LIMIT_ERROR
        return None
    if not isinstance(value, str):
        return None
    try:
        string_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return "registry YAML is malformed or exceeds structural limits"
    if string_size > MAX_REGISTRY_STRING_BYTES:
        return REGISTRY_STRUCTURAL_LIMIT_ERROR
    return None


def enqueue_registry_children(
    stack: list[tuple[object, int]],
    value: object,
    depth: int,
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            stack.append((key, depth + 1))
            stack.append((item, depth + 1))
    elif isinstance(value, list):
        stack.extend((item, depth + 1) for item in value)


def registry_complexity_problem(data: object) -> str | None:
    stack = [(data, 0)]
    node_count = 0
    while stack:
        value, depth = stack.pop()
        node_count += 1
        if node_count > MAX_REGISTRY_NODES:
            return REGISTRY_STRUCTURAL_LIMIT_ERROR
        if depth > MAX_REGISTRY_DEPTH:
            return REGISTRY_STRUCTURAL_LIMIT_ERROR
        value_problem = registry_value_problem(value)
        if value_problem:
            return value_problem
        enqueue_registry_children(stack, value, depth)
    return None


def parse_unique_safe_yaml(registry_text: str) -> object:
    loader = UniqueKeySafeLoader(registry_text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def load_registry_document(path: Path) -> dict:
    registry_text = read_small_text(path, MAX_REGISTRY_BYTES)
    if registry_text is None:
        fail_registry(
            path,
            "registry must be one regular UTF-8 file within 262144 bytes",
        )
    try:
        data = parse_unique_safe_yaml(registry_text)
    except (
        yaml.YAMLError,
        RecursionError,
        MemoryError,
        TypeError,
        ValueError,
    ):
        fail_registry(
            path,
            "registry YAML is malformed or exceeds structural limits",
        )

    complexity_problem = registry_complexity_problem(data)
    if complexity_problem:
        fail_registry(path, complexity_problem)

    return require_exact_fields(path, data, REGISTRY_FIELDS, "registry")


def validate_compensating_controls(
    path: Path,
    control: dict,
    context: str,
) -> None:
    compensating_controls = control["compensating_controls"]
    if not isinstance(compensating_controls, list) or not compensating_controls:
        fail_registry(
            path,
            f"{context}.compensating_controls must be a non-empty list",
        )
    for index, compensating_control in enumerate(compensating_controls):
        if (
            not isinstance(compensating_control, str)
            or not compensating_control.strip()
        ):
            fail_registry(
                path,
                f"{context}.compensating_controls[{index}] must be a "
                "non-empty string",
            )


def validate_expiry(
    path: Path,
    control: dict,
    context: str,
    current_date: date,
) -> None:
    expires_on_raw = control["expires_on"]
    try:
        expires_on = date.fromisoformat(expires_on_raw)
    except ValueError:
        fail_registry(
            path,
            f"{context}.expires_on must be an ISO calendar date (YYYY-MM-DD)",
        )
    if expires_on.isoformat() != expires_on_raw:
        fail_registry(
            path,
            f"{context}.expires_on must use exact YYYY-MM-DD format",
        )
    if expires_on < current_date:
        fail_registry(
            path,
            f"{context} expired on {expires_on_raw}; remove or renew it "
            "through review (expires_on is the last valid UTC calendar date)",
        )


def validate_control(
    path: Path,
    index: int,
    exception_id: object,
    raw_control: object,
    current_date: date,
) -> None:
    if not isinstance(exception_id, str) or not KEBAB_CASE_IDENTITY_RE.fullmatch(
        exception_id
    ):
        fail_registry(
            path,
            "registry.controls keys must be non-empty kebab-case exception ids",
        )
    context = f"controls[{index}]"
    control = require_exact_fields(
        path,
        raw_control,
        CONTROL_FIELDS,
        context,
    )
    require_exact_identity_string(path, control, "owner", context)
    for field in ("tracking_ref", "expires_on", "reason", "exit_strategy"):
        require_non_empty_string(path, control, field, context)

    if not TRACKING_REF_RE.fullmatch(control["tracking_ref"]):
        fail_registry(
            path,
            f"{context}.tracking_ref must be an absolute HTTPS GitLab "
            "issue or merge request URL",
        )
    validate_expiry(path, control, context, current_date)
    validate_compensating_controls(path, control, context)


def load_controls(
    path: Path,
    data: dict,
    current_date: date,
) -> dict:
    controls = data["controls"]
    if not isinstance(controls, dict):
        fail_registry(path, "registry.controls must be a mapping")
    for index, (exception_id, raw_control) in enumerate(controls.items()):
        validate_control(path, index, exception_id, raw_control, current_date)
    return controls


def require_known_control(
    path: Path,
    controls: dict,
    item: dict,
    context: str,
) -> str:
    exception_id = item["exception_id"]
    if exception_id not in controls:
        fail_registry(
            path,
            f"{context}.exception_id references unknown control",
        )
    return exception_id


def require_registry_phase_prefix(
    path: Path,
    item: dict,
    context: str,
) -> None:
    if not item["namespace"].startswith(FLUENT_BIT_PREFIXES):
        fail_registry(
            path,
            f"{context}.namespace must start with a Fluent Bit business "
            f"prefix: {', '.join(FLUENT_BIT_PREFIXES)}",
        )


def load_application_exceptions(
    path: Path,
    raw_applications: object,
    controls: dict,
) -> tuple[set[ApplicationException], set[str]]:
    if not isinstance(raw_applications, list):
        fail_registry(path, "registry.applications must be a list")
    referenced_controls: set[str] = set()
    applications: set[ApplicationException] = set()
    application_bases: set[LegacyApplicationException] = set()
    for index, raw_item in enumerate(raw_applications):
        context = f"applications[{index}]"
        item = registry_item_fields(
            path,
            raw_item,
            APPLICATION_EXCEPTION_FIELDS,
            APPLICATION_REPOSITORY_BINDING_FIELDS,
            context,
        )
        values = {
            field: require_exact_identity_string(path, item, field, context)
            for field in APPLICATION_EXCEPTION_FIELDS
        }
        has_repository_binding = (
            set(item) == BOUND_APPLICATION_EXCEPTION_FIELDS
        )
        if has_repository_binding:
            manifest_repository = require_registry_repository(
                path,
                item,
                "manifest_repository",
                context,
            )
            source_repository = require_registry_repository(
                path,
                item,
                "source_repository",
                context,
            )
            source_path = require_source_path(path, item, "source_path", context)
        require_registry_phase_prefix(path, values, context)
        application_name = values["application_name"]
        image_app = values["image_app"]
        name_prefix = f"{image_app}-"
        historical_target = application_name[len(name_prefix) :]
        if (
            not application_name.startswith(name_prefix)
            or not KEBAB_CASE_IDENTITY_RE.fullmatch(historical_target)
        ):
            fail_registry(
                path,
                f"{context}.application_name must be "
                f"'<image_app>-<historical-target>' with a non-empty "
                "kebab-case target suffix",
            )
        exception_id = require_known_control(path, controls, values, context)
        referenced_controls.add(exception_id)
        base_identity: LegacyApplicationException = (
            values["cluster_dir"],
            values["application_name"],
            values["namespace"],
            values["image_app"],
        )
        if base_identity in application_bases:
            fail_registry(path, f"{context} duplicates an Application identity")
        application_bases.add(base_identity)
        identity: ApplicationException = base_identity
        if has_repository_binding:
            identity = (
                *identity,
                manifest_repository,
                source_repository,
                source_path,
            )
        applications.add(identity)
    return applications, referenced_controls


def require_complete_overlay_suffix(
    path: Path,
    item: dict,
    context: str,
) -> tuple[str, ...]:
    path_suffix = item["path_suffix"]
    if not isinstance(path_suffix, list) or not path_suffix:
        fail_registry(path, f"{context}.path_suffix must be a non-empty list")
    if any(
        not isinstance(segment, str)
        or not segment
        or segment in {".", ".."}
        or "*" in segment
        for segment in path_suffix
    ):
        fail_registry(
            path,
            f"{context}.path_suffix must contain only exact non-empty path segments",
        )
    if path_suffix[-1] != KUSTOMIZATION_FILE:
        fail_registry(
            path,
            f"{context}.path_suffix must end with {KUSTOMIZATION_FILE!r}",
        )
    if (
        len(path_suffix) < 4
        or path_suffix[0] != "k8s"
        or path_suffix[-3] != "overlays"
    ):
        fail_registry(
            path,
            f"{context}.path_suffix must identify a complete overlay as "
            "k8s/[.../]overlays/<target>/kustomization.yaml",
        )
    return tuple(path_suffix)


def load_kustomization_exceptions(
    path: Path,
    raw_kustomizations: object,
    controls: dict,
) -> tuple[set[KustomizationException], set[str]]:
    if not isinstance(raw_kustomizations, list):
        fail_registry(path, "registry.kustomizations must be a list")
    referenced_controls: set[str] = set()
    kustomizations: set[KustomizationException] = set()
    kustomization_bases: set[LegacyKustomizationException] = set()
    for index, raw_item in enumerate(raw_kustomizations):
        context = f"kustomizations[{index}]"
        item = registry_item_fields(
            path,
            raw_item,
            KUSTOMIZATION_EXCEPTION_FIELDS,
            KUSTOMIZATION_REPOSITORY_BINDING_FIELDS,
            context,
        )
        for field in ("namespace", "image_app", "exception_id"):
            require_exact_identity_string(path, item, field, context)
        require_registry_phase_prefix(path, item, context)
        path_suffix = require_complete_overlay_suffix(path, item, context)
        exception_id = require_known_control(path, controls, item, context)
        referenced_controls.add(exception_id)
        base_identity: LegacyKustomizationException = (
            path_suffix,
            item["namespace"],
            item["image_app"],
        )
        if base_identity in kustomization_bases:
            fail_registry(path, f"{context} duplicates a Kustomization identity")
        kustomization_bases.add(base_identity)
        identity: KustomizationException = base_identity
        if set(item) == BOUND_KUSTOMIZATION_EXCEPTION_FIELDS:
            identity = (
                *identity,
                require_registry_repository(
                    path,
                    item,
                    "repository",
                    context,
                ),
            )
        kustomizations.add(identity)
    return kustomizations, referenced_controls


def load_namespace_legacy_exceptions(
    path: Path = LEGACY_EXCEPTIONS_YAML,
    *,
    today: date | None = None,
) -> tuple[set[ApplicationException], set[KustomizationException]]:
    """Load active exact identities or terminate with registry status 2."""
    data = load_registry_document(path)
    current_date = today or datetime.now(timezone.utc).date()
    controls = load_controls(path, data, current_date)
    applications, application_controls = load_application_exceptions(
        path,
        data["applications"],
        controls,
    )
    kustomizations, kustomization_controls = load_kustomization_exceptions(
        path,
        data["kustomizations"],
        controls,
    )

    referenced_controls = application_controls | kustomization_controls
    unused_controls = sorted(set(controls) - referenced_controls)
    if unused_controls:
        fail_registry(
            path,
            f"registry has {len(unused_controls)} unused control(s)",
        )

    return applications, kustomizations


def application_matches_exception(
    file: Path,
    scan_root: Path,
    application_name: str,
    namespace: str,
    image_app: str | None,
    exceptions: Collection[ApplicationException],
    application: object | None = None,
) -> bool:
    """Match a legacy identity, or every repository-bound Application dimension."""
    if not image_app:
        return False
    legacy_identity = (
        file.parent.name,
        application_name,
        namespace,
        image_app,
    )
    if legacy_identity in exceptions:
        return True
    if not isinstance(application, dict):
        return False
    spec = application.get("spec")
    if not isinstance(spec, dict) or "sources" in spec:
        return False
    source = spec.get("source")
    if not isinstance(source, dict):
        return False
    worktree = containing_worktree(file)
    scan_worktree = containing_worktree(scan_root)
    if worktree is None or scan_worktree is None:
        return False
    resolved_file, worktree_root = worktree
    if worktree_root != scan_worktree[1]:
        return False
    try:
        relative_parts = resolved_file.relative_to(worktree_root).parts
    except ValueError:
        return False
    manifest_origin = repository_origin(worktree_root)
    base_identity = (
        relative_parts[0] if len(relative_parts) == 2 else "",
        application_name,
        namespace,
        image_app,
    )
    if relative_parts != (base_identity[0], f"{application_name}.yaml"):
        return False
    for exception in exceptions:
        if len(exception) != 7 or exception[:4] != base_identity:
            continue
        if not same_repository(manifest_origin, exception[4]):
            continue
        if not same_repository(source.get("repoURL"), exception[5]):
            continue
        if source.get("path") == exception[6]:
            return True
    return False


def kustomization_matches_exception(
    file: Path,
    scan_root: Path,
    namespace: str,
    image_app: str | None,
    exceptions: Collection[KustomizationException],
) -> bool:
    """Match exact overlay identity and, when declared, its repository origin."""
    if not image_app:
        return False
    try:
        resolved_file = file.resolve(strict=True)
        resolved_scan_root = scan_root.resolve(strict=True)
        resolved_file.relative_to(resolved_scan_root)
    except (OSError, RuntimeError, ValueError):
        return False

    worktree = containing_worktree(resolved_file)
    scan_worktree = containing_worktree(resolved_scan_root)
    if worktree is None or scan_worktree is None:
        return False
    _, worktree_root = worktree
    if worktree_root != scan_worktree[1]:
        return False
    relative_parts = resolved_file.relative_to(worktree_root).parts
    legacy_identity = (relative_parts, namespace, image_app)
    if legacy_identity in exceptions:
        return True
    origin = repository_origin(worktree_root)
    return any(
        len(exception) == 4
        and exception[:3] == legacy_identity
        and same_repository(origin, exception[3])
        for exception in exceptions
    )
