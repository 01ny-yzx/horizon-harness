"""Run portable source checks in temporary data; never change real keys/clipboard."""
from pathlib import Path
import json
import os
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tui.dependencies import missing_runtime_modules


def main():
    print(f'Horizon source check: {platform.system()} {platform.machine()}, Python {platform.python_version()}', flush=True)
    missing = missing_runtime_modules()
    if missing:
        print('Missing required packages: ' + ', '.join(missing), flush=True)
        print('Dependency installation did not complete. Run these first:', flush=True)
        print(f'"{sys.executable}" -m pip install --upgrade pip', flush=True)
        print(f'"{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}"', flush=True)
        return 2
    with TemporaryDirectory(prefix='horizon-platform-check-') as temp:
        os.environ.update(HORIZON_USER_DATA_ROOT=temp, LLM_PROVIDER='mock', LLM_MODEL='mock',
            LLM_API_KEY='local-test-key', LLM_BASE_URL='http://127.0.0.1:1/v1',
            LLM_CAPABILITIES_JSON=json.dumps({'max_context_tokens':128000}),
            EMBEDDING_ENABLED='false', MCP_ENABLED='false', WEB_SEARCH_PROVIDER='disabled',
            TAVILY_API_KEY='', API_AUTH_ENABLED='false',
            PYTHONPATH=os.pathsep.join(filter(None,(str(ROOT),os.environ.get('PYTHONPATH','')))))
        names=['platform_support','bundle_entry','external_process','windows_terminal_colors','global_model','tui_model_inheritance','tui_runtime','tui_models','search_configuration',
               'tui_tab_persistence','tui_copy_selection','tui_python_version','working_directory']
        suite=unittest.defaultTestLoader.loadTestsFromNames(['scripts.smoke_'+name for name in names])
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        for _, reason in result.skipped:
            print('Optional check skipped: ' + reason, flush=True)
        if not result.wasSuccessful():return 1
        check=subprocess.run([sys.executable,str(ROOT/'scripts/smoke_host_execution_access_modes.py')],
                             cwd=ROOT,env=dict(os.environ),timeout=60)
        if check.returncode:return check.returncode
    print('Portable source checks passed. Native interactive terminal/clipboard still require a manual check.',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
