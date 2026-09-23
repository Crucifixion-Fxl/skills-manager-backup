"""harness-failover: detect exhausted agent harnesses and fail Buzz agents over together."""
import os

_PKG = os.path.dirname(os.path.abspath(__file__))


def asset_path(name: str) -> str:
    """assets/ lives next to the package when installed (DEST/assets) and two levels up in the repo
    (skills/harness-failover/assets)."""
    for base in (os.path.join(_PKG, "..", "assets"), os.path.join(_PKG, "..", "..", "assets")):
        path = os.path.normpath(os.path.join(base, name))
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"asset not found: {name}")
