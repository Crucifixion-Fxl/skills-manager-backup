"""Exercise the documented helper with a fake token and a network-free curl stub."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


SKILL_TEXT = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
SHELL_HELPER = re.search(r"(?ms)^cms_curl\(\) \{\n.*?^\}", SKILL_TEXT).group(0)
FAKE_TOKEN = "offline-fixture-token"


def invoke_helper(tmp_path, *, base_url, exit_status=0, token=FAKE_TOKEN, path="/api/paywalls",
                  options=None):
    capture = tmp_path / "request.json"
    stub = tmp_path / "curl"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CURL_STUB_CAPTURE']).write_text(json.dumps({"
        "'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
        "'exported_token': os.environ.get('TOKEN')}))\n"
        "print('stub response')\n"
        "sys.exit(int(os.environ['CURL_STUB_EXIT']))\n"
    )
    stub.chmod(0o700)
    if options is None:
        options = ["-X", "PATCH", "-H", "Content-Type: application/json",
                   "-d", '{"_status":"draft"}']
    result = subprocess.run(
        [shutil.which("bash"), "--noprofile", "--norc", "-c",
         SHELL_HELPER + '\nset -x\ncms_curl "$@"', "cms-curl-fixture",
         path, *options],
        env={"PATH": str(tmp_path), "BASE_URL": base_url, "TOKEN": token,
             "CURL_STUB_CAPTURE": str(capture), "CURL_STUB_EXIT": str(exit_status)},
        text=True, capture_output=True, timeout=5,
    )
    return result, json.loads(capture.read_text()) if capture.exists() else None


@pytest.mark.parametrize("base_url", [
    "https://marketing-cms-staging-us.addx.live",
    "https://marketing-cms-pre-us.addx.live",
])
def test_header_stays_in_stdin_and_request_options_are_preserved(tmp_path, base_url):
    result, request = invoke_helper(tmp_path, base_url=base_url)
    assert result.returncode == 0
    assert request["stdin"] == f"Authorization: Bearer {FAKE_TOKEN}\n"
    assert FAKE_TOKEN not in " ".join(request["argv"])
    assert request["exported_token"] is None
    assert FAKE_TOKEN not in result.stdout + result.stderr
    assert request["argv"] == [
        "--disable", "--globoff", "--fail-with-body", "--silent", "--show-error",
        "--header", "@-", base_url + "/api/paywalls", "-X", "PATCH", "-H",
        "Content-Type: application/json", "--data-raw", '{"_status":"draft"}',
    ]


@pytest.mark.parametrize("exit_status", [7, 22])
def test_network_and_http_failures_propagate(tmp_path, exit_status):
    result, _ = invoke_helper(tmp_path, base_url="https://marketing-cms-staging-us.addx.live",
                              exit_status=exit_status)
    assert result.returncode == exit_status
    assert result.stdout == "stub response\n"


@pytest.mark.parametrize("overrides", [
    {"base_url": "https://unapproved.example"},
    {"token": ""},
    {"path": "https://unapproved.example/api/paywalls"},
])
def test_invalid_target_or_missing_token_never_reaches_curl(tmp_path, overrides):
    options = {"base_url": "https://marketing-cms-staging-us.addx.live", **overrides}
    result, request = invoke_helper(tmp_path, **options)
    assert result.returncode == 64
    assert request is None
    assert FAKE_TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("options", [
    ["--url", "https://unapproved.invalid/api"],
    ["https://unapproved.invalid/api"],
    ["--next", "https://unapproved.invalid/api"],
    ["--config", "unread-curl-config"],
    ["--proxy", "https://unapproved.invalid"],
    ["--location", ""],
    ["--trace", "unwritten-trace"],
    ["--verbose", ""],
    ["--output", "unwritten-output"],
    ["-X", "CONNECT"],
    ["-H", "Authorization: Bearer replacement"],
    ["-d", "@unread-body.json"],
    ["-d", "@-"],
    ["-d"],
    ["-F", "file=@"],
    ["-F", "file=@-"],
    ["-F", "file=@image.jpg;headers=@unread-headers"],
    ["-F", "file=@image.jpg,another.jpg"],
    ["-F", "file=@image.jpg", "-F", "file=@another.jpg"],
    ["-F", "file=@https://unapproved.invalid/image.jpg"],
    ["-F", "file=@image.jpg\nextra"],
    ["-F", "_payload=@unread-payload.json"],
    ["-F", "unexpected=value"],
])
def test_unsafe_or_unpaired_options_are_rejected_before_curl(tmp_path, options):
    result, request = invoke_helper(tmp_path, base_url="https://marketing-cms-staging-us.addx.live",
                                    options=options)
    assert result.returncode == 64
    assert request is None
    assert FAKE_TOKEN not in result.stdout + result.stderr


def test_upload_and_inline_payload_preserve_literal_form_values(tmp_path):
    payload = '_payload={"alt":"image;headers=@not-read"}'
    result, request = invoke_helper(
        tmp_path, base_url="https://marketing-cms-staging-us.addx.live", path="/api/media",
        options=["-X", "POST", "-F", "file=@authorized image.jpg", "-F", payload],
    )
    assert result.returncode == 0
    assert request["argv"][-6:] == [
        "-X", "POST", "-F", "file=@authorized image.jpg", "--form-string", payload,
    ]
    assert request["stdin"] == f"Authorization: Bearer {FAKE_TOKEN}\n"


def test_all_documented_shell_examples_parse_without_execution():
    reference = Path(__file__).resolve().parents[1] / "references/promotion-components.md"
    for document in (SKILL_TEXT, reference.read_text()):
        for example in re.findall(r"```bash\n(.*?)\n```", document, re.S):
            result = subprocess.run([shutil.which("bash"), "-n"], input=example,
                                    text=True, capture_output=True, timeout=5)
            assert result.returncode == 0, result.stderr
