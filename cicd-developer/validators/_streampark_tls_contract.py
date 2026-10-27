"""One human-approved source-only TLS set; never a deployment or generic waiver."""

import hashlib
from pathlib import Path


TLS_FILES = {
    "clusters/aws-390709477306-us-staging/streampark-builder/readers/server.yaml":
        "4c1bb30f989b21829e5a6f9ae5fb4df283c5e2e2fb6bb5c52617e136dde220bf",
    "clusters/aws-390709477306-us-staging/streampark-builder/readers/client.yaml":
        "f1728dd006012c454c821d980c173febcc21da7ef2684b8376a41749496d45e1",
}
TLS_REASON = (
    "the StreamPark staging TLS source exception requires the exact four-object "
    "file at its fixed path; it does not authorize registration or deployment"
)


def is_reviewed_tls_source(path: Path, root: Path) -> bool:
    """Require the fixed relative path, complete bytes and no symlink traversal.

    As with the existing pull exception, boundary review accepts an isolated
    tree only when the caller explicitly supplies --repo-context k8s and keeps
    the original repository-relative paths. Local caller trust is not Git auth.
    """
    try:
        relative = path.relative_to(root).as_posix()
        expected = TLS_FILES.get(relative)
        return bool(
            expected
            and path.is_file()
            and not path.is_symlink()
            and path.resolve() == root.resolve() / relative
            and path.stat().st_size <= 16384
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected
        )
    except (OSError, ValueError):
        return False
