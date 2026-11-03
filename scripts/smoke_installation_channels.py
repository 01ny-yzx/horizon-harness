"""Real offline-registry npm and curl installs against loopback native assets."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import unittest

from scripts.prepare_installers import prepare
from tui.version import __version__

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'dist' / 'bundles'
VERSION = __version__
ARCHIVE = ARTIFACTS / f'horizon-{VERSION}-macos-arm64.tar.gz'


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64' and ARCHIVE.is_file(),
                     'Requires a native macOS arm64 test archive')
class InstallationChannels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='horizon-installer-test-')
        cls.root = Path(cls.tmp.name)
        cls.output = cls.root / 'generated'
        cls.manifest = prepare(ARTIFACTS, cls.output)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path.startswith('/corrupt/'):
                    self.send_response(200); self.end_headers(); self.wfile.write(b'corrupt archive'); return
                if self.path == f'/v{VERSION}/{ARCHIVE.name}':
                    self.send_response(200)
                    self.send_header('Content-Length', str(ARCHIVE.stat().st_size)); self.end_headers()
                    with ARCHIVE.open('rb') as file:
                        shutil.copyfileobj(file, self.wfile)
                    return
                self.send_error(404)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.env = dict(os.environ, HORIZON_DOWNLOAD_BASE=cls.base,
                       HORIZON_USER_DATA_ROOT=str(cls.root / 'userdata'),
                       NPM_CONFIG_USERCONFIG=str(cls.root / 'empty.npmrc'),
                       NPM_CONFIG_CACHE=str(cls.root / 'npm-cache'))
        (cls.root / 'empty.npmrc').touch()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.tmp.cleanup()

    def run_command(self, command, *, env=None, success=True, cwd=None):
        run = subprocess.run(command, env=env or self.env, cwd=cwd or self.root,
                             capture_output=True, text=True, timeout=180)
        if success:
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        else:
            self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        return run

    def test_npm_pack_global_install_offline_launch_and_corrupt_download(self):
        packed = self.run_command(['npm', 'pack', str(self.output / 'npm'), '--json', '--pack-destination', str(self.root)])
        pack = json.loads(packed.stdout)[0]
        files = {item['path'] for item in pack['files']}
        self.assertEqual(files, {'package.json', 'LICENSE', 'README.md', 'platforms.json',
                                 'bin/horizon.cjs', 'lib/install.cjs', 'lib/run.cjs', 'lib/update.cjs', 'scripts/postinstall.cjs'})
        prefix = self.root / 'npm prefix'
        self.run_command(['npm', 'install', '--global', '--prefix', str(prefix), '--offline', '--no-audit', '--no-fund',
                          str(self.root / pack['filename'])])
        executable = prefix / 'bin' / 'horizon'
        offline = dict(self.env, HORIZON_DOWNLOAD_BASE='http://127.0.0.1:1')
        run = self.run_command([str(executable), '--version'], env=offline)
        self.assertEqual(run.stdout.strip(), f'Horizon {VERSION}')
        cwd = self.root / '用户 working directory'; cwd.mkdir()
        self.run_command([str(executable), '--_horizon-self-test'], env=offline, cwd=cwd)
        package = prefix / 'lib' / 'node_modules' / 'horizon-harness'
        native = package / 'native' / 'horizon'
        probe = self.root / 'probe-npm.py'
        probe.write_text('from tui.updates import detect_installation\nprint(detect_installation().method)\n')
        detected = self.run_command([str(executable), '--_horizon-python', str(probe)], env=offline)
        self.assertEqual(detected.stdout.strip(), 'npm')
        before = hashlib.sha256(native.read_bytes()).hexdigest()
        (package / 'native' / 'installation.json').unlink()
        code = "require(process.argv[1]).install({root:process.argv[2],base:process.argv[3]}).catch(e=>{console.error(e.message);process.exitCode=1})"
        bad = self.run_command(['node', '-e', code, str(package / 'lib' / 'install.cjs'), str(package), self.base + '/corrupt'], success=False)
        self.assertIn('checksum mismatch', bad.stderr)
        self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(), before)
        self.run_command([str(native), '--version'])

    def test_curl_style_install_reinstall_and_corruption_preserves_previous(self):
        root = self.root / 'curl install'
        bin_dir = self.root / 'curl bin'
        env = dict(self.env, HORIZON_INSTALL_ROOT=str(root), HORIZON_BIN_DIR=str(bin_dir))
        script = str(self.output / 'install.sh')
        self.run_command(['/bin/sh', script], env=env)
        executable = bin_dir / 'horizon'
        self.assertTrue(executable.is_symlink())
        target = executable.readlink()
        self.assertEqual((target.parent / '.horizon-bin').read_text().strip(), str(executable))
        probe = self.root / 'probe-curl.py'
        probe.write_text('from tui.updates import detect_installation\nprint(detect_installation().method)\n')
        without_install_env = {key:value for key,value in env.items() if key not in {'HORIZON_BIN_DIR', 'HORIZON_INSTALL_ROOT'}}
        detected = self.run_command([str(executable), '--_horizon-python', str(probe)], env=without_install_env)
        self.assertEqual(detected.stdout.strip(), 'curl')
        self.run_command([str(executable), '--version'], env=env)
        self.run_command(['/bin/sh', script], env=env)
        self.assertEqual(executable.readlink(), target)
        bad = self.run_command(['/bin/sh', script], env=dict(env, HORIZON_DOWNLOAD_BASE=self.base + '/corrupt'), success=False)
        self.assertIn('Checksum mismatch', bad.stderr)
        self.assertEqual(executable.readlink(), target)
        self.run_command([str(executable), '--version'], env=env)
        self.assertEqual(list(root.glob('.install.*')), [])
        self.assertFalse((root / '.install-lock').exists())

    def test_curl_refuses_to_replace_an_unmanaged_command(self):
        bin_dir = self.root / 'unmanaged-bin'; bin_dir.mkdir()
        command = bin_dir / 'horizon'; command.write_text('keep this file')
        env = dict(self.env, HORIZON_INSTALL_ROOT=str(self.root / 'unused-root'), HORIZON_BIN_DIR=str(bin_dir))
        self.run_command(['/bin/sh', str(self.output / 'install.sh')], env=env, success=False)
        self.assertEqual(command.read_text(), 'keep this file')

    def test_curl_cannot_remove_another_install_lock(self):
        root = self.root / 'locked-root'; root.mkdir()
        lock = root / '.install-lock'; lock.mkdir()
        env = dict(self.env, HORIZON_INSTALL_ROOT=str(root), HORIZON_BIN_DIR=str(self.root / 'locked-bin'))
        run = self.run_command(['/bin/sh', str(self.output / 'install.sh')], env=env, success=False)
        self.assertIn('Another install is running', run.stderr)
        self.assertTrue(lock.is_dir())

    def test_curl_locked_existing_install_keeps_command(self):
        root = self.root / 'existing-locked-root'
        bin_dir = self.root / 'existing-locked-bin'
        env = dict(self.env, HORIZON_INSTALL_ROOT=str(root), HORIZON_BIN_DIR=str(bin_dir))
        script = str(self.output / 'install.sh')
        self.run_command(['/bin/sh', script], env=env)
        command = bin_dir / 'horizon'
        target = command.readlink()
        lock = root / '.install-lock'; lock.mkdir()
        self.run_command(['/bin/sh', script], env=env, success=False)
        self.assertTrue(command.is_symlink())
        self.assertEqual(command.readlink(), target)
        self.assertTrue(lock.is_dir())
        self.run_command([str(command), '--version'], env=env)

    def test_homebrew_formula_has_actual_sha_and_valid_ruby(self):
        formula = self.output / 'Formula' / 'horizon.rb'
        text = formula.read_text()
        self.assertIn(self.manifest['targets']['macos-arm64']['sha256'], text)
        self.assertIn('depends_on arch: :arm64', text)
        self.assertNotIn('macos-x64.tar.gz', text)
        self.run_command(['/usr/bin/ruby', '-c', str(formula)])


if __name__ == '__main__':
    unittest.main()
