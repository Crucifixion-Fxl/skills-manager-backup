# 默认工艺方案只读字段契约

读取已有默认工艺方案时，先从真实列表取得 ID，再用 `production-plan --id <现存ID>`。`POST /produce/plan/default-produce-plan/{id}` 的已审计调用链只有三次 SELECT；网页详情另读取设备类别字典和型号支持工厂。禁止通过新增、编辑或发布页面准备验收数据。

## 旧型号类别默认值

`modelNoList[*].modelNoCategory` 是方案关联表的旧字段。固定后端源码 [sql/2024/checkpoint](https://gitlab.addx.ai/CLOUD/revenue-sharing/-/blob/67ff74a8feab308a7a60b67275094ba1e7cd3180/sql/2024/checkpoint) 明确把 `model_no_category` 默认值设为 `-1`，同时引入 `category_id`。前端 [AddEditProcessConfiguration.vue](https://gitlab.addx.ai/CLOUD/revenue-sharing-front/-/blob/2eb99779e75ae8f90f0a99b8c866bf87b3caa768/src/views/production/process-scheme-management/components/AddEditProcessConfiguration.vue) 在保存关联行时提交 `categoryId`，已注释 `modelNoCategory`；查看页面按首条关联行的 `categoryId` 显示设备类别。

因此此字段的原生读取投影保留恰好 `-1` 这个已核默认值，并继续接受原有非负整数。不要把 `-1` 当成真实设备型号类型、推测某一具体类别或转写成 `0`。它也不是全局允许负数的依据：其他负整数、非整数、字符串 `"-1"` 仍报契约错误；`categoryId`、厂家 ID、数量和现存资源 ID 的校验不变。

网页没有展示旧型号类别值，不能把 API 中 `modelTypes:[-1]` 算作网页字段已比对。网页可比对设备类别标签、型号范围、工艺名称和工艺数量；内部数值 ID 与旧字段仅按源码/返回契约核验。列表型号数量按唯一型号去重，详情数量按关联行数，二者语义须分开记录。

## 回归与验收证据

给字段新增合法默认值时，先添加脱敏 fixture 验证现有投影失败，再做仅该字段的窄修，并验证其他负值/类型错误仍被拒绝、配置值和原始规则仍不输出。离线测试通过仅证明本地契约；先前两端真实失败应保留，修复后的真实请求和网页对照单独记录，不改写为从未失败。
