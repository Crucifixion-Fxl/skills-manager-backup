---
name: tracker-manager
description: "⚠️ DEPRECATED — 此 Skill 已合并到 tracking-lifecycle。当用户提到埋点相关操作时，请使用 tracking-lifecycle skill。"
---

# tracker-manager (DEPRECATED)

> **此 Skill 已废弃，能力已合并到 [tracking-lifecycle](../tracking-lifecycle/SKILL.md)**。
> 请直接使用 tracking-lifecycle skill。

## Description

此 Skill 已废弃。所有埋点管理平台操作能力（查询、创建、发布校验、API 认证等）已合并到 tracking-lifecycle skill。

## Rules

### Rule 1 — 重定向到 tracking-lifecycle

当此 Skill 被触发时，直接切换到 tracking-lifecycle skill 执行。不再提供独立功能。

## Examples

### Good Example

用户说"查一下 vicohome 有哪些埋点"：

→ 使用 tracking-lifecycle skill 处理。

### Bad Example

用户说"查一下 vicohome 有哪些埋点"：

→ 继续在 tracker-manager 中处理（此 Skill 已废弃，不应继续使用）。
