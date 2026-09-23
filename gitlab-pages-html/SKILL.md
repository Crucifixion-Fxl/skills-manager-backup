---
name: gitlab-pages-html
description: 把 HTML/CSS/JS 静态文件发布到 GitLab Pages（pages.addx.ai）得到可分享链接。当用户要托管 HTML demo、产品原型、交互前端页、方案演示，或说“把这个 HTML 发出去看”“发个 demo 链接”“配 pages”“更新 pages”时使用；也覆盖 URL 定制——去随机 hash、namespace 根路径（pages.addx.ai/{namespace}/）、按分支路径（/{branch-slug}/，CE 自组装 protected publisher 模式）。覆盖 addx 特有的必踩点（push 发布当前分支、MR 发布 destination branch、受保护 docs-pages publisher、publish 直接发已有目录、开 shared runner、sg-amd64 tag、Kyverno 合规 harbor 镜像、access 保持 private + 读者授 Guest、仅公司网可达、MR pipeline 注入 ci:init 要求 stages 含 test），通用 Pages 文档不会讲。Markdown 文档不用它——GitLab 仓库原生渲染 .md。
---

# 把 HTML 发到 GitLab Pages（addx）

## 描述

把 HTML/CSS/JS 静态文件发布成一个可分享的 `pages.addx.ai` 链接，零运维、零服务器。
**Markdown 不用它** —— GitLab 仓库里点开 `.md` 就原生渲染了。

**适用场景**：发 HTML demo / 产品原型 / 方案演示 / 带 JS 交互的前端页、把静态站托管成在线链接、更新已有 Pages。

**默认标准：每个 Git branch 都发布到自己的 `/<branch-slug>/` 路径，分享具体 HTML 时必须继续带上文件相对路径。** push 和 Merge Request 只是触发器：push 选择当前 branch，MR 选择 destination branch；两者最终进入同一个受保护的 `docs-pages` publisher。GitLab CE 由该 publisher 聚合所有 branch 快照，不能让后完成的普通 Pages job 覆盖其他分支。

## 标准：每个分支一个路径

实施前完整阅读 [`references/branch-pages-publisher.md`](references/branch-pages-publisher.md)，按以下顺序落地：

1. source pipeline 只用短期 `CI_JOB_TOKEN` 请求受保护的 publisher；push/MR 仅决定何时触发。
2. 触发器映射 ref：push 传 `CI_COMMIT_BRANCH`；MR 传 `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`，不能把 source branch 冒充保护分支文档。
3. protected `docs-pages` 串行维护 `public/<branch-slug>/`、branch map 和根索引，再发布聚合后的单个 Pages artifact。
4. 用 publisher job 与 Pages API 做服务端验收；浏览器或本机 `curl` 只用于查看，不能证明部署成功。
5. 分享单个 HTML 时，把仓库相对路径追加到 branch slug 后：产物 `public/<branch-slug>/<html-relative-path>` 对应 Page URL `<CI_PAGES_URL>/<branch-slug>/<html-relative-path>`；不得只分享 branch 根索引。

只有一次性 demo 或项目明确要求“后一次覆盖前一次”时，才使用下面的简化模式。

## 简化模式：单快照覆盖

HTML 演示/原型可走 `docs` 分支，不碰 `main`。推到 `docs` 才触发部署；`docs` 名字被 `docs/*` 占用时退 `pages-static`。以下编号步骤与 YAML 只适用于本简化模式。**已配置标准分支 publisher 的项目不得混用本模式；`docs-pages` 保留给标准 protected publisher。**

1. **选发布目录**：默认**直接发布仓库里已有的目录**（典型是 `docs/`）——pages job 用 `publish:` 指向原路径，**不复制、不新建 `public/`**。仓库没有现成目录、或只是几个零散 HTML 时，才退回新建 `public/` 收纳（子目录原样映射到 URL 路径）。
2. **检查 `docs*` 命名空间**：`git ls-remote origin 'docs*'`。若已存在 `docs/xxx` 形式分支（如 `docs/issue-185_...`），`refs/heads/docs` **建不出来**（见规则"分支"）；空闲则 `git checkout -b docs`，被占用走 **`pages-static`** 回退。
3. 加下面这个 `pages` job，`git push` 到 **`docs`**（或回退名 `pages-static`）→ 自动部署。
4. **授 Guest**：保持 `pages_access_level=private`（默认即是，**不要开 public**），把读者加成项目 **Guest** 成员（`POST /projects/:id/members`，`access_level=10`）。发链接的同时发授权，且**只授 Guest，不要更高角色**（见规则"access"）。
5. **确认部署成功**：看 GitLab 服务端证据 —— pages job `success` + `GET /api/v4/projects/:id/pages` 的 `deployments[0].created_at` 刷新成最新；**别靠本机 curl 的 HTTP 码**（见规则"确认部署"）。打开 `CI_PAGES_URL`（登录项目成员账号）只是给人看内容，不是验证手段。

```yaml
stages: [test, deploy]
pages:
  stage: deploy
  image: registry-harbor-sg.addx.live/base/maven:3.8.5-openjdk-17   # 见规则②
  tags: [sg-amd64]                                                   # 见规则②
  script: ['echo "published to $CI_PAGES_URL"']                     # 纯静态，空转即可
  publish: docs                    # 直接发已有目录（或 docs/<子目录>）；GitLab >=16.1 job 级关键字
  artifacts:
    paths: [docs]                  # 必须与 publish 一致
  rules:
    - if: '$CI_COMMIT_BRANCH == "docs"'   # docs 名被占用时同步改成 "pages-static"
```

> 发布目录里的 HTML 是你**提交进 git 的**，`script` 不负责“放文件”（checkout 自带）；
> “发布”= 这个 job 跑成功，push 到 docs 自动触发，没有独立的发布按钮。
> `publish` 两种写法实测都生效：job 级 `publish:`（推荐）与嵌套 `pages: {publish: ...}`。

## 规则

### addx 三处必须改对（否则 job 跑不通）★ 本 skill 核心价值

| # | 必改 | 不改的后果 |
|---|------|-----------|
| ① | 个人/新项目先开 `shared_runners_enabled`（默认关）| 没 runner 可用，job 一直 pending |
| ② | `tags: [sg-amd64]` | `us-tech-amd64` 是 group 专属，个人项目用不了 → pending |
| ② | `image: registry-harbor-sg.addx.live/base/maven:3.8.5-openjdk-17` | dockerhub 扁平镜像被 Kyverno 拦 → `runner_system_failure` |

开 shared runner：`PUT /api/v4/projects/:id`，body `{"shared_runners_enabled": true}`（需 Maintainer+）。
镜像虽 ~500MB，但 harbor 内网拉取 + 缓存，pages job **实测 6.7 秒**，不必嫌重。harbor `base/` 下
**没有 alpine/busybox/node 等轻量镜像**（实测 ErrImagePull），要轻量得先走 base-images 同步流程。

### 几条要点

- **单快照发布分支**：推到 **`docs` 分支**才发布（`rules` 限定），`main` 不发——HTML 演示/原型与生产代码隔离。
  注意 CE 是**单部署**，`pages` URL 仍是项目主 URL，docs 每次 push 覆盖上一次（已实证非默认分支可部署）。
- **ref 命名空间冲突（实战踩坑）**：git ref 是文件路径式命名空间，仓库已存在 `docs/xxx` 形式分支
  （如 `docs/issue-185_...`）时 `refs/heads/docs` **无法创建**，push 报 `cannot lock ref 'refs/heads/docs'` /
  `failed to update ref`。发布前先 `git ls-remote origin 'docs*'` 检查。被占用时：**已合并**的残留 `docs/*`
  分支可删除腾位；存在**未合并**的 `docs/*` 分支时**不要动别人的分支**，改用回退命名 **`pages-static`**
  （CI rules 同步改为 `$CI_COMMIT_BRANCH == "pages-static"`）。若项目已有标准 `docs-pages` publisher，必须使用标准模式，不得再开单快照分支。
  另：`git fetch` 的 FETCH_HEAD 是 **per-worktree** 的，多 worktree 仓库要在**同一 worktree** 内
  fetch + 建分支，或直接用 sha。
- **发布目录**：默认 `publish:` 指向**已有目录**（典型 `docs/`），不复制、不新建 `public/`。
  发布**子目录**（`publish: docs/<子目录>`）时 URL 根 = 该子目录；发布**整个 `docs/`** 时各子站挂在
  对应相对路径下。`artifacts.paths` 必须与 `publish` 一致。备选：零散 HTML 才新建 `public/` 收纳。
- **更新**：改发布目录里的 HTML → `git push` 到 **`docs`**（或 `pages-static`）→ **自动覆盖重部署，无需“发布”**。
  没看到更新只可能是：pipeline 没绿 / 浏览器缓存（强刷 Ctrl+Shift+R）。
- **access（策略：guest 可看，不开更高）**：`pages_access_level` **保持/设为 `private`**（默认即是），
  **不要设 `public` / `enabled`**。读者访问方式 = 加为项目 **Guest** 成员
  （`POST /projects/:id/members`，`access_level=10`），private Pages 对 Guest 及以上成员开放。
  发链接时同时给 reader 授 Guest，且**只授 Guest，不要更高角色**。
  `public` 的真实含义供权衡：pages.addx.ai 仅公司网可达，`public` = **全公司内网免登录可看**
  （不是互联网公开）；即便如此默认也不开，敏感内容**更不能设 public**，按需逐人授 Guest。

  Guest 在 private 项目能/不能（给授权者的安心丸——核心是代码权限不会给出去）：

  | Guest 能 | Guest 不能 |
  |---------|-----------|
  | 访问 private Pages（本 skill 的目的） | **读仓库代码 / clone / pull / 浏览文件** |
  | 查看与创建 issue、评论 | 查看 Merge Request 与 diff |
  | 查看 milestones / releases | 查看 CI/CD 流水线与 job 日志、下载 artifacts |
  | 查看 wiki（若开放） | push 任何分支、跑 pipeline、改任何设置 |

- **可达性**：Pages **只在公司网络可达**，对公网超时。发链接前确认对方在公司网。验证“是否公网可达”
  **别用本机 curl / WebFetch**（出口在公司网，会误判成已暴露）→ 用 `check-host.net` 多节点 + example.com 对照。
  探测会把目标发给外部服务，**敏感内部项目别把真实内部 URL 直接喂给外部探测站**，用对照站点确认链路连通性即可。
- **确认部署成功（重要）**：权威证据**只有** ① pages job `success` ② `GET /api/v4/projects/:id/pages` 的 `deployments[0].created_at` 刷新成这次部署的最新时间。
  ❌ **绝不能用本机 curl pages URL 的 HTTP 码判断部署是否成功** —— private Pages 的 access control 是**前置**的，内容**存不存在都先返回 `302 → /projects/auth`**（存在和不存在的项目 302 一模一样），`302` 跟"内容部署了没"完全无关；`200` 也只在 public + 已登录时才有意义。验证部署一律看服务端 job/API，不看 curl 状态码。
- **URL**：一律用 **`CI_PAGES_URL`**，别硬拼。`is_unique_domain_enabled` 因项目而异
  （有的 `pages.addx.ai/<group>/<project>`，有的带随机 hash），不可预测。
- **提交大 HTML**：内容很大时别走 shell heredoc（大量引号会崩），用“写文件 → 读取提交”。

### URL 结构（分支路径 → 去 hash → namespace 根路径）

三档递进，全部在本实例（GitLab 18.0 CE）实证；参照实现：`design-system/design-system.pages.addx.ai`。

| URL 目标 | 做法 |
|-----------|------|
| `<CI_PAGES_URL>/<branch-slug>/<html-relative-path>`（标准文档链接） | protected publisher 增量写入 `public/<branch-slug>/<html-relative-path>`，再把聚合后的 `public/` 作为一次 Pages deployment 发布；`public/` 是 artifact 根，不出现在外部 URL 中 |
| `pages.addx.ai/<group>/<project>/`（去随机 hash） | 关 unique domain：`glab api --method PATCH "projects/:id/pages" -F pages_unique_domain_enabled=false`（Maintainer+；API 返回的新 URL 立即成为 canonical；旧 hash URL 可能暂时命中登录前置或缓存，不再作为有效分享地址） |
| `pages.addx.ai/<ns>/`（namespace 根路径，无项目段） | namespace-website 命名规则：顶级 group `<ns>` 下建项目，项目名必须是 **`<ns>.pages.addx.ai`**，再关 unique domain，URL 即变 `pages.addx.ai/<ns>/` |

**按分支路径的 publisher 模式**默认只有一个标准拓扑，并保留一个权限升级路径：

- **标准：同项目 protected `docs-pages` branch**。源 pipeline 只用短期 `CI_JOB_TOKEN` 请求 publisher；受保护分支持有确定性发布脚本，并在串行 job 中增量维护 `public/<branch-slug>/`。
- **升级路径：受控 broker**。只有必须让普通 Developer 自动触发，而 GitLab CE 的同项目 protected-ref 权限阻止该路径时才使用；它不是与标准方案并列的默认选项。

标准拓扑与升级路径的 ref 语义、安全边界、完整 CI 模板、slug 冲突处理和验证清单统一见
[`references/branch-pages-publisher.md`](references/branch-pages-publisher.md)。

### MR pipeline 大坑：实例注入 ci:init，stages 必须含 test

实例会向 **merge_request_event pipeline** 注入 `ci:init`（stage `test`）+ `global:credentials-scan` / `global:repo-boundary-lint`（均 stage `.pre`）+ `global:code-review`（stage `.post`，仅目标分支 protected 时）。
注入清单以 `addx:api-synthetic-monitoring` 的 [`references/ci-templates-contract.md`](../api-synthetic-monitoring/references/ci-templates-contract.md) 为准。
（本仓 `engineering/skills` 在 `repo-boundary-lint` 的黑名单里、在 `code-review` 的 warn-only 档。）
仓库 `stages` 里**没有 `test`** 时，MR pipeline **创建即 failed**：零 job、`yaml_errors: null`、CI Lint 显示 valid——只有 GraphQL
`pipeline(iid:"..."){ errorMessages { nodes { content } } }` 能看到真实报错（`chosen stage test does not exist`）。
分支 pipeline 完全正常，所以极难定位；开了「pipelines must succeed」的仓 MR 会**永远合不了**。
**修法：`stages` 必含 `test`**，如 `stages: [check, test, deploy]`。只有 pages / trigger job 的纯发布仓同样中招。

### 权限（分层）

| 做什么 | 最低角色 |
|--------|---------|
| 读者：看 private Pages | Guest（**只授这个，不要更高**）|
| 提交配置 / 走 MR / 跑 pipeline / push docs 分支 | Developer |
| 开 shared runner / 改 access / 加 Guest 成员 | Maintainer |
| 自己的个人项目 | 你是 Owner，最省事 |

## 示例

### ❌ Bad

```yaml
pages:
  image: python:3.11-slim        # dockerhub 扁平镜像 → Kyverno 拦 → runner_system_failure
  tags: [us-tech-amd64]          # group 专属 runner，个人项目用不了 → 永久 pending
  script: ['cp -r docs public']  # 多此一举：发已有目录直接 publish，不要复制进 public/
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'   # 把 HTML 演示混进生产分支
  artifacts: {paths: [public]}
```
外加项目没开 `shared_runners_enabled` → 连 runner 都没有；没检查 `docs*` 命名空间就建分支 →
push 报 `cannot lock ref 'refs/heads/docs'` 折腾半天；为了让同事能看把 `pages_access_level`
设成 `public` → 全公司内网免登录可见。随后用本机 `curl pages.addx.ai/...` 看到 200 →
误判“已公网可达，内容泄露了”。

### ✅ Good

```yaml
stages: [test, deploy]
pages:
  stage: deploy
  image: registry-harbor-sg.addx.live/base/maven:3.8.5-openjdk-17   # 合规 harbor 镜像
  tags: [sg-amd64]                                                   # instance shared runner
  script: ['echo "published to $CI_PAGES_URL"']
  publish: docs                    # 直接发已有目录，不复制、不新建 public/
  artifacts:
    paths: [docs]                  # 与 publish 一致
  rules:
    - if: '$CI_COMMIT_BRANCH == "docs"'   # docs 名被占时改 "pages-static"
```
先 `git ls-remote origin 'docs*'` 确认命名空间（被未合并 `docs/*` 占用就改用 `pages-static` 分支 +
同步改 rules），`PUT /projects/:id {shared_runners_enabled:true}`，push 后保持
`pages_access_level=private`，给读者 `POST /projects/:id/members` 授 **Guest**（access_level=10），
用 `check-host.net` + example.com 对照验证真实可达性。

## References

- **原理**：Pages = 「名为 `pages` 的 job + artifact（默认 `public` 目录，`publish:` 可改成任意已有目录）」，
  job 成功后 GitLab 后台自动发布（发布动作不在 script 里）。artifacts 里的 HTML 会被 pages daemon
  安全隔离渲染（那个“redirected away from GitLab”警告），但会随 artifact 过期，持久要真发 Pages。
- 官方文档：[Pages](https://docs.gitlab.com/user/project/pages/) ·
  [access control](https://docs.gitlab.com/user/project/pages/pages_access_control/)
- 分支发布实施手册：[`references/branch-pages-publisher.md`](references/branch-pages-publisher.md)
- 实战参考：`xzhou1/html-pages-demo`（纯静态 / 复杂前端 / 更新 / 多分支 / docs 分支部署 全场景实证）
- 历史单快照案例：`applications/golf` 曾用 **`docs-pages` 分支 + `publish: docs`** 发布——`docs` 名字被
  未合并的 `docs/*` 分支占用的真实案例。新接入的标准方案中 `docs-pages` 专用于 protected publisher，不复制该历史命名。
- 历史非标准案例：`design-system/design-system.pages.addx.ai` 曾使用独立薄项目、只读 token 全量组装；仅供理解 namespace 根路径，不作为当前同项目 protected publisher 的实施模板。
- 配套：`gitlab-ci`、`gitlab-mr`、`harbor`
