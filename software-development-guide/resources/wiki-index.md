# 飞书 Wiki 索引（A4x Wiki - 产研知识库）

> Agent 用法：用户问"上线流程 / 代码规范 / SQL 审核 / 物模型 / IoT 灰度发布 / 增值业务流程" 等运营 SOP 时，先告诉用户对应飞书 wiki 链接（agent 直接抓飞书内容受 SSO 限制），让用户自己看；同时把本文件已抽取的关键 SOP 摘要给出来。
>
> 这些 wiki 文档是飞书云文档，**需要登录态**才能完整看。Agent 可读到的只是公开摘要 + 本文件已经抽取的部分。

---

## A4x Wiki 顶部目录

来源：https://a4x-paas.feishu.cn/wiki/R9wiwomPyif9PKkbpp1cMYNRnK1（"上线规范"页面侧边栏）

```
A4x Wiki - 产研知识库
├── 技术文档
├── 上线规范
│   ├── 上线日志模版
│   ├── 上线日志
│   ├── 当前问题及优化点
│   ├── 2024 春节前 后端上线安排
│   ├── 2025 春节前 后端上线安排
│   ├── 上线问题复盘
│   ├── SQL 审核 SOP
│   └── a4x 高可用
├── 研发流程
├── 代码规范
├── 技术方案
├── 技术分享
├── 前端基础框架使用及开发说明文档
├── 第三方技术调研
├── api
├── Open-API 控制 VIP
├── 业务逻辑
│   ├── 线上 bug 类问题定位
│   ├── 4g 设备后端改动
│   ├── 常见问题整理
│   ├── 喂鸟器差异化功能技术方案
│   ├── KF126 服务端技术方案（增值除外）
│   └── linode 技术方案
├── BX 自动化工具
├── 日志 log trace id 使用指南
├── MeterSphere api 版本管理
├── 技术预研
├── logo 水印技术方案（后端）
├── 设备设置、绑定和 OTA
├── 物模型
├── 增值业务研发流程
├── 状态机
└── IoT 服务灰度发布完整技术方案
```

---

## 核心 wiki 直链

### 流程 / 规范

| 主题 | 飞书链接 |
|------|---------|
| **上线规范**（含 12 步 SOP）| https://a4x-paas.feishu.cn/wiki/R9wiwomPyif9PKkbpp1cMYNRnK1 |
| **APP 版本值班计划 + 提审流程** | https://a4x-paas.feishu.cn/wiki/E5b8wJbRJiRMe3k9I9mcWRFKnxh |
| **5 月份周报**（每周更新）| https://a4x-paas.feishu.cn/wiki/II6ywUqggiWU5DkGilzcpUapn3f |
| 5 月需求和资源 | https://a4x-paas.feishu.cn/wiki/F25ywb6ajiQEoDkelkUc2MrvnSf |
| 增值业务规划 | https://mg5nag3zsf.feishu.cn/docx/Bm9YdpiL0oWZrxx4qTeclH0onIb |

### 业务/技术专题

| 主题 | 路径 |
|------|------|
| 增值业务研发流程 | A4x Wiki → 增值业务研发流程 |
| 物模型 | A4x Wiki → 物模型 |
| IoT 服务灰度发布完整技术方案 | A4x Wiki → IoT 服务灰度发布完整技术方案 |
| 状态机 | A4x Wiki → 状态机 |
| KF126 服务端技术方案（增值除外）| A4x Wiki → 业务逻辑 → KF126 服务端技术方案 |
| 喂鸟器差异化功能技术方案 | A4x Wiki → 业务逻辑 → 喂鸟器差异化功能技术方案 |
| 4G 设备后端改动 | A4x Wiki → 业务逻辑 → 4g 设备后端改动 |
| 设备设置、绑定和 OTA | A4x Wiki → 设备设置、绑定和 OTA |
| 日志 log trace id 使用指南 | A4x Wiki → 日志 log trace id 使用指南 |
| logo 水印技术方案（后端）| A4x Wiki → logo 水印技术方案 |
| Open-API 控制 VIP | A4x Wiki → Open-API 控制 VIP |
| 前端基础框架使用及开发说明 | A4x Wiki → 前端基础框架使用及开发说明文档 |

### 内部 GitLab 文档（无需飞书登录）

| 主题 | GitLab 链接 |
|------|------------|
| **每月需求和资源 monthly plan** | https://gitlab.addx.ai/weekly-reports/software/-/blob/main/2026-05-monthly-plan.md（按月递增）|
| **Weekly Report 主仓** | https://gitlab.addx.ai/weekly-reports/software |
| Engineering skills 仓 | https://gitlab.addx.ai/engineering/skills |

#### Weekly Report 文件命名

| 文件 | 用途 |
|------|------|
| `{YYYY-MM}-monthly-plan.md` | 月度需求资源整合（如 `2026-05-monthly-plan.md`）|
| `weekly/{YYYY-WNN}.md` | 单周周报（按团队规范命名）|

#### 周会节奏

详见 `dev-flow.md#周会-weekly-report-节奏`。

#### 工时填报

每周五填工时（部分团队）→ 子 skill `worktime-filing`

---

## Agent 路由策略

新人提问 → 路由表：

| 用户问题模式 | 先答 | 跳到哪 |
|--------------|------|--------|
| "上线流程是什么" | dev-flow.md 12 步 SOP 摘要 | 飞书"上线规范"链接 |
| "App X 谁负责提审" | ownership.md 中"APP 版本值班"段直接答 | 飞书"APP 版本值班计划"链接 |
| "本周上线了什么" | 让用户看飞书周报 | 飞书"5 月份周报"链接 |
| "Y 模块谁负责" | ownership.md 业务域查表 | monthly plan 最新链接 |
| "代码规范" | 各仓 CLAUDE.md + 通用 skill | 飞书"代码规范" |
| "SQL 审核流程" | 引用 NineData SQL 审批 | 飞书"SQL 审核 SOP" |
| "物模型 / 设备类型" | 简介 + 跳飞书 | 飞书"物模型" |
| "IoT 灰度发布" | 简介 + 跳飞书 | 飞书"IoT 服务灰度发布完整技术方案" |
| "线上 bug 定位" | dev-flow.md 排查段 + 跳飞书 | 飞书"线上 bug 类问题定位" |
| "增值业务研发流程" | business-map.md 增值业务段 + 跳飞书 | 飞书"增值业务研发流程" |

---

## Refresh

飞书 wiki 顶部目录会动态变化，每季度手动重抓一次本文件内容。命令：

```bash
# 用 cmux browser pane 抓飞书 wiki（需登录）
cmux browser open "https://a4x-paas.feishu.cn/wiki/R9wiwomPyif9PKkbpp1cMYNRnK1"
sleep 5
cmux browser --surface <id> eval "document.body.innerText"
```
