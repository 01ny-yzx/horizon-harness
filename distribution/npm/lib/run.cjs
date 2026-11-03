'use strict';
const { spawn, spawnSync } = require('node:child_process');
const path = require('node:path');

function run(executable, args, options = {}) {
  return new Promise((resolve, reject) => {
    const grouped = options.killTree && process.platform !== 'win32';
    const child = spawn(executable, args, {stdio:'inherit', cwd:process.cwd(), env:options.env || process.env,
      detached:!!grouped, timeout:options.killTree ? undefined : options.timeout});
    const stop = signal => {
      if (!child.pid) return;
      if (options.killTree && process.platform === 'win32') {
        spawnSync(path.join(process.env.SystemRoot || 'C:/Windows', 'System32/taskkill.exe'),
          ['/PID', String(child.pid), '/T', '/F'], {stdio:'ignore', timeout:5000});
      } else if (grouped) {
        try { process.kill(-child.pid, signal); } catch(error) { if (error.code !== 'ESRCH') throw error; }
      } else child.kill(signal);
    };
    const timer = options.killTree && options.timeout ? setTimeout(() => stop('SIGKILL'), options.timeout) : undefined;
    const handlers = new Map();
    for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
      const handler = () => stop(signal);
      handlers.set(signal, handler); process.on(signal, handler);
    }
    const cleanup = () => {
      clearTimeout(timer);
      for (const [signal, handler] of handlers) process.removeListener(signal, handler);
    };
    child.once('error', error => { cleanup(); reject(error); });
    child.once('exit', (code, signal) => { cleanup(); resolve({code, signal}); });
  });
}
module.exports = {run};
