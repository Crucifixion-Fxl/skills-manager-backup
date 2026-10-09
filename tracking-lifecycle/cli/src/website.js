'use strict';

const { ContractError } = require('./contracts');
const { reviewedHttpsEndpoint } = require('./endpoint');
const { operations, sourceCommit } = require('./platform-operations');
const { loopbackEndpoint } = require('./browser-transport');
const { webAccessRuntimeModule } = require('./web-access-runtime');
const { runOpenCLI } = webAccessRuntimeModule('browser.js');

const frontendCommit = 'b6bf12a13bb809f7569a714a67d245cc8dd24bd4';
const pages = [
  ['home', '/', '首页'], ['login', '/auth/login/', '飞书登录'],
  ['events', '/spm/list/', '应用、事件树、搜索、详情、标签'],
  ['event-edit', '/spm/edit/', '点位编辑'], ['event-import', '/spm/import/', '飞书表格导入'],
  ['event-board', '/spm/databoard/', 'SPM 看板'],
  ['bases', '/schema/list/', '公共 Base 参数'], ['base-edit', '/schema/edit/', 'Base 编辑'],
  ['contexts', '/context/list/', 'Context 列表'], ['context-edit', '/context/edit/', 'Context 编辑'],
  ['validation', '/check/dashboard/', '埋点验证'], ['app-validation', '/check/app_check/', '应用埋点验证'],
  ['workorders', '/workOrder/list/', '工单、版本、差异、审核、发布、废弃'],
  ['workorder-edit', '/workOrder/edit/', '工单编辑'],
  ['personal-tokens', '/access-token/', '个人凭据及管理员吊销'],
  ['project-tokens', '/project-token/', 'Project Token'],
  ['roles', '/role-management/', '用户角色'], ['app-owners', '/role-management/app-owner/', '应用 Owner'],
  ['approvals', '/approval-management/', '审批管理'],
  ['register', '/auth/register/', '注册模板'], ['settings', '/settings/', '设置模板'],
].map(([id, path, description]) => ({ id, path, description,
  status: ['register', 'settings'].includes(id) ? 'FRONTEND_TEMPLATE' : 'BROWSER_ENTRY' }));

const gaps = [
  ['GET', '/api/release/diff', '版本差异', '可读取当前/上一版事件，在本地比较；此路由未实现'],
  ['POST', '/api/info/classification/save', '分类保存', '需要平台实现分类持久化'],
  ['DELETE', '/api/info/classification/delete', '分类删除', '需要平台实现分类持久化'],
  ['GET', '/api/info/classification/list', '分类列表', '需要平台实现分类持久化'],
  ['GET', '/api/kanban/getSpmInfoById', 'SPM 看板详情', '可通过事件详情和完整事件树读取；此路由未实现'],
  ['GET', '/api/kanban/getChildrenSpmInfoById', 'SPM 看板子节点', '可从完整事件树派生；此路由未实现'],
  ['GET', '/api/kanban/getSourceSpmInfoById', 'SPM 看板来源', '平台缺少来源查询实现，不推测来源关系'],
  ['POST', '/api/spm/importConfig', '旧 SPM 配置导入', '使用已实现的事件契约 post；旧配置路由未实现'],
].map(([method, path, feature, recovery]) => ({ method, path, feature, recovery, status: 'BACKEND_ROUTE_MISSING' }));

function coverage() {
  return { status: 'SOURCE_COVERAGE_ONLY', backendCommit: sourceCommit, frontendCommit,
    note: '注册命令与源码覆盖不代表线上逐项验收；浏览器打开编辑页也不代表完成写入。',
    summary: { platformOperations: operations.length, frontendPages: pages.length, missingBackendRoutes: gaps.length },
    operations: operations.map(op => ({ ...op, status: op.workflow ? 'CONTRACT_WORKFLOW' :
      op.auth === 'mcp' ? 'MCP_KEY_ONLY' : op.effect === 'authentication' ? 'OFFICIAL_LOGIN_WORKFLOW' :
      op.secretResponse ? 'LOCAL_PRIVATE_OUTPUT' : 'API_COMMAND',
      browser: op.auth !== 'mcp' && op.effect === 'read',
      bridge: op.auth !== 'mcp' && op.effect === 'read' && !op.secretResponse })),
    bridgeScope: 'identity and explicit App binding; unproven scope is refused. Writes require contract workflows.',
    pages, gaps,
    separateServices: [
      { feature: 'Micro good/bad', command: 'verify-sandbox', status: 'SEPARATE_ORIGIN_NO_PLATFORM_CREDENTIALS' },
      { feature: 'Micro all', status: 'BROWSER_VALIDATION_PAGE' },
      { feature: 'Micro global reset', status: 'BLOCKED_GLOBAL_SIDE_EFFECT', recovery: '使用本次唯一 namespace 隔离验证' },
      { feature: '独立 Context schema 文件上传', status: 'EXTERNAL_ARTIFACT_WORKFLOW', recovery: 'resolver 上传不在这 76 个平台路由中' },
    ],
    remoteLogin: { supported: ['project-token', 'pat', 'local-browser-over-ssh-reverse-tunnel'],
      deviceAuth: 'IDENTITY_PROVIDER_TOKEN_IS_NOT_A_PLATFORM_SESSION',
      noBackendChangeRequiredForBridge: true } };
}

async function openPage(args, env = process.env) {
  const selected = pages.find(page => page.id === args.page);
  if (!selected) throw new ContractError('UNKNOWN_PAGE', 'choose a page from site-coverage');
  const origin = reviewedHttpsEndpoint(env.TRACKING_PLATFORM_BASE_URL);
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(args.session || '')) throw new ContractError('BROWSER_SESSION_REQUIRED', '--session is required');
  if (env.OPENCLI_CDP_ENDPOINT) {
    // CDP tab identity is caller-owned; opening a page should not overwrite it.
    loopbackEndpoint(env.OPENCLI_CDP_ENDPOINT);
    throw new ContractError('BROWSER_BRIDGE_REQUIRED', 'site-open uses an isolated OpenCLI extension session; for CDP, open a dedicated tab then select it explicitly');
  }
  if (!args.profile || !args.tab) throw new ContractError('BROWSER_TARGET_REQUIRED', 'explicit profile and dedicated tab are required');
  await runOpenCLI(['--profile', args.profile, 'browser', args.session, 'open', origin + selected.path, '--tab', args.tab], { env });
  return { status: 'PAGE_OPENED', page: selected.id, url: origin + selected.path, session: args.session, effect: 'navigation-only', sourceStatus: selected.status };
}

module.exports = { frontendCommit, pages, gaps, coverage, openPage };
