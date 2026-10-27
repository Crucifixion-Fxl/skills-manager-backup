# Troubleshooting 平台认证流程

## 凭据选择

调用者统一通过 `TROUBLESHOOTING_TOKEN` 注入凭据，请求使用 `Authorization: Bearer <凭据>`。

| 凭据 | 获取方式 | 使用与期限 |
|------|----------|------------|
| 个人 PAT（`tmt_` 前缀） | 微应用平台的 API Access Token 页面 | 不透明随机凭据，直接调用 API；期限由创建时设置及服务端上限决定，不换取 JWT、没有 refresh token |
| 交互登录 JWT | 飞书业务 OAuth；可用 `get_token.mjs` 辅助获取 | 短期登录凭据，既有契约为 24 小时，以服务端实际签发为准 |

优先复用已注入的凭据。`tmt_` 前缀仅用于识别 PAT 路径，不能据此认定有效；不要对 PAT 做 JWT 解码或套用 JWT 过期时间。非 `tmt_` 也不自动等于 JWT，需由可信凭据来源确认类型。

## PAT：创建、保存与使用

1. 使用本人飞书账号登录生产微应用平台，进入头像菜单 **API Access Token**，或打开 [我的 API Access Token](https://micro-app-platform-us.addx.live/settings/personal-access-tokens)。需要受信个人登录；历史会话提示不受信时，重新完成个人登录。PAT 本身不能调用 PAT 管理接口。
2. 在用户明确授权创建后，填写用途名称和有效期。上限以页面/能力接口为准，默认最大 365 天，不是永久有效。仅使用现有 PAT 不意味着获准创建、轮换或撤销凭据。
3. 完整 Token 仅创建或轮换时显示一次，列表前缀不能用于调用。经宿主秘密管理安全注入 `TROUBLESHOOTING_TOKEN`；不要要求用户把明文发到聊天或写进 shell 历史。遗失明文不能从列表找回，需要在授权后创建/轮换并更新调用方。
4. 先选择目标环境，再调用该环境的 `/api/current` 检查 HTTP 状态、符合目标响应契约的业务成功标记及 `result.data.userid`。仅 HTTP 200 不足以证明成功；不要打印完整响应。验证通过后复用同一凭据执行业务请求。
5. PAT 绑定创建它的微应用平台内部用户主体，身份由服务端解析；不在请求里自填用户 ID 来指定所有者。权限随所有者在目标应用中的当前权限，不增加角色或资源权限，也不替代业务写入审批。`/api/current` 返回的业务 `userid` 不应当作平台内部 UUID。

US/EU、Prod/Staging 的地址见主 Skill 环境表。这些入口接入生产微应用平台，不意味着授权互通；PAT 依赖平台及目标消费应用启用支持。每个实际使用的环境分别验证，不用 Prod 成功代替 Staging 成功。

### 只读请求示例

宿主已注入 `TROUBLESHOOTING_TOKEN` 和所选 `BASE_URL` 后，下例从环境读取秘密，不放入进程命令行实参、不输出响应正文或 Token。HTTP 200 后仍需按上面的响应契约核验业务结果。

```python
import os
import urllib.error
import urllib.request

allowed_origins = {
    "https://troubleshooting-us.addx.live",
    "https://troubleshooting-eu.addx.live",
    "https://troubleshooting-staging-us.addx.live",
    "https://troubleshooting-staging-eu.addx.live",
}
origin = os.environ["BASE_URL"].rstrip("/")
if origin not in allowed_origins:
    raise SystemExit("请选择已登记的 Troubleshooting 环境")

# 不跟随重定向，防止将 Bearer 转发至其他地址。
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

request = urllib.request.Request(
    origin + "/api/current",
    headers={"Authorization": "Bearer " + os.environ["TROUBLESHOOTING_TOKEN"]},
)
try:
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        payload = response.read()  # 仅在内存中按目标 schema 核验，不直接打印
        print("HTTP", response.status)
except urllib.error.HTTPError as error:
    print("HTTP", error.code)  # 按下表分类，不自动重新登录
except urllib.error.URLError:
    print("网络或 TLS 错误；保留凭据并检查连接")
```

## JWT：保留的交互登录流程

`get_token.mjs` 使用 Node.js 内置模块。仅在无凭据且选择交互登录，或来源明确的 JWT 收到 401、允许重新登录时调用；已有 PAT 或未知类型凭据失败时不要调用它。

```bash
# 先按主 Skill 选择 BASE_URL；例如 Staging-EU。
# REGION 仅取 us、eu、staging-us、staging-eu，不能任意拼接。
REGION=staging-eu
# stdout 必须捕获；单独运行 node 会把 JWT 打印出来。
if troubleshooting_login_result=$(node <skill_dir>/references/get_token.mjs "$REGION"); then
  export TROUBLESHOOTING_TOKEN="$troubleshooting_login_result"
  unset troubleshooting_login_result
else
  unset troubleshooting_login_result
  exit 1
fi
```

脚本先检查 `~/.troubleshooting-token-{region}`，在对应 `https://troubleshooting-{region}.addx.live/api/current` 同时校验 HTTP 200、业务成功及非空字符串 `result.data.userid`。支持旧 `success: true` 和 a4x APIResponse 的 `error_code: ""`；如果两者同时出现则均须成功，若存在 `status_code` 则须为 200。契约不匹配时停止，不能只靠 HTTP 200 接受缓存。Prod-US/EU 参数分别为 `us`/`eu`。

仅无缓存、缓存返回 HTTP 401，或明确指定 `--force-login` 时，脚本才监听 `127.0.0.1:9876` 并打开飞书 OAuth，经生产微应用平台 `POST /api/auth/login/prepare`、`POST /api/auth/login/confirm` 获取 JWT。新 JWT 必须再次通过目标 `/api/current` 校验，才写缓存并输出；失败不再登录或重试。HTTP 403 / 503 / 重定向、网络错误、超时、损坏或过大的响应都停止。脚本不输出认证响应正文及底层异常原文。

已通过可信来源确认当前凭据是业务 JWT、目标接口返回 401、且允许重新登录时，才用下面的单次命令跳过旧缓存。仅对本次登录子进程移除旧凭据，宿主保留原值；成功后才替换。PAT、未知来源凭据及权限/依赖故障不得走此路径：

```bash
if troubleshooting_login_result=$(env -u TROUBLESHOOTING_TOKEN node <skill_dir>/references/get_token.mjs "$REGION" --force-login); then
  export TROUBLESHOOTING_TOKEN="$troubleshooting_login_result"
  unset troubleshooting_login_result
else
  unset troubleshooting_login_result
  exit 1
fi
```

脚本在读取缓存或联网前，检测到任意非空 `TROUBLESHOOTING_TOKEN` 就拒绝执行（含 PAT、JWT、未知类型和仅空白的值），包括 `--force-login`；不输出凭据、不启动浏览器、不改缓存。普通缓存路径读到 PAT 也拒绝发送。不要为绕过此保护而清空 PAT 或未知凭据再登录。上面的子进程例外仅用于已确认且获准重新登录的 JWT。命令示例供独立脚本/受控子 shell 使用；执行失败时不继续业务请求，宿主不要将失败输出覆盖到已有凭据配置。`--force-login` 不证明失败写操作未执行，仍需核对业务结果。

## 失败处理

| 结果 | 下一步 |
|------|--------|
| PAT 401 | 停止使用该凭据；核对到期、禁用、撤销、格式/完整性，以及平台和目标应用的 PAT 开关。不自动改用 JWT、不循环尝试、不自动轮换 |
| 已知 JWT 401 | 在当前授权范围内用 `--force-login` 重新登录一次，然后只重试只读或已确认可安全重放的请求；仍失败则停止。写操作先确认结果，不盲目重放 |
| 类型未知的凭据 401 | 报告认证失败并确认可信来源，不默认清空或替换为 JWT |
| 403 | 检查账号是否停用、目标应用/资源权限或接口是否禁止 PAT；不通过换凭据或身份绕过 |
| 503 / 网络超时 | 认证依赖或网络不可用，不能判定为过期；保留凭据，报告故障，不触发登录/轮换 |
| 200 但业务失败 / 响应不符 | 不视为认证成功；按目标契约诊断，不继续业务操作 |
| 登录端口 9876 占用 / 120 秒授权超时 | 报告登录阻塞，检查占用或授权状态，不自动终止无关进程 |

PAT 到期需在授权后创建/轮换并更新宿主注入值；JWT 重新登录不会使旧 PAT 恢复有效。撤销或禁用 PAT 会使后续校验拒绝它；清空环境变量、删除本地缓存不是服务端撤销。执行撤销、轮换、禁用前确认准确目标和用户授权，不清理其他人或其他用途的凭据。

## 安全边界

- 不把完整 Token、Cookie、授权 code 写入聊天、仓库、URL、日志、截图或命令行实参；禁用 `set -x`、请求 verbose/trace 及记录认证响应正文的日志。
- PAT 只走宿主安全注入或受控进程环境，不放入 `~/.troubleshooting-token-*`。旧 JWT helper 确实会明文写缓存，且代码未显式强制文件权限，不能宣称“仅当前用户可读”；使用前确认用户目录/文件权限或 Windows ACL，不复制已有凭据到共享位置。
- 本认证用于 Troubleshooting API，不是普通飞书 OpenAPI 凭据，不能用普通 `lark-cli` 替代；PAT 也不是 Service JWT 或任意项目均已支持的证明。

## 离线回归

从仓库根目录运行 `node --test skills/observability/troubleshooting/tests/get_token.test.mjs`（Node.js 20+）。测试从真实 CLI 入口运行，仅将网络、浏览器和 OAuth 回调替换为合成输入，缓存位于隔离临时目录；不会读取个人缓存或发起真实登录。这些结果不替代目标环境的真实登录、响应契约及业务权限验收。
