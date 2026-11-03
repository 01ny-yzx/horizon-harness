#!/usr/bin/env node
'use strict';
const { install } = require('../lib/install.cjs');
const { launch } = require('../lib/update.cjs');
(async () => {
  const executable = await install();
  const result = await launch(executable, process.argv.slice(2));
  if (result.signal) process.kill(process.pid, result.signal);
  else process.exitCode = result.code === null ? 1 : result.code;
})().catch(error => { console.error(`Horizon: ${error.message}`); process.exitCode = 1; });
