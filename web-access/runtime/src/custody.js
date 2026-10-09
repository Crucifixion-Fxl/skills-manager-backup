'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { fail } = require('./gateway');

function directory(file, create = false) {
  if (!path.isAbsolute(file || '') || typeof process.getuid !== 'function') fail('PRIVATE_STORAGE_UNAVAILABLE');
  const parent = path.dirname(file);
  if (create && !fs.existsSync(parent)) fs.mkdirSync(parent, { mode: 0o700 });
  const stat = fs.lstatSync(parent);
  if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid() || stat.mode & 0o077
      || fs.realpathSync(parent) !== parent) fail('INVALID_BRIDGE_CREDENTIALS');
  return parent;
}
function saveCredentials(file, value) {
  try {
    directory(file, true);
    const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
    let inode;
    try { inode = fs.fstatSync(fd).ino; fs.writeFileSync(fd, JSON.stringify(value)); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
    return () => { try { if (fs.lstatSync(file).ino === inode) fs.unlinkSync(file); } catch {} };
  } catch { fail('INVALID_BRIDGE_CREDENTIALS'); }
}
function readCredentials(file) {
  let fd;
  try {
    directory(file);
    fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.uid !== process.getuid() || stat.mode & 0o077 || stat.size > 4096) throw new Error();
    const value = JSON.parse(fs.readFileSync(fd, 'utf8'));
    if (!/^[a-f0-9]{64}$/.test(value.key || '') || typeof value.origin !== 'string' || typeof value.url !== 'string') throw new Error();
    return value;
  } catch { fail('INVALID_BRIDGE_CREDENTIALS'); }
  finally { if (fd !== undefined) fs.closeSync(fd); }
}
module.exports = { directory, saveCredentials, readCredentials };
