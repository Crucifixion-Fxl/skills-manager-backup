// 飞书 open 平台登录 + 建应用的无头浏览器会话（agent 代做 `lark-cli config init --new` 的确认页）。
// 用法：node feishu_browser.js <确认页 URL>   （工作目录里用文件通信，见 feishu_register_agent_apps.sh）
//   shoot.txt  → 刷新登录二维码并截图到 qr.png（仅在要发码时才刷新，刷新会作废旧码）
//   cmd.txt    → JSON {goto, fill:[[selector,value]], click:[selector], wait:ms}，结果写 result.txt / page.txt；
//                命令不是合法的 JSON 对象只算这一步失败（result.txt 写 ERR <短原因>，会话继续）
//   quit.txt   → 退出（用完不删，所以启动时先清掉上一次留下的）
// 前置：playwright-core（npm i playwright-core）和本机 chromium。用完删 ~/.cache/feishu-agent-browser。
// 威胁模型：单用户、0700 目录；符号链接检查是检查前 + 创建后复核，不防同 uid 的进程。
// 下面对 cmd.txt / quit.txt 的符号链接处理都是这个范围内的尽力而为（打开时不跟随链接、打开之后再核对类型和属主）；
// 能在检查与使用之间换文件的只有同一个用户下的进程，本来就能读写这个目录；jchen 已接受这个残余竞态（skills#117）。
const fs = require('fs');
// The working directory holds page snapshots of a logged-in session and is where commands arrive: it must be private.
{
  const st = fs.statSync('.');
  if ((st.mode & 0o077) !== 0 || st.uid !== process.getuid()) {
    console.error('feishu_browser: the working directory must be private (0700, owned by you)');
    process.exit(2);
  }
  process.umask(0o077); // everything written from here is 0600, the browser profile 0700
}
// quit.txt is the signal that ends a session and nothing deletes it afterwards: one left by an earlier session in this directory
// would end this one the moment it is logged in (and the owner would have to scan the login code again). Clear it before anything
// else. lstat, not stat: a symbolic link is removed as the link it is, never followed, and what it points at is left alone.
try {
  const stale = fs.lstatSync('quit.txt');
  if (stale.isFile() || stale.isSymbolicLink()) fs.unlinkSync('quit.txt');
} catch (e) {
  if (e.code !== 'ENOENT') throw e;
}
const { chromium } = require('playwright-core');

// Everything one session leaves in this directory: page snapshots of a logged-in session, the login marker, the command channel.
// It is removed however the session ends: normal quit, an error nobody caught, or a terminating signal.
const SESSION_FILES = ['page.txt', 'page.html', 'page.png', 'qr.png', 'ready.txt', 'state.txt', 'cmd.txt', 'result.txt'];
const cleanup = () => { for (const f of SESSION_FILES) fs.rmSync(f, { force: true }); };
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
    if (e.code !== 'ENOENT') fs.rmSync('cmd.txt', { force: true }); // ELOOP: a symbolic link
    return null;
  }
  try {
    const meta = fs.fstatSync(fd);
    if (!meta.isFile() || meta.uid !== process.getuid()) { fs.rmSync('cmd.txt', { force: true }); return null; }
    const text = fs.readFileSync(fd, 'utf8');
    fs.unlinkSync('cmd.txt');
    let cmd;
    try { cmd = JSON.parse(text); } catch (_) { cmd = undefined; }
    if (cmd === null || typeof cmd !== 'object' || Array.isArray(cmd)) {
      fs.writeFileSync('result.txt', 'ERR command is not valid JSON (an object was expected)');
      return null;
    }
    return cmd;
  } finally {
    fs.closeSync(fd);
  }
}

async function main() {
  fs.writeFileSync('run.pid', String(process.pid));
  const target = process.argv[2];
  ctx = await chromium.launchPersistentContext(process.env.HOME + '/.cache/feishu-agent-browser', {
    headless: true, executablePath: process.env.HOME + '/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',
    viewport: { width: 1280, height: 900 }, locale: 'zh-CN', args: ['--no-sandbox'],
    handleSIGINT: false, handleSIGTERM: false, handleSIGHUP: false }); // the handlers above close the browser and clean up
  const page = ctx.pages()[0] || await ctx.newPage();
  const log = (m) => fs.appendFileSync('run.log', new Date().toISOString().slice(11, 19) + ' ' + m + '\n');
  const dump = async () => {
    fs.writeFileSync('page.txt', await page.evaluate(() => document.body.innerText));
    fs.writeFileSync('page.html', await page.evaluate(() => document.body.innerHTML));
    await page.screenshot({ path: 'page.png', fullPage: true });
  };
  await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const deadline = Date.now() + 30 * 60 * 1000;
  let n = 0;
  while (Date.now() < deadline) {
    if (!/accounts\.feishu\.cn/.test(page.url())) {
      await page.waitForTimeout(3000); await dump();
      fs.writeFileSync('state.txt', 'LOGGED_IN ' + page.url()); log('LOGGED_IN'); break;
    }
    if (fs.existsSync('shoot.txt')) {          // 按需：先点刷新，再立刻拍最新的码
      fs.unlinkSync('shoot.txt');
      try {
        const refresh = await page.$('text=刷新二维码');
        if (refresh) { await refresh.click({ timeout: 5000 }); await page.waitForTimeout(1800); log('refreshed'); }
        const qr = await page.$('canvas');
        if (qr) { await qr.screenshot({ path: 'qr.png' }); n++; fs.writeFileSync('state.txt', 'QR ' + n + ' ' + Date.now()); log('qr ' + n); }
      } catch (e) { log('shoot err ' + e.message); }
    }
    await page.waitForTimeout(1200);
  }
  fs.writeFileSync('ready.txt', 'ready ' + page.url());
  while (Date.now() < deadline + 60 * 60 * 1000) {
    if (fs.existsSync('quit.txt')) break;
    const cmd = readCommand();
    if (cmd) {
      try {
        if (cmd.goto) await page.goto(cmd.goto, { waitUntil: 'domcontentloaded', timeout: 60000 });
        if (cmd.fill) for (const [sel, val] of cmd.fill) await page.fill(sel, val, { timeout: 15000 });
        if (cmd.click) for (const sel of cmd.click) await page.click(sel, { timeout: 15000 });
        if (cmd.wait) await page.waitForTimeout(cmd.wait);
        await dump(); fs.writeFileSync('result.txt', 'OK ' + page.url());
      } catch (e) {
        try { await dump(); } catch (_) {}
        fs.writeFileSync('result.txt', 'ERR ' + e.message);
      }
    }
    await page.waitForTimeout(1200);
  }
}

main()
  .catch((e) => { if (stopping) return; /* the page died because we closed it on a signal: not an error */ fs.writeFileSync('error.txt', String(e)); process.exitCode = 1; })
  .finally(async () => { await closeBrowser(); cleanup(); });
