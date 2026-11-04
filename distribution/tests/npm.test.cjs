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
test('update handoff accepts only stable versions and retains install prefix', () => {
  const { validateRequest, packagePrefix } = require('../npm/lib/update.cjs');
  assert.equal(validateRequest({version:'1.0.12'}), '1.0.12');
  for (const version of ['1.0.2; echo bad','1.0.2-beta','--global','01.0.2']) {
    assert.throws(() => validateRequest({version}), /version/i);
  }
  assert.equal(packagePrefix('/usr/local/lib/node_modules/horizon-harness','linux'), '/usr/local');
  assert.equal(packagePrefix('C:\\custom\\node_modules\\horizon-harness','win32'), 'C:\\custom');
});
test('npm update waits for native shutdown, installs pinned version, and starts a fresh launcher', async () => {
  const { launch } = require('../npm/lib/update.cjs');
  const prefix = fs.mkdtempSync(path.join(os.tmpdir(),'horizon-update-test-'));
  const root = path.join(prefix, 'node_modules/horizon-harness');
  fs.mkdirSync(path.join(root, 'native'), {recursive:true});
  const calls=[];
  try {
    const result=await launch('fake-native', ['--project','test'], {root,install:async()=>path.join(root,'native/horizon'),npmCLI:'fake-npm-cli.js',
      run:async (file,args,options={}) => {
        calls.push({file,args,env:options.env});
        if(calls.length === 1) {
          assert.equal(options.env.HORIZON_NPM_ROOT,root);
          fs.writeFileSync(options.env.HORIZON_UPDATE_REQUEST,JSON.stringify({version:'1.0.2'}));
          return {code:85,signal:null};
        }
        if(calls.length === 2) {
          fs.writeFileSync(path.join(root,'native/installation.json'),JSON.stringify({version:'1.0.2'}));
        }
        return {code:0,signal:null};
      }});
    assert.equal(result.code,0);
    assert.deepEqual(calls[1].args,['fake-npm-cli.js','install','--global','--prefix',prefix,'horizon-harness@1.0.2']);
    assert.deepEqual(calls[2].args,[path.join(root,'bin/horizon.cjs'),'--project','test']);
    assert.equal(fs.existsSync(calls[0].env.HORIZON_UPDATE_REQUEST),false);
  } finally {fs.rmSync(prefix,{recursive:true,force:true});}
});
test('failed npm update never restarts or reports success', async () => {
  const { launch }=require('../npm/lib/update.cjs');
  const prefix=fs.mkdtempSync(path.join(os.tmpdir(),'horizon-failed-update-'));
  const root=path.join(prefix,'node_modules/horizon-harness');
  let count=0;
  try {
    const result=await launch('fake-native',[],{root,install:async()=>path.join(root,'native/horizon'),npmCLI:'fake-npm',run:async (file,args,options={})=>{
      count++;
      if(count===1){fs.writeFileSync(options.env.HORIZON_UPDATE_REQUEST,JSON.stringify({version:'1.0.2'}));return {code:85,signal:null};}
      return {code:1,signal:null};
    }});
    assert.equal(count,2);assert.equal(result.code,1);
  } finally {fs.rmSync(prefix,{recursive:true,force:true});}
});
test('real child-process handoff upgrades after exit and restarts without requiring the old launcher', async () => {
  const { launch }=require('../npm/lib/update.cjs');
  const prefix=fs.mkdtempSync(path.join(os.tmpdir(),'horizon-handoff-e2e-'));
  const root=path.join(prefix,'node_modules/horizon-harness');
  fs.mkdirSync(path.join(root,'native'),{recursive:true});fs.mkdirSync(path.join(root,'bin'));
  const native=path.join(prefix,'native.cjs'), npm=path.join(prefix,'npm-cli.js'), marker=path.join(prefix,'restarted');
  fs.writeFileSync(native,`require('node:fs').writeFileSync(process.env.HORIZON_UPDATE_REQUEST,JSON.stringify({version:'1.0.2'}));process.exit(85);`);
  fs.writeFileSync(npm,`require('node:fs').writeFileSync(${JSON.stringify(path.join(root,'native/installation.json'))},JSON.stringify({version:'1.0.2'}));`);
  fs.writeFileSync(path.join(root,'bin/horizon.cjs'),`require('node:fs').writeFileSync(${JSON.stringify(marker)},'ok');`);
  try {
    const result=await launch(process.execPath,[native],{root,install:async()=>path.join(root,'native/horizon'),npmCLI:npm});
    assert.equal(result.code,0);assert.equal(fs.readFileSync(marker,'utf8'),'ok');
  } finally {fs.rmSync(prefix,{recursive:true,force:true});}
});
test('update timeout stops package-manager descendants too', async () => {
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'horizon-timeout-tree-'));
  const heartbeat=path.join(temp,'heartbeat');
  const child=`const fs=require('node:fs');setInterval(()=>fs.appendFileSync(${JSON.stringify(heartbeat)},'x'),20);`;
  const parent=`require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(child)}],{stdio:'inherit'});setInterval(()=>{},1000);`;
  try {
    await run(process.execPath,['-e',parent],{killTree:true,timeout:1000});
    assert.ok(fs.existsSync(heartbeat),'descendant must have started');
    const before=fs.readFileSync(heartbeat,'utf8');
    await new Promise(resolve=>setTimeout(resolve,100));
    assert.equal(fs.readFileSync(heartbeat,'utf8'),before);
  } finally {fs.rmSync(temp,{recursive:true,force:true});}
});
test('Windows directory promotion retries only transient sharing violations', async () => {
  const { renameWithRetry }=require('../npm/lib/install.cjs');
  let attempts=0;
  await renameWithRetry('stage','native',{platform:'win32',timeout:100,interval:1,rename:()=>{
    attempts++;if(attempts<3)throw Object.assign(new Error('scan in progress'),{code:'EPERM'});
  }});
  assert.equal(attempts,3);
  attempts=0;
  await assert.rejects(renameWithRetry('stage','native',{platform:'win32',timeout:5,interval:1,rename:()=>{
    attempts++;throw Object.assign(new Error('locked'),{code:'EACCES'});
  }}),/locked/);
  assert.ok(attempts>1);
  await assert.rejects(renameWithRetry('stage','native',{platform:'win32',rename:()=>{throw Object.assign(new Error('missing'),{code:'ENOENT'});}}),/missing/);
});

test('npm runtime files live outside the package and are isolated by version, platform and checksum', () => {
  const { runtimeRoot } = require('../npm/lib/install.cjs');
  const hash = 'a'.repeat(64), otherHash = 'b'.repeat(64);
  for (const platform of ['darwin', 'win32']) {
    const cacheRoot = path.join(os.tmpdir(), 'horizon-cache-fixture');
    const first = runtimeRoot('1.0.3', platform === 'win32' ? 'windows-x64' : 'macos-arm64', hash, {cacheRoot});
    assert.ok(first.startsWith(cacheRoot + path.sep));
    assert.notEqual(first, runtimeRoot('1.0.4', platform === 'win32' ? 'windows-x64' : 'macos-arm64', hash, {cacheRoot}));
    assert.notEqual(first, runtimeRoot('1.0.3', platform === 'win32' ? 'windows-x64' : 'macos-arm64', otherHash, {cacheRoot}));
    assert.notEqual(first, runtimeRoot('1.0.3', 'macos-x64', hash, {cacheRoot}));
  }
  assert.throws(() => runtimeRoot('../bad', 'windows-x64', hash), /Invalid/);
  assert.throws(() => runtimeRoot('1.0.3', '../bad', hash), /Invalid/);
  assert.throws(() => runtimeRoot('1.0.3', 'windows-x64', '../bad'), /Invalid/);
});

test('update rereads package version after npm replaces files in the same Node process', async () => {
  const { install, runtimeRoot } = require('../npm/lib/install.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'horizon-refresh-version-'));
  const cacheRoot = path.join(root, 'separate-cache'), hash = 'a'.repeat(64), target = 'windows-x64';
  const packageFile = path.join(root, 'package.json');
  try {
    fs.writeFileSync(packageFile, JSON.stringify({version:'1.0.3'}));
    require(packageFile); // A launcher in an older process has already loaded this file.
    fs.writeFileSync(packageFile, JSON.stringify({version:'1.0.4'}));
    fs.writeFileSync(path.join(root,'platforms.json'), JSON.stringify({version:'1.0.4',targets:{[target]:{
      filename:'horizon-1.0.4-windows-x64.zip',sha256:hash}}}));
    const native = path.join(runtimeRoot('1.0.4',target,hash,{cacheRoot}), 'native');
    fs.mkdirSync(native,{recursive:true});
    fs.writeFileSync(path.join(native,'horizon.exe'),'fixture');
    fs.writeFileSync(path.join(native,'installation.json'),JSON.stringify({version:'1.0.4',sha256:hash}));
    assert.equal(await install({root,target,cacheRoot}),path.join(native,'horizon.exe'));
  } finally {delete require.cache[packageFile];fs.rmSync(root,{recursive:true,force:true});}
});
