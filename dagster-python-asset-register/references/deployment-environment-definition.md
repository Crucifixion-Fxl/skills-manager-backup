# Deployment Environment Definition

本文件统一定义 Dagster Python 资产 `deployment_environments` 的取值与选择规则。

## 1) 结构定义

- `deployment_environments` 是字符串列表
- 每个元素格式：`<region>[-<env>]`

## 2) 部署区域（region）

- 可选值：`us`、`eu`、`cn`

## 3) 部署环境（env）

- 可选值：`dev`、`staging`、`prod`
- `env` 可选；省略时表示该区域全环境

## 4) 语义规则

- 若只写区域（如 `["us"]`），表示该区域全环境：
  - `us-dev` + `us-staging` + `us-prod`
- 同理：
  - `["eu"]` 表示 `eu-dev` + `eu-staging` + `eu-prod`
  - `["cn"]` 表示 `cn-dev` + `cn-staging` + `cn-prod`

## 5) 选择规则（强制）

- 若用户未明确部署区域或部署环境，必须先让用户选择
- 禁止默认拍板、禁止隐式填值
- 仅在用户确认后写入 `deployment_environments`

## 6) 示例

- 全区域：`["us"]`、`["eu"]`、`["cn"]`
- 单环境：`["us-dev"]`、`["eu-staging"]`、`["cn-prod"]`
- 多区域多环境：`["us-prod", "eu-staging"]`
