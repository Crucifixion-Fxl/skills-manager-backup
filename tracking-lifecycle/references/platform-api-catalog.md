# 埋点平台完整 Controller 接口清单

核对日期：2026-10-03。后端 commit `d61e1692c3d28224e4d642f7bdf765a6a982ab0d`；
扫描完整 checkout 的 `**/src/main/java/**/*Controller.java`，纳入全部 13 个 Spring Controller、76 个有效 HTTP method/path。
注释中的旧接口不计入；这是固定源码的路由清单，不是在线 OpenAPI，也不是全部端点已实测或已部署的声明。
未包含网关、Micro、Iglu/S3、MCP Server 本身或飞书第三方 API；这些不是本后端 Controller。
请求/响应仍以 DTO、服务实现和目标环境回读为准，不从方法名猜测权限、JSON 形状或无副作用。

鉴权与读写边界见 [平台鉴权](platform-auth.md)；Base/Context 具体流程见 [公共 Schema API](base-context-api.md)。
Java 签名中 `@RequestBody` 表示 JSON body，`@PathVariable` 表示路径，`@RequestParam` 表示 query/form；
未标注的简单参数和 Param 对象采用 Spring 参数绑定，不能一律改成 JSON。`@Valid` 不等于嵌套字段已逐项校验。

## AdminApiAccessTokenController

来源：[AdminApiAccessTokenController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/AdminApiAccessTokenController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/admin/access-tokens` | [`Result<List<ApiAccessTokenVO>> listAll()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/AdminApiAccessTokenController.java#L29) |
| `POST /api/admin/access-tokens/{id}/revoke` | [`Result<Boolean> revoke(@PathVariable("id") Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/AdminApiAccessTokenController.java#L35) |

## ApiAccessTokenController

来源：[ApiAccessTokenController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApiAccessTokenController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `POST /api/access-tokens` | [`Result<ApiAccessTokenCreateVO> create(@Valid @RequestBody ApiAccessTokenCreateParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApiAccessTokenController.java#L36) |
| `GET /api/access-tokens` | [`Result<List<ApiAccessTokenVO>> list()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApiAccessTokenController.java#L47) |
| `POST /api/access-tokens/{id}/status` | [`Result<Boolean> updateStatus(@PathVariable("id") Long id, @RequestBody Map<String, Object> body)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApiAccessTokenController.java#L53) |
| `POST /api/access-tokens/{id}/delete` | [`Result<Boolean> delete(@PathVariable("id") Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApiAccessTokenController.java#L65) |

## ApprovalController

来源：[ApprovalController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApprovalController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/approval/list` | [`Result list()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApprovalController.java#L40) |
| `POST /api/approval/updateStatus` | [`Result<Void> updateStatus(@RequestParam("id") Long id, @RequestParam("status") String status)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApprovalController.java#L47) |
| `POST /api/approval/syncStatus` | [`Result<Void> syncStatus(@RequestParam("id") Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ApprovalController.java#L55) |

## BaseSchemaController

来源：[BaseSchemaController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/BaseSchemaController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/baseSchema/list` | [`Result getAllBaseSchemas(@ApiParam(name = "应用id") Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/BaseSchemaController.java#L26) |
| `DELETE /api/baseSchema/delete` | [`Result deleteBaseSchemasById(@Valid BaseSchemaDeleteParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/BaseSchemaController.java#L32) |
| `POST /api/baseSchema/add` | [`Result saveBaseSchemas(@Valid @RequestBody BaseSchemaParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/BaseSchemaController.java#L39) |
| `POST /api/baseSchema/update` | [`Result updateBaseSchemas(@Valid @RequestBody BaseSchemaParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/BaseSchemaController.java#L46) |
| `GET /api/baseSchema/detail` | [`Result getDetailByBaseSchemaId(@ApiParam(name = "基础schema id") Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/BaseSchemaController.java#L53) |

## ContextController

来源：[ContextController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ContextController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/context/list` | [`Result list(@ApiParam(name = "应用id") Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ContextController.java#L21) |
| `POST /api/context/save` | [`Result save(@Valid @RequestBody ContextParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ContextController.java#L27) |
| `GET /api/context/detail` | [`Result detail(@ApiParam(name = "context id") Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ContextController.java#L34) |
| `GET /api/context/schema` | [`Result contextSchema(@Valid Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ContextController.java#L40) |

## EventTagController

来源：[EventTagController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/EventTagController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/eventTag/list` | [`Result<List<EventTag>> getTagList(@RequestParam("applicationId") Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/EventTagController.java#L30) |
| `POST /api/eventTag/add` | [`Result<Long> addTag(@RequestBody AddTagParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/EventTagController.java#L42) |
| `DELETE /api/eventTag/cleanUnused` | [`Result<Integer> cleanUnusedTags(@RequestParam("applicationId") Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/EventTagController.java#L54) |

## LoginController

来源：[LoginController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/LoginController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/user/feishuLogin` | [`void feishuLogin(String requestUrl, String code, String state, HttpServletResponse response)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/LoginController.java#L27) |
| `GET /api/user/loginRedirect` | [`Result loginRedirect(String referUrl)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/LoginController.java#L49) |
| `GET /api/user/getCurrentUser` | [`Result getCurrentUser()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/LoginController.java#L56) |
| `GET /api/user/logout` | [`Result logout()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/LoginController.java#L63) |

## McpProxyController

来源：[McpProxyController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/mcp/applications` | [`Result<List<?>> listApplications()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L34) |
| `POST /api/mcp/applications` | [`Result<Long> createOrUpdateApplication(@Valid @RequestBody EventInfoParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L39) |
| `GET /api/mcp/applications/{id}/pages` | [`Result<List<?>> listPages(@PathVariable("id") Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L46) |
| `GET /api/mcp/applications/{id}/modules` | [`Result<List<?>> listModules(@PathVariable("id") Long appId, @RequestParam("pageId") Long pageId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L51) |
| `GET /api/mcp/applications/{id}/tags` | [`Result<List<?>> listTags(@PathVariable("id") Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L57) |
| `POST /api/mcp/tags` | [`Result<Long> createTag(@Valid @RequestBody EventTagController.AddTagParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L62) |
| `GET /api/mcp/events/search` | [`Result<List<?>> searchEvents(@Valid EventInfoQueryParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L67) |
| `GET /api/mcp/events/{id}` | [`Result<?> getEvent(@PathVariable("id") Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L72) |
| `POST /api/mcp/events` | [`Result<Long> createOrUpdateEvent(@Valid @RequestBody EventInfoParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L77) |
| `DELETE /api/mcp/events/{id}` | [`Result<Void> deleteEvent(@PathVariable("id") Long id, @RequestParam(value = "forceDeleteFlag", defaultValue = "0") Integer forceDeleteFlag)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L82) |
| `GET /api/mcp/releases` | [`Result<List<?>> listReleases(@Valid ReleaseQueryParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L92) |
| `POST /api/mcp/releases` | [`Result<Void> createRelease(@Valid @RequestBody ReleaseAddParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/McpProxyController.java#L97) |

## ProjectAccessTokenController

来源：[ProjectAccessTokenController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ProjectAccessTokenController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `POST /api/project-tokens` | [`Result<ProjectAccessTokenCreateVO> create(@Valid @RequestBody ProjectAccessTokenCreateParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ProjectAccessTokenController.java#L36) |
| `GET /api/project-tokens` | [`Result<List<ProjectAccessTokenVO>> list()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ProjectAccessTokenController.java#L46) |
| `POST /api/project-tokens/{id}/status` | [`Result<Boolean> updateStatus(@PathVariable("id") Long id, @RequestBody Map<String, Object> body)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ProjectAccessTokenController.java#L52) |
| `POST /api/project-tokens/{id}/delete` | [`Result<Boolean> delete(@PathVariable("id") Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ProjectAccessTokenController.java#L64) |

## ReleaseController

来源：[ReleaseController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/release/getAllRelease` | [`Result getAllRelease(ReleaseQueryParam releaseQueryParam)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L34) |
| `POST /api/release/addRelease` | [`Result addRelease(@Valid @RequestBody ReleaseAddParam releaseAddParam)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L40) |
| `POST /api/release/onlineRelease` | [`Result onlineRelease(@Valid @RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L47) |
| `POST /api/release/onlineTestRelease` | [`Result onlineTestRelease(@RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L60) |
| `POST /api/release/getReleaseEvents` | [`Result getReleaseEvents(@RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L67) |
| `POST /api/release/getPreviousReleaseEvents` | [`Result getPreviousReleaseEvents(@RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L73) |
| `GET /api/release/getReleaseStatus` | [`Result getReleaseStatus(ReleaseStatusCheckParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L79) |
| `GET /api/release/getPublishedEvents` | [`Result getPublishedEvents(ReleaseStatusCheckParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L85) |
| `GET /api/release/getReleaseVersionList` | [`Result getReleaseNameList(ReleaseStatusCheckParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L91) |
| `POST /api/release/getUnpassedEvents` | [`Result getUnpassedEvents(@RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L97) |
| `POST /api/release/validate` | [`Result validate(@RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L103) |
| `POST /api/release/createApproval` | [`Result<Void> createApproval(@RequestParam("releaseId") Long releaseId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L112) |
| `POST /api/release/deleteRelease` | [`Result<Void> deleteRelease(@RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L122) |
| `POST /api/release/checkS3Files` | [`Result checkS3Files(@RequestBody ReleaseOnlineParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/ReleaseController.java#L128) |

## TrackerInfoController

来源：[TrackerInfoController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/info/getAllApplication` | [`Result getAllApplication()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L34) |
| `POST /api/info/saveOrUpdateApplicationInfo` | [`Result saveOrUpdateApplicationInfo(@Valid @RequestBody EventInfoParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L40) |
| `GET /api/info/getAllPageByApplicationId` | [`Result getAllPageByApplicationId(Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L47) |
| `GET /api/info/getAllModuleByPageId` | [`Result getAllModuleByPageId(Long pageId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L53) |
| `GET /api/info/getAllSelfDefine` | [`Result getAllSelfDefine(Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L59) |
| `GET /api/info/getParametersByEventId` | [`Result getParametersByEventId(Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L65) |
| `GET /api/info/search` | [`Result search(@Valid EventInfoQueryParam searchParam)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L71) |
| `DELETE /api/info/deleteEventDetail` | [`Result deleteDetail(@RequestParam("id") @NotNull(message = "id不能为空") Long id, @RequestParam("forceDeleteFlag") @NotNull(message = "强制删除标识不能为空") Integer forceDeleteFlag)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L77) |
| `GET /api/info/getEventDetail` | [`Result getEventDetail(Long eventId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L88) |
| `POST /api/info/saveOrUpdateEventInfo` | [`Result saveOrUpdateEventInfo(@Valid @RequestBody EventInfoParam eventInfoParam)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L94) |
| `POST /api/info/batchCreateEvents` | [`Result<BatchEventResult> batchCreateEvents(@Valid @RequestBody BatchEventParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L101) |
| `GET /api/info/getEventSchema` | [`Result getEventSchema(@Valid Long id)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L108) |
| `POST /api/info/importFromFeishuExcel` | [`Result startImport(@Valid @RequestBody EventInfoImportParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L113) |
| `GET /api/info/getImportHistory` | [`Result getImportHistory(@Valid EventInfoImportHistoryParam param)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/TrackerInfoController.java#L119) |

## UserNamespaceController

来源：[UserNamespaceController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserNamespaceController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/namespace/getUserNamespace` | [`Result<String> getUserNamespace()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserNamespaceController.java#L27) |

## UserRoleController

来源：[UserRoleController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserRoleController.java)。

| 方法与路径 | 源码方法 / 请求绑定 |
|---|---|
| `GET /api/role/current` | [`Result<UserRoleVO> getCurrentUserRole()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserRoleController.java#L39) |
| `GET /api/role/all` | [`Result<List<UserRoleVO>> getAllUserRoles()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserRoleController.java#L67) |
| `POST /api/role/update` | [`Result<Boolean> updateUserRole(@RequestBody Map<String, Object> params)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserRoleController.java#L99) |
| `POST /api/role/setAppOwner` | [`Result<Boolean> setAppOwner(@RequestBody Map<String, Object> params)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserRoleController.java#L126) |
| `GET /api/role/isAppOwner` | [`Result<Boolean> isAppOwner(@RequestParam("applicationId") Long applicationId)`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserRoleController.java#L154) |
| `GET /api/role/isAdmin` | [`Result<Boolean> isAdmin()`](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/UserRoleController.java#L169) |

## Controller 请求模型字段

以下是上述签名直接引用的 Param DTO 的字段清单，便于定位请求。类型和初始化值取自源码；
字段标注不替代服务层业务规则、跨应用归属检查或完整 JSON Schema。

### AddTagParam

嵌套类：[EventTagController](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/controller/EventTagController.java)。完整定义见源码。

### ApiAccessTokenCreateParam

来源：[ApiAccessTokenCreateParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ApiAccessTokenCreateParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `name` | `String` | `—` |
| `expiryDays` | `Integer` | `—` |

### BaseSchemaDeleteParam

来源：[BaseSchemaDeleteParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/BaseSchemaDeleteParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `parentApplicationId` | `Long` | `—` |
| `id` | `Long` | `—` |

### BaseSchemaParam

来源：[BaseSchemaParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/BaseSchemaParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `applicationId` | `Long` | `—` |
| `id` | `Long` | `—` |
| `name` | `String` | `—` |
| `description` | `String` | `—` |
| `parameters` | `List<EventParameterParam>` | `—` |

### BatchEventParam

来源：[BatchEventParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/BatchEventParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `applicationId` | `Long` | `—` |
| `events` | `List<BatchEventItemParam>` | `—` |

### ContextParam

来源：[ContextParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ContextParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `applicationId` | `Long` | `—` |
| `id` | `Long` | `—` |
| `name` | `String` | `—` |
| `schemaUrl` | `String` | `—` |
| `description` | `String` | `—` |
| `parameters` | `List<EventParameterParam>` | `—` |

### EventInfoImportHistoryParam

来源：[EventInfoImportHistoryParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/EventInfoImportHistoryParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `workOrderId` | `Long` | `—` |
| `applicationId` | `Long` | `—` |

### EventInfoImportParam

来源：[EventInfoImportParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/EventInfoImportParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `tableUrl` | `String` | `—` |
| `id` | `Long` | `—` |

### EventInfoParam

来源：[EventInfoParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/EventInfoParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `id` | `Long` | `—` |
| `parentApplicationId` | `Long` | `—` |
| `parentPageId` | `Long` | `—` |
| `parentModuleId` | `Long` | `—` |
| `type` | `Integer` | `—` |
| `name` | `String` | `—` |
| `description` | `String` | `—` |
| `point` | `String` | `—` |
| `alias` | `String` | `—` |
| `category` | `String` | `—` |
| `baseSchemas` | `List<Long>` | `—` |
| `parameters` | `List<EventParameterParam>` | `—` |

### EventInfoQueryParam

来源：[EventInfoQueryParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/EventInfoQueryParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `pageNum` | `Integer` | `1` |
| `pageSize` | `Integer` | `10` |
| `name` | `String` | `—` |
| `applicationId` | `Long` | `—` |
| `pageId` | `Long` | `—` |
| `category` | `String` | `—` |

### ProjectAccessTokenCreateParam

来源：[ProjectAccessTokenCreateParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ProjectAccessTokenCreateParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `name` | `String` | `—` |
| `appId` | `Long` | `—` |
| `expiryDays` | `Integer` | `—` |

### ReleaseAddParam

来源：[ReleaseAddParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ReleaseAddParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `name` | `String` | `—` |
| `version` | `String` | `—` |
| `applicationId` | `Long` | `—` |

### ReleaseOnlineParam

来源：[ReleaseOnlineParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ReleaseOnlineParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `id` | `Long` | `—` |
| `applicationId` | `Long` | `—` |

### ReleaseQueryParam

来源：[ReleaseQueryParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ReleaseQueryParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `pageNum` | `Integer` | `1` |
| `pageSize` | `Integer` | `10` |
| `applicationId` | `Long` | `—` |
| `releaseStatus` | `Integer` | `—` |
| `releaseValidateStatus` | `Integer` | `—` |
| `releaseStartTime` | `Date` | `—` |

### ReleaseStatusCheckParam

来源：[ReleaseStatusCheckParam](https://gitlab.addx.ai/TRAC/tracker-management/-/blob/d61e1692c3d28224e4d642f7bdf765a6a982ab0d/tracker_management/src/main/java/tracker/management/param/ReleaseStatusCheckParam.java)。

| 字段 | Java 类型 | 默认值 |
|---|---|---|
| `application` | `String` | `—` |
| `version` | `String` | `—` |

## 独立核对边界

- 清单有路由，不表示当前 Skill 为每个路由提供 CLI 命令。当前 `PlatformClient` 的专用方法仅覆盖树/事件/工单。
- 未执行登录、登出、权限更改、Token 创建/吊销、删除、审批或发布；GET 也可能有登录/登出副作用。
- PAT/Project Token 创建必须使用交互式账号 Session；已有 Token 不能再签发新 Token。
- `/api/mcp/**` 使用独立 `X-MCP-API-Key`，不接受 PAT/Project Token 代替。不要将该入口当作绕过应用权限的办法。
- 服务层仍有权限、全量覆盖、已发布不可变和产物回读规则；见 [主 API reference](api-reference.md)。

## 前端能力对照与边界

固定前端 `b6bf12a13bb809f7569a714a67d245cc8dd24bd4` 的 64 个平台请求调用位置中，53 个匹配上述固定后端路由；其余 11 个调用位置涉及以下 8 个不同路由。此处是源码比对，不代表逐项线上执行成功。

| 前端请求 | 固定后端情况 | 前端来源 |
|---|---|---|
| `GET /api/release/diff` | 无有效路由 | `src/services/workOrder.js:42` |
| `POST /api/info/classification/save` | 无有效路由 | `src/services/classification.js:5`、`tracker.js:53` |
| `DELETE /api/info/classification/delete` | 无有效路由 | `src/services/classification.js:10`、`tracker.js:43` |
| `GET /api/info/classification/list` | 无有效路由 | `src/services/classification.js:14`、`tracker.js:48` |
| `GET /api/kanban/getSpmInfoById` | 无有效路由 | `src/services/kanban.js:4` |
| `GET /api/kanban/getChildrenSpmInfoById` | 无有效路由 | `src/services/kanban.js:8` |
| `GET /api/kanban/getSourceSpmInfoById` | 无有效路由 | `src/services/kanban.js:12` |
| `POST /api/spm/importConfig` | 无有效路由 | `src/services/spm.js:4` |

Micro good/bad/all/reset 属于另一服务，不能携带平台凭据；Context 保存仅登记元数据及参数，独立 schema 文件上传不在这 76 个 Controller 路由中。前端入口存在也不证明对应能力可用，缺口不得改写成通用任意 URL 请求或假造 JWT/文件上传。
