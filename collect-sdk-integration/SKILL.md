---
name: collect-sdk-integration
description: 把 ai-data-platform 的 Collect SDK 接进当前项目（Python / C++ / Flutter），按项目技术栈拉取对应产物、生成 begin/PUT/commit 调用代码。当用户说"接入 collect sdk"、"往 rerun-hub 提交数据"、"从 Nexus 装 collect-sdk"、"我要提交 RRD 到 gateway"时触发。不回答"Collect SDK 是什么"这类架构咨询——那类问题直接读 infra/ai-data-platform 仓库的 collect-sdk/architecture.html。
---

# collect-sdk-integration — 接入 Collect SDK

Collect SDK 是 `infra/ai-data-platform` 仓库给设备、App、Web Tool 和云服务用的采集上传库：写数据的一端用官方 Rerun SDK 把内容记成 RRD，Collect SDK 只负责把这份文件提交给 collect-gateway（`begin` 拿许可、直传对象存储、`commit` 交回校验值，Gateway 通过后登记进 Hub）。本 skill 只做**已确定要接入**之后的落地——语言识别、拉包、生成调用代码；不是"Collect SDK 是什么"的架构咨询。

## Description — 现状 SSOT（先查这里，不要凭记忆）

| 语言 | 状态 | 分发方式 | 权威来源 |
|---|---|---|---|
| Python | 实现中，目标 2026-10-09（issue #3） | Nexus PyPI-style hosted repo，当前 CI 变量指向 `nexus-sg.addx.live` 的 `devt-hosted`（**未最终定，见下方"未定项"**） | `collect-sdk/python/`、`.gitlab-ci.yml` 的 `collect-sdk:release-*` job |
| C++ | 已实现（`core/` C ABI + `transport_curl/`），**尚未发布到公司 Bazel Registry**（issue #9 未开始） | 当前只能源码依赖：Bazel `bazel_dep` + `git_override` 指到 `infra/ai-data-platform` 仓库 | `collect-sdk/MODULE.bazel`、`collect-sdk/core/include/collect/collect.h` |
| Flutter | **未实现**，只是提案（issue #8，dart:ffi 绑定同一个 C ABI），手机端能不能写 Rerun 也是未验证风险 | 无 | `docs/integration/launch_monitor/implementation-plan.html` 风险 R5 |

**未定项（写代码前先确认，不要替用户拍板）**：
- Python 的 Nexus hosted repo 名称：`.gitlab-ci.yml` 里的 `COLLECT_SDK_NEXUS_REPOSITORY: devt-hosted` 带着一条 TODO——复用现有 `devt-hosted`，还是给 ai-data-platform 单独建一个 `s3-*` 后端的 repo，这个决定没定（issue #19）。定之前 `release-publish` job 会失败在 `NEXUS_USER`/`NEXUS_PASSWD` 检查上。
- 通用 Nexus 访问规则（凭证、repository/blob store 红线）看 `addx-nexus-usage` skill，本 skill 不重复。

## Rules — 边界

- **只做接入落地**，不做架构咨询（"Collect SDK 是什么""为什么这么设计"直接读 `collect-sdk/architecture.html`）。
- **不编造未发布的东西**：C++ 没有 Bazel Registry 坐标就不编一个；Flutter 没有包就不生成 `pubspec.yaml` 依赖行。三种语言现状不一样，如实分开说。
- **不替用户在未定项上拍板**：Python 的 hosted repo 名称没定，生成代码时用当前 CI 变量的值并注明"以 issue #19 结论为准"，不要自己选一个改掉。
- **字段和调用顺序按信封契约来**：`recording_id`、`layer`（必填，不用 `base` 默认值）、幂等键、`source_record_id`——不要自创字段名。信封没有 schema 名，不要给集成加 schema 版本字段。
- **凭证规则复用 `addx-nexus-usage`**：Nexus 账号从环境变量 / CI Secret 读，不写进代码、Dockerfile、日志。

## 使用流程

### Step 0：确认目标语言

用户带了参数（如 `/collect-sdk-integration python`）就跳过；否则问一句"这次是 Python（云端作业）、C++（自己联网的设备）还是 Flutter（手机 App）？"——不要在用户没说清楚时猜。

### Step 1：识别当前项目技术栈（辅助确认，不替代 Step 0 的追问）

- `pyproject.toml` / `requirements.txt` → Python
- `MODULE.bazel` / `WORKSPACE` / `BUILD.bazel` → C++（Bazel 项目）
- `pubspec.yaml` → Flutter（**先看 Flutter 状态是"未实现"，跟用户确认清楚后再继续，不要直接生成代码**）

### Step 2：读取对应的语言参考

- Python → [`references/python.md`](references/python.md)
- C++ → [`references/cpp.md`](references/cpp.md)
- Flutter → [`references/flutter.md`](references/flutter.md)（**先读这份文件的"现状"一节，如实告知用户暂时接不了，不要往下走生成代码的步骤**）

### Step 3：核对信封字段（三种语言共用）

不管哪种语言，都要向用户确认或从项目上下文推断这几个字段，缺了不要瞎填：

| 字段 | 来源 | 备注 |
|---|---|---|
| `dataset` | 凭证 / 用户配置 | 可省略，省略时用凭证上的 Dataset |
| `recording_id` | 这次实验/会话的 id，必须和 RRD 里写的一致 | 不要自己生成一个不相关的值 |
| `layer` | 用户指定，必填 | `recording` / `algo_dump` / `calibration` / `trackman` / `metrics`，不用官方"省略即 base"的默认值 |
| `idempotency_key` | 按调用方约定派生，通常是 `<生产者>:<recording_id>:<layer>` | 同一次逻辑提交复用同一个键，重试/续传不产生第二份对象 |
| `source_record_id` | 生产者自己的记录号 | 和 Hub 的记录 ID 分开存放，不混用 |

完整字段表和线协议见 `infra/ai-data-platform` 仓库的 `collect-gateway/envelope.html`；本 skill 不复制维护第二份，字段有变以那份为准。

### Step 4：生成接入代码

按对应语言参考文件的模板生成：拉包 / 依赖声明 + `begin` → 直传 → `commit` 的调用代码。**只生成基础设施代码**（提交流程本身），不猜业务逻辑（比如"什么时候该提交"由调用方的业务场景决定，不是本 skill 该管的）。

### Step 4.5：带上资源要求（写 RRD 与自写传输，生成代码时一起给）

设备和手机的内存、CPU 都有限。Collect SDK 在上传一侧已经做了能做的（硬件 SHA、续传追加写），下面几条只有集成方能做，生成代码时要一起写进去，不要只给提交流程。权威说明和实测数字：`infra/ai-data-platform` 的 `docs/collect-sdk/integration.html`（总览与上传一端）和 `integration-rrd.html`（写 RRD 一端），Pages：https://pages.addx.ai/infra/ai-data-platform/collect-sdk/integration.html 。

**写 RRD 的代码（Rerun SDK）**
- **积压上限（必须）**：Rerun 没有反压，写盘跟不上时数据全部留在内存、没有上限。记下交给 Rerun 的字节数，减去 RRD 文件大小；超过上限（建议 8 MiB）时——**离线转写**（会话结束后把已有数据写成 RRD，手机上就是这种）调用 flush 阻塞等它写完；**实时录制**不能阻塞采集线程，先停写视频这类大层、积压回落再恢复。实测：离线转写峰值 353 → 65 MiB；实时场景用阻塞式等待会让采集落后真实时间 168 s。
- **建流后立刻 `save()`（必须）**：没有 sink 的流把数据一直留在内存。
- **结束时在后台线程 flush 和销毁流**：`flush_blocking(timeout)` 超时只是把控制权还回来，销毁流仍会等到写完；不要放在 UI 线程或关机路径上同步等。
- 1000 Hz 以上的数据攒 50～100 帧用 `send_columns` 发一次；时间轴用设备时钟时关掉 `log_time`。

**自己实现传输时（例如 Flutter 原生侧）**
- **响应体上限不低于 32 MiB（必须）**：begin 响应列出每片的预签名 URL，1 万片时有几 MB；上限 1 MiB 会让约 11 GiB 以上的文件必定失败。按 `Content-Length` 一次分配；PUT 从文件区间流式读；`headers_json` 原样发送。
- 不要在上传前自己再算一遍 SHA-256，SDK 会算（并用 CPU 的 SHA 指令）。
- `state_dir` 放持久存储，不要放 tmpfs。

### Step 5：如实报告现状，不假装成功

- Python：如果 issue #19 的 hosted repo 名称还没定，明确告诉用户"生成的安装命令用的是当前 CI 变量值，正式发布前可能会变"。
- C++：明确告诉用户"这是源码依赖写法，不是从制品仓库拉预编译产物；等 issue #9 发布到 Bazel Registry 后要换成 `bazel_dep` 的版本号写法"。
- Flutter：不生成代码，把 issue #8 和 implementation-plan.html 的风险 R5 链接给用户，说明当前接不了。

## Examples

### Good

**用户**：`/collect-sdk-integration python`，项目里有 `pyproject.toml`。

✅ 我：识别到 Python 项目 → 读 `references/python.md` → 确认 `recording_id`/`layer`/幂等键怎么来 → 生成 `pip install --index-url .../devt-hosted/simple/ addx-collect-sdk` 安装命令（注明 hosted repo 名称待 issue #19 定稿）+ begin/PUT/commit 调用代码 → 提醒"Python wheel 目标 2026-10-09 交付，现在这条安装命令可能还装不到"。

**用户**：想在手机 App 里接 Collect SDK。

✅ 我：先读 `references/flutter.md`，看到"未实现"，告诉用户 Flutter 版还没做（issue #8），手机端能不能写 Rerun 也没验证（R5），现在没有代码可生成；如果是要看 golf App 的完整方案，指向 `docs/integration/launch_monitor/implementation-plan.html`。

### Bad

❌ 编一个 `pub.dev` 包名和版本号让 Flutter 项目 `flutter pub add` ——包不存在，用户装不到，还会误导他去查一个从没发布过的包。

❌ 看到 `MODULE.bazel` 就生成 `bazel_dep(name = "addx_collect_sdk", version = "0.1.0")` 假装能从公司 Bazel Registry 拉——issue #9 还没做，这个 `bazel_dep` 版本号解析不出来；应该用 `git_override` 的源码依赖写法（见 `references/cpp.md`）。

❌ 信封字段自己发明一个 `schema_version` 字段——信封本来就没有 schema 名，加了字段和真实契约对不上。

❌ 只生成「Rerun 写 RRD → Collect SDK 提交」的调用代码，不加积压上限——写盘一慢，进程内存就按数据产生速度无上限上涨（实测 16 Mbps 视频时 +60 MiB/min），手机上会被系统杀掉。见 Step 4.5。
