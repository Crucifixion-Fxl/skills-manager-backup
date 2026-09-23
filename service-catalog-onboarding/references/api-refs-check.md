# api-refs-check —— 引用解析校验（用 service-catalog-search）

实际可执行脚本：[`../scripts/api-drift/check-api-refs.sh`](../scripts/api-drift/check-api-refs.sh)。
.gitlab-ci.yml job 范本：[`../scripts/gitlab-ci-snippets/api-drift.yml`](../scripts/gitlab-ci-snippets/api-drift.yml)（含 `api:lint:refs-resolve` job）。
原理 + 设计选型见 SKILL.md §5.1。

`service-catalog-search` skill 上线后把 `check-api-refs.sh` 里 `curl ... | jq` 那段换成：

```bash
service-catalog-search lookup --kind API --name "$ref" --exact
```

skill 内部封装 catalog REST API + 离线降级（catalog 不可达时读 GitLab 仓的 `catalog-info.yaml`）。
