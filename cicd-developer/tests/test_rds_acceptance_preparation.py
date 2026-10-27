import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "validators/prepare_rds_acceptance.py"
spec = importlib.util.spec_from_file_location("acceptance", TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
INPUT = {"account": "390709477306", "region": "us-east-1", "cluster": "us-eks-rds-e2e-w2-staging",
         "run_id": "rds-e2e-w2", "engine_version": "16.15", "db_security_group": "sg-0123456789abcdef0"}


class PreparationTest(unittest.TestCase):
    def test_untrusted_privilege_and_target_overrides_rejected(self):
        for key in ["admin", "username", "password", "grants", "providerConfigRef", "vault_path", "namespace", "publiclyAccessible"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.prepare(dict(INPUT, **{key: "untrusted"}))
        for key, value in [("account", "302571458622"), ("region", "eu-central-1"),
                           ("cluster", "us-eks-staging"), ("cluster", "us-eks-rds-acceptance-staging"),
                           ("run_id", "normal-app"), ("run_id", "../escape"),
                           ("engine_version", "15.8"), ("engine_version", "16.15\nadmin"),
                           ("db_security_group", "sg-00000000000000000")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                module.prepare(dict(INPUT, **{key: value}))

    def test_request_contract_and_digest(self):
        encoded, report = module.prepare(INPUT)
        self.assertEqual(json.loads(encoded), {
            "apiVersion": "platform.addx.io/v1alpha1", "kind": "Database",
            "metadata": {"name": "rds-e2e-w2", "namespace": "crossplane-system",
                         "annotations": {"argocd.argoproj.io/sync-options": "Prune=false,Delete=false"}},
            "spec": {"app": "rds-e2e-w2", "engineVersion": "16.15",
                     "securityGroupId": "sg-0123456789abcdef0"}})
        self.assertEqual(report["request_sha256"], hashlib.sha256(encoded.encode()).hexdigest())
        self.assertEqual(report["kit_revision"], "3de28b04cb379dd9a177e7e17004eaf3c608afe5")

    def test_missing_fields_and_non_object_rejected(self):
        for key in INPUT:
            c = copy.deepcopy(INPUT); del c[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.prepare(c)
        with self.assertRaises(ValueError): module.prepare([])

    def test_cli_rejects_special_symlink_and_oversize_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            valid = root / "valid.json"; valid.write_text(json.dumps(INPUT))
            link = root / "link"; link.symlink_to(valid)
            fifo = root / "fifo"; os.mkfifo(fifo)
            large = root / "large.json"; large.write_bytes(b" " * 65537)
            for i, path in enumerate([link, fifo, large, root, pathlib.Path("/dev/null")]):
                out = root / ("output-" + str(i))
                result = subprocess.run([sys.executable, str(TOOL), "--inputs", str(path),
                                         "--output", str(out)], capture_output=True, timeout=3)
                with self.subTest(path=str(path)):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(out.exists())

    def test_cli_writes_only_inert_template_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp); inputs = root / "inputs.json"; inputs.write_text(json.dumps(INPUT))
            out = root / "prepared"
            command = [sys.executable, str(TOOL), "--inputs", str(inputs), "--output", str(out)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({p.name for p in out.iterdir()}, {"database.yaml.tmpl", "handoff.json"})
            report = json.loads((out / "handoff.json").read_text())
            self.assertEqual(report["status"], "PREPARED_NOT_AUTHORIZED")
            before = (out / "database.yaml.tmpl").read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual((out / "database.yaml.tmpl").read_bytes(), before)
            link = root / "link"; link.symlink_to(out, target_is_directory=True)
            self.assertNotEqual(subprocess.run(command[:-1] + [str(link)], capture_output=True).returncode, 0)


if __name__ == "__main__": unittest.main()
