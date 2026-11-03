"""Actual API child + Agent core + local streaming model; no paid model calls."""
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tui.client import HorizonClient
from tui.runtime import LocalService
from tui.app import HorizonApp, PromptInput
from textual.widgets import Markdown, Static


class EndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_failed_initialization_keeps_user_message_and_error_after_restart(self):
        with TemporaryDirectory() as directory:
            env = dict(os.environ, HORIZON_USER_DATA_ROOT=directory, RATE_LIMIT_ENABLED='true',
                       DAILY_CHAT_LIMIT='1', DEFAULT_USER_ID='quota-smoke-user', LLM_PROVIDER='openai_compatible',
                       LLM_MODEL='unconfigured-test', LLM_BASE_URL='http://127.0.0.1:1/v1', LLM_API_KEY='',
                       MCP_ENABLED='false', EMBEDDING_ENABLED='false',
                       LLM_CAPABILITIES_JSON=json.dumps({'max_context_tokens': 128000}))
            from core.usage import UsageTracker
            tracker = UsageTracker(Path(directory)/'usage'/'usage.json')
            tracker.increment('quota-smoke-user', 'chat', 100)
            service = LocalService(env=env)
            try:
                await service.start()
                async with HorizonClient(service.url, api_key=service.api_key) as client:
                    session = await client.create()
                    admitted = await client.prompt(session['id'], 'Explain the project architecture')
                    self.assertIsNone(admitted['remaining'])
                    self.assertEqual(tracker.get_usage('quota-smoke-user')['chat'], 101)
                    async with asyncio.timeout(10):
                        async for event in client.events(session['id']):
                            if event['type'] == 'run.ended':
                                self.assertEqual(event['data']['status'], 'failed')
                                break
                    visible = await client.context(session['id'])
                    self.assertEqual([m['type'] for m in visible], ['user', 'run_status'])
                    await client.configure_connection('saved-local', {'name': 'Saved local',
                        'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'test',
                        'model': 'saved-model', 'context_tokens': 128000})
                    await client.select_model(session['id'], 'saved-local', 'saved-model')
            finally:
                await service.stop()
            restarted = LocalService(env=env)
            try:
                await restarted.start()
                async with HorizonClient(restarted.url, api_key=restarted.api_key) as client:
                    self.assertEqual((await client.sessions())[0]['title'], 'Explain the project architecture')
                    self.assertEqual((await client.context(session['id']))[-1]['status'], 'failed')
                    self.assertIn('saved-local:saved-model', [row['id'] for row in (await client.models())['recent']])
                    app = HorizonApp(client)
                    async with app.run_test(size=(80, 24)) as pilot:
                        await app._open(session['id'])
                        await pilot.pause(.15)
                        self.assertEqual(len(app.query('.user-message')), 1)
                        self.assertIn('Explain the project architecture', str(app.query_one('.user-message').render()))
                        self.assertEqual(str(app.query_one('.run-outcome').render()), app.tr('执行失败'))
                        # The UI stays concise, while the durable record retains
                        # the actionable cause for diagnosis after a restart.
                        recovered = await client.context(session['id'])
                        self.assertEqual(recovered[-1]['error']['code'], 'model_unconfigured')
                        self.assertIn('API Key', recovered[-1]['error']['message'])
                        self.assertFalse(app.busy)
                        await app.action_models()
                        await pilot.pause(.05)
                        self.assertEqual(app.screen.rows[0]['id'], 'saved-local:saved-model')
                        await pilot.press('enter')
                        await pilot.pause(.05)
                        from tui.pickers import ConnectionForm, SelectionPicker
                        self.assertNotIsInstance(app.screen, (ConnectionForm, SelectionPicker))
                    self.assertEqual(await client.active(), [])
            finally:
                await restarted.stop()

    async def test_actual_core_forwards_first_chunk_before_model_finishes(self):
        release = threading.Event()
        requests = []
        class Model(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if not body.get('stream'):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(json.dumps({'id': 'title', 'object': 'chat.completion', 'created': 0,
                        'model': body['model'], 'choices': [{'index': 0, 'message': {'role': 'assistant',
                        'content': '简短问候'}, 'finish_reason': 'stop'}]}).encode())
                    return
                requests.append(body)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                def emit(text, finish=None):
                    item = {'id': 'chat-test', 'object': 'chat.completion.chunk', 'created': 0,
                            'model': 'local-stream-test', 'choices': [
                                {'index': 0, 'delta': {'role': 'assistant', 'content': text}, 'finish_reason': finish}]}
                    self.wfile.write(('data: ' + json.dumps(item) + '\n\n').encode())
                    self.wfile.flush()
                emit('你好，')
                release.wait(12)
                emit('我是 Horizon。', 'stop')
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as directory:
                env = dict(os.environ, HORIZON_USER_DATA_ROOT=directory, RATE_LIMIT_ENABLED='false',
                           LLM_PROVIDER='openai_compatible', LLM_MODEL='local-stream-test',
                           LLM_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',
                           LLM_REASONING_MODE='disabled', MCP_ENABLED='false', EMBEDDING_ENABLED='false',
                           LLM_CAPABILITIES_JSON=json.dumps({'max_context_tokens': 128000,
                                                            'max_input_tokens': 120000, 'max_output_tokens': 8000}),
                           CACHE_ENABLED='false')
                env['LLM_API_KEY'] = 'test'
                service = LocalService(env=env)
                try:
                    await service.start()
                    async with HorizonClient(service.url, api_key=service.api_key) as client:
                        session = await client.create()
                        await client.prompt(session['id'], '你好，请简单打个招呼。', access_mode='read_only')
                        received = []
                        async with asyncio.timeout(20):
                            async for event in client.events(session['id']):
                                received.append(event)
                                if event['type'] == 'llm.delta' and not release.is_set():
                                    self.assertEqual(event['data']['text'], '你好，')
                                    release.set()
                                if event['type'] == 'run.ended':
                                    if event['data']['status'] != 'completed':
                                        from desktop.log_viewer import sanitize_log_text
                                        service._log.seek(0)
                                        print(sanitize_log_text(service._log.read().decode(errors='replace'))[-5000:])
                                    self.assertEqual(event['data']['status'], 'completed')
                                    break
                        self.assertTrue(release.is_set(), 'must receive a delta while the model is still waiting')
                        self.assertTrue(requests[0]['stream'])
                        self.assertNotIn('write_file', [tool['function']['name'] for tool in requests[0].get('tools', [])])
                        for _ in range(30):
                            if (await client.sessions())[0]['title'] == '简短问候':
                                break
                            await asyncio.sleep(.05)
                        self.assertEqual((await client.sessions())[0]['title'], '简短问候')
                        messages = await client.context(session['id'])
                        text = json.dumps(messages, ensure_ascii=False)
                        self.assertIn('你好，我是 Horizon。', text)
                        self.assertEqual(len([e for e in received if e['type'] == 'llm.delta']), 2)
                        app = HorizonApp(client)
                        async with app.run_test(size=(100, 34)) as pilot:
                            await app._open(session['id'])
                            await pilot.pause(.15)
                            answers = list(app.query('#messages Markdown'))
                            self.assertEqual(len(answers), 1)
                            self.assertEqual(answers[0]._markdown, '你好，我是 Horizon。')
                            self.assertIn('你好，请简单打个招呼。', str(app.query_one('.user-message', Static).render()))
                            self.assertRegex(str(app.query_one('.answer-meta', Static).render()),
                                             r'local-stream-test · \d+\.\ds')
                            self.assertFalse(app.busy)
                        await client.configure_connection('local-switch', {
                            'name': 'Local stream', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                            'api_key': 'test', 'model': 'switched-model', 'context_tokens': 128000})
                        await client.select_model(session['id'], 'local-switch', 'switched-model')
                        self.assertEqual((await client.session_model(session['id']))['model'], 'switched-model')
                        cursor = received[-1]['id']
                        await client.prompt(session['id'], '请再打个招呼。', access_mode='full_access')
                        async with asyncio.timeout(20):
                            async for event in client.events(session['id'], after=cursor):
                                if event['type'] == 'run.ended':
                                    self.assertEqual(event['data']['status'], 'completed')
                                    break
                        self.assertEqual(requests[-1]['model'], 'switched-model')
                        self.assertIn('write_file', [tool['function']['name'] for tool in requests[-1].get('tools', [])])
                        canonical = await client.context(session['id'])
                        assistants = [m for m in canonical if m['type'] == 'assistant']
                        self.assertEqual(assistants[-1]['model'], 'switched-model')
                        self.assertEqual(assistants[0]['model'], 'local-stream-test')
                        reopened = HorizonApp(client)
                        async with reopened.run_test(size=(80, 24)) as pilot:
                            await reopened._open(session['id'])
                            self.assertIn('switched-model', str(reopened.query_one('#composer-hint').render()))
                finally:
                    release.set()
                    await service.stop()
        finally:
            release.set()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
