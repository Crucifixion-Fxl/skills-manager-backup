"""Keep downstream instructions consistent with the admitted CN target."""
import json
from pathlib import Path
import re
from urllib.parse import unquote

import pytest
import yaml


SKILLS = Path(__file__).resolve().parents[3]
INVENTORY = SKILLS / "infrastructure/k8s-ops/references/cn-tencent-inventory.md"


@pytest.fixture
def admitted_target():
    catalog = yaml.safe_load((SKILLS / "delivery/cicd-developer/references/data/clusters.yaml").read_text())
    target = next(row for row in catalog["clusters"] if row["name"] == "cn-tke-tech-service")
    assert target["deployment_status"] == "allowed"
    assert target["endpoint_status"] == "verified"
    return target


@pytest.mark.parametrize("relative", [
    "delivery/cicd-opensource/SKILL.md",
    "delivery/cicd-opensource/references/deploy-workflow.md",
    "delivery/cicd-opensource/references/k8s-templates.md",
    "delivery/cicd-opensource/references/image-sync.md",
    "delivery/argocd-deploy/SKILL.md",
])
def test_deployment_consumers_do_not_reintroduce_global_cutover_block(admitted_target, relative):
    text = (SKILLS / relative).read_text()
    # Inspect paragraphs mentioning this target, not unrelated blocked clusters
    # or per-application readiness requirements.
    paragraphs = [part for part in text.split("\n\n") if "tech-service" in part]
    assert paragraphs, relative
    stale = re.compile(r"准入.{0,8}blocked|禁止复用旧 cn-tech tag|尚未切换|目标[，（]待切换|均为 `pending-cutover`")
    assert not any(stale.search(part) for part in paragraphs), relative
    assert "cn-tech-service-admission-2026-10-19.md" in text, relative
    if relative == "delivery/cicd-opensource/SKILL.md":
        assert admitted_target["runner_tags"][0] in text


def test_argocd_auth_recovery_matches_verified_owner_without_claiming_sso_acceptance(admitted_target):
    root = SKILLS / "delivery/argocd/references"
    discovery = json.loads((root / "auth-discovery.json").read_text())
    workflow = discovery["auth"]["missingCredentialWorkflow"]
    assert "pending-cutover" not in workflow
    assert admitted_target["argocd_url"] in workflow
    assert "argocd-cn-tech-service.addx.live" in workflow
    assert "server/callback verification" in workflow
    assert "interactive SSO" in workflow and "resource permissions" in workflow
    assert workflow in (root / "saas-access.md").read_text()


@pytest.mark.parametrize("relative", [
    "observability/prometheus/SKILL.md",
    "quality/performance-preflight/references/endpoints.md",
    "quality/performance-preflight/SKILL.md",
    "infrastructure/sre-agent/references/infra/prometheus.md",
    "infrastructure/sre-agent/SKILL.md",
    "infrastructure/sre-agent/references/gather-templates/gather-prometheus.md",
])
def test_monitoring_consumers_record_probe_scope_and_keep_explicit_selection(admitted_target, relative):
    text = (SKILLS / relative).read_text()
    assert "2026-10-19" in text
    assert "PROMETHEUS_CN_TECH_SERVICE_URL" in text
    assert "cn-tech-service-admission-2026-10-19.md" in text
    paragraphs = [part for part in text.split("\n\n") if "tech-service" in part]
    assert not any(re.search(r"目标[，（]待切换|尚待切换|部署切换尚待核验|tech-service 切换前|是待切换目标", part)
                   for part in paragraphs), relative
    summaries = ("infrastructure/sre-agent/SKILL.md", "quality/performance-preflight/SKILL.md")
    if relative not in summaries:
        assert admitted_target["prometheus_url"] in text
    if relative not in summaries + ("infrastructure/sre-agent/references/gather-templates/gather-prometheus.md",):
        assert admitted_target["vmalert_url"] in text


def test_builder_health_evidence_does_not_waive_authentication_checks():
    text = (SKILLS / "security/vault-kv-manager/SKILL.md").read_text()
    assert "pending-cutover" not in text
    assert "2026-10-19" in text and "health" in text and "Ingress" in text
    assert "cn-tech-service-admission-2026-10-19.md" in text
    assert "未核实前不发起登录" in text
    assert "Builder mount/issuer/role/audience/SA 尚无新 TKE 证据" in text


def test_consumer_regressions_run_in_ci_for_catalog_and_consumer_changes():
    fragment = SKILLS.parent / ".gitlab/ci/cn-target-routing-unit.yml"
    job = yaml.safe_load(fragment.read_text())["cn-target-routing:unit"]
    test_path = Path(__file__).relative_to(SKILLS.parent).as_posix()
    scripts = job["script"]
    test_index = next(i for i, command in enumerate(scripts) if test_path in command)
    assert "pytest" in scripts[test_index]
    assert test_index < next(i for i, command in enumerate(scripts) if command.startswith("cd "))
    required = {
        "skills/delivery/cicd-developer/**/*", "skills/delivery/cicd-opensource/**/*",
        "skills/delivery/argocd/**/*", "skills/delivery/argocd-deploy/**/*",
        "skills/infrastructure/argocd-fleet-scan/**/*", "skills/infrastructure/sre-agent/**/*",
        "skills/quality/performance-preflight/**/*", "skills/observability/prometheus/**/*",
        "skills/security/vault-kv-manager/**/*",
        "skills/infrastructure/k8s-ops/references/cn-tencent-inventory.md",
    }
    assert {rule["if"] for rule in job["rules"]} == {
        '$CI_PIPELINE_SOURCE == "merge_request_event"', '$CI_COMMIT_BRANCH == "main"',
    }
    for rule in job["rules"]:
        assert required <= set(rule["changes"])
    assert not job.get("allow_failure", False)


def test_all_cn_inventory_fragment_links_resolve():
    text = INVENTORY.read_text()
    anchors = set(re.findall(r'<a\s+(?:id|name)="([^"]+)"', text))
    for heading in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
        anchors.add(re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-"))
    links = []
    for source in SKILLS.rglob("*.md"):
        for target, fragment in re.findall(r"\]\(([^\s)#]*cn-tencent-inventory\.md)#([^\s)]+)\)", source.read_text()):
            if (source.parent / target).resolve() == INVENTORY.resolve():
                links.append((source.relative_to(SKILLS), unquote(fragment)))
    assert links, "Inventory references must be exercised"
    assert all(fragment in anchors for _, fragment in links), [item for item in links if item[1] not in anchors]
