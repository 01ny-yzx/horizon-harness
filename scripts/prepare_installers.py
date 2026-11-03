"""Assemble reviewable installation files from verified native archives; never publish."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tui.version import __version__

BASE = 'https://github.com/01ny-yzx/horizon-harness/releases/download'


def prepare(artifacts: Path, output: Path, *, version=__version__, required=()):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid release version')
    targets = {}
    for target, suffix in (('macos-arm64', '.tar.gz'), ('macos-x64', '.tar.gz'), ('windows-x64', '.zip')):
        archive = artifacts / f'horizon-{version}-{target}{suffix}'
        if not archive.exists():
            continue
        checksum = archive.with_name(archive.name + '.sha256')
        expected = checksum.read_text().split()[0]
        with archive.open('rb') as file:
            actual = hashlib.file_digest(file, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError(f'Checksum mismatch: {archive.name}')
        metadata = json.loads(archive.with_name(archive.name + '.build.json').read_text())
        if any(metadata.get(k) != value for k, value in
               (('version', version), ('target', target), ('filename', archive.name), ('sha256', actual))):
            raise ValueError(f'Build metadata mismatch: {archive.name}')
        if target.startswith('macos-') and not re.fullmatch(r'\d+(\.\d+){1,2}', metadata.get('minimum_macos', '')):
            raise ValueError('Missing minimum macOS version')
        targets[target] = metadata
    if not targets:
        raise ValueError('No verified native archives found')
    missing = set(required) - targets.keys()
    if missing:
        raise ValueError('Missing required native artifacts: ' + ', '.join(sorted(missing)))
    package = output / 'npm'
    if package.exists() and not (package / '.horizon-generated-package').is_file():
        raise ValueError(f'Refusing to replace an unmanaged directory: {package}')
    manifest = {'version': version, 'download_base': BASE, 'targets': targets}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(ROOT / 'distribution' / 'npm', package)
    (package / '.horizon-generated-package').touch()
    config = json.loads((package / 'package.json').read_text())
    config.update(version=version, private=False)
    (package / 'package.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    (package / 'platforms.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    shutil.copy2(ROOT / 'LICENSE', package / 'LICENSE')
    (package / 'bin' / 'horizon.cjs').chmod(0o755)
    (package / 'README.md').write_text(f'''# Horizon Harness

Terminal AI agent. Install with `npm install -g horizon-harness`, then run `horizon`.
Node.js 18+ is required for this installation channel; Python is bundled.
The native package is downloaded from the matching GitHub release and checked against the SHA-256 embedded in this package.
Available targets for {version}: {', '.join(targets)}.
Settings and API keys stay in your user profile. With npm scripts disabled, the first launch completes the download.
Source and usage: https://github.com/01ny-yzx/horizon-harness
''', encoding='utf-8')
    macs = {key: value for key, value in targets.items() if key.startswith('macos-')}
    if macs:
        cases = []
        for target in ('macos-arm64', 'macos-x64'):
            item = macs.get(target, {})
            cases.append(f"  {target}) EXPECTED_SHA='{item.get('sha256', '')}'; MIN_MACOS='{item.get('minimum_macos', '0.0')}' ;;")
        shell = (ROOT / 'distribution' / 'install.sh.in').read_text()
        shell = shell.replace('@@VERSION@@', version).replace('@@TARGET_CASES@@', '\n'.join(cases))
        (output / 'install.sh').write_text(shell, encoding='utf-8')
        (output / 'install.sh').chmod(0o755)
        formula = ['class Horizon < Formula', '  desc "Terminal AI agent"',
                   '  homepage "https://github.com/01ny-yzx/horizon-harness"',
                   f'  version "{version}"', '  license "MIT"', '  depends_on :macos']
        if len(macs) == 1:
            target, item = next(iter(macs.items()))
            formula += [f'  depends_on arch: :{"arm64" if target.endswith("arm64") else "x86_64"}',
                        f'  url "{BASE}/v{version}/{item["filename"]}"', f'  sha256 "{item["sha256"]}"']
        else:
            for target, item in macs.items():
                formula += [f'  {"on_arm" if target.endswith("arm64") else "on_intel"} do',
                            f'    url "{BASE}/v{version}/{item["filename"]}"', f'    sha256 "{item["sha256"]}"', '  end']
        formula += ['  def install']
        for target, item in macs.items():
            condition = 'Hardware::CPU.arm?' if target.endswith('arm64') else 'Hardware::CPU.intel?'
            minimum = item['minimum_macos']
            formula += [f'    odie "This build requires macOS {minimum}+" if {condition} && MacOS.version < MacOSVersion.new("{minimum}")']
        formula += ['    libexec.install Dir["*"]', '    bin.install_symlink libexec/"horizon"', '  end',
                    '  test do', '    system bin/"horizon", "--version"', '  end', 'end', '']
        (output / 'Formula').mkdir(exist_ok=True)
        (output / 'Formula' / 'horizon.rb').write_text('\n'.join(formula), encoding='utf-8')
    else:
        (output / 'install.sh').unlink(missing_ok=True)
        (output / 'Formula' / 'horizon.rb').unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, default=ROOT / 'dist' / 'bundles')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'distribution')
    parser.add_argument('--require-target', action='append', default=[])
    args = parser.parse_args()
    manifest = prepare(args.artifacts.resolve(), args.output.resolve(), required=args.require_target)
    print('Installer files prepared (not published):', ', '.join(manifest['targets']))


if __name__ == '__main__':
    main()
