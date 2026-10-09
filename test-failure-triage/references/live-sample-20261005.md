# 真实取证样本 2026-10-05

平台 Job18119、Plan15549；ReportPortal Launch52175：
https://reportportal.builder.addx.live/ui/#builder_prod_cn/launches/all/52175
这是单Job取证验收，不是七日完整归因。运行类型UNKNOWN，实际mode/argv/版本缺失；
失败阶段EXECUTION，Behave已匹配并进入click操作。AI tags/manual Job不改变这项判断。

| Scenario | 实际证据 | 当前判断 | 缺项 / 优化 |
| --- | --- | --- | --- |
| 612387 App Language 显示当前手动选择的语言 | RP item521609；3408条日志完整分页；log112481426进入English点击，112481436无可用WebView context，112481762抛ElementNotFoundError；录像185秒帧显示日文WebView更新/刷新页面 | 前置页面与目标不一致；context可达性是次级线索，不能确认框架不支持 | 缺XML、context/debug/driver信息、请求链与部署SHA；先修语言设置导航与成功页面断言，验证WebView更新页的产品/服务原因 |
| 612388 重置为跟随系统后切换为中文 | RP item521612；3264条日志完整分页；log112484901 L2缓存命中，112484909 bbox fallback点击；112485202 AI将Labs开关判为Confirm，112485287 deadline异常；录像185秒帧显示Labs | 错页面和不适用缓存/视觉目标是可观察因果链；deadline是末端错误 | 核验点击前后页面与L2缓存适用范围，补错误页/目标语义校验；未确认独立Plugin能力缺口，不自动派发开发 |

6个附件（两段录像、两份签名manifest、两份Scenario诊断JSON）成功下载、哈希记录。
录像185秒帧由Chrome解码后实际查看；第一条末帧为黑屏，不能当作失败页证据。
没有在RP列表查到日志宣称保存的失败XML/独立PNG；上传调用完成≠可查询。
缺少XML时无法确认accessibility缺失，不能因找不到English就生成新的通用tap能力。

凭据实测：平台PAT能读Job/Plan，GraphQL诊断拒绝PAT；REST诊断限定计划owner。
使用独立GET代理补精确UUID的RP内容，上游key不进入Agent容器；写路由、跨项目、
重复/越界分页、未认证和到期由确定性门禁拒绝。在线群回读与定时启用单独记部署receipt。
