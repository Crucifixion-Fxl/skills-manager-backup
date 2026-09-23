"""S3a: rewriting agent env files must be surgical, atomic and reversible.

Env files hold private keys: other lines stay byte-identical, files stay 0600, failures roll back.
"""
import os
import stat

import pytest

from harness_failover import envfile as E

SECRET = "BUZZ_PRIVATE_KEY=nsec1thisisafakesecretforthetestsonly"
BASE = (
    "BUZZ_RELAY_URL=wss://relay.test\n"
    f"{SECRET}\n"
    "BUZZ_ACP_AGENT_COMMAND=/old/grok-buzz-acp.sh\n"
    "BUZZ_ACP_AGENT_ARGS=''\n"
    "BUZZ_ACP_MODEL='grok-4.6'\n"
    "BUZZ_ACP_EFFORT_LEVEL=high\n"
    "GITLAB_TOKEN=glpat-faketoken\n"
)
KEYS = ["BUZZ_ACP_AGENT_COMMAND", "BUZZ_ACP_AGENT_ARGS", "BUZZ_ACP_MODEL", "BUZZ_ACP_EFFORT_LEVEL",
        "HARNESS_CLAUDE_WRAPPER", "CODEX_HOME"]
UPDATES = {"BUZZ_ACP_AGENT_COMMAND": "/new/claude-agent-acp", "BUZZ_ACP_AGENT_ARGS": "",
           "BUZZ_ACP_MODEL": "sonnet", "BUZZ_ACP_EFFORT_LEVEL": "medium",
           "HARNESS_CLAUDE_WRAPPER": "/w/claude-buzz", "CODEX_HOME": None}


def make(tmp_path, name="a.env", text=BASE, mode=0o600):
    p = tmp_path / name
    p.write_text(text)
    os.chmod(p, mode)
    return str(p)


def test_rewrite_replaces_appends_and_keeps_other_lines_byte_identical():
    out = E.rewrite_text(BASE, UPDATES)
    lines = out.splitlines()
    assert "BUZZ_RELAY_URL=wss://relay.test" in lines and SECRET in lines and "GITLAB_TOKEN=glpat-faketoken" in lines
    assert "BUZZ_ACP_AGENT_COMMAND=/new/claude-agent-acp" in lines
    assert "BUZZ_ACP_MODEL=sonnet" in lines and "BUZZ_ACP_EFFORT_LEVEL=medium" in lines
    assert "BUZZ_ACP_AGENT_ARGS=" in lines
    assert lines[-1] == "HARNESS_CLAUDE_WRAPPER=/w/claude-buzz"  # appended
    assert not any(l.startswith("CODEX_HOME") for l in lines)  # None => absent


def test_rewrite_removes_an_existing_variable_set_to_none():
    out = E.rewrite_text(BASE + "CODEX_HOME=/old\n", UPDATES)
    assert "CODEX_HOME" not in out


def test_rewrite_quotes_values_that_need_it():
    out = E.rewrite_text(BASE, {"BUZZ_ACP_MODEL": "opus[1m]"})
    assert "BUZZ_ACP_MODEL='opus[1m]'" in out.splitlines()


def test_rewrite_adds_missing_trailing_newline():
    assert E.rewrite_text("A=1", {"B": "2"}) == "A=1\nB=2\n"


def test_rewrite_only_touches_lines_that_start_with_the_key():
    text = "# BUZZ_ACP_MODEL=commented\nXBUZZ_ACP_MODEL=other\n"
    out = E.rewrite_text(text, {"BUZZ_ACP_MODEL": "sonnet"})
    assert "# BUZZ_ACP_MODEL=commented" in out and "XBUZZ_ACP_MODEL=other" in out
    assert out.endswith("BUZZ_ACP_MODEL=sonnet\n")


def test_read_vars_returns_only_requested_keys(tmp_path):
    v = E.read_vars(make(tmp_path), ["BUZZ_ACP_MODEL", "BUZZ_ACP_AGENT_ARGS"])
    assert v == {"BUZZ_ACP_MODEL": "grok-4.6", "BUZZ_ACP_AGENT_ARGS": ""}
    assert "BUZZ_PRIVATE_KEY" not in v


def test_bash_verify_reads_back_what_bash_would_see(tmp_path):
    p = make(tmp_path, text=E.rewrite_text(BASE, UPDATES))
    got = E.bash_verify(p, KEYS)
    assert got["BUZZ_ACP_MODEL"] == "sonnet" and got["HARNESS_CLAUDE_WRAPPER"] == "/w/claude-buzz"
    assert got["CODEX_HOME"] == ""


def test_bash_verify_rejects_a_file_bash_cannot_source(tmp_path):
    with pytest.raises(E.EnvError):
        E.bash_verify(make(tmp_path, text="A='unterminated\n"), KEYS)


def test_apply_updates_writes_0600_files_and_0600_backups(tmp_path):
    p = make(tmp_path)
    backups = E.apply_updates([p], UPDATES, "20260919-050000")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert len(backups) == 1 and stat.S_IMODE(os.stat(backups[0]).st_mode) == 0o600
    assert open(backups[0]).read() == BASE
    assert "BUZZ_ACP_MODEL=sonnet" in open(p).read().splitlines()
    assert SECRET in open(p).read().splitlines()


def test_apply_updates_rolls_everything_back_when_one_file_fails_verification(tmp_path):
    a, b = make(tmp_path, "a.env"), make(tmp_path, "b.env")
    calls = []

    def verify(path, keys):
        calls.append(path)
        if path != a and len(calls) > 1:  # second file fails
            raise E.EnvError("boom")
        return E.bash_verify(path, keys)

    with pytest.raises(E.EnvError):
        E.apply_updates([a, b], UPDATES, "20260919-050000", verify=verify)
    assert open(a).read() == BASE and open(b).read() == BASE
    assert stat.S_IMODE(os.stat(a).st_mode) == 0o600


def test_apply_updates_fails_when_verified_values_do_not_match(tmp_path):
    p = make(tmp_path)
    with pytest.raises(E.EnvError):
        E.apply_updates([p], UPDATES, "t", verify=lambda path, keys: {k: "WRONG" for k in keys})
    assert open(p).read() == BASE


def test_apply_updates_refuses_symlinked_env(tmp_path):
    real = make(tmp_path, "real.env")
    link = tmp_path / "link.env"
    link.symlink_to(real)
    with pytest.raises(E.EnvError):
        E.apply_updates([str(link)], UPDATES, "t")
    assert open(real).read() == BASE


def test_apply_updates_refuses_loose_permissions(tmp_path):
    p = make(tmp_path, mode=0o644)
    with pytest.raises(E.EnvError):
        E.apply_updates([p], UPDATES, "t")


# ───────── security review fixes ─────────
def test_a_failing_backup_leaves_no_temp_copy_of_the_secrets_behind(tmp_path):
    p = make(tmp_path)
    open(f"{p}.bak.t-failover", "w").write("already here")  # O_EXCL collision
    with pytest.raises(FileExistsError):
        E.apply_updates([p], UPDATES, "t")
    assert [f for f in os.listdir(tmp_path) if f.startswith(".env.new.")] == []
    assert open(p).read() == BASE


def test_rollback_keeps_going_when_one_restore_fails(tmp_path, monkeypatch):
    """Rollback walks the replaced files last-to-first. The FIRST restore (b) fails; the earlier file (a) must
    still be restored, and the error must say which file needs a manual restore."""
    a, b, c = (make(tmp_path, n) for n in ("a.env", "b.env", "c.env"))
    real_replace = os.replace
    state = {"armed": False}

    def flaky(src, dst):
        if state["armed"] and str(dst) == b:
            raise OSError("disk hiccup")
        return real_replace(src, dst)

    calls = []

    def verify(path, keys):
        calls.append(path)
        if len(calls) == 3:  # c fails verification => a and b were already replaced
            state["armed"] = True
            raise E.EnvError("third file fails")
        return E.bash_verify(path, keys)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(E.EnvError) as e:
        E.apply_updates([a, b, c], UPDATES, "t", verify=verify)
    assert "rollback incomplete" in str(e.value) and "b.env" in str(e.value)
    assert open(a).read() == BASE   # restored despite b's failure
    assert open(c).read() == BASE   # never replaced


def test_prune_backups_keeps_only_the_newest_n_per_file(tmp_path):
    p = make(tmp_path)
    for ts in ("20260101-000001", "20260101-000002", "20260101-000003", "20260101-000004"):
        open(f"{p}.bak.{ts}-failover", "w").write(ts)
    removed = E.prune_backups([p], keep=2)
    left = sorted(f for f in os.listdir(tmp_path) if ".bak." in f)
    assert removed == 2 and left == ["a.env.bak.20260101-000003-failover", "a.env.bak.20260101-000004-failover"]
    assert open(p).read() == BASE  # the live file is never touched


# ───────── reliability review fixes ─────────
def test_verification_does_not_inherit_the_callers_environment(tmp_path, monkeypatch):
    """A shell or manager that happens to export CODEX_HOME must not make every switch fail verification."""
    monkeypatch.setenv("CODEX_HOME", "/somewhere/else")
    monkeypatch.setenv("HARNESS_CLAUDE_WRAPPER", "/stale/wrapper")
    p = make(tmp_path)
    E.apply_updates([p], {**UPDATES, "HARNESS_CLAUDE_WRAPPER": None}, "t")  # both are removed by the update
    assert "CODEX_HOME" not in open(p).read() and "HARNESS_CLAUDE_WRAPPER" not in open(p).read()


def test_crlf_lines_survive_byte_for_byte_including_in_the_backup(tmp_path):
    text = "BUZZ_RELAY_URL=wss://relay.test\r\nNOTE=keep me\r\nBUZZ_ACP_MODEL='grok-4.6'\n"
    p = tmp_path / "crlf.env"
    p.write_bytes(text.encode())
    os.chmod(p, 0o600)
    (backup,) = E.apply_updates([str(p)], {"BUZZ_ACP_MODEL": "sonnet"}, "t")
    assert open(backup, "rb").read() == text.encode()
    new = p.read_bytes()
    assert b"BUZZ_RELAY_URL=wss://relay.test\r\nNOTE=keep me\r\n" in new
