---
name: pushsecret-literal-secret-prefix
description: Kyverno forbid-eso-secret-literal-prefix 拒绝 PushSecret，因为 remoteKey 错写了 secret/ 前缀。
---

# Playbook：PushSecret 的 `remoteKey` 带字面 `secret/`

## 症状

- ArgoCD Application 首次同步停在 PushSecret，状态为 `SyncFailed` / `OutOfSync`。
- admission 返回 ClusterPolicy `forbid-eso-secret-literal-prefix`。
- 报错指出 PushSecret / ClusterPushSecret 的 `remoteRef.remoteKey` 不能以字面 `secret/` 开头。
- 同一 sync-wave 后面的 Crossplane RDS / Aurora / Redis 资源还没有创建。

## 根因

ClusterSecretStore 已配置 `spec.provider.vault.path: secret`。ESO 的 reader 和 writer 字段都
相对这个 mount 解析：

- 完整规范 Vault 路径：`secret/prod/rds/application/superset/database`
- manifest 正确 remote key：`prod/rds/application/superset/database`

在 manifest 再写 `secret/...` 会拼成 `secret/secret/...`。除路径错误外，Vault KV v2 还可能
留下错误前缀的 metadata husk，所以 Kyverno 在 admission 阶段直接拒绝。

## Diagnosis / 诊断

1. 只读确认 Application 的失败资源和 admission message，不能仅凭 `OutOfSync` 猜根因。
2. 查看目标 ClusterSecretStore，确认 `spec.provider.vault.path`。若不是 `secret`，STOP：本
   playbook 的相对路径结论不适用，先核对该集群的 store 契约。
3. 在 Git 渲染结果中定位所有 ESO Vault 引用：
   - ExternalSecret：`remoteRef.key` / `extract.key`
   - PushSecret / ClusterPushSecret：`remoteRef.remoteKey`
4. 运行：

   ```bash
   python3 "$skill_root/validators/check_vault_paths.py" <manifest-dir>
   ```

## 修复

只修改 Git 声明中的 ESO 字段，去掉一个开头的 `secret/`；不要改变规范 Vault 路径、环境、
platform、app 或 key。例如：

```yaml
remoteRef:
  remoteKey: prod/rds/application/superset/database
```

完成 kustomize/Helm render、`check_vault_paths.py` 和服务仓完整 validator 后，提交 MR。由
ArgoCD 在 MR 合并后自动重试新 revision。

## 禁止动作

- 不要 live patch PushSecret 或绕过 Kyverno；Git 仍错误会被 selfHeal 拉回。
- 不要手工强制同步旧 revision；admission 会继续拒绝。
- 不要为消除错误而修改 ClusterSecretStore 的 `path`；这会影响该 store 的所有消费者。
- 不要把 Vault 中的完整规范路径改成少一层 `secret`；相对化只发生在 ESO manifest 字段。

## 验证末态

1. Application 已同步包含修复的 Git revision，失败 operation 已由自动同步替换。
2. PushSecret condition 为 `Ready=True` / `Synced=True`。
3. 后续 sync-wave 的 Crossplane CR 已创建并最终 `Ready=True`。
4. ExternalSecret 为 `Ready=True`，只核对目标 Secret 的 key 名，不读取或输出值。
5. Vault 规范路径仍为原来的 `secret/{env}/{platform}/application/{app}/{key}`，没有新增
   `secret/secret/...`。

## 参考

- hard-rules #7d
- `references/vault-paths/resolver.md`
- `validators/check_vault_paths.py`
