"""Run the generated Node installer against the native archive on each CI OS."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'dist' / 'distribution'
    manifest = json.loads((output / 'release-manifest.json').read_text())
    if len(manifest['targets']) != 1:
        raise SystemExit('Run native download smoke with exactly one CI target artifact')
    item = next(iter(manifest['targets'].values()))
    archive = ROOT / 'dist' / 'bundles' / item['filename']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path != f'/v{manifest["version"]}/{archive.name}':
                self.send_error(404); return
            self.send_response(200); self.send_header('Content-Length', str(archive.stat().st_size)); self.end_headers()
            with archive.open('rb') as file:
                shutil.copyfileobj(file, self.wfile)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='horizon-native-download-') as temp:
            root = Path(temp)
            package = root / 'package'; shutil.copytree(output / 'npm', package)
            cwd = root / '用户 workspace'; cwd.mkdir()
            env = dict(os.environ, HORIZON_DOWNLOAD_BASE=f'http://127.0.0.1:{server.server_port}',
                       HORIZON_USER_DATA_ROOT=str(root / 'userdata'))
            subprocess.run(['node', str(package / 'scripts' / 'postinstall.cjs')], cwd=cwd, env=env, check=True, timeout=180)
            # Prove subsequent launches use the cached binary, not the network.
            env['HORIZON_DOWNLOAD_BASE'] = 'http://127.0.0.1:1'
            subprocess.run(['node', str(package / 'bin' / 'horizon.cjs'), '--_horizon-self-test'],
                           cwd=cwd, env=env, check=True, timeout=120)
    finally:
        server.shutdown(); server.server_close(); thread.join()
    print('Native npm downloader and offline launcher: PASS')


if __name__ == '__main__':
    main()
