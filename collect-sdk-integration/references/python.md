# Python 接入

状态：实现中，目标 2026-10-09（`infra/ai-data-platform` issue #3）。绑定方式是 nanobind（稳定 ABI，CPython 3.12+ 一个 wheel），源码在 `collect-sdk/python/`。给 cloud-ingest 这类云上作业用。

## 拉包

Nexus 上的 hosted repository 名称**还没最终定**（issue #19）。当前 `.gitlab-ci.yml` 的 `COLLECT_SDK_NEXUS_REPOSITORY` 变量值是 `devt-hosted`；生成安装命令时用这个值，并向用户说明"以 issue #19 结论为准，可能会变成 ai-data-platform 专属的 repo"。

```bash
pip install \
  --index-url https://nexus-sg.addx.live/repository/devt-hosted/simple/ \
  addx-collect-sdk
```

Nexus 通用访问规则（凭证从哪来、内网白名单、CI 团队账号）看 `addx-nexus-usage` skill，这里不重复。

版本号规则：tag 是 `collect-sdk-v<major>.<YYMMDD>.<patch>`（例：`collect-sdk-v0.260926.0`），只在 `infra/ai-data-platform` 主干上已评审合并的提交才允许发布。

## 调用代码

```python
# 1. 用 Rerun SDK 把这次实验记成文件（官方提供，已可用）
import rerun as rr

recording_id = "FIELD-2026-09-25"          # 与目标实验一致，不新起一个
rr.init("launch_monitor", recording_id=recording_id)
rr.log("video/replay_main", rr.AssetVideo(path="replay_main.mp4"))
rr.save("field-2026-09-25.recording.rrd")

# 2. Collect SDK 提交（addx_collect_sdk，Python 绑定还在实现中）
from addx_collect_sdk import Layer, CollectClient

collect = CollectClient(gateway_url, token=producer_token, state_dir="/var/lib/collect")

layer = Layer(
    dataset=None,                          # 省略则用凭证上的 Dataset
    recording_id=recording_id,
    layer="recording",                     # 必填，不用 base 默认值
    idempotency_key=f"{producer_id}:{recording_id}:recording",
    content_length=os.path.getsize(path),
    sha256=sha256_file(path),
)
grant = collect.begin(layer)                # POST /v1/layers:begin
put_object(grant.upload, path)              # 直接写对象存储，不经过 collect-gateway
receipt = collect.commit(layer, grant.object_uri, etag=put_etag)  # POST /v1/layers:commit
```

再交 `algo_dump`、`calibration` 或 `trackman` 时，只改 `layer` 和 `idempotency_key`，`recording_id` 不变，并换一份 RRD；同一幂等键加同一 sha256 是续传，换了内容要用新的幂等键，Hub 按 REPLACE 换掉这一层的指向。

## 写 RRD 时的资源要求

Rerun 没有反压：用 Rerun Python SDK 批量写 RRD 时，同样要记下交出去的字节数、和 RRD 文件大小比较，超过上限（建议 8 MiB）就调用 flush 阻塞等它写完（具体方法名以所用 rerun-sdk 版本的文档为准）；建流后立刻 `save()`。说明和实测见 `docs/collect-sdk/integration-rrd.html` R-1、R-2。

## 校验

- 字段和线协议（`POST /v1/layers:begin|commit|status|delete`）以 `collect-gateway/envelope.html` 为准。
- 提交前确认 `NEXUS_USER`/`NEXUS_PASSWD` 或对应的下载凭证已经可用；纯下载在内网白名单内通常不需要鉴权。
- 生成代码后如实告诉用户：这个包截至现在（issue #3 未完成）可能还没有真实 wheel 可下载。
