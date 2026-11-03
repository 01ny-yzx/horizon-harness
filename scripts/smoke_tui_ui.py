"""Headless terminal checks: input, incremental display, lifecycle, no live models."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeClient:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.messages = []
        self.interrupted = False
        self.sent = []
        self.model_choices = {}
        self.configured = []
        self.prompt_modes = []
        self.default_model = {'connection_id': 'test', 'model': 'offline-test'}
    async def status(self):
        return {'llm': {'model': 'offline-test'}}
    async def create(self):
        self.model_choices['ses_ui'] = dict(self.default_model)
        return {'id': 'ses_ui', 'directory': '/project', 'title': 'test'}
    async def prompt(self, session_id, text, *, message_id=None, access_mode=None):
        self.prompt_modes.append(access_mode)
        self.sent.append(text)
        self.messages.append({'id': message_id or 'u1', 'type': 'user', 'text': text})
        return {'session_id': session_id}
    async def context(self, _):
        return self.messages
    async def events(self, _, after=0):
        while True:
            yield await self.queue.get()
    async def interrupt(self, _):
        self.interrupted = True
        await self.queue.put({'type': 'run.ended', 'data': {'status': 'interrupted'}})
    async def sessions(self):
        return [{'id': 'ses_ui', 'title': 'test', 'directory': '/project'}]
    async def active(self):
        return []
    async def models(self):
        return {'current': dict(self.default_model), 'models': [
            {'id': 'test:offline-test', 'connection_id': 'test', 'model': 'offline-test', 'provider': 'Test', 'configured': True},
            {'id': 'test:other-model', 'connection_id': 'test', 'model': 'other-model', 'provider': 'Test', 'configured': True}]}
    async def connection(self, key):
        return {'id': key, 'name': 'Test', 'base_url': 'https://example.invalid/v1', 'configured': True}
    async def configure_connection(self, key, config):
        self.configured.append((key, config))
        return {'id': key, 'configured': True}
    async def session_model(self, session_id):
        return self.model_choices.get(session_id, {'connection_id': 'test', 'model': 'offline-test'})
    async def select_model(self, session_id, connection_id, model):
        self.model_choices[session_id] = {'connection_id': connection_id, 'model': model}
        return self.model_choices[session_id]
    async def set_default_model(self, connection_id, model):
        self.default_model = {'connection_id': connection_id, 'model': model}
        return dict(self.default_model)


class UIContracts(unittest.IsolatedAsyncioTestCase):
    async def test_exit_closes_tui_without_admitting_a_message_even_when_busy(self):
        from tui.app import HorizonApp, PromptInput
        for busy in (False, True):
            client = FakeClient()
            app = HorizonApp(client)
            async with app.run_test(size=(80, 24)) as pilot:
                app.busy = busy
                app.query_one(PromptInput).load_text(' exit ')
                await pilot.press('enter')
                await pilot.pause(.05)
                self.assertFalse(app.is_running)
                self.assertEqual(client.sent, [])
                self.assertEqual(client.messages, [])

    async def test_escape_closes_inline_commands_before_interrupting_a_run(self):
        from tui.app import HorizonApp
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app.session_id = 'ses_ui'
            app._set_phase('正在回答', busy=True)
            await pilot.press('/')
            await pilot.pause(.05)
            self.assertTrue(app.query_one('#command-suggestions').display)
            await pilot.press('escape')
            self.assertFalse(app.query_one('#command-suggestions').display)
            self.assertTrue(app.busy)
            self.assertFalse(app.client.interrupted)
            await pilot.press('escape')
            await pilot.pause(.05)
            self.assertTrue(app.client.interrupted)

    async def test_late_connection_fetch_cannot_open_form_in_a_changed_session(self):
        from tui.app import HorizonApp
        from textual.screen import ModalScreen
        for busy in (False, True):
            class SlowConnection(FakeClient):
                def __init__(self):
                    super().__init__()
                    self.entered, self.release = asyncio.Event(), asyncio.Event()
                async def connection(self, key):
                    self.entered.set()
                    await self.release.wait()
                    return await super().connection(key)
            client = SlowConnection()
            app = HorizonApp(client)
            async with app.run_test(size=(80, 24)) as pilot:
                app.session_id = 'old'
                task = app._spawn(app._model_picked({'id': 'test:model', 'connection_id': 'test', 'configured': False}))
                await asyncio.wait_for(client.entered.wait(), 1)
                if busy:
                    app._set_phase('正在回答', busy=True)
                else:
                    await app.action_new_session()
                client.release.set()
                await task
                self.assertNotIsInstance(app.screen, ModalScreen)
                self.assertEqual(client.configured, [])

    async def test_known_provider_only_needs_key_and_reuses_saved_key(self):
        from tui.app import HorizonApp, PromptInput
        from tui.pickers import ConnectionForm
        from textual.widgets import Input
        client = FakeClient()
        app = HorizonApp(client)
        async with app.run_test(size=(80, 24)) as pilot:
            row = {'id': 'test:offline-test', 'connection_id': 'test', 'model': 'offline-test', 'configured': False}
            connection = {'name': 'Test', 'base_url': 'https://example.invalid/v1', 'configured': False,
                          'model_contexts': {'offline-test': 128000}}
            app.push_screen(ConnectionForm(client, row, connection))
            await pilot.pause(.05)
            self.assertEqual(len(app.screen.query(Input)), 1)
            await pilot.press('enter')
            self.assertEqual(client.configured, [])
            self.assertIsInstance(app.screen, ConnectionForm)
            app.screen.query_one(Input).value = 'test-key'
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertNotIsInstance(app.screen, ConnectionForm)
            self.assertEqual(client.configured[-1], ('test', {'name': 'Test',
                'base_url': 'https://example.invalid/v1', 'api_key': 'test-key',
                'model': 'offline-test', 'context_tokens': 128000}))
            app.push_screen(ConnectionForm(client, row, {**connection, 'configured': True}))
            await pilot.pause(.05)
            self.assertEqual(app.screen.query_one(Input).value, '')
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertEqual(client.configured[-1][1]['api_key'], '')
            self.assertEqual(client.sent, [])

    async def test_provider_menu_avoids_duplicate_regional_brands(self):
        from tui.model_menu import visible_provider_ids
        groups = {key: [{'configured': False}] for key in ['alibaba-cn', 'alibaba', 'agentrouter']}
        self.assertEqual(visible_provider_ids(groups, 'deepseek'), {'alibaba-cn'})
        self.assertEqual(visible_provider_ids(groups, 'alibaba'), {'alibaba'})
        groups['alibaba'][0]['configured'] = True
        self.assertEqual(visible_provider_ids(groups, 'deepseek'), {'alibaba'})
        groups['agentrouter'][0]['configured'] = True
        self.assertEqual(visible_provider_ids(groups, 'deepseek'), {'alibaba', 'agentrouter'})

    async def test_provider_menu_only_lists_featured_brands_and_saved_connections(self):
        from tui.app import HorizonApp
        from tui.pickers import ConnectionForm, SelectionPicker
        from textual.widgets import Input
        class CatalogClient(FakeClient):
            async def models(self):
                return {'current': {'connection_id': 'deepseek', 'model': 'deepseek-flash'}, 'models': [
                    {'id': f'{provider}:{model}', 'connection_id': provider, 'model': model,
                     'provider': name, 'configured': configured}
                    for provider, model, name, configured in [
                        ('deepseek', 'deepseek-flash', 'deepseek', False),
                        ('deepseek', 'deepseek-pro', 'deepseek', False),
                        ('openai', 'gpt-test', 'openai', False),
                        ('alibaba-cn', 'qwen-test', 'Alibaba (China)', False),
                        ('agentrouter', 'extra', 'AgentRouter', False),
                        ('302ai', 'extra', '302.AI', False),
                        ('saved-custom', 'my-model', 'My local service', True)]]}
        client = CatalogClient()
        app = HorizonApp(client)
        async with app.run_test(size=(80, 24)) as pilot:
            await app.action_models()
            await pilot.pause(.05)
            rows = app.screen.rows
            self.assertEqual([row.get('connection_id', row['id']) for row in rows],
                             ['openai', 'deepseek', 'alibaba-cn', 'saved-custom', '__custom__'])
            self.assertIn('DeepSeek', rows[1]['label'])
            self.assertIn('OpenAI', rows[0]['label'])
            self.assertIn('Qwen', rows[2]['label'])
            self.assertNotIn('Alibaba (China)', rows[2]['label'])
            self.assertEqual(len(app.screen.query(Input)), 0)
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertIsInstance(app.screen, SelectionPicker)
            self.assertEqual([row['model'] for row in app.screen.rows], ['deepseek-flash', 'deepseek-pro'])
            self.assertIn('DeepSeek', app.screen.title_text)
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertIsInstance(app.screen, ConnectionForm)
            self.assertEqual(len(app.screen.query(Input)), 1)
            app.screen.query_one(Input).value = 'test-key'
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertEqual(client.configured[0][0], 'deepseek')
            self.assertEqual(client.default_model['model'], 'deepseek-flash')

    async def test_selection_lists_have_no_search_or_result_limit(self):
        from tui.app import HorizonApp
        from tui.pickers import SelectionPicker
        from textual.widgets import Input, OptionList
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(60, 20)) as pilot:
            rows = [{'id': str(i), 'label': f'Model {i}'} for i in range(150)]
            app.push_screen(SelectionPicker('选择模型', rows))
            await pilot.pause(.05)
            self.assertEqual(len(app.screen.query(Input)), 0)
            self.assertEqual(app.screen.query_one(OptionList).option_count, 150)
            self.assertLessEqual(app.screen.query_one('.picker-hint').region.bottom,
                                 app.screen.query_one('#selection-picker').content_region.bottom)
            await pilot.press('end')
            self.assertEqual(app.screen.query_one(OptionList).highlighted, 149)
            await pilot.press('home')
            self.assertEqual(app.screen.query_one(OptionList).highlighted, 0)
            await pilot.press('escape')
            await app.action_models()
            await pilot.pause(.05)
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertIn('Select model', app.screen.title_text)
            await pilot.press('escape')
            await pilot.pause(.05)
            self.assertEqual(app.screen.title_text, 'Select provider')

    async def test_custom_connection_form_saves_then_selects_and_preserves_draft(self):
        from tui.app import HorizonApp, PromptInput
        from textual.widgets import Input
        client = FakeClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one(PromptInput).load_text('my draft')
            await app._model_picked({'id': '__custom__', 'configured': False})
            await pilot.pause(.05)
            values = {'id': 'local', 'name': 'Local', 'url': 'http://localhost:9000/v1',
                      'key': 'test-secret', 'model': 'my-model', 'context': '128000'}
            for name, value in values.items():
                app.screen.query_one('#connection-' + name, Input).value = value
            await pilot.click('#connection-save')
            await pilot.pause(.08)
            self.assertEqual(client.configured[0][0], 'local')
            self.assertEqual(client.configured[0][1]['api_key'], 'test-secret')
            self.assertEqual(client.default_model['model'], 'my-model')
            self.assertEqual(app.query_one(PromptInput).text, 'my draft')
            self.assertEqual(client.sent, [])

    async def test_slash_models_selects_without_sending_command_to_agent(self):
        from tui.app import HorizonApp, PromptInput
        from tui.pickers import SelectionPicker
        from textual.widgets import Input
        client = FakeClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('/')
            await pilot.pause(.08)
            self.assertNotIsInstance(app.screen, SelectionPicker)
            self.assertTrue(app.query_one(PromptInput).has_focus)
            self.assertTrue(app.query_one('#command-suggestions').display)
            await pilot.press('m', 'o')
            await pilot.press('enter')
            await pilot.pause(.08)
            self.assertIsInstance(app.screen, SelectionPicker)
            self.assertEqual(len(app.screen.query(Input)), 0)
            await pilot.press('enter')
            await pilot.pause(.05)
            await pilot.press('down')
            await pilot.pause(.05)
            await pilot.press('enter')
            await pilot.pause(.08)
            self.assertEqual(client.sent, [])
            self.assertEqual(client.default_model['model'], 'other-model')
            self.assertEqual(app._selected_model, 'other-model')
            self.assertIsNone(app.session_id)
            self.assertEqual(app.query_one(PromptInput).text, '')
            # Model selection creates no session; the first message starts its watcher.
            app.query_one(PromptInput).load_text('hello')
            await pilot.press('enter')
            await pilot.pause(.08)
            self.assertEqual(client.sent, ['hello'])
            self.assertIsNotNone(app._watcher)

    async def test_notifications_use_composer_footer_then_restore_latest_model(self):
        from tui.app import HorizonApp, PromptInput
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.05)
            app.query_one(PromptInput).load_text('keep my draft')
            app.notify('语言已更新', timeout=.3)
            await pilot.pause(.05)
            hint = app.query_one('#composer-hint')
            self.assertIn('✓ Language updated', str(hint.render()))
            self.assertEqual(len(app._notifications), 0)
            self.assertTrue(app.query_one(PromptInput).has_focus)
            app._set_model_hint('new-model')
            self.assertIn('Language updated', str(hint.render()))
            await pilot.pause(.35)
            self.assertIn('new-model', str(hint.render()))
            self.assertEqual(app.query_one(PromptInput).text, 'keep my draft')

    async def test_new_notification_replaces_old_timer_and_errors_use_exclamation(self):
        from tui.app import HorizonApp
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(45, 20)) as pilot:
            app.notify('old message', timeout=.1)
            await pilot.pause(.04)
            app.notify('保存失败，请检查本地服务', severity='error', timeout=.3)
            await pilot.pause(.12)
            hint = app.query_one('#composer-hint')
            self.assertIn('! Could not save', str(hint.render()))
            self.assertEqual(len(app._notifications), 0)
            self.assertEqual(hint.content_region.height, 1)
            self.assertLessEqual(hint.region.right, 45)
            await pilot.pause(.25)
            self.assertNotIn('Could not save', str(hint.render()))

    async def test_keyboard_selection_is_visible_in_rendered_command_and_picker_rows(self):
        from tui.app import HorizonApp, PromptInput
        from tui.pickers import SelectionPicker
        from textual.widgets import OptionList
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press('/')
            await pilot.pause(.05)
            menu = app.query_one('#command-suggestions', OptionList)
            def highlighted_rows(widget):
                return [y for y in range(widget.content_size.height)
                        if any(segment.style and segment.style.reverse for segment in widget.render_line(y))]
            self.assertEqual(highlighted_rows(menu), [0])
            await pilot.press('down')
            await pilot.pause(.05)
            self.assertEqual(highlighted_rows(menu), [1])
            self.assertTrue(app.query_one(PromptInput).has_focus)
            await pilot.press('up')
            await pilot.pause(.05)
            self.assertEqual(highlighted_rows(menu), [0])
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertIsInstance(app.screen, SelectionPicker)
            options = app.screen.query_one(OptionList)
            self.assertEqual(highlighted_rows(options), [0])
            await pilot.press('down')
            await pilot.pause(.05)
            self.assertEqual(highlighted_rows(options), [1])

    async def test_inline_commands_filter_navigate_and_escape_in_the_editor(self):
        from tui.app import HorizonApp, PromptInput
        from textual.screen import ModalScreen
        from textual.widgets import OptionList
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            field = app.query_one(PromptInput)
            await pilot.press('/')
            await pilot.pause(.08)
            self.assertNotIsInstance(app.screen, ModalScreen)
            options = app.query_one('#command-suggestions', OptionList)
            self.assertEqual(options.option_count, 6)
            self.assertEqual(options.region.bottom, app.query_one('#composer').region.y)
            await pilot.press('m')
            await pilot.pause(.05)
            self.assertEqual([row['id'] for row in app._command_rows], ['models'])
            self.assertEqual(options.option_count, 1)
            self.assertEqual(field.text, '/m')
            self.assertTrue(field.has_focus)
            await pilot.press('backspace')
            await pilot.pause(.05)
            self.assertEqual(options.option_count, 6)
            await pilot.press('down')
            self.assertEqual(options.highlighted, 1)
            self.assertEqual(field.text, '/')
            await pilot.press('escape')
            self.assertFalse(options.display)
            self.assertEqual(field.text, '/')
            await pilot.press('h')
            await pilot.pause(.05)
            self.assertTrue(options.display)
            self.assertEqual(options.option_count, 1)
            await pilot.resize_terminal(45, 20)
            await pilot.pause(.12)
            self.assertTrue(field.has_focus)
            self.assertEqual(app.cursor_position, field.cursor_screen_offset)
            self.assertLessEqual(options.region.right, 45)
            await pilot.press('enter')
            await pilot.pause(.05)
            self.assertIsInstance(app.screen, ModalScreen)
            self.assertEqual(field.text, '')
            self.assertEqual(app.client.sent, [])

    async def test_history_selection_escape_preserves_draft_and_configuration_masks_key(self):
        from tui.app import HorizonApp, PromptInput
        from tui.pickers import SelectionPicker, ConnectionForm
        from textual.widgets import Input
        client = FakeClient()
        app = HorizonApp(client)
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(PromptInput).load_text('keep this draft')
            await app.action_history()
            await pilot.pause(.05)
            self.assertIsInstance(app.screen, SelectionPicker)
            await pilot.press('escape')
            await pilot.pause(.08)
            self.assertEqual(app.query_one(PromptInput).text, 'keep this draft')
            await app.action_models()
            await pilot.pause(.05)
            await pilot.press('ctrl+e')
            await pilot.pause(.05)
            self.assertIsInstance(app.screen, ConnectionForm)
            key = app.screen.query_one('#connection-key', Input)
            self.assertEqual(len(app.screen.query(Input)), 1)
            self.assertTrue(key.password)
            self.assertEqual(key.value, '')
            key.focus()
            await pilot.pause(.05)
            await pilot.resize_terminal(45, 20)
            await pilot.pause(.12)
            self.assertLessEqual(app.screen.query_one('#connection-form').region.bottom, 20)
            self.assertTrue(key.content_region.contains(*app.cursor_position))
            await pilot.press('escape')
            await pilot.pause(.08)
            self.assertEqual(app.query_one(PromptInput).text, 'keep this draft')
            app._set_phase('正在回答', busy=True)
            await app.action_models()
            self.assertNotIsInstance(app.screen, SelectionPicker)

    async def test_thinking_follows_user_and_completed_answer_shows_model_and_total_time(self):
        from tui.app import HorizonApp
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 30)) as pilot:
            app._show_chat()
            user = {'type': 'user', 'text': '检查这个项目', 'time': {'created': 1000}}
            await app._render_context([user])
            await app.handle_event({'type': 'run.started', 'data': {}})
            await pilot.pause(.05)
            activity = app.query_one('#activity')
            self.assertEqual(activity.parent.id, 'transcript')
            self.assertGreaterEqual(activity.region.y, app.query_one('.user-message').region.bottom)
            self.assertLessEqual(activity.region.y - app.query_one('.user-message').region.bottom, 2)
            await app.handle_event({'type': 'llm.delta', 'data': {'text': '检查结果'}})
            await pilot.pause(.05)
            self.assertGreaterEqual(activity.region.y, app.query_one('#live-output').region.bottom)
            app._set_phase('已完成', busy=False)
            answer = {'type': 'assistant', 'model': 'deepseek-flash', 'finish': 'stop',
                      'time': {'created': 2500, 'completed': 4200},
                      'content': [{'type': 'text', 'text': '检查结果'}]}
            await app._render_context([user, answer])
            self.assertIn('deepseek-flash · 3.2s', str(app.query_one('.answer-meta').render()))
            await app._render_context([user, answer])
            self.assertEqual(len(app.query('.answer-meta')), 1)
            self.assertFalse(activity.display)
            app._set_phase('正在重新连接', busy=True)
            await app.handle_event({'type': 'session.snapshot',
                                    'data': {'messages': [user, answer], 'active': False}})
            self.assertEqual(len(app.query('.answer-meta')), 1)
            self.assertFalse(app.busy)

    async def test_user_message_is_visible_before_create_or_admission_returns(self):
        from tui.app import HorizonApp, PromptInput
        class SlowClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.creating = asyncio.Event()
                self.allow_create = asyncio.Event()
                self.admitting = asyncio.Event()
                self.allow_prompt = asyncio.Event()
            async def create(self):
                self.creating.set()
                await self.allow_create.wait()
                return await super().create()
            async def prompt(self, session_id, text, *, message_id=None, access_mode=None):
                await super().prompt(session_id, text, message_id=message_id, access_mode=access_mode)
                self.admitting.set()
                await self.allow_prompt.wait()
                return {'session_id': session_id, 'message_id': message_id}
        client = SlowClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 30)) as pilot:
            field = app.query_one(PromptInput)
            field.load_text('立即显示这条消息')
            await pilot.press('enter')
            await asyncio.wait_for(client.creating.wait(), 1)
            self.assertEqual(len(app.query('.user-message')), 1)
            self.assertIn('立即显示这条消息', str(app.query_one('.user-message').render()))
            self.assertEqual(field.text, '')
            client.allow_create.set()
            await asyncio.wait_for(client.admitting.wait(), 1)
            await app.handle_event({'type': 'session.snapshot', 'data': {'messages': [], 'active': True}})
            self.assertEqual(len(app.query('.user-message')), 1)
            await app.handle_event({'type': 'session.snapshot', 'data': {'messages': client.messages, 'active': True}})
            self.assertEqual(len(app.query('.user-message')), 1)
            client.allow_prompt.set()
            await pilot.pause(.08)
            self.assertEqual(len(app.query('.user-message')), 1)

    async def test_failed_admission_restores_draft_and_removes_local_echo(self):
        from tui.app import HorizonApp, PromptInput
        class FailingClient(FakeClient):
            async def create(self):
                raise RuntimeError('服务不可用')
        app = HorizonApp(FailingClient())
        async with app.run_test(size=(100, 30)) as pilot:
            field = app.query_one(PromptInput)
            field.load_text('保留失败的输入')
            await pilot.press('enter')
            await pilot.pause(.08)
            self.assertEqual(field.text, '保留失败的输入')
            self.assertEqual(len(app.query('.user-message')), 0)
            self.assertFalse(app.busy)

    async def test_user_background_tracks_light_and_dark_terminal_reports(self):
        from tui.app import HorizonApp
        from tui.terminal_colors import TerminalBackground, message_background
        for rgb in ((255, 255, 255), (240, 240, 240), (24, 24, 24), (0, 0, 0)):
            with self.subTest(rgb=rgb):
                app = HorizonApp(FakeClient())
                async with app.run_test(size=(80, 24)) as pilot:
                    app._show_chat()
                    messages = [{'type': 'user', 'text': '你好\nsecond line'}]
                    await app._render_context(messages)
                    app.post_message(TerminalBackground(rgb))
                    await pilot.pause(.03)
                    background = app.query_one('.user-message').styles.background
                    self.assertEqual(background.hex.lower(), message_background(rgb))
                    self.assertIsNone(background.ansi)
                    self.assertEqual(app.query_one('.user-message').styles.color.ansi, -1)
                    # Refresh/history must retain the same highlight.
                    await app._render_context(messages)
                    self.assertEqual(app.query_one('.user-message').styles.background, background)
                    self.assertEqual(app.screen.styles.background.ansi, -1)

    async def test_editor_cursor_preserves_glyph_with_contrasting_terminal_colors(self):
        from tui.app import HorizonApp, PromptInput
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 30)) as pilot:
            field = app.query_one(PromptInput)
            field.cursor_blink = False
            for text in ('输入中文', 'hello', ''):
                field.load_text(text)
                await pilot.pause(.03)
                cursor_style = field._theme.cursor_style
                self.assertFalse(cursor_style.reverse)
                self.assertNotEqual(cursor_style.color, cursor_style.bgcolor)
                self.assertEqual(cursor_style.color.number, 7)
                self.assertEqual(cursor_style.bgcolor.number, 0)
                rendered = ''.join(segment.text for segment in field.render_line(0))
                self.assertTrue(rendered.startswith(text or field.placeholder))

    async def test_native_ime_cursor_follows_layout_changes_and_terminal_resize(self):
        from tui.app import HorizonApp, PromptInput
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(120, 30)) as pilot:
            field = app.query_one(PromptInput)
            before = field.size
            app.query_one('#topbar-row').styles.height = 5
            await pilot.pause(.12)
            self.assertEqual(field.size, before)
            self.assertEqual(app.cursor_position, field.cursor_screen_offset)
            app._show_chat()
            await pilot.pause(.12)
            self.assertEqual(app.cursor_position, field.cursor_screen_offset)
            await app.action_new_session()
            await pilot.pause(.12)
            self.assertEqual(app.cursor_position, field.cursor_screen_offset)
            await pilot.resize_terminal(80, 24)
            await pilot.pause(.12)
            self.assertEqual(app.cursor_position, field.cursor_screen_offset)
            field.load_text('你好，Horizon')
            field.move_cursor((0, 3))
            await pilot.resize_terminal(100, 30)
            await pilot.pause(.12)
            self.assertEqual(app.cursor_position, field.cursor_screen_offset)
            self.assertTrue(field.content_region.contains(*app.cursor_position))
            self.assertEqual(field.text, '你好，Horizon')
            self.assertEqual(app.client.sent, [])

    async def test_prompt_renders_inline_hint_and_clears_full_input_width(self):
        from tui.app import HorizonApp, PromptInput
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 30)) as pilot:
            field = app.query_one(PromptInput)
            await pilot.pause(.05)
            self.assertTrue(field.has_focus)
            self.assertEqual(field.placeholder, 'Ask anything, or start a task…')
            self.assertTrue(field.render_line(0).text.startswith('Ask anything'))
            self.assertEqual(field.render_line(0).cell_length, field.content_size.width)
            self.assertFalse(app.query_one('#composer').border_title)
            composer = app.query_one('#composer')
            self.assertEqual(composer.styles.border_top[0], 'solid')
            self.assertEqual(composer.styles.border_bottom[0], 'solid')
            self.assertIn(composer.styles.border_right[0], ('', 'none'))
            self.assertEqual(len(app.query('#directory')), 0)
            app.query_one('#history').focus()
            await pilot.pause(.05)
            self.assertTrue(field.render_line(0).text.startswith('Ask anything'))
            field.focus()
            await pilot.pause(.05)
            self.assertEqual(field.text, '')
            field.load_text('hello')
            await pilot.pause(.03)
            self.assertTrue(field.render_line(0).text.startswith('hello'))
            self.assertNotIn('Ask anything', field.render_line(0).text)
            field.clear()
            await pilot.pause(.03)
            self.assertTrue(field.render_line(0).text.startswith('Ask anything'))
            self.assertEqual(field.render_line(0).cell_length, field.content_size.width)
            self.assertFalse(app.query_one('#composer').border_title)

    async def test_chat_fills_width_and_uses_terminal_foreground_for_all_answers(self):
        from tui.app import HorizonApp
        from textual.widgets import Static
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(120, 30)) as pilot:
            home_box = app.query_one('#composer').region
            self.assertIn('Horizon harness', str(app.query_one('#app-title', Static).render()))
            app._show_chat()
            await app._render_context([
                {'type': 'user', 'text': '你好'},
                {'type': 'assistant', 'content': [{'type': 'text', 'text': '模型回答'}]},
            ])
            await app.handle_event({'type': 'llm.delta', 'data': {'call_id': 'a', 'text': '流式回答'}})
            await pilot.pause(.05)
            box = app.query_one('#composer').region
            self.assertEqual(app.query_one('#composer').styles.border_top[0], 'solid')
            self.assertIn(app.query_one('#composer').styles.border_right[0], ('', 'none'))
            self.assertGreaterEqual(box.width, 116)
            self.assertEqual(box.bottom, app.query_one('#composer-hint').region.y)
            self.assertEqual(app.query_one('.answer').styles.color.ansi, -1)
            self.assertEqual(app.query_one('.answer').styles.color, app.query_one('#live-output').styles.color)
            # User text begins after the two-cell "› " prefix. Both completed
            # Markdown and live text must start in that same terminal column.
            text_x = app.query_one('.user-message').content_region.x + 2
            self.assertEqual(app.query_one('.answer MarkdownParagraph').content_region.x, text_x)
            self.assertEqual(app.query_one('#live-output').content_region.x, text_x)
            await app.handle_event({'type': 'run.ended', 'data': {'status': 'completed'}})
            self.assertEqual(len(app.query('#send')), 0)
            self.assertFalse(app.query_one('#activity').display)
            await app.action_new_session()
            await pilot.pause(.05)
            self.assertEqual(app.query_one('#composer').region, home_box)

    async def test_command_home_positions_logo_title_and_input_across_sizes(self):
        from tui.app import HorizonApp, PromptInput
        for size in ((120, 30), (80, 24), (45, 20)):
            with self.subTest(size=size):
                app = HorizonApp(FakeClient())
                async with app.run_test(size=size) as pilot:
                    await pilot.pause(.05)
                    welcome = app.query_one('#welcome').region
                    logo = app.query_one('#home-logo').region
                    composer = app.query_one('#composer').region
                    self.assertGreaterEqual(composer.y, welcome.bottom)
                    self.assertGreaterEqual(composer.y, logo.bottom)
                    self.assertLess(composer.bottom, size[1])
                    self.assertGreaterEqual(composer.width, size[0] - 4)
                    self.assertLessEqual(abs(composer.x - (size[0]-composer.right)), 2)
                    self.assertTrue(app.query_one('#composer-hint').display)
                    self.assertIn('Make it happen_', str(app.query_one('#home-title').render()))
                    self.assertEqual(app.query_one('#home-title-art').display, size[0] >= 112 and size[1] >= 26)
                    if app.query_one('#home-title-art').display:
                        self.assertEqual(app.query_one('#home-title-art').region.height, 6)
                        self.assertLessEqual(max(len(line) for line in str(app.query_one('#home-title-art').render()).splitlines()),
                                             app.query_one('#home-intro').content_size.width)
                    if size[0] >= 76:
                        self.assertGreaterEqual(logo.x, app.query_one('#home-intro').region.right)
                    else:
                        self.assertLessEqual(logo.bottom, app.query_one('#home-intro').region.y)
                    self.assertTrue(app.query_one(PromptInput).rich_style.bold)
                    app._show_chat()
                    await pilot.pause(.05)
                    self.assertTrue(app.query_one('#composer-hint').display)
                    self.assertTrue(app.query_one('#prompt-prefix').display)
                    self.assertGreater(app.query_one('#transcript').region.height, 4)
                    self.assertLessEqual(app.query_one('#composer').region.bottom, size[1])

    async def test_failure_message_keeps_internal_context_details_out_of_chat(self):
        from tui.app import HorizonApp
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 34)):
            await app.handle_event({'type': 'run.ended', 'data': {
                'status': 'failed', 'error': {'code': 'system_context_unavailable',
                                             'message': '无法加载本地记忆或项目指令'}}})
            self.assertFalse(app.busy)
            self.assertEqual(app.phase, '执行失败')

    async def test_reconnect_retries_final_event_when_context_refresh_failed(self):
        from tui.app import HorizonApp
        from textual.widgets import Markdown
        client = FakeClient()
        attempts = []
        hold = asyncio.Event()
        async def context(_):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError('temporary 503')
            return [{'type': 'assistant', 'content': [{'type': 'text', 'text': '恢复后的最终回答'}]}]
        async def stream(_, after=0):
            yield {'type': 'ready', 'data': {}}
            if after < 1:
                yield {'id': 1, 'type': 'run.ended', 'data': {'status': 'completed'}}
            await hold.wait()
        client.context, client.events = context, stream
        app = HorizonApp(client)
        async with app.run_test(size=(90, 32)) as pilot:
            app.session_id = 'ses_ui'
            app._show_chat()
            app._watcher = app._spawn(app._watch('ses_ui'))
            await pilot.pause(.8)
            self.assertGreaterEqual(len(attempts), 2)
            self.assertEqual(app.query_one('#messages Markdown', Markdown)._markdown, '恢复后的最终回答')
            self.assertEqual(app.phase, '已完成')

    async def test_help_can_stay_open_while_execution_updates_arrive(self):
        from tui.app import HorizonApp
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 34)) as pilot:
            app.action_help()
            await pilot.pause(.05)
            await app.handle_event({'type': 'run.started', 'data': {}})
            await app.handle_event({'type': 'llm.delta', 'data': {'call_id': 'a', 'text': 'hello'}})
            self.assertTrue(app.busy)
            await pilot.press('escape')
            await pilot.pause(.05)
            self.assertEqual(app.phase, '正在回答')

    async def test_old_interrupt_response_does_not_stop_a_new_turn(self):
        from tui.app import HorizonApp, PromptInput
        client = FakeClient()
        release = asyncio.Event()
        async def delayed_interrupt(_):
            await client.queue.put({'type': 'run.ended', 'data': {'status': 'interrupted'}})
            await release.wait()
        client.interrupt = delayed_interrupt
        app = HorizonApp(client)
        async with app.run_test(size=(90, 32)) as pilot:
            field = app.query_one(PromptInput)
            field.load_text('first')
            await pilot.press('enter')
            await pilot.pause(.05)
            await pilot.press('escape')
            await pilot.pause(.08)
            field.load_text('second')
            await pilot.press('enter')
            await pilot.pause(.08)
            release.set()
            await pilot.pause(.05)
            self.assertTrue(app.busy)
            self.assertNotEqual(app.phase, '已中断')

    async def test_canonical_api_messages_survive_overlapping_refreshes(self):
        from core.session_message import SessionMessage
        from tui.app import HorizonApp
        from textual.widgets import Markdown
        messages = [
            SessionMessage('u', 'ses_ui', 'user', 1, 0, 0, {'text': '中文问题'}).to_dict(),
            SessionMessage('a', 'ses_ui', 'assistant', 2, 0, 0,
                           {'content': [{'type': 'text', 'text': '持久化回答'}]}).to_dict(),
        ]
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 34)) as pilot:
            app._show_chat()
            await asyncio.gather(app._render_context(messages), app._render_context(messages))
            await pilot.pause(.05)
            self.assertEqual(len(app.query('.user-message')), 1)
            self.assertEqual(len(app.query('.speaker')), 0)
            self.assertEqual(len(app.query('#messages Markdown')), 1)
            self.assertIn('中文问题', str(app.query_one('.user-message').render()))
            self.assertEqual(app.query_one('#messages Markdown', Markdown)._markdown, '持久化回答')

    async def test_terminal_default_background_and_centered_composer(self):
        from tui.app import HorizonApp
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(.05)
            self.assertTrue(app.ansi_color)
            self.assertEqual(app.screen.styles.background.ansi, -1)
            box = app.query_one('#composer').region
            self.assertLessEqual(abs(box.x - (100-box.right)), 2)

    async def test_input_streaming_status_and_interrupt(self):
        from tui.app import HorizonApp, PromptInput
        from textual.widgets import Static
        client = FakeClient()
        app = HorizonApp(client)
        async with app.run_test(size=(100, 36)) as pilot:
            field = app.query_one(PromptInput)
            field.load_text('你好，查看项目')
            await pilot.press('enter')
            await pilot.pause(.1)
            self.assertEqual(client.sent, ['你好，查看项目'])
            self.assertTrue(app.busy)
            await client.queue.put({'type': 'llm.started', 'data': {'call_id': 'a'}})
            await pilot.pause(.05)
            self.assertEqual(app.phase, '正在思考')
            await client.queue.put({'type': 'llm.delta', 'data': {'call_id': 'a', 'text': '第一段'}})
            await pilot.pause(.08)
            self.assertEqual(app.phase, '正在回答')
            self.assertIn('第一段', app.query_one('#live-output').source)
            await client.queue.put({'type': 'session.next.tool.called',
                                    'data': {'call_id': 'tool1', 'name': 'read_file'}})
            await pilot.pause(.05)
            self.assertIn('read_file', app.phase)
            await pilot.press('escape')
            await pilot.pause(.15)
            self.assertTrue(client.interrupted)
            self.assertFalse(app.busy)
            self.assertEqual(app.phase, '已中断')
            await pilot.press('ctrl+n')
            await pilot.pause(.05)
            self.assertIsNone(app.session_id)
            self.assertTrue(app.query_one('#home').display)

    async def test_multiline_and_narrow_terminal(self):
        from tui.app import HorizonApp, PromptInput
        client = FakeClient()
        app = HorizonApp(client)
        async with app.run_test(size=(45, 24)) as pilot:
            field = app.query_one(PromptInput)
            field.load_text('第一行')
            field.move_cursor((0, 3))
            await pilot.press('ctrl+j')
            await pilot.pause(.05)
            self.assertIn('\n', field.text)
            self.assertEqual(client.sent, [])
            self.assertLessEqual(app.query_one('#composer').region.right, 45)

    async def test_composer_grows_for_wrapped_text_and_shrinks_when_cleared(self):
        from tui.app import HorizonApp, PromptInput
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 30)) as pilot:
            field = app.query_one(PromptInput)
            await pilot.pause(.08)
            self.assertEqual(field.region.height, 1)
            self.assertEqual(app.query_one('#composer').region.height, 3)
            self.assertEqual(len(app.query('#send')), 0)
            self.assertIn('>', str(app.query_one('#prompt-prefix').render()))
            field.load_text('first\nsecond\nthird')
            await pilot.pause(.08)
            self.assertEqual(field.region.height, 3)
            field.load_text('中文输入' * 20)
            await pilot.pause(.08)
            self.assertGreater(field.region.height, 1)
            await pilot.resize_terminal(45, 24)
            await pilot.pause(.12)
            self.assertEqual(field.region.height, min(6, field.wrapped_document.height))
            self.assertTrue(field.content_region.contains(*app.cursor_position))
            field.clear()
            await pilot.pause(.08)
            self.assertEqual(field.region.height, 1)
            app._show_chat()
            await pilot.pause(.08)
            self.assertEqual(app.query_one('#composer').region.height, 3)
            self.assertTrue(app.query_one('#prompt-prefix').display)
            field.load_text('\n'.join(['line'] * 20))
            await pilot.pause(.08)
            self.assertEqual(field.region.height, 6)
            field.move_cursor(field.document.end)
            field.scroll_cursor_visible()
            await pilot.pause(.12)
            self.assertTrue(field.content_region.contains(*app.cursor_position))

    async def test_failure_stops_spinner_and_preserves_literal_markup(self):
        from tui.app import HorizonApp, PromptInput
        from textual.widgets import Static
        client = FakeClient()
        app = HorizonApp(client)
        async with app.run_test(size=(90, 32)) as pilot:
            app.query_one(PromptInput).load_text('[red]中文任务')
            await pilot.press('enter')
            await pilot.pause(.08)
            await client.queue.put({'type': 'llm.delta', 'data': {'call_id': 'x', 'text': '[link=bad]literal'}})
            await pilot.pause(.08)
            self.assertIn('[link=bad]literal', app.query_one('#live-output').source)
            await client.queue.put({'type': 'run.ended', 'data': {'status': 'failed'}})
            await pilot.pause(.1)
            self.assertFalse(app.busy)
            self.assertEqual(app.phase, '执行失败')


if __name__ == '__main__':
    unittest.main()
