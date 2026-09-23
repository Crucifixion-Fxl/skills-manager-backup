# Composite Service — 1 System + N Component (共享 kubernetes-id)

> SKILL.md §1 提到的「共部署多 Component」的详细模式。看这个 reference 当：单 binary 多业务单元 / 想拆 Component 但又共部署 / 需要决定 catalog 写 1 Component + N API 还是 N Component 共享 kubernetes-id。

## 何时用

多个相对独立的业务能力**共部署在 1 个 binary**（运维成本 / 团队规模 / 跨 Component 通信延迟考虑）—— **catalog 形态是 1 个 System + N 个 Component**（每个业务单元都是独立 Component，**不**合成 1 个 Component），N 个 Component 共享 `backstage.io/kubernetes-id` 注解指向同一个 K8s app。

## 范本

```yaml
kind: System
metadata: { name: golf-backend }
spec: { domain: golf, owner: group:default/team-golf }
---
kind: Component  # 8 个 Component 各自独立声明，下方仅示意 2 个
metadata:
  name: golf-players
  annotations:
    backstage.io/kubernetes-id: golf-backend       # 共享 K8s app
    a4x.io/cicd-app-name: golf-backend             # 共享 ArgoCD app
spec:
  type: service
  system: golf-backend
  providesApis: [golf-players-api]
---
kind: Component
metadata:
  name: golf-play
  annotations:
    backstage.io/kubernetes-id: golf-backend
    a4x.io/cicd-app-name: golf-backend
spec:
  type: service
  system: golf-backend
  providesApis: [golf-play-api]
---
# kind: API 实体 N 个（每个 Component 一个），路径 / definition / lifecycle 各自标
```

## 为什么不合成 1 个 Component

- Backstage UI 可按 Component 分别 own / lifecycle 标 / TechDocs 入口分别跳
- API drift 检查、OpenAPI lint、CODEOWNERS 各自隔离
- 拆 Component 为独立部署时仅改注解（`kubernetes-id` / `a4x.io/cicd-app-name`），文档与代码结构不动
- 业务高内聚 + 演进节奏 / SLO / owner 可能分化的能力，提早建独立 Component 实体成本几乎为零

## 文档结构对应（与 architect skill §4 一致）

```
docs/architecture/<system>/
├── index.md                              # System overview（Component 列表 / 跨 Component 通信图 / 对外 API 汇总）
├── <component-A>/
│   ├── index.md                          # Component 详设（API / 表 / ports / 监控）
│   └── <subtopic>.md                     # 可选：复杂子主题
├── <component-B>/
│   └── index.md
└── data/  adrs/  cross-component-flows.md  ...
```

## OpenAPI 文件组织

- `api/<component>.openapi.yaml` × N（每个 Component 一文件，文件名 = Component 名）
- `api/common.yaml`（跨 Component 共用 schema 用 `$ref` include）
- CI lint 对 N 个文件分别校验，独立 drift detection

## 反模式

- ❌ 把多个独立业务能力压成 1 个 Component + N 个 API（drift 不可隔离 / API owner & lifecycle 无法分别标 / 拆 Component 时要改文档结构）
- ❌ 把共部署的 Component 拆成 N 个 Domain 或 N 个 System（过度治理，业务高内聚的能力集合是 1 个 System）
- ❌ Component 间 SQL JOIN 或直接 import internal package（共部署 ≠ 共享代码内部，硬边界由 ports + 静态分析 CI 保证）
