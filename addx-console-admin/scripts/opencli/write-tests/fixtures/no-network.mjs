// Default transport is denied; individual tests may replace fetch with fixtures.
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
const deny=()=>{throw new Error('OFFLINE_TEST_NETWORK_FORBIDDEN')};
globalThis.fetch=deny;
net.connect=deny;net.createConnection=deny;net.Socket.prototype.connect=deny;
tls.connect=deny;http.request=deny;http.get=deny;https.request=deny;https.get=deny;
