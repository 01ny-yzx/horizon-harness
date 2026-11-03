"""Frozen host tools must not inherit the application's DLL search directory."""
import subprocess
import os
import signal
import time
import sys
import unittest
from unittest.mock import Mock, patch


class ExternalProcess(unittest.TestCase):
    def test_windows_dll_search_is_restored_after_launch_failure(self):
        from core.external_process import external_launch
        kernel = Mock()
        def read(size, buffer):
            if buffer is not None:
                buffer.value = 'bundle'
            return 6
        kernel.GetDllDirectoryW.side_effect = read
        kernel.SetDllDirectoryW.return_value = 1
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), \
             patch('core.external_process._kernel32', return_value=kernel):
            with self.assertRaises(RuntimeError):
                with external_launch():
                    self.assertIsNone(kernel.SetDllDirectoryW.call_args.args[0])
                    raise RuntimeError('spawn failed')
        self.assertEqual(kernel.SetDllDirectoryW.call_args.args, ('bundle',))

    def test_run_captures_output_and_timeout(self):
        from core.external_process import run_external
        result = run_external([sys.executable, '-c', "print('ok')"], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'ok')
        with self.assertRaises(subprocess.TimeoutExpired):
            run_external([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=.05)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX pipe ownership')
    def test_descendant_owning_pipe_does_not_extend_timeout(self):
        from core.external_process import run_external
        code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)']); print(p.pid,flush=True); time.sleep(5)"
        started = time.monotonic()
        try:
            run_external([sys.executable, '-c', code], capture_output=True, timeout=.15)
            self.fail('Expected timeout')
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            if exc.stdout:
                try:
                    os.kill(int(exc.stdout.strip()), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.assertLess(elapsed, 1)


class FrozenClipboard(unittest.IsolatedAsyncioTestCase):
    async def test_windows_clipboard_uses_external_launcher_and_utf8_stdin(self):
        from tui.clipboard import copy_text
        owner = Mock()
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'win32'), \
             patch('tui.clipboard.run_external') as run:
            await copy_text('中文 日本語', owner)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs['input'], '中文 日本語'.encode('utf-8'))
        self.assertTrue(run.call_args.kwargs['check'])
        owner.copy_to_clipboard.assert_called_once_with('中文 日本語')


if __name__ == '__main__':
    unittest.main()
