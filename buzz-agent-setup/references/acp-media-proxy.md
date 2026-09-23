# ACP 图片代理（不改 Buzz 源码）

## 结论

stock `buzz-acp` 会把 NIP-92 `imeta` 完整放进 prompt 的 `Tags:` 文本，但不会把媒体字节变成 ACP image block。默认解法不是 fork Buzz：在 `buzz-acp` 与原 ACP adapter 之间放
[`buzz_acp_media_proxy.py`](../scripts/buzz_acp_media_proxy.py)。代理保持原 adapter basename，从而保留 Buzz 对 `claude-agent-acp`／`codex-acp` 的识别；它只拦截带 `params.prompt` 的 NDJSON 请求，调用 stock `buzz media get` 以 Agent 自己的 Blossom 身份下载，再追加 inline base64 `image` block。

不得把受保护媒体 URL 写进 image block 的 `uri`；adapter 会优先自行拉 URL，绕过 Buzz CLI 的鉴权并得到 `authentication failed`。代理只写 `data` 与 `mimeType`。

## 默认行为与边界

- adapter 的 initialize response 明确声明 `agentCapabilities.promptCapabilities.image=true` 后才下载；否则保持 text-only。
- JPEG／PNG／GIF／WebP；每个 prompt 最多 9 张、单张 10 MiB、合计 20 MiB。
- 去重键是 imeta SHA-256；下载后再次核对 size、SHA-256 与 magic MIME。
- 下载失败 fail-soft：原文本 prompt 仍发给 adapter，stderr 只记 found／added／failed／bytes，不打印 URL、key 或 auth tag。
- `buzz media get` 自身限制 relay 同源 `/media/` URL、使用 kind:24242 `t=get` Blossom auth，并带 Agent 现有 `x-auth-tag`。代理不复制、解析或落盘私钥，也不写临时图片。
- 普通 `session/prompt` 与包含 `params.prompt` 的 steer 请求都能增强；无需修改 `buzz-acp`。

## 安装

先更新 addx plugin，并解析出唯一、固定 revision 的 install path；不要把运行时指向开发 worktree。把代理复制到 owner-owned、不可变目录，并让文件名与真实 adapter 相同：

```bash
PLUGIN_ROOT=<resolve_plugin_install.py 回执里的 install_path>
REAL_ADAPTER=<原 BUZZ_ACP_AGENT_COMMAND 的绝对路径>
ADAPTER_NAME=$(basename "$REAL_ADAPTER")
PROXY_SHA=$(sha256sum "$PLUGIN_ROOT/skills/buzz-agent-setup/scripts/buzz_acp_media_proxy.py" | awk '{print $1}')
PROXY_DIR="$HOME/.local/share/buzz-agent-setup/acp-media-proxy/$PROXY_SHA"
install -d -m 700 "$PROXY_DIR"
install -m 0555 "$PLUGIN_ROOT/skills/buzz-agent-setup/scripts/buzz_acp_media_proxy.py" "$PROXY_DIR/$ADAPTER_NAME"
```

每个 Agent 的 0600 env 把原 command 移到代理的显式下游配置；默认启用：

```bash
BUZZ_ACP_MEDIA_ADAPTER_COMMAND=<原 adapter 绝对路径>
BUZZ_ACP_MEDIA_BUZZ_CLI=<stock buzz CLI 绝对路径>
BUZZ_ACP_AGENT_COMMAND=<PROXY_DIR>/<与原 adapter 相同的 basename>
```

`BUZZ_ACP_AGENT_ARGS` 不变，代理会原样转发 argv。若本机 launcher 只在 command 等于真实 Claude adapter 时才导出 `CLAUDE_CODE_EXECUTABLE`，还要把该变量直接写进 Agent 的 0600 env；不能因 command 改成代理而悄悄切换底层模型 runtime。

升级时创建新 SHA 目录、原子替换 env 中的 command 后重启；不覆盖旧目录。回滚为恢复 env 备份中的原 `BUZZ_ACP_AGENT_COMMAND`、删除两个 `BUZZ_ACP_MEDIA_*` 键并重启。旧代理目录保留到验证完成。

## 验证

L1：

```bash
python3 -m unittest skills/buzz-agent-setup/tests/test_buzz_acp_media_proxy.py -v
```

L3：重启后核对进程树包含同名 proxy 与真实 adapter；日志必须出现 adapter online，且不出现启动循环。用带 imeta 的合成 ACP prompt 验证代理追加 `type=image`、含 `data/mimeType`、不含 `uri`。

L4 必须是真实链路：飞书群里的**非 Buzz 成员**发一张能客观判定内容的图片并真实 @ 目标 Agent；回读 Buzz 事件必须同时有 imeta 和目标 Agent 的真实 `p` tag；代理日志 `added>=1, failed=0`；Agent 在同一 Thread 回复图片内容。只有同步成功或只有文字回复都不算 L4。
