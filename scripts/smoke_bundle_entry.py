"""Executable routing must preserve source mode and avoid recursive TUI children."""
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class BundleEntry(unittest.TestCase):
    def test_frozen_service_relaunches_binary_without_python_flags(self):
        from tui.runtime import _service_command
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', '/app/horizon'):
            self.assertEqual(_service_command({}), ['/app/horizon', '--_horizon-service'])

    def test_source_service_keeps_module_entry(self):
        from tui.runtime import _service_command
        with patch.object(sys, 'frozen', False, create=True):
            command = _service_command({})
        self.assertEqual(command[1:], ['-P', '-m', 'tui.service'])

    def test_frozen_python_tool_uses_internal_script_runner(self):
        from core.python_runtime import python_command
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'horizon.exe'):
            self.assertEqual(python_command('hello 世界.py'), ['horizon.exe', '--_horizon-python', 'hello 世界.py'])

    def test_entry_dispatches_service_without_starting_ui(self):
        with patch.object(sys, 'argv', ['horizon', '--_horizon-service']), patch('tui.service.main') as service:
            runpy.run_path(str(Path(__file__).with_name('horizon.py')), run_name='__main__')
        service.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
