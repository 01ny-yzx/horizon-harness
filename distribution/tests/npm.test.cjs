'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { targetFor, validateManifest, safeEntries, acquireLock, download } = require('../npm/lib/install.cjs');
const { run } = require('../npm/lib/run.cjs');

test('native platform selection is explicit', () => {
  assert.equal(targetFor('darwin', 'arm64'), 'macos-arm64');
  assert.equal(targetFor('darwin', 'x64'), 'macos-x64');
  assert.equal(targetFor('win32', 'x64'), 'windows-x64');
  assert.throws(() => targetFor('linux', 'x64'), /not supported/);
  assert.throws(() => targetFor('win32', 'arm64'), /not supported/);
});
test('manifest versions and checksums cannot drift', () => {
  const manifest = {version:'0.1.0', targets:{'macos-arm64':{
    filename:'horizon-0.1.0-macos-arm64.tar.gz', sha256:'a'.repeat(64)}}};
  assert.equal(validateManifest(manifest, '0.1.0', 'macos-arm64').sha256, 'a'.repeat(64));
  assert.throws(() => validateManifest(manifest, '0.2.0', 'macos-arm64'), /version/);
  assert.throws(() => validateManifest(manifest, '0.1.0', 'windows-x64'), /not available/);
});
test('archive paths stay under the native directory', () => {
  assert.equal(safeEntries('horizon/\nhorizon/_internal/a\nhorizon/horizon\n'), true);
  for (const entries of ['../outside', '/tmp/outside', 'horizon/../../outside', 'other/file']) {
    assert.throws(() => safeEntries(entries), /archive path/);
  }
});
test('concurrent installs serialize and cannot remove another owner lock', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'horizon-lock-test-'));
  try {
    const release = await acquireLock(root);
    await assert.rejects(acquireLock(root, 10), /Another install/);
    assert.ok(fs.existsSync(path.join(root, '.horizon-install.lock')));
    release();
    const next = await acquireLock(root); next();
    assert.equal(fs.existsSync(path.join(root, '.horizon-install.lock')), false);
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
});
test('native exit codes are retained', async () => {
  assert.deepEqual(await run(process.execPath, ['-e', 'process.exit(7)']), {code:7, signal:null});
});
test('native signal exits are retained', {skip:process.platform === 'win32'}, async () => {
  assert.deepEqual(await run(process.execPath, ['-e', "process.kill(process.pid,'SIGTERM')"]), {code:null, signal:'SIGTERM'});
});
test('download handles closed large HTTP bodies and bounded redirects', async () => {
  const http = require('node:http');
  const crypto = require('node:crypto');
  const payload = Buffer.alloc(256 * 1024, 97);
  const server = http.createServer((request, response) => {
    if (request.url === '/redirect') { response.writeHead(302, {Location:'/asset'}); response.end(); }
    else if (request.url === '/loop') { response.writeHead(302, {Location:'/loop'}); response.end(); }
    else if (request.url === '/bad') { response.writeHead(302, {Location:'http://['}); response.end(); }
    else { response.writeHead(200, {'Content-Length':payload.length, Connection:'close'}); response.end(payload); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'horizon-download-test-'));
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    const digest = await download(base + '/redirect', path.join(root, 'asset'));
    assert.equal(digest, crypto.createHash('sha256').update(payload).digest('hex'));
    assert.deepEqual(fs.readFileSync(path.join(root, 'asset')), payload);
    await assert.rejects(download(base + '/loop', path.join(root, 'loop')), /redirect/i);
    await assert.rejects(download(base + '/bad', path.join(root, 'bad')), /redirect/i);
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, {recursive:true, force:true});
  }
});
