# OpenCLI 与全站覆盖

## 日常入口

认证与 scope 使用 [web-access](../../../web-access/SKILL.md)，本机 Companion 的 Cookie 不导出；开发机设置 `TRACKING_BRIDGE_CREDENTIALS_FILE` 后用 `--auth=bridge`。纯 REST 可继续用已批准的 PAT/Project Token。Project Token GET-only；某些读业务使用 POST，不能因为“查询”就绕过此限制。

```bash
# Skill 已有 npm ci；另在 web-access/runtime 安装 npm 依赖。
node cli/bin/events-tdd.js opencli-install
opencli tracking site-coverage
opencli tracking context-list --auth bridge --request context-query.json
```

从 npm 安装的 OpenCLI 使用公开 registry/errors API 加载生成 adapter。`opencli-install` 默认写入个人 `.opencli/clis/tracking/tracking.js`；已有不同内容需要显式 `--replace=true`，不会默默覆盖。自定义 `--root` 用于产物/测试，不会自动注册为 OpenCLI 的个人发现路径。Skill 移动后重装 adapter。

76 路由 alias、14 lifecycle 命令和 5 个辅助入口仍由现有 CLI 实施。通用参数：`--request`、`--auth`、`--session`、`--profile`、`--tab`、`--page`、本地私有响应文件 `--private-output`。复杂 lifecycle 参数可写成 `--args-file` JSON 字符串数组，如 `["--baseline=events/baseline.yaml","--change=events/changes/demo.yaml"]`；不放凭据。95 个命令注册成功不是95个功能已上线验收。

`site-open` 是明确 profile/tab 的导航入口，不完成编辑/审批。CDP 模式不重定向用户已有 tab；先由用户打开本任务独立 tab 再选定。`--auth=browser` 未发布原型入口已收敛为 Companion + `--auth=bridge`，以统一 subject 与 App 核验。

## 覆盖与证据

固定后台 SHA `d61e1692c3d28224e4d642f7bdf765a6a982ab0d` 和前台 SHA `b6bf12a13bb809f7569a714a67d245cc8dd24bd4`。`site-coverage` 返回 76 后端路由、21 前端路由、8 个前端引用但后台不存在的路由，以及独立 Micro/Iglu 服务边界。Controller 参数与权限正本是 [platform-api-catalog.md](platform-api-catalog.md)；前端模板不是已实现功能。

缺失路由包括 release diff、classification save/delete/list、3 个 kanban 查询、旧 importConfig。可以从已实现的事件树派生部分只读结果，不能假装所有缺口都有可用 API。MCP key、私有 PAT 创建响应、OAuth 跳转、生产发布有各自入口；生产发布仅受保护 CI。App 绑定代理不允许无法核实 App 归属的原始更新/删除；写入走 `post`/`create-workorder` 契约门禁，管理操作需专用 scope adapter 或已明确授权的原生凭据。

行为 TDD：`tests/browser-bridge.test.js`（身份、App、TTL、权限、业务门禁），`tests/opencli-adapter.test.js`（真实公开 registry 注册/调用、生产门禁、私有参数），`tests/browser-fixture.test.js`（真实隔离 Chrome+HTTPS+HttpOnly Session）。浏览器 fixture 用 `SAAS_BROWSER_FIXTURE=1 node --test tests/browser-fixture.test.js`，依赖本机 Chrome 与 openssl；普通单测显式跳过该环境用例。

状态：源码覆盖 `source-verified`；公共适配器/认证/browser 机制为 `fixture-verified`；Mac→SSH→noVNC 人工登录与 device-cloud-host 的 7 项真实 OpenCLI 只读验收通过，见 [线上回执](live-device-host-acceptance.json)；本次一次性 profile、凭据及自有画面服务已清除。Companion 本机→开发机的跨机器反向认证转发、角色与所有功能/写入逐项验收仍为 `pending`。新增菜单、后台 SHA/接口、认证或角色权限变化时使用 [platform-onboarding](../../../platform-onboarding/SKILL.md)，重新验证受影响功能并更新本 reference 和覆盖回执。

Bridge 认证/权限/范围/到期/撤销拒绝码原样保留为安全公开 code，未知错误仍脱敏；扩展连接必须显式 `--profile` 与 `--tab`。`bridge-serve --private-output` 使用绝对私有路径，到期或退出清理凭据文件。
