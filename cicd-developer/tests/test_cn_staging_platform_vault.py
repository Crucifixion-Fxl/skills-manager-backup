"""Exact approved platform consumer; no arbitrary custom-mount escape hatch."""
import copy
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL / "validators/check_vault_paths.py"
FIXTURE = SKILL / "references/approved-exceptions/cn-staging-shared-pg-platform.yaml"
RELATIVE = Path("clusters/tencent-100052802231-cn-staging/cicd/crossplane/post-install/shared-postgres-platform-connection.yaml")
APPROVED = FIXTURE.read_bytes()


def put(root, data=APPROVED, relative=RELATIVE):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def git_context(root, origin="https://gitlab.addx.ai/DEV/k8s.git", tracked=True):
    commands = [["init", "-q"], ["config", "--local", "remote.origin.url", origin],
                ["config", "--local", "user.name", "Public fixture"],
                ["config", "--local", "user.email", "fixture@example.invalid"]]
    if tracked:
        commands.append(["add", "--", str(RELATIVE)])
    commands.append(["commit", "-q", "--allow-empty", "-m", "public fixture"])
    for command in commands:
        subprocess.run(["git", "-C", str(root), *command], check=True, capture_output=True)


def check(root):
    return subprocess.run([sys.executable, str(VALIDATOR), str(root)], capture_output=True, text=True, check=False)


def test_exact_platform_source_and_complete_digest(tmp_path):
    assert hashlib.sha256(APPROVED).hexdigest() == "c8c040f6ab710190ccdf6e23114eb85aa9d590cd72defd529ffd437f201f1125"
    put(tmp_path); git_context(tmp_path)
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 vault path(s)" in result.stdout
    assert "reviewed-cn-staging-platform" in result.stdout


def test_authoritative_non_deployable_fixture():
    result = check(FIXTURE.parent)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("origin", ["https://gitlab.addx.ai/DEV/other.git", "https://example.invalid/DEV/k8s.git", "https://gitlab.addx.ai.evil.invalid/DEV/k8s.git"])
def test_wrong_origin_cannot_enable_custom_mount(tmp_path, origin):
    put(tmp_path); git_context(tmp_path, origin=origin)
    assert check(tmp_path).returncode == 1


def test_untracked_source_is_not_approved(tmp_path):
    put(tmp_path); git_context(tmp_path, tracked=False)
    assert check(tmp_path).returncode == 1


@pytest.mark.parametrize("relative", [RELATIVE.with_name("neighbor.yaml"), Path("clusters/tencent-100052802231-cn-prod/cicd/crossplane/post-install/shared-postgres-platform-connection.yaml"), Path("k8s/staging-cn/external-secret.yaml")])
def test_other_path_and_production_cannot_borrow_contract(tmp_path, relative):
    put(tmp_path, relative=relative); git_context(tmp_path, tracked=False)
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", str(relative)], check=True)
    assert check(tmp_path).returncode == 1


def variants():
    base = yaml.safe_load(APPROVED)
    rows = []
    def add(name, mutation):
        d = copy.deepcopy(base); mutation(d); rows.append(pytest.param(yaml.safe_dump(d).encode(), id=name))
    add("name", lambda d: d["metadata"].__setitem__("name", "other"))
    add("namespace", lambda d: d["metadata"].__setitem__("namespace", "default"))
    add("store", lambda d: d["spec"]["secretStoreRef"].__setitem__("name", "vault-backend"))
    add("cluster-store", lambda d: d["spec"]["secretStoreRef"].__setitem__("kind", "ClusterSecretStore"))
    add("version", lambda d: d["spec"]["data"][0]["remoteRef"].__setitem__("version", "2"))
    add("property", lambda d: d["spec"]["data"][0]["remoteRef"].__setitem__("property", "password"))
    add("other-platform", lambda d: d["spec"]["data"][0]["remoteRef"].__setitem__("key", "platform/cn-staging/other"))
    add("ordinary-canonical-path", lambda d: [f["remoteRef"].__setitem__("key", "staging/rds/application/mlflow/postgres") for f in d["spec"]["data"]])
    add("remove-field", lambda d: d["spec"]["data"].pop())
    add("template", lambda d: d["spec"]["target"]["template"]["data"].__setitem__("password", "{{ .password }}"))
    add("data-from", lambda d: d["spec"].__setitem__("dataFrom", [{"extract": {"key": "platform/cn-staging/postgres"}}]))
    add("approval-annotation", lambda d: d["metadata"]["annotations"].__setitem__("approved", "true"))
    rows.extend([pytest.param(APPROVED+b"# changed\n", id="bytes"), pytest.param(APPROVED+b"---\nkind: Secret\n", id="extra-document"), pytest.param(b"", id="empty")])
    return rows


@pytest.mark.parametrize("data", variants())
def test_reserved_source_mutation_cannot_fall_back_to_generic_rules(tmp_path, data):
    put(tmp_path, data); git_context(tmp_path)
    result = check(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr


def test_file_and_parent_symlinks_are_not_approved(tmp_path):
    target=put(tmp_path); git_context(tmp_path)
    real=tmp_path/"copy"; real.write_bytes(APPROVED); target.unlink(); target.symlink_to(real)
    assert check(tmp_path).returncode == 1
    target.unlink(); target.parent.rmdir(); actual=tmp_path/"actual"; actual.mkdir(); (actual/target.name).write_bytes(APPROVED)
    target.parent.symlink_to(actual, target_is_directory=True)
    assert check(tmp_path).returncode == 1


def test_exact_file_does_not_allow_a_neighbor(tmp_path):
    put(tmp_path); git_context(tmp_path)
    put(tmp_path, relative=RELATIVE.with_name("neighbor.yaml"))
    assert check(tmp_path).returncode == 1


def test_required_ci_entrypoint_runs_regressions():
    repository=next(p for p in SKILL.parents if (p/".gitlab/ci/cicd-developer-offline-e2e.yml").is_file())
    job=yaml.safe_load((repository/".gitlab/ci/cicd-developer-offline-e2e.yml").read_text())["cicd-developer:offline-e2e"]
    commands=[x for x in job["script"] if isinstance(x,str) and " pytest " in x]
    assert len(commands)==1
    assert "skills/delivery/cicd-developer/tests/test_cn_staging_platform_vault.py" in commands[0].split()
    assert job.get("allow_failure",False) is False


@pytest.mark.parametrize("mutation", ["empty", "canonical", "missing", "symlink"])
def test_authoritative_fixture_changes_cannot_fall_back(tmp_path, mutation):
    # Run the validator's public entrypoint from an isolated skill copy.
    skill = tmp_path / "skill"
    shutil.copytree(SKILL / "validators", skill / "validators")
    shutil.copytree(SKILL / "references/vault-paths", skill / "references/vault-paths")
    fixture = skill / "references/approved-exceptions/cn-staging-shared-pg-platform.yaml"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(APPROVED)
    if mutation == "empty":
        fixture.write_bytes(b"")
    elif mutation == "canonical":
        d = yaml.safe_load(APPROVED)
        for field in d["spec"]["data"]:
            field["remoteRef"]["key"] = "staging/rds/application/mlflow/postgres"
        fixture.write_text(yaml.safe_dump(d))
    elif mutation == "missing":
        fixture.unlink()
    else:
        source=tmp_path/"source"; source.write_bytes(APPROVED)
        fixture.unlink(); fixture.symlink_to(source)
    result = subprocess.run([sys.executable, str(skill/"validators/check_vault_paths.py"), str(fixture.parent)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr


def test_reserved_source_canonical_mutant_through_lexical_alias_is_rejected(tmp_path):
    d = yaml.safe_load(APPROVED)
    for field in d["spec"]["data"]:
        field["remoteRef"]["key"] = "staging/rds/application/mlflow/postgres"
    target = put(tmp_path, yaml.safe_dump(d).encode()); git_context(tmp_path)
    child=target.parent/"child"; child.mkdir()
    assert check(child/"..").returncode == 1


def test_reserved_source_canonical_mutant_through_symlink_scope_is_rejected(tmp_path):
    d = yaml.safe_load(APPROVED)
    for field in d["spec"]["data"]:
        field["remoteRef"]["key"] = "staging/rds/application/mlflow/postgres"
    target=put(tmp_path, yaml.safe_dump(d).encode()); git_context(tmp_path)
    alias=tmp_path/"alias"; alias.symlink_to(target.parent, target_is_directory=True)
    assert check(alias).returncode == 1


def test_direct_reserved_scope_symlink_outside_git_cannot_fall_back(tmp_path):
    root=tmp_path/"repo"; root.mkdir()
    target=put(root); git_context(root)
    d=yaml.safe_load(APPROVED)
    for field in d["spec"]["data"]:
        field["remoteRef"]["key"]="staging/rds/application/mlflow/postgres"
    outside=tmp_path/"outside"; outside.mkdir(); (outside/target.name).write_text(yaml.safe_dump(d))
    target.unlink(); target.parent.rmdir(); target.parent.symlink_to(outside, target_is_directory=True)
    assert check(target.parent).returncode == 1


def test_direct_authoritative_fixture_parent_symlink_outside_cannot_fall_back(tmp_path):
    skill=tmp_path/"skill"
    shutil.copytree(SKILL/"validators", skill/"validators")
    shutil.copytree(SKILL/"references/vault-paths", skill/"references/vault-paths")
    d=yaml.safe_load(APPROVED)
    for field in d["spec"]["data"]:
        field["remoteRef"]["key"]="staging/rds/application/mlflow/postgres"
    outside=tmp_path/"outside"; outside.mkdir(); (outside/FIXTURE.name).write_text(yaml.safe_dump(d))
    alias=skill/"references/approved-exceptions"; alias.symlink_to(outside, target_is_directory=True)
    result=subprocess.run([sys.executable,str(skill/"validators/check_vault_paths.py"),str(alias)],capture_output=True,text=True)
    assert result.returncode == 1, result.stdout+result.stderr


def test_symlink_dotdot_must_not_change_the_scanned_tree(tmp_path):
    root=tmp_path/"repo"; root.mkdir(); target=put(root); git_context(root)
    outside=tmp_path/"outside"; (outside/"nested").mkdir(parents=True)
    wrong=yaml.safe_load(APPROVED)
    for field in wrong["spec"]["data"]:
        field["remoteRef"]["key"]="platform/cn-staging/other"
    external=outside/"post-install"; external.mkdir(); (external/target.name).write_text(yaml.safe_dump(wrong))
    alias=target.parent.parent/"alias"; alias.symlink_to(outside/"nested", target_is_directory=True)
    raw=alias/".."/"post-install"
    assert raw.resolve()==external
    assert check(external).returncode == 1
    result=check(raw)
    assert result.returncode != 0, result.stdout+result.stderr
    assert "reviewed-cn-staging-platform exact files=1" not in result.stdout


@pytest.mark.parametrize("mutation", ["missing-file", "dangling-parent", "unreadable-parent"])
def test_tracked_reserved_input_cannot_disappear_from_scan(tmp_path, mutation):
    target=put(tmp_path); git_context(tmp_path)
    if mutation == "missing-file":
        target.unlink()
    elif mutation == "dangling-parent":
        target.unlink(); target.parent.rmdir(); target.parent.symlink_to(tmp_path/"missing", target_is_directory=True)
    else:
        target.parent.chmod(0)
    try:
        if mutation == "unreadable-parent" and os.access(target, os.R_OK):
            pytest.skip("this process can still read chmod000; unreadability was not established")
        assert check(tmp_path).returncode == 1
    finally:
        if mutation == "unreadable-parent":
            target.parent.chmod(0o755)


def test_old_checkout_without_platform_consumer_uses_default_inventory(tmp_path):
    git_context(tmp_path, tracked=False)
    assert check(tmp_path).returncode == 0


@pytest.mark.parametrize("context", ["source", "fixture"])
@pytest.mark.parametrize("link_type", ["parent", "file"])
@pytest.mark.parametrize("mutation", ["empty", "canonical"])
def test_two_aliases_cannot_hide_reserved_physical_content(tmp_path, context, link_type, mutation):
    # The reserved slot and a separate scan alias point at the same Git-external
    # directory. Neither alias may hide an empty or ordinary-path replacement.
    if context == "source":
        root = tmp_path / "repo"
        root.mkdir()
        target = put(root)
        git_context(root)
        validator = VALIDATOR
        diagnostic = "reserved CN staging platform consumer differs from its exact reviewed source"
    else:
        root = tmp_path / "skill"
        shutil.copytree(SKILL / "validators", root / "validators")
        shutil.copytree(SKILL / "references/vault-paths", root / "references/vault-paths")
        target = root / "references/approved-exceptions" / FIXTURE.name
        target.parent.mkdir(parents=True)
        target.write_bytes(APPROVED)
        validator = root / "validators/check_vault_paths.py"
        diagnostic = "authoritative CN staging platform fixture differs from its exact reviewed source"
    outside = tmp_path / "outside"
    outside.mkdir()
    replacement = outside / target.name
    if mutation == "empty":
        replacement.write_bytes(b"")
    else:
        data = yaml.safe_load(APPROVED)
        for field in data["spec"]["data"]:
            field["remoteRef"]["key"] = "staging/rds/application/mlflow/postgres"
        replacement.write_text(yaml.safe_dump(data))
    target.unlink()
    if link_type == "parent":
        target.parent.rmdir()
        target.parent.symlink_to(outside, target_is_directory=True)
    else:
        target.symlink_to(replacement)
    scan = root / "separate-scan-alias"
    scan.symlink_to(outside, target_is_directory=True)
    result = subprocess.run([sys.executable, str(validator), str(scan)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert diagnostic in result.stdout
    assert "PASS:" not in result.stdout


@pytest.mark.parametrize("context", ["source", "fixture"])
def test_unresolvable_reserved_parent_is_not_assumed_outside_alias_scope(tmp_path, context):
    if context == "source":
        root = tmp_path / "repo"
        root.mkdir()
        target = put(root)
        git_context(root)
        validator = VALIDATOR
        diagnostic = "reserved CN staging platform consumer differs from its exact reviewed source"
    else:
        root = tmp_path / "skill"
        shutil.copytree(SKILL / "validators", root / "validators")
        shutil.copytree(SKILL / "references/vault-paths", root / "references/vault-paths")
        target = root / "references/approved-exceptions" / FIXTURE.name
        target.parent.mkdir(parents=True)
        target.write_bytes(APPROVED)
        validator = root / "validators/check_vault_paths.py"
        diagnostic = "authoritative CN staging platform fixture differs from its exact reviewed source"
    target.unlink()
    target.parent.rmdir()
    target.parent.symlink_to(target.parent.name, target_is_directory=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    scan = root / "separate-scan-alias"
    scan.symlink_to(outside, target_is_directory=True)
    result = subprocess.run([sys.executable, str(validator), str(scan)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert diagnostic in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("context", ["source", "fixture"])
@pytest.mark.parametrize("missing", ["file", "parent"])
def test_missing_reservation_outside_narrow_scan_preserves_default_rules(tmp_path, context, missing):
    if context == "source":
        root = tmp_path / "repo"
        root.mkdir()
        target = put(root)
        git_context(root)
        validator = VALIDATOR
    else:
        root = tmp_path / "skill"
        shutil.copytree(SKILL / "validators", root / "validators")
        shutil.copytree(SKILL / "references/vault-paths", root / "references/vault-paths")
        target = root / "references/approved-exceptions" / FIXTURE.name
        target.parent.mkdir(parents=True)
        target.write_bytes(APPROVED)
        validator = root / "validators/check_vault_paths.py"
    target.unlink()
    if missing == "parent":
        target.parent.rmdir()
    scan = root / "unrelated-app-scope"
    scan.mkdir()
    document = yaml.safe_load(APPROVED)
    for field in document["spec"]["data"]:
        field["remoteRef"]["key"] = "staging/rds/application/mlflow/postgres"
    (scan / "ordinary.yaml").write_text(yaml.safe_dump(document))
    result = subprocess.run([sys.executable, str(validator), str(scan)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 vault path(s) checked" in result.stdout
    assert "reviewed-cn-staging-platform" not in result.stdout
