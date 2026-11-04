"""Asynchronous HTTP/SSE client; has no direct Agent or storage imports."""
from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any
import uuid

import httpx

from tui.i18n import translate


class HorizonClient:
    def __init__(self, url: str, *, api_key: str = "", api_key_header: str = "X-API-Key",
                 user_id: str | None = None, project_id: str | None = None, transport=None, language: str = 'en') -> None:
        self.language = language
        self.identity = {key: value for key, value in {"user_id": user_id, "project_id": project_id}.items()
                         if value is not None}
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/"), headers={api_key_header: api_key} if api_key else {},
            timeout=httpx.Timeout(30, connect=5), trust_env=False, transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.http.aclose()

    async def _request(self, method: str, path: str, *, body=None):
        response = await self.http.request(method, path,
                                           params=self.identity if method in {"GET", "DELETE"} else None,
                                           json={**self.identity, **(body or {})} if method not in {"GET", "DELETE"} else None)
        if not response.is_success:
            if response.status_code == 409 and method == 'DELETE' and path.startswith('/session/') and path.count('/') == 2:
                raise RuntimeError(translate('请先中断该会话的任务，再删除会话', self.language))
            if response.status_code in {400, 409} and (path.startswith(('/models','/search')) or path.endswith('/model')):
                detail = response.json().get('detail')
                if isinstance(detail, str):
                    raise RuntimeError(detail)
            raise RuntimeError(translate('服务请求失败（HTTP {status}）', self.language, status=response.status_code))
        payload = response.json()
        if not payload.get("success"):
            # Do not reflect arbitrary upstream exception text or credentials.
            raise RuntimeError(translate('服务未能完成请求，请检查模型配置或本地服务', self.language))
        return payload.get("data", payload)

    async def status(self):
        return await self._request("GET", "/status")

    async def search_config(self):
        return await self._request('GET','/search')

    async def configure_search(self,key:str):
        return await self._request('PUT','/search',body={'api_key':key})

    async def models(self):
        return await self._request('GET', '/models')

    async def connection(self, connection_id: str):
        from urllib.parse import quote
        return await self._request('GET', '/models/connections/' + quote(connection_id, safe=''))

    async def configure_connection(self, connection_id: str, config: dict):
        from urllib.parse import quote
        return await self._request('PUT', '/models/connections/' + quote(connection_id, safe=''), body=config)

    async def session_model(self, session_id: str):
        return await self._request('GET', f'/session/{session_id}/model')

    async def select_model(self, session_id: str, connection_id: str, model: str):
        return await self._request('PUT', f'/session/{session_id}/model', body={'connection_id': connection_id, 'model': model})

    async def set_default_model(self, connection_id: str, model: str):
        return await self._request('PUT', '/models/default', body={'connection_id': connection_id, 'model': model})

    async def sessions(self):
        return await self._request("GET", "/session")

    async def delete_session(self, session_id: str):
        return await self._request("DELETE", f"/session/{session_id}")

    async def active(self):
        return await self._request("GET", "/session/active")

    async def create(self):
        return await self._request("POST", "/session")

    async def session(self, session_id: str):
        return await self._request('GET', f'/session/{session_id}')

    async def pending(self, session_id: str):
        return await self._request('GET', f'/session/{session_id}/pending')

    async def steer_pending(self, session_id: str, message_id: str):
        return await self._request('POST', f'/session/{session_id}/pending/{message_id}/steer')

    async def recall_pending(self, session_id: str, message_id: str):
        return await self._request('DELETE', f'/session/{session_id}/pending/{message_id}')

    async def context(self, session_id: str):
        return await self._request("GET", f"/session/{session_id}/transcript")

    async def prompt(self, session_id: str, text: str, *, message_id: str | None = None, access_mode: str | None = None,
                     delivery: str = 'steer'):
        return await self._request("POST", "/chat", body={
            "session_id": session_id, "message": text, "message_id": message_id or f"msg_{uuid.uuid4().hex}",
            'delivery': delivery,
            **({"access_mode": access_mode} if access_mode else {}),
        })

    async def interrupt(self, session_id: str):
        return await self._request("POST", f"/session/{session_id}/interrupt")

    async def events(self, session_id: str, after: int | None = 0) -> AsyncIterator[dict[str, Any]]:
        async with self.http.stream("GET", f"/session/{session_id}/events",
                                    params={**self.identity, "after": after or 0, "tail": after is None},
                                    timeout=httpx.Timeout(35, connect=5)) as response:
            if not response.is_success:
                raise RuntimeError(translate('事件连接失败（HTTP {status}）', self.language, status=response.status_code))
            kind, data, seq = "", [], None
            async for line in response.aiter_lines():
                if not line:
                    if data:
                        event = {"type": kind or "message", "data": json.loads("\n".join(data))}
                        if seq is not None:
                            event["id"] = seq
                        yield event
                    kind, data, seq = "", [], None
                elif line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("id:"):
                    seq = int(line[3:].strip())
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
