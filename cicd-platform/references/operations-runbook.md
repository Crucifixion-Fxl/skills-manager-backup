# CICD 上线单人工运维手册

## 使用边界与版本

平台进入维护期：用已验证的页面、只读接口和必要的人工运维解决问题，不默认要求修改/发布 CICD 服务。用户说“恢复让回滚可用”只授权恢复资格；说“先不回滚”时不得触发部署。“看是否有影响”只授权诊断；“按当前镜像 rollout 一次”在上下文明确时授权一次滚动重启，不授权更新镜像或其他项目。

这份手册记录 2026-10-10 至 12 日上线单1046的实操，以及后端已核验版本 `9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1`。案例编号/镜像/集群仅是证据，不是未来默认目标。固定方法可直接复用；任务、身份、阶段占用、正在运行的部署、实际镜像和期望副本必须实时读取。若现象或部署版本与这里的合同不符，只研究差异，不重复全站扫描。

按动作读取：

1. [确认回滚目标](#确认回滚目标)
2. [恢复已关闭上线单](#恢复已关闭上线单)
3. [失败是否影响服务](#失败是否影响服务)
4. [按当前镜像滚动重启](#按当前镜像滚动重启)
5. [只读监控](#只读监控)

## 确认回滚目标

UI 上项目标题的 commit 是本单构建版本，不等于某环境当前实际运行版本；“回滚”也不是按 Git 日期查找。记录 taskId、projectId、buildId、env、当前部署和目标部署的 ID、commit、完整 tag、原单号及 deployAt。

后端 [db/deploy.go](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/db/deploy.go) 的 `GetTaskProjectRollbackDeployByEnv`：同一 project/env，排除本 task；每个其他 task 先取成功部署最大ID，再排除 `is_rollback=1`，按ID降序取首条。不能简化成“最新成功部署”或“上一 Git commit”。只读等价查询，必须使用绑定参数：

```sql
SELECT d.id, d.task_id, d.project_id, d.build_id, d.env,
       d.git_commit, d.container_tag, d.deploy_at,
       d.jenkins_job_name, d.jenkins_build_number
FROM deploys d
JOIN (SELECT MAX(id) id FROM deploys
      WHERE project_id = ? AND env = ? AND deploy_status = 1
        AND task_id <> ? GROUP BY task_id) last_deploys ON d.id = last_deploys.id
WHERE d.is_rollback = 0 ORDER BY d.id DESC LIMIT 1;
```

优先平台已显示的回滚记录/只读数据；UI不提供目标时使用已有授权的后端只读路径，不导出浏览器 token。读取接口见 [API参考](api-reference.md)。时间同时区分镜像构建时间、原部署时间和本次回滚时间；tag里的日期不代表原部署日期。用户问“几月几号版本”时说明采用哪一种。

案例：1046的美国 PROD iot-service 目标为1040的 `9be023_20260924_163442105`；consumer 是独立镜像 `9be023_20260924_163600224`，不能复用 service tag。

## 恢复已关闭上线单

### 判别与冲突处理

- 挂起：`suspended=true`，使用管理员“恢复”解除挂起；它不把完成态6恢复成灰度5。
- 关闭：`status=6`，恢复回滚资格通常需要工作流回退到5；build workflow 从700恢复到500，PROD测试状态重置为未执行0。
- 作废：`enable=false`；不能把解除挂起或关闭态恢复当成启用作废单。
- 同一项目的另一活跃单可能占用相同阶段。目标恢复包含该单所有项目，不能只核对准备回滚的一个项目。占用阶段按 [model/release_stage.go](https://gitlab.addx.ai/DEV/cicd-server/-/tree/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/model) 的实际映射核对，不按单号新旧判断。

先检查目标是否启用、未挂起、无在飞部署以及项目阶段冲突。发现冲突时给用户具体单号、项目、阶段和影响。作废冲突单必须有对该单的授权；作废只停止后续上线操作，保留历史部署，不撤回已部署版本。正常用UI作废并刷新核验 `[废]`，再恢复目标。若冲突单仍有部署执行，不作废/恢复来掩盖它，也不自动取消。

### 已知关闭态缺陷与受控恢复

已核验版本中 [TaskStepRollback](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/task_admin.go) 在事务锁内先调用 [validateMutableTask](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/handler/stage_conflict.go)，会对关闭态返回“上线单已关闭”。管理员UI回退因此可能不可用；直接重发同一POST不会修复它。

关闭态恢复涉及生产上线资格，用户授权是必要条件，执行还必须有工具侧的资源范围、审批和审计约束。普通缺权限、挂起、作废、非法workflow和阶段冲突不是关闭态缺陷。不要创建/运行临时SQL或Go helper直接写库，不重发POST绕过UI，不禁用权限检查，不伪造部署成功。

处理方式：

1. 优先已可用的管理员工作流回退入口，遵守角色、阶段冲突、在飞部署和原因门禁。
2. 若页面因已知关闭态缺陷拒绝，只读收集指定task、全部build/project IDs、现状态、冲突单、部署记录及期望恢复目标；提供与用户授权对应的运维工单。
3. 若组织已有受控恢复工具，只有其能力和权限已被核验、强制绑定工单和目标task/project白名单、一次性批准、执行前审计及失败回滚时，才可按该工具既有合同执行。该手册**没有核验到这样的可调用恢复入口**，不得猜测命令/API或宣称可以自动完成关闭态恢复。
4. 缺少受控入口时停止状态写入，将只读快照和以下验收要求交给有权限的运维人员处理；其他只读诊断仍可继续。不得为了绕过此限制发布平台改动或另建生产恢复工具。

交接验收要求（参考已核验 [db/project.go](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/db/project.go) 和 [ApplyAdminRollback](https://gitlab.addx.ai/DEV/cicd-server/-/blob/9eda25f1e5af8861cd2d8f1293d8dc899d45b8b1/db/task.go)，是验收合同，非AI直接数据库写入指令）：

- 指定关闭单的资格恢复应覆盖全部关联项目；6→5对应build workflow700→500，并重置PROD测试结果为未执行。不能修改镜像/commit、部署成功状态、Apollo发布历史或其他单；作废/挂起不自动解除。
- 受控工具应确定性验证操作者、目标单、授权工单、在飞部署与项目阶段占用；执行时需要一致性锁和最新状态，不能只依赖执行前快照。
- 原子状态恢复与审计必须一同成功；审计带真实身份、原因、前后状态和工单引用，失败回滚。不得把恢复资格等同已回滚。
- 完成后回读task/build/audit，核验原部署记录逐行未变，再刷新UI确认回滚按钮enabled；报告实际处理结果。用户要求“暂不回滚”时在这里结束。

案例1046：先经用户授权作废1048，再将1046 `6→5`、build1348/1349 `700→500`，审计98；41条部署记录保留。恢复和用户稍后手动点击回滚是两个独立动作。

## 失败是否影响服务

### 三层证据

| 层 | 读取 | 能证明什么 |
|---|---|---|
| CICD | 对应项目/地区的当前deployment ID和状态 | 平台记录的本次结果 |
| Jenkins | deployment记录绑定的job/build `/api/json`、`/consoleText` | 执行结果、是否自动撤回、失败触发条件 |
| Kubernetes与业务指标 | 实际Deployment/RS/Pod镜像、ready/available、消费吞吐/lag | 当前运行状态和可能业务影响 |

Jenkins地址以部署记录绑定job/build为准，不猜最新build。已验证控制器公开入口 `https://jenkins-next.addx.live`；内网别名 `jenkins-next-internal.addx.live` 在办公主机可能超时。使用 [Jenkins技能](../../jenkins/SKILL.md) 的现有Basic Auth，token在内存构造header；API只做GET。日志可能包含设备/用户信息，只输出必要错误分类、原因、时间和rollout控制日志，不倾倒原始业务payload。

平台“失败”不证明原版本还在，也不证明所有Pod已恢复。Jenkins可能已改镜像、部分目标实例已开始消费，再执行 `rollout undo`。分别报告 desired镜像、每个实际tag的非终止/就绪实例、终止实例、是否仍混跑。故障触发条件与根因分开：单Pod重启且未就绪触发回退，不足以断言OOM或代码缺陷；需要lastState/events/previous日志支撑。

跨项目报告要列出 service 和 consumer 各自版本。用户说“全部恢复了吧”时核对其语境范围，不把consumer自动恢复推断成service也恢复。

### 消费业务核验

US prod指标入口 `https://victoria-metrics-us-prod.addx.live/select/0/prometheus`（VictoriaMetrics）；其他环境使用 [Prometheus技能](../../../observability/prometheus/SKILL.md) 的已核验入口。只读查询：

```promql
sum(kafka_consumer_fetch_manager_records_consumed_rate{job="prod-us-iot-consumer"})
max by(topic)(kafka_consumer_fetch_manager_records_lag{job="prod-us-iot-consumer"})
sum by(job)(up{job="prod-us-iot-consumer"})
```

比较操作前、操作中和当前lag走势。ready和持续消费只能证明未整体停服，不证明无消息延迟或无数据损失；单分区最大lag不是整个队列总积压。若 `kafka_consumergroup_lag` 只采集日志Kafka，不拿日志积压代替业务队列，也不把空结果当成0。用客户端consumer指标及实际业务topic。结论说“未见整体中断/发现某topic积压升高/缺少某证据”，不要直接说“无影响”。

案例：consumer Jenkins `cicd-rollback-us/18` 在一个 `9be023` Pod重启且未就绪后失败并自动undo至 `a487cf_20260929_143737181`；service的 `cicd-rollback-us/17` 成功留在 `9be023`。video-generate lag有波动，不能仅凭61个available判定无影响。

## 按当前镜像滚动重启

使用 [Kubernetes技能](../../../infrastructure/k8s-ops/SKILL.md)；这是直接K8s动作，不是CICD“部署”按钮。沿用上下文明确的region/context/namespace/Deployment，写前读取完整当前image并记录generation、template、RS、strategy、desired/available及HPA。只输出白名单字段，不打印整份spec/env。已知US例子是 `us-eks-prod / prod-us / iot-service`，不得作为其他请求默认值。

检查GitOps归属/同步策略：tracking annotation中的Application可能由另一控制集群管理，业务集群 `Application NotFound` 不表示不受GitOps管理；按已有本地ArgoCD context/argocd-apps入口查询。重启不改image，仍要确认restart annotation是否会被selfHeal撤销。若存在覆盖风险，报告并跟踪，不能自动关闭selfHeal或修改Git来源。

用户已明确授权一次重启时展示目标和影响后执行一次：

```bash
kubectl --context "$CTX" -n "$NS" rollout restart deployment/"$APP"
kubectl --context "$CTX" -n "$NS" rollout status deployment/"$APP" --timeout=45s
```

45秒本地等待超时不等于Deployment失败。记录新 `kubectl.kubernetes.io/restartedAt`、generation、新RS UID/名称；验证镜像未改变。后续只读poll，不再执行restart。若原有实例未就绪，报告基线和原因；不将原有故障算作重启造成，也不暗示 `maxUnavailable=0` 能保证完全无影响。

完成条件：observedGeneration追上当前generation，本次目标RS updated/ready/available覆盖**当前期望副本数**、总副本已收敛、旧RS无实例、没有残留旧RS终止Pod，所有实际镜像符合记录。HPA/KEDA会改变期望副本，不能把启动时250写死为终态；核对HPA来源并说明如“自动扩缩容调整为244，244/244就绪”，不能为了满足监控自行scale。

## 只读监控

使用 [rollout_snapshot.py](../scripts/rollout_snapshot.py) 做一次脱敏快照；该工具只GET、验证Deployment owner链，并复读Deployment/相关RS/Pod的resourceVersion；读取期间状态变化返回retry_snapshot。它不提供跨资源原子快照，完成结果是当次稳定观测，不执行任何修复：

```bash
python3 scripts/rollout_snapshot.py --context us-eks-prod --namespace prod-us \
  --deployment iot-service --expected-image 'harbor-us-internal.addx.live/cicd/prod-us/iot-service:9be023_20260924_163442105' \
  --restart-at '<本次回读的restartedAt>'
```

连续监控按平台heartbeat工具，每1–2分钟只读检查；prompt写清目标、新RS/restart-at/镜像、禁止写操作和安静规则。复用本任务原监控而非创建重复项；更新前读取旧配置并保留不相关字段。只在完成、首次显著异常、异常恶化、需要人处理时通知，同原因重复超时不刷屏。正常推进和状态未变保持安静；异常解除可通知一次。终态验证后暂停该监控，不归档用户聊天。

`ProgressDeadlineExceeded` 不会自动停止Deployment控制器，不是永久失败；保留监控，继续检查能否恢复。US iot-service在实操中每批约38个新实例可能等待多个 `target-health.elbv2.k8s.aws/*` readiness gates；容器全ready、无重启仍可因部分gate条件缺失而Pod未ready。诊断顺序：容器state/lastState → startup/readiness事件 → readinessGate缺失/false → TargetGroupBinding/负载均衡controller与云端target health。这证明阻塞位置，不能只凭缺失gate宣称ALB故障或直接删除gate。原有available达到desired时保留旧实例等待，不自动取消、降副本或再次重启。

案例：2026-10-11 23:56的service重启，RS `5c694445f9` 替换 `6fb5dd75f9`，多批等待负载均衡状态且一度进度超时；2026-10-12 00:57已确认244/244、新实例无重启、旧实例全部清理。同tag同时存在旧/新RS，所以仅看镜像tag无法证明滚动重启完成。
