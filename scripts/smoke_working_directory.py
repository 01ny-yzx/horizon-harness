"""Launch-directory and durable-session path contracts; no paid requests."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class WorkingDirectoryContracts(unittest.TestCase):
    def test_directory_validation_and_scoped_tools(self):
        from core.working_directory import resolve_working_directory, working_directory_context
        from core.path_grounding import build_path_context
        from tools.file_tools import read_file
        from tools.project_tools import find_files
        from tools.git_tools import git_is_repo
        from core.command_execution_context import get_host_command_context
        with TemporaryDirectory() as root:
            a = Path(root) / '项目 A'; a.mkdir()
            b = Path(root) / 'B'; b.mkdir()
            (a / 'marker.txt').write_text('directory A')
            subprocess.run(['git', 'init', '-q', str(a)], check=True)
            self.assertEqual(resolve_working_directory('项目 A', base=Path(root)), a.resolve())
            for invalid in (a / 'missing', a / 'marker.txt'):
                with self.assertRaises((ValueError, OSError)):
                    resolve_working_directory(invalid)
            cwd = Path.cwd()
            with working_directory_context(a):
                self.assertEqual(build_path_context().project_root, a.resolve())
                self.assertTrue(read_file('marker.txt')['success'])
                self.assertTrue(find_files('marker.txt')['success'])
                self.assertEqual(git_is_repo()['data']['repo_root'], str(a.resolve()))
                self.assertEqual(get_host_command_context().project_root, a.resolve())
                with working_directory_context(b):
                    self.assertFalse(read_file('marker.txt')['success'])
                    self.assertFalse(git_is_repo()['data']['is_repo'])
                self.assertTrue(read_file('marker.txt')['success'])
            self.assertEqual(Path.cwd(), cwd)
            self.assertEqual(build_path_context().project_root, cwd)

    def test_git_commit_safety_gate_is_preserved(self):
        from tools.git_tools import git_commit
        with patch.dict(os.environ, {'AGENT_ALLOW_GIT_COMMIT': 'false'}):
            result = git_commit('test')
        self.assertFalse(result['success'])
        self.assertIn('disabled', result['error'])

    def test_launcher_imports_outside_source_and_rejects_invalid_arguments(self):
        launcher = Path(__file__).resolve().with_name('horizon.py')
        with TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, str(launcher), '--help'], cwd=root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('directory', result.stdout)
            result = subprocess.run([sys.executable, str(launcher), root, '--url', 'http://localhost:1'],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('remote service', result.stderr)

    def test_session_execution_binds_saved_directory(self):
        from core.loop import AgentLoop
        from core.path_grounding import build_path_context
        from types import SimpleNamespace as NS
        import threading
        with TemporaryDirectory() as root:
            captured = []
            def run(*args, **kwargs):
                from core.state import TaskState
                captured.append(build_path_context().project_root)
                self.assertEqual(Path(TaskState('t', 'hello', 'simple', None, 'start', []).start_cwd), Path(root).resolve())
            agent = NS(_session_info=NS(directory=root), workspace=NS(user_id='u', project_id='p'),
                       _run_request=run)
            AgentLoop._run_session_work_item(agent, 'hello', None, threading.Event())
            self.assertEqual(captured, [Path(root).resolve()])
            self.assertIsNone(agent._session_cancelled)


class ServiceWorkingDirectoryContracts(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_directory_contexts_do_not_change_process_cwd(self):
        from core.working_directory import working_directory_context, get_working_directory
        with TemporaryDirectory() as root:
            paths = [Path(root) / name for name in ('A', 'B')]
            for path in paths:
                path.mkdir()
            before = Path.cwd()
            async def worker(path):
                with working_directory_context(path):
                    await asyncio.sleep(.01)
                    return get_working_directory()
            self.assertEqual(await asyncio.gather(*(worker(p) for p in paths)),
                             [p.resolve() for p in paths])
            self.assertEqual(Path.cwd(), before)

    async def test_child_starts_outside_source_and_session_survives_restart(self):
        from tui.runtime import LocalService
        from tui.client import HorizonClient
        with TemporaryDirectory() as root:
            base = Path(root)
            a = base / '工作 A'; a.mkdir()
            b = base / '工作 B'; b.mkdir()
            # User project module names must not shadow the application packages.
            for directory in (a, b):
                (directory / 'api.py').write_text("raise RuntimeError('user api imported')")
                (directory / 'core.py').write_text("raise RuntimeError('user core imported')")
            env = dict(os.environ, HORIZON_USER_DATA_ROOT=str(base / 'data'),
                       LLM_PROVIDER='mock', MCP_ENABLED='false', EMBEDDING_ENABLED='false')
            session = None
            for directory in (a, b):
                service = LocalService(env=env, directory=directory)
                try:
                    await service.start()
                    async with HorizonClient(service.url, api_key=service.api_key) as client:
                        if session is None:
                            session = await client.create()
                            self.assertEqual(Path(session['directory']), a.resolve())
                        else:
                            sessions = await client.sessions()
                            self.assertEqual(Path(sessions[0]['directory']), a.resolve())
                            admitted = await client._request('POST', '/chat', body={
                                'session_id': session['id'], 'message': 'saved directory question',
                                'message_id': 'msg_directory_restore', 'resume': False})
                            self.assertEqual(admitted['session_id'], session['id'])
                            transcript = await client.context(session['id'])
                            self.assertIn('saved directory question', [m.get('text') for m in transcript])
                            refused = await client.http.post('/chat', json={
                                'session_id': session['id'], 'message': 'wrong owner',
                                'user_id': 'another_user', 'resume': False})
                            self.assertFalse(refused.json()['success'])
                            self.assertEqual(len(await client.context(session['id'])), len(transcript))
                            new = await client.create()
                            self.assertEqual(Path(new['directory']), b.resolve())
                finally:
                    await service.stop()


if __name__ == '__main__':
    unittest.main()
