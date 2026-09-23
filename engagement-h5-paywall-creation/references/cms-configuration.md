# Marketing CMS 配置边界

## 数据关系

运行时链路保持如下职责：

```text
App / Engagement SDK 打开 paywallId
  -> Engagement Service 获取并组装 Paywall 内容
  -> Marketing CMS Paywalls 返回已发布配置
  -> Paywall H5 根据 templateKey 选择已注册模板
  -> Template Kit 加载价格并处理购买 / Bridge / 埋点
  -> 模板只渲染只读 view model
```

H5 模板代码不直接请求 Marketing CMS，不读取 raw Payload document，也不自行获取价格。

## 当前两类配置

执行时必须读取 Marketing CMS 最新 `Paywalls.staging.ts`、对应 validator 和 Engagement 的 `templateKeys.ts`，不要把本页当成永不变化的 schema。

| 类型 | `templateKey` | CMS 数据 | 适用场景 |
|---|---|---|---|
| 标准 Paywall | 留空，运行时解析为标准模板 | `components`、商品容器、按钮区等 | CMS blocks 能表达的通用购买页 |
| 个性化 Paywall | 当前注册的个性化 key | `offerMatrix` 与分群配置 | Figma 按用户分群、设备档位和 Offer 展示 |

新 key 必须同时在 Engagement template catalog 和 Marketing CMS 的选项 / validator / transformer 中存在。Skill 不修改这些源文件，也不假设只改 H5 manifest 就能上线。

## 个性化矩阵核对

当前代码的关键维度包括：

- audience：`LAPSED` 或 `NEVER`；
- segment：由当前 validator 定义；
- device tier：`SINGLE`、`DUAL`、`MULTI`；
- Lapsed 还包含目标商品 tier type；
- 每行包含 offers 和默认商品；特殊分群可能包含独立 All Plans 商品组；
- Product ID 必须来自当前 tenant / 套餐商品目录；
- Offer ID 是否必填由 Offer 类型决定；
- 订阅商品的月份必须与商品目录一致，一次性商品不能伪装成订阅。

不要手工猜测完整矩阵行数或枚举。创建草稿前从当前 validator 和 Admin editor 读取规则，使用 CMS 自带初始化能力，再按业务输入替换商品和 Offer。

## 安全写入顺序

1. 默认只操作 Staging。
2. 创建稳定业务 key；重复 key 使用 PATCH，不创建第二份同名记录。
3. 保存 Draft，先核对英文源文案、模板、商品、Offer、默认选择和支付方式。
4. 使用 CMS 预览和二维码在目标 App / 环境核对。
5. 英文确认后再触发翻译，逐语言检查截断、换行和图片内文字。
6. 发布到 Staging 后做 H5 与 App 验收。
7. 只有用户明确要求且验收证据完成，才依次 Transfer 到 Pre、验证，再由 Pre Transfer 到 Prod。

Transfer 没有通用回滚 API，Draft 也不会被常规 App 读取。不得把 Save Draft、Publish 和 Transfer 混为同一步。

## 配置与代码的分工

| 变化 | 优先位置 |
|---|---|
| 文案、图片、已有 blocks、商品和 Offer | Marketing CMS |
| 布局、视觉组件、动画、模板私有展示 | H5 模板目录 |
| 新字段、通用 schema、校验和转换 | Owner 评审后的 Marketing CMS 代码 |
| 价格、支付、Bridge、基础埋点 | 既有 Paywall 框架，不由业务模板实现 |

如果同一个视觉变化可以通过 CMS 配置完成，不修改 H5。反之，如果 Figma 需要 CMS 和模板都不存在的新数据语义，不能用硬编码绕过，应进入 Owner 评审。
