# EU-prod VM stack 平台采集校验

状态：本能力仅作已提交配置的静态 Review/Scan；不是Build新路由、平台权限豁免或生产接管批准。
Root / Work Item：[engineering/skills#304](https://gitlab.addx.ai/engineering/skills/-/issues/304)。
设计基线：[argocd-apps !2539](https://gitlab.addx.ai/DEV/argocd-apps/-/merge_requests/2539)。

## 何时选择、如何固定来源

仅支持 `DEV/observability-platform` 中EU-prod准备来源，完整15对象、精确4个VMServiceScrape。不因kind、名字、标签、目录名、repo-context或一份caller allowlist自动启用；其它集群/业务/GKE仍按原规则。

在真实、干净的该来源Git checkout中执行，`SOURCE_SHA`必须替换为当前实际40位HEAD，不填写分支名或凭空生成值：

```bash
bash "$skill_root/validators/validate.sh" \
  --vm-scrape-profile eu-prod-vm-stack-v1 \
  --vm-scrape-cluster aws-740315635167-eu-prod \
  --vm-scrape-source-revision "$SOURCE_SHA" \
  "$SOURCE_CHECKOUT/clusters/aws-740315635167-eu-prod/vm-stack-observability"
```

默认repo-context=auto；不可与app/k8s/argocd-apps/crossplane-infra上下文或generic `--require-target-match`混用。wrapper仍执行全部其它validator，只把这三个专用参数交给scrape checker，不跳过任一通用检查。

checker校验Git root、origin host/repo、HEAD、目录/文件及已提交字节；目录只能有manifests.yaml，禁止symlink、额外文件、复制出的无Git来源或未提交内容。CI环境必须同时匹配CI_PROJECT_PATH=DEV/observability-platform、CI_PROJECT_ID=1433、CI_COMMIT_SHA=该HEAD，实例为gitlab.addx.ai；缺/错即失败。结构检查只把固定上下文视作受信调用者环境的证据，不声称origin字符串或CI变量能认证远端权限。正式CI接入应由可信入口注入这些字段，不从应用标签/manifest正文取得context。

JSON policy随技能源仓审核，固定完整15对象的结构摘要，包括其它11个依赖声明及四scrape完整metadata/spec；额外/遗漏/重复对象、未知字段或任意selector/namespace/endpoint/认证/TLS/labels差异全部拒绝。来源修订SHA不是放宽内容的凭据。policy不接受外部文件路径、动态allowlist或自称“已批准”的文本。

## 平台规则与真正安全阻断

原业务generic保持：part-of、同namespace发现、显式path/interval和禁止认证字段等不变；精确GKE DCGM分支也不变。EU-prod平台静态合同有kube-system/default/victoria-metrics中的固定Service发现、标准SA token与Kubernetes CA路径。它只解决这些已审核平台声明被通用业务规则误分类的问题。

原完整来源中CoreDNS带bearerTokenFile而scheme缺省HTTP；profile因此返回**EUVM-AUTH-001 / exit1**，不把精确匹配等价当安全批准。该错误仅描述静态配置，不断言现场已经发出token或发生泄露。实际有效scheme/relabel、token必要性/发送、target、SA权限/受众和链路保护仍待独立核验。

唯一可返回静态PASS的候选差异是移除CoreDNS不必要的bearerTokenFile，其它15对象内容保持；policy含该精确候选结构摘要。本validator不会改文件，此候选不是当前来源事实。采用它仍须后续独立来源MR和原chart/编译输入更新、65分区复编译/显式差异审核，不能在消费端私改生成物或先切Application。任意其它认证/TLS修复方案必须另行审核policy更新，不允许凭标签扩权。

path=/metrics与interval=30s的静态适用性绑定固定版本声明（原VMAgent的scrapeInterval为30s），不以此冒充已读生产生成配置；实际target/TLS/RBAC/defaults和独立安全适用性仍是接管门禁。profile PASS的输出明确static only，不是DEP整体关闭、运行时合规或部署授权。

## 返回值与保留证据

- exit0：精确安全候选的静态配置通过；其它validator仍可使aggregate失败。
- exit1：EUVM-AUTH-001安全阻断、EUVM-CONFIG-001配置漂移或EUVM-INVENTORY-001库存错误。
- exit2：EUVM-SOURCE-001来源上下文、EUVM-CONTEXT-001参数组合、EUVM-POLICY-001安装合同错误。选择profile后不fallback到generic制造PASS。

[原失败回执](../../../../../docs/requirements/304/original-generic-failure.json)固定旧checker哈希、源SHA、exit1和19条失败；默认generic对同源依然真实失败19条。新profile减少误分类但保留真实认证阻断。VM-SOURCE-DEP-001在正式分发、可信消费CI、源语义修复与现场适用性均验收前继续OPEN_BLOCKS_ADOPTION；本MR不改Application、Project或中央门禁模板。

## 回归与正式入口覆盖

`tests/test_vm_platform_profile.py`的真实Git/publicCLI正负例经现有`test_offline_e2e.py`导入，进入原CI回归任务；不新增一条绕开门禁的pipeline。测试覆盖来源绑定、CI字段、结构/认证漂移、原generic19失败、GKE/业务不变，以及真实validate.sh执行全部其它checker且不吞错误。
