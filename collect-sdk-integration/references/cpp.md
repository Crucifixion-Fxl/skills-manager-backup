# C++ 接入

状态：**已实现**——`collect-sdk/core/`（C++17，对外是 C ABI `collect/collect.h`）+ `collect-sdk/transport_curl/`（嵌入式 Linux 直接用）。给自己联网、不经手机中转的设备用，例如场边相机。

尚未发布到公司 Bazel Registry（issue #9 还没开始）。现在只能源码依赖，不要生成假装能从 Registry 拉预编译产物的 `bazel_dep` 版本号写法。

## 拉包（源码依赖，issue #9 发布前的唯一方式）

`MODULE.bazel` 里用 `git_override` 指到 `infra/ai-data-platform`，钉在一个已发布的 `collect-sdk-v*` tag 上（tag 只在主干已评审合并的提交上打）：

```python
bazel_dep(name = "addx_collect_sdk", version = "0.1.0")
git_override(
    module_name = "addx_collect_sdk",
    remote = "https://gitlab.addx.ai/infra/ai-data-platform.git",
    commit = "<collect-sdk-v0.YYMMDD.N 对应的 commit sha>",
    strip_prefix = "collect-sdk",
)
```

`version` 字段填 `collect-sdk/python/BUILD.bazel` 里当前声明的版本号即可（发布脚本会替换成真实 tag 版本号，源码依赖场景下这个字段基本不影响解析）。**issue #9 发布到 Bazel Registry 后**，把这段改成不带 `git_override` 的普通 `bazel_dep(name = "addx_collect_sdk", version = "<真实版本号>")`。

## 调用代码

`finalize` 之后在同一台设备上直接提交：

```cpp
#include <collect/collect.h>
#include <collect/curl_transport.h>
#include <nlohmann/json.hpp>

bool submit(const Manifest& m, const std::string& token) {
    const auto config = nlohmann::json{{"gateway_url", gateway_url()},
                                       {"token", token},
                                       {"state_dir", "/data/collect/state"}}.dump();
    const auto layer = nlohmann::json{{"recording_id", m.recording_id},
                                      {"layer", m.layer},              // 必填，不用 base
                                      {"source_record_id", m.idempotency_key},
                                      {"idempotency_key", m.idempotency_key}}.dump();
    char* err = nullptr;
    collect_session* s = collect_submit_new(config.c_str(), layer.c_str(), m.path.c_str(), &err);
    if (s == nullptr) { log_error(err); collect_free(err); return false; }

    const int kind = collect_run_blocking(s, /*timeout_seconds=*/300);
    collect_action last{};
    collect_next_action(s, &last);        // 最终结果：DONE 的回执，或 FAILED 的错误分类
    const bool ok = kind == COLLECT_ACTION_DONE;
    if (!ok) log_error(last.error_code);  // 错误信息里不含 URL 和 token
    collect_session_free(s);
    return ok;                            // 失败时续传状态留在 state_dir，下次同一幂等键接着传
}
```

## 写 RRD 时的资源要求（和上传代码一起生成）

Rerun 没有反压：写盘跟不上时，写不出去的数据全部留在内存。写 RRD 的代码要自己给积压设上限（说明和实测见 `docs/collect-sdk/integration-rrd.html` R-1）：

```cpp
// 建流后立刻 save()，不要留下没有 sink 的流
rerun::RecordingStream rec("launch_monitor", recording_id);
rec.save(rrd_path).exit_on_failure();
rec.set_log_time_enabled(false);          // 时间轴用设备时钟时关掉墙钟列

long handed = 0;                          // 交给 Rerun 的字节数（视频帧、点云等大头）
const long kLimit = 8L << 20;             // 积压上限；实际内存约 30~40 MiB + 3 × 上限
auto on_disk = [&] { struct stat st; return ::stat(rrd_path.c_str(), &st) == 0 ? st.st_size : 0L; };

// 离线转写（数据已在手上）：超限就等写完
rec.log("camera/main", frame);
handed += frame_bytes;
if (handed - on_disk() > kLimit) rec.flush_blocking();

// 实时录制：不能阻塞采集线程，超限时这一帧视频不写（雷达等小数据照写），并计数
if (handed - on_disk() > kLimit) { ++dropped_frames; }
```

结束时在后台线程里 `flush_blocking()` 并销毁流：带超时的 `flush_blocking(timeout)` 只是提前返回，销毁流仍会等到数据写完。

## 续传行为（已实现，写代码前需要知道）

- 进程重启但 `state_dir` 没丢：核心直接用本地记录的 upload_id 续传，不用等 Gateway 查询。
- `state_dir` 丢了但对象存储上分片还在：`collect_submit_new` 会从 Gateway 查出已完成的分片并带回 ETag，效果一样，不重传。
- 进程被杀掉（真实掉电/断网）：只要字节已完整落地，续传不会重传；没确认的分片会补上。

## 校验

- 头文件路径以仓库里真实存在的 `collect-sdk/core/include/collect/collect.h` 为准，不要凭记忆改函数签名。
- 字段和线协议以 `collect-gateway/envelope.html` 为准。
- 生成代码后如实告诉用户：这是源码依赖，不是从制品仓库拉预编译产物；`bazel test //...` 建议先在本地跑一遍确认能编译。
