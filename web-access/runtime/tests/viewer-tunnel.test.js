'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { viewerTunnel } = require('../src/viewer-tunnel');
test('viewer helper exposes recovery codes without leaking SSH diagnostics and never silently succeeds on auth failure', async () => {
  for (const [message, code] of [['Permission denied (publickey). private-path', 'SSH_AUTH_REQUIRED'],
    ['bind: Address already in use private-path', 'LOCAL_PORT_IN_USE'],
    ['channel 0: open failed: administratively prohibited: open failed', 'SSH_FORWARDING_DENIED']]) {
    const spawnImpl = () => {
      const child = new EventEmitter(); child.stderr = new PassThrough(); child.kill = () => {};
      queueMicrotask(() => { child.stderr.write(message); child.emit('exit', 1); }); return child;
    };
    await assert.rejects(viewerTunnel({ target: 'naturehood', port: 19828, spawnImpl, fetchImpl: async () => { throw new Error(); } }),
      error => error.code === code && !error.message.includes('private-path'));
  }
});
test('mux forward port collision fails before capability transfer and HTTP readiness', async () => {
  let fetches = 0, capabilityReads = 0;
  const spawnImpl = () => {
    const child = new EventEmitter(); child.stderr = new PassThrough();
    child.kill = () => queueMicrotask(() => child.emit('exit', 0)); return child;
  };
  const controlImpl = async (cmd, args) => {
    if (args.includes('forward')) { const error = new Error(); error.stderr = 'Could not request local forwarding'; throw error; }
    return { stdout: '', stderr: '' };
  };
  await assert.rejects(viewerTunnel({ target: 'fixture', port: 19828, spawnImpl, controlImpl,
    fetchImpl: async () => { fetches++; return {}; }, readCapabilityImpl: async () => { capabilityReads++; return {}; } }),
    e => e.code === 'LOCAL_PORT_IN_USE');
  assert.equal(fetches, 0); assert.equal(capabilityReads, 0);
});
