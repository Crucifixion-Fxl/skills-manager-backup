# references/db-migration/

DB schema migration 的延伸 narrative —— `workflows/add-db-migration.md` 走完之后，详细 case 和反模式都在这里。

## 索引

| 文件 | 内容 |
|---|---|
| `secret-patterns.md` | 三种正确 Secret 设计模式 + 两种禁忌反模式 |
| `timing-pitfalls.md` | 时序坑：PreSync hook 跑在 ExternalSecret 同步之前 |
| `end-to-end-example.md` | 完整端到端 MR 示例（cd-requirements + 4 个 manifest + binary 模板） |

`workflows/add-db-migration.md` 走完正常情况就够；只在出现下列场景查这些 narrative：

- 选 Secret 模式拿不准（A 单 Secret / B 双 Secret / C 合并 template）→ `secret-patterns.md`
- 改了 ExternalSecret 又改了 image tag → `timing-pitfalls.md`
- 完整跑通需要看一个 working example → `end-to-end-example.md`

## 关联

- workflow: `workflows/add-db-migration.md`
- playbook: `troubleshooting/db-migration-failures.md`
- recipe: `recipes/db-migration/presync-hook.yaml.tmpl`
- hard-rules: #15 / #16
- Legacy source: `cicd-developer/references/db-migration/`（这里的 narrative 文件直接照搬，validation / failure lessons / migration-contract 内容已重新组织到 current workflow / playbook）
