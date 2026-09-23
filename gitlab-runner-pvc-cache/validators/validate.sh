#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 <k8s-gitlab-runner-dir|/dev/null> <argocd-cluster-dir|/dev/null> <project-dir|/dev/null>" >&2
}

if [ "$#" -ne 3 ]; then
    usage
    exit 2
fi

k8s_dir="$1"
argocd_dir="$2"
project_dir="$3"
failures=0

fail() {
    failures=$((failures + 1))
    echo "FAIL: $*" >&2
}

has_yq() {
    command -v yq >/dev/null 2>&1
}

scan_placeholders() {
    local dir="$1"
    [ -d "${dir}" ] || return 0
    local matches_a="" matches_b="" rc=0

    matches_a="$(grep -RIn 'REPLACE_WITH_' "${dir}" --include='*.yaml' --include='*.yml' 2>&1)" || rc=$?
    if [ "${rc}" -ge 2 ]; then
        fail "grep failed while scanning REPLACE_WITH_ placeholders under ${dir}:"
        echo "${matches_a}" >&2
        return 0
    fi

    rc=0
    matches_b="$(grep -RInF '{{' "${dir}" --include='*.yaml' --include='*.yml' 2>&1)" || rc=$?
    if [ "${rc}" -ge 2 ]; then
        fail "grep failed while scanning {{ placeholders under ${dir}:"
        echo "${matches_b}" >&2
        return 0
    fi
    if [ -n "${matches_b}" ]; then
        matches_b="$(printf '%s\n' "${matches_b}" | grep -F '}}' || true)"
    fi

    local matches="${matches_a}"
    if [ -n "${matches_b}" ]; then
        matches="${matches}${matches:+
}${matches_b}"
    fi
    if [ -n "${matches}" ]; then
        fail "unreplaced placeholders found under ${dir}:"
        echo "${matches}" >&2
    fi
}

check_pvc_rwx() {
    local dir="$1"
    [ -d "${dir}" ] || return 0
    local pvc_files
    pvc_files="$(grep -RIl '^kind:[[:space:]]*PersistentVolumeClaim' "${dir}" --include='*.yaml' --include='*.yml' 2>/dev/null || true)"
    [ -n "${pvc_files}" ] || return 0
    while IFS= read -r file; do
        [ -n "${file}" ] || continue
        if ! grep -Eq '^[[:space:]]*-[[:space:]]*ReadWriteMany[[:space:]]*$' "${file}"; then
            fail "PVC does not declare ReadWriteMany: ${file}"
        fi
        if grep -Eq 'storageClassName:[[:space:]]*(REPLACE_WITH|""|$)' "${file}"; then
            fail "PVC storageClassName is empty or placeholder: ${file}"
        fi
    done <<< "${pvc_files}"
}

check_sync_wave_quoted() {
    local dir="$1"
    [ -d "${dir}" ] || return 0
    local app_files
    app_files="$(grep -RIl '^kind:[[:space:]]*Application' "${dir}" --include='*.yaml' --include='*.yml' 2>/dev/null || true)"
    [ -n "${app_files}" ] || return 0
    while IFS= read -r file; do
        [ -n "${file}" ] || continue
        if grep -q 'argocd.argoproj.io/sync-wave:' "${file}"; then
            if ! grep -Eq 'argocd.argoproj.io/sync-wave:[[:space:]]*"[-0-9]+"' "${file}"; then
                fail "sync-wave must be a quoted string in ${file}"
            fi
        fi
    done <<< "${app_files}"
}

check_runner_tag_alignment() {
    [ -d "${k8s_dir}" ] || return 0
    [ -d "${project_dir}" ] || return 0
    local tags
    tags="$(grep -RhoE 'tags:[[:space:]]*"[^"]+"' "${k8s_dir}" --include='*.yaml' --include='*.yml' 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/' | sort -u || true)"
    [ -n "${tags}" ] || return 0
    while IFS= read -r tag; do
        [ -n "${tag}" ] || continue
        if ! find "${project_dir}" \
            \( -path "${project_dir}/.git" -o -path "${project_dir}/node_modules" -o -path "${project_dir}/bazel-*" \) -prune -o \
            -type f \( -name '.gitlab-ci.yml' -o -path '*/.gitlab-ci/*.yml' -o -path '*/.gitlab/*.yml' -o -path '*/ci/*.yml' -o -path '*/ci/*.yaml' \) \
            -exec grep -qs -- "${tag}" {} + 2>/dev/null; then
            echo "WARN: runner tag from values not found in common project CI files: ${tag}" >&2
            echo "      This can be ignored for unrelated runner releases or unusual include paths." >&2
        fi
    done <<< "${tags}"
}

check_application_path_collisions() {
    local dir="$1"
    [ -d "${dir}" ] || return 0
    if ! has_yq; then
        echo "WARN: yq not found; skipping Application path collision semantic check" >&2
        return 0
    fi
    local paths
    paths="$(find "${dir}" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 |
        xargs -0 -r yq -r 'select(.kind == "Application") | .spec.source.path // empty' 2>/dev/null |
        sort | uniq -d || true)"
    if [ -n "${paths}" ]; then
        fail "multiple Applications use the same spec.source.path; verify directory-mode ownership:"
        echo "${paths}" >&2
    fi
}

scan_placeholders "${k8s_dir}"
scan_placeholders "${argocd_dir}"
check_pvc_rwx "${k8s_dir}"
check_sync_wave_quoted "${argocd_dir}"
check_runner_tag_alignment
check_application_path_collisions "${argocd_dir}"

if [ "${failures}" -ne 0 ]; then
    echo "Validation failed with ${failures} issue(s)." >&2
    exit 1
fi

echo "Validation passed."
