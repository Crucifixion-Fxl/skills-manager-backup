# share-device

把 VicoHome / KiwiBit / VicoNature 设备从 admin 账号分享给另一个账号（产生真实分享关系）。

复刻 MeterSphere 场景 `OEM_设备分享` 的 9 步 API 链路。已在 KB prod 端到端验证。

## 快速上手

```bash
# 1. 创建 profile（交互式，存 ~/.config/addx/share-device/<name>.env）
python scripts/share_device.py --init-profile kb-prod-bird

# 2. 分享
python scripts/share_device.py --profile kb-prod-bird --action share

# 3. 解除（sharer 主动）
python scripts/share_device.py --profile kb-prod-bird --action unshare

# 4. 看当前分享关系
python scripts/share_device.py --profile kb-prod-bird --action list
```

## 文档

- [SKILL.md](SKILL.md) — Claude 触发条件 + decision tree + 公司特定规则
- [CHANGELOG.md](CHANGELOG.md) — 版本历史 + 9 个 endpoint 表
- [references/scenarios.md](references/scenarios.md) — 9 个典型用法
- [references/troubleshooting.md](references/troubleshooting.md) — 故障排查
- [references/brands.md](references/brands.md) — brand × region × env preset

## 与 create-pir 的关系

姊妹 skill。profile 互通（命名规则一致）。组合用法：

```bash
# 先分享设备给 sharer
python ../share-device/scripts/share_device.py --profile kb-prod-bird --action share

# 再用 sharer 视角的 PIR 数据
python ../create-pir/scripts/create_pir_event.py --profile kb-prod-a6xtest --object-type bird
```

## 依赖

```bash
pip install requests
```

仅依赖 Python 标准库 + `requests`（与 create-pir 一致）。
