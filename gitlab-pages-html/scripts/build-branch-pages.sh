#!/usr/bin/env bash
# Incrementally assemble docs snapshots at public/<branch-slug>/docs/ on GitLab CE.
set -euo pipefail

pages_source_ref="${PUBLISH_REF:?PUBLISH_REF is required}"
pages_map_file="public/.branch-map.tsv"
pages_site_title="${PAGES_SITE_TITLE:-Branch documentation}"

case "${pages_source_ref}" in
  *$'\n'*|*$'\r'*) echo "invalid source ref" >&2; exit 1 ;;
esac
if ! git check-ref-format --branch "${pages_source_ref}" >/dev/null; then
  echo "invalid source ref" >&2
  exit 1
fi

pages_slug=$(printf '%s' "${pages_source_ref}" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
  | cut -c1-63 \
  | sed -E 's/-+$//')
if [ -z "${pages_slug}" ]; then
  echo "source ref produced an empty slug" >&2
  exit 1
fi

pages_work_dir=$(mktemp -d)
trap 'rm -rf "${pages_work_dir}"' EXIT
mkdir -p public "${pages_work_dir}/source"
touch "${pages_map_file}"

pages_existing_ref=$(awk -F '\t' -v slug="${pages_slug}" '$1 == slug {print $2; exit}' "${pages_map_file}")
if [ -n "${pages_existing_ref}" ] && [ "${pages_existing_ref}" != "${pages_source_ref}" ]; then
  echo "slug collision: ${pages_source_ref} and ${pages_existing_ref} both map to ${pages_slug}" >&2
  exit 1
fi

echo "fetch docs from source ref ${pages_source_ref}"
git fetch --quiet origin "refs/heads/${pages_source_ref}"
pages_docs_type=$(git cat-file -t FETCH_HEAD:docs 2>/dev/null || true)
if [ "${pages_docs_type}" != "tree" ]; then
  echo "ref ${pages_source_ref} has no docs directory" >&2
  exit 1
fi
git archive FETCH_HEAD docs | tar -x -C "${pages_work_dir}/source"

pages_branch_target="public/${pages_slug}"
pages_target="${pages_branch_target}/docs"
rm -rf "${pages_branch_target}"
mkdir -p "${pages_branch_target}"
mv "${pages_work_dir}/source/docs" "${pages_target}"

awk -F '\t' -v slug="${pages_slug}" '$1 != slug' "${pages_map_file}" > "${pages_work_dir}/branch-map.next"
printf '%s\t%s\n' "${pages_slug}" "${pages_source_ref}" >> "${pages_work_dir}/branch-map.next"
sort -t $'\t' -k2,2 "${pages_work_dir}/branch-map.next" > "${pages_map_file}"

pages_rows="${pages_work_dir}/rows.html"
: > "${pages_rows}"
while IFS=$'\t' read -r pages_row_slug pages_row_ref; do
  [ -n "${pages_row_slug}" ] || continue
  pages_escaped_ref=$(printf '%s' "${pages_row_ref}" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g')
  printf '<li><a href="%s/docs/">%s</a> <code>/%s/docs/</code></li>\n' \
    "${pages_row_slug}" "${pages_escaped_ref}" "${pages_row_slug}" >> "${pages_rows}"
done < "${pages_map_file}"

pages_escaped_title=$(printf '%s' "${pages_site_title}" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g')
{
  printf '%s\n' '<!doctype html>' '<html lang="en"><head><meta charset="utf-8">'
  printf '<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title></head>\n' "${pages_escaped_title}"
  printf '<body><main><h1>%s</h1><p>Published Git branch snapshots.</p><ul>\n' "${pages_escaped_title}"
  cat "${pages_rows}"
  printf '%s\n' '</ul></main></body></html>'
} > public/index.html

echo "assembled ${pages_source_ref} -> /${pages_slug}/docs/"
