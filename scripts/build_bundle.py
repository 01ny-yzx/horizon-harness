"""Build, test and archive a native terminal executable on the current OS."""
from pathlib import Path
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tui.version import __version__
from scripts.release_assets import build_metadata


def main():
    system = platform.system()
    arch = {'AMD64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(platform.machine())
    if system not in ('Darwin', 'Windows') or arch not in ('x64', 'arm64') or system == 'Windows' and arch != 'x64':
        raise SystemExit('Build on macOS arm64/x64 or Windows x64 using the matching Python architecture.')
    try:
        import PyInstaller
    except ImportError:
        raise SystemExit('Install build dependencies first: python -m pip install -r requirements-build.txt') from None
    out = ROOT / 'dist' / 'bundles'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(out), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
                    str(ROOT / 'packaging' / 'horizon.spec')], cwd=ROOT, check=True)
    bundle = out / 'horizon'
    executable = bundle / ('horizon.exe' if system == 'Windows' else 'horizon')
    # Verify the executable in an unrelated Unicode/spaces directory, without Python on PATH.
    with tempfile.TemporaryDirectory(prefix='horizon-native-') as temp:
        cwd = Path(temp) / '工作目录 workspace'
        cwd.mkdir()
        env = dict(os.environ, PATH=str(executable.parent))
        for key in ('PYTHONPATH', 'PYTHONHOME', '__PYVENV_LAUNCHER__'):
            env.pop(key, None)
        subprocess.run([str(executable), '--version'], cwd=cwd, env=env, check=True, timeout=30)
        subprocess.run([str(executable), '--_horizon-self-test'], cwd=cwd, env=env, check=True, timeout=120)
    for name in ('LICENSE', 'LICENSE.zh-CN'):
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, bundle / name)
    name = f'horizon-{__version__}-' + ('macos' if system == 'Darwin' else 'windows') + '-' + arch
    if system == 'Windows':
        archive = out / (name + '.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as file:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    file.write(path, path.relative_to(out))
    else:
        archive = out / (name + '.tar.gz')
        with tarfile.open(archive, 'w:gz', dereference=False) as file:
            file.add(bundle, arcname='horizon')
    with archive.open('rb') as file:
        checksum = hashlib.file_digest(file, 'sha256').hexdigest()
    archive.with_name(archive.name + '.sha256').write_text(checksum + '  ' + archive.name + '\n', encoding='utf-8')
    build_metadata(bundle, archive, __version__, ('macos' if system == 'Darwin' else 'windows') + '-' + arch, checksum)
    print('Native build verified:', archive)


if __name__ == '__main__':
    main()
