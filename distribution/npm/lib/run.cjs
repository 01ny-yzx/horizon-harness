'use strict';
const { spawn } = require('node:child_process');

function run(executable, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {stdio:'inherit', cwd:process.cwd(), env:process.env});
    const handlers = new Map();
    for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
      const handler = () => child.kill(signal);
      handlers.set(signal, handler); process.on(signal, handler);
    }
    const cleanup = () => {
      for (const [signal, handler] of handlers) process.removeListener(signal, handler);
    };
    child.once('error', error => { cleanup(); reject(error); });
    child.once('exit', (code, signal) => { cleanup(); resolve({code, signal}); });
  });
}
module.exports = {run};
