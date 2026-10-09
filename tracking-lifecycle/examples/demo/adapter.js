// Demonstration project adapter. Real projects must use the actual SDK path.
const fs = require('node:fs');
const path = require('node:path');
const mode = process.argv[2];
const output = path.join(__dirname, '.tracking');
fs.mkdirSync(output, { recursive: true });
const event = { type: 'PAGE', trackerType: 'BASE', spm: ['home'] };
if (mode === 'usage') {
  fs.writeFileSync(path.join(output, 'usage.json'), JSON.stringify({ events: [{ ...event, issue: '101' }] }));
} else if (mode === 'local') {
  fs.writeFileSync(path.join(output, 'local-capture.json'), JSON.stringify({ events: [{ ...event, fields: { source: 'home' } }] }));
} else if (mode === 'artifact') {
  fs.writeFileSync(path.join(output, 'artifact.bin'), 'synthetic demo artifact');
} else {
  process.stderr.write(`Unknown demo mode: ${mode}\n`);
  process.exitCode = 1;
}
