'use strict';
// Runs in Node after the native child and its local service have exited.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { run } = require('./run.cjs');
const { install } = require('./install.cjs');
const UPDATE_EXIT = 85;
function validateRequest(data) {
  if (!data || typeof data.version !== 'string' || data.version.length > 64 ||
      !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(data.version)) {
    throw new Error('Invalid update version');
  }
  return data.version;
}
function packagePrefix(root, platform = process.platform) {
  const paths = platform === 'win32' ? path.win32 : path.posix;
  const modules = paths.dirname(root);
  if (paths.basename(modules) !== 'node_modules') throw new Error('Unrecognized npm installation path');
  const parent = paths.dirname(modules);
  return platform !== 'win32' && paths.basename(parent) === 'lib' ? paths.dirname(parent) : parent;
}
function npmCLI() {
  const candidates = [process.env.npm_execpath,
    path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js'),
    path.resolve(path.dirname(process.execPath), '../lib/node_modules/npm/bin/npm-cli.js')];
  for (const directory of (process.env.PATH || '').split(path.delimiter)) {
    for (const name of ['npm','npm.cmd']) {
      try {
        const executable = fs.realpathSync(path.join(directory, name));
        candidates.push(executable, path.join(path.dirname(executable), 'node_modules/npm/bin/npm-cli.js'));
      } catch {}
    }
  }
  const found = candidates.find(file => file && path.basename(file) === 'npm-cli.js' && fs.existsSync(file));
  if (!found) throw new Error('npm was not found. Reinstall Node.js, then retry with horizon upgrade.');
  return found;
}
async function launch(executable, args, options = {}) {
  const root = options.root || path.resolve(__dirname, '..');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'horizon-update-'));
  const request = path.join(directory, 'request.json');
  const env = {...process.env, HORIZON_NPM_ROOT:root, HORIZON_NPM_EXECUTABLE:executable, HORIZON_UPDATE_REQUEST:request};
  const runner = options.run || run;
  try {
    const result = await runner(executable, args, {env});
    if (result.code !== UPDATE_EXIT || result.signal) return result;
    const version = validateRequest(JSON.parse(fs.readFileSync(request, 'utf8')));
    const prefix = packagePrefix(root);
    console.log(`Updating Horizon to ${version} with npm…`);
    const resultInstall = await runner(process.execPath,
      [options.npmCLI || npmCLI(), 'install', '--global', '--prefix', prefix, `horizon-harness@${version}`],
      {timeout:600000,killTree:true});
    if (resultInstall.code !== 0 || resultInstall.signal) {
      console.error('Update failed. Retry with horizon upgrade or npm install -g horizon-harness.');
      return {code:1, signal:null};
    }
    // A successful npm exit is insufficient: scripts may have been disabled.
    const updatedExecutable = await (options.install || install)({root});
    const receipt = JSON.parse(fs.readFileSync(path.join(path.dirname(updatedExecutable), 'installation.json'), 'utf8'));
    if (receipt.version !== version) throw new Error('Native update incomplete. Run npm rebuild -g horizon-harness.');
    console.log(`Horizon ${version} installed. Restarting…`);
    return await runner(process.execPath, [path.join(root,'bin/horizon.cjs'), ...args.filter((arg,index) => !(index === 0 && arg === 'upgrade'))]);
  } finally {
    fs.rmSync(directory, {recursive:true, force:true});
  }
}
module.exports = {launch, validateRequest, packagePrefix, npmCLI};
