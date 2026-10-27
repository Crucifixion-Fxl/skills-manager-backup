// Child-process transport fixture: no real network, browser, or user cache.
import fs from 'node:fs';
import os from 'node:os';
import http from 'node:http';
import https from 'node:https';
import childProcess from 'node:child_process';
import { EventEmitter } from 'node:events';
import { syncBuiltinESMExports } from 'node:module';

const fixture = JSON.parse(fs.readFileSync(process.env.AUTH_TEST_FIXTURE_FILE, 'utf8'));
const events = { requests: [], browsers: 0 };
os.homedir = () => process.env.AUTH_TEST_HOME;
childProcess.spawn = () => {
  events.browsers++;
  return { unref() {}, on() {} };
};
function transport(url, options, callback) {
  const req = new EventEmitter();
  req.destroy = () => {};
  req.write = () => {};
  req.end = () => {};
  events.requests.push({ url, method: options.method || 'GET', authorization: options.headers?.Authorization });
  const next = fixture.responses.shift();
  queueMicrotask(() => {
    if (!next) return req.emit('error', new Error('Unexpected transport call'));
    if (next.error) return req.emit('error', new Error('SYNTHETIC_SECRET network error'));
    if (next.timeout) return req.emit('timeout');
    const res = new EventEmitter();
    res.statusCode = next.status ?? 200;
    res.resume = () => {};
    res.destroy = () => {};
    callback(res);
    if (next.aborted) return res.emit('aborted');
    const body = next.raw ?? JSON.stringify(next.body ?? {});
    res.emit('data', Buffer.from(body));
    res.emit('end');
  });
  return req;
}
https.get = (url, options, callback) => transport(url, options, callback);
https.request = (options, callback) => transport(
  `https://${options.hostname}${options.path}`, options, callback,
);
http.createServer = (callback) => {
  const server = new EventEmitter();
  server.close = () => {};
  server.listen = (_port, _host, ready) => {
    ready();
    queueMicrotask(() => callback(
      { url: '/callback?code=synthetic-code' },
      { writeHead() {}, end() {} },
    ));
  };
  return server;
};
syncBuiltinESMExports();
process.once('exit', () => fs.writeFileSync(process.env.AUTH_TEST_EVENTS, JSON.stringify(events)));
