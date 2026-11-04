"""Release checks and host-owned update handoff; no agent or API keys involved."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

import httpx
from tui.version import __version__

REPOSITORY = '01ny-yzx/horizon-harness'
RELEASE_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
UPDATE_EXIT = 85


def release_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value) or len(value) > 64:
        return None
    return tuple(int(part) for part in value.removeprefix('v').split('.'))


@dataclass(frozen=True)
class Installation:
    method: str
    request: str = ''
    root: str = ''
    binary: str = ''
    command: str = ''


def detect_installation() -> Installation:
    if not getattr(sys, 'frozen', False):
        return Installation('source')
    exe = Path(sys.executable).resolve()
    request = os.environ.get('HORIZON_UPDATE_REQUEST', '')
    root = os.environ.get('HORIZON_NPM_ROOT', '')
    launched_exe = os.environ.get('HORIZON_NPM_EXECUTABLE', '')
    if request and root and (exe.parent == (Path(root) / 'native').resolve()
                             or (launched_exe and exe == Path(launched_exe).resolve())):
        return Installation('npm', request=request)
    brew = shutil.which('brew')
    if sys.platform == 'darwin' and brew and '/Cellar/horizon/' in str(exe):
        return Installation('brew', command=brew)
    # Only update a managed curl tree and the symlink which points to this binary.
    if sys.platform == 'darwin' and (exe.parent / '.horizon-managed').is_file() and exe.parent.parent.name == 'versions':
        root = exe.parent.parent.parent
        try:
            saved_binary = (exe.parent / '.horizon-bin').read_text(encoding='utf-8').removesuffix('\n')
        except (OSError, UnicodeError):
            saved_binary = ''
        binary = Path(saved_binary) if saved_binary else Path(os.environ.get('HORIZON_BIN_DIR', str(Path.home() / '.local/bin'))) / 'horizon'
        if binary.is_symlink() and binary.resolve() == exe:
            return Installation('curl', root=str(root), binary=str(binary))
    return Installation('unknown')


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.update-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class UpdateManager:
    def __init__(self, path: Path, *, current=__version__, installation=None, http=None):
        self.path = Path(path)
        self.current = current
        self.installation = installation or detect_installation()
        self.http = http
        self.latest = None
        self._lock = asyncio.Lock()
        self._skipped = None
        try:
            self._skipped = json.loads(self.path.read_text(encoding='utf-8')).get('skipped')
        except (OSError, ValueError, AttributeError):
            pass

    def should_notify(self, version):
        return version != self._skipped

    def skip(self, version):
        if release_version(version) is None:
            raise ValueError('Invalid version')
        atomic_json(self.path, {'skipped': version})
        self._skipped = version

    async def check(self, *, manual=False):
        if self.installation.method == 'source':
            return None
        async with self._lock:
            try:
                if self.http is None:
                    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=True) as http:
                        data = await self._release(http)
                else:
                    data = await self._release(self.http)
                version = release_version(data.get('tag_name'))
                current = release_version(self.current)
                if not version or not current or data.get('draft') or data.get('prerelease') or version <= current:
                    self.latest = None
                    return None
                self.latest = '.'.join(map(str,version))
                return self.latest
            except (httpx.HTTPError, ImportError, ValueError, TypeError, AttributeError):
                if manual:
                    raise RuntimeError('无法检查更新，请检查网络后重试') from None
                return None

    async def _release(self, http):
        response = await http.get(RELEASE_API, headers={'Accept':'application/vnd.github+json', 'User-Agent':f'Horizon/{self.current}'})
        response.raise_for_status()
        if len(response.content) > 1024 * 1024:
            raise ValueError('Release response too large')
        return response.json()

    def handoff(self, version):
        if release_version(version) is None:
            raise ValueError('Invalid version')
        if self.installation.method == 'npm' and self.installation.request:
            atomic_json(Path(self.installation.request), {'version':version})


def perform_update(manager: UpdateManager, version: str, restart_args: list[str]) -> int:
    """Called only after TUI and local service stop. npm is handled by its parent."""
    if release_version(version) is None:
        raise ValueError('Invalid version')
    if manager.installation.method == 'npm':
        try:
            manager.handoff(version)
            return UPDATE_EXIT
        except OSError as error:
            print(f'Update failed: {error}\nRetry with horizon upgrade.', file=sys.stderr, flush=True)
            return 1
    from core.external_process import run_external, external_env
    installation = manager.installation
    print(f'Updating Horizon {manager.current} → {version}…', flush=True)
    try:
        if installation.method == 'brew':
            run_external([installation.command, 'update'], check=True, timeout=600)
            run_external([installation.command, 'upgrade', '01ny-yzx/tap/horizon'], check=True, timeout=600)
            prefix = run_external([installation.command, '--prefix', '01ny-yzx/tap/horizon'], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
            executable = str(Path(prefix) / 'bin/horizon')
        elif installation.method == 'curl':
            with tempfile.TemporaryDirectory(prefix='horizon-update-') as temp:
                script = str(Path(temp) / 'install.sh')
                run_external(['/usr/bin/curl', '-fsSL', '--connect-timeout', '15', '--max-time', '120',
                              f'https://github.com/{REPOSITORY}/releases/download/v{version}/install.sh', '-o', script], check=True, timeout=130)
                env = dict(os.environ, HORIZON_INSTALL_ROOT=installation.root,
                           HORIZON_BIN_DIR=str(Path(installation.binary).parent))
                # Public updates always use the official release URLs.
                env.pop('HORIZON_DOWNLOAD_BASE', None)
                run_external(['/bin/sh', script], env=env, check=True, timeout=600)
            executable = installation.binary
        else:
            raise RuntimeError('无法识别安装方式，请通过原安装渠道更新')
        result = run_external([executable, '--version'], capture_output=True, text=True, check=True, timeout=30)
        actual = result.stdout.strip().removeprefix('Horizon ')
        if release_version(actual) is None or release_version(actual) < release_version(version):
            raise RuntimeError('安装渠道尚未同步此版本，请稍后重试')
        print(f'Horizon {actual} installed. Restarting…', flush=True)
        os.execve(executable, [executable, *restart_args], dict(external_env(), PYINSTALLER_RESET_ENVIRONMENT='1'))
    except Exception as error:
        # Keep the terminal output visible; do not restart a failed installation.
        print(f'Update failed: {error}\nRetry with horizon upgrade.', file=sys.stderr, flush=True)
        return 1
