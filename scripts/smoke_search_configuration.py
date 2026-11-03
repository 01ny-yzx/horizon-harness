"""Private search settings, dynamic schemas and TUI configuration."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from scripts.smoke_tui_ui import FakeClient

class SearchSettings(unittest.TestCase):
    def test_key_is_private_persistent_and_immediately_enables_tool_schema(self):
        from core.search_connections import SearchConnections
        from core.web_search_provider import TavilyProvider
        from tools.registry import get_local_tool_schemas
        with TemporaryDirectory() as tmp, patch.dict(os.environ, HORIZON_USER_DATA_ROOT=tmp, TAVILY_API_KEY='', WEB_SEARCH_PROVIDER='auto'):
            config=SearchConnections()
            self.assertFalse(config.configured())
            self.assertNotIn('web_search', [s['function']['name'] for s in get_local_tool_schemas()])
            config.save('private-search-key')
            if os.name != 'nt':
                self.assertEqual(config.path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(config.path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(SearchConnections().key(), 'private-search-key')
            self.assertEqual(TavilyProvider().api_key, 'private-search-key')
            self.assertIn('web_search', [s['function']['name'] for s in get_local_tool_schemas()])
            config.save('')
            self.assertEqual(config.key(), 'private-search-key')
            for invalid in ['key\nsecret', 'x'*5000]:
                with self.assertRaises(ValueError) as raised: config.save(invalid)
                self.assertNotIn(invalid, str(raised.exception))

    def test_http_is_authenticated_and_never_returns_key_in_success_or_errors(self):
        from fastapi import FastAPI, Depends
        from fastapi.testclient import TestClient
        from api.deps import require_api_key
        from api.routes import search
        from config.settings import settings
        with TemporaryDirectory() as tmp, patch.dict(os.environ,HORIZON_USER_DATA_ROOT=tmp,TAVILY_API_KEY=''), \
             patch('api.deps.validate_api_key',side_effect=lambda key:key=='local-api-key'):
            app=FastAPI()
            app.include_router(search.router,dependencies=[Depends(require_api_key)])
            with TestClient(app) as client:
                self.assertEqual(client.get('/search').status_code,401)
                headers={settings.api_key_header:'local-api-key'}
                response=client.put('/search',json={'api_key':'private-search-key'},headers=headers)
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.json()['data']['configured'])
                self.assertFalse(response.json()['data']['credential_verified'])
                self.assertNotIn('private-search-key',response.text)
                self.assertNotIn('private-search-key',client.get('/search',headers=headers).text)
                bad=client.put('/search',json={'api_key':'private-search-key\n'},headers=headers)
                self.assertEqual(bad.status_code,400)
                self.assertNotIn('private-search-key',bad.text)

    def test_credentials_are_blocked_from_reads_searches_and_command_output(self):
        from core.search_connections import SearchConnections
        from core.agent_access_policy import access_mode_scope
        from tools.file_tools import read_file
        from tools.project_tools import search_text
        from core.sandbox import SandboxManager
        with TemporaryDirectory() as tmp, patch.dict(os.environ,HORIZON_USER_DATA_ROOT=tmp,TAVILY_API_KEY=''):
            config=SearchConnections();config.save('private-search-key')
            with access_mode_scope('full_access'):
                direct=read_file(str(config.path))
                self.assertFalse(direct['success'])
                matches=search_text('api_key',tmp)
                self.assertNotIn('private-search-key',str(matches))
                self.assertNotIn('private-search-key',SandboxManager._redact('private-search-key'))

    def test_symlink_cannot_bypass_private_key_guard_when_supported(self):
        from core.search_connections import SearchConnections
        from core.agent_access_policy import access_mode_scope
        from tools.file_tools import read_file
        with TemporaryDirectory() as tmp, patch.dict(os.environ,HORIZON_USER_DATA_ROOT=tmp,TAVILY_API_KEY=''):
            config=SearchConnections();config.save('private-search-key')
            alias=Path(tmp)/'alias.json'
            try:
                alias.symlink_to(config.path)
            except OSError as exc:
                if getattr(exc,'winerror',None)==1314:
                    self.skipTest('Windows user lacks optional symbolic-link privilege; direct credential guards still tested')
                raise
            with access_mode_scope('full_access'):
                self.assertFalse(read_file(str(alias))['success'])

    def test_corrupt_configuration_can_be_replaced_and_validation_errors_hide_inputs(self):
        from core.search_connections import SearchConnections
        from api.routes import search
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        with TemporaryDirectory() as tmp, patch.dict(os.environ,HORIZON_USER_DATA_ROOT=tmp,TAVILY_API_KEY=''):
            config=SearchConnections();config.path.parent.mkdir(parents=True)
            config.path.write_text('{broken')
            app=FastAPI();app.include_router(search.router)
            with TestClient(app) as client:
                status=client.get('/search')
                self.assertEqual(status.status_code,200)
                self.assertFalse(status.json()['data']['configured'])
                bad=client.put('/search',json={'api_key':['private-search-key']})
                self.assertEqual(bad.status_code,422)
                self.assertNotIn('private-search-key',bad.text)
                self.assertEqual(client.put('/search',json={'api_key':'replacement-search-key'}).status_code,200)

class SearchUI(unittest.IsolatedAsyncioTestCase):
    async def test_search_command_masks_key_and_preserves_draft(self):
        from tui.app import HorizonApp, PromptInput
        from textual.widgets import Input
        class Client(FakeClient):
            async def search_config(self): return {'configured':True,'search_available':True}
            async def configure_search(self,key):
                self.saved_search=key
                return {'configured':True,'search_available':True}
        client=Client();app=HorizonApp(client)
        async with app.run_test(size=(60,20)) as pilot:
            app.query_one(PromptInput).load_text('keep this')
            await app.action_search()
            await pilot.pause(.05)
            key=app.screen.query_one(Input)
            self.assertTrue(key.password)
            self.assertEqual(key.value,'')
            self.assertEqual(len(app.screen.query(Input)),1)
            key.value='private-search-key'
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertEqual(client.saved_search,'private-search-key')
            self.assertEqual(app.query_one(PromptInput).text,'keep this')
            self.assertNotIn('private-search-key', str(app.query_one('#composer-hint').render()))
            self.assertEqual(client.sent,[])
            app.query_one(PromptInput).load_text('/sear')
            await pilot.pause(.05)
            self.assertEqual([row['id'] for row in app._command_rows],['search'])

if __name__=='__main__':unittest.main()
