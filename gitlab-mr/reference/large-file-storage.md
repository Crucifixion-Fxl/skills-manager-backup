# Git 大文件与制品提交检查

本规则由 `gitlab-mr` 维护，Step 2.85 是 MR 提交/更新前的主检查入口；
`code-submit` 在暂存/commit 时提前检查，`code-review` 只读审查，三者共用有效证据。

规则只保存在 skill；不写入项目 `AGENTS.md` / `CLAUDE.md` 或 auto-memory，也不要求安装规则块。
读取并遵循项目已有政策、阈值和例外，不自动删除或改写既有 memory。
执行检查不代表授权上传制品、修改全局 Git 配置、迁移历史、强推或联系他人。

## 存储选择

| 内容 | 默认去向 | 决策依据 |
|---|---|---|
| 大型纹理、模型、音视频、设计源文件、必须随源码版本管理的模型权重 | Git LFS | 例如 `.exr`、`.fbx`、大型 `.png`；看用途和体积，不按扩展名一刀切 |
| 编译生成的 AAR/JAR、framework、`.so`/`.a`、APK/IPA、SDK 分发包 | Nexus；通过包管理器或固定 URL 消费 | 固定版本或内容摘要，记录来源和恢复命令；特殊情况按项目已批准的路径例外处理 |
| 运行数据库、日志、构建缓存、临时包、整仓 ZIP | Git 外的运行目录 / 对象存储 | 不转成 LFS；可再生成的内容配置 `.gitignore`，已有 tracked 内容另行治理 |
| 小型必要测试夹具 | 项目约定 | 可保留经过脱敏、体积受控的 fixture；数据库后缀本身不等于运行数据库 |

默认治理阈值：**二进制文件 ≥10 MiB 必须作出存储选择；本次引入的普通 Git blob
≥50 MiB 不提交，除非项目已有明确的路径级例外**。这是本 skill 的可配置默认值，
不是 Git/GitLab 服务端限制；先读仓内政策，保留已有更严格约束及已批准例外。
例外记录路径、用途、大小上限和维护责任，不能只写“忽略大文件检查”。
大型文本另查生成来源和可压缩性，不机械搬入 LFS。

**制品统一走 Nexus**：发布/下载引用 [addx:addx-nexus-usage](../../addx-nexus-usage/SKILL.md)。
服务地址、团队 hosted repository、`s3-*` blob store、上传账号和凭证规约以该 skill 为准；
本 reference 不复制详情、不猜 repository；检查本身不授权上传制品。

## 提交前执行

检查证据绑定候选 HEAD、实际目标分支 SHA、项目政策，以及本次 index 内容（若有）；
跨 `code-submit` / `gitlab-mr` / `code-review` 可复用同一有效证据，不重复扫描。
任一绑定内容改变时重查；MR 提交场景无法完成对象核验时记录缺口，不放行 push/MR 写入。

- 先检查本次变更的用途、体积、项目例外和实际属性；Nexus 引用必须能按固定版本恢复，发布/下载遵循 `addx:addx-nexus-usage`。
- 暂存后检查 **index 中的 blob**，用 NUL 分隔处理文件名，例如
  `git diff --cached --name-only -z --diff-filter=ACMR` 配合 `git cat-file`。
  已配置 `filter=lfs` 但仍暂存原始大文件的情况不通过；
  校验暂存内容是有效 LFS pointer，并核对对应对象和 CI 的 LFS 拉取配置。
  不把 blob 内容输出到日志，避免泄露数据库、日志或其它文件中的数据。
- 同时检查本次分支相对目标分支新增的历史 blob；只检查最终 diff 会漏掉
  “先提交大文件、再删除”的情况。不要对每次小提交全量扫描所有旧历史。
- 只阻止本次引入的不合规内容，列出路径、大小、分类与修复方式；
  旧历史登记待治理项，不阻断无关变更。不要擅自清空用户 index、删除资产或改写历史。
- LFS 跟踪规则、`.gitignore` 与 history migration 是不同操作：
  配规则不会自动转换已有 blob，ignore 不会移除已跟踪文件。
  存量迁移需单独确定 refs、备份、本地分支保护、CI 与协作者切换方案。

## 依据

- [Git LFS 的 pointer 存储机制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)
- [Android 官方：通过 Maven 仓库分发 AAR](https://developer.android.com/build/publish-library/upload-library)
- [Git LFS migrate：历史迁移与 refs 范围](https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-migrate.adoc)

实际案例：2026-10 的本地扫描发现 `golf_unity.aar` 当前路径有 85 个唯一历史版本，
对象占用约 4.65 GiB；另外发现大型 Unity 源资产、framework、运行日志和数据库进入普通 Git。
这说明治理需按内容用途分类，不能只给所有“大文件”统一加 LFS。
