"""S4a: the buzz CLI wrapper. Tests always inject a fake CLI via BUZZ_CLI — never the real wrapper."""
import json
import os
import stat

import pytest

from harness_failover.buzzcli import BuzzCli, BuzzCliError


def fake_cli(tmp_path, stdout="[]", rc=0, stderr=""):
    rec = tmp_path / "rec"
    script = tmp_path / "fake-buzz"
    script.write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > '{rec}.args'
env | grep -c '^BUZZ_' > '{rec}.buzzvars' || true
env | grep -c '^BUZZ_PRIVATE_KEY=' > '{rec}.key' || true
printf '%s' '{stderr}' >&2
printf '%s' '{stdout}'
exit {rc}
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), rec


def read(rec, ext):
    return open(f"{rec}.{ext}").read().split()


def test_cli_path_comes_from_buzz_cli_env(tmp_path):
    path, rec = fake_cli(tmp_path)
    cli = BuzzCli(home="/nonexistent", environ={"BUZZ_CLI": path, "PATH": "/usr/bin:/bin"})
    assert cli.run(["channels", "list"]) == []
    assert read(rec, "args") == ["channels", "list"]


def test_default_path_is_the_wrapper_under_home():
    cli = BuzzCli(home="/home/u", environ={})
    assert cli.path == "/home/u/.local/bin/buzz"


def test_as_owner_strips_every_buzz_variable_so_the_wrapper_loads_the_owner_identity(tmp_path):
    path, rec = fake_cli(tmp_path)
    env = {"BUZZ_CLI": path, "PATH": "/usr/bin:/bin", "BUZZ_PRIVATE_KEY": "agentkey", "BUZZ_AUTH_TAG": "x", "BUZZ_RELAY_URL": "r"}
    BuzzCli(home=str(tmp_path), environ=env).run(["x"], as_owner=True)
    assert read(rec, "buzzvars") == ["0"]  # nothing BUZZ_* leaked, incl. BUZZ_CLI itself


def test_default_run_passes_the_callers_identity_through(tmp_path):
    path, rec = fake_cli(tmp_path)
    env = {"BUZZ_CLI": path, "PATH": "/usr/bin:/bin", "BUZZ_PRIVATE_KEY": "agentkey"}
    BuzzCli(home=str(tmp_path), environ=env).run(["x"], as_owner=False)
    assert read(rec, "key") == ["1"]


def test_auth_failure_maps_to_a_typed_error(tmp_path):
    path, _ = fake_cli(tmp_path, stdout="", rc=3, stderr=json.dumps({"error": "auth", "message": "not a member"}))
    cli = BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": path, "PATH": "/usr/bin:/bin"})
    with pytest.raises(BuzzCliError) as e:
        cli.run(["messages", "get"])
    assert e.value.code == 3 and e.value.category == "auth"


def test_non_json_output_is_an_error(tmp_path):
    path, _ = fake_cli(tmp_path, stdout="<html>")
    cli = BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": path, "PATH": "/usr/bin:/bin"})
    with pytest.raises(BuzzCliError):
        cli.run(["x"])


def test_missing_cli_is_an_error(tmp_path):
    with pytest.raises(BuzzCliError):
        BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": str(tmp_path / "nope"), "PATH": "/usr/bin"}).run(["x"])


def test_messages_get_builds_the_expected_command(tmp_path):
    path, rec = fake_cli(tmp_path)
    BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": path, "PATH": "/usr/bin:/bin"}).messages_get("CH", 1789794000, limit=50)
    assert read(rec, "args") == ["messages", "get", "--channel", "CH", "--since", "1789794000", "--limit", "50"]


def test_messages_send_replies_in_thread_and_mentions_the_agent(tmp_path):
    path, rec = fake_cli(tmp_path)
    BuzzCli(home=str(tmp_path), environ={"BUZZ_CLI": path, "PATH": "/usr/bin:/bin"}).messages_send(
        "CH", "please retry", reply_to="e" * 64, mention="a" * 64)
    args = open(f"{rec}.args").read().split("\n")
    assert args[:2] == ["messages", "send"]
    for flag, val in (("--channel", "CH"), ("--content", "please retry"), ("--reply-to", "e" * 64), ("--mention", "a" * 64)):
        assert args[args.index(flag) + 1] == val
