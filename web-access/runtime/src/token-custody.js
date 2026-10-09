'use strict';
const fs = require('node:fs');
const { fail } = require('./gateway');
const { directory } = require('./custody');
function reserveToken(file) {
  let inode;
  try {
    directory(file, true);
    const fd = fs.openSync(file, fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
    try { inode = fs.fstatSync(fd).ino; } finally { fs.closeSync(fd); }
  } catch { fail('PRIVATE_STORAGE_UNAVAILABLE'); }
  return {
    write(value) {
      let fd, stagedInode;
      const staged = file + '.' + require('node:crypto').randomBytes(16).toString('hex') + '.pending';
      try {
        directory(file);
        const current = fs.lstatSync(file);
        if (!current.isFile() || current.ino !== inode || current.mode & 0o077) fail('PRIVATE_STORAGE_CHANGED');
        fd = fs.openSync(staged, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
        stagedInode = fs.fstatSync(fd).ino;
        // writeFileSync handles partial writes; never truncate the existing sole credential.
        fs.writeFileSync(fd, JSON.stringify(value)); fs.fsyncSync(fd);
        fs.closeSync(fd); fd = undefined;
        if (fs.lstatSync(file).ino !== inode) fail('PRIVATE_STORAGE_CHANGED');
        fs.renameSync(staged, file); inode = stagedInode;
      } finally {
        if (fd !== undefined) fs.closeSync(fd);
        try { if (fs.lstatSync(staged).ino === stagedInode) fs.unlinkSync(staged); } catch {}
      }
    },
    close() {},
    discardEmpty() { try { if (fs.lstatSync(file).ino === inode && fs.lstatSync(file).size === 0) fs.unlinkSync(file); } catch {} },
  };
}
function readToken(file) {
  let fd;
  try {
    directory(file); fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.uid !== process.getuid() || stat.mode & 0o077 || stat.size > 16384) throw Error();
    const record = JSON.parse(fs.readFileSync(fd, 'utf8'));
    if (record.schemaVersion !== 1 || typeof record.secret !== 'string' || !record.secret
      || /[\r\n]/.test(record.secret) || !['UNVERIFIED','VERIFIED','REVOKED'].includes(record.state)) throw Error();
    return { record, inode: stat.ino };
  } catch { fail('PRIVATE_STORAGE_UNAVAILABLE'); }
  finally { if (fd !== undefined) fs.closeSync(fd); }
}
function removeToken(file, inode) {
  if (fs.lstatSync(file).ino !== inode) fail('PRIVATE_STORAGE_CHANGED');
  fs.unlinkSync(file);
}
module.exports = { reserveToken, readToken, removeToken };
