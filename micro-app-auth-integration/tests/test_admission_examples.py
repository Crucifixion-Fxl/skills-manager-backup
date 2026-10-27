"""Exercise the shipped declaration against the platform's reviewed SSOT tool.

Set MICRO_APP_PLATFORM_REPO and MICRO_APP_PLATFORM_REVISION from an authorized,
exact-source review receipt. Neither value is discovered from checkout prose.
Platform code is not vendored; verified commit blobs form a private test snapshot.
The tool is a read-only local planner; this test never dereferences bindingRef.
"""
import copy
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = SKILL_ROOT / "references/examples/application-admission.json"
NEXT_EXAMPLE = SKILL_ROOT / "references/examples/nextjs-platform-admission.json"
PLATFORM_ORIGINS = {
    "https://gitlab.addx.ai/WEB/micro_app_platform.git",
    "https://gitlab.addx.ai/WEB/micro_app_platform",
    "git@gitlab.addx.ai:WEB/micro_app_platform.git",
    "ssh://git@gitlab.addx.ai/WEB/micro_app_platform.git",
}
MAX_BLOB_BYTES = 1024 * 1024
# This allowlist records planner-execution review only, never SDK runtime support.
# Updating it requires an independent exact-source review; checkout/env data cannot add entries.
REVIEWED_PLATFORM_REVISIONS = frozenset({"83ca0b6653cda78b707169c97dcad6a8e0387279", "e63f7547d09dd0a695327c217326e7633fd6ec32"})


def reviewed_platform_snapshot(raw_path, revision):
    """Bind operator-supplied reviewed revision, then execute only verified blobs.

    An origin/SHA match does not itself grant review authorization. The caller
    must obtain both explicit inputs from the current trusted review/run scope.
    No ref resolution, repository instructions, imports or hooks establish trust.
    """
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Explicit reviewed full revision is required")
    if revision not in REVIEWED_PLATFORM_REVISIONS:
        raise ValueError("Platform revision has no committed execution-review pin")
    platform = Path(raw_path).absolute()
    if any(p.is_symlink() for p in (platform, *platform.parents)) or not platform.is_dir():
        raise ValueError("Ordinary platform checkout required")
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*arguments):
        try:
            return subprocess.check_output(
                ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *arguments],
                cwd=platform, env=environment, stderr=subprocess.DEVNULL, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Reviewed platform source cannot be verified") from None

    if git("remote", "get-url", "origin").decode().strip() not in PLATFORM_ORIGINS:
        raise ValueError("Canonical platform origin required")
    if git("rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("Platform HEAD does not match reviewed revision")

    def blob(relative, optional=False):
        entry = git("ls-tree", revision, "--", relative)
        if not entry and optional:
            return None
        if not entry.startswith((b"100644 blob ", b"100755 blob ")):
            raise ValueError("Ordinary committed source file required")
        size = int(git("cat-file", "-s", revision + ":" + relative))
        if size > MAX_BLOB_BYTES:
            raise ValueError("Platform source blob exceeds test budget")
        current = platform
        for part in Path(relative).parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Platform source symlink rejected")
        if not current.is_file():
            raise ValueError("Platform source file unavailable")
        with current.open("rb") as source:
            live = source.read(MAX_BLOB_BYTES + 1)
        pinned = git("show", revision + ":" + relative)
        if len(pinned) != size or live != pinned:
            raise ValueError("Unreviewed platform file change rejected")
        return pinned

    files = {name: blob(name) for name in ("scripts/sdk-admission.py", "docs/api/sdk-support-v1.json")}
    catalog = json.loads(files["docs/api/sdk-support-v1.json"])
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("Bounded platform SDK catalog required")
    for entry in entries:
        sdk_path = entry.get("sdkPath") if isinstance(entry, dict) else None
        if not isinstance(sdk_path, str) or not re.fullmatch(r"packages/sdk/[A-Za-z0-9_-]+", sdk_path):
            raise ValueError("Canonical SDK manifest path required")
        manifests = []
        for filename in ("package.json", "pom.xml", "pyproject.toml", "go.mod"):
            name = sdk_path + "/" + filename
            content = blob(name, optional=True)
            if content is not None:
                files[name] = content
                manifests.append(name)
        if not manifests:
            raise ValueError("Reviewed SDK manifest required")
    snapshot = tempfile.TemporaryDirectory(prefix="reviewed-micro-planner-")
    try:
        for relative, content in files.items():
            destination = Path(snapshot.name) / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    except Exception:
        snapshot.cleanup()
        raise
    return snapshot, catalog


def verify_snapshot(platform, hashes):
    if any(p.is_symlink() for p in (platform, *platform.parents)):
        raise ValueError("Planner snapshot directory symlink rejected")
    for relative, expected in hashes.items():
        current = platform
        for part in Path(relative).parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Planner snapshot symlink rejected")
        if not current.is_file():
            raise ValueError("Planner snapshot file unavailable")
        with current.open("rb") as source:
            actual = source.read(MAX_BLOB_BYTES + 1)
        if hashlib.sha256(actual).hexdigest() != expected:
            raise ValueError("Planner snapshot changed after verification")


class AdmissionExampleTest(unittest.TestCase):
    def test_example_has_only_isolated_binding_references(self):
        for example in (EXAMPLE, NEXT_EXAMPLE):
            with self.subTest(example=example.name):
                declaration = json.loads(example.read_text(encoding="utf-8"))
                self.assertEqual(declaration["schemaVersion"], "micro-app-admission/1")
                self.assertEqual(declaration["environment"], "staging-us")
                bindings = declaration["credentials"]
                self.assertTrue(bindings)
                self.assertTrue(all(set(v) == {"purpose", "bindingRef"} for v in bindings))
                self.assertEqual(len({v["purpose"] for v in bindings}), len(bindings))
                self.assertEqual(len({v["bindingRef"] for v in bindings}), len(bindings))
                self.assertTrue(all(v["bindingRef"].startswith("example:") for v in bindings))


class PlannerExecutionTrustTest(unittest.TestCase):
    def test_untrusted_checkout_never_executes_planner(self):
        for defect in ("wrong-origin", "missing-revision", "symbolic-revision", "wrong-revision", "unreviewed-revision",
                       "dirty-script", "dirty-catalog", "dirty-manifest", "symlink-script"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve() / "platform"
                root.mkdir()
                marker = Path(tmp) / "planner-executed"
                script = root / "scripts/sdk-admission.py"
                script.parent.mkdir()
                script.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\nprint('{}')\n")
                catalog = root / "docs/api/sdk-support-v1.json"
                catalog.parent.mkdir(parents=True)
                catalog.write_text('{"entries":[{"sdkPath":"packages/sdk/auth_node_core"}]}')
                manifest = root / "packages/sdk/auth_node_core/package.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text('{"version":"0.1.0"}')
                def git(*args):
                    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                                                    "-c", "core.untrackedCache=false", *args], cwd=root, text=True,
                                                   stderr=subprocess.DEVNULL, timeout=5).strip()
                git("init", "-q")
                git("config", "user.name", "Isolated test")
                git("config", "user.email", "test@example.invalid")
                git("remote", "add", "origin", "https://gitlab.addx.ai/WEB/micro_app_platform.git")
                git("add", ".")
                git("commit", "-qm", "Synthetic planner trust fixture")
                revision = git("rev-parse", "HEAD")
                fixture_revision = revision
                if defect == "wrong-origin": git("remote", "set-url", "origin", "https://example.invalid/untrusted.git")
                if defect == "wrong-revision": revision = "f" * 40
                if defect == "symbolic-revision": revision = "HEAD"
                if defect == "dirty-script": script.write_text(script.read_text() + "# unreviewed change\n")
                if defect == "dirty-catalog": catalog.write_text(catalog.read_text() + "\n")
                if defect == "dirty-manifest": manifest.write_text('{"version":"9.9.9"}')
                if defect == "symlink-script":
                    outside = Path(tmp) / "outside.py"
                    outside.write_bytes(script.read_bytes())
                    script.unlink()
                    script.symlink_to(outside)
                reviewed_fixture = (nullcontext() if defect == "unreviewed-revision" else
                                    patch.dict(globals(), {"REVIEWED_PLATFORM_REVISIONS": {fixture_revision}}))
                with reviewed_fixture, patch.dict(os.environ, {"MICRO_APP_PLATFORM_REPO": str(root),
                                                                "MICRO_APP_PLATFORM_REVISION": revision}):
                    if defect == "missing-revision": os.environ.pop("MICRO_APP_PLATFORM_REVISION", None)
                    with self.assertRaises(ValueError):
                        PlatformAdmissionContractTest.setUpClass()
                        PlatformAdmissionContractTest("test_staging_plan_keeps_every_external_gate_pending").resolve({})
                self.assertFalse(marker.exists(), "Untrusted planner executed before its trust gate")

    def test_snapshot_tampering_is_rejected_before_each_execution(self):
        for defect in ("changed-bytes", "symlink"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                script = root / "scripts/sdk-admission.py"
                script.parent.mkdir()
                script.write_text("# owned inert fixture\n")
                hashes = {"scripts/sdk-admission.py": hashlib.sha256(script.read_bytes()).hexdigest()}
                verify_snapshot(root, hashes)
                if defect == "changed-bytes": script.write_text("# unreviewed replacement\n")
                else:
                    other = root / "replacement.py"
                    other.write_bytes(script.read_bytes())
                    script.unlink()
                    script.symlink_to(other)
                with self.assertRaises(ValueError): verify_snapshot(root, hashes)

    def test_owned_os_temporary_root_is_pinned_before_symlink_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp).resolve(strict=True)
            script = canonical / "planner.py"
            script.write_bytes(b"# owned fixture\n")
            hashes = {"planner.py": hashlib.sha256(script.read_bytes()).hexdigest()}
            verify_snapshot(canonical, hashes)

    def test_replacing_pinned_snapshot_root_with_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "snapshot"
            root.mkdir()
            script = root / "planner.py"
            script.write_bytes(b"# owned fixture\n")
            hashes = {"planner.py": hashlib.sha256(script.read_bytes()).hexdigest()}
            verify_snapshot(root, hashes)
            moved = root.with_name("replaced")
            root.rename(moved)
            root.symlink_to(moved, target_is_directory=True)
            with self.assertRaises(ValueError): verify_snapshot(root, hashes)


@unittest.skipUnless(os.environ.get("MICRO_APP_PLATFORM_REPO"), "reviewed platform checkout required")
class PlatformAdmissionContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot, cls.catalog = reviewed_platform_snapshot(
            os.environ["MICRO_APP_PLATFORM_REPO"], os.environ.get("MICRO_APP_PLATFORM_REVISION"))
        cls.addClassCleanup(snapshot.cleanup)
        # Canonicalize once at trusted creation (macOS /var is a system alias),
        # then retain this path so later directory replacement cannot be hidden.
        cls.platform = Path(snapshot.name).resolve(strict=True)
        cls.script = cls.platform / "scripts/sdk-admission.py"
        cls.snapshot_hashes = {str(p.relative_to(cls.platform)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in cls.platform.rglob("*") if p.is_file()}

    def resolve(self, declaration, catalog=None):
        verify_snapshot(self.platform, self.snapshot_hashes)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "declaration.json"
            support = Path(tmp) / "catalog.json"
            source.write_text(json.dumps(declaration), encoding="utf-8")
            support.write_text(json.dumps(catalog or self.catalog), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(self.script), "--input", str(source), "--catalog", str(support)],
                cwd=self.platform, text=True, capture_output=True, timeout=15, check=False,
            )
            self.assertEqual(result.stderr, "")
            return result.returncode, json.loads(result.stdout)

    def declaration(self):
        return json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_staging_plan_keeps_every_external_gate_pending(self):
        code, plan = self.resolve(self.declaration())
        self.assertEqual(code, 0)
        self.assertEqual(plan["status"], "REGISTRATION_PENDING")
        self.assertEqual(set(plan["gates"].values()), {"pending"})
        entry = next(e for e in self.catalog["entries"] if e["id"] == plan["sdk"]["id"])
        self.assertEqual(plan["sdk"]["version"], entry["version"])

    def test_candidate_prod_missing_capability_and_profile_credentials_block(self):
        cases = []
        prod = self.declaration()
        prod["environment"] = "prod-us"
        cases.append(prod)
        capability = self.declaration()
        capability["requiredCapabilities"].append("not-a-declared-capability")
        cases.append(capability)
        oidc = self.declaration()
        oidc.update(sdk="python", profile="oidc")
        cases.append(oidc)
        bridge = self.declaration()
        bridge.update(sdk="go", profile="web-bridge")
        cases.append(bridge)
        reused = self.declaration()
        reused["credentials"].append({"purpose": "oidc-client", "bindingRef": reused["credentials"][0]["bindingRef"]})
        cases.append(reused)
        for declaration in cases:
            with self.subTest(environment=declaration["environment"], profile=declaration["profile"]):
                code, result = self.resolve(declaration)
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertNotIn("credentials", result)

    def test_authn_only_declaration_does_not_require_facade_binding(self):
        declaration = self.declaration()
        declaration["requiredCapabilities"] = ["authn"]
        declaration["credentials"] = []
        code, plan = self.resolve(declaration)
        self.assertEqual(code, 0)
        self.assertEqual(plan["credentials"], [])
        self.assertEqual(plan["status"], "REGISTRATION_PENDING")

    def test_profile_binding_is_required_but_does_not_complete_registration(self):
        for sdk, profile, purpose in (("python", "oidc", "oidc-client"), ("go", "web-bridge", "web-bridge-client")):
            with self.subTest(profile=profile):
                declaration = self.declaration()
                declaration.update(sdk=sdk, profile=profile)
                declaration["credentials"].append({"purpose": purpose, "bindingRef": "example:workload/" + purpose})
                code, plan = self.resolve(declaration)
                self.assertEqual(code, 0)
                self.assertEqual(plan["status"], "REGISTRATION_PENDING")
                self.assertEqual(set(plan["gates"].values()), {"pending"})

    def test_web_bridge_old_hmac_purpose_cannot_replace_registered_client(self):
        declaration = self.declaration()
        declaration.update(sdk="go", profile="web-bridge")
        declaration["credentials"].append({"purpose": "web-bridge-hmac", "bindingRef": "example:wrong-purpose"})
        self.assertEqual(self.resolve(declaration)[0], 2)

    def test_raw_value_field_is_rejected_without_echo(self):
        declaration = self.declaration()
        declaration["credentials"][0]["value"] = "synthetic-never-echo-fixture"
        code, result = self.resolve(declaration)
        self.assertEqual(code, 2)
        self.assertNotIn("synthetic-never-echo-fixture", json.dumps(result))

    def test_next_online_requires_its_own_context_binding_and_keeps_runtime_pending(self):
        declaration = json.loads(NEXT_EXAMPLE.read_text(encoding="utf-8"))
        code, plan = self.resolve(declaration)
        self.assertEqual(code, 0)
        self.assertEqual(plan["status"], "REGISTRATION_PENDING")
        self.assertEqual(set(plan["gates"].values()), {"pending"})
        context = next(v for v in declaration["credentials"] if v["purpose"] == "next-context-signing")
        context["bindingRef"] = next(v["bindingRef"] for v in declaration["credentials"] if v["purpose"] == "facade-signing")
        self.assertEqual(self.resolve(declaration)[0], 2)
        declaration["credentials"] = [v for v in declaration["credentials"] if v["purpose"] != "next-context-signing"]
        self.assertEqual(self.resolve(declaration)[0], 2)

    def test_supported_null_or_wrong_environment_profile_evidence_cannot_admit(self):
        catalog = copy.deepcopy(self.catalog)
        entry = next(e for e in catalog["entries"] if e["id"] == self.declaration()["sdk"])
        entry["state"] = "supported"
        entry["runtimeEvidence"] = [None]
        self.assertEqual(self.resolve(self.declaration(), catalog)[0], 2)
        entry["runtimeEvidence"] = [{
            "environment": "staging-cn", "profile": self.declaration()["profile"],
            "sdkVersion": entry["version"], "contractVersion": catalog["contractVersion"],
            "sourceRevision": "a" * 40, "packageSha256": "b" * 64,
            "receiptUrl": "https://gitlab.addx.ai/example/project/-/issues/1#note-1",
            "result": "PASS", "capabilities": self.declaration()["requiredCapabilities"],
        }]
        self.assertEqual(self.resolve(self.declaration(), catalog)[0], 2)
        entry["runtimeEvidence"][0]["environment"] = self.declaration()["environment"]
        entry["runtimeEvidence"][0]["profile"] = "trusted-server"
        self.assertEqual(self.resolve(self.declaration(), catalog)[0], 2)
        entry["runtimeEvidence"][0]["profile"] = self.declaration()["profile"]
        self.assertEqual(self.resolve(self.declaration(), catalog)[0], 0)


if __name__ == "__main__":
    unittest.main()
