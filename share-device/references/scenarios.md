# 典型用法场景

## 1. share — admin 把设备分享给 sharer（默认同意）

```bash
python scripts/share_device.py --profile kb-prod-bird --action share
```

预期输出：
```
✅ 登录 admin (a2xtest@163.com) ...
✅ 登录 sharer (a6xtest@163.com) ...
✅ sharer 名下 N 台设备，目标 sn 不在列表里（预期：应不在）
✅ shareId=share:<hash>-prod-US-kiwibit (expireTime=...)
✅ requireshare 已发起
✅ approvalId=... targetId=...
✅ admin 已同意申请
✅ sharer 名下 N+1 台设备，目标 sn 在列表里（预期：应在）
```

## 2. share + decline — admin 拒绝申请

测试"拒绝分享"分支：

```bash
python scripts/share_device.py --profile kb-prod-bird --action share --decline
```

输出 `action: share-decline`，sharer 不会拿到设备（验证：`--action list` 看 sharer 列表里不增加目标 sn）。

## 3. unshare — sharer 主动退出（默认）

```bash
python scripts/share_device.py --profile kb-prod-bird --action unshare
```

预期：sharer 设备列表 -1，目标 sn 消失。

## 4. unshare --by-admin — admin 强制收回

⚠️ **仅设备真 owner 能用**。被二级分享的 admin 调会拿到 `result=-9999`，脚本会给出友好提示。

```bash
python scripts/share_device.py --profile kb-prod-bird --action unshare --by-admin
```

如果要显式指定收回谁（不依赖 sharer 凭证 / sharestatus 推断）：

```bash
python scripts/share_device.py --profile kb-prod-bird \
    --action unshare --by-admin --sharer-user-id 5224501
```

## 5. list — 看当前分享关系

```bash
python scripts/share_device.py --profile kb-prod-bird --action list
```

返回：
- `admin_share_list`：admin 视角已分享给谁（每条含 userId / userName / userEmail / role）
- `sharer_devices`：sharer 名下绑定的设备列表
- `sharer_target_visible`：目标 sn 是否在 sharer 列表里

## 6. 串联 create-pir：造 sharer 视角的鸟类 PIR

share-device 与 create-pir 解耦设计，profile 互通。先分享，再用 sharer profile 跑 PIR：

```bash
# step 1: admin 把鸟类喂鸟器分享给 sharer
python ~/path/to/share-device/scripts/share_device.py --profile kb-prod-bird --action share

# step 2: 用 sharer 账号造 PIR（注意：bird PIR 由真主人账号写入更稳，
#         sharer 账号写入会有权限差异）
python ~/path/to/create-pir/scripts/create_pir_event.py --profile kb-prod-a2xtest --object-type bird

# step 3: 验证 sharer 也能在自己 App 看到这条鸟类卡片
python ~/path/to/share-device/scripts/share_device.py --profile kb-prod-bird --action list
```

## 7. 多轮验证：同意 → 解除 → 再分享

```bash
for i in 1 2 3; do
    python scripts/share_device.py --profile kb-prod-bird --action share --no-verify --quiet
    python scripts/share_device.py --profile kb-prod-bird --action unshare --no-verify --quiet
done
```

`--no-verify` + `--quiet` 用于批量回归，跳过双向 listuserdevices 验证省时间。

## 8. 跨环境测试

```bash
# VH staging-eu
python scripts/share_device.py --brand vicohome --region eu --env staging \
    --admin-email vh-admin@yopmail.net --admin-password ... \
    --target-email vh-target@yopmail.net --target-password ... \
    --device <sn> --action share

# 或者先 init 一个 profile：
python scripts/share_device.py --init-profile vh-stage-eu
python scripts/share_device.py --profile vh-stage-eu --action share
```

## 9. dry-run / show-config

```bash
# 检查 profile 解析结果
python scripts/share_device.py --profile kb-prod-bird --show-config

# 列出所有 preset
python scripts/share_device.py --show-presets
```
