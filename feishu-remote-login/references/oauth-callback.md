# 用已登录的无头浏览器完成「授权码回调到 localhost」的 OAuth

有些内部平台的 CLI 或 skill 用飞书 OAuth 换取自己的 token：脚本在本机起一个 `localhost:<端口>/callback` 的临时服务，再让浏览器打开飞书授权页，授权后飞书带着一次性 `code` 回调到这个地址，脚本用 `code` 换 token。在无图形界面的主机上，这一步「打开浏览器并点授权」可以交给已经登录飞书的无头浏览器会话来做。

前提：`remote_login.js` 的会话已经登录飞书（见 SKILL.md）；授权的是 **owner 本人**并且 owner 明确同意做这件事。

## 步骤

1. **用临时 HOME 起换 token 的脚本**，让它的缓存文件落在临时目录，而不是真实家目录：

   ```bash
   TH=$(mktemp -d); chmod 700 "$TH"; trap 'rm -rf "$TH"' EXIT   # 失败或中途中断也会清掉临时目录
   ( umask 077; cd "$TH" && HOME="$TH" node <换 token 的脚本> <参数> > out 2> err & )
   ```

   点「授权」之前，先确认 `localhost:<端口>` 上监听的正是你刚起的这个进程（`ss -ltnp | grep <端口>` 看 PID）。端口若被别的进程占了，回调里的一次性 `code` 会落到它手里。

   为什么：这类脚本通常把 token 缓存到 `~/.<平台>-token-<区域>`，而同一个 skill 装在别的 agent 上时，会先读这个缓存、悄悄用上 owner 的 token。临时 HOME 保证只有你拿到的那一份。

2. **让已登录的浏览器打开授权 URL**（`goto`）。飞书授权页会写「请求以下飞书账号进行授权」并列出请求的权限。**先读一遍请求的权限**：只该是「获取用户身份标识」这类最小权限；权限过大就停下来问 owner。同时确认授权 URL 里的 `redirect_uri` 是 `localhost`（就是你刚起的那个回调），且页面上的应用名是你要授权的那个应用：点「授权」用的是 owner 的完整飞书身份。

3. **点「授权」**。页面上有多个含「授权」字样的元素（例如「应用授权管理」），用实心主按钮精确定位，先从 `page.html` 里列出所有 `<button>` 再选：

   ```bash
   printf '%s' '{"click":["button.ud__button--filled-primary:has-text(\"授权\")"],"wait":5000}' > cmd.txt.tmp && mv cmd.txt.tmp cmd.txt
   ```

   成功后浏览器会跳到 `http://localhost:<端口>/callback?code=...`，页面显示「授权成功，可以关闭此页面」；换 token 的脚本收到回调、换出 token 后退出。`code` 是一次性的，也不要打印在回复里。会话写到磁盘的 `url.txt` / `state.txt` / `result.txt` 只有主机和路径，不含 `?code=...`；但 `page.txt` 是回调页面的文字，读完就删。

4. **验证并落地**：用 `/api/current` 之类的接口确认 token 有效，并读出它的**角色**（普通用户还是管理员），据此判断能给谁用。写进目标文件时 `umask 077`，写完删掉临时 HOME，并确认真实家目录里没有留下缓存：

   ```bash
   rm -rf "$TH"; ls ~/.<平台>-token-* 2>/dev/null | wc -l   # 必须是 0
   ```

## 之后要把这个 token 给 agent 时

按 SKILL.md 规则 6：必须是 owner 明确同意的例外，同时具备只读白名单调用脚本（拒绝管理类接口，本地就拒、不发请求）、过期处理（token 过期时如实报告，不重试）、审计（只记方法和路径，不记请求体和 token）、每日上限，并写进凭据表和 Canvas。token 放在 `0600` 文件里、由脚本读取，不放环境变量（环境变量要重启进程才能换，过期就得重启）。owner 的飞书登录态本身仍然不给任何 agent。
