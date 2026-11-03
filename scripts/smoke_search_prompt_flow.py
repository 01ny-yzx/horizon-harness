"""Real Agent tool continuation with fake search transport/model, no paid calls.
The test verifies wiring and outgoing messages, not an LLM's semantic compliance.
"""
import json
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


class SearchPromptFlow(unittest.TestCase):
    def test_saved_search_key_and_shared_policy_reach_real_agent_tool_rounds(self):
        from core.agent_factory import create_session_for_workspace, build_agent_for_session
        from core.mcp_registry import MCPRegistry
        from core.mcp_runtime import MCPRuntimeStatus
        from core.session import SessionService
        from core.session_execution import SessionExecution
        from core.session_runner import SessionRunner
        from core.search_connections import SearchConnections
        from core import web_search_provider
        from providers.mock import MockProvider, assistant_message
        from prompts.agent_prompt import build_agent_policy
        calls=[]
        class FakeTavily:
            def __init__(self,api_key):
                self_test.assertEqual(api_key,'private-search-key')
            def search(self,**kwargs):
                calls.append(kwargs['query'])
                return {'results':[{'title':'Official travel guide','url':'https://example.com/guide',
                                    'content':'Official opening hours and transport details'}]}
        def tool(name,args,id):
            return NS(id=id,type='function',function=NS(name=name,arguments=json.dumps(args)))
        provider=MockProvider(responses=[
            assistant_message('',[tool('web_search',{'query':'travel official opening hours'},'search_1')]),
            assistant_message('',[tool('fetch_url',{'url':'https://example.com/guide'},'fetch_1')]),
            assistant_message('路线和开放时间见[官方资料](https://example.com/guide)。')])
        self_test=self
        with TemporaryDirectory() as tmp, patch.dict(os.environ,HORIZON_USER_DATA_ROOT=tmp,
                TAVILY_API_KEY='',WEB_SEARCH_PROVIDER='auto',MCP_ENABLED='false',EMBEDDING_ENABLED='false'), \
                patch.object(web_search_provider,'TavilyClient',FakeTavily):
            SearchConnections().save('private-search-key')
            _,session=create_session_for_workspace('search-smoke','project')
            agent=build_agent_for_session(session,llm_factory=lambda:provider,
                runtime_manager_factory=lambda:NS(snapshot=lambda:(MCPRegistry(),MCPRuntimeStatus(enabled=False,config_path=''))))
            fetched=[]
            def fetch(url,**kwargs):
                fetched.append(url)
                return {'success':True,'data':{'url':url,'content':'Official opening hours and transport details', 'format':'markdown'}}
            agent.tools['fetch_url']=fetch
            execution=SessionExecution(database=agent.session_store.database,runner_resolver=lambda _:SessionRunner(
                agent._run_session_work_item,database=agent.session_store.database))
            service=SessionService(database=agent.session_store.database,execution=execution)
            service.prompt(session.id,'帮我做个旅行攻略',resume=False,access_mode='read_only')
            execution.resume(session.id)
            self.assertEqual(calls,['travel official opening hours'])
            self.assertEqual(fetched,['https://example.com/guide'])
            self.assertEqual(len(provider.calls),3)
            for call in provider.calls:
                self.assertIn(build_agent_policy(),call['messages'][0]['content'])
                self.assertNotIn('private-search-key',json.dumps(call['messages'],ensure_ascii=False))
            text=json.dumps([m.to_dict() for m in service.transcript(session.id)],ensure_ascii=False)
            self.assertIn('官方资料',text)
            self.assertIn('https://example.com/guide',text)
            self.assertNotIn('private-search-key',text)

if __name__=='__main__':unittest.main()
