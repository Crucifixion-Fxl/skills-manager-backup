# 连接、token、网络

正本：https://pages.addx.ai/infra/ai-data-platform/site/docs/hub.html#environments（与本文冲突时以它为准）。

## 环境

各环境的实际地址只维护在正本的环境表里（上面的链接），这里不抄一份，免得过期。取法：

- SDK 用 gRPC 地址：`export RERUN_HUB_URL=rerun+http://<host>:51234`
- 控制台和 `/hub/v1` 用 HTTP 地址：`export RERUN_HUB_HTTP=http://<host>:8877`（同一主机）

2026-09-27 的状态：办公网预览可用（演示数据，可能随时重灌）；staging 只有集群内地址，集群外连不上；prod 未上线。

集群外的对外入口还没定（infra/ai-data-platform#39，Ops Todo H7）。在那之前，日常取数用办公网预览。

## token

- 权限按 hub → project → dataset 三层。角色：`viewer`（读、分享）、`analyst`（再加原始包下载）、`producer`（写入）、`owner`（全部，包括建数据集）。取数用 `viewer` 就够了。
- **个人口令**：在 Hub 控制台登录后，顶栏切到要用的 project，在「我的口令」签发。口令只对当前 project 有效，也不会超出你自己的权限。
- **没有权限**：找这个 project 的 owner，在控制台「成员」里给你授予角色。没有「申请访问」流程（ADR-0004）。还不能登录控制台的环境（tokens 模式），向 Hub 维护人（jchen）要，说明环境、project、用途和期限。
- 拿到后执行 `export RERUN_HUB_TOKEN=...`。先 `curl -H "Authorization: Bearer $RERUN_HUB_TOKEN" <http>/hub/v1/me`，看自己在哪些 project 有什么能力。
- 泄露或不用了：`POST /hub/v1/tokens/{jti}:revoke`，或找维护人吊销。
- 训练的 dataloader 另外要 `export REDAP_TOKEN="$RERUN_HUB_TOKEN"`（worker 自建客户端时只认这个变量）。

## 网络

SDK 取数时，Hub 只返回 chunk 位置和对象的**预签名 URL**，数据字节由 SDK 直接对对象存储发 Range 请求拉取。所以跑取数的机器要能直连对象存储：
- 办公网预览：同一主机上的 MinIO（端口 19000）
- US：S3 / Linode
- CN：腾讯云 COS

连得上 Hub 却连不上桶，表现是取数超时或失败。服务端强制经 Hub 转发（FetchChunks）时结果相同，只是更慢。预签名 URL 默认 300 秒过期，拿到就能下载，不能外传。

## 版本

`client.version_info().version` 必须是 `0.38.1`，与本地 `rerun-sdk` 一致。不一致就停下来，不要试别的版本。
