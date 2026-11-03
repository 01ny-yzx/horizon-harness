'use strict';
if (process.env.HORIZON_SKIP_DOWNLOAD !== '1') {
  require('../lib/install.cjs').install().catch(error => {
    console.error(`Horizon install: ${error.message}`); process.exitCode = 1;
  });
}
