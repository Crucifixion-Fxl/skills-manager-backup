"""Automation type never supplies implicit business resource constraints."""

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


@pytest.mark.parametrize("kind", ["app", "embedded", "hardware"])
@pytest.mark.parametrize("resources", [None, [], pool(), pool("B"),
                                       [resource("PHONE", None)]])
def test_automation_type_does_not_add_business_constraints(tmp_path, monkeypatch, kind, resources):
    args = args_for(tmp_path, monkeypatch, kind=kind)
    context = build(args, resources)
    assert "constraints" not in context["job"][0]
    assert "placement" not in context["job"][0]
    assert context["preview"]["execution"]["concurrency"] == 1


@pytest.mark.parametrize("resources", [None, [], pool("B"),
                                       [resource("PHONE", None)]])
def test_explicit_cabinet_constraint_preserved_for_server_even_without_matching_inventory(
    tmp_path, monkeypatch, resources,
):
    args = args_for(tmp_path, monkeypatch, constraints=[affinity("belong_to_cabinet")])
    context = build(args, resources)
    assert context["job"][0]["constraints"] == [{
        "type": "AFFINITY", "topologyKey": "belong_to_cabinet", "resources": ["camera", "phone"],
    }]
    assert context["preview"]["resourceGroups"][0]["constraints"] == context["job"][0]["constraints"]


def test_inventory_topology_changes_do_not_change_confirmed_execution(tmp_path, monkeypatch):
    args = args_for(tmp_path, monkeypatch)
    before = build(args, pool())
    after = build(args, pool("B"))
    assert before["preview"]["confirmation"]["token"] == after["preview"]["confirmation"]["token"]
