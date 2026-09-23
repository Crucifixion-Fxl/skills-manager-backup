# 故障排查

> share-device 9 步链路每步可能的失败模式和定位方法。

## 🎯 快速诊断

脚本日志格式 `[HH:MM:SS] ✅ <步骤名>` / `❌ <错误>`。卡在哪步决定排查方向。

```
1. login admin    →  2. login sharer    →  3. sharer baseline
4. getshareid     →  5. requireshare    →  6. recentapprovals
7. handleapproval →  8. verify sharer   →  9. admin sharestatus
```

---

## Step 1/2 — 登录失败

### ❌ `登录失败: result=-1001 (ACCOUNT_NOT_REGISTERED)`

**根因**：`app.tenantId` 与账号实际 tenant 不匹配；或邮箱根本没注册到这个环境。

**排查**：
- KiwiBit 必须 tenantId=`kiwibit` + 域名 `*.kiwibit.com`
- VicoHome / VicoNature → tenantId=`vicoo` + 域名 `*.vicohome.io`
- preset 已正确处理。如果报这个错，检查是否被 `--brand` 错传

### ❌ `WRONG_PASSWORD / result=-1021`

密码错。检查 `.env` 里 `SHARE_ADMIN_PASSWORD` / `SHARE_SHARER_PASSWORD`。

---

## Step 4 — getshareid 失败

### ❌ `result=-100x (设备不存在 / 没有权限)`

**根因**：admin 账号没有这台设备的"分享权"。

**前提条件**：
- admin 必须**已绑定**这台设备（不管是真 owner 还是被二级分享的）
- 设备 sn 拼写正确（注意大小写敏感）

**验证**：先用 `--action list` 看 admin 名下分享列表，再用 create-pir 的 `--list-devices` 看 admin 是否绑定了这台。

---

## Step 5 — requireshare 失败

### ❌ `shareId 已失效 / 已过期`

shareId 有 30min 有效期。脚本是连续调的（getshareid → requireshare 间隔 < 1s），不会触发；
**手动拼接**调用时才需要注意。

### ❌ `不能给自己分享`

sharer 与 admin 是同一账号 → 无意义操作，后端拒绝。
检查 `SHARE_ADMIN_EMAIL` ≠ `SHARE_SHARER_EMAIL`。

---

## Step 6 — recentapprovals 列表为空

### ⚠️ `recentapprovals 列表为空，sharer 申请可能未到 admin`

**原因**：requireshare 与 recentapprovals 之间的间隔太短，admin 端未物化。

**修法**：脚本默认睡 0.5s。极少触发；如果反复出现，提 issue 让我加重试。

### ⚠️ `未精确匹配 shareId，用最近一条 approval 兜底`

admin 账号此前还有别的待批申请（其他设备 / 其他 sharer）。脚本兜底拿最近一条，绝大多数情况下就是刚发的那条。如果碰巧抢到了别人的：手动指定 sharer，或先用 admin 账号在 App 里清理掉历史挂起的申请。

---

## Step 7 — handleapproval 失败

### ❌ `result=-9999 / Unknown Error`

**几乎一定是权限问题**。可能性：
1. admin 不是真 owner，无权代设备主人处理申请
2. 申请已过期（请求过 24h）

验证：换真 owner 账号；或重跑 share 流程。

---

## Step 9 — sharestatus 推断真 owner

### ⚠️ `--by-admin 调 undoshare 拿到 result=-9999`

**根因**：当前 admin 账号自己也是被分享的，不是真 owner。

**判断方法**：
```bash
python scripts/share_device.py --profile <name> --action list
```
看 `admin_share_list` —— **真 owner 不会出现在自己的 sharestatus 列表里**。
如果列表里有当前 admin email，那它就是被分享者，无法 `--by-admin`。

**两个修法**：
1. 换设备真 owner 账号到 profile 重跑
2. 改用 sharer 主动退出（默认 action，不加 `--by-admin`）

---

## 通用：脚本"hang 住"

每步带 15s timeout。如果某一步超时报 `requests.RequestException: Read timed out`：
- 网络问题 → 重试
- 反复出现：用 `curl` 直连看后端是否真挂了

---

## prod 操作的影响范围

| 操作 | 真账号是否可见 | 推送是否触发 | 可逆 |
|---|---|---|---|
| share (同意) | ✅ sharer App 立即看到设备 | ✅ sharer 收到推送 | ✅ unshare 可逆 |
| share + decline | ❌ 不影响 | sharer 收到 "申请被拒" 推送 | n/a |
| unshare (self/admin) | ✅ sharer 设备消失 | sharer 可能收到推送 | ✅ 重新 share 可逆 |
| list | ❌ 只读 | ❌ | n/a |

prod 操作前建议先 `--action list` 看清现状再决定。

---

## 联系人

新发的失败模式，把完整脚本输出（含 `result`, `msg`, 响应 JSON）贴到 issue。
