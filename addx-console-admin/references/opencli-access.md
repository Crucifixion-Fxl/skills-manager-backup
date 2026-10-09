# console.addx.live: native REST and OpenCLI maintenance

优先采用可维护的 native REST 契约；业务模型、角色、状态、读写副作用、回读入口先读取 [platform-api-catalog.md](platform-api-catalog.md) 与 [source-api-catalog.json](source-api-catalog.json)。凭据来源、身份探测与能力边界采用 [saas-access.md](saas-access.md)，不得另造登录方案。候选 base URL 为 `https://revenus-sharing-backend.addx.live`，只有部署实例确认后才能将源码相对路径用于实际请求；CICD JSON 清单路径已经包含 /api，不能重复拼接 /api。Console 的代理 /api 前缀与后端路径须按现有 auth profile 和实例来源匹配。

route catalog、可执行 command registry、离线契约测试和真实验收报告分别维护。源码目录和本文不是线上通过证据；可执行适配器见 scripts/opencli/addx-console，实际部署与逐动作通过以任务独立报告为准。

可维护的 REST 可以封装为 OpenCLI LOCAL 命令，保留原生认证。只有 REST 不完整或缺少操作时，才进入浏览器 OpenCLI sitemap/adapter 维护。复用已安装 upstream [opencli-adapter-author](../../../agent-harness/web-access/runtime/node_modules/@jackwener/opencli/skills/opencli-adapter-author/SKILL.md)，不复制其教程；依赖安装后由该目录提供实际 Skill，未安装先按 web-access runtime 的锁定依赖准备。浏览器站点入口维护使用同 upstream 的 `opencli-browser-sitemap` / `opencli-sitemap-author`，遵守 web-access 的隔离浏览器与用户认证要求。

维护记录最少保存：owner 与源码/前端 SHA、策略和为何 native REST 不足、角色和资源权限证据、页面路由/semantic selector、实际请求模型及响应解码、目标前后状态、写入副作用、同资源回读、typed refusal、adapter fixture 和浏览器 verify 回执。证据必须关联同一候选版本和身份；失配、页面禁用、角色不符、部署未知或缺回读时 fail closed。无源码或合法 UI 证据的写入不得编造 command。

写能力候选必须经本地模拟契约覆盖拒绝分支（角色/资源/状态/挂起/目标变化）、单次写入与回读；本仓库 `skills/delivery/cicd-platform/tests/test_platform_api_catalog.py` 包含静态路由提取和权限/状态安全模拟，明确不是生产服务器运行证明。后续 adapter 还须实际接口/schema 与 UI 验收，不得引用模拟证明线上支持。开源组件的官方 API/CLI 使用对应 owner Skill，不纳入平台站点 adapter 重写。

恢复条件：服务 owner 提供实际部署 SHA/前端 SHA 与角色 grant，完成读回模型与接口契约对照；用户授权目标业务动作后，按该 owner 的状态和发布门禁运行验收。

## 可执行原生读取适配器

适配器源码位于 `../scripts/opencli/addx-console/`，使用 OpenCLI LOCAL 模式、平台原生 Authorization，不依赖浏览器扩展。安装到 OpenCLI 用户命令目录前先检查同名适配器是否存在；禁止覆盖已有用户实现。已有命令见 scripts/opencli/addx-console，覆盖身份、部分电池/计划/入库、云服务商品/账单、客户分成、硬件型号及参数、部件/部件组/类别、文件元数据和 App 型号列表；这是局部覆盖，不代表全站接口完备。凭据由私有通道注入 CONSOLE_TOKEN 环境变量，可设置 CONSOLE_EXPECTED_EMAIL 校验目标身份；不写入参数、配置或报告。分页命令支持 --page 和 --limit（1–20）。

电池包命令可带 --model、--cell-model、--supplier-id；参数校验在认证请求前执行。筛选验收须核对网页实际请求中的字段及完整结果，不能仅凭 CLI 返回成功就认为筛选生效；每个筛选维度分别记录覆盖。

生产计划读取使用 battery-plans，分页有界，服务端可能按当前身份的厂商限制范围；不能把客户端指定厂商当授权依据。空页与身份拒绝分别报告，不能把空页变成平台不可用。脚本目录还包含 pack-factories、pack-factory-cells；关联集合按稳定键比较。

云账单使用 cloud-bills --group app 或 --group non-app；group 必填，避免隐式扩大范围。cloud-products 读取商品分页，cloud-product --id 读取列表已知商品详情。账户身份在每个命令实际业务读取前核验；仅保存脱敏计数/摘要作为账单验收报告，不将原始业务明细复制到通用 Skill。

cloud-bill-detail --tenant <应用> --month YYYY-MM 查询单应用单月，按日历计算月末。orderedAt 是原始下单时间，createdAt 在页面标为本月生效时间；两者不能互换，也不能要求原始下单时间落在查询月份内。

远端批量验收执行器位于 scripts/opencli/acceptance-remote.py（相对 Skill 根目录）。输入仅走私有 stdin；安装、命名空间冲突、执行超时均须进入清理 finally。安装返回码必须检查，不能继续执行不存在的 CLI。保留原有同名目录，只删除创建成功的任务目录与自身解析器链接。失败路径测试是执行器验证，不属于真实 SaaS 验收。

hardware-models --model 使用服务端 LIKE 匹配，不能称为精确型号查询；筛选请求和无筛选翻页分别验收。传空字符串作为显式筛选应拒绝，省略参数才使用页面默认范围。

hardware-model --model 为精确已有型号的基本信息查询，区别于 hardware-models --model 的 LIKE 列表筛选。详情页面还加载部件参数/部件组等接口，每个接口独立记录覆盖；基本信息命令成功不等于完整硬件参数覆盖。

设备类别、部件、部件组命令分别为 device-categories、components、component-groups，使用有界 --page/--limit。firmware-build-platforms 和 file-types 使用展示 limit，不代表后端分页；files --type 从实际类型列表取 code，仅输出元数据，排除下载签名 URL。app-models 列表与硬件型号列表不是同一状态/资源范围，分别核验。production-processes 的网页入口及实际消费须另行证明，代码存在不代表验收。

写命令 battery-cell-write、battery-pack-write、pack-factory-write 默认 dry-run。执行器的显式参数、计划 hash、删除范围 hash 和私有写开关是防误操作约束，不能代替实际部署、权限和用户业务授权。Pack 厂输入必须完整集合；空子型号集合可能被服务跳过，不能用它清空关联。真实提交后须单独读回；无原子服务端版本条件时保留竞态限制。只读任务不运行 submit。

只读验收执行器在注入凭据及运行 CLI 前，静态核验整批目标命令的唯一字面量 site/name/access 注册。只允许 read，或明确且唯一的 --mode dry-run；submit、重复 mode、未知/缺失/多注册 metadata 拒绝，并清除继承写开关。这个防误选择策略不是代码沙箱或后端权限证明，仍须审查主/辅助查询副作用；普通业务执行器继续保留受授权的写能力。

角色与工艺定义命令为 server-roles、client-roles、production-arts。角色返回总量是服务过滤后的定义数量，不是全部数据库角色；工艺列表及角色定义接口没有服务端分页，limit只限制投影输出。客户端角色ID零值合法，按实际契约保留。


功能映射读取使用 function-mappings --page/--limit（按组件分组），以及 function-mapping --component <实际既有组件ID>。详情可读取无保存母版的组件候选；候选成功不证明保存母版存在，空列表 EMPTY_RESULT 不计业务成功。网页查看入口使用 operation=check，只读取组件名/结构计数；详情参数值与 App 功能完整字典不输出。

最低固件配置使用 minimum-firmwares --page/--limit，历史使用 minimum-firmware-history --model <列表实际型号> --limit。配置列表是型号到最低固件版本的元数据；历史后端无分页，limit 为本地输出，returnedTotal/truncated 单独记录。仅比较网页操作时间列，不收操作人/内容，不打开新增编辑或构建发布操作。

厂商对接使用 factory-integrations 的实际已有 ID，再调用 factory-integration --id <ID> 与 factory-integration-files --id <ID> --page/--limit。详情 requestedId、文件列表 requestedIntegrationId 是请求来源，不能代替响应回显。查看页可能自动发 POST validateCodeRulesRuleId，源码确认 SELECT 链后才执行该页面；不要忽略 watcher 请求。文件只输出记录数量、状态、时间及返回记录 ID，不输出接口地址、文件名、操作人、签名链接或内容；网页名称与原生 ID 映射未验证时明确未覆盖。

production-bindings --page/--limit 读取已发布生产方案的绑定工艺摘要，production-binding --model <实际列表型号> 读取组装视图基础字段、工艺名与组数。详情服务只按型号聚合，即使网页传 artPlanId 也不能声称隔离该方案；默认组可能未保存。只读页面为 binding-process-config 与 binding-process-config-detail，不进入编辑/保存；日志和配置值排除，零数量的页面横线映射单独核验。

test-item-models --page/--limit --model-type 0|1 查询既有管理记录，可选 status1/3/4；未发布候选使用独立 test-item-unconfigured-models --page/--limit --model-type 0|1，不含保存 ID。网页筛选标签为审核状态，表头为发布状态；选择未发布并查询只访问 new-nosave 列表，禁止进入 test-item-detail/info 的自动初始化查询。部分计数为 null，以实际结果和网页横线分别核验。

默认工艺方案可用 `production-plans --page 1 --limit 10` 与 `production-plan --id <既有列表ID>` 读取许可元数据；旧 `modelNoCategory=-1` 默认值、计数口径及辅助标签对照见 [读取契约](production-plan-read-contract.md)。不调用注册、编辑或发布，不输出完整规则配置。

工艺功能字典 `production-art-abilities --limit 100` 的 limit 是本地输出上限；工艺元数据 `production-art --id <实际既有ID>` 从全量列表精确选择，再联查功能名称，明确为列表支持的查看视图。只输出允许列，排除 ext、条码规则和完整路由 query；身份检查与两端原生实际执行仍分别完成。

App第三步使用 `app-function-step3 --model <fresh实际型号编码> --limit 10`，limit为本地投影上限（1–20），服务无分页。仅参数/套餐名称、类型、可见性和租户/事件目录或选择计数，不返回配置值、事件内容或租户身份；读取前分别核验个人身份及跨服务查询契约。静态freeTier、服务tierConfig和保存选择分别对照，合法tier=-1无套餐保持原义，不运行save/release。

4G收益统计用 `sim-revenue-stats --kind customer|factory --page 1 --limit 10`；可选对应类型的实际企业代码 `--customer` 或正厂商ID `--manufacturer`、`--config 0|1`，起止月 `--start-month YYYY-MM --end-month YYYY-MM` 必须成对且最多12个月。只投影月份、设备/活跃/激励计数、规则存在状态和范围总量，不输出金额、设备标识或个人字段。规则状态不是网页已显示的配置状态证明；同月ties用完整有界月份元数据多重集合核验，不声称跨页唯一实体匹配。

DAC剩余用 `dac-remaining --page 1 --limit 10`（服务端分页，limit1–20），输出型号、生成/分配/剩余计数、isAlert与total，排除证书、密钥、SN及通知对象。网页型号与预警标签分别读取，计数与标签分别比对；不运行生成、分配或告警保存。查询只读性质与独立后台生成生命周期分别核验，不将后台变化归因于查询或把生成请求接受当完成。

CD卡片列表用 `cd-certificates --limit 10`（本地投影1–20，服务列表无分页），原生输出仅id/name/vid/pid/createdAt/updatedAt/boundModelCount/total。网页 `.cd-list > .cd-item` 对照 name、VID、PID和卡片数；`.top-title` 首 span为图标，应读仅标题的整节点或真实名称span。id/时间/关联数量没有对应卡片DOM时单列未覆盖，不补猜映射；modelNoList为空值时关联数量保留null。服务端继承DTO可能携带证书正文，只在正常授权响应中瞬时解析后做白名单投影，不log/save/preview正文、不下载、不输出型号关联值。默认页面自动参数字典与列表读取仍需独立审SELECT链；新增、编辑、下载、生产型号设置均是另行授权和审计的动作。

公开只读验收计划使用 [acceptance-plan.py](../scripts/opencli/acceptance-plan.py) 构造：`python3 <脚本路径> --adapter-directory <已审adapter目录> --namespace <本任务独占addx-console-acceptance-命名空间> --commands-json '[["<read命令>",["--limit","10"]]]'`。公开参数只有所选命令/非秘密flags和独占命名空间，目录用于本地源码读取；禁止传凭据。由构造器递归生成必要相对依赖闭包，不手工维护files；输出仅site、文件名数组、commands，不执行命令、不读取凭据、不安装runtime或联网。未知依赖、动态import及越界路径拒绝，外部包限已核pinned runtime契约。仍须审查主/辅助读取副作用，并通过命名空间及策略gate；静态闭包不是代码sandbox或线上权限/部署证明，接收执行器仍重复校验。

`production-account-summary --page 1 --limit 10` 对服务端当前页账户按企业code/name/type分组，只返回企业元数据、原始status及pageAccountCount/returnedRows/totalAccounts；pageAccountCount不是全企业账号量，totalAccounts不是命令或企业数量。页面v-show双表可能同时有DOM与预取请求，使用thead含“厂测类型”的closest `.arco-table`限定账号表，白名单读取厂测类型/客户编码/客户简称并分组计数，禁止全tbody、整行、客户账号列与复制按钮（可能复制密码）。查询实际status0与DO注释相反时保留raw0、列明未直显DOM，不推正常或删除；账户type1“渠道商厂测”与厂商type1“供应商厂测”分别对照。响应DTO含账号/密码等隐私字段时只作正常授权响应的瞬时解析、立即严格白名单聚合，不读取敏感属性、不log/save或输出原始响应；不调用重置、删除、创建、修改类型。无排序分页导致成员变化时保留差异，不重复采样凑一致。

厂商目录 `production-manufacturers --type all|0|1 --limit 10` 使用独立 GET `/worker/consumer/list`，不同于 `manufacturer-codes` 的业务字典。all省略查询参数，0/1分别显式 `?type=0|1`，服务端仅SELECT筛选；原生投影验证所有返回类型符合selected，未知或不符fail closed。输出id/name/companyCode/type/typeLabel/returnedTotal，无个人字段；id没有表格直显时列为DOM未覆盖。第二tab工厂管理切换仅显示预取结果，定位thead“工厂名称/客户编码/主体类型”的closest `.arco-table`，不抓双表全tbody、整行或账号；按名称/编码/厂商类型核验，type1为供应商厂测，不能套账号渠道商标签。此公开企业目录命令limit1–100（默认10）仅本地投影、无服务器分页排序，完整类型子集与本次目录对应类型元数据比较，不虚构UI筛选操作。不点击修改类型、checkTypeChangeable、创建或复制账号。

目录元数据精确对照时，业务名称/编码读取实际 `.arco-table-td-content > span` 原始 `textContent`；不得对整td盲trim或归一化业务值以制造一致。表头及静态类型label可按其结构解析，须与业务数据原文区分；保留原生值并说明真实差异，不重复原生请求凑匹配。网页没有type筛选控件时，原生筛选返回与全目录合法类型子集逐行对照即可，报告该验证范围，不虚构UI筛选操作。

SN红线配置元数据用 `sn-redlines --page 1 --limit 10`，可选 `--manufacturer <实际工厂ID>`、`--model <实际PCBA型号>`、`--status 0|1|2`（正常/达红线/停用）；limit1–20，状态筛选先全量计算后分页，不承诺服务工作量有界。仅返回工厂名称、型号、阈值、可注册数、enabled、redlineReached、状态、更新时间及total，不输出通知渠道对象/人员/账号/SN。可注册数量排除未生成批次、禁用、已注册和10分钟占用；动态计数不可当固定快照。页间和rowspan合并行按实际列及沿用厂商核对，网页字典名与服务解析名分别验证；enabled0/1与查询status0/1/2分开。实际页面mount主列表及工厂/PCBA辅助字典须审读链，编辑/飞书同步/成员查询不属于默认列表读取，不触发。

`mac-available-count --type 0|1|2` 只调用 `/mac-address/available-count?type=<实际项目>`，SELECT COUNT按项目/status范围返回标量，不读取MAC地址、SN或文件。页面项目切换唯一countGET，初始availableCount=-1只是未查询sentinel；成功真实0可比较计数，但不等于非空地址列表或完整地址库存验证。邻近HTTP GET `/mac-address/export` 会获取锁、acquire地址并更新状态后下载，必须归类write并排除。比较实际selected项目label与 `.available-info .count-text`，不写quantity或点击导出。Arco选定后readonly textbox可能opacity/不可点击，应先fresh snapshot，再点可见selected label/真实view容器展开，避免强制fill隐藏input或不必要改值。数量COUNT不证明实际分配地址范围或主库事务快照。常值TTL锁无持有者校验、续期与fencing时不能代替CAS；无owner的解锁可能删除后继锁，finally解锁异常也可能遮蔽已提交结果。事务内分配与CSV构造、提交后的HTTP下载分别判定：下载IO失败不代表分配回滚，无幂等/收据时重试可能再消耗地址，不自动重放。合法type0不能用truthiness判断是否刷新，文件下载链接点击也不证明保存成功。

红线配置、通知和冷却动作不由只读 `sn-redlines` 提供。自然键upsert忽略ID时必须区分创建与编辑，禁止用省略通知字段作局部更新；元数据投影不能证明完整私有原状。GET通知回调、冷却写入/清理仍属业务写，eligible数量不能称delivery；Redis failopen与吞发送错误时不以成功响应证明消息送达。缺完整状态、版本、ownership和业务授权时保持离线意图，禁止提交payload或用修改请求探测读取。

`prepaid-cloud-products --page 1 --limit 10` 使用 POST `/recharge_cloud_service/product/list` 默认空日期/id/cancel筛选，服务端分页limit1–20；仅输出id/name/settlementStrategy/settlementLabel/createdAt/updatedAt/total，排除金额、币种和租户。白名单对照商品名称、结算策略、组合时间单元格两p及总量，禁止全行、金额、编辑和导出；详情只按下面独立已审读取方法进入；id不在表格直显时列为DOM未覆盖。页面mount辅助 `/cloud/service/product/param` 来自普通商品表，主列表为预充值表，命名空间不同，不推同ID同商品、不猜filterID或替换接口。cancel0服务开始收费、1服务结束收费，null原值不改成1；UI任何非0走结束收费的ternary可能掩盖空值，分别报告。列表无ORDERBY，不重排或重复采样制造相同分页；恢复历史本地筛选时先读取实际范围，默认空条件与该范围分别验收。

预充值商品写契约独立于普通云商品，不共用ID、授权范围或编辑原状。区分空ID创建与非空ID更新；未核目标存在、更新行数的成功返回可能是无操作。名称禁用控件不是服务端不可变证明，创建时间未提交不是请求字段被剔除。详情DTO缺租户、表单默认币种叠加及隐藏价格配置，不能还原完整私有状态；租户/OEM分桶与通配价格索引须追消费范围，不能按可见行推定影响范围。缺所有权、完整状态和原子CAS时，写能力仅作无载荷离线意图审计，永久拒绝该验收流程中的提交；离线测试不计真实写验收。

预充值企业元数据用 `merchant-accounts --page 1 --limit 10`，对应 POST `/merchant_manage/query_account_list`；默认日期、企业、客户类型为null，保留服务端既有角色与分页门禁。`summary.customerNum` 是已开通正常账户计数并供页面分页；`data.total` 是只移除本页已开通企业后剩余OEM/SDK映射数量，两者单独输出，不能合并成同一个总量，也不能把占位企业计入已持久化账户。未开通项的默认告警配置与账户DTO由内存构造，名称含create不等于数据库写入，须以实际调用链确认。

仅投影企业编码、名称、客户类型、App名称、raw账户状态与上述数量；严格排除余额、充值/支付/期初/期末金额、tenant标识、告警配置/阈值/收件人及个人账号信息。raw `accountStatus` 不能当网页余额状态标签：余额标签另外由金额和阈值推导，未读取这些值时不声称完整标签对照。网页对照只取许可企业列和真实分页计数，不读取整行、金额或告警tooltip，不点击编辑、充值、复制或导出。

日期/企业空值在主列表表示无该筛选，不能把用户指定目标缺失自动转成null扩大查询；默认主列表与已有目标请求分别保留其实际范围。后续充值/订阅子页只使用本次真实主列表返回并已核对的企业编码与名称，不猜客户ID、不用空route参数替代未知企业，更不把上游字典/同名账号当目标身份。先核页面mount的CUID辅助字典、查询与本地筛选恢复分支；纯SELECT源码与实际部署/双端/UI验收证据分开记录。

服务端只从映射中删除当前页已有账户的企业，其它页已开通企业可能以status0内存占位再次出现；跨页重复companyCode不证明同一保存态、未开通名单完整或账号不存在。保留每页原始行及状态，不擅自去重、合并为完整企业状态，不把相加分页/占位数当持久化账户总数。

企业子页使用 `merchant-recharge-summary --company <实际企业编码> --page 1 --limit 10` 或 `merchant-subscription-summary` 同参数，服务端分页 limit1–20。company 必填，来自本次真实企业主列表，不用 null/all/数字记录ID替代；路由分别为 `/pre-recharge/recharge-record-admin` 和 `/pre-recharge/cloud-service-subscription-bill-admin`，query 的 cuid 为企业编码、customerName 为其真实名称。默认日期 null/null 对齐页面未选择日期；可选 `--month YYYY-MM` 只有网页选同一实际日范围时才比较，不强制当前月或猜历史月凑非空。服务端日期按 Asia/Shanghai 日起点、结束日加一天排他；充值筛选 cdate，订阅筛选 sort_date（订单/退款时间），并非页面“生效时间”。

仅输出当页分组的元数据：充值按平台与 paymentStatus 分组，网页只核充值平台、行数与分页总量，状态未直显须列 DOM 未覆盖；订阅按商品/套餐/支付方式标签及 billType 分组，网页仅核这些列和订单状态。pageRecordCount 是当前页组内数量，returnedRows 是当页行数，totalRecords 是服务端匹配总量，不能互换或声称全企业逐行覆盖。静态退款/支付状态标签可单独解析，业务名称取精确数据节点原始文本，不盲 trim。订阅 mount 的 `/merchant_manage/query_app_cloud_bill_filter` `{}` 为全管理范围 SELECT 字典，不是选中企业专属选项，命令不据此猜商品/租户筛选ID。

空列表保持 typed EmptyResult，认证成功与业务范围为空分别记录；空结果不算非空读取通过，不扩大企业或日期范围、不创建样本。DTO 中的交易、卡片、收据、订单/用户/租户标识和金额只经授权响应的瞬时解析后排除，不输出、记录或保存；页面不读整行、私有列、tooltip，不点击充值、告警编辑、重算、刷新或导出。

### 客户端用户列表的公司／角色聚合

`client-user-summary --page <正整数> --limit <1到10>` 对 `/user/list` 做有界原生读取。默认无公司、角色、个人或日期筛选；只聚合当前页的公司标签与完整 `typeStr` 角色文本，输出页内组人数、返回页人数及服务端总人数。组人数不是角色成员数，也不是该企业全部账号数；服务端总数不是公司目录大小。不同请求无稳定排序保证时，保持页成员变化的范围限制，不自动枚举个人列表。

客户端账户写意图仅离线审计：公司 getter 的内存校验与生产账号隐式工厂创建分别追链。创建中的邮件外部副作用不受 DB 回滚保护，忽略发送布尔结果不证明投递。角色非空全替换、空集合保留须与完整私有原状对照；汇总不提供该原状。save 后缓存与权限事件要追到消费者，可能条件写入关联业务账户；普通监听与事务提交后监听不同，异步执行配置和完成结果分别核验。reset/self-password 不能继承 save 的目标类型守卫，应独立核对 requester/target 绑定、owner/CAS；未证实则拒绝 submit，无载荷，不计真实写验收。

先核对固定来源的 controller、service、DTO 和页面 mount 辅链：主查询 `selectPage` 后以客户／工厂 SELECT 关联公司、角色成员 SELECT 关联角色目录 SELECT；`typeStr` 来自目录 `roleName` 用逗号连接。角色名称本身可能含逗号，因此保持完整 composite 文本，不能拆分来证明完整 roleSet；缺目录记录被过滤，空文本不证明没有权限。`UserResponse` 主链未设置 `userPermission`，不额外调用个人权限详情补齐。消费者严格白名单丢弃个人 ID／姓名／邮箱／手机号／密码、企业内部 ID、权限编码与原始权限对象；原始响应只在内存解码，不输出或保存。

初始客户端列表还自动请求 `/permission/query_client_role`、`/user/cuid/list`、`/user/manufacturer/list`；必须连同角色／页面目录和公司／厂家 DAO 核实为 SELECT，再允许打开页面。普通默认路由与从编辑返回时恢复的本地筛选不同，不能将恢复条件当默认查询。页面空字符串条件与原生 null 条件，须由 DTO 和 `isNotBlank`／null guard 证明无筛选语义等价，不能声称请求体逐字相同；`roleIds: []` 只表示未指定角色过滤，不从旧 `type` 字段推断角色。

公司标签按实际页面 slot 的非空厂商名优先，再取客户名；保留业务文本的前后空白，不整体 trim。两个公司标签都为空时页面可显示连字符占位，该占位不是保存的企业名称。真实页面对照按当前表头“公司名称”“角色”找到对应 cell，只读取这两列和分页总量，在内存按精确公司／完整角色文本组合聚合；不读取整行、姓名／联系信息列，也不保存含这些列的截图。表头和 slot 变化时先校正投影，不能用固定列索引绕过检查。复制、编辑、导出、冻结、重置密码及添加用户均不是只读验收步骤。

组织目录用 `org-directory --limit 100`，POST `/permission/query_server_org`；部门汇总用 `org-summary --organization <fresh 精确唯一组织名称>`，重新查询最新目录后，仅在名称唯一匹配时内部解析 orgId，再 POST `/permission/query_server_org_detail`。业务名称保留原始空格，不修剪或猜ID，不输出组织/管理员/成员标识、姓名、联系方式、角色名称或权限内容。缺失/重名目标拒绝详情请求。目录只投影组织名、父组织名、depth、直属成员与子组织数；汇总增加角色/原始页面/操作数量，空权限集合的零计数合法。成员数为返回列表出现次数，不是跨组织去重人数；同人多组织不得当新用户。

组织树接口不分页，目录 limit1–100 默认100仅限制本地输出；验证完整组织结构、深度与重复组织ID后才截断，不把截断叫服务端分页或全目录覆盖。上游响应含成员个人字段，仅读成员数组长度并做白名单投影，不遍历成员对象、不日志或保存原文。UserAdmin mount 自动查询组织树与首个部门详情，两链为 SELECT；对应 LDAP 查询不调用独立同步/插入/更新方法。点击用户节点会查询个人详情，编辑/启停/角色变更属于另一路径，禁止使用。

网页最小对照只读基础信息中“部门名称”对应的数据节点和必要组织标签，先确认显示的是部门而非“姓名”；禁止整树文本或用户叶子采集。节点类型由业务 entityType 区分，不能按是否 leaf/有子节点判断组织或用户，重名文字不得猜类型并点击。成员与操作数量没有专用 DOM 计数时列未覆盖；角色空占位仅可与零角色指标核对，不代表所有权限为空。原始 rolePages 经前端路由 requiresAuth 过滤、映射、排序与合并才成为权限表，原始 pageCount 不等表行数，不能用 UI 行数宣称原始权限计数一致或完整权限验收。

### 新营销位／素材目录的原生代理读取

`marketing-slots` 与 `marketing-creatives` 只允许当前 Console 页面对应的 `/iot-tool/invokeMarketingApi`。按实际页面请求核实 account／tenant／node、内层路径、方法与分页 scope；注册服务是 `marketing`，不能因为旧服务有同名接口就改用 `invokePaasApi` 或借用 legacy handler 契约。适配器固定外层 Console origin、已核 node scope、account／tenant 和两个内层 list 路径，禁止任意 host／path／region／method 覆盖；不能把缺失指定目标自动变成别区或全局查询。分页参数先验证，拒绝非法范围之后才进入 Console 身份检查及私有凭据使用。

外层以 POST 和空对象 body 调用，内层固定 `/inner-api/creative/getSlots` 或 `/inner-api/creative/getCreatives` 的有界 page／pageSize。Console 代理直接返回营销 `Result`（`result: 0`、`data.list`、`data.total`），不是普通 Console `code: 0` envelope；独立消费者必须检查真实响应模型，不能复用错误解码器或从旧 handler 猜响应。HTTP 401／403、签名鉴权拒绝、业务非零 result、schema 变化、超时与空页分别保留 typed 结果；不输出 message、raw body、签名 URL、权限对象或错误响应预览，不自动换 host／代理或重试扩范围。空页不是非空读取通过，也不能直接归为账号错误。

源码审查包括 Console 现有 access 匹配、内存 registry 的 node／service 过滤、签名函数和 HTTP dispatch，以及营销 controller／service／DAO 与 mount 自动辅助链。两个目录业务查询是 count 和有界 SELECT；邻接新增／同步／发布是独立写路径，不触发。正常服务端签名鉴权会调用独立 auth 服务，应标记正常 authentication 调用，不能称其为业务 SELECT 或新建 Token。Console 个人身份与营销服务接受的 M2M account 是两种证据：后者不证明营销个人身份、角色范围或完整权限；registry origin、部署 release／SHA及 auth 下游源码未知时如实保留 UNKNOWN。公开源码的项目／SHA与运行页面契约分别记录，不用同名源码伪造部署映射。

严格投影名称、创建／更新时间、总量，以及素材 ctype／尺寸／语言等许可属性；丢弃链接／资源 URL、内容、layouts、描述、UUID、内部 ID和鉴权数据。保留名称的业务空白。页面营销位可能只显示名称和总量，不能把原生时间当作已做 DOM 对照；素材页面按实际表头选名称、类型、尺寸、更新时间，实际静态类型标签只对当前可证明的 code 映射，不猜未知 code。epoch 时间按页面实际浏览器时区格式比较，零值／空值占位与真实时间分开；页面未展示的语言／创建时间保留原生读取、未 UI 验证边界。只取允许列与分页总量，不打开含链接／布局的详情或执行同步／发布。页内列表不证明整库或全部权限范围。服务鉴权代码若会服务器日志记录签名／原始 auth 结果，验收不读取这些日志，也不转发或保存该内容；源码日志风险与读取通过分开记录。

`marketing-solutions --slot-name <fresh 精确 Slot 名称> --limit 50` 使用固定 prod-us Marketing 代理：先 getSlots 第一页10并要求 total 等于返回行数、目录完整，精确名称仅有一个匹配后查询 getSolutions。名称保留原始空格，编码查询参数；目录不完整或名称缺失/重复时拒绝，不猜 ID、UUID 或扩大节点范围。主响应为 controller Result 包 service Result，两层 result 均须0，再取内层 data.solutions；不能套 Console.code 或单层列表 decoder。仅输出 slotName/name/requestedLimit/returnedSolutions，排除 UUID、内部ID、modules 与 productList。默认 getSolutions 省略分页参数，服务端第一页50；可选 limit1–49 是明确的第一页范围，返回数量不是总量，满页不能证明完整，源没有 total/status/time 字段不得制造。

营销方案写契约与名称读取分开：复用浏览器缓存可能重新 INSERT 并生成 UUID，不能称编辑或完整原状复制。同步 Slot/Creative/Solution 的目标 INSERT 与已有 UUID/名称报错不是内容等价证明；级联请求或顺序批量插入没有原子整体时，失败可留下前缀，重复重试可能停在已存在项。链接复制不证明目标文件可达。逐层区分 Console 调用者与下游签名机器身份，核目标依赖、所有权、版本及部署授权；专用媒体标识迁移是写操作，不作读取探针。未核这些条件的 helper 只做离线意图分类，无 payload、无提交，不计真实验收。

方案列表网页 mount 查询 getSolutions，还预取 getCreatives 第一页、pageSize1000000；主查询自身同时读取全部租户的商品分类目录。须追这些完整 SELECT 链及正常签名鉴权，不把大辅助响应当已脱敏，只瞬时解析后投影许可元数据，不记录原文。网页只读名称列原始数据文本及行数，不读 ID/modules、商品桶或整行，不点击预览、二维码、复制、发布或创建。详情页从 localStorage 读取选中记录及商品目录，进入查看会缓存完整 raw record/modules 并再次预取 Creative，没有独立 fresh 方案详情查询；当前名称元数据命令不导航此页、不把缓存显示算独立 native/API 读取。邻接 syncSolution/syncSlot/syncCreative/addSolution 是写路径，禁止只读验证调用。

`encoding-rule-detail --name <fresh 精确唯一名称>` 先核身份，再按固定 pageSize20 读取最新 `/code-rules/info/list` 的完整有界分页目录，校验稳定 total、完整页长度与唯一ID后精确匹配名称，仅在内存用其ID查询 `/code-rules/info/{id}`。不接受任意ID、不修剪业务名称、不在只取第一页时假称全目录唯一；分页超过安全上限、total改变或成员重复时拒绝，不重复采样凑一致。详情ID/名称/raw状态必须与最新父对象一致，只投影名称、生效方式0/1、nullable状态0/1及活动规则/生成记录数组数量。数组数量是记录出现次数，不是独立人员或生成编码总数；合法0计数与缺失资源分开处理，缺失保持 typed EmptyResult。

详情 GET 依次SELECT规则主体、活动规则和全部生成记录，没有自动初始化或远端查询；上游包含注释、规则定义和记录人员/参数，仅读数组长度，不检查、记录或保存元素。网页 `/production/check-encoding-rule-detail?pageType=2` mount只查详情，子组件仅本地数据挂载；生成组件由modal显式打开后才挂载，不打开。最小DOM对照为禁用的“规则名称”输入及生效方式静态标签（0识别、1识别和生成），原始状态/数量未直显时列未覆盖。不读备注/规则值/记录人员、参数或整页；父级disabled未传给所有子组件，查看路径本身不能当禁止写证明，严禁生成、保存、确认、删除、复用与导出。

`component-detail --name <fresh 精确唯一名称>` 先核身份，固定POST `/model/component/list` 的pageSize20完整有界分页目录，校验稳定总量、页长度、唯一ID与精确唯一名称后，内部解析ID并POST `/model/component/info`。名称不修剪；无ORDERBY分页成员重复/变动时拒绝，不重采样凑一致。详情名称、编码与raw状态必须与父目标一致；仅投影name/code/group/modelType/releaseStatus及数量，不输出目标ID或个人、配置与URL字段。发布状态仅接受源枚举0/1/3/4，不按UI默认标签把未知code视已核。缺失详情可能仍带字典，无资源ID时保持 typed EmptyResult，不计字典-only PASS。

部件写契约不继承 metadata 读取结论：当前版本详情、父组发布目录和完整保存态分别核对，数量和父版本不能补齐原状。核变化判断的输入是否被提前修改、关联字段是否遗漏及相等条件，不能用相同表单认定 no-op；秒级 version 追加关联不能替代原子 CAS。saveAndRelease 会把保存扩展到 Git 分支/推送/PR 与数据库跟踪，逐阶段判断部分成功、重试风险及权限；PR/待审状态不算 deployed。缺真实删除 handler 不注册猜测动作；未核 owner、完整原状及并发条件时仅无载荷离线意图审计，拒绝 submit。

ModelComponentResponse未定义版本字段；parentVersion/parentReleasedVersion从fresh父列表继承的DO字段读取，不能宣称详情回显或CAS/完整已保存状态。selectedParameterCount是当前version的paramIds Map键数，selectedSupplierCount是选中供应商Set的长度，仅读数量、不检查值或ID元素，合法空集合保持0。网页按已发布部件组version加载参数定义，再解析当前部件选中key，二者版本不同，不能把页面参数格数当完整当前配置计数。

查看页mount还查发布组参数、有效编码规则、空ID部件组字典；附件参数存在时读取类型文件列表并签GET URL，相关链为SELECT及签名，不上传、写库或下载。空ID字典的实际解析/成功状态另核，不把安全源码当200证明。原生详情命令不请求这些aux；页面自然读取也不保存/输出链接或请求内容。页面会console.log参数与文件目录，禁止读取浏览器日志或原始响应。DOM仅按“所属部件组/部件名称/部件编码/型号类型”静态描述标签定位相邻数据节点，业务文本保留原值；不采整基础信息（含备注）、参数区域、附件或整行，状态/版本/数量未直显时列DOM未覆盖。禁止编辑、发布、生成、复制、上传与下载。

`production-plan-factory-summary --name <fresh 精确唯一默认方案名>` 先核身份并完整有界分页读取 `/produce/plan/query-default-produce-plan-list`（固定pageSize20），校验稳定总量/页长度/唯一ID后，唯一精确名称内部解析目标，POST `/produce/plan/default-produce-plan/{id}/manufacturer-info-list`。目标ID不接受外部输入或输出，名称保留原文；缺失或重名拒绝，不猜旧方案。仅读name、eligibleFactoryCount、eligibleRegisteredBindingAppearanceCount、defaultPlanFactoryCount和customPlanFactoryCount，不读取/输出 nested绑定、配置、参数或成员字段。内部临时校验eligible厂家ID唯一及registered属于支持集，不保存ID；registered允许重复并保持出现次数，不能去重或要求不超过eligible。

支持集来自现有方案型号对应的工厂/供应商 SELECT，不是全部生产工厂；登记数组是已保存绑定与当前支持集交集，不是原始绑定总数。useDefaultPlan由该厂家ANY自定义型号方案的COUNT是否0推导，不是当前方案/当前型号的完整配置指标。空型号分支仅返回方案ID且数组null/未设置，保持 typed EmptyResult而非补0成功行；现有型号但两个非null空数组可以合法返回零指标，不能声称有非空工厂样本。原生仅请求父目录和汇总；网页 mount还查询 `/user/manufacturer/list`用于企业名称展示，两链均SELECT，不调用LDAP同步、方案初始化、复制、保存或发布。

网页只对照两个顶端数量及当前方案标题，不采厂家卡片/绑定ID/配置或整页，也不点勾选、保存、保存并发布。UI caption“已导入工厂数量”实际用绑定数组length，按出现次数核对，不能改成distinct以凑一致。UI n||“-”将合法0显示为占位，保留此差异；非默认方案的status1由同数组成员检测得出，status2“未配置”分支不能作为独立缺配置证明。默认/自定义布尔数量没有专用DOM计数时列未覆盖，不能从卡片标签推完整业务状态。

### 兑换管理的默认批次元数据

`redeem-license-batches --page <正整数> --limit <1到10>` 只接受当前页面默认空筛选，先拒绝非法分页或型号／日期／类别／兑换码／displayId／region override，再运行 Console 身份预检与原生读取。当前 mount 辅助为 `/manage/list/param` 的 `deviceCategory` 字典；主接口为 direct POST `/iot-tool/listExchangeCodeDisplay`，普通 Console `code: 0` 解码，body 只有 page、pageSize和默认空字符串过滤。注释中的 `invokePaasApi`／work路径、未实际发送的 frontend requestConfig node／env 不能当运行契约；后端硬编码US并经部署envPrefix选PaaS服务，实际页面与部署选择仍须核验。

完整源审查包括 display page/count、每批 queryAllCodeByDisplayId、工厂使用code SELECT和model映射SELECT；过期／使用状态 setStatus、codeNum与modelNoList构造仅内存，不等于数据库更新。正常服务签名鉴权单列，未知 registry release／auth 下游不伪造纯SELECT或营销／PaaS个人身份。外层少量display分页仍会为每批无分页读取所有代码，逐代码查工厂使用记录；保留unpaged fanout/N+1性能边界，有界timeout不自动重试或扩页。源码查询和正常业务响应只保留白名单，含真实兑换码的中间结果／服务器日志不读取、不转存。

输出仅商品名称、当前字典类别标签、实际modelNoList名称拼接、生成／可用／已用／禁用数量与页元数据。类别关联ID只在内存匹配本次字典，不输出；缺标签是unknown／null，不能猜类别。商品和型号名称保留业务空白；空原生标签与页面连字符占位分开。只取“生成数量”“设备类别”“设备型号”“商品名称”“数量”这些实际表头对应cells和分页总数；显示数字格式可按格式化规则核对，不能trim业务文字或用序号当资源ID。兑换码、prefix、UUID、displayId／tierId、URL、交易／个人、参数、原始error message/body均丢弃；不打开详情代码表、创建／禁用／变更型号、不下载CSV或导出。当前适配器不投影时间，不能声明时间DOM已验。

数量合并US代码库存与工厂使用观察，不是跨库原子快照；过期条目仍在total，可用数量不包括它们，使用状态可覆盖内存过期状态。list型号LIKE但count型号等于、list日期区间但count日期LIKE原日期串，不能假定筛选后的分页total正确；默认空筛选以外的能力仍需独立契约和验收，不能通过缺失目标／空值扩为全局查询。列表page/count、状态组数量以及原生Token权限各自证明，不据此宣称完整兑换库、逐码有效性或后端资源ownership。路由管理 `/authority/router-management` 的mount无读取，唯一添加动作 `/permission/edit_page` 先SELECT再INSERT；其静态页面不算只读命令，不能以既有／空pageId试写探权限。

兑换批次直连接口的原生预算仅精确 `/iot-tool/listExchangeCodeDisplay` 为40秒；路径带额外query或相邻 `/iot-tool/listExchangeCodes` 不继承这个预算。`/revenue/sim/list` 既有35秒，其他仍15秒，无环境覆盖、无自动重试。AbortSignal必须实际传入请求，超时仍抛typed TimeoutError，不把同样失败结果匹配计为PASS，也不因等候增加而变更筛选／目标。上层单CLI预算须容纳身份预检与40秒请求，初始失败报告保留，修复后用独立运行收据记录结果；扩大预算不等于解决unpaged/N+1或原子计数问题。

`sim-agreement-logs --kind customer|factory --name <fresh 精确企业标签> --page 1 --limit 10` 先核身份与既有LDAP门禁，再完整有界分页读取 `/devide/sim/coefficient/list`（kind→customerType0/1，固定pageSize20）。校验稳定总量、完整页及唯一ID后，按客户 `cuid-customerName` 或工厂 manufacturerName 原始文字精确匹配，ID只保留内存。企业有多个协议时，配对 `--valid-start/--valid-end` 使用本次实际父对象的整数epoch组合，0值不得truthy丢弃；未唯一或时期不符时拒绝，不猜ID、名称或历史日期凑样本。父响应含金融与激励字段，仅瞬时解析后保留目标标签/有效期，不读取或输出系数、金额或激励内容。

SIM 协议写能力另审：先写激励后拒绝协议的链意味着失败响应仍可能部分成功，不能作读取探针或盲重试。LDAP 门禁不补齐 owner/RBAC/CAS，嵌套对象ID也须独立绑定授权范围。对照日期验证分支与实际 create 条件，分开 UTC/本地日界、重叠区间和生效查询语义。日志没有追加不证明 DB 无操作；日志 metadata、表单缓存不足完整比例/激励/有效期原状。save、日志、月变更信息及后台消费分别核事务/结果，不把未来或重处理影响当 save 即时重算。未核完整财务状态、授权与版本则只离线无 payload、拒绝 submit，不计真实写验收。

日志POST `/devide/sim/coefficient/logs/list` 请求内部目标ID、pageIndex/pageSize，SELECT日志与操作用户后仅投影原始 operationTime、requestedPage、returnedRows、totalRecords；不访问 operator/content，不分类内容、不读取个人或设备标识。多个日志相同时间仍各保留一行，不能当distinct事件或身份；时间字符串不修剪、不猜服务器时区或转UTC。main响应无协议ID回显，不制造“已回显目标身份”证据；fresh父来源、请求范围与正常身份检查分别记录。

页面 mount只有日志列表SELECT，无同步、写日志或自动刷新；控制器文档标题误称保存不改变实际查询链。服务端操作用户不完整时也返回空列表/total0，typed EmptyResult保留，不能证明没有历史、认证失败或非空读取通过。网页仅读操作时间列原始数据节点、行数与分页总量，禁止整行、操作人、操作内容、金融列或全页；现有日志链接的企业标题用于展示，不是日志操作人。不得导出、编辑/保存协议或触发其他写路径。

工厂标签的实际数据span由 `${manufacturerId}-${manufacturerName}` 生成，CLI的 `--kind factory --name` 必须是原始 manufacturerName，不能把显示编号作为名称或协议ID输入。最小DOM方法先读取客户名称列的真实数据span，仅校验源码十进制编号加首个 `-` 前缀，再用 `raw.replace(/^[0-9]+-/, "")` 剥离一次；若无此已核前缀则报告选择器/契约差异，不猜。不对业务名称使用split后取单段或trim，保留后续所有连字符、编号字符及首尾空白。客户分支的CLI标签继续为源码 `${cuid}-${customerName}` 组合，不剥离客户编码。只对照业务标签，不记录内部工厂编号。


### SIM 卡统计当前页

`sim-card-summary --page 1 --limit 10 --status all` 固定 POST `/device/sim/list`，身份预检后只提取 activateStatus。可选 `--status 0..5` 保留数值0，其他筛选保持页面默认 null/blank。仅输出当前页状态出现次数、returnedRows 和 totalUnfilteredRecords；null状态保留，不补造不存在的组。后置筛选、无排序及跨区补充不支持完整库存或稳定排序结论。源服务内部补充所用标识不由CLI访问或输出，网页比较也只读状态列及pagination。`QuestionBack`现有query含惰性备份/写入，不能作为只读替代入口。


`prepaid-cloud-product --name '<exact fresh productName>'` is the independent
prepaid metadata detail read, not ordinary `/cloud/service/product/info`.
Validate the sole exact name argument before native identity preflight; do not
accept an ID, host, date/filter or arbitrary path override. Resolve the name
from a fresh complete bounded parent `/recharge_cloud_service/product/list`
directory with the existing blank-filter body, page size20, maximum100 pages
and2000 rows. Require stable total, expected page sizes, unique positive IDs and
exactly one untrimmed name match. Keep only internal ID/name; absence or ambiguity
stops before detail. No ORDER BY exists, so reject inconsistent retrieval and
preserve actual differences rather than retrying to manufacture a match.

POST `/recharge_cloud_service/product/info` with only the resolved ID. The
selected backend method uses `selectById`, response-only `BeanUtils` copying and
a static currency enum; neighboring `/save` is not part of this read. The DTO
copies prepaid data into an ordinary product response shape: do not interpret
the shared Java type as permission to use the ordinary namespace. Require exact
ID/name echo. Project only name, raw nullable cancel/currency, their valid enum
labels and raw nullable create/modify times. Prices, formatted prices, tier/OEM,
tenant, currency-option collections and raw DTOs must not be accessed, logged,
saved or output. Missing detail may throw server error; do not invent a row or
zero values. Missing required response fields/unknown enums fail closed; explicit
null remains null, including labels.

The safe UI route is `/pre-recharge/check-recharge-cloud-goods` selected from a
fresh parent “查看” row. It mounts only the info request and has no automatic
business mutation/auxiliary request. Read only `.value-txt-style` inside the
three `li` entries whose static labels are 商品名称, 结算策略 and 结算币种;
never read the whole panel/UL or adjacent 商品价格 item. Preserve the business
name data node exactly; parse static labels separately. UI `cancel === 0` and
`currency === '￥'` ternaries map other/null values to a valid-looking alternative,
so the label alone cannot establish the raw enum. Compare observed valid enums,
record null/fallback discrepancies explicitly, and leave dates/internal ID as
not DOM-covered when the view does not display them. Do not enter 编辑, submit,
export, pricing detail or downloads. Source/offline staging is separate from
fresh deployed both-host/UI acceptance.


When a strict argument-key allowlist fails under real OpenCLI, inspect the exact
runtime's Commander Bridge and execution preparation before relaxing it. Run
that actual Bridge source with the installed Commander parser in an offline VM;
stub execution/native transport, registry and rendering so only framework
argument hydration runs. A fixture calling `func({name})` alone misses provenance
metadata and is not framework compatibility evidence. The explicit name option
can produce `__opencliOptionSources: {name: "cli"}`; output format/trace/debug
options are handled separately and must not be broadly admitted as business
kwargs. For `prepaid-cloud-product`, allow only `name` plus that optional exact
single-name `cli` metadata shape. Reject malformed metadata, additional nested
parameter names and arbitrary `__` prefixes; never forward provenance into a
request or use it as authorization. Unknown ID/origin/filter overrides still
fail before identity, and identity denial still prevents both parent and detail
queries. Retain the initial real typed failure, a regression that is RED before
the narrow repair, offline GREEN afterward and an independent real retry; no
credential capture or business probe is needed to diagnose this mismatch.

On the prepaid read view, the static label span is `.title-txt-style`, not a
synthetic `.label`. Locate the containing `li` by one of the three allowed static
labels, then read its `.value-txt-style` node only. If the selector contract does
not match, inspect fresh authorized structure rather than falling back to entire
panels or adjacent pricing text. Raw business names remain exact and untrimmed.

### 角色改名离线客户端

`../scripts/opencli/addx-console/role-name-offline-client.mjs` 提供 client/server 独立请求构造、完整关联校验、diff/hash 和独立 fixture 回读；测试位于 `../scripts/opencli/write-tests/role-name-offline-client-tests.mjs`。只有显式 fixture 来源可参与，不能将 query role VO、任意 callback 或摘要伪装为完整保存态。请求只包含不可变 roleId、目标 roleName 及完整保留的 rolePages；不携带未参与编辑的类型字段。提交入口永久拒绝，无网络和生产 CLI 注册。真实完整原状接口、实际编辑权限、原子并发保护和异步结果仍须另证；fixture 测试不计实际写入验收。


### 生产报告缓存目录

`production-report-catalogue` 无参数，先复用 native identity gate，再 POST `/factory/test-item-result/export-info` 的八个默认空字符串字段。仅白名单输出四静态类别、各缓存集合 cardinality/cacheState 与可空 cachedReportCount/reportCountState；禁止任意过滤/host/ID 及模型字符串、manufacturer map、下载属性输出。Service 默认 hasFilter=false，Redis 普通 GET/SMEMBERS 与 manufacturer 按 ID SELECT 无 lazy init；缓存 refresh/parse-sync/export 是独立有副作用路径，不能调用。

路由 `/production/device-produce-trace` 必须区分自动隐藏弹窗读取与手动 SN 查询。设备生产工艺 tab 的 export dialog 开关只改变可见性；仅展开型号 cascader 读取父类别标签并取消，不 hover 或选择未授权子资源、不点确认。第一列标签对照只证明分类显示，子集合数仍需独立有界 DOM/虚拟化证据，cache null/zero 不推断持久化报告总量。当前部署 SHA 与源码证据分开，UI cache 快照可能跨请求变化。

Cascader 子列计数可在未选择、未发新请求的条件下 hover 父选项：确认第二列已渲染全部真实 option、没有 virtual marker，并区分 scrollHeight/clientHeight 与 DOM 数量；长列表可滚动不等于虚拟列表。只读取 cardinality，不读取子资源文本或点击选项。没有完整渲染证据时保留数量未覆盖。

IoT 配置目录只读用 `iot-service-node-summary`，无参数、身份先验、POST `/iot/manage/serve/node` 不发 body/query；仅投影当前响应环境字符串和节点数组 cardinality，不能输出节点名称/host/连接字段。环境标签按后端原文 exact，不借框架 Env 静态枚举推造完整组，也不把静态页面选项当配置。网页只在内存切换实际返回环境，展开完整非虚拟 node options 核数量，不选择节点、填SN或调用查询/保存/删除；目录数量不证明健康或设备权限。正常业务空白保持空白，不允许 host/origin/SN 任意覆盖。

测试项详情非空参数分支须由 fresh 有界 `test-items` 父响应选择真实 resource，内部 ID仅用于固定 `test-item` 读取，不手猜、不输出关联ID。页面列表允许四列 itemCode/itemName/完整参数及工艺 joined text 对照；参数名本身可含逗号，不能 split 判断条目数量或 role/参数集合。数组 count 只原生证明，DOM需独立结构，不把 editor临时空行算持久化参数。禁止编辑/保存/阈值/脚本/日志UI；info 服务端既有日志 SELECT 即使客户端丢弃仍是上游查询范围，应明确列边界。

自身企业统计须验证 server own-company binding 与当前主体，不可借用目录其他企业 CUID、把空/0绑定当跨企业默认，也不得提供覆盖参数。UI入口重定向或隐藏不证明 REST授权拒绝；未发业务请求明确记未尝试，不造403/EMPTY。已授权正常入口和可信自身绑定未建立时拒绝查询；valid-empty可作为空分支事实，不计非空样本验收。

生产方案多型号分支由 fresh有界 `production-plans` 第一条 modelCount>1 的同一响应行派生内部 ID，再固定 `production-plan` 详情，禁止历史ID/跨行名称拼接。列表 distinct modelCount 与详情 relation appearances分别记录，不因新分支增加唯一命令。仅导航 readonly查看路由且完整辅助请求先证明只读，不能进edit/clone/publish/delete；型号原文joined、工艺名及结构数量分别DOM比对，工厂显示label不证明ID，legacy分类sentinel不推DOM绑定，默认组占位不证明保存配置。

### 硬件参数与受信执行边界

硬件参数的 savedValue/displayValue 默认隐藏：当前只允许已审公共 boolean code `supportBirdDetect`、`supportShutterRemote` 的合法 enum 值与固定公开 label。未知 code、未知类型、异常值及 label 不得由命名黑名单推断为安全。返回参数定义不代表允许返回所有保存值。

远端执行器的 metadata、静态依赖闭包和 dry-run 检查是防误选择策略，不是 JavaScript sandbox。只能转发受信、经过代码审查的 checkout bundle；不得因为源文件声称 `access:read` 就执行任意 JavaScript 或认为后端权限已经证明。
