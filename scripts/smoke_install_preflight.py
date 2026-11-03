"""Dependency failure is actionable and stops before importing the test suite."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch


class InstallPreflight(unittest.TestCase):
    def test_missing_dependency_stops_before_loading_tests(self):
        from scripts import smoke_cross_platform as checks
        output=StringIO()
        with patch('tui.dependencies.find_spec', return_value=None), patch.object(checks.unittest.defaultTestLoader,'loadTestsFromNames') as load, redirect_stdout(output):
            self.assertEqual(checks.main(),2)
            load.assert_not_called()
        self.assertIn('requirements.txt',output.getvalue())
        self.assertIn('Missing',output.getvalue())


    def test_browser_can_be_absent_without_blocking_core_imports(self):
        import os, subprocess, sys
        from tempfile import TemporaryDirectory
        code = """import importlib.abc, sys
class NoBrowser(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'playwright' or fullname.startswith('playwright.'):
   raise ModuleNotFoundError('optional browser absent')
sys.meta_path.insert(0, NoBrowser())
import core.browser, core.loop, tui.app, api.app
assert core.browser.sync_playwright is None
assert 'requirements-browser.txt' in core.browser.INSTALL_PLAYWRIGHT
print('core imports without browser: OK')
"""
        with TemporaryDirectory() as tmp:
            run = subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ,HORIZON_USER_DATA_ROOT=tmp,LLM_PROVIDER='mock',EMBEDDING_ENABLED='false',MCP_ENABLED='false'),
                capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('core imports without browser: OK',run.stdout)

    def test_missing_dependency_cli_has_no_traceback(self):
        import subprocess,sys
        code = """from unittest.mock import patch
import runpy
with patch('tui.dependencies.find_spec',return_value=None):
 runpy.run_module('tui',run_name='__main__')
"""
        run=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)
        self.assertIn('dependencies are incomplete',run.stderr)
        self.assertNotIn('Traceback',run.stderr)

    def test_windows_symlink_privilege_skips_only_optional_case(self):
        from scripts.smoke_search_configuration import SearchSettings
        error=OSError('symlink privilege unavailable');error.winerror=1314
        result=unittest.TestResult()
        case=SearchSettings('test_symlink_cannot_bypass_private_key_guard_when_supported')
        with patch.object(Path,'symlink_to',side_effect=error):case.run(result)
        self.assertEqual(len(result.skipped),1)
        self.assertFalse(result.errors)
        self.assertFalse(result.failures)


if __name__=='__main__':unittest.main()
