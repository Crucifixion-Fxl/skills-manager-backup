# Zendesk Help Center References

本目录存放 Zendesk Help Center 批量操作的脚本、示例和规则文档。

## 目录结构

```text
references/
|- README.md
|- scripts/
|  |- batch_copy.py
|  |- batch_translate.py
|  `- utils.py
|- examples/
|  |- copy_faq_to_wiki.py
|  `- add_translations.py
`- rules/
   `- translation_rules.md
```

## 登录与使用前置条件（重要）

执行本 Skill 前，本地必须先具备可用的 AWS 配置（用于 AssumeRole 到 `cs-tools-role` 并读取 Secrets Manager）。

通常由团队 AWS 管理员负责提供与开通，包含：

1. `~/.aws/credentials` 中可用 profile（例如 `cstools-dev`）
2. 对 `arn:aws:iam::002497567426:role/cs-tools-role` 的 AssumeRole 权限
3. 本地终端设置 `AWS_PROFILE`（例如 `cstools-dev`）

建议先执行认证检查：

```bash
python {SKILL_DIR}/zendesk_helper.py check-auth
```

## 安全要求

- 不在本地环境变量中配置或暴露 `ZENDESK_TOKEN` 明文。
- 不在命令行参数、日志、对话中输出 token 或 SecretString。
- 所有 Zendesk 操作统一通过 `zendesk_helper.py` 执行。
