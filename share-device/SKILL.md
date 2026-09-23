---
name: share-device
description: 把 VicoHome / KiwiBit / VicoNature 设备从 admin 账号分享给另一个账号（产生真实分享关系，被分享者会在自己的设备列表里看到这台设备），并支持解除分享、查看已分享列表三个动作。当用户说"分享设备"、"share device"、"造一个 sharer 视角"、"sharer 测试数据"、"添加好友设备"、"解除分享"、"踢掉分享者"、"requireshare / handleapproval / undoshareself" 时使用此 Skill。三个动作：share（admin → sharer 全流程，默认同意，可加 --decline 拒绝）、unshare（默认 sharer 主动退出，加 --by-admin 改 admin 强制收回）、list（看 admin 视角已分享列表 + sharer 名下设备）。
---

# share-device

## Description

把一台已绑定的设备从 admin 账号 **真实分享给** 另一个账号，让 sharer 在自己 App 里看到这台设备。复刻 MeterSphere 场景 `OEM_设备分享` (id `621c0b5a-...`) 的 9 步 API 链路，已在 KB prod 端到端验证。

适用场景：
- L4 回归 / UI 测试需要"sharer 视角"的脏数据（同一台设备在多账号下可见）
- 联调验证设备分享流程（申请 → 审批 → 解除）四个分支
- 诊断生产事故时复现"分享关系异常"的场景

不适用：给真用户分享（协议要求 sharer 主动调 `/device/requireshare`，必须有目标账号密码，仅适用测试账号家族）。

本 skill 与 [create-pir](../create-pir/) 是姊妹关系：

| 需求 | 用哪个 skill |
|---|---|
| 造一条相册事件（PIR / 视频 / AI tag） | `create-pir` |
| 把设备从 A 账号分享给 B 账号 | **`share-device`**（本 skill） |
| 造完后再以 sharer 视角验证 | 先 `share-device share` → 再用 sharer profile 跑 `create-pir` |

## 三个 action 速查

```bash
# 1. share — admin 把设备分享给 sharer，admin 自动同意，sharer 名下设备 +1
python scripts/share_device.py --profile <name> --action share

# 2. unshare — 默认 sharer 主动退出
python scripts/share_device.py --profile <name> --action unshare
#    或 admin 强制收回（仅设备真 owner 能用，被二级分享的 admin 调会 -9999）
python scripts/share_device.py --profile <name> --action unshare --by-admin

# 3. list — 看 admin 视角已分享列表 + sharer 名下设备列表
python scripts/share_device.py --profile <name> --action list
```

## 9 步 API 链路（来源：MS `OEM_设备分享`）

```
share 流程（默认同意）：
  1. admin login                  → /account/login
  2. sharer login                 → /account/login
  3. (验证) sharer listuserdevices(基线)
  4. admin getshareid             → /device/getshareid       (data.shareId, expireTime ~30min)
  5. sharer requireshare          → /device/requireshare     (推送 messageId)
  6. admin recentapprovals        → /device/recentapprovals  (data.list[].id = approvalId)
  7. admin handleapproval         → /device/handleapproval   (status=0 同意 / 1 拒绝)
  8. (验证) sharer listuserdevices → 列表 +1
  9. (验证) admin sharestatus      → 已分享列表 +1

unshare 流程：
  默认：sharer 主动退出  → /device/undoshareself  (body 只要 serialNumber)
  --by-admin：admin 收回 → /device/undoshare      (body 要 serialNumber + userId)
```

## Rules

### 公司特定规则

1. **🔴 admin 强制收回 (`--by-admin`) 只有设备真 owner 能用**
   - 被二级分享的 admin（自己也是被 share 的）调 `/device/undoshare` 会拿到 `result=-9999`
   - 怎么判断"真 owner"：调 `/device/sharestatus`，**真 owner 不会出现在自己的列表里**
   - 兜底：用 sharer 主动退出（默认 action，不加 `--by-admin`）

2. **🔴 OEM 品牌走自己的 business_api 域**
   - VicoHome / VicoNature → `api-us.vicohome.io`（VN 是 VH 的 OEM 壳，tenantId 都是 `vicoo`）
   - KiwiBit → `api-us.kiwibit.com`（独立租户 `kiwibit`）
   - preset 已按此配好，用户不需要手动改

3. **🔴 share 操作会产生真实分享关系**
   - sharer 会真的在自己 App 看到这台设备
   - prod 上做完务必记得 unshare 清理（或保留作为测试基础数据）
   - 操作的是真账号，不是 mock；脚本不强制 prod 二次确认（与 create-pir 不同），因为 share/unshare 可逆且无写入相册

4. **🔴 status 字段：0=同意，1=拒绝**
   - `--decline` 让 admin 拒绝（这之后 sharer 不会拿到设备）
   - 测试拒绝场景时验证：`sharer listuserdevices` 不应有这个 sn

### 推荐工作流

新场景前先跑 `--action list` 看清当前分享关系，再决定 share / unshare。

## 配置

### Profile 推荐用法

```bash
# 首次：创建 profile（交互式）
python scripts/share_device.py --init-profile kb-prod-bird

# 日常使用
python scripts/share_device.py --profile kb-prod-bird --action share
```

profile 存到 `~/.config/addx/share-device/<name>.env`（chmod 600）。

#### 密码最小化保留 + 轮换建议

profile 内的 admin/sharer 密码是**明文存储**（chmod 600 是文件级访问控制，不是
加密）。这是与 create-pir 一致的取舍：脚本要登录就必须有密码，而 OS keychain
集成在跨用户分发的 plugin 场景成本过高。

为降低风险：

- **只用专用测试账号**：永远**不要**把个人/管理员账号的密码写进 profile；只用
  公司 a2xtest@a4x.io / a3xtest@a4x.io / a6xtest@a4x.io 这类专用 QA 账号
- **定期轮换**：建议每**季度**轮换一次测试账号密码（轮换流程：登录 App
  改密码 → 跑 `--init-profile <same-name>` 覆盖更新 profile）
- **离开公司**：`--delete-profile <name>` 清掉本地 profile；同步通知 QA 团队
  从测试账号家族剔除该账号
- **不要把 profile 交叉提交**：profile 路径在 `~/.config/addx/`，不在 skill
  目录内，plugin 分发不会复制；千万不要手动把它复制进项目仓库

### 环境变量 / .env 字段

| 字段 | 说明 |
|---|---|
| `SHARE_BRAND` | vicohome / kiwibit / viconature |
| `SHARE_REGION` | us / eu |
| `SHARE_ENV` | staging / pre / prod |
| `SHARE_ADMIN_EMAIL` / `SHARE_ADMIN_PASSWORD` | admin（设备主人）凭证 |
| `SHARE_SHARER_EMAIL` / `SHARE_SHARER_PASSWORD` | sharer（被分享者）凭证 |
| `SHARE_DEVICE` | 要分享的设备 serialNumber |

CLI 标志（`--admin-email` / `--target-email` / 等）覆盖 env 和 profile。

## Decision Tree（什么时候触发本 skill）

```
用户提到 "分享设备" / "添加好友设备" / "share / unshare device"
     ↓
看 admin / sharer 双账号
     ↓
启动 share-device skill
     ├── 用户没说删除 → --action share
     ├── 用户说 "拒绝" / "decline" → --action share --decline
     ├── 用户说 "解除" / "退出" / "撤销" → --action unshare
     │      ├── 用户是 sharer → 默认（sharer 主动）
     │      └── 用户是 owner → --by-admin
     └── 用户说 "看 / 列表" / "状态" → --action list
```

## 端到端验证

每个 action 默认带 `listuserdevices` / `sharestatus` 双向验证。可加 `--no-verify` 跳过加速。

实测数据（KB prod，a2xtest → a6xtest，bird feeder `4293ff854...`）：

| Action | 时长 | 验证结果 |
|---|---|---|
| share | 15.2s | sharer 设备 1→2 ✅ |
| unshare (sharer-self) | 8.7s | sharer 设备 2→1 ✅ |
| list | 4s | admin 列表 + sharer 列表 |

## 故障排查

详见 [references/troubleshooting.md](references/troubleshooting.md)。

## Examples

### Good Example — 用 share-device 造"sharer 视角"测试数据

**场景**：测试同事说"App 端鸟类卡片在被分享账号下显示异常"。需要还原：admin 名下的喂鸟器分享给一个测试 sharer 账号。

```bash
# 一行：admin 把喂鸟器分享给 sharer，自动同意，双向验证
python scripts/share_device.py \
    --profile kb-prod-bird \
    --target-email a3xtest@163.com --target-password Addx1234 \
    --action share
```

输出 `verified_visible: true` + `sharer_devices_after: 1`，可立即用 sharer 账号在 App 上复现。

### Good Example — 串联 create-pir 造完整 sharer 视角脏数据

```bash
# 1. 分享设备
python ../share-device/scripts/share_device.py --profile kb-prod-bird --action share

# 2. admin 视角造 PIR（事件会出现在 admin + 所有 sharer 的相册）
python ../create-pir/scripts/create_pir_event.py --profile kiwibit-prod-a2xtest --object-type bird

# 3. 验证 sharer 也能看到
python ../share-device/scripts/share_device.py --profile kb-prod-bird --action list
```

### Bad Example — 试图给真用户分享

```bash
# ❌ 用户不知道目标账号密码，必然失败
python scripts/share_device.py \
    --target-email someone@gmail.com --target-password '?未知?' --action share
```

**为什么不行**：协议要求 sharer 主动用自己的 token 调 `/device/requireshare`。给真用户分享必须走基站邮箱邀请流（路径 B，v1.0 未实现）或让用户在 App 自己点同意。

### Bad Example — 用二级 admin 强制收回

```bash
# ❌ a2xtest 是被分享的 co-admin（不是真 owner），调 undoshare 会拿到 -9999
python scripts/share_device.py --profile kb-prod-bird --action unshare --by-admin
```

**为什么不行**：被二级分享的账号无权代真主人收回别人。脚本会给友好提示。

**修法**：去掉 `--by-admin` 走 sharer 主动退出（默认）。

## 不做什么

- **不做基站 (SS121/SS131) 邮箱邀请流**：v1.0 只覆盖摄像头主流（路径 A）。基站需要 `/device/share/invite/email` + IMAP 拉邮件，依赖更多，后续按需补。
- **不做联动 create-pir 造 sharer 视角脏数据**：share 完后用户手动调 `create-pir --profile <sharer>` 即可。两个 skill 解耦，profile 互通。
