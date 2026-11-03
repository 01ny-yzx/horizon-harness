"""Failed admission history and background titles, using isolated databases."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from core.session import SessionService
from core.session_execution import SessionExecution


class Transcript(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'test.db'
        self.execution = SessionExecution(self.path, runner_resolver=lambda _: (_ for _ in ()).throw(ValueError('secret-do-not-store')))
        self.service = SessionService(self.path, execution=self.execution)
        self.session = self.service.create(user_id='u', project_id='p', workspace_id='u/p', directory=self.tmp.name)

    def test_unprocessed_input_survives_reopen_without_promoting_it(self):
        admitted = self.service.prompt(self.session.id, 'Explain this project architecture', resume=False)
        reopened = SessionService(self.path, execution=self.execution)
        visible = reopened.transcript(self.session.id)
        self.assertEqual([(m.id, m.type, m.data['text']) for m in visible],
                         [(admitted.id, 'user', 'Explain this project architecture')])
        self.assertEqual(reopened.context(self.session.id), [])
        self.assertEqual(reopened.get(self.session.id).title, 'Explain this project architecture')
        self.assertEqual(reopened.list()[0].title, 'Explain this project architecture')
        self.service.inputs.promote_steers(self.session.id, 1000)
        self.assertEqual(len(reopened.transcript(self.session.id)), 1)

    def test_transcript_keeps_old_turns_after_context_compaction(self):
        from core.session_projector import COMPACTION_STARTED
        from core.session_message_updater import COMPACTION_ENDED
        self.service.prompt(self.session.id, 'Earlier goal', resume=False)
        self.service.inputs.promote_steers(self.session.id, 1000)
        data = {'session_id': self.session.id, 'message_id': 'msg_compaction_test', 'reason': 'auto', 'timestamp': 2}
        self.service.events.publish(aggregate_id=self.session.id, event_type=COMPACTION_STARTED, data=data)
        self.service.events.publish(aggregate_id=self.session.id, event_type=COMPACTION_ENDED,
                                   data={**data, 'timestamp': 3, 'text': 'summary', 'recent': ''})
        self.assertFalse(any(m.type == 'user' for m in self.service.context(self.session.id)))
        self.assertEqual([m.data['text'] for m in self.service.transcript(self.session.id) if m.type == 'user'], ['Earlier goal'])

    def test_failure_is_durable_and_does_not_save_exception_secrets(self):
        self.service.prompt(self.session.id, 'My question', resume=False)
        with self.assertLogs('core.session_execution', level='ERROR'), self.assertRaises(ValueError):
            self.execution.resume(self.session.id)
        reopened = SessionService(self.path, execution=self.execution)
        visible = reopened.transcript(self.session.id)
        self.assertEqual([m.type for m in visible], ['user', 'run_status'])
        self.assertEqual(visible[-1].data['status'], 'failed')
        self.assertNotIn('secret-do-not-store', str([m.to_dict() for m in visible]))
        self.assertIn('error', visible[-1].data)
        self.service.inputs.promote_steers(self.session.id, 1000)
        self.assertEqual([m.type for m in reopened.transcript(self.session.id)], ['user', 'run_status'])

    def test_model_title_uses_first_input_only_and_custom_titles_are_preserved(self):
        from core.session_title import SessionTitles
        self.service.prompt(self.session.id, 'Explain this project architecture', resume=False)
        self.service.prompt(self.session.id, 'Second question', resume=False)
        titles = SessionTitles(self.service.database)
        with patch('core.session_title.call_llm_chat_result', return_value=NS(message={'content': 'Project architecture'})) as call:
            self.assertTrue(titles.generate(self.session.id, provider=object()))
            self.assertEqual(self.service.get(self.session.id).title, 'Project architecture')
            self.assertIn('Explain this project architecture', str(call.call_args))
            self.assertNotIn('Second question', str(call.call_args))
            self.assertEqual(call.call_args.kwargs['options'].stage, 'session_title')
            self.assertFalse(titles.generate(self.session.id, provider=object()))
            self.assertEqual(call.call_count, 1)

    def test_title_failure_keeps_temporary_title(self):
        from core.session_title import SessionTitles
        self.service.prompt(self.session.id, 'My first goal', resume=False)
        with patch('core.session_title.call_llm_chat_result', side_effect=RuntimeError('private')):
            self.assertFalse(SessionTitles(self.service.database).generate(self.session.id, provider=object()))
        self.assertEqual(self.service.get(self.session.id).title, 'My first goal')


if __name__ == '__main__': unittest.main()
