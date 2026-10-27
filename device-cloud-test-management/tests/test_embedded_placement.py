"""Offline coverage for Skill-only, pre-creation embedded placement."""

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "device_cloud.py"
SPEC = importlib.util.spec_from_file_location("embedded_placement_cli", SCRIPT)
CLI = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLI
SPEC.loader.exec_module(CLI)

REQUIREMENTS = [
    {"name": "phone", "type": "PHONE", "conditions": {"platform": "$eq:android"}},
    {"name": "camera", "type": "BATTERYCAM", "conditions": {"device_model": "$eq:KF451"}},
]


def resource(kind, cabinet, *, host="H", model=None, available=True):
    return {"type": kind, "belongToCabinet": cabinet, "belongToHost": host,
            "platform": "android", "deviceModel": model, "available": available}


def pool(camera_cabinet="A"):
    return [resource("PHONE", "A"), resource("BATTERYCAM", camera_cabinet, model="KF451")]


def affinity(key):
    return {"type": "AFFINITY", "topologyKey": key, "resources": ["phone", "camera"]}


def args_for(tmp_path, monkeypatch, *, kind="embedded", requirements=None, constraints=()):
    requirements = copy.deepcopy(REQUIREMENTS if requirements is None else requirements)
    feature = tmp_path / "features" / "placement.feature"
    feature.parent.mkdir(exist_ok=True)
    rows = "\n".join(f"      | {item['name']} | {item['type']} |" for item in requirements)
    feature.write_text(f"""Feature: placement
  Background:
    Given 我有resources配置
{rows}
  @uid=placement-case
  Scenario: placement
    Then inspect resources
""", encoding="utf-8")
    monkeypatch.setattr(CLI, "_tests_snapshot", lambda *_: {
        "requestedRef": "main", "resolvedCommit": "a" * 40, "catalogDigest": "b" * 64,
    })
    command = ["run", "--automation-type", kind, "--uid", "placement-case",
               "--cases-root", str(tmp_path), "--tests-repo", "https://example.invalid/cases.git",
               "--plan-name", "offline-placement", "--resource-json", json.dumps(requirements),
               "--resource-constraints", json.dumps(list(constraints)), "--dry-run"]
    if kind == "embedded":
        command += ["--firmware-version", "1.21.40-d"]
    return CLI.parser().parse_args(command)


def build(args, resources):
    return CLI.build_trigger_command(args, resources)[1]


def placement(context):
    result = context["job"][0]["placement"]
    assert result == context["preview"]["resourceGroups"][0]["placement"]
    assert result["policy"] == "same_cabinet_first"
    return result["mode"]


def test_same_cabinet_complete_group_beats_first_cross_cabinet_pair(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch)
    resources = [*pool("B"), resource("PHONE", "B")]
    context = build(args, resources)
    assert placement(context) == "same_cabinet"
    assert context["job"][0]["constraints"] == [affinity("belong_to_cabinet") | {
        "resources": ["camera", "phone"],
    }]


def test_preference_covers_every_alias_not_just_phone_camera(tmp_path, monkeypatch):
    requirements = [*REQUIREMENTS, {"name": "router", "type": "NETWORK_SLOT", "conditions": {}}]
    args = args_for(tmp_path, monkeypatch, requirements=requirements)
    resources = [*pool(), resource("NETWORK_SLOT", "B")]
    assert placement(build(args, resources)) == "cross_cabinet_allowed"
    context = build(args, [*resources, resource("NETWORK_SLOT", "A")])
    assert placement(context) == "same_cabinet"
    assert context["job"][0]["constraints"][0]["resources"] == ["camera", "phone", "router"]


def test_cross_cabinet_keeps_existing_hard_host_constraint(tmp_path, monkeypatch):
    hard = affinity("belong_to_host")
    args = args_for(tmp_path, monkeypatch, constraints=[hard])
    context = build(args, pool("B"))
    assert placement(context) == "cross_cabinet_allowed"
    assert context["job"][0]["constraints"] == [hard | {"resources": ["camera", "phone"]}]


def test_fallback_must_not_drop_hard_host_constraint(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, constraints=[affinity("belong_to_host")])
    resources = pool("B")
    resources[1]["belongToHost"] = "other-host"
    with pytest.raises(ValueError, match="实时空闲资源"):
        build(args, resources)


def test_cross_cabinet_fallback_backtracks_over_overlapping_requirements(tmp_path, monkeypatch):
    requirements = [
        {"name": "first", "type": "PHONE", "conditions": {"platform": "$eq:android"}},
        {"name": "second", "type": "PHONE", "conditions": {"device_model": "$eq:specific"}},
    ]
    args = args_for(tmp_path, monkeypatch, requirements=requirements)
    resources = [resource("PHONE", "A", model="specific"), resource("PHONE", "B", model="other")]
    assert placement(build(args, resources)) == "cross_cabinet_allowed"


def test_hard_same_cabinet_constraint_never_degrades(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, constraints=[affinity("belong_to_cabinet")])
    with pytest.raises(ValueError, match="实时空闲资源"):
        build(args, pool("B"))


@pytest.mark.parametrize("cabinet", [None, "", True])
def test_missing_topology_cannot_be_treated_as_no_same_cabinet_group(tmp_path, monkeypatch, cabinet):
    args = args_for(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="机柜拓扑"):
        build(args, pool(cabinet))


@pytest.mark.parametrize("resources", [[], [resource("PHONE", "A")],
                                       [*pool()[:1], resource("BATTERYCAM", "A", model="KF451", available=False)]])
def test_insufficient_resources_rejected_before_creation(tmp_path, monkeypatch, resources):
    args = args_for(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="实时空闲资源"):
        build(args, resources)


def test_no_live_snapshot_rejected(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="实时空闲资源快照"):
        build(args, None)


def test_multiple_resources_per_alias_not_silently_downgraded(tmp_path, monkeypatch):
    requirements = copy.deepcopy(REQUIREMENTS)
    requirements[0]["count"] = 2
    args = args_for(tmp_path, monkeypatch, requirements=requirements)
    with pytest.raises(ValueError, match="count=1"):
        build(args, pool())


@pytest.mark.parametrize("kind", ["app", "hardware"])
def test_other_automation_types_keep_existing_behavior(tmp_path, monkeypatch, kind):
    args = args_for(tmp_path, monkeypatch, kind=kind)
    context = build(args, pool())
    assert "placement" not in context["job"][0]
    assert "constraints" not in context["job"][0]


def test_single_resource_does_not_need_cabinet_preference(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch, requirements=REQUIREMENTS[1:])
    context = build(args, [resource("BATTERYCAM", None, model="KF451")])
    assert "placement" not in context["job"][0]


def test_unrelated_resource_and_enumeration_order_do_not_change_confirmation(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch)
    before = build(args, pool())
    after = build(args, [resource("BATTERYCAM", None, model="unrelated"), *reversed(pool())])
    assert before["preview"]["confirmation"]["token"] == after["preview"]["confirmation"]["token"]


def test_selected_same_cabinet_constraint_reaches_managed_batch(tmp_path, monkeypatch):
    from trigger_cloud_job import load_batch_jobs

    args = args_for(tmp_path, monkeypatch)
    initial = build(args, pool())
    args.confirm = initial["preview"]["confirmation"]["token"]
    args.dry_run = False
    monkeypatch.setattr(CLI, "_authenticate_and_fetch_available_resources",
                        lambda _env: (pool(), object(), "tester@example.com"))
    calls = []

    def inspect_batch(trigger_args, _auth, _launched_by):
        jobs = load_batch_jobs(trigger_args.batch)
        assert jobs[0]["constraints"] == initial["job"][0]["constraints"]
        assert jobs[0]["placement"]["mode"] == "same_cabinet"
        calls.append(jobs)
        return 0

    monkeypatch.setattr(CLI, "_run_batch_with_reused_auth", inspect_batch)
    assert CLI.run_test(args) == 0
    assert len(calls) == 1


@pytest.mark.parametrize(("original", "live"), [(pool(), pool("B")), (pool("B"), pool())])
def test_changed_placement_requires_confirmation_before_any_job(tmp_path, monkeypatch, original, live):
    args = args_for(tmp_path, monkeypatch)
    initial = build(args, original)
    current = build(args, live)
    assert initial["preview"]["confirmation"]["token"] != current["preview"]["confirmation"]["token"]
    args.confirm = initial["preview"]["confirmation"]["token"]
    args.dry_run = False
    monkeypatch.setattr(CLI, "_authenticate_and_fetch_available_resources",
                        lambda _env: (live, object(), "tester@example.com"))
    monkeypatch.setattr(CLI, "_run_batch_with_reused_auth",
                        lambda *_: pytest.fail("placement changed: must not create any Job"))
    with pytest.raises(ValueError, match="结果已变化"):
        CLI.run_test(args)
