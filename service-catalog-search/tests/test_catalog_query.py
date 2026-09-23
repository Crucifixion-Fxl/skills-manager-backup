import json
import os
import stat
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "catalog-query.sh"


def test_gitlab_fallback_keeps_tls_verification_enabled() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "CERT_NONE" not in source
    assert "check_hostname=False" not in source
    assert 'gitlab_api() { "${CURL[@]}"' not in source


def run_list_capabilities(
    tmp_path: Path, scenario: str, *, insecure: bool = False
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl.log"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["CURL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")

if any("/healthcheck" in arg for arg in args):
    print("200")
elif any(arg == "cursor=page-2" for arg in args):
    scenario = os.environ["FAKE_SCENARIO"]
    if scenario == "network-error":
        sys.exit(7)
    if scenario == "api-error":
        print(json.dumps({"error": {"message": "backend unavailable"}}))
    elif scenario == "malformed":
        print("not-json")
    else:
        next_cursor = "page-2" if scenario == "repeated-cursor" else None
        print(json.dumps({
            "items": [{
                "metadata": {"name": "beta", "tags": []},
                "spec": {"type": "openapi", "lifecycle": "production", "system": "catalog"},
                "relations": [],
            }],
            "pageInfo": {"nextCursor": next_cursor} if next_cursor else {},
        }))
else:
    print(json.dumps({
        "items": [{
            "metadata": {"name": "alpha", "tags": []},
            "spec": {"type": "openapi", "lifecycle": "production", "system": "catalog"},
            "relations": [],
        }],
        "pageInfo": {"nextCursor": "page-2"},
    }))
""",
        encoding="utf-8",
    )
    fake_curl.chmod(fake_curl.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env.update(
        {
            "CURL_LOG": str(curl_log),
            "FAKE_SCENARIO": scenario,
            "PATH": f"{fake_bin}:{env['PATH']}",
            "RHDH_BASE_URL": "https://catalog.example.test",
            "RHDH_TOKEN": "test-token",
        }
    )
    if insecure:
        env["RHDH_INSECURE"] = "1"
    else:
        env.pop("RHDH_INSECURE", None)
    return subprocess.run(
        ["bash", str(SCRIPT), "list-capabilities"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )



def query_calls(curl_log: Path) -> list[list[str]]:
    calls = [json.loads(line) for line in curl_log.read_text(encoding="utf-8").splitlines()]
    return [call for call in calls if any("/entities/by-query" in arg for arg in call)]


def test_list_capabilities_aggregates_cursor_pages(tmp_path: Path) -> None:
    result = run_list_capabilities(tmp_path, "success")

    assert result.returncode == 0, result.stderr
    assert "alpha" in result.stdout
    assert "beta" in result.stdout

    calls = query_calls(tmp_path / "curl.log")
    assert len(calls) == 2
    for call in calls:
        assert "limit=100" in call
        assert any(arg.startswith("fields=metadata.name,") for arg in call)
        assert "-k" not in call
    assert "filter=kind=API" in calls[0]
    assert "filter=kind=API" not in calls[1]
    assert "cursor=page-2" not in calls[0]
    assert "cursor=page-2" in calls[1]


def test_list_capabilities_allows_explicit_insecure_tls_for_local_poc(tmp_path: Path) -> None:
    result = run_list_capabilities(tmp_path, "success", insecure=True)

    assert result.returncode == 0, result.stderr
    calls = query_calls(tmp_path / "curl.log")
    assert calls
    assert all("-k" in call for call in calls)


def test_list_capabilities_rejects_partial_results_on_network_error(tmp_path: Path) -> None:
    result = run_list_capabilities(tmp_path, "network-error")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "request failed" in result.stderr


def test_list_capabilities_rejects_catalog_error(tmp_path: Path) -> None:
    result = run_list_capabilities(tmp_path, "api-error")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "backend unavailable" in result.stderr


def test_list_capabilities_rejects_malformed_response(tmp_path: Path) -> None:
    result = run_list_capabilities(tmp_path, "malformed")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Unrecognized" in result.stderr


def test_list_capabilities_rejects_repeated_cursor(tmp_path: Path) -> None:
    result = run_list_capabilities(tmp_path, "repeated-cursor")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "repeated pagination cursor" in result.stderr
    assert len(query_calls(tmp_path / "curl.log")) == 2
