---
name: addx-console-admin
description: A4x 内部管理后台 (console.addx.live) 浏览器自动化操作助手。通过 agent-browser 控制浏览器完成后台管理任务，用户可实时看到操作过程。当用户需要在 A4x 后台查询云服务账单、客户分成规则、设备生产数据、工单管理、固件版本、电池管理、4G增值服务，或执行批量操作（导出设备信息、配置 IoT 参数、管理兑换码等）时使用此 Skill。只要提到 A4x 后台、console.addx.live、或涉及 OEM 客户管理和云服务运营的任务，都应使用此 Skill。
---

# addx-console-admin

A4x 内部管理后台 (console.addx.live) 的浏览器自动化操作助手。通过 agent-browser 控制浏览器，在用户可见的情况下完成后台管理任务。

- 页面操作指南见下方各模块说明
- 完整 API 端点参考（备选方案）见 [references/api-reference.md](references/api-reference.md)

## Description

此 Skill 指导 AI 通过 agent-browser 操作 A4x 后台管理系统浏览器，覆盖 8 大业务模块、80+ 个页面：

| 模块 | 路由前缀 | 核心功能 |
|------|---------|---------|
| App管理 | `/app-management/` | 协议配置（服务端）、截图工具。⚠️ 客户端协议配置和协议模板管理页面已弃用 |
| 云服务管理 | `/sharing/` | 收益统计、客户/工厂分成规则、商品、账单、收入报表、成本设置 |
| 4G增值服务 | `/4g-service/` | 分成协议管理、增值收益统计、SIM卡统计查询 |
| 产品管理 | `/product/` | 硬件规格、套餐白名单、App功能配置、部件/部件组、设备类别、电量计、文件中心、固件构建 |
| 生产管理 | `/production/` | 生产账号、CUID/出厂统计、测试项、发布、工艺方案、证书(DAC/CD)、SN号、电池、三方对接 |
| 运营管理 | `/operation/` | 工单管理、售后工具、Slot/Creative 营销管理 |
| 内部工具 | `/temp-tools/` | 设备配置(coturn/PIR/网络)、兑换码、Redeem License、IoT管理、设备日志 |
| 权限管理 | `/authority/` | 客户端/服务端用户管理、角色管理、路由管理 |

## Rules

### 前置条件：agent-browser

此 Skill 通过 `agent-browser` CLI 控制浏览器。安装方式：

```bash
npx skills add vercel-labs/agent-browser --skill agent-browser --agent claude-code -y
```

基本用法（open → snapshot → interact）：

```bash
agent-browser open https://console.addx.live/
agent-browser snapshot -i   # 获取元素引用 @e1, @e2...
agent-browser click @e3
agent-browser fill @e1 "username"
agent-browser wait --load networkidle
```

### 操作原则

1. **浏览器操作优先** — 优先通过 agent-browser 操作浏览器 UI，用户可实时看到过程
2. **API 调用备选** — 仅在浏览器操作不便时（批量数据导出、脚本集成）才直接调 API
3. **凭证安全** — 让用户在浏览器中手动输入密码，不要在对话或代码中传递密码
4. **操作前确认** — 写操作（创建/修改/删除）前必须告知用户并等待确认

### 登录流程

```bash
agent-browser open https://console.addx.live/
agent-browser snapshot -i
# @e1 = input[type="text"] 用户名, @e2 = input[type="password"] 密码
# 让用户手动输入凭证，或经用户同意后填写：
agent-browser fill @e1 "username"
agent-browser fill @e2 "password"   # 仅经用户明确同意
agent-browser click @e3             # 登录按钮
agent-browser wait --load networkidle
# 等待跳转到 /home/home-index 表示登录成功
```

### UI 框架：Arco Design Vue

此后台使用 [Arco Design Vue](https://arco.design/) 组件库，关键选择器：

| 组件 | 选择器 | 用途 |
|------|--------|------|
| 侧边栏菜单组 | `.arco-menu-inline` | 可展开的菜单分组 |
| 菜单组标题 | `.arco-menu-inline-header` | 点击展开/折叠子菜单 |
| 菜单项 | `.arco-menu-item` | 可点击的叶子菜单项 |
| 菜单项文字 | `.arco-menu-item-inner` | 获取菜单项名称 |
| 菜单组内容 | `.arco-menu-inline-content` | 子菜单容器（折叠时 display:none） |
| 选中状态 | `.arco-menu-selected` | 当前激活的菜单项 |
| 表格 | `.arco-table` | 数据列表 |
| 表格行 | `.arco-table-tr` | 表格中的数据行 |
| 分页 | `.arco-pagination` | 翻页控件 |
| 按钮 | `.arco-btn` | 操作按钮 |
| 输入框 | `.arco-input` | 表单输入 |
| 下拉选择 | `.arco-select` | 下拉筛选 |
| 日期选择 | `.arco-picker` | 日期范围选择 |
| 模态框 | `.arco-modal` | 弹窗对话框 |

### 侧边栏菜单结构

后台侧边栏包含 8 个可展开菜单组，每组点击标题展开后显示子菜单：

```
首页 (顶级菜单，直接可点击)

📂 App管理
  ├── App协议配置（客户端）⚠️ 已弃用，APP下拉框无数据
  ├── App协议配置（服务端）✅ 查看/编辑各客户协议配置的正确入口
  ├── App协议模板管理 ⚠️ 已弃用
  └── 截图工具

📂 云服务管理
  ├── 云服务收益统计
  ├── 客户分成规则
  ├── 工厂分成规则
  ├── 云服务收益统计（按客户/工厂）
  ├── DB3激励收益统计
  ├── 云服务商品
  ├── 云服务账单
  ├── 云服务收入报表
  └── 云服务成本设置

📂 4G增值服务管理
  ├── 4G分成协议管理
  ├── 4G增值收益统计
  └── SIM卡统计查询

📂 产品管理
  ├── 硬件规格配置
  ├── 设备套餐白名单配置
  ├── App功能配置
  ├── 功能映射母版
  ├── 部件管理
  ├── 部件组管理
  ├── 设备类别管理
  ├── 电量计管理
  ├── 文件中心
  └── 固件构建平台管理

📂 生产管理 (26项)
  ├── 生产账号管理      ├── DAC证书管理
  ├── CUID登记统计       ├── CD证书管理
  ├── 出厂数量统计       ├── 规则引擎
  ├── 设备生产追溯       ├── 最低固件版本
  ├── 测试项管理         ├── SN号管理
  ├── 测试项库           ├── 电池包管理
  ├── 发布列表           ├── 电池生产计划
  ├── 客户整机型号管理    ├── 电芯物料管理
  ├── 设备信息导出       ├── Pack厂管理
  ├── 工艺方案管理       ├── 入库管理
  ├── 工厂工艺管理       └── 三方对接管理
  ├── 绑定工艺配置
  └── 工艺管理

📂 运营管理
  ├── 工单管理
  ├── 售后工具
  ├── 新Slot管理
  ├── 新Creative管理
  └── Slot管理

📂 内部工具 (18项)
  ├── 设备coturn服务器配置  ├── Redeem License管理
  ├── 设备后台参数配置      ├── 用户问题反馈
  ├── 设备PIR视频云存储配置 ├── 设备wifi发射功率
  ├── 重发所有设备设置      ├── 加载设备日志
  ├── 同步设备时区偏移      ├── 设备的码率上下限
  ├── 批量修改Wifi发射功率  ├── iot dtim设置
  ├── 设备网络测试          ├── 录像编码配置管理
  ├── 兑换码                ├── iot用户vip管理
  └── Gen2兑换码            └── iot用户订阅时间管理

📂 权限管理
  ├── 客户端用户管理
  ├── 服务端用户管理
  ├── 客户端角色管理
  ├── 服务端角色管理
  └── 路由管理
```

### 导航操作模式

导航到目标页面的标准步骤：

```
1. 找到目标菜单组的 .arco-menu-inline-header，点击展开
2. 等待 .arco-menu-inline-content 显示（display 不为 none）
3. 在子菜单中找到目标 .arco-menu-item，点击
4. 等待页面内容加载（通常等待表格或表单出现）
```

也可以直接导航到已知路由（更快）：

```
直接访问 https://console.addx.live/<路由前缀>/<页面名>
例：https://console.addx.live/sharing/cloud-bill-admin
```

### 常见操作步骤

#### 查询云服务账单

```
1. 导航到 /sharing/cloud-bill-admin
2. 页面自动加载账单列表（.arco-table）
3. 可用顶部筛选条件过滤（日期、OEM类型等）
4. 点击表格行查看详情
```

#### 查询客户分成规则

```
1. 导航到 /sharing/cuid-rules-management
2. 查看分成系数列表
3. 点击客户行展开详情
```

#### 查询设备生产追溯

```
1. 导航到 /production/device-produce-trace
2. 输入设备 SN 或 MAC 地址搜索
3. 查看生产工艺和测试结果
```

#### 管理工单

```
1. 导航到 /operation/terminal-user-order
2. 工单列表支持按客户、状态筛选
3. 点击工单查看详情和回复
```

### API 直接调用（备选方案）

当浏览器操作不便（如批量导出、脚本集成）时，可直接调用 API。详见 [references/api-reference.md](references/api-reference.md)。

关键信息：
- **Base URL**: `https://revenus-sharing-backend.addx.live`
- **认证**: `POST /login` → 获取 `userToken` → 后续请求 `Authorization: <token>`（无 Bearer 前缀）
- **响应格式**: `{"code": 0, "msg": "", "data": ...}`

## Examples

### ❌ Bad

```
# 在对话中明文传递密码
用户："帮我登录，密码是 xxx"
AI：好的，我用密码 xxx 登录...（不应该回显密码）

# 未确认就执行写操作
AI：我已经帮你删除了那个配置...（应该先确认）

# 用错误的选择器
page.click('.el-menu-item')  // 这是 Element UI 的选择器，不是 Arco Design
```

### ✅ Good

```
# 正确的登录引导
AI：我已经打开了 console.addx.live，请在浏览器中输入你的用户名和密码。
    输入完成后告诉我，我来点击登录按钮。

# 正确的导航操作
AI：正在导航到云服务账单页面...
    → 点击侧边栏"云服务管理"展开子菜单
    → 点击"云服务账单"
    → 页面已加载，显示 10 条账单记录

# 写操作前确认
AI：我找到了该配置项，你确定要修改吗？
    当前值：xxx → 目标值：yyy
    确认后我会点击保存。

# 使用正确的 Arco Design 选择器
page.click('.arco-menu-inline-header:has-text("云服务管理")')
page.click('.arco-menu-item:has-text("云服务账单")')
page.waitForSelector('.arco-table')
```

## References

- [完整 API 端点参考](references/api-reference.md) — 88 个端点的请求/响应文档
- [A4x 后台管理系统](https://console.addx.live/) — Web 前端界面
- [Arco Design Vue](https://arco.design/) — UI 组件库文档
- 后端 API Base URL: `https://revenus-sharing-backend.addx.live`
