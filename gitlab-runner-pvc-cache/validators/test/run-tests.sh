#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
validator="$(cd "${script_dir}/.." && pwd)/validate.sh"
repo_root="$(cd "${script_dir}/../../../.." && pwd)"

pass_count=0
fail_count=0

run_case() {
    local name="$1"
    local expected="$2"
    shift 2
    local output rc=0

    output="$("$@" 2>&1)" || rc=$?
    if [ "${rc}" = "${expected}" ]; then
        printf 'PASS %s\n' "${name}"
        pass_count=$((pass_count + 1))
    else
        printf 'FAIL %s: expected rc=%s got rc=%s\n%s\n' "${name}" "${expected}" "${rc}" "${output}" >&2
        fail_count=$((fail_count + 1))
    fi
}

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

mkdir -p "${tmp}/empty" "${tmp}/placeholder_slot" "${tmp}/placeholder_replace" \
    "${tmp}/pvc_rwo" "${tmp}/sync_wave_number" "${tmp}/argocd"

cat > "${tmp}/placeholder_slot/values.yaml" <<'YAML'
name: {{cache_purpose}}
YAML

cat > "${tmp}/placeholder_replace/values.yaml" <<'YAML'
storageClassName: REPLACE_WITH_RWX_STORAGE_CLASS
YAML

cat > "${tmp}/pvc_rwo/pvc.yaml" <<'YAML'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: bad-cache
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  storageClassName: efs-sc
YAML

cat > "${tmp}/sync_wave_number/app.yaml" <<'YAML'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: bad-app
  annotations:
    argocd.argoproj.io/sync-wave: -10
spec: {}
YAML

run_case "all-dev-null" 0 bash "${validator}" /dev/null /dev/null /dev/null
run_case "placeholder-slot" 1 bash "${validator}" "${tmp}/placeholder_slot" /dev/null /dev/null
run_case "placeholder-replace" 1 bash "${validator}" "${tmp}/placeholder_replace" /dev/null /dev/null
run_case "pvc-rwo" 1 bash "${validator}" "${tmp}/pvc_rwo" /dev/null /dev/null
run_case "sync-wave-number" 1 bash "${validator}" /dev/null "${tmp}/sync_wave_number" /dev/null

k8s_sample="/home/huatuo/project/k8s/clusters/aws-125710977284-sg-devops/cicd/gitlab-runner"
argocd_sample="/home/huatuo/project/argocd-apps/aws-125710977284-sg-devops"
project_sample="/home/huatuo/project/addx-things"
if [ -d "${k8s_sample}" ] && [ -d "${argocd_sample}" ] && [ -d "${project_sample}" ]; then
    run_case "addx-things-production-sample" 0 bash "${validator}" "${k8s_sample}" "${argocd_sample}" "${project_sample}"
else
    printf 'SKIP addx-things-production-sample: sample repositories not present\n'
fi

printf '%s passed, %s failed\n' "${pass_count}" "${fail_count}"
[ "${fail_count}" -eq 0 ]
