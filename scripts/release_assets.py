"""Native artifact metadata, including an honest minimum macOS version."""
import json
from pathlib import Path
import re
import subprocess


def macos_minimum(bundle: Path) -> str:
    versions = []
    for path in bundle.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix not in ('.so', '.dylib') and path.name not in ('horizon', 'Python'):
            continue
        result = subprocess.run(['/usr/bin/otool', '-l', str(path)], capture_output=True, text=True)
        versions += re.findall(r'cmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform \d+\s+minos ([\d.]+)', result.stdout)
        versions += re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)', result.stdout)
    if not versions:
        raise ValueError('Cannot determine the native bundle minimum macOS version')
    return max(versions, key=lambda value: tuple(int(part) for part in value.split('.')))


def build_metadata(bundle, archive, version, target, sha256):
    result = {'version': version, 'target': target, 'filename': archive.name, 'sha256': sha256}
    if target.startswith('macos-'):
        result['minimum_macos'] = macos_minimum(bundle)
    archive.with_name(archive.name + '.build.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result
