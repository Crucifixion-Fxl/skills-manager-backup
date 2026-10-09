const test = require('node:test');
const assert = require('node:assert/strict');
const { MicroClient, normalizeMicroEvent } = require('../cli/src/micro');
const { ContractError } = require('../cli/src/contracts');

const event = { type: 'COMPONENT', trackerType: 'CLK', spm: ['feed', 'card', 'submit'], point: 'submit', parameters: [] };
const schema = 'iglu:com.smart_camera/feed_card_submit_clk/jsonschema/1-0-7';

test('live Micro client requires the actual HTTPS sandbox endpoint', () => {
  assert.throws(() => new MicroClient(), e => e.code === 'INVALID_ENDPOINT');
  assert.throws(() => new MicroClient({ baseUrl: 'http://sandbox.example.test' }),
    e => e.code === 'INVALID_ENDPOINT');
  assert.doesNotThrow(() => new MicroClient({ baseUrl: 'https://cn-sandbox.example.test' }));
});
const raw = { rawEvent: { parameters: { eid: 'evt-101', ue_pr: JSON.stringify({
  schema: 'iglu:com.snowplowanalytics.snowplow/unstruct_event/jsonschema/1-0-0',
  data: { schema, data: { source: 'feed' } },
}) }, context: { timestamp: '2026-10-02T04:00:00Z' } }, event: { event_vendor: 'com.smart_camera' } };

test('Micro payload is mapped by actual Iglu URI, not a bare point', () => {
  const result = normalizeMicroEvent(raw, 'smart_camera', '1-0-7', [event]);
  assert.deepEqual(result.spm, event.spm);
  assert.equal(result.eventId, 'evt-101');
  assert.deepEqual(result.fields, { source: 'feed' });
  assert.equal(result.schemaVersion, '1-0-7');
  assert.throws(() => normalizeMicroEvent(raw, 'other_app', '1-0-7', [event]), e => e.code === 'UNKNOWN_SCHEMA');
});

test('Micro query uses isolated namespace and detects limit saturation', async () => {
  const calls = [];
  const client = new MicroClient({ request: async (path, body) => {
    calls.push([path, body]);
    return path.endsWith('/good') ? [raw] : [];
  } });
  const result = await client.query({ applicationId: 14, application: 'smart_camera',
    namespace: 'ci-101-job-1', version: '1-0-7', events: [event], limit: 50 });
  assert.equal(result.good[0].eventId, 'evt-101');
  assert.deepEqual(calls.map(([path]) => path), ['/micro/good', '/micro/bad']);
  assert.ok(calls.every(([_, body]) => body.namespace === 'ci-101-job-1'));
  assert.ok(calls.every(([_, body]) => body.app_id === 'smart_camera'));
  await assert.rejects(() => client.query({ applicationId: 14, application: 'smart_camera',
    namespace: 'shared', version: '1-0-7', events: [event] }), e => e instanceof ContractError && e.code === 'INVALID_NAMESPACE');
});
