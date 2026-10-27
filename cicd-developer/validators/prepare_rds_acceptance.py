"""Prepare a bounded platform acceptance request; never call a service."""
import argparse
import hashlib
import json
import os
import pathlib
import re
import stat

KIT_REVISION = "3de28b04cb379dd9a177e7e17004eaf3c608afe5"
FIELDS = {"account", "region", "cluster", "run_id", "engine_version", "db_security_group"}


def prepare(c):
    if not isinstance(c, dict) or set(c) != FIELDS:
        raise ValueError("Only the six platform acceptance inputs are accepted")
    if c["account"] != "390709477306" or c["region"] != "us-east-1":
        raise ValueError("Only the isolated US Staging account is accepted")
    run = c["run_id"]
    if not isinstance(run, str) or not re.fullmatch(r"rds-e2e-[a-z0-9]{2,12}", run):
        raise ValueError("Reserved acceptance run required")
    if c["cluster"] != f"us-eks-{run}-staging":
        raise ValueError("Dedicated cluster must derive from run_id")
    if not isinstance(c["engine_version"], str) or not re.fullmatch(r"16\.[0-9]+", c["engine_version"]):
        raise ValueError("PostgreSQL 16 minor required")
    sg = c["db_security_group"]
    if not isinstance(sg, str) or not re.fullmatch(r"sg-[a-f0-9]{17}", sg) or set(sg[3:]) == {"0"}:
        raise ValueError("Explicit reviewed security group ID required")
    request = {"apiVersion": "platform.addx.io/v1alpha1", "kind": "Database",
               "metadata": {"name": run, "namespace": "crossplane-system",
                            "annotations": {"argocd.argoproj.io/sync-options": "Prune=false,Delete=false"}},
               "spec": {"app": run, "engineVersion": c["engine_version"], "securityGroupId": sg}}
    # This is a template handoff, never a registration or an authorization receipt.
    encoded = json.dumps(request, indent=2) + "\n"
    manifest = {"status": "PREPARED_NOT_AUTHORIZED", "kit_revision": KIT_REVISION,
                "root_issue": "https://gitlab.addx.ai/issues/sre/dev/-/issues/168",
                "work_item": "https://gitlab.addx.ai/DEV/k8s/-/issues/57",
                "target": c, "request_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                "pending_gates": ["owner revisions", "approved cost and execution window",
                                  "live target and identity boundaries", "collision checks",
                                  "request review and explicit activation authorization"]}
    return encoded, manifest


MAX_INPUT_BYTES = 65536


def read_inputs(path):
    # Never block on FIFO/device inputs or follow a final-component symlink.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT_BYTES:
            raise ValueError("Inputs must be a regular file of at most 64 KiB")
        payload = source.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError("Inputs exceed 64 KiB")
    return json.loads(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    encoded, manifest = prepare(read_inputs(args.inputs))
    # Refuse every existing path, including symlinks; never update a GitOps source in place.
    args.output.mkdir(parents=False, exist_ok=False)
    (args.output / "database.yaml.tmpl").write_text(encoded)
    (args.output / "handoff.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("PREPARED_NOT_AUTHORIZED: database.yaml.tmpl and handoff.json")


if __name__ == "__main__":
    main()
