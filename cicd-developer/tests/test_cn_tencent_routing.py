"""Regression gates for retired AWS CN and exact current Tencent targets."""
import importlib.util
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deployment_target", ROOT / "validators/check_deployment_target.py")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def facts():
    data = ROOT / "references/data"
    return yaml.safe_load((data / "clusters.yaml").read_text()), yaml.safe_load((data / "env-keywords.yaml").read_text())


@pytest.mark.parametrize("keyword,directory,account", [
    ("prod-cn", "tencent-100014919455-cn-main/", "100014919455"),
    ("prod-cn-tke", "tencent-100014919455-cn-main/", "100014919455"),
    ("staging-cn", "tencent-100052802231-cn-staging/", "100052802231"),
    ("staging-cn-tke", "tencent-100052802231-cn-staging/", "100052802231"),
])
def test_current_cn_routes(keyword, directory, account):
    clusters, envs = facts()
    target = GATE.resolve_target(clusters, envs, env_keyword=keyword)
    assert target["cloud"] == "tencent"
    assert target["account_id"] == account
    assert target["argocd_apps_dir"] == directory
    assert "partition" not in target and "aws_region" not in target


def test_every_aws_cn_target_is_retired_and_not_default():
    clusters, envs = facts()
    retired = [c for c in clusters["clusters"] if c.get("partition") == "aws-cn"]
    assert len(retired) == 4
    for target in retired:
        assert target["deployment_status"] == "retired"
        with pytest.raises(ValueError, match="retired"):
            GATE.resolve_target(clusters, envs, cluster_name=target["name"])
    for key, target in envs["env_keywords"].items():
        if key != "dev-cn":
            assert target["cluster"] not in {c["name"] for c in retired}


def test_new_tech_lookup_is_correct_but_business_build_is_blocked():
    clusters, envs = facts()
    name = envs["env_keywords"]["prod-cn-restricted-admin"]["cluster"]
    target = next(c for c in clusters["clusters"] if c["name"] == name)
    assert target["account_id"] == "100052802231"
    assert target["argocd_apps_dir"] == "tencent-100052802231-cn-tech-service/"
    assert target["runner_tags"] == []
    with pytest.raises(ValueError, match="blocked"):
        GATE.resolve_target(clusters, envs, env_keyword="prod-cn-restricted-admin")


@pytest.mark.parametrize("keyword", ["staging-cn", "staging-cn-tke"])
def test_current_staging_vault_and_harbor_do_not_reuse_retired_defaults(keyword):
    clusters, envs = facts()
    target = GATE.resolve_target(clusters, envs, env_keyword=keyword)
    assert target["vault_css"] == "vault-backend"
    assert target["harbor_url"] == "harbor-02231-cn-staging.addx.live"
    assert target["harbor_public_url"] == "harbor-02231-cn-staging-pub.addx.live"
    assert target["runner_tags"] == ["tke-cn-staging-amd64"]
    vaults = yaml.safe_load((ROOT / "references/vault-paths/instances.yaml").read_text())["vaults"]
    assert vaults[target["vault"]]["url"] == "http://vault-active.vault.svc:8200"
    brokers = yaml.safe_load((ROOT / "references/shared-middleware/kafka-brokers.yaml").read_text())["clusters"]
    assert "staging-cn" not in brokers and "tech-cn" not in brokers
    for broker in brokers.values():
        if broker["cloud"] == "aws-cn":
            assert broker["deployment_status"] == "retired"


def test_planned_tech_endpoints_cannot_be_admitted_by_status_change_alone():
    clusters, envs = facts()
    name = envs["env_keywords"]["prod-cn-restricted-admin"]["cluster"]
    target = next(c for c in clusters["clusters"] if c["name"] == name)
    assert target["endpoint_status"] == "pending-cutover"
    assert target["vault"] == "vault-cn-prod"
    assert target["vault_css"] == "vault-backend"
    assert target["vault_builder_url"] == "https://vault-cn.builder.addx.live"
    target["deployment_status"] = "allowed"
    with pytest.raises(ValueError, match="endpoint cutover is pending"):
        GATE.resolve_target(clusters, envs, env_keyword="prod-cn-restricted-admin")
    with pytest.raises(ValueError, match="endpoint cutover is pending"):
        GATE.resolve_target(clusters, envs, cluster_name=name)


@pytest.mark.parametrize("status", ["pending_cutover", "ready", "", None, False, []])
def test_invalid_endpoint_status_rejects_catalog_for_both_selectors(status):
    clusters, envs = facts()
    tech = next(c for c in clusters["clusters"] if c["name"] == "cn-tke-tech-service")
    tech["deployment_status"] = "allowed"
    tech["endpoint_status"] = status
    with pytest.raises(ValueError, match="invalid endpoint_status"):
        GATE.resolve_target(clusters, envs, env_keyword="prod-cn-restricted-admin")
    with pytest.raises(ValueError, match="invalid endpoint_status"):
        GATE.resolve_target(clusters, envs, cluster_name="cn-tke-tech-service")
    # Like deployment_status schema errors, an invalid endpoint field makes
    # the catalog invalid even if a different target is requested.
    with pytest.raises(ValueError, match="invalid endpoint_status"):
        GATE.resolve_target(clusters, envs, env_keyword="staging-cn")


def test_verified_endpoint_status_does_not_replace_deployment_admission():
    clusters, envs = facts()
    tech = next(c for c in clusters["clusters"] if c["name"] == "cn-tke-tech-service")
    tech["endpoint_status"] = "verified"
    with pytest.raises(ValueError, match="blocked"):
        GATE.resolve_target(clusters, envs, cluster_name="cn-tke-tech-service")
    tech["deployment_status"] = "allowed"
    assert GATE.resolve_target(clusters, envs, cluster_name="cn-tke-tech-service") is tech
