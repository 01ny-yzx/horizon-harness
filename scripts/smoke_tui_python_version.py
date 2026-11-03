"""Unsupported Python gets an actionable message before runtime imports."""
from pathlib import Path
import subprocess
import os
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PythonVersion(unittest.TestCase):
    def test_python_310_rejected_before_importing_311_dependencies(self):
        code = '''import sys, datetime, runpy
sys.version_info = (3, 10, 14)
del datetime.UTC
runpy.run_module('tui', run_name='__main__')
'''
        with TemporaryDirectory() as directory:
            run = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True,
                                 env=dict(os.environ, HORIZON_USER_DATA_ROOT=directory, LLM_PROVIDER='mock'))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Python 3.11', run.stderr)
        self.assertNotIn('Traceback', run.stderr)


if __name__ == '__main__':
    unittest.main()
