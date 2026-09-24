# 多型号 App 配置：批量修改、一个 PR 发布

适用于 revenue-sharing 后台的型号 App 功能配置，例如为多个型号接入某开关映射并设置默认值。配置编辑仍逐型号调用既有接口，全部读回正确后只提交一次 `type=6` 批量发布，生成一条任务、一个 Bitbucket PR。硬件配置发布不是本流程。

## 1. 先确认环境、部署版本与授权范围

- 入口：产品管理 → App功能配置；发布状态在生产管理 → 发布列表。批量操作可以使用下列已核准 API；不要猜不存在的前端按钮或批量保存接口。
- 已验证部署：2026-09-22 的 **test 后台** `https://revenus-sharing-backend-test.addx.live`，GitLab `CLOUD/revenue-sharing` !1239、!1240。批量功能受 `release.batch-app-function.enabled` 控制；运行环境需实际启用。生产版本/开关未在本轮部署验证，不能把 test 成功外推为 prod 可用。
- `test` 发布扇出到三个 staging 目标：`https://api-stage.addx.live`、`https://api-staging-us.addx.live`、`https://api-staging-eu.addx.live`。每次仍核对当前配置与 PR manifest 的完整目标集合，不根据“后台部署在 CN”推断只影响 CN。
- 共享配置仓库不等于某一区的运行库。若用户只授权 **prod-us**，不要自行走共享发布；既有生产发布可能影响 prod/pre/staging 多目标。先只读查清实际路由，范围不符时停在发布前，请用户决定范围或另选已支持的单区路径；不能靠改 manifest 删除目标来绕过路由校验。
- 明确本次型号、目标参数/映射、默认值、环境，以及是否包含保存、提交 PR、正式合并与下发。**仅本工作流**：用户已明确授权的这些步骤可连续执行，不因拆成多次接口调用重复确认；新增型号、环境、硬件发布、撤回或其他未授权动作才补问。历史任务授权和示例不构成未来授权。
- 硬件未发布的型号不能直接提交 App 批次。若确需前置硬件发布，另核授权并逐型号走现有正式流程；不要自动使用硬件 `type=5` 多型号批量，既有固件映射生成有 `findFirst` 仅首项限制。
- 认证沿用目标环境正式登录与权限。凭证只从环境变量或权限 `0600`、不入 Git 的本地 `.credential` 读取；不要打印密码、userToken、Cookie、Authorization 或完整认证响应。后续请求使用 `Authorization: <userToken>`（无 Bearer 前缀）。不要复制参考文档中的历史账号/响应作为凭证。

## 2. 已核准 API

以下路径相对已确认的后台 Base URL；成功还需检查业务 `code=0`，不能仅看 HTTP 200。用户身份由正式登录解析，不伪造 `userId`。

| 操作 | 方法和完整路径 | 请求/响应要点 |
|---|---|---|
| 查询 App 参数映射 | `POST /device/app/model/component/app/function/query` | body `modelNo` 字符串、`functionType:1`；读取 `data.paramResponseList` 的部件组/部件/参数树 |
| 查询映射候选 | `POST /device/app/model/component/param/query` | 同上；另返回 `data.appFunctionDOList`，用于核对真实功能 ID |
| 保存映射 | `POST /device/app/model/component/app/function/save` | body `{modelNo,paramList:[...]}`；条目字段见下节 |
| 查询套餐/默认值 | `POST /device/app/model/component/app/function/tier/query` | body `modelNo`、`functionType:1`；读取完整型号设置与 `tierParamResponseList` |
| 保存套餐/默认值 | `POST /device/app/model/component/app/function/tier/save` | 完整 read-modify-save body，显式 `saveAndRelease:false` |
| 一次提交批次 | `POST /device/release/submit/task` | body `type:6,idList:[型号的数字ID,...]`；返回 `data.taskId/prUrl/releaseStatus` |
| 查当前批次 | `GET /device/release/release/tasks/6?taskId=<taskId>` | `data.list`，核对准确任务；默认不带 taskId 的列表仅展示待处理任务 |
| 只读查队列 | `GET /device/release/release/tasks/-1` | 可见的各类待处理任务，用于确认已有 PR 阻塞，不修改它们 |
| 为同一排队任务建 PR | `POST /device/release/release/task/<taskId>/create-pr` | 无请求体；返回 `data.prUrl`；有全局可见 pending PR guard |
| 取消同一任务 | `POST /device/release/release/task/<taskId>/delete` | 无请求体；状态与真实 PR DECLINED 前置条件见第5节 |
| 全量 PR 状态同步 | `POST /device/release/sync-pr-status` | **无 taskId、无单任务范围**；可能处理其他历史任务，不是默认补偿按钮 |

`POST /device/release/merge`、`POST /device/release/decline` 是 Bitbucket 通知入口。不要自行构造请求模拟 merge/decline，也不要把 `/tier/release` 单型号发布混入本批次。

## 3. 逐型号完整读取、修改、保存、读回

1. 从后台列表/正式查询解析型号名和数字 ID；名称精确匹配、ID去重，确认没有同型号待发布任务。一次批次最多100个不同型号。不要照搬历史夹具 ID 或硬编码某型号族。
2. 保存 before-image：完整映射树、套餐/默认值响应；有目标库查询权限时，优先用 NineData 采集目标运行库相关行、factory 本地发布行，记录环境/数据库、型号和查询时间。读回工具省略 NULL 列时按列标识解析，不能把后续列左移。已有权限但查询失败或关键标识缺失时先排查，不能把坏结果当空配置。**无目标库查询权限时**不要求申请数据库权限，也不因此阻断整个流程；按第4节改用该目标环境的 iot-service 执行日志核验，提前确认日志可访问并能关联本次操作。没有数据库基线就不宣称已完成前后逐字段比对。
3. 分清两种字段：`appFunctionId/appFunctionValue` 表示映射；`paramDefaultValue` 表示参数默认值。`defaultSwitch` 表示是否启用套餐设置，**不是“充电自动开机”开关本身**。映射已接入不代表默认已开，反之也不能凭默认值臆造映射。
4. 保存映射时，从完整 `paramResponseList → componentList → paramDOList` 展开 `paramList`，保留每一项，仅修改授权目标。每项带：
   - `modelComponentGroupId`（读取树的 `componentGroupId`）、`modelComponentId`（`componentId`）、`paramId`；
   - `appFunctionId`、`appFunctionValue`、`syncMaster`、`paramMasterId`。
   使用查询得到的真实功能 ID；空 `appFunctionValue` 可以是有效开关映射，不为凑 YAML 内容随意填值。
5. 映射保存后重新查询，再构造第三步完整保存体。保留 `modelNo,tenantList,aiEvent,zendesk,installBoot,magicpix,freeTierId,promotionPeriod`，以及已存在的 `iconUrl/smallIconUrl`；设置 `saveAndRelease:false`。其中查询 `tenantIdList/aiEventList` 转成逗号分隔字符串，`zendeskUrl` 对应请求 `zendesk`。不要将未读取字段默认清空。
6. `tierList` 保留所有参数，每项携带 `modelComponentGroupId,modelComponentId,paramId,defaultSwitch,paramDefaultValue,settingList`。`tierDefaultResponse` 转为 `settingList`；若子项为 JSON 字符串，解析成含 `tierLevel,supportValue,defaultValue` 的对象。仅把授权目标的默认值改为其参数类型允许的值，例如 BOOL 开启为字符串 `"true"`；不是统一把所有类型都写成 true。
7. 当前 App 参数版本中缺目标 `paramId` 时，`tier/save` 应明确拒绝；确认未变化。不要跳过失败后宣称开启，不自动克隆型号、补硬件参数或改库。需要补参数时作为额外前置工作确认范围。
8. 保存后再次查映射与套餐，核对每个目标确实为期望值，其他参数/套餐/tenant/AI 等保持原样。全部型号通过后才进入一次批量提交。

## 4. 一个任务、一个 PR，合并后核验

- 仅一次调用 `/submit/task`：`type=6`，`idList` 为本次准确型号 ID 集合；不把型号名作为 ID，不逐型号重复提交。
- 保存返回 taskId。若超时或响应不完整，先查询现有任务和远端 PR，确认是否已创建；不要盲重试产生第二条任务。列表有可见性/起始ID过滤，查不到不等于不存在，必要时 NineData 只读核对 `factory.device_release`。
- 若返回 `releaseStatus=0`、`prUrl` 为空，通常已进入现有队列，不等于提交失败。检查当前任务和可见历史 PR，等待正常队列释放；有权限且本任务可建 PR 时，调用 **同 taskId** 的 `/create-pr`。被其他任务阻塞时报告任务，不撤回、删除或篡改别人记录来解锁，不重新 submit。
- 批量记录复用 `device_release`：`type=6`、`keyId` 为0或null、`modelNo` 为逗号分隔数字ID。旧单型号 type6 的 keyId 非零，不能把其型号名称按批次 ID 解析。状态0待创建、1待发布/未全部成功、2全部发布成功、-1已取消；状态1本身不能证明发布成功或具备可靠重试能力。
- 打开**该任务**生成的 Bitbucket PR，逐项审核：
  1. PR来源分支与任务一致、目标 master（共享配置仓库的目标，不代表发布到生产）；只有期望型号。
  2. `app_function.yml` 两个或多个目标型号均展示本次参数和默认值，非目标语义不变。映射ID有效、默认非空但 App值为空时，也必须可见默认值（!1240 修正）；发现空参数列表掩盖改动时停在合并前。
  3. `release_manifests/app-function-<taskId>.json` 的 taskId、型号ID/名称、完整 targets、六步骤与 payload 正确。五类远端步骤为 event、tenant、zendesk、modelConfig、adminConfig，最后为 factory 的 appModelReleaseConfig。按当前定义核对 skip 原因；没有 BASE_CONFIG choice 时 modelConfig 可合法 skip，不能把所有空表都当成功或失败。
  4. 充电示例应在适用 adminConfig 中出现 `chargeAutoPowerOnSwitch:1`；不要只审核 YAML 而忽略实际下发 manifest，也不能只看 manifest 就忽略 YAML 审核缺口。
- 在已授权范围内走 Bitbucket 正式审批/合并。**APPROVED 不等于 MERGED**；确认真实 MERGED、PR ID、源commit，再等待正式回调。不要伪造 webhook，也不直接调用 iot-service 写入绕开审核。
- 回调按已合并 PR 的源commit读取 manifest 执行；不以最新未审核草稿替代。发布后逐目标环境选择核验路径：NineData 对该环境目标数据库有查询权限时，直接读库；仅无查询权限时，改查该环境 iot-service 执行日志。不同环境可分别采用不同路径。
- **有目标环境库查询权限**：用 NineData 只读核对该环境 manifest 所列目标的运行配置和 factory `app_model_release_config`，并与 before-image 比对非目标型号/字段。确认整任务状态2、目标默认值读回正确、合法skip有依据后，数据库读回就是最终发布证据，报告数据库核验通过；**无需再查 revenue-sharing 或 iot-service 执行日志**。
- **无目标环境库查询权限**：才查询该环境 **iot-service 执行日志**，优先用 `troubleshooting` skill 定位对应环境日志，或使用经验证的该环境 OpenSearch 入口；不要求申请数据库权限，也不能只用 revenue-sharing 发请求日志代替。结合型号、目标环境、时间窗口和实际存在的关联ID，核对 `taskId/prId/commit/modelId/modelNo/target/step/result/reason` 等实际可用字段，确认对应配置处理结果与失败原因，分别报告 SUCCESS/FAILED/SKIPPED 与受控原因；不要保存完整HTTP响应或认证信息。日志有字段值时与已审核 manifest 比对，未记录字段不能宣称逐字段落库一致。结合真实 PR MERGED 与任务状态给出结论：**“日志核验通过，未做数据库读回（无权限）”**，分环境注明核验方式。iot-service 日志也无权限、无法关联本次操作或结果不明确时，标记核验未完成，不能推定成功。不虚构索引名、字段或日志格式。
- 无论数据库还是日志核验，都不等于已测试真实设备充电行为。
- 若回调没到或失败，先查当前 PR/task，再按上述权限路径核验目标环境。`/sync-pr-status` 是全量操作，只有查明受影响任务集合并获得相应授权后才考虑，不把它当单任务重试。此功能没有新增可靠恢复/断点状态机；部分失败先定位再决定处理，不反复触发写入。

## 5. 取消与边界

对准确的本次任务先读状态与真实 PR 状态：无PR且状态0，可在取消授权内调用同taskId `/delete`；已有PR则先通过 Bitbucket 正式 **Decline**，等待真实拒绝回调，再读任务。若已-1无需重复取消；只有确认 PR 已 DECLINED、任务仍需清理且授权覆盖时才调用同taskId `/delete`。已发布状态2不能取消，需另行评估补偿PR；OPEN/MERGED状态不能伪装DECLINED。

本流程不需要 DDL、新表或直接SQL写配置。NineData在此用于查询与前后比对，不能绕过编辑/PR发布流程。拒绝/发布均限定本次任务；不要清理其他历史队列。不要把 App type6 流程推广为硬件多型号 type5 流程。

## 6. 轻量离线场景检查

仅检查执行决策，不调用真实写接口：

| 输入场景 | 应采取的动作 | 不应采取的动作 |
|---|---|---|
| 已授权test多型号映射+默认开+一个PR合并，参数齐全 | 完整读改存，`saveAndRelease:false`；读回后一次type6；审核YAML/manifest/三区；真实合并后读回 | 每型号单独发布、沿用历史夹具ID、假回调 |
| 同批已返回taskId、状态0、prUrl空；有其他历史PR | 核对同task，保留排队；条件满足后同task create-pr | 再次submit、删除别人任务、默认全量sync |
| 用户仅授权prod-us，当前共享发布会影响更多目标 | 只读核范围；停在写入/发布前，说明实际范围并请用户决定 | 静默扩大范围、套用test成功证明prod可用、裁剪manifest绕过校验 |
| 目标库无查询权限，iot-service日志可访问 | 直接核对该环境iot-service处理结果并关联PR/任务/批次日志；注明日志核验、未做库读回 | 要求申请库权限、仅凭发送日志判成功、把缺失字段说成已落库一致 |

## 实现核对来源

- `CLOUD/revenue-sharing` !1239、!1240：`DeviceModelAppController`、`DeviceReleaseController`、`DeviceModelAppFunctionSaveRequest`、`ModelAppFunctionTierRequest`、`BatchAppFunctionReleaseService`、`DeviceModelService`。
- 同仓 `scripts/batch-app-function/README.md` 与 `e2e.py` 提供测试流程参考；其固定夹具只用于该次测试，不能照搬到业务操作。
- 查询库采用 `ninedata` skill；后台服务部署采用 `revenue-sharing-deploy` skill。修改配置PR与部署后台代码MR是两条不同流程。
