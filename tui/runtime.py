"""Own exactly one child API process and a private loopback service."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets
import json
import sys
import tempfile

import httpx

from tui.service import READY_PREFIX
from tui.i18n import translate
from core.working_directory import resolve_working_directory


ROOT = Path(__file__).resolve().parents[1]


def _service_executable(env: dict[str, str]) -> str:
    executable = sys.executable
    base = getattr(sys, '_base_executable', executable)
    if (sys.platform == 'win32' and not getattr(sys, 'frozen', False)
            and os.path.normcase(executable) != os.path.normcase(base)):
        # Match CPython multiprocessing: bypass the Windows venv redirector
        # so our process handle belongs to the server, while retaining its venv.
        env['__PYVENV_LAUNCHER__'] = executable
        return base
    return executable


def _service_command(env: dict[str, str]) -> list[str]:
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--_horizon-service']
    return [_service_executable(env), '-P', '-m', 'tui.service']


class LocalService:
    def __init__(self, *, env: dict[str, str] | None = None, language: str = 'en',
                 directory: str | Path | None = None) -> None:
        self.language = language
        self.directory = resolve_working_directory(directory)
        self.env = dict(os.environ if env is None else env)
        self.api_key = secrets.token_urlsafe(32)
        self.port = 0
        self.process: asyncio.subprocess.Process | None = None
        self._log = None
        self._reader = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self, timeout: float = 20) -> None:
        if self.process is not None:
            raise RuntimeError("Local service already started")
        self._log = tempfile.TemporaryFile()
        try:
            env = {**self.env, "API_HOST": "127.0.0.1", "API_AUTH_ENABLED": "true",
                   "API_KEYS": self.api_key, "API_KEY_HEADER": "X-API-Key", "PYTHONUNBUFFERED": "1",
                   "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            if not getattr(sys, 'frozen', False):
                env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(ROOT), env.get('PYTHONPATH', ''))))
            deadline = asyncio.get_running_loop().time() + timeout
            self.process = await asyncio.create_subprocess_exec(
                *_service_command(env), cwd=self.directory, env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **({'creationflags': 0x08000000} if sys.platform == 'win32' else {}),
            )
            ready = asyncio.get_running_loop().create_future()
            self._reader = asyncio.create_task(self._read_output(ready))
            try:
                self.port = await asyncio.wait_for(ready, max(.01, deadline - asyncio.get_running_loop().time()))
            except asyncio.TimeoutError:
                raise RuntimeError(translate('本地服务启动超时', self.language)) from None
            async with httpx.AsyncClient(trust_env=False, timeout=.8) as client:
                while asyncio.get_running_loop().time() < deadline:
                    if self.process.returncode is not None:
                        raise RuntimeError(translate('本地服务启动失败（退出码 {code}）', self.language, code=self.process.returncode))
                    try:
                        response = await client.get(self.url + "/health")
                        if response.is_success and response.json().get("success"):
                            return
                    except (httpx.HTTPError, ValueError):
                        pass
                    await asyncio.sleep(.1)
            raise RuntimeError(translate('本地服务启动超时', self.language))
        except BaseException:
            await self.stop()
            raise

    async def _read_output(self, ready):
        try:
            while line := await self.process.stdout.readline():
                if line.startswith(READY_PREFIX.encode()) and not ready.done():
                    try:
                        message = json.loads(line[len(READY_PREFIX):])
                        port = message['port']
                        if type(port) is not int or not 1 <= port <= 65535 or message.get('pid') != self.process.pid:
                            raise ValueError('Invalid service readiness')
                    except (ValueError, KeyError, TypeError):
                        ready.set_exception(RuntimeError('Invalid local service readiness'))
                    else:
                        ready.set_result(port)
                else:
                    self._log.seek(0, os.SEEK_END)
                    self._log.write(line)
        finally:
            if not ready.done():
                code = await self.process.wait()
                ready.set_exception(RuntimeError(translate('本地服务启动失败（退出码 {code}）', self.language, code=code)))

    async def stop(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=4)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                await process.wait()
        if self._reader is not None:
            if not self._reader.done():
                self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._log is not None:
            self._log.close()
            self._log = None
