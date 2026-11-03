"""Actual child service lifecycle, random port ownership, and SSE parser checks."""
from __future__ import annotations

import asyncio
from unittest.mock import patch
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx


class RuntimeContracts(unittest.IsolatedAsyncioTestCase):
    async def test_windows_venv_bypasses_redirector_and_preserves_environment(self):
        from tui.runtime import _service_executable
        env = {'API_HOST': '127.0.0.1'}
        with patch('sys.platform', 'win32'), patch('sys.executable', r'C:\app\.venv\Scripts\python.exe'), patch('sys._base_executable', r'C:\Python312\python.exe'):
            executable = _service_executable(env)
        self.assertEqual(executable, r'C:\Python312\python.exe')
        self.assertEqual(env['__PYVENV_LAUNCHER__'], r'C:\app\.venv\Scripts\python.exe')
        self.assertEqual(env['API_HOST'], '127.0.0.1')

    async def test_native_interpreter_keeps_existing_launch_path(self):
        from tui.runtime import _service_executable
        for platform in ('darwin', 'linux', 'win32'):
            env = {}
            with patch('sys.platform', platform), patch('sys.executable', '/python'), patch('sys._base_executable', '/python'):
                self.assertEqual(_service_executable(env), '/python')
            self.assertNotIn('__PYVENV_LAUNCHER__', env)

    async def test_owned_service_starts_with_auth_and_stops_only_itself(self):
        from tui.runtime import LocalService
        from tui.client import HorizonClient
        with TemporaryDirectory() as directory, socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            env = dict(os.environ, HORIZON_USER_DATA_ROOT=directory, LLM_PROVIDER='mock',
                       MCP_ENABLED='false', EMBEDDING_ENABLED='false')
            service = LocalService(env=env)
            try:
                await service.start()
                self.assertNotEqual(service.port, occupied.getsockname()[1])
                process = service.process
                async with HorizonClient(service.url, api_key=service.api_key) as client:
                    self.assertEqual(await client.sessions(), [])
                    session = await client.create()
                    self.assertTrue(session['id'].startswith('ses_'))
                    self.assertEqual(await client.context(session['id']), [])
                async with httpx.AsyncClient(trust_env=False) as raw:
                    response = await raw.get(service.url + '/session')
                    self.assertEqual(response.status_code, 401)
            finally:
                await service.stop()
            self.assertIsNotNone(process.returncode)
            # The unrelated socket remains ours and usable after shutdown.
            self.assertNotEqual(occupied.fileno(), -1)
            await service.stop()

    async def test_service_needs_no_posix_descriptor_inheritance(self):
        from tui.runtime import LocalService
        original = asyncio.create_subprocess_exec
        async def portable_spawn(*args, **kwargs):
            self.assertTrue('pass_fds' not in kwargs, 'POSIX descriptor inheritance is not portable')
            return await original(*args, **kwargs)
        with TemporaryDirectory() as directory:
            env = dict(os.environ, HORIZON_USER_DATA_ROOT=directory, LLM_PROVIDER='mock',
                       MCP_ENABLED='false', EMBEDDING_ENABLED='false')
            service = LocalService(env=env, directory=directory)
            with patch('tui.runtime.asyncio.create_subprocess_exec', side_effect=portable_spawn):
                try:
                    await service.start()
                    self.assertGreater(service.port, 0)
                finally:
                    await service.stop()

    async def test_start_timeout_cleans_up_owned_child_and_reader(self):
        from tui.runtime import LocalService
        from unittest.mock import AsyncMock, Mock
        process = Mock(pid=123, returncode=None, stdout=asyncio.StreamReader())
        process.wait = AsyncMock(return_value=0)
        def terminate():
            process.returncode = 0
        process.terminate.side_effect = terminate
        service = LocalService()
        with patch('tui.runtime.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            spawn.return_value = process
            with self.assertRaises(RuntimeError):
                await service.start(timeout=.03)
        process.terminate.assert_called_once()
        self.assertIsNone(service._reader)
        self.assertIsNone(service._log)

    async def test_sse_parser_preserves_chinese_fragmented_frames_and_cursor(self):
        from tui.client import HorizonClient
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                data = 'id: 7\nevent: llm.delta\ndata: {"text":"你好"}\n\n: ping\n\nid: 8\nevent: run.ended\ndata: {"status":"completed"}\n\n'.encode()
                for i in range(0, len(data), 5):
                    yield data[i:i+5]
        async def handle(request):
            self.assertEqual(request.url.params['after'], '6')
            return httpx.Response(200, stream=Stream(), headers={'content-type': 'text/event-stream'})
        async with HorizonClient('http://local', transport=httpx.MockTransport(handle)) as client:
            items = [e async for e in client.events('ses_test', after=6)]
        self.assertEqual(items, [
            {'id': 7, 'type': 'llm.delta', 'data': {'text': '你好'}},
            {'id': 8, 'type': 'run.ended', 'data': {'status': 'completed'}},
        ])


if __name__ == '__main__':
    unittest.main()
