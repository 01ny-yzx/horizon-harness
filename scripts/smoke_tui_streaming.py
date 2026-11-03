"""Offline streaming contract: deltas precede completion; tools/usage survive."""
from __future__ import annotations

import sys
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers.base import LLMCallOptions, LLMConfig
from providers.openai_compatible import OpenAICompatibleProvider


def chunk(content=None, tools=None, reasoning=None, finish=None, usage=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tools,
                                  reasoning_content=reasoning), finish_reason=finish)], usage=usage)


class StreamingContracts(unittest.TestCase):
    def test_configuration_failure_is_emitted_even_when_runtime_handles_it(self):
        from providers.streaming import live_stream
        events = []
        provider = OpenAICompatibleProvider(LLMConfig(provider='openai_compatible', model='test', api_key=''))
        with live_stream(lambda t, d: events.append((t, d)), threading.Event()):
            with self.assertRaises(Exception):
                provider.chat_result([], [], LLMCallOptions(stage='initial_agent_turn'))
        self.assertIn('llm.failed', [t for t, _ in events])

    def test_chunks_arrive_before_completion_and_keep_tool_fragments_and_usage(self):
        from providers.streaming import live_stream
        events = []
        closed = []

        class Stream:
            def __iter__(self):
                yield chunk('你好', reasoning='private reasoning')
                self_test.assertEqual([d['text'] for t, d in events if t == 'llm.delta'], ['你好'])
                yield chunk('世界', [NS(index=0, id='call_1', type='function',
                                      function=NS(name='read_file', arguments='{"pa'))])
                yield chunk(tools=[NS(index=0, id=None, type=None,
                                     function=NS(name=None, arguments='th":"a.py"}'))], finish='tool_calls')
                yield NS(choices=[], usage=NS(prompt_tokens=10, completion_tokens=4, total_tokens=14))

            def close(self):
                closed.append(True)

        self_test = self
        provider = OpenAICompatibleProvider(LLMConfig(provider='openai_compatible', model='test', api_key='test'))
        payloads = []
        def create(**kwargs):
            payloads.append(kwargs)
            return Stream()
        provider.client = NS(chat=NS(completions=NS(create=create)))
        with live_stream(lambda t, d: events.append((t, d)), threading.Event()):
            result = provider.chat_result([{'role': 'user', 'content': 'hi'}], [], LLMCallOptions(stage='final_answer'))
        self.assertTrue(payloads[0]['stream'])
        self.assertEqual(result.message.content, '你好世界')
        self.assertEqual(result.message.tool_calls[0].function.arguments, '{"path":"a.py"}')
        self.assertEqual(result.message.tool_calls[0].function.name, 'read_file')
        self.assertEqual(result.usage.total_tokens, 14)
        self.assertEqual(result.provider_metadata['reasoning_content'], 'private reasoning')
        self.assertNotIn('private reasoning', repr(events))
        self.assertEqual(closed, [True])

    def test_cancelled_stream_closes_without_returning_success(self):
        from providers.streaming import live_stream
        cancelled = threading.Event()
        closed = []
        class Stream:
            def __iter__(self):
                yield chunk('partial')
                cancelled.set()
                yield chunk('must not appear', finish='stop')
            def close(self):
                closed.append(True)
        provider = OpenAICompatibleProvider(LLMConfig(provider='openai_compatible', model='test', api_key='test'))
        provider.client = NS(chat=NS(completions=NS(create=lambda **_: Stream())))
        events = []
        with live_stream(lambda t, d: events.append((t, d)), cancelled):
            with self.assertRaises(InterruptedError):
                provider.chat_result([], [], LLMCallOptions(stage='final_answer'))
        self.assertTrue(closed)
        self.assertNotIn('must not appear', repr(events))

    def test_truncated_stream_is_failure(self):
        from providers.streaming import live_stream
        class Stream:
            def __iter__(self):
                yield chunk('partial')
            def close(self):
                pass
        provider = OpenAICompatibleProvider(LLMConfig(provider='openai_compatible', model='test', api_key='test'))
        provider.client = NS(chat=NS(completions=NS(create=lambda **_: Stream())))
        events = []
        with live_stream(lambda t, d: events.append((t, d)), threading.Event()):
            with self.assertRaises(Exception) as error:
                provider.chat_result([], [], LLMCallOptions(stage='final_answer'))
        self.assertIn('stream', str(error.exception).lower())
        self.assertIn('llm.failed', [t for t, _ in events])

    def test_cancellation_unblocks_pending_read_as_interruption(self):
        from providers.streaming import live_stream
        cancelled = threading.Event()
        closed = threading.Event()
        class Stream:
            def __iter__(self):
                closed.wait(2)
                raise OSError('socket closed')
                yield
            def close(self):
                closed.set()
        provider = OpenAICompatibleProvider(LLMConfig(provider='openai_compatible', model='test', api_key='test'))
        provider.client = NS(chat=NS(completions=NS(create=lambda **_: Stream())))
        timer = threading.Timer(.03, cancelled.set)
        timer.start()
        try:
            with live_stream(lambda *_: None, cancelled):
                with self.assertRaises(InterruptedError):
                    provider.chat_result([], [], LLMCallOptions(stage='final_answer'))
            self.assertTrue(closed.is_set())
        finally:
            timer.join()

    def test_bounded_journal_reports_gap_and_isolates_sessions(self):
        from core.session_live import LiveJournal
        journal = LiveJournal(capacity=2, sessions=2)
        journal.publish('a', 'llm.delta', {'text': '1'})
        journal.publish('a', 'llm.delta', {'text': '2'})
        journal.publish('a', 'llm.delta', {'text': '3'})
        journal.publish('b', 'llm.delta', {'text': 'other'})
        batch = journal.read('a', 0)
        self.assertTrue(batch.reset)
        self.assertEqual([e.data['text'] for e in batch.events], ['2', '3'])
        self.assertEqual([e.data['text'] for e in journal.read('b', 0).events], ['other'])
        self.assertEqual(journal.read('a', batch.cursor).events, ())
        self.assertTrue(journal.read('a', 999).reset)


if __name__ == '__main__':
    unittest.main()
