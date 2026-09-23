# Pre-commit 接入

用于团队推广的本地提交前拦截，不替代统一 CI 门禁。

## 适用场景

- “提交前就拦截泄露”
- “给仓库加 pre-commit secret scan”

## 接入原则

- 按 TruffleHog 官方 pre-commit 方式接入
- 默认 `--no-update`
- 默认先看 `verified`
- 输出只保留 `Redacted`，禁止打印原文密钥
- pre-commit 为提交前阻断场景，可不生成 JSON 报告文件

## 最小示例

`.pre-commit-config.yaml`：

```yaml
repos:
  - repo: local
    hooks:
      - id: trufflehog-verified
        name: trufflehog verified scan
        entry: ./skills/trufflehog-cli/scripts/pre-commit-trufflehog.sh
        language: system
        pass_filenames: false
```

脚本行为：

- 若存在 `HEAD~1`：执行增量 git 扫描
- 若不存在 `HEAD~1`（例如首提交）：自动回退到 `filesystem` 扫描

## 初始化

```bash
pre-commit install
pre-commit run --all-files
```

## 推广建议

- 第一阶段仅对 `verified` 做阻断
- 团队稳定后再扩展 `unknown` 排查
- 本地 pre-commit 失败时，修复后必须重新执行一轮
