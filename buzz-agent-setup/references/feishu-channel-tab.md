# 飞书绑定群的 Buzz Channel 标签页

新建或关联飞书群时，由 Agent 在本次绑定授权范围内交付一个 **Buzz Channel** URL 标签页。
存量群补齐须有相应范围授权；不要把单群请求扩成全部群。本流程是 Agent 操作契约，
不代表现有 hostd、`bind` 或 `round` 已自动实现标签页管理。关联需求：
[skills#275](https://gitlab.addx.ai/engineering/skills/-/issues/275)。

## 目标与身份预检

1. 从当前部署的绑定配置/运行时登记读取 `binding_id`、`chat_id`、`channel_id` 与
   `people_api.base_url`，交叉核对配置与登记。只纳入已建立、未退役且属于本次授权的绑定；
   pending/conflict 或映射冲突先排除并报告。degraded 只表示同步异常，须核实映射仍有效，
   标签页添加成功不能作为同步恢复证据。不要扫描任意群或复制另一台机器的固定清单。
2. 使用宿主批准的 lark-cli 入口、profile 和身份，业务命令始终显式传 `--profile`、`--as`。
   当前个人用户身份核验后必须保持不变；不可因权限失败切换到 bot、其他应用或 MCP。
   普通个人执行使用 `--as user`；其他宿主按其已有明确身份授权执行，不能借本 reference 扩权。
3. 读写最小 scope 分别为 `im:chat.tabs:read`、`im:chat.tabs:write_only`。
   用户缺 scope 时按 lark-shared 的 split-flow 发起这两项增量授权，展示链接与二维码，
   收到用户完成确认后由 Agent 完成 device-code 轮询。不要改用更宽的 `im:chat`。
   授权只解决 API scope；操作者仍须在群内，群限制管理权限时须为群主或管理员。

## 选择可用的 HTTP 入口

- 使用已核验的 Bridge HTTPS origin，不把 `buzz://channel/<UUID>` 直接写进飞书标签页。
  当前已验证的中转形式为：
  `https://<bridge-host>/bind/open?e=<event-id>&c=<channel-uuid>`。
  2026-10-12 实测：只带 `c` 返回 400；带合法 `e`、`c` 返回 200 并提供 Buzz 消息深链。
  这是当时部署的能力，后续部署变化先重新核验，不能把纯频道路由当成已有能力。
- 优先复用该群已同步消息中的有效「在 Buzz 中打开」链接；或从该绑定的已确认投递映射
  选择真实 Buzz 事件（如已 ack 的 Buzz → 飞书消息），核对频道归属及飞书消息未撤回。
  `e` 必须为 64 位小写 hex，`c` 为规范小写 UUID；不要造随机事件 ID 或借别的频道消息。
  新绑定尚无锚点时，等首条真实同步消息成功后再添加，不为获得锚点额外发消息/唤醒 Agent。
- GET 候选 HTTPS 页面，确认最终 origin 仍是已核验的 Bridge、响应为 200，且页面提供的
  Buzz 链接内频道和事件与目标一致。HTTP 200 本身不证明频道、事件存在或当前用户可访问；
  必须结合前一步的真实消息/投递证据。消息入口会定位到该频道的一条消息，不是纯频道首页。
- 不获取 Bridge 的 HMAC key，不向链接加入凭据。Bridge 通知链接的 `s` 签名只保护
  `n` 频道显示名，验签失败会舍弃名字；已验证部署接受无 `s`/`n` 的消息入口。
  `t` 仅在已核验真实话题根时沿用，
  不猜测。消息被删除或换绑后，先选取并验证新目标，再按授权维护同一个标签页。

卡片同步复用以上入口时，保留既有行为：正文里的 `buzz://` 中和为 `buzz：//`，
卡片不输出自定义协议。`t` 与 Bridge 的 `threadRoot` 取法一致：存在 `root` 标记取该根；
没有任何标记取第一个 `e` tag；只有 `reply` / `mention` 标记则不带 `t`。
消息 ID、频道 UUID 或 HTTPS base URL 不合法时，卡片省略链接、照常发送且不因此计失败；
此降级仅适用于消息卡片，标签页仍须有通过验证的入口才能创建。

## 查重、创建与读回

执行前查 CLI help；有 typed command 时优先使用。当前未封装标签页命令时，用
`lark-cli api` 按官方接口调用（`PROFILE`、`CHAT_ID` 取上面的已核验值，
`lark-cli` 在示例中代表宿主批准的实际 CLI 入口）：

```bash
lark-cli api GET "/open-apis/im/v1/chats/${CHAT_ID}/chat_tabs/list_tabs" \
  --profile "$PROFILE" --as user
```

保存原标签页快照，按解析后的 HTTPS origin、`/bind/open` 路径和 `c` 匹配频道，
不只按名称或完整 URL 字符串查重（消息锚点与参数顺序可能不同）。同一频道已有一个有效
入口且名称/配置正确时复用，GET 其现有 URL 验证目标，不因出现更新消息而重建。
已有同频道页但名称/配置不符时，在本次维护授权内按官方更新接口修正同一 `tab_id` 后读回；
同名异目标、多个匹配项、目标不明时先报告，不能自动覆盖或删除。所有其他标签页保留。

无匹配项时，生成相对路径 `tab-request.json`，仅包含以下字段。实际 `url` 必须替换为
前面验证过的链接；不要直接执行占位示例。外部打开用 **`is_built_in: false`**，
避免飞书内嵌 WebView 拦截唤起 Buzz Desktop。

```json
{
  "chat_tabs": [{
    "tab_name": "Buzz Channel",
    "tab_type": "url",
    "tab_content": {"url": "https://<bridge-host>/bind/open?e=<event-id>&c=<channel-uuid>"},
    "tab_config": {"is_built_in": false}
  }]
}
```

```bash
lark-cli api POST "/open-apis/im/v1/chats/${CHAT_ID}/chat_tabs" \
  --profile "$PROFILE" --as user --data @tab-request.json --dry-run
lark-cli api POST "/open-apis/im/v1/chats/${CHAT_ID}/chat_tabs" \
  --profile "$PROFILE" --as user --data @tab-request.json
lark-cli api GET "/open-apis/im/v1/chats/${CHAT_ID}/chat_tabs/list_tabs" \
  --profile "$PROFILE" --as user
```

读取 CLI 的 `ok == true` 与退出码，不用顶层 `code == 0` 判断成功。每个群写前重新 GET，
本批只有一个 writer，写前保存目标和 pending 回执。超时/断连等结果未知时只 GET 对账，
不得盲目重放 POST；无法确认则保留 unknown，缺权限等明确拒绝记 failed/blocked。
前后查重不是服务端原子幂等，不承诺并发创建绝不重复。标签页达到 20 个自定义页上限时
报告阻塞，不删其他页腾位置。

## 验收与回执

用户指定「先这个群」时，该群创建并读回成功后再处理其余群；读与 HTTP 检查可以有界并行，
写入逐群执行。个别群无权只阻塞该群，其他明确授权且预检通过的群继续，保留完整失败清单。

- GET 返回恰好一个目标标签页，记录 `tab_id`，名称为 `Buzz Channel`、类型为 `url`，
  URL 指向预期频道且通过 HTTP 检查，`is_built_in` 为 false。
- 原有其他标签页的 ID、内容与配置均保留；不能只检查新增 API 返回成功。
- 本机有可用客户端自动化时由 AI 实际点击验证唤起与频道落点；仅完成 API/HTTP 校验时，
  如实写「标签页已创建并读回；客户端唤起未验证」，不声称端到端 UI 已通过。
- 在宿主私有证据目录保存：时间、执行身份（无 token）、绑定/群/频道、入口来源、
  写前快照、请求、写入结果、读回、tab_id 与 created/existing/blocked/unknown 分类。
  汇总已创建、已存在、失败与待处理数；不向 plugin 提交真实群清单、正文或凭据。
- 不靠数据库游标、进程 active 或标签页成功推断消息同步/Agent 已恢复。
  回滚只针对本次创建且有回执的 tab_id，取得对应删除授权后执行并读回；不顺带清理别人的页。

## 官方接口

- [拉取标签页](https://open.feishu.cn/document/server-docs/group/chat-tab/list_tabs.md)：GET `/open-apis/im/v1/chats/:chat_id/chat_tabs/list_tabs`
- [添加标签页](https://open.feishu.cn/document/server-docs/group/chat-tab/create.md)：POST `/open-apis/im/v1/chats/:chat_id/chat_tabs`
- [更新标签页](https://open.feishu.cn/document/server-docs/group/chat-tab/update_tabs.md)：调用前读取当前参数契约，仅维护核验后的目标 tab_id。

上述是官方 API 的 Agent 调用流程，不是随 Skill 分发的批量执行器；不要把本机临时脚本
或单次成功回执当作已发布工具、其他部署的认证证明或 hostd 自动维护能力。
