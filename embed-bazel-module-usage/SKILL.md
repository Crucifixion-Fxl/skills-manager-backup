---
name: embed-bazel-module-usage
description: Use when embedded firmware teams need guidance for creating, publishing, registering, consuming, or locally overriding internal Bazel modules, especially with GitLab CI, Nexus source tarballs, GitLab Pages Bazel registries, MODULE.bazel, .bazelrc common --registry config, and --override_module/local_path_override workflows.
---

# embed-bazel-module-usage

面向嵌入式开发的 Bazel module 使用指导。覆盖新建 module、配置 CI 发布 Nexus 制品、向 bazel-registry 提交版本信息、通过 GitLab Pages 提供 registry、下游项目使用 `bazel_dep`，以及用 override 完成本地开发验证。

## Description

整体链路分为四类仓库或服务：

| 角色 | 职责 | 参考示例 |
|------|------|----------|
| Bazel module 仓库 | 维护源码、`MODULE.bazel`、`BUILD.bazel`、测试和发布 CI | `https://gitlab.addx.ai/firmware/cluster/addx_demo_module.git` |
| Nexus | 保存 module 源码 tarball；路径由团队自行约定 | 可参考 `bazel-package/<module>/<version>.tar.gz` |
| Bazel registry 仓库 | 维护 `modules/<name>/<version>/` 元数据，并通过 MR 审核版本变更 | `https://gitlab.addx.ai/firmware/building/bazel-registry.git` |
| GitLab Pages | 将 registry 静态目录暴露为 Bazel `--registry=<url>` 来源 | `https://pages.addx.ai/<bazel-registry-pages>` |

流程说明文档可参考：`https://pages.addx.ai/addx-demo-module-42a2bb/docs/flow-demo.html`。它是 demo module 的说明页，不是可直接传给 Bazel 的 `--registry` URL。

标准流程：

1. 在独立 GitLab 仓库中创建 Bazel module。
2. module 仓库 tag pipeline 生成可复现源码 tarball，并发布到 Nexus。
3. module 仓库 CI 自动向 bazel-registry 仓库提交 MR，追加新版本元数据。
4. bazel-registry MR 合并后，GitLab Pages 更新 registry 静态内容。
5. 下游项目在 `MODULE.bazel` 使用 `bazel_dep`，在 `.bazelrc` 使用 `common --registry=...` 固化 registry 顺序。
6. module 开发者使用 `--override_module` 或 `local_path_override` 做本地联调。

不要把 Nexus 路径、registry 仓库、GitLab Pages URL 写成唯一公司标准。示例链接只作为参考，落地时必须根据目标团队实际仓库和制品路径调整。

## Rules

### Rule 1 - 创建新的 Bazel module

新 module 仓库最小结构：

```text
<module>/
├── .bazelversion
├── MODULE.bazel
├── BUILD.bazel
├── include/
├── src/
├── tests/
├── ci/
│   ├── package-release.sh
│   └── create-registry-mr.sh
└── .gitlab-ci.yml
```

`MODULE.bazel` 必须声明稳定的 module 名称和版本：

```starlark
module(
    name = "addx_xxx",
    version = "0.1.0",
    compatibility_level = 1,
)
```

最小公开 target 示例：

```starlark
cc_library(
    name = "addx_xxx",
    srcs = ["src/addx_xxx.c"],
    hdrs = ["include/addx_xxx/addx_xxx.h"],
    includes = ["include"],
    visibility = ["//visibility:public"],
)
```

命名和版本规则：

- module 名称建议等于 Git 仓库名，这是公司约定，不是 Bazel 原生限制。
- release tag 推荐严格使用 `x.y.z`，不要加 `v` 前缀。
- 已发布版本不可变；同一个 module/version 不允许覆盖 Nexus 包，也不允许修改 registry 中已存在的 `modules/<name>/<version>/**`。
- 每个 module 应声明一个可被下游验证的 smoke target，例如 `@addx_xxx//:all` 或 `@addx_xxx//tests/...`。

### Rule 2 - 配置 module 仓库 CI

module 仓库 CI 至少需要三类 job：

| job | 触发 | 职责 |
|-----|------|------|
| test | branch / MR | 构建和测试 module 自身 |
| package | tag | 从 tag 生成源码 tarball，并计算 sha256 / SRI integrity |
| register | tag，且 package 成功后 | 生成 release metadata，向 bazel-registry 仓库提交 MR |

生成 tarball 时使用可复现方式，不要使用普通 `tar -czf` 打包工作区：

```bash
git archive --format=tar --prefix="${CI_PROJECT_NAME}-${CI_COMMIT_TAG}/" "${CI_COMMIT_TAG}" \
  | gzip -n > "dist/${CI_PROJECT_NAME}-${CI_COMMIT_TAG}.tar.gz"
```

发布到 Nexus 后，CI 应输出或传递 release metadata：

```json
{
  "module": "addx_xxx",
  "version": "0.1.0",
  "nexus_url": "https://<nexus-host>/<repo>/bazel-package/addx_xxx/0.1.0.tar.gz",
  "strip_prefix": "addx_xxx-0.1.0",
  "source_repo": "https://gitlab.addx.ai/<group>/addx_xxx.git",
  "tag": "0.1.0",
  "commit_sha": "<release-commit>",
  "tarball_sha256_hex": "<hex-sha256>",
  "integrity": "sha256-<base64>"
}
```

CI 变量建议：

| 变量 | 用途 |
|------|------|
| `NEXUS_USER` / `NEXUS_PASSWD` | 上传 Nexus 制品 |
| `REGISTRY_PROJECT_TOKEN` | 创建 bazel-registry MR；只从环境变量读取，不通过 CLI 参数传递 |
| `REGISTRY_PROJECT_ID` 或 registry HTTP URL | 供脚本定位 registry 项目 |
| `REGISTRY_TARGET_BRANCH` | 通常是 `main` |

安全规则：

- 不在 `.gitlab-ci.yml`、脚本参数、日志中打印 token。
- clone 带 token 的 registry 仓库时，目录不能被收进 artifacts；artifacts 只收 tarball、metadata 和必要 env 文件。
- registry 更新脚本必须校验 `module`、`version`、`nexus_url`、`strip_prefix`、`integrity`，禁止 `../`、绝对路径、控制字符和非约定 host/path。

### Rule 3 - 维护 bazel-registry 仓库

registry 仓库负责维护 Bazel registry 静态结构，不保存源码本体。源码本体来自 `source.json` 指向的 tarball URL。

典型结构：

```text
bazel-registry/
├── bazel_registry.json
└── modules/
    └── addx_xxx/
        ├── metadata.json
        └── 0.1.0/
            ├── MODULE.bazel
            └── source.json
```

`source.json` 中的 integrity 使用 SRI 格式，不是 hex sha256：

```json
{
  "url": "https://<nexus-host>/<repo>/bazel-package/addx_xxx/0.1.0.tar.gz",
  "integrity": "sha256-<base64>",
  "strip_prefix": "addx_xxx-0.1.0"
}
```

registry CI 必须做这些校验：

- `bazel_registry.json`、`metadata.json`、`source.json` JSON 格式正确。
- 已存在版本不可修改，只允许追加新版本。
- `source.json.url` 可下载，`integrity` 和实际 tarball 匹配。
- tarball 解压后存在 `strip_prefix/MODULE.bazel`，且 module name/version 与 registry 路径一致。
- 每个 module 的 smoke target 能被下游消费方解析或构建。
- 恶意 metadata fixture 必须失败，例如非法 module 名、非法 semver、错误 Nexus host、URL 路径不匹配、非法 integrity。

### Rule 4 - 使用 GitLab Pages 提供 `--registry`

Bazel `--registry` 需要 HTTP(S) 静态目录。GitLab Pages 可以直接托管 registry 仓库的静态内容。

要求：

- Pages 必须对下游构建环境可访问；如果构建机没有 GitLab 登录态，Pages 需要设为 Everyone 或提供可用的反向代理/认证方案。
- Pages URL 更新内容后一般保持不变；项目哈希后缀是否变化取决于公司 GitLab Pages 配置，不应写死成业务含义。
- registry URL 应和 BCR fallback 同时配置，避免内部 registry 找不到官方依赖。

推荐在下游项目 `.bazelrc` 中配置：

```bazelrc
common --registry=https://pages.addx.ai/<bazel-registry-pages>
common --registry=https://bcr.bazel.build
```

如果还有本地实验 registry，可放在更前面：

```bazelrc
common --registry=file:///home/<user>/tmp/addx-bazel-exp/registry
common --registry=https://pages.addx.ai/<bazel-registry-pages>
common --registry=https://bcr.bazel.build
```

`common` 表示所有 Bazel 命令都会带上这些 option，包括 `bazel build`、`bazel test`、`bazel mod deps`。registry 查询按声明顺序进行：先本地，再公司 registry，最后 BCR。

### Rule 5 - 下游项目使用 `bazel_dep`

在下游项目 `MODULE.bazel` 中声明依赖：

```starlark
bazel_dep(name = "addx_xxx", version = "0.1.0")
```

如果只是示例或测试依赖，不应成为业务传递依赖：

```starlark
bazel_dep(name = "addx_xxx", version = "0.1.0", dev_dependency = True)
```

首次接入公司 registry 时，先只添加 registry 和 BCR fallback，刷新并提交 lockfile：

```bash
bazel mod deps --lockfile_mode=update
git diff --check -- .bazelrc MODULE.bazel MODULE.bazel.lock
```

再引入第一个内部 `bazel_dep`，并再次刷新 lockfile：

```bash
bazel mod deps --lockfile_mode=update
rg 'addx_xxx|MODULE.bazel|source.json' MODULE.bazel.lock
git diff --check -- .bazelrc MODULE.bazel MODULE.bazel.lock
```

第二次刷新必须让 `MODULE.bazel.lock` 出现内部 module 对应的 registry checksum/source 记录，至少能看到该 module 的 `MODULE.bazel` 和 `source.json` checksum。只在热缓存环境中 `bazel test --lockfile_mode=error` 通过不够，clean Bazel root 仍可能因为缺少 checksum 失败。

引入内部 module 后，运行：

```bash
bazel test //path/to:smoke_test --lockfile_mode=error --test_output=errors
```

如果要验证 clean Bazel root 下 lockfile 完整性：

```bash
tmp_root=$(mktemp -d /tmp/addx-bazel-clean-root.XXXXXX)
bazel --output_user_root="${tmp_root}" test //path/to:smoke_test --lockfile_mode=error --test_output=errors
```

`--lockfile_mode=error` 用来防止 CI 静默刷新 `MODULE.bazel.lock`。如果 clean root 下提示缺少 registry checksum，必须重新运行 `bazel mod deps --lockfile_mode=update` 并提交更新后的 `MODULE.bazel.lock`。

不要手工解决 `MODULE.bazel.lock` 冲突；统一重新生成 lockfile 后跑产品构建或 smoke test。

### Rule 6 - 使用 override 做本地开发验证

推荐方式一：命令行参数，不改项目文件，适合日常本地联调。

```bash
bazel test //path/to:smoke_test \
  --override_module=addx_xxx=/home/<user>/work/addx_xxx \
  --test_output=errors
```

推荐方式二：在实验分支修改 `MODULE.bazel`，适合需要反复调试或共享实验分支的场景。

```starlark
local_path_override(
    module_name = "addx_xxx",
    path = "/home/<user>/work/addx_xxx",
)
```

约束：

- `local_path_override` 只能放在个人实验分支或临时验证分支，不能合入长期主线配置。
- override 只能覆盖同名 Bazel module；本地仓库的 `MODULE.bazel` 中 `module(name = "...")` 必须与被覆盖的 module 名一致。
- 通过 override 验证时，建议让本地 module 返回一个可观察差异，例如版本字符串或 test-only symbol，避免误以为仍在使用 registry/Nexus 包。
- 反向验证时去掉 override，确认构建又回到 registry/Nexus 发布版本。

### Rule 7 - 补充检查项

创建或审查 bazel-module 使用方案时，主动检查这些点：

- Nexus 是否禁止覆盖已发布文件。
- GitLab Pages 是否能被 CI runner 和普通开发者匿名或授权访问。
- registry MR 是否有不可变版本校验。
- module tag 规则是否严格为 `x.y.z`。
- tarball 是否从 clean tag 生成，而不是从脏工作区生成。
- smoke target 是否覆盖公开 API，而不是只证明 module 可解析。
- CI 是否把带 token 的 clone 目录放入 artifacts。
- 下游项目是否把公司 registry 和 BCR fallback 都写入 `.bazelrc`。
- 下游项目是否提交了 `MODULE.bazel.lock` 的内部 module checksum/source 记录。

## Examples

### Bad

```bash
# 每次构建都手动输入 registry，容易漏掉 BCR fallback，也不利于 CI 固化
bazel test //... \
  --registry=https://pages.addx.ai/<registry>
```

```starlark
# 把本地 override 提交到主线，其他开发者机器上路径不存在
local_path_override(
    module_name = "addx_xxx",
    path = "/home/huatuo/project/addx_xxx",
)
```

```bash
# 普通 tar 打包工作区，mtime/owner/文件顺序不稳定，同一 tag 可能生成不同 sha
tar -czf dist/addx_xxx-0.1.0.tar.gz .
```

### Good

```bazelrc
# 下游项目固化 registry 顺序
common --registry=https://pages.addx.ai/<bazel-registry-pages>
common --registry=https://bcr.bazel.build
```

```bash
# 本地联调用命令行 override，不污染 MODULE.bazel
bazel test //tests/integration/bazel_module_demo:demo_module_usage_test \
  --override_module=addx_xxx=/home/<user>/work/addx_xxx \
  --test_output=errors
```

```bash
# tag pipeline 生成可复现源码包
git archive --format=tar --prefix="${CI_PROJECT_NAME}-${CI_COMMIT_TAG}/" "${CI_COMMIT_TAG}" \
  | gzip -n > "dist/${CI_PROJECT_NAME}-${CI_COMMIT_TAG}.tar.gz"
```

## References

- Demo module 仓库：`https://gitlab.addx.ai/firmware/cluster/addx_demo_module.git`
- Demo 流程说明：`https://pages.addx.ai/addx-demo-module-42a2bb/docs/flow-demo.html`
- Bazel registry 示例仓库：`https://gitlab.addx.ai/firmware/building/bazel-registry.git`
