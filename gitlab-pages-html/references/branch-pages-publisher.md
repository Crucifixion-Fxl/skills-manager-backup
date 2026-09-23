# GitLab CE 基于分支的 Pages 发布

本手册用于一个 GitLab Pages 项目同时保留多个 Git ref 的静态文档：

```text
<CI_PAGES_URL>/main/docs/architecture/overview.html
<CI_PAGES_URL>/docs-agent-identity-vaultwarden/docs/architecture/agent-identity.html
<CI_PAGES_URL>/feature-x/docs/demo/index.html
```

GitLab CE 没有可用的原生 `pages.path_prefix` parallel deployments 时，不能让每个分支各自运行普通 `pages` job：后完成的 job 会覆盖前一个部署。可改用一个受保护 publisher，把多个 ref 的 `docs/` 聚合成一次 `public/` 部署。

具体文件的路径映射是：

```text
通用发布产物: public/<branch-slug>/<html-relative-path>
通用 Page URL: <CI_PAGES_URL>/<branch-slug>/<html-relative-path>

仓库文件: docs/architecture/example.html
发布产物: public/<branch-slug>/docs/architecture/example.html
Page URL: <CI_PAGES_URL>/<branch-slug>/docs/architecture/example.html
```

`public/` 是 Pages artifact 的发布根，不会成为外部 URL 的字面路径段。根索引 `<CI_PAGES_URL>/<branch-slug>/` 只能用于浏览分支快照；给 Issue、Task 或 reviewer 的链接必须指向 `<html-relative-path>` 对应的具体 HTML 页面。

## 1. 先固定触发器到 ref 的映射

push 和 MR 只是触发器，底层始终发布 branch snapshot。两个触发器必须映射到以下 ref：

| 触发事件 | `PUBLISH_REF` | 原因 |
|---|---|---|
| 普通 branch push | `CI_COMMIT_BRANCH` | 展示该开发分支已经提交的文档 |
| Merge Request pipeline | `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` | 展示 MR destination 的受保护分支文档；不能把尚未合并的 source branch 冒充保护分支 |

例如 `docs/change-a → main` 的 MR：

- branch pipeline 更新 `/docs-change-a/`；
- MR pipeline 更新 `/main/`，内容从真实 `main` 拉取；
- MR pipeline 禁止使用 `CI_MERGE_REQUEST_SOURCE_BRANCH_NAME`。

这与“用 MR source 构建 review app”是不同产品语义。若需求明确是预览未合并内容，应另建 review-app 流程，不要复用“保护分支文档”路径。

## 2. 推荐拓扑：同项目 protected publisher

```text
source branch / MR pipeline
  │  ephemeral CI_JOB_TOKEN + PUBLISH_REF
  ▼
POST /projects/:id/trigger/pipeline  ref=docs-pages
  │
  ▼
protected docs-pages pipeline
  │  trusted script + protected publisher credential
  ├─ validate ref and slug
  ├─ fetch refs/heads/<PUBLISH_REF>:docs
  ├─ replace public/<slug>/ only
  ├─ update public/.branch-map.tsv and root index
  ├─ commit aggregate with [skip ci]
  └─ publish public/ as the single Pages deployment
```

信任边界：source pipeline 可以请求“发布哪个 ref”，但不能获得 publisher credential，也不能提供或覆盖 publisher 脚本。真正执行写入的代码只来自受保护的 `docs-pages` branch。

### 2.1 Source branch 的 request job

把以下 job 放在正常开发分支的 `.gitlab-ci.yml`。先明确发布门禁，再选择 `needs`：

- 文档刷新与项目测试无关：保留模板中的 `needs: []`，request job 可直接执行。
- 必须测试通过才发布：删除 `needs: []` 依赖 stage 顺序，或把它改成必须成功的精确 test job 列表。不要在文档里声明要等测试，却留下会绕过依赖的 `needs: []`。

```yaml
stages: [test, build, deploy]

pages:request:
  stage: deploy
  needs: []
  image: registry-harbor-sg.addx.live/base/maven:3.8.5-openjdk-17
  tags: [sg-amd64]
  script:
    - |
      test -n "${PUBLISH_REF:?}"
      test -n "${PUBLISH_TRIGGER_SOURCE:?}"
      response_file=$(mktemp)
      trap 'rm -f "${response_file}"' EXIT
      status=$(curl --show-error --silent \
        --connect-timeout 10 --max-time 60 \
        --output "${response_file}" --write-out '%{http_code}' \
        --request POST \
        --form-string "token=${CI_JOB_TOKEN:?}" \
        --form-string "ref=docs-pages" \
        --form-string "variables[PUBLISH_REF]=${PUBLISH_REF}" \
        --form-string "variables[PUBLISH_TRIGGER_SOURCE]=${PUBLISH_TRIGGER_SOURCE}" \
        "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/trigger/pipeline")
      case "${status}" in
        2*) ;;
        *) cat "${response_file}" >&2; exit 1 ;;
      esac
      echo "requested protected Pages publisher for ${PUBLISH_REF}"
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      changes: [docs/**/*, README.md, .gitlab-ci.yml]
      variables:
        PUBLISH_REF: $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
        PUBLISH_TRIGGER_SOURCE: merge_request_event
    - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH != "docs-pages"'
      changes: [docs/**/*, README.md, .gitlab-ci.yml]
      variables:
        PUBLISH_REF: $CI_COMMIT_BRANCH
        PUBLISH_TRIGGER_SOURCE: push
```

不要在 source job 中放 `PAGES_PUBLISH_TOKEN`、静态 pipeline trigger token 或组装脚本。不要把 token 拼进 Git URL；否则 job trace、错误信息或进程参数可能泄露它。
trigger request 必须同时设置连接和总时限；模板使用 10 秒连接、60 秒总时限。不要给该 POST 无条件加自动重试：服务端已受理但响应丢失时，重试会重复触发 publisher。

同项目调用不要改写成 `trigger:project` bridge；该关键字面向 multi-project downstream，实例/版本组合可能对 same-project ref 返回 `invalid_bridge_trigger`。这里使用 pipeline trigger API，并让 `CI_JOB_TOKEN` 建立 downstream 关联。

### 2.2 `docs-pages` branch 的 publisher job

一次性准备：

1. 从可信基线创建 `docs-pages`，放入 publisher CI；把本 Skill 随包的 [`../scripts/build-branch-pages.sh`](../scripts/build-branch-pages.sh) 复制为项目的 `scripts/build-branch-pages.sh`，review 后随分支保护起来。
2. 将 `docs-pages` 设为 protected，禁止 force push，push/merge 至少限制为 Maintainer。
3. 创建 access level 为 Maintainer、scope 只含 `write_repository` 的 project access token；作为 `PAGES_PUBLISH_TOKEN` 保存为 masked + protected variable，并把 environment scope 限制为 `pages-publisher`。scope 不替代 role：Developer token 无法写入 Maintainer-only protected branch。
4. 保持 “Allow Git push requests to the repository” for `CI_JOB_TOKEN` 关闭。聚合提交只能由 publisher credential 写入。
5. 若实例向 pipeline 注入外部 CI template，确保被引用的 template project 只 allowlist 必要 source project 的 job token。
6. publisher branch 明确拥有聚合产物 `public/`。模板使用 `git add -f -- public`，即使 source 基线的 `.gitignore` 含 `public/*`，也只强制 stage 这个确定目标；禁止扩大为 `git add -f .`。

publisher job 的核心形态：

```yaml
stages: [test, build, deploy]

pages:
  stage: deploy
  image: registry-harbor-sg.addx.live/base/maven:3.8.5-openjdk-17
  tags: [sg-amd64]
  environment:
    name: pages-publisher
  resource_group: pages-publisher
  script:
    - |
      test -n "${PAGES_PUBLISH_TOKEN:?}"
      askpass=$(mktemp)
      trap 'rm -f "${askpass}"' EXIT
      printf '%s\n' '#!/bin/sh' 'printf "%s\\n" "${PAGES_PUBLISH_TOKEN:?}"' > "${askpass}"
      chmod 700 "${askpass}"
      export GIT_ASKPASS="${askpass}" GIT_TERMINAL_PROMPT=0
      git remote set-url origin "https://oauth2@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git"
      git pull --ff-only --quiet origin docs-pages
      bash scripts/build-branch-pages.sh
      git config user.name "Pages Publisher"
      git config user.email "pages-publisher@example.invalid"
      git add -f -- public
      if ! git diff --cached --quiet; then
        git commit --quiet -m "docs(pages): publish ${PUBLISH_REF} [skip ci]"
        git push --quiet origin HEAD:docs-pages
      fi
    - echo "published ${PUBLISH_REF} to ${CI_PAGES_URL}"
  pages:
    publish: public
  artifacts:
    expire_in: never
    paths: [public]
  rules:
    - if: '$CI_COMMIT_BRANCH == "docs-pages" && $CI_PIPELINE_SOURCE == "pipeline" && $PUBLISH_REF'
```

`resource_group` 是必要条件：多个 branch/MR 同时触发时必须串行更新聚合分支，否则后写入者会覆盖先写入者。

### 2.3 Build script 的确定性契约

`scripts/build-branch-pages.sh` 不接受任意 Git URL 或路径，只接受 `PUBLISH_REF`，并必须满足：

1. 用 `git check-ref-format --branch "$PUBLISH_REF"` 验证 ref，拒绝换行和回车。
2. 生成与 `CI_COMMIT_REF_SLUG` 相同语义的 slug：小写、非字母数字转 `-`、去首尾 `-`、最长 63 字符。
3. slug 为空时 fail closed；不得让目标路径退化为 `public/` 根目录。
4. 在 `public/.branch-map.tsv` 保存 `slug<TAB>raw-ref`。同一 slug 已属于其他 raw ref 时 fail closed，禁止静默覆盖。
5. 只 fetch `refs/heads/$PUBLISH_REF`，并用 `git cat-file -t` 验证该 ref 的 `docs` 对象确实是 tree；普通文件或 symlink 必须 fail closed。
6. 只替换 `public/$slug/`，保留其他分支快照。
7. 对 root index 中的 ref 做 HTML escaping，再生成相对链接；不得把 raw ref 直接拼进 HTML。
8. 构建产物稳定时不产生空提交；有效更新提交必须带 `[skip ci]`，避免 push 后递归触发。
9. 构建临时目录使用任务专用名称并在结束时清理，不删除 workspace 根目录或未解析路径。

分支删除不会天然触发 source pipeline。可在后续任意发布时对 branch map 做远端存在性对账，或提供仅限管理员的显式 cleanup job；删除目录时必须先核对 raw ref 与 slug 映射。

## 3. 权限边界与 CE 限制

protected downstream pipeline 以触发用户权限进行校验。当前 GitLab CE 形态下：

- Maintainer/Owner 请求 protected `docs-pages` 可以自动发布；
- Developer 没有启动该 protected ref pipeline 的权限时，请求 fail closed；
- 不要为了绕过它把长期 trigger token 暴露给 source pipeline。

如果必须让所有 Developer 自动发布，使用外部受控 broker。broker 验证 project、event、source/target ref 后，以有权启动 protected publisher 的受限身份发起请求。job-token allowlist 只允许 token 到达目标项目，不会提升触发用户的项目角色，因此单纯换成独立 publisher project 不能解决权限问题。

GitLab CE 的 project access token 不能按 branch 限定 `write_repository`。同项目方案靠四层收敛风险：

1. publisher token 仅出现在 protected ref；
2. variable environment scope 仅 `pages-publisher`；
3. publisher 脚本来自 protected branch 且 push 目标固定为 `docs-pages`；
4. source pipeline 只持有短期 `CI_JOB_TOKEN`。

## 4. URL 与 Unique Domain

始终从 Pages API 或 `CI_PAGES_URL` 读取主 URL，禁止硬拼：

```bash
glab api projects/:id/pages
```

新项目通常启用 Unique Domain，URL 可能为 `pages.addx.ai/<project>-<random>/`。它隔离同 group 项目的 cookie。若确定需要稳定的 namespace/project 路径，可由 Maintainer 关闭：

```bash
glab api --method PATCH projects/:id/pages \
  -F pages_unique_domain_enabled=false
```

以 API 返回的 `url` 和 `is_unique_domain_enabled` 为权威。切换后旧 URL 不再是 canonical；登录前置、代理或缓存可能仍返回响应，不能据此继续分享旧地址。

## 5. 验证清单

不要只看浏览器或本机 `curl`。至少保留以下服务端证据：

- source branch pipeline：request job 成功，日志只显示 ref，不显示 credential；
- MR pipeline：日志明确为 destination，例如 `requested ... for main`；
- downstream pipeline：branch 场景日志为 `fetch ... <current branch>`，MR 场景为 `fetch ... <target branch>`；
- publisher job 成功，Pages API 的 `deployments[0].created_at` 已刷新；
- `docs-pages` 新提交只改变目标 slug、map 和 root index；
- 页面根索引同时保留多个已发布 ref；
- source branch/MR job 环境中没有 `PAGES_PUBLISH_TOKEN`；
- `docs-pages` 仍 protected，force push 关闭，publisher variable 为 masked + protected + environment-scoped；
- 对 MR source/target 语义、无长期 token、无 token-in-URL、publisher ref 排除规则建立静态契约测试。

## 6. 恢复与轮换

- 误发布：先停新的 request job；在 `docs-pages` 对错误聚合提交执行 `git revert`，不要 force push，再从正确 ref 重跑。
- slug 冲突：修正分支命名或引入明确映射；禁止手工覆盖 `.branch-map.tsv`。
- 并发 push 冲突：保留失败证据，从最新 `docs-pages` 重跑；不要用 force push。
- token 到期或疑似泄露：先 revoke，签发同等或更小 scope 的替代 token，更新 protected variable，再验证 publisher；不要把旧 token 写入 issue、MR 或日志。

## 7. Developer 自动化的升级路径

同项目 protected `docs-pages` 是标准拓扑。受控 broker 只是 GitLab CE 权限边界下的升级路径，不应在标准方案可用时增加额外系统。

下列任一成立时才引入 broker：

- Developer 必须在不具备 source project Maintainer 权限时自动发布；
- source 项目的 CI 可编辑者范围过大，无法把 publisher ref 和 environment 可靠隔离；
- 组织要求 write credential 与 source repository 完全分离；
- 需要集中托管多个 source repository 的静态站。

broker 必须固定允许的 source project、pipeline source、target publisher 与 ref 语义，并记录审计事件。即使 publisher 放在独立项目，也必须显式给 broker 身份目标项目的最低必要角色；job-token allowlist 本身不授予该角色。

## References

- [GitLab Pages](https://docs.gitlab.com/user/project/pages/)
- [Pages parallel deployments](https://docs.gitlab.com/user/project/pages/parallel_deployments/)
- [Merge request pipelines](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/)
- [Downstream pipelines](https://docs.gitlab.com/ci/pipelines/downstream_pipelines/)
- [Downstream pipeline troubleshooting](https://docs.gitlab.com/ci/pipelines/downstream_pipelines_troubleshooting/)
- [CI job tokens](https://docs.gitlab.com/ci/jobs/ci_job_token/)
- [Pipeline trigger API](https://docs.gitlab.com/api/pipeline_triggers/)
- [Protected branches API](https://docs.gitlab.com/api/protected_branches/)
- [Project access tokens API](https://docs.gitlab.com/api/project_access_tokens/)
