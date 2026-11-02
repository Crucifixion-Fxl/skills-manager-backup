"""Contract and review behavior for the bounded CI Job setup projection."""

import json
import re
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parents[1]
REFERENCES = SKILL / "references"
GUIDE = REFERENCES / "ci-job-setup.md"


def test_ci_job_setup_descriptor_is_independently_negotiated():
    assert json.loads((REFERENCES / "ci-job-setup-contract.json").read_text()) == {
        "schema_version": 1,
        "capability": "ci-job-setup/v1",
        "tool": "read_review_source",
        "operation": "ci-job-setup",
        "flag": "CODE_REVIEW_CI_JOB_SETUP_ENABLED",
    }


def test_contract_is_linked_from_authoritative_review_entrypoints():
    for filename in (
        "references/step-0-intake.md",
        "references/ci-integration.md",
        "references/review-data-contract.md",
        "references/REFERENCE.md",
    ):
        assert "ci-job-setup.md" in (SKILL / filename).read_text(), filename


def test_request_is_one_fixed_root_job_and_dependency():
    examples = re.findall(r"```json\n(.*?)\n```", GUIDE.read_text(), re.DOTALL)
    assert len(examples) == 1
    request = json.loads(examples[0])
    assert set(request) == {"operation", "source", "revision", "path", "job", "dependency"}
    assert request["operation"] == "ci-job-setup" and request["source"] == "root"
    assert request["revision"] in {"base", "head"}
    assert request["path"] in {".gitlab-ci.yml", ".gitlab-ci.yaml"}
    assert request["job"] and request["dependency"]


def test_boundaries_separate_static_declarations_from_runtime_and_absence():
    guide = GUIDE.read_text()
    for required in (
        'coverage="static_job_setup_declarations_only"',
        "installation_declared=false",
        "effective_configuration_resolved=false",
        "runtime_installation_verified=false",
        "owner_sha256",
        "failure_tolerated",
        "requirements",
        "shell",
        "外部 include",
        "固定 SHA",
        "inputs_unresolved",
        "external_config_unresolved",
        "pip_arguments_unresolved",
        "shell_execution_unresolved",
        "UNKNOWN",
        "incomplete",
        "产品红线",
        "whole-review digest",
        "结果缓存",
    ):
        assert required in guide


def test_issue_evals_cover_anchor_missing_unknown_and_cache_replay():
    cases = json.loads((SKILL / "evals/ci-job-setup/cases.json").read_text())
    assert cases["source_issue"] == "engineering/ci-templates#158"
    by_id = {case["id"]: case for case in cases["cases"]}
    assert len(by_id) == len(cases["cases"]), "eval case IDs must be unique"
    assert set(by_id) == {
        "two-document-input-header-anchor-and-tolerated-failure",
        "confirmed-missing-in-clean-pinned-image",
        "unsupported-requirements-and-external-include",
        "arbitrary-multidocument-input-remains-unknown",
        "capability-marker-invalidates-legacy-review-artifact",
    }
    anchor = by_id["two-document-input-header-anchor-and-tolerated-failure"]
    assert anchor["expected_projection"][
        "installation_declared"
    ] is True
    assert "inputs_unresolved" in anchor["expected_projection"]["unknown_reasons_include"]
    assert anchor["expected_projection"]["contains_record"]["failure_tolerated"] is True
    missing = by_id["confirmed-missing-in-clean-pinned-image"]
    assert missing["expected_projection"]["installation_declared"] is False
    assert "independently prove" in missing["expected_review"]
    unsupported = by_id["unsupported-requirements-and-external-include"]
    assert "incomplete" in unsupported["expected_review"]
    assert set(unsupported["expected_projection"]["unknown_reasons_include"]) >= {
        "external_config_unresolved",
        "pip_arguments_unresolved",
        "shell_execution_unresolved",
    }
    multi_doc = by_id["arbitrary-multidocument-input-remains-unknown"]
    assert multi_doc["expected_projection"] == {"status": "unknown", "reason": "CI_JOB_SETUP_UNSAFE"}
    cache = by_id["capability-marker-invalidates-legacy-review-artifact"]
    assert cache["projection_requests"]["result_cache"] is False
    assert cache["projection_requests"]["budget_query_identity"] == ["job", "dependency"]
    anchor_documents = list(yaml.safe_load_all(anchor["yaml_fixture"]))
    assert len(anchor_documents) == 2 and list(anchor_documents[0]) == ["spec"]
    assert len(list(yaml.safe_load_all(multi_doc["yaml_fixture"]))) == 3
    for case in by_id.values():
        if "yaml_fixture" in case:
            list(yaml.safe_load_all(case["yaml_fixture"]))
