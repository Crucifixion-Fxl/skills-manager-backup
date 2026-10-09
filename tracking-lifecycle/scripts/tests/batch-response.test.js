const assert = require('node:assert/strict');
const { test } = require('node:test');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

const cases = [
  ['success', 200, { code: 200, success: true, data: { createdCount: 1 } }, 0],
  ['legacy code-only success', 200, { code: 200, data: { createdCount: 1 } }, 0],
  ['explicit failure overrides code', 200, { code: 200, success: false }, 3],
  ['error code overrides success', 200, { code: 500, success: true }, 3],
  ['business failure', 200, { code: 500, success: false }, 3],
  ['missing envelope', 200, {}, 3],
  ['HTTP failure', 500, { code: 200, success: true }, 3],
];
for (const [name, status, body, expected] of cases) {
  test(name, () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tracker-response-'));
    try {
      const stub = path.join(directory, 'fetch.cjs');
      fs.writeFileSync(stub, `global.fetch = async () => ({ok: ${status === 200}, status: ${status}, json: async () => (${JSON.stringify(body)})});`);
      const script = path.join(__dirname, '../yaml-to-batch-payload.js');
      const input = path.join(__dirname, 'test-self-define/tracking-design.md');
      const result = spawnSync(process.execPath, ['--require', stub, script, `--input=${input}`, '--app-id=14', '--api-key=test-only', '--execute'], { encoding: 'utf8', env: { ...process.env, TRACKING_PLATFORM_BASE_URL: 'https://fixture.example' } });
      assert.equal(result.status, expected, result.stdout + result.stderr);
      if (expected !== 0) assert.ok(!result.stdout.includes('创建成功'));
    } finally {
      fs.rmSync(directory, { recursive: true });
    }
  });
}
