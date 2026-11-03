"""Contracts for the effective instructions, not just unused prompt files."""
import unittest
from types import SimpleNamespace as NS
from core.prompt_pack import build_initial_agent_turn_pack, build_agent_continuation_pack, build_final_responder_pack
from core.simple_fast_path import build_simple_chat_messages
from core.finalization_context_snapshot import build_finalization_context_snapshot

class PromptPolicy(unittest.TestCase):
    def test_shared_policy_reaches_initial_continuation_simple_and_final(self):
        from prompts.agent_prompt import build_agent_policy
        shared=build_agent_policy()
        initial=build_initial_agent_turn_pack(user_input='你是谁',tools=[])
        continuation=build_agent_continuation_pack(user_input='继续',task_state=NS(metadata={}),tools=[],memory_messages=[])
        simple=build_simple_chat_messages(NS(messages=[]),'你是谁')
        snapshot=build_finalization_context_snapshot(user_request='完成了吗',task_state=NS(task_id='x',metadata={}))
        final=build_final_responder_pack(snapshot)
        for messages in [initial.messages,continuation.messages,simple,final.messages]:
            self.assertIn(shared,messages[0]['content'])
        self.assertIn('tools are disabled',final.messages[0]['content'].lower())
        for messages in [initial.messages, continuation.messages, simple, final.messages]:
            policy = messages[0]['content']
            self.assertIn('Use occasional contextual emoji', policy)
            self.assertIn('Never insert emoji into code, commands', policy)
            self.assertNotIn('capability lists, emoji,', policy)

    def test_web_capability_matches_actual_schema_and_offline_scope_is_retained(self):
        tool={'type':'function','function':{'name':'web_search','description':'Search','parameters':{'type':'object','properties':{}}}}
        online=build_initial_agent_turn_pack(user_input='做一个旅行攻略',tools=[tool])
        offline=build_initial_agent_turn_pack(user_input='只用我提供的资料',tools=[])
        self.assertIn('Active web search is available',online.messages[0]['content'])
        self.assertIn('Active web search is not available',offline.messages[0]['content'])
        self.assertIn('offline',offline.messages[0]['content'])
        self.assertIn('/search',offline.messages[0]['content'])

if __name__=='__main__':unittest.main()
