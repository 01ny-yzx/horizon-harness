"""Platform branches plus real lock contention; no credentials/clipboard writes."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class PlatformSupport(unittest.TestCase):
    def test_windows_lock_uses_same_byte_and_unlocks_on_error(self):
        from core.platform_support import process_file_lock
        fake=SimpleNamespace(LK_LOCK=1,LK_UNLCK=0,locking=Mock())
        with TemporaryDirectory() as tmp, patch('core.platform_support.WINDOWS',True), patch.dict('sys.modules',msvcrt=fake):
            with self.assertRaises(ValueError):
                with process_file_lock(Path(tmp)/'tabs.lock'):
                    raise ValueError('test')
            self.assertEqual([c.args[1:] for c in fake.locking.call_args_list],[(1,1),(0,1)])

    def test_connections_save_without_fchmod_on_windows(self):
        from core.platform_support import private_file_mode
        with TemporaryDirectory() as tmp:
            fd=os.open(Path(tmp)/'key',os.O_CREAT|os.O_RDWR,0o600)
            try:
                with patch('core.platform_support.WINDOWS',True), patch('core.platform_support.os.fchmod',create=True) as chmod:
                    private_file_mode(fd)
                    chmod.assert_not_called()
            finally:os.close(fd)


if __name__=='__main__':unittest.main()
