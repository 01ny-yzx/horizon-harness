"""Launch host-installed programs without PyInstaller's private library paths."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import threading

_launch_lock = threading.RLock()


def _kernel32():
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int32
    return kernel


@contextmanager
def external_launch():
    if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
        yield
        return
    import ctypes
    # This process-global change lasts only through Popen, not communicate/wait.
    with _launch_lock:
        kernel = _kernel32()
        size = kernel.GetDllDirectoryW(0, None)
        previous = ctypes.create_unicode_buffer(size + 1)
        if size:
            kernel.GetDllDirectoryW(len(previous), previous)
        if not kernel.SetDllDirectoryW(None):
            raise OSError('Could not reset DLL search path for host tool')
        try:
            yield
        finally:
            if not kernel.SetDllDirectoryW(previous.value or None):
                raise OSError('Could not restore application DLL search path')


def external_env(env=None):
    env = dict(os.environ if env is None else env)
    if not getattr(sys, 'frozen', False):
        return env
    bundle = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)).resolve()
    for name in ('PATH', 'DYLD_LIBRARY_PATH'):
        if name in env:
            env[name] = os.pathsep.join(part for part in env[name].split(os.pathsep)
                                       if part and not Path(part).resolve().is_relative_to(bundle))
    if sys.platform.startswith('linux'):
        original = env.pop('LD_LIBRARY_PATH_ORIG', None)
        if original is None:
            env.pop('LD_LIBRARY_PATH', None)
        else:
            env['LD_LIBRARY_PATH'] = original
    return env


class ExternalPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        kwargs['env'] = external_env(kwargs.get('env'))
        with external_launch():
            super().__init__(*args, **kwargs)


def run_external(*args, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    if input is not None:
        if kwargs.get('stdin') is not None:
            raise ValueError('stdin and input cannot be combined')
        kwargs['stdin'] = subprocess.PIPE
    if capture_output:
        if kwargs.get('stdout') is not None or kwargs.get('stderr') is not None:
            raise ValueError('capture_output cannot be combined with stdout/stderr')
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with ExternalPopen(*args, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            if sys.platform == 'win32':
                # Match subprocess.run's Windows reader-thread handling.
                exc.stdout, exc.stderr = process.communicate()
            else:
                # Descendants may still own the pipes; do not wait for their EOF.
                process.wait()
            raise
        except BaseException:
            process.kill()
            process.wait()
            raise
        if check and process.returncode:
            raise subprocess.CalledProcessError(process.returncode, process.args, stdout, stderr)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
