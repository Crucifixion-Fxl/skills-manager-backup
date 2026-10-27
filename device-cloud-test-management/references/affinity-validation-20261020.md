# 资源亲和性验收记录（2026-10-20）

## 验收范围

验证统一 `device_cloud.py` 入口的 Feature/CLI 约束解析、预检、确认快照、原生建单、实际分配回读及终态资源释放。不修改 Server、Client、Host 或插件，不部署，不以本次结果宣称 BLE 业务用例通过。

## 本地验证

- 真实云测前 Skill 全部单元/契约测试：214 passed；审查补充边界修复后为 216 passed。
- `python -X utf8 scripts/validate.py --skill skills/quality/device-cloud-test-management`：0 errors / 0 warnings。
- `git diff --check`：通过。
- 测试使用 `DEVICE_CLOUD_CLIENT_ROOT` 指向兼容的只读 Client checkout，仅用于既有跨仓配置契约检查；未修改 Client。

## 真实云测

- Device Cloud：prod-cn。
- 测试仓库：[firmware-tests](https://gitlab.addx.ai/DEVT/test-suite/firmware-tests)，`main@3146298f30bcf09694cc19a568143c800489087e`。
- UID：`a8c2f115`，真实 BLE/Wi-Fi 绑定；1 Plan / 1 Job / 1 Scenario，并发 1。
- 固件配置：`1.21.40-d`；App：Kiwibit Android 2.25.0 staging，build 184883，commit 4198be5。
- 资源：KF451 + Pixel 7 Pro Android 真机。
- 约束：`belong_to_host`、`belong_to_cabinet`，均为原生 `AFFINITY`，关联 `camera`、`phone`。
- Plan：16059，`ai-affinity-kf451-20261020-platform-fix`。
- [Job 18821](https://device-cloud.builder.addx.live/test/job/detail/18821)：11:53:13（北京时间）创建，11:53:24 运行中回读确认完整分配、同 Host、同机柜，服务端保留两条约束。
- 11:58:19 进入 `FAILED_CLIENT_EXIT`。业务步骤 9 passed / 1 failed / 9 skipped / 0 undefined；Scenario 0/1 通过。
- 首个失败步骤为 Kiwibit UI 新鲜登录，邮箱输入失败。失败截图显示 Android 应用兼容性系统弹窗提示 APK 原生库 16 KB 对齐问题；这是观察到的 UI 阻塞，不能据此认定固件缺陷。
- 统一入口输出 `affinityVerification.verified=true`、`cleanup.verified=true`；独立回读终态绑定资源数为 0。释放后的空绑定不推翻运行中已验证的正确分配。

## 首单暴露的修复点

Plan 16058 / Job 18820 在执行 Scenario 前以 `FAILED_RESOURCE_NOT_AVAILABLE` 结束。原因是预检曾忽略 platform 大小写，但载荷仍传 `Android`，线上标签是 `android`；只读标签筛选分别命中 0 台、2 台。

修复为 platform 条件在预检和载荷生成前统一小写，型号与拓扑仍精确匹配，并补充回归测试。用户确认修正快照后才创建 Plan 16059，未删除亲和条件、未随机反复建单。首单亦已终态且无资源绑定。

真实执行代码为 `0244864d557c40b82464c80d5bc2d1e3e1cb47dd`。后续在最新 main 上 rebase，并根据代码审查做三项边界修复：带约束本地执行建单前拒绝（避免 Client 建连/分配循环等待）；Job 状态与绑定分开读取时复核正常终态释放；亲和组所有别名均要求 count=1。四条新增/替换测试先复现失败，修复后通过；最终代码未重新创建云测 Plan，真实验收证据对应上述前序提交，不声称最终提交已重新云测。

## 兼容边界

没有 CLI 约束且 Feature 未声明亲和性的用例，不新增同 Host/同机柜要求；Feature 已声明的约束不会因为 CLI 省略而消失。本次只验收 Skill 建单及资源生命周期，业务登录弹窗需单独处理。
