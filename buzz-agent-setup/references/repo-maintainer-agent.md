# Repo maintainer agent：每个 repo 一个 Maintainer 身份

一个 GitLab repo 配一个 maintainer agent（`<repo-slug>-maintainer`），权限等同该 repo 的 GitLab Maintainer 角色。**只有该 repo 当前的 Maintainer 能点名它，点名本身就是授权**，不再走 `ACT-<id>` 提案／批准。

这是 owner 在 #125 明确做的取舍，只适用于本页定义的 maintainer 身份；[act-authorization.md](act-authorization.md) 对其它 executor 的要求不变。它换掉的是「人批 ACT」这一步，不是身份校验：仍然由脚本核对原始事件 `pubkey`、merge 绑定 `head_sha`、每条请求只执行一次。

## 身份与凭据

| 项 | 要求 |
|---|---|
| 名称 | `<repo-slug>-maintainer`（本仓即 `skill-maintainer`）。一个 repo 一个身份，不跨 repo 共用 |
| token | Project Access Token：Maintainer（40）＋`api`。**不加 `write_repository`**：merge、approve、评论都走 API，加了它 bot 就能推所有非保护分支 |
| 归属 | bot 由实例管理员标为 external，显式 membership 恰好是目标项目（验证方式见 [agent-credentials.md](agent-credentials.md)）；bot 角色不可改，降权只能换 token |
| 位置 | token 只在独立 OS principal／容器里的 broker，LLM 进程不持有。Maintainer `api` 在平台上没有更细的动作档位，「能做什么」只能由 broker 的动作目录限定，不能靠 prompt |
| 与 Issue token | LLM 另有一把独立 Planner `api` 只维护 Issue，和 broker 的 Maintainer token 分开签发、分开注入 |

签发 Maintainer token 是平台管理员的动作，按 [agent-credentials.md](agent-credentials.md) 的授权路径做；`provision_gitlab_agent_token.py` 没有 maintainer profile，它会把 token 写进目标 Agent 的 env，正是本页不允许的。

## 谁能点名：名单就是授权

名单来自 GitLab，不来自人手维护的 pubkey 列表：`members/all` 里 `access_level >= 40`、状态 active、非 bot 的用户，经共享 people 文件（GitLab 用户名 → Buzz pubkey，见 [gitlab-buzz-sync.md](gitlab-buzz-sync.md)「people 的生成器」）转成 pubkey。只认原始事件的 `pubkey`，不认显示名、@ 提及或正文自称。GitLab 里升为 Maintainer 的人自动能点名，降权的人自动失去；没有 Buzz 公钥的 Maintainer 只是点不了名。

`scripts/gitlab_maintainer_roster.py` 是这一层的确定性实现，没有 LLM：

```bash
# 定时刷新（跟 sync timer 同频，5 分钟）：只读 token 取自环境变量，写 0600 roster
python3 scripts/gitlab_maintainer_roster.py refresh --project-id <id> --base-url https://<gitlab> \
  --people-file /abs/people.json --out /abs/maintainers.<id>.json
# 每个 turn 的第一步：退出码 0 允许，1 拒绝，2 名单不可用
python3 scripts/gitlab_maintainer_roster.py admit --project-id <id> --roster /abs/maintainers.<id>.json --pubkey <事件 pubkey>
# 执行任何写动作之前再现查一次，降权立即生效
python3 scripts/gitlab_maintainer_roster.py admit --project-id <id> --live --base-url … --people-file … --pubkey <事件 pubkey>
```

fail closed：roster 缺失、超过 TTL（默认 30 分钟）、`project_id` 不符、格式或权限不对、API 报错、分页不完整、同一 pubkey 对应多个用户名，都是退出码 2，不会被当成「不是 maintainer」，也不会放行；名单只会因刷新失败而变空，不会变宽。刷新失败不改动上一份 roster。

## 每个请求怎么处理

1. `admit --roster`。退出码 1：不执行任何动作，在原 Thread 回固定一句「该操作需要本仓 Maintainer，当前身份不在名单」，话术不列名单成员；同一 (Thread, pubkey) 只回一次，发起者是 bot 不回。退出码 2：回「名单暂不可用，已按 fail closed 拒绝」，与前一句区分。
2. `claim --ledger-dir <0700 目录> --event-id <请求事件 id>`。退出码 1 是重放（翻旧帖重放一条历史请求），拒绝；每条请求事件最多执行一次。
3. 写动作前 `admit --live`，用现时的 `access_level` 再确认一次，请求者中途被降权则拒绝。
4. 经 broker 提供的 `glab` 入口执行目录内动作（下节）。回帖写实际做了什么、结果、GitLab 链接。

## 用 glab 做 approve 和 auto-merge

agent 通过 broker 暴露的受限 `glab` 入口操作，入口固定 `--repo`、host 与 token，只放行下表目录内的子命令和参数形状，不透传任意 `glab api`。

- **approve**：`glab mr approve <iid> --sha <head_sha>`。GitLab CE 的 approve 只是信息性标记（[approval-authz.md](approval-authz.md) 一），既不校验谁批、也不因缺失而拦合并，它记录的是「maintainer agent 应某位 Maintainer 的请求核过这个 head」，不替代 Maintainer 的请求。
- **auto-merge**：`glab mr merge <iid> --sha <head_sha> --auto-merge -y`（流水线未过时是 merge when pipeline succeeds）。
- **head_sha 绑定**：请求必须整条是 `/approve !<iid> <head_sha>`（允许 `@<agent>` 前缀与整条加粗，其余一律不认，引用或转述不算）。`<head_sha>` 是完整 40 位小写 hex，同时传给 approve 与 merge 的 `--sha`；和 MR 当前 head 不一致就拒绝并回报当前 head，不代为改用新 head。GitLab 也会用 `--sha` 再校验一次。
- 新推送会让已核对的 head 过期，只能重新请求；CE 不会自动作废旧 approve，所以以请求里的 `head_sha` 为准，不以 approve 是否存在为准。

## 动作目录

目录外一律拒绝。A、M、E 类只在请求者是名单成员时可用；D 类不进目录，只能人手。

| 类 | 动作 | 约束 |
|---|---|---|
| A 读与协作 | 读 MR／pipeline／job／分支／保护规则／成员／CI 变量**名与 flag**；评论、label、assignee、reviewer、标题描述、draft↔ready、close／reopen；重试／取消 MR 的 job；取消 merge-when-pipeline-succeeds | 不读、不回显 CI 变量的值 |
| M 合并 | 上节的 approve 与 auto-merge | 需要 `/approve !<iid> <head_sha>`，不是「先批、再往分支推代码」的批准 |
| E 变更 | rebase、解冲突；新建或改保护规则（**不含** `main` 的放宽）；成员增删与改角色，**上限 Developer（30）**；CI 变量的 `protected`／`masked`／`environment_scope` flag 与删除；项目设置白名单键（合并方式、`only_allow_merge_if_pipeline_succeeds`、`remove_source_branch_after_merge`、pipeline 超时）；删除非 MR 源分支、建／删非保护标签；runner 启停 | rebase 会改变 head，之后的 merge 请求要带新 `head_sha`；每次回帖原样回显执行的动作与参数 |
| D 人手 | token 管理（项目／组／个人／deploy token 的创建、轮换、吊销，含 bot 自己的 self-rotate）；CI 变量的创建与改值（密文不经 Buzz 消息）；授予 ≥ 40 或改 bot 成员；webhook、集成、deploy key；放宽 `main` 保护、对保护分支直推或 force push；改可见性、转移、归档、删除、默认分支、`external` 标记；pipeline schedule 与带变量的手动触发；任何 Group 级或跨 project 操作 | 请求者要求 D 类时回「该动作不在 maintainer agent 的能力内，需 Owner 手工执行」，不提案、不代做 |

## 旁路发现

token 权限大于目录。给项目加一个确定性只读探针（无 LLM，另一把 Reporter 只读身份），定时对「保护分支规则、成员及角色、token 列表、CI 变量名与 flag、webhook／deploy key、白名单项目设置」做快照，出现**没有对应回帖记录**的变更就告警并按降权路径换 token。它抓的是 token 泄露或 broker 被绕过后的越权，不是替代 broker 的闸。

## 入频道、退频道、回收

- **入**：先在 Canvas「代码仓库」表与 Agent 清单登记该 repo 与 maintainer，再由管理员签 token 并标 external，配置 broker，最后 owner `channels add-member --role bot`（私有频道不能自行加入）。一个 repo 的 maintainer 可以在含该 repo 的多个频道里，凭据始终是 per-repo。
- **退**（先收最危险的能力）：① 吊销 token 并移除 bot 的项目 membership；② 从频道 `remove-member`；③ 删除 broker 侧配置与 ledger 目录的写权限；④ 更新 Canvas 清单。降权走「签新 token → 验证 → 写 broker 配置 → 重启 → 吊销旧」。
- 与 [#122](https://gitlab.addx.ai/engineering/skills/-/issues/122) 的多仓映射互不依赖；只有 broker 将来做成一个进程服务多个 repo 时，才复用「owner 固定 project → token」映射，调用方不能选 env 名或 host。

## 上线前置与验收

在以下项完成前 maintainer agent 保持禁用，不能把文档或离线测试通过写成已上线：

1. 独立 broker（含受限 `glab` 入口与 ledger），以及跨 UID、直连平台的负向验证（同 [act-authorization.md](act-authorization.md)「强制执行边界」）。
2. 管理员签发的 external Maintainer token，membership 只有目标项目。
3. Canvas 由 owner 更新：Agent 清单加一行；「没有自动合并的 Agent」「全部人手合」两处改成 maintainer agent 在名单成员请求下合并、人手合作为 break-glass。
4. 本实例（GitLab 18.0 CE）的 L4 实测：`--sha` 校验、新推送后 merge-when-pipeline-succeeds 的行为、classic `api` 是否能 self-rotate 或创建同级 token、名单成员与非成员的正负向请求。

离线契约见 `tests/test_gitlab_maintainer_roster.py`：非 maintainer、已降权、显示名与大小写冒充、名单缺失／过期／跨项目／畸形／不安全权限、API 失败、分页不完整、同 pubkey 多用户名、bot 排除、live 复核、重放。目录外动作、D 类拒绝、`head_sha` 不一致等 broker 侧用例属于上面第 1 项的 L3／L4。
