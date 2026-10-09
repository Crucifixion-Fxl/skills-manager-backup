# Flutter 接入

## 现状：未实现，不能生成代码

Flutter 版 Collect SDK 目前只是提案（`infra/ai-data-platform` issue #8）：dart:ffi 绑定同一个 C ABI（`collect-sdk/core/include/collect/collect.h`），原生库由 Bazel 交叉编译到 Android / iOS。没有代码、没有包、没有发布渠道。

还有一层更前置的风险没验证：手机端能不能写 Rerun（`rerun_c` 交叉编译到移动端，官方没有把移动端列为支持平台）——这是 `docs/integration/launch_monitor/implementation-plan.html` 的风险 R5，交叉编译可行性和包体积都还没验证过。

## 遇到这个请求时怎么办

1. **不要生成任何代码**——没有包可以 `flutter pub add`，编出来的依赖行用户装不到。
2. 如实告诉用户：Flutter 版还在提案阶段（issue #8），且依赖一个未验证的前置风险（R5）。
3. 如果用户是要看 launch_monitor / golf App 的完整集成方案（这是目前唯一具体在推进的手机端场景），指向：
   - [`docs/integration/launch_monitor/implementation-plan.html`](https://gitlab.addx.ai/infra/ai-data-platform/-/blob/m1-collect-ingest/docs/integration/launch_monitor/implementation-plan.html) —— §05 B 段写了会话上传器的调用顺序（设计层面，不是代码）
   - issue #8（Flutter 插件）、issue #23（golf App 业务集成）
4. 如果用户坚持要"先写着，等 SDK 出了再接"，可以按 §05 B 段的调用顺序给一段**明确标注"设计中，非真实 API"**的伪代码帮助理解调用形状；不要省略这条标注。

## 原生侧实现时的硬性要求（issue #26）

原生传输（Android OkHttp / iOS URLSession）驱动 `collect_next_action` / `collect_on_result` 时，响应体上限不能低于 32 MiB（begin 响应列出每片的预签名 URL，上限 1 MiB 会让约 11 GiB 以上的文件必定失败），按 `Content-Length` 一次分配，PUT 从文件区间流式读。手机上写 RRD 属于「离线转写」，要按 SKILL.md Step 4.5 加积压上限。详见 `docs/collect-sdk/integration.html`。

## 什么时候这份文件需要更新

issue #8 有实际产出（哪怕只是内部预览版）之后，这份文件要换成和 `python.md`/`cpp.md` 一样的"拉包 + 调用代码"结构，不再是"未实现"状态。
