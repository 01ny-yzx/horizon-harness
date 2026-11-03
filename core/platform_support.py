"""Small platform boundary for private files and interprocess configuration locks."""
from contextlib import contextmanager
import os
from pathlib import Path
import sys

WINDOWS = sys.platform == 'win32'


def private_file_mode(fd):
    # Windows relies on inherited user-profile ACLs, not POSIX mode bits.
    if not WINDOWS:
        os.fchmod(fd, 0o600)


@contextmanager
def process_file_lock(path: Path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        private_file_mode(fd)
        if WINDOWS:
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'\0')
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
