#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Call GitLab for one mapped project using its injected Project Access Token.

This is the runtime boundary for ordinary multi-repository Agents.  The host
and token environment name come only from the owner-controlled map; neither is
accepted as a caller-supplied option.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

try:  # Script execution and test import both work without packaging.
    from gitlab_agent_project_tokens import (
        ProjectTokenMap,
        ProjectTokenMapError,
        load_mapping,
    )
    from gitlab_l4_receipt import (
        L4ReceiptError,
        OWNER_L4_HEAD_ENV,
        OWNER_L4_RECEIPT_ENV,
        OWNER_PROVISIONING_RECEIPT_ENV,
        RECEIPT_SCHEMA,
        WRITE_METHODS,
        load_verified_receipt,
    )
except ImportError:  # pragma: no cover
    from .gitlab_agent_project_tokens import (
        ProjectTokenMap,
        ProjectTokenMapError,
        load_mapping,
    )
    from .gitlab_l4_receipt import (
        L4ReceiptError,
        OWNER_L4_HEAD_ENV,
        OWNER_L4_RECEIPT_ENV,
        OWNER_PROVISIONING_RECEIPT_ENV,
        RECEIPT_SCHEMA,
        WRITE_METHODS,
        load_verified_receipt,
    )


class ProjectTokenRequestError(RuntimeError):
    pass


_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_WRITE_METHODS = WRITE_METHODS
OWNER_MAP_ENV = "BUZZ_GITLAB_PROJECT_TOKEN_MAP"
_L4_RECEIPT_SCHEMA = RECEIPT_SCHEMA


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a project token to a redirected or foreign origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _resolve_config_path(explicit: str | None) -> str:
    """Honor the owner launcher pin when it is present.

    A registered runtime pins the path in its launcher environment; a model
    cannot replace that path with a map containing a different host or
    allowlist.  There is deliberately no unpinned CLI fallback: a missing
    launcher pin is a configuration failure, not permission to choose a map.
    """
    pinned = os.environ.get(OWNER_MAP_ENV)
    if pinned:
        # Keep symlink spelling intact.  ``load_mapping`` opens with
        # O_NOFOLLOW; resolving here would silently turn an unsafe pinned path
        # into its target before that check could run.
        pinned_path = os.path.abspath(os.path.expanduser(pinned))
        if explicit and os.path.abspath(os.path.expanduser(explicit)) != pinned_path:
            raise ProjectTokenRequestError(f"{OWNER_MAP_ENV} is owner-pinned; --config does not match")
        return pinned_path
    raise ProjectTokenRequestError(f"{OWNER_MAP_ENV} owner pin is required")


def _safe_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise ProjectTokenRequestError("path must be an API-relative path beginning with '/'")
    if endpoint.startswith("//") or "://" in endpoint or "\\" in endpoint:
        raise ProjectTokenRequestError("path must not contain an absolute URL or backslash")
    parsed = urllib.parse.urlsplit(endpoint)
    # GitLab (and its reverse proxies) may normalize URL-escaped path
    # segments before routing. Check several decoding layers so `%2e%2e`,
    # `%2f`, and double-encoded variants cannot escape the selected project
    # prefix after the request is sent.
    decoded_path = parsed.path
    for _ in range(8):
        unescaped = urllib.parse.unquote(decoded_path)
        if unescaped == decoded_path:
            break
        decoded_path = unescaped
    else:
        raise ProjectTokenRequestError("path contains too many encoded layers")
    if (parsed.scheme or parsed.netloc or "\\" in decoded_path
            or any(part in {".", ".."} for part in decoded_path.split("/"))):
        raise ProjectTokenRequestError("path contains an unsafe traversal or authority")
    if decoded_path.startswith("//") or decoded_path.startswith("/api/v4") or decoded_path.startswith("/projects/"):
        raise ProjectTokenRequestError("path must be relative to the selected project")
    return endpoint


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: "<redacted>" if str(key).lower() in {"token", "access_token", "private_token", "secret"}
                else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _load_verified_l4_receipt(mapping: ProjectTokenMap, environ: Mapping[str, str]) -> None:
    try:
        load_verified_receipt(mapping, environ)
    except L4ReceiptError as exc:
        raise ProjectTokenRequestError(str(exc)) from exc


class GitLabProjectClient:
    """A fail-closed client bound to one map entry."""

    def __init__(
        self,
        mapping: ProjectTokenMap,
        *,
        project_id: int | None = None,
        project_path: str | None = None,
        environ: Mapping[str, str] | None = None,
        opener: Any | None = None,
    ) -> None:
        try:
            self.entry = mapping.select(project_id=project_id, project_path=project_path)
            values = mapping.token_values(environ)
        except ProjectTokenMapError as exc:
            raise ProjectTokenRequestError(str(exc)) from exc
        self.mapping = mapping
        self.environ = os.environ if environ is None else environ
        self.token = values[self.entry.token_env]
        self.base = f"https://{mapping.host}/api/v4/projects/{self.entry.project_id}"
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _request(self, endpoint: str, *, method: str, payload: Any | None = None) -> Any:
        endpoint = _safe_endpoint(endpoint)
        if method not in _METHODS:
            raise ProjectTokenRequestError(f"unsupported HTTP method {method}")
        if method != "GET":
            # Keep the private transport method fail-closed as well; callers
            # must not be able to bypass the public request gate by invoking it.
            self._authorize_method(method)
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"PRIVATE-TOKEN": self.token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base}{endpoint}", data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            raise ProjectTokenRequestError(f"GitLab request returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ProjectTokenRequestError("GitLab request failed") from exc
        if status < 200 or status >= 300:
            raise ProjectTokenRequestError(f"GitLab request returned HTTP {status}")
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectTokenRequestError("GitLab returned invalid JSON") from exc

    def verify_target(self) -> dict[str, Any]:
        target = self._request("/", method="GET")
        if (not isinstance(target, dict) or target.get("id") != self.entry.project_id
                or target.get("path_with_namespace") != self.entry.project_path):
            raise ProjectTokenRequestError("token identity does not match the mapped project")
        return target

    def _authorize_method(self, method: str) -> None:
        if method not in _METHODS:
            raise ProjectTokenRequestError(f"unsupported HTTP method {method}")
        if method == "GET":
            return
        if self.entry.profile != "developer":
            raise ProjectTokenRequestError("mapped profile does not permit L4 writes")
        _load_verified_l4_receipt(self.mapping, self.environ)

    def request(self, endpoint: str, *, method: str = "GET", payload: Any | None = None) -> Any:
        self._authorize_method(method)
        self.verify_target()
        return self._request(endpoint, method=method, payload=payload)


def _parse_data(value: str | None) -> Any | None:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProjectTokenRequestError("--data must be valid JSON") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="optional path that must exactly match BUZZ_GITLAB_PROJECT_TOKEN_MAP")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--project-id", type=int)
    selector.add_argument("--project-path")
    parser.add_argument("--method", default="GET", choices=sorted(_METHODS))
    parser.add_argument("--path", required=True, help="project-relative GitLab API path, e.g. /issues")
    parser.add_argument("--data", help="optional JSON request body")
    parser.add_argument("--verify-only", action="store_true", help="verify the selected token/project without a write")
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, opener: Any | None = None, environ: Mapping[str, str] | None = None) -> Any:
    mapping = load_mapping(_resolve_config_path(args.config))
    client = GitLabProjectClient(mapping, project_id=args.project_id, project_path=args.project_path,
                                  opener=opener, environ=environ)
    if args.verify_only:
        return client.verify_target()
    return client.request(args.path, method=args.method, payload=_parse_data(getattr(args, "data", None)))


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
    except (ProjectTokenMapError, ProjectTokenRequestError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(_redact(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
