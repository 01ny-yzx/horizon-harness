"""Offline verification of the actual executable, using disposable user data."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    original_cwd = Path.cwd().resolve()
    with tempfile.TemporaryDirectory(prefix='horizon-bundle-check-') as temp:
        root = Path(temp)
        os.environ.update(HORIZON_USER_DATA_ROOT=str(root / 'data'), LLM_PROVIDER='mock',
                          LLM_MODEL='mock', LLM_API_KEY='bundle-check-placeholder',
                          LLM_BASE_URL='http://127.0.0.1:1/v1', EMBEDDING_ENABLED='false',
                          MCP_ENABLED='false', WEB_SEARCH_PROVIDER='disabled',
                          API_AUTH_ENABLED='false', LLM_CAPABILITIES_JSON=json.dumps({'max_context_tokens': 128000}))
        from tui.dependencies import missing_runtime_modules
        from core.python_runtime import python_command
        from core.model_connections import ModelConnections
        from core.model_default import GlobalModelSelection
        from providers.model_catalog import SNAPSHOT_PATH
        assert not missing_runtime_modules(), missing_runtime_modules()
        assert json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))['models']
        assert Path(__file__).with_name('horizon.tcss').is_file()
        connections = ModelConnections()
        chosen = GlobalModelSelection(connections).select(connections.default_id, 'mock')
        assert GlobalModelSelection().get() == chosen
        script = root / '脚本 test.py'
        script.write_text("import json, sqlite3, ssl\nprint(json.dumps({'message':'你好 日本語'},ensure_ascii=False))\n", encoding='utf-8')
        result = subprocess.run(python_command(str(script)), capture_output=True, encoding='utf-8', timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['message'] == '你好 日本語'
        # Include these modules in the bundle and verify real frozen workers.
        from concurrent.futures import ProcessPoolExecutor
        script.write_text("from concurrent.futures import ProcessPoolExecutor\nif __name__ == '__main__':\n with ProcessPoolExecutor(1) as pool:\n  print(list(pool.map(abs, [-2]))[0])\n", encoding='utf-8')
        workers = subprocess.run(python_command(str(script)), capture_output=True, encoding='utf-8', timeout=30)
        assert workers.returncode == 0, workers.stderr
        assert workers.stdout.strip() == '2'

        async def verify():
            import httpx
            # Construct the actual optional SOCKS transport without network IO.
            async with httpx.AsyncClient(proxy='socks5h://127.0.0.1:9', trust_env=False):
                pass
            from tui.runtime import LocalService
            from tui.client import HorizonClient
            from tui.app import HorizonApp
            service = LocalService(directory=original_cwd)
            try:
                await service.start(timeout=45)
                async with httpx.AsyncClient(trust_env=False) as raw:
                    assert (await raw.get(service.url + '/session')).status_code == 401
                async with HorizonClient(service.url, api_key=service.api_key) as client:
                    session = await client.create()
                    assert Path(session['directory']).samefile(original_cwd), (session['directory'], str(original_cwd))
                    assert await client.session_model(session['id']) == chosen
                    app = HorizonApp(client, local_directory=original_cwd)
                    async with app.run_test(size=(100, 30)) as pilot:
                        await pilot.pause(.2)
                        assert app._prompt_widget.is_mounted
                        await app._open(session['id'])
                        assert app._selected_model == 'mock'
            finally:
                await service.stop()
            assert service.process.returncode is not None
        asyncio.run(verify())
    assert Path.cwd().samefile(original_cwd)
    print('Horizon bundle check: PASS (resources, Python tools, local API/auth, model settings, UI, shutdown)')
