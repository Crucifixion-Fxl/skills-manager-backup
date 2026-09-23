// 飞书登录的无头浏览器会话：headless Chromium + 持久 profile，人只需用手机飞书扫一次码，登录态留在 profile 里。
// 用法：node remote_login.js <起始 URL>     （工作目录必须是自己的 0700 目录；用文件和它通信）
//   --help              打印本说明
// 两个阶段：
//   1) 等登录：页面还在 accounts.feishu.cn（只认这个主机，不是 URL 里含这串字）。
//      shoot.txt  → 刷新登录二维码并截图到 qr.png（刷新会作废旧码；页面显示「确认登录」= 已扫待手机确认时不刷新）
//      quit.txt   → 退出（本阶段也生效）
//      页面跳到 http(s) 且主机不是 accounts.feishu.cn → 登录成功：state.txt 写 LOGGED_IN，进入第 2 阶段并写 ready.txt。
//      about:blank、错误页不算登录。等太久（默认 30 分钟，REMOTE_LOGIN_LOGIN_TIMEOUT 秒可改）就放弃，退出码 3，不写 ready.txt。
//   2) 命令模式（有 ready.txt，最长 60 分钟）：
//      cmd.txt    → JSON {goto, fill:[[selector,value]], click:[selector], shoot:true, wait:ms}；每条命令开始前清掉旧的
//                   result.txt，结果写 result.txt（OK <URL> 或 ERR <原因>），页面快照写 page.txt/page.html/page.png。
//                   坏 JSON 只算这一步失败，会话继续。shoot:true 在这里同样可用（页面又回到扫码页时，例如 SSO 会话超时重发起）。
//      quit.txt   → 退出
// 两个阶段都会每约 1.2 秒写 url.txt 和 page.txt（有命令在执行或 wait 时会停一会儿）。
// 写到磁盘的 URL 只有「主机 + 路径」，不含 query 和 fragment（OAuth 回调的一次性 code、隐式流的 token 都在那里）；
//   错误信息里的 URL 同样去掉 query。page.txt / page.html / page.png 是已登录页面的内容，退出时自动清理。
// 启动时先清掉上一个（可能被 SIGKILL 的）会话留下的所有文件，避免它们被当成新会话的状态。
// 退出码：0 正常（quit.txt / 命令模式结束）；1 未捕获的错误（原因在 error.txt）；2 工作目录不私有 / profile 不可用 / 目录里已有会话；
//   3 等登录超时；129/130/143 收到 SIGHUP/SIGINT/SIGTERM。
// 一个工作目录只能有一个会话：启动时原子地认领 run.pid（硬链接一个写好 pid 的文件；过期的锁在独占的 takeover 锁下移除），
//   run.pid 指着还活着的 remote_login 进程时，新启动的直接退出码 2，什么都不动；多个同时启动也只有一个成功。
// url.txt / state.txt / ready.txt / result.txt / page.txt 先写临时文件再改名，读的一方不会读到空文件或写了一半的内容。
// 环境变量：
//   REMOTE_LOGIN_PROFILE   Chromium 持久 profile 目录，默认 ~/.cache/feishu-agent-browser；不存在则以 0700 创建，已存在会收紧到 0700
//                          （里面就是 owner 的飞书登录态，见 SKILL.md 的安全规则）
//   CHROMIUM_PATH          Chromium 可执行文件，默认取 ~/.cache/ms-playwright/chromium-*/ 里最新的（Linux 布局）
//   REMOTE_LOGIN_SANDBOX   设为 1 才启用 Chromium 沙箱。默认加 --no-sandbox，因为禁用非特权用户命名空间的主机上沙箱起不来；
//                          代价是渲染进程被攻破就等于以当前用户身份执行，所以别用它打开不可信页面
//   REMOTE_LOGIN_LOGIN_TIMEOUT  等登录的秒数，默认 1800
//   NODE_PATH              需要能解析到 playwright-core
// 前置：playwright-core 和一份 Chromium（npx playwright-core install chromium）。
if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.log('usage: node remote_login.js <start-url>   (run in a private 0700 directory; see the header comment or SKILL.md)');
  process.exit(0);
}
const fs = require('fs');
const path = require('path');
const rmQuiet = (f) => { try { fs.rmSync(f, { force: true }); } catch (_) {} };
// The working directory holds page snapshots of a logged-in session and is where commands arrive: it must be private.
{
  const st = fs.statSync('.');
  if ((st.mode & 0o077) !== 0 || st.uid !== process.getuid()) {
    console.error('remote_login: the working directory must be private (0700, owned by you)');
    process.exit(2);
  }
  process.umask(0o077); // everything written from here is 0600, the browser profile 0700
}
// One session per directory, claimed atomically. run.pid is created by hard-linking a file that already holds our pid, so it appears
// complete or not at all (an O_EXCL create followed by a write leaves a moment where a reader sees an empty file); link() fails with
// EEXIST when another session got there first. A live remote_login pid there means refuse and touch nothing. A stale file (dead pid, a
// pid reused by something else, garbage) must be removed, and removal is where naive lock files race: two starters that both judge the
// same file stale would each delete, and the slower one deletes the faster one's fresh lock. So removals are serialised by a second
// exclusive file (run.pid.takeover); its holder re-reads run.pid and removes only the very stale content it examined. A fresh claim
// cannot slip in between that check and the removal, because link() needs run.pid to be absent. The startup cleanup below runs only
// after the claim, so a refused start cannot damage a live session.
const isLiveSession = (pid) => {
  if (!(pid > 0) || pid === process.pid) return false;
  let alive = false;
  try { process.kill(pid, 0); alive = true; } catch (e) { alive = e.code === 'EPERM'; }
  if (!alive) return false;
  let cmdline = '';
  try { cmdline = fs.readFileSync('/proc/' + pid + '/cmdline', 'utf8'); } catch (_) {}
  return !cmdline || cmdline.includes('remote_login');   // no /proc (not Linux): trust the liveness check
};
// -1: no run.pid; 0: there is one but it is not a pid; otherwise the pid in it.
const readPid = () => {
  let text;
  try { text = fs.readFileSync('run.pid', 'utf8'); } catch (e) { return e.code === 'ENOENT' ? -1 : 0; }
  const n = parseInt(text, 10);
  return n > 0 ? n : 0;
};
const sleepMs = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
function claimSession() {
  const mine = 'run.pid.' + process.pid, takeover = 'run.pid.takeover';
  fs.writeFileSync(mine, String(process.pid));
  try {
    for (let attempt = 0; attempt < 300; attempt++) {
      try { fs.linkSync(mine, 'run.pid'); return; } catch (e) { if (e.code !== 'EEXIST') throw e; }
      const other = readPid();
      if (other === -1) continue;   // released between our link and our read: try to claim again
      if (isLiveSession(other)) {
        console.error('remote_login: a session is already running in this directory (pid ' + other + '); stop it first (quit.txt).');
        process.exit(2);
      }
      let lock = -1;
      try { lock = fs.openSync(takeover, 'wx'); } catch (e) {
        if (e.code !== 'EEXIST') throw e;
        try { if (Date.now() - fs.statSync(takeover).mtimeMs > 5000) rmQuiet(takeover); } catch (_) {}  // a holder that died mid-removal
        sleepMs(10);
        continue;
      }
      try {
        const now = readPid();
        if (now === other && !isLiveSession(now)) rmQuiet('run.pid');
      } finally { fs.closeSync(lock); rmQuiet(takeover); }
    }
    console.error('remote_login: could not claim run.pid (other sessions keep starting here); try again');
    process.exit(2);
  } finally { rmQuiet(mine); }
}
claimSession();
// Everything one session leaves in this directory: page snapshots of a logged-in session, the login marker, the command channel.
const SESSION_FILES = ['url.txt', 'page.txt', 'page.html', 'page.png', 'qr.png', 'ready.txt', 'state.txt', 'cmd.txt', 'result.txt'];
// url.txt / state.txt / ready.txt / result.txt / page.txt are polled by other processes: write a temp file and rename it, so a reader
// sees the old or the new content, never an empty or half-written file.
const POLLED = ['url.txt', 'page.txt', 'state.txt', 'ready.txt', 'result.txt'];
const put = (name, text) => { fs.writeFileSync(name + '.tmp', text); fs.renameSync(name + '.tmp', name); };
// A session that was SIGKILLed or lost to an OOM kill cannot clean up. What it left (a state.txt that says LOGGED_IN, a ready.txt,
// a quit.txt that would end this session the moment it starts, a cmd.txt that would run in a fresh logged-in browser) must not be
// mistaken for this session's state, so clear all of it before anything else. rmSync removes a symbolic link as the link, never
// what it points at.
for (const f of [...SESSION_FILES, 'quit.txt', 'shoot.txt', 'error.txt', 'run.log', 'qr-big.png', 'cmd.txt.tmp', ...POLLED.map((n) => n + '.tmp')]) rmQuiet(f);
const { chromium } = require('playwright-core');

// Chromium: CHROMIUM_PATH if set, else the newest ~/.cache/ms-playwright/chromium-<n>/chrome-linux64/chrome.
function chromiumPath() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const root = process.env.HOME + '/.cache/ms-playwright';
  const dirs = fs.existsSync(root) ? fs.readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)) : [];
  dirs.sort((a, b) => parseInt(b.split('-')[1], 10) - parseInt(a.split('-')[1], 10));
  for (const d of dirs) { const c = root + '/' + d + '/chrome-linux64/chrome'; if (fs.existsSync(c)) return c; }
  throw new Error('no Chromium found: set CHROMIUM_PATH or run `npx playwright-core install chromium`');
}


// A page counts as "the Feishu login page" only when its HOST is accounts.feishu.cn, and as "logged in" only when it is a real http(s)
// page on another host: a URL that merely contains that text, about:blank and Chromium's error pages are neither.
const LOGIN_HOST = 'accounts.feishu.cn';
const parseUrl = (u) => { try { return new URL(u); } catch (_) { return null; } };
const isWebPage = (u) => { const p = parseUrl(u); return !!p && (p.protocol === 'http:' || p.protocol === 'https:') && !!p.hostname; };
const onLoginPage = (u) => isWebPage(u) && parseUrl(u).hostname === LOGIN_HOST;
const loggedInUrl = (u) => isWebPage(u) && !onLoginPage(u);
// Only host + path is ever written to disk. An OAuth callback carries a one-time code, an implicit flow a token, in the query or fragment.
const safeUrl = (u) => {
  const p = parseUrl(u);
  if (!p) return '(unparsable url)';
  if (p.protocol === 'http:' || p.protocol === 'https:') return p.origin + p.pathname;
  return u === 'about:blank' ? u : p.protocol + '(opaque)';  // data: and blob: URLs carry their payload in the URL itself
};
const scrub = (s) => String(s).replace(/(https?:\/\/)[^\s/?#]*@/gi, '$1').replace(/(https?:\/\/[^\s?#]+)[?#]\S*/gi, '$1');

// Removed at exit however the session ends: normal quit, an error nobody caught, or a terminating signal.
const cleanup = () => { if (readPid() === process.pid) rmQuiet('run.pid'); for (const f of [...SESSION_FILES, 'quit.txt', 'shoot.txt', 'cmd.txt.tmp', ...POLLED.map((n) => n + '.tmp')]) rmQuiet(f); };
process.on('exit', cleanup); // last resort: covers process.exit() and anything else that ends the process

let ctx = null;
const closeBrowser = () => Promise.race([Promise.resolve(ctx && ctx.close()).catch(() => {}), new Promise((r) => setTimeout(r, 5000).unref())]);
let stopping = false;
for (const [sig, code] of [['SIGINT', 130], ['SIGTERM', 143], ['SIGHUP', 129]]) {
  process.on(sig, () => { if (!stopping) { stopping = true; closeBrowser().finally(() => process.exit(code)); } });
}

// The next command from cmd.txt (and remove it), or null. The file is opened without following links, and what was opened is
// checked (a regular file, owned by us): a link, a FIFO or someone else's file is deleted unread, and cannot be swapped in between
// a check and the read. A command that is not a JSON object is a failed step, not a crash: it is removed, result.txt says
// `ERR <short reason>` (never the command itself, which may hold what was to be typed into a form), and the session waits for the
// next command with the browser still open and ready.txt in place, so nobody has to scan the login code again.
function readCommand() {
  let fd;
  try {
    fd = fs.openSync('cmd.txt', fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  } catch (e) {
    if (e.code !== 'ENOENT') fs.rmSync('cmd.txt', { force: true, recursive: true }); // ELOOP: a symbolic link
    return null;
  }
  try {
    const meta = fs.fstatSync(fd);
    if (!meta.isFile() || meta.uid !== process.getuid()) { fs.rmSync('cmd.txt', { force: true, recursive: true }); return null; }  // a FIFO, a directory, someone else's file
    const text = fs.readFileSync(fd, 'utf8');
    fs.unlinkSync('cmd.txt');
    let cmd;
    try { cmd = JSON.parse(text); } catch (_) { cmd = undefined; }
    if (cmd === null || typeof cmd !== 'object' || Array.isArray(cmd)) {
      put('result.txt', 'ERR command is not valid JSON (an object was expected)');
      return null;
    }
    return cmd;
  } finally {
    fs.closeSync(fd);
  }
}

async function main() {
  const profile = process.env.REMOTE_LOGIN_PROFILE || (process.env.HOME ? process.env.HOME + '/.cache/feishu-agent-browser' : '');
  if (!profile) { console.error('remote_login: HOME is not set and REMOTE_LOGIN_PROFILE is empty'); process.exit(2); }
  const profileAbs = path.resolve(profile);
  if (profileAbs === '/' || (process.env.HOME && profileAbs === path.resolve(process.env.HOME))) {
    console.error('remote_login: refusing to use ' + profileAbs + ' as the browser profile: it must be a dedicated directory (it is chmod-ed to 0700)');
    process.exit(2);
  }
  fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
  if (fs.statSync(profile).uid !== process.getuid()) { console.error('remote_login: the profile directory is not yours'); process.exit(2); }
  fs.chmodSync(profile, 0o700);  // it holds the owner's Feishu login: an older, more open directory is tightened, not trusted
  const target = process.argv[2];
  ctx = await chromium.launchPersistentContext(profile, {
    headless: true, executablePath: chromiumPath(),
    viewport: { width: 1280, height: 900 }, locale: 'zh-CN',
    args: process.env.REMOTE_LOGIN_SANDBOX === '1' ? [] : ['--no-sandbox'],
    handleSIGINT: false, handleSIGTERM: false, handleSIGHUP: false }); // the handlers above close the browser and clean up
  const page = ctx.pages()[0] || await ctx.newPage();
  const log = (m) => fs.appendFileSync('run.log', new Date().toISOString().slice(11, 19) + ' ' + m + '\n');
  // Live text of the page, refreshed every loop in both phases, so a watcher can tell "still a QR" from "scanned, confirm on the phone"
  // without sending commands. Best effort: the page may be mid-navigation.
  const liveText = async () => { try { put('page.txt', await page.evaluate(() => document.body.innerText)); } catch (_) {} };
  const dump = async () => {
    put('page.txt', await page.evaluate(() => document.body.innerText));
    fs.writeFileSync('page.html', await page.evaluate(() => document.body.innerHTML));
    await page.screenshot({ path: 'page.png', fullPage: true });
  };
  let n = 0;
  // A fresh QR for the owner: refresh only a code that has expired, then screenshot the canvas. While the page says 确认登录 the owner
  // has scanned and is confirming on the phone: refreshing now would void that login, so refuse.
  const shootQr = async () => {
    let text;
    try { text = await page.evaluate(() => document.body.innerText); } catch (_) { throw new Error('the page cannot be read right now: not refreshing the QR'); }
    if (text.includes('确认登录')) throw new Error('awaiting phone confirmation: not refreshing the QR');
    const refresh = await page.$('text=刷新二维码');
    if (refresh) { await refresh.click({ timeout: 5000 }); await page.waitForTimeout(1800); log('refreshed'); }
    const qr = await page.$('canvas');
    if (!qr) throw new Error('no QR canvas on this page');
    await qr.screenshot({ path: 'qr.png' }); n++; put('state.txt', 'QR ' + n + ' ' + Date.now()); log('qr ' + n);
  };
  await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const loginDeadline = Date.now() + (parseInt(process.env.REMOTE_LOGIN_LOGIN_TIMEOUT || '', 10) || 1800) * 1000;
  let loggedIn = false;
  while (Date.now() < loginDeadline) {
    if (fs.existsSync('quit.txt')) return;   // asked to stop while still waiting for the scan
    put('url.txt', safeUrl(page.url()));
    await liveText();
    if (loggedInUrl(page.url())) {
      // goto() resolves on domcontentloaded, so an app page that bounces to the login page client-side looks logged in for a moment:
      // let it settle, then look again. Only a URL that is still a real page off the login host after the wait counts.
      await page.waitForTimeout(3000);
      if (!loggedInUrl(page.url())) { log('bounced to the login page: still waiting for the scan'); continue; }
      try { await dump(); } catch (_) { /* a redirect chain can destroy the page context right here: the login itself still happened */ }
      put('state.txt', 'LOGGED_IN ' + safeUrl(page.url())); log('LOGGED_IN'); loggedIn = true; break;
    }
    if (fs.existsSync('shoot.txt')) {          // on demand: refresh if expired, then photograph the newest code
      try { fs.unlinkSync('shoot.txt'); await shootQr(); } catch (e) { log('shoot err ' + scrub(e.message)); }
    }
    await page.waitForTimeout(1200);
  }
  if (!loggedIn) { log('gave up waiting for the login'); process.exitCode = 3; return; }  // never write ready.txt: nobody logged in
  put('ready.txt', 'ready ' + safeUrl(page.url()));
  const commandDeadline = Date.now() + 60 * 60 * 1000;
  while (Date.now() < commandDeadline) {
    if (fs.existsSync('quit.txt')) break;
    const cmd = readCommand();
    if (cmd) {
      rmQuiet('result.txt');  // a caller waiting on result.txt must never read the previous command's result
      try {
        if (cmd.goto) await page.goto(cmd.goto, { waitUntil: 'domcontentloaded', timeout: 60000 });
        if (cmd.fill) for (const [sel, val] of cmd.fill) await page.fill(sel, val, { timeout: 15000 });
        if (cmd.click) for (const sel of cmd.click) await page.click(sel, { timeout: 15000 });
        if (cmd.shoot) await shootQr();
        if (cmd.wait) await page.waitForTimeout(cmd.wait);
        await dump(); put('result.txt', 'OK ' + safeUrl(page.url()));
      } catch (e) {
        try { await dump(); } catch (_) {}
        put('result.txt', 'ERR ' + scrub(e.message));
      }
    }
    put('url.txt', safeUrl(page.url()));
    await liveText();
    await page.waitForTimeout(1200);
  }
}

main()
  .catch((e) => {
    if (stopping) return; /* the page died because we closed it on a signal: not an error */
    process.exitCode = 1;
    try { fs.writeFileSync('error.txt', scrub(e)); } catch (_) { /* the working directory is gone: nothing to report to, still close the browser below */ }
  })
  .finally(async () => { await closeBrowser(); cleanup(); });
