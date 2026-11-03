'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Readable, Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const { spawnSync } = require('node:child_process');

function targetFor(platform = process.platform, arch = process.arch) {
  const target = { 'darwin-arm64':'macos-arm64', 'darwin-x64':'macos-x64', 'win32-x64':'windows-x64' }[`${platform}-${arch}`];
  if (!target) throw new Error(`Horizon is not supported on ${platform}/${arch} yet`);
  return target;
}
function validateManifest(manifest, version, target) {
  if (manifest.version !== version) throw new Error('Horizon package/manifest version mismatch');
  const item = manifest.targets[target];
  if (!item) throw new Error(`Horizon ${version} is not available for ${target}`);
  const extension = target.startsWith('windows') ? '.zip' : '.tar.gz';
  if (item.filename !== `horizon-${version}-${target}${extension}` || !/^[a-f0-9]{64}$/.test(item.sha256)) {
    throw new Error('Invalid Horizon release manifest');
  }
  return item;
}
function safeEntries(listing) {
  for (const name of listing.split(/\r?\n/).filter(Boolean)) {
    if (!name.startsWith('horizon/') || name.split(/[\\/]/).includes('..') || name.includes('\\')) {
      throw new Error(`Unsafe archive path: ${name}`);
    }
  }
  return true;
}
function command(file, args, options = {}) {
  const result = spawnSync(file, args, {encoding:'utf8', timeout:120000, maxBuffer:16 * 1024 * 1024, ...options});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${path.basename(file)} failed: ${(result.stderr || '').slice(-1500)}`);
  return result.stdout;
}
async function download(url, file) {
  const response = await fetch(url, {signal:AbortSignal.timeout(120000)});
  if (!response.ok) throw new Error(`Download failed (${response.status}). Horizon release assets may not be published yet.`);
  const hash = crypto.createHash('sha256');
  let bytes = 0;
  const check = new Transform({transform(chunk, encoding, done) {
    bytes += chunk.length;
    if (bytes > 512 * 1024 * 1024) return done(new Error('Horizon download exceeds size limit'));
    hash.update(chunk); done(null, chunk);
  }});
  await pipeline(Readable.fromWeb(response.body), check, fs.createWriteStream(file, {mode:0o600}));
  return hash.digest('hex');
}
function executableAt(root, target) {
  return path.join(root, 'native', target.startsWith('windows') ? 'horizon.exe' : 'horizon');
}
async function acquireLock(root, timeout = 300000) {
  const file = path.join(root, '.horizon-install.lock');
  const token = `${process.pid}:${crypto.randomBytes(16).toString('hex')}`;
  const deadline = Date.now() + timeout;
  while (true) {
    try {
      const fd = fs.openSync(file, 'wx', 0o600);
      fs.writeFileSync(fd, token); fs.closeSync(fd);
      return () => {
        if (fs.existsSync(file) && fs.readFileSync(file, 'utf8') === token) fs.unlinkSync(file);
      };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (Date.now() >= deadline) throw new Error(`Another install is running. If it was interrupted, remove ${file} and retry.`);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
}
async function install(options = {}) {
  const root = options.root || path.resolve(__dirname, '..');
  const version = options.version || require(path.join(root, 'package.json')).version;
  const manifest = options.manifest || JSON.parse(fs.readFileSync(path.join(root, 'platforms.json'), 'utf8'));
  const target = options.target || targetFor();
  const item = validateManifest(manifest, version, target);
  if (process.platform === 'darwin' && item.minimum_macos) {
    const current = command('/usr/bin/sw_vers', ['-productVersion']).trim().split('.').map(Number);
    const minimum = item.minimum_macos.split('.').map(Number);
    for (let i = 0; i < 3; i++) {
      if ((current[i] || 0) > (minimum[i] || 0)) break;
      if ((current[i] || 0) < (minimum[i] || 0)) throw new Error(`This Horizon build requires macOS ${item.minimum_macos}+`);
    }
  }
  const executable = executableAt(root, target);
  const receipt = path.join(root, 'native', 'installation.json');
  const cached = () => {
    try {
      const saved = JSON.parse(fs.readFileSync(receipt, 'utf8'));
      return saved.version === version && saved.sha256 === item.sha256 && fs.existsSync(executable);
    } catch { return false; }
  };
  if (cached()) return executable;
  const release = await acquireLock(root);
  try {
  if (cached()) return executable;
  const base = (options.base || process.env.HORIZON_DOWNLOAD_BASE || manifest.download_base).replace(/\/$/, '');
  const stage = fs.mkdtempSync(path.join(root, '.horizon-download-'));
  const native = path.join(root, 'native');
  const backup = path.join(stage, 'previous');
  let keepStage = false;
  try {
    const archive = path.join(stage, item.filename);
    const digest = await download(`${base}/v${version}/${item.filename}`, archive);
    if (digest !== item.sha256) throw new Error('Horizon checksum mismatch; existing installation was kept');
    const tar = process.platform === 'win32'
      ? path.join(process.env.SystemRoot || 'C:/Windows', 'System32', 'tar.exe') : '/usr/bin/tar';
    safeEntries(command(tar, ['-tf', archive]));
    const unpack = path.join(stage, 'unpack'); fs.mkdirSync(unpack);
    command(tar, ['-xf', archive, '-C', unpack]);
    const candidate = path.join(unpack, 'horizon', target.startsWith('windows') ? 'horizon.exe' : 'horizon');
    if (!fs.existsSync(candidate)) throw new Error('Horizon executable is missing from the archive');
    if (!target.startsWith('windows')) fs.chmodSync(candidate, 0o755);
    if (command(candidate, ['--version']).trim() !== `Horizon ${version}`) throw new Error('Horizon executable version mismatch');
    fs.writeFileSync(path.join(unpack, 'horizon', 'installation.json'), JSON.stringify({version, target, sha256:item.sha256}));
    if (fs.existsSync(native)) fs.renameSync(native, backup);
    try {
      fs.renameSync(path.join(unpack, 'horizon'), native);
    } catch (error) {
      if (fs.existsSync(backup)) {
        try { fs.renameSync(backup, native); }
        catch { keepStage = true; throw new Error(`Installation failed; previous files preserved at ${backup}`); }
      }
      throw error;
    }
    return executable;
  } finally {
    if (!keepStage) fs.rmSync(stage, {recursive:true, force:true});
  }
  } finally { release(); }
}
module.exports = {targetFor, validateManifest, safeEntries, acquireLock, install};
