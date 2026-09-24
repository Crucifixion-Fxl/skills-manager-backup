"""S1: profiles = adapter + wrapper + account home + model + effort.

Covers the PO mapping (grok high / claude sonnet medium / codex gpt-5.6-sol medium / glm high),
multiple accounts of one harness, user overrides, validation and reverse lookup.
"""
import json
import os

import pytest

from harness_failover import profiles as P

HOME = "/home/u"


def by_id(ps):
    return {p.id: p for p in ps}


def test_default_profiles_follow_po_mapping():
    ps = by_id(P.load_profiles(HOME))
    assert (ps["grok"].model, ps["grok"].effort) == ("grok-4.6", "high")
    assert (ps["claude-buzz"].model, ps["claude-buzz"].effort) == ("sonnet", "medium")
    assert (ps["codex-buzz"].model, ps["codex-buzz"].effort) == ("gpt-5.6-sol", "medium")
    assert (ps["glm"].model, ps["glm"].effort) == ("opus[1m]", "high")


def test_default_priority_is_grok_claude_codex_glm():
    assert [p.id for p in P.load_profiles(HOME)] == ["grok", "claude-buzz", "codex-buzz", "glm"]


def test_paths_expand_against_given_home():
    ps = by_id(P.load_profiles(HOME))
    assert ps["claude-buzz"].wrapper == "/home/u/.local/bin/claude-buzz"
    assert ps["claude-buzz"].home == "/home/u/.claude-buzz"
    assert ps["codex-buzz"].home == "/home/u/.codex-buzz"


def test_env_updates_for_claude_profile():
    env = by_id(P.load_profiles(HOME))["claude-buzz"].env_updates()
    assert env == {
        "BUZZ_ACP_AGENT_COMMAND": "/home/u/.local/lib/buzz-agents/node_modules/.bin/claude-agent-acp",
        "BUZZ_ACP_AGENT_ARGS": "",
        "BUZZ_ACP_MODEL": "sonnet",
        "BUZZ_ACP_EFFORT_LEVEL": "medium",
        "HARNESS_CLAUDE_WRAPPER": "/home/u/.local/bin/claude-buzz",
        "CODEX_HOME": None,
        "CLAUDE_CODE_EXECUTABLE": "/home/u/.local/bin/claude-buzz",
        "CLAUDE_CONFIG_DIR": "/home/u/.claude-buzz",
        "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": None,
        "BUZZ_ACP_MEDIA_BUZZ_CLI": None,
        "BUZZ_ACP_MEDIA_MODE": "stock_text_only",
    }


def test_env_updates_for_grok_removes_wrapper_and_codex_home():
    env = by_id(P.load_profiles(HOME))["grok"].env_updates()
    assert env["BUZZ_ACP_AGENT_COMMAND"] == "/home/u/.config/buzz/agents/grok-buzz-acp.sh"
    assert env["BUZZ_ACP_MODEL"] == "grok-4.6" and env["BUZZ_ACP_EFFORT_LEVEL"] == "high"
    assert env["HARNESS_CLAUDE_WRAPPER"] is None and env["CODEX_HOME"] is None


def test_env_updates_for_codex_sets_codex_home_and_removes_claude_wrapper():
    env = by_id(P.load_profiles(HOME))["codex-buzz"].env_updates()
    assert env["BUZZ_ACP_AGENT_COMMAND"] == "/home/u/.local/lib/buzz-agents/node_modules/.bin/codex-acp"
    assert env["CODEX_HOME"] == "/home/u/.codex-buzz"
    assert env["HARNESS_CLAUDE_WRAPPER"] is None
    assert env["CLAUDE_CONFIG_DIR"] is None and env["CLAUDE_CODE_EXECUTABLE"] is None
    assert env["BUZZ_ACP_MODEL"] == "gpt-5.6-sol" and env["BUZZ_ACP_EFFORT_LEVEL"] == "medium"


def test_glm_is_a_claude_adapter_with_the_glm_wrapper():
    env = by_id(P.load_profiles(HOME))["glm"].env_updates()
    assert env["BUZZ_ACP_AGENT_COMMAND"].endswith("/claude-agent-acp")
    assert env["HARNESS_CLAUDE_WRAPPER"] == "/home/u/.local/bin/claude-glm"


def write_user(tmp_path, profiles):
    f = tmp_path / "profiles.json"
    f.write_text(json.dumps({"profiles": profiles}))
    return str(f)


def test_user_file_can_add_a_second_claude_account(tmp_path):
    f = write_user(tmp_path, [{"id": "claude-work", "harness": "claude", "model": "sonnet", "effort": "medium",
                               "priority": 25, "wrapper": "~/.local/bin/claude-work", "home": "~/.claude-work"}])
    ps = P.load_profiles(HOME, user_file=f)
    assert [p.id for p in ps] == ["grok", "claude-buzz", "claude-work", "codex-buzz", "glm"]
    work = by_id(ps)["claude-work"]
    assert work.wrapper == "/home/u/.local/bin/claude-work"
    assert work.env_updates()["HARNESS_CLAUDE_WRAPPER"] == "/home/u/.local/bin/claude-work"


def test_user_file_overrides_a_default_by_id(tmp_path):
    f = write_user(tmp_path, [{"id": "claude-buzz", "effort": "high"}])
    p = by_id(P.load_profiles(HOME, user_file=f))["claude-buzz"]
    assert p.effort == "high" and p.model == "sonnet"  # untouched fields survive


def test_user_file_can_disable_a_profile(tmp_path):
    f = write_user(tmp_path, [{"id": "glm", "enabled": False}])
    assert "glm" not in [p.id for p in P.load_profiles(HOME, user_file=f)]


def test_missing_user_file_is_fine(tmp_path):
    assert len(P.load_profiles(HOME, user_file=str(tmp_path / "nope.json"))) == 4


@pytest.mark.parametrize("bad", [
    {"id": "x", "harness": "claude", "model": "sonnet", "effort": "turbo", "priority": 1},
    {"id": "x", "harness": "claude", "model": "", "effort": "high", "priority": 1},
    {"id": "x", "harness": "vim", "model": "m", "effort": "high", "priority": 1},
    {"id": "", "harness": "claude", "model": "m", "effort": "high", "priority": 1},
])
def test_invalid_user_profiles_are_rejected(tmp_path, bad):
    with pytest.raises(P.ProfileError):
        P.load_profiles(HOME, user_file=write_user(tmp_path, [bad]))


def test_duplicate_ids_in_user_file_are_rejected(tmp_path):
    two = {"id": "dup", "harness": "grok", "model": "m", "effort": "high", "priority": 1}
    with pytest.raises(P.ProfileError):
        P.load_profiles(HOME, user_file=write_user(tmp_path, [two, two]))


def test_identify_from_env_vars():
    ps = P.load_profiles(HOME)
    grok = by_id(ps)["grok"].env_updates()
    assert P.identify({
        "BUZZ_ACP_AGENT_COMMAND": grok["BUZZ_ACP_AGENT_COMMAND"],
        "BUZZ_ACP_MEDIA_MODE": "stock_text_only",
    }, ps) == "grok"


def test_identify_distinguishes_claude_accounts_by_wrapper(tmp_path):
    f = write_user(tmp_path, [{"id": "claude-work", "harness": "claude", "model": "sonnet", "effort": "medium",
                               "priority": 25, "wrapper": "~/.local/bin/claude-work", "home": "~/.claude-work"}])
    ps = P.load_profiles(HOME, user_file=f)
    e = by_id(ps)["claude-work"].env_updates()
    assert P.identify({k: v for k, v in e.items() if v}, ps) == "claude-work"
    e = by_id(ps)["claude-buzz"].env_updates()
    assert P.identify({k: v for k, v in e.items() if v}, ps) == "claude-buzz"


def test_identify_codex_by_codex_home():
    ps = P.load_profiles(HOME)
    e = by_id(ps)["codex-buzz"].env_updates()
    assert P.identify({k: v for k, v in e.items() if v}, ps) == "codex-buzz"


def test_identify_unknown_returns_none():
    assert P.identify({"BUZZ_ACP_AGENT_COMMAND": "/opt/goose"}, P.load_profiles(HOME)) is None
    assert P.identify({}, P.load_profiles(HOME)) is None


def test_media_proxy_profile_updates_and_identifies_the_full_runtime_tuple(tmp_path):
    f = write_user(tmp_path, [{
        "id": "codex-buzz",
        "command": "~/.local/share/buzz-agent-setup/adapters/codex-acp",
        "media_proxy": "~/.local/share/buzz-agent-setup/acp-media-proxy/abc/codex-acp",
        "media_buzz_cli": "~/.local/opt/buzz-0.5.23/usr/bin/buzz",
    }])
    ps = P.load_profiles(HOME, user_file=f)
    p = by_id(ps)["codex-buzz"]
    env = p.env_updates()
    assert p.command == "/home/u/.local/share/buzz-agent-setup/adapters/codex-acp"
    assert env["BUZZ_ACP_AGENT_COMMAND"] == "/home/u/.local/share/buzz-agent-setup/acp-media-proxy/abc/codex-acp"
    assert env["BUZZ_ACP_MEDIA_ADAPTER_COMMAND"] == p.command
    assert env["BUZZ_ACP_MEDIA_BUZZ_CLI"] == "/home/u/.local/opt/buzz-0.5.23/usr/bin/buzz"
    assert P.identify({k: v for k, v in env.items() if v is not None}, ps) == "codex-buzz"
    assert P.identify({**env, "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": "/wrong/codex-acp"}, ps) is None
    assert P.identify({**env, "BUZZ_ACP_MEDIA_BUZZ_CLI": "/wrong/buzz"}, ps) is None
    assert P.identify({**env, "BUZZ_ACP_MEDIA_MODE": "stock_text_only"}, ps) is None


def test_direct_profile_rejects_conflicting_proxy_fields():
    ps = P.load_profiles(HOME)
    env = by_id(ps)["codex-buzz"].env_updates()
    assert P.identify({k: v for k, v in env.items() if v is not None}, ps) == "codex-buzz"
    missing_mode = {k: v for k, v in env.items() if v is not None and k != "BUZZ_ACP_MEDIA_MODE"}
    assert P.identify(missing_mode, ps) is None
    assert P.identify({**missing_mode, "BUZZ_ACP_MEDIA_MODE": ""}, ps) is None
    assert P.identify({**env, "BUZZ_ACP_MEDIA_ADAPTER_COMMAND": "/proxy/codex-acp"}, ps) is None


@pytest.mark.parametrize("override", [
    {"media_proxy": "~/proxy/codex-acp"},
    {"media_buzz_cli": "~/bin/buzz"},
    {"media_proxy": "~/proxy/claude-agent-acp", "media_buzz_cli": "~/bin/buzz"},
    {"command": "relative/codex-acp"},
])
def test_media_proxy_profile_rejects_incomplete_or_mismatched_paths(tmp_path, override):
    profile = {"id": "codex-buzz", **override}
    with pytest.raises(P.ProfileError):
        P.load_profiles(HOME, user_file=write_user(tmp_path, [profile]))


@pytest.mark.parametrize("field,value", [
    ("model", "sonnet\nBUZZ_ACP_MODEL=evil"), ("wrapper", "/w/x\ny"), ("home", "~/a\rb"), ("model", " sonnet"),
    ("model", 42), ("wrapper", ["/w/x"]), ("model", "a\x00b"),
])
def test_control_characters_and_non_strings_are_rejected_in_user_profiles(tmp_path, field, value):
    prof = {"id": "x", "harness": "claude", "model": "sonnet", "effort": "medium", "priority": 1, "wrapper": "~/w", "home": "~/h"}
    prof[field] = value
    with pytest.raises(P.ProfileError):
        P.load_profiles(HOME, user_file=write_user(tmp_path, [prof]))


def test_user_profile_file_must_be_owned_by_us_and_not_writable_by_others(tmp_path):
    f = write_user(tmp_path, [{"id": "glm", "enabled": False}])
    os.chmod(f, 0o666)
    with pytest.raises(P.ProfileError):
        P.load_profiles(HOME, user_file=f)
    os.chmod(f, 0o600)
    assert "glm" not in [p.id for p in P.load_profiles(HOME, user_file=f)]
