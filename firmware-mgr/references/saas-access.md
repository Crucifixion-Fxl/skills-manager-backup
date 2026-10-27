# firmware-mgr：认证与源码入口

认证正本：[SKILL.md Rule 1](../SKILL.md)；证据：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码版本：[source-discovery.json](source-discovery.json)。

优先复用环境对应的 `FIRMWARE_MGR_TOKEN` / `FIRMWARE_MGR_TEST_TOKEN`。没有可用 token 时，从 `FIRMWARE_MGR_USERNAME` / `FIRMWARE_MGR_PASSWORD`（测试使用 `FIRMWARE_MGR_TEST_USERNAME` / `FIRMWARE_MGR_TEST_PASSWORD`）读取平台账号密码，调用 `POST /api/login`，JSON 字段为 `userName`、`password`，成功读取 `data.token`。生产、测试凭据不交叉使用。

[只读查询脚本](../scripts/query.py) 在同一进程内执行登录、`get_models` 探针和目标查询。token 只在内存使用；curl 通过 stdin 接收凭据，不在 argv、stdout、文件或日志暴露。缺少凭据时让用户在宿主环境安全配置，不索要明文到聊天，不默认去浏览器复制 Cookie。

所有业务请求携带 `token` Header（不是 Bearer，Cookie 不能代替），POST JSON 对象。探针失败最多尝试一次账号登录；登录失败或新 token 不可用时停止。探针成功后的目标接口拒绝可能是权限不足或 token 随后失效，不循环登录。`get_user_info` 不是探针，稳定 subject 与 token 生命周期仍待核验。

源码 `DEV/firmware-mgr-server@ac2c999b5e68540330d9c8f2544697636a2cf6c0` 的 `api.py:769–782`、`user.py:23–80` 与 2026-10-15 生产前端登录契约一致；空 JSON 探针确认路由存在。未提供真实凭据，未完成生产账号登录；测试实例部署匹配未知。因此 auth-profile 仍为 delegated、runtimeVerification=pending，不把离线测试升级成线上验收。

浏览器仅作交互登录要求或明确 UI 请求的备选，按 [web-access](../../../agent-harness/web-access/SKILL.md) 操作。固件发布、撤销、白名单和权限修改仍遵守原 Skill 的授权与执行后回读规则，本次不改变业务写入门禁。
