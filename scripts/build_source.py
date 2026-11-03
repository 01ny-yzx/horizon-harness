"""Export versioned source without private data or generated build artifacts."""
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tui.version import __version__

SOURCE_DIRS = {'.github', 'api', 'cloud_mcp', 'config', 'core', 'desktop', 'distribution',
               'docs', 'evals', 'frontend', 'marketplace', 'mcp_servers', 'packaging',
               'prompts', 'providers', 'scripts', 'tools', 'tui', 'workflows'}
ROOT_FILES = {'.env.example', '.gitignore', 'README.md', 'README.zh-CN.md',
              'CONTRIBUTING.md', 'CONTRIBUTING.zh-CN.md', 'SECURITY.md', 'SECURITY.zh-CN.md',
              'LICENSE', 'LICENSE.zh-CN', 'main.py', 'requirements.txt',
              'requirements-browser.txt', 'requirements-build.txt'}


def source_path(name: str) -> bool:
    path = Path(name)
    if not path.parts or path.is_absolute() or '..' in path.parts:
        return False
    if any(part in {'__pycache__', 'node_modules', '.git', '.venv', 'superpowers', 'audits', '.DS_Store'} for part in path.parts):
        return False
    if path.name.startswith('.env') and path.name != '.env.example':
        return False
    if path.name.endswith(('.pyc', '.pyo', '.local.json')):
        return False
    return name in ROOT_FILES or path.parts[0] in SOURCE_DIRS


def build_source(root: Path = ROOT, output: Path | None = None) -> Path:
    if not (root / '.git').exists():
        raise ValueError('Build the source archive from a Git checkout')
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    files = [name for name in sorted(set(names)) if source_path(name)
             and (root / name).is_file() and not (root / name).is_symlink()]
    out = output or root / 'dist'
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f'horizon-{__version__}-source.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as file:
        for name in files:
            file.write(root / name, 'horizon-harness/' + name)
    with zipfile.ZipFile(archive) as file:
        if file.testzip() is not None:
            raise ValueError('Source archive verification failed')
    return archive


if __name__ == '__main__':
    print('Source archive verified:', build_source())
