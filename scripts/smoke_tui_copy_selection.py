"""Actual mouse selections and OS clipboard adapter checks without host clipboard writes."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch
import unittest
from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp


class SelectionCopy(unittest.IsolatedAsyncioTestCase):
    async def test_mouse_release_copies_chinese_and_repeated_selections(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock, create=True) as copy:
            async with app.run_test(size=(100, 30)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text', 'text': '苹果没有开放接口。'}]}])
                await pilot.pause(.05)
                paragraph = app.query_one('#messages MarkdownParagraph')
                for start, end, text in [(0, 4, '苹果'), (4, 8, '没有')]:
                    await pilot.mouse_down(paragraph, offset=(start, 0))
                    await pilot.hover(paragraph, offset=(end, 0))
                    self.assertEqual(copy.await_count, 0 if start == 0 else 1)
                    await pilot.mouse_up(paragraph, offset=(end, 0))
                    await pilot.pause(.08)
                    self.assertEqual(copy.await_args.args[0], text)
                    self.assertEqual(app._notice[0], '已复制')
                    self.assertIsNone(app.screen.get_selected_text())
                self.assertEqual(copy.await_count, 2)
                await pilot.click(paragraph, offset=(0, 0))
                await pilot.pause(.05)
                self.assertEqual(copy.await_count, 2)

    async def test_failed_copy_keeps_selection_and_reports_failure(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock, create=True) as copy:
            copy.side_effect = OSError('clipboard unavailable')
            async with app.run_test(size=(90, 28)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'user', 'text': 'A user message'}])
                await pilot.pause(.05)
                message = app.query_one('.user-message')
                await pilot.mouse_down(message, offset=(5, 1))
                await pilot.hover(message, offset=(9, 1))
                await pilot.mouse_up(message, offset=(9, 1))
                await pilot.pause(.08)
                self.assertEqual(copy.await_count, 1)
                self.assertEqual(app._notice[0], '复制失败，请重试')
                self.assertTrue(app.screen.get_selected_text())

    async def test_cross_paragraph_selection_copies_all_lines_then_clears(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock) as copy:
            async with app.run_test(size=(80, 30)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text',
                    'text': 'First paragraph\n\nSecond paragraph\n\nLast paragraph'}]}])
                await pilot.pause(.05)
                paragraphs = list(app.query('#messages MarkdownParagraph'))
                await pilot.mouse_down(paragraphs[0], offset=(0, 0))
                await pilot.hover(paragraphs[-1], offset=(14, 0))
                await pilot.mouse_up(paragraphs[-1], offset=(14, 0))
                await pilot.pause(.08)
                copy.assert_awaited_once()
                self.assertEqual(copy.await_args.args[0], 'First paragraph\nSecond paragraph\nLast paragraph')
                self.assertIsNone(app.screen.get_selected_text())

    async def test_wrapped_chinese_paragraph_copies_across_visual_lines(self):
        text = '这是一个会自动换行的长段落。' * 8
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock) as copy:
            async with app.run_test(size=(50, 30)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text', 'text': text}]}])
                await pilot.pause(.05)
                paragraph = app.query_one('#messages MarkdownParagraph')
                self.assertGreater(paragraph.size.height, 2)
                await pilot.mouse_down(paragraph, offset=(0, 0))
                await pilot.hover(paragraph, offset=(12, 2))
                selected = app.screen.get_selected_text()
                await pilot.mouse_up(paragraph, offset=(12, 2))
                await pilot.pause(.08)
                copy.assert_awaited_once()
                copied = copy.await_args.args[0]
                self.assertEqual(copied, selected)
                self.assertGreater(len(copied), 30)
                self.assertTrue(text.startswith(copied))
                self.assertIsNone(app.screen.get_selected_text())

    async def test_slow_copy_does_not_clear_a_new_drag(self):
        app = HorizonApp(FakeClient())
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def delayed_copy(text, owner):
            nonlocal count
            count += 1
            if count == 1:
                entered.set()
                await release.wait()
        with patch('tui.app.copy_text', side_effect=delayed_copy) as copy:
            async with app.run_test(size=(90, 28)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text', 'text': '苹果没有开放接口。'}]}])
                await pilot.pause(.05)
                paragraph = app.query_one('#messages MarkdownParagraph')
                await pilot.mouse_down(paragraph, offset=(0, 0))
                await pilot.hover(paragraph, offset=(4, 0))
                await pilot.mouse_up(paragraph, offset=(4, 0))
                await asyncio.wait_for(entered.wait(), 1)
                await pilot.mouse_down(paragraph, offset=(4, 0))
                await pilot.hover(paragraph, offset=(8, 0))
                release.set()
                await pilot.pause(.05)
                self.assertEqual(app.screen.get_selected_text(), '没有')
                await pilot.mouse_up(paragraph, offset=(8, 0))
                await pilot.pause(.08)
                self.assertEqual(copy.await_count, 2)
                self.assertEqual(copy.await_args.args[0], '没有')
                self.assertIsNone(app.screen.get_selected_text())

    async def test_scrolled_multiline_selection_includes_offscreen_middle(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock) as copy:
            async with app.run_test(size=(80, 24)) as pilot:
                app._show_chat()
                text = '\n\n'.join(f'ROW_{i:02d} complete paragraph' for i in range(25))
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text', 'text': text}]}])
                await pilot.pause(.1)
                view = app.query_one('#transcript')
                view.scroll_home(animate=False); await pilot.pause(.05)
                paragraphs = list(app.query('#messages MarkdownParagraph'))
                await pilot.mouse_down(paragraphs[0], offset=(0, 0))
                await pilot.hover(paragraphs[1], offset=(4, 0))
                view.scroll_end(animate=False); await pilot.pause(.05)
                await pilot.hover(paragraphs[-1], offset=(30, 0))
                await pilot.mouse_up(paragraphs[-1], offset=(30, 0))
                await pilot.pause(.08)
                copy.assert_awaited_once()
                copied = copy.await_args.args[0]
                for i in range(25):
                    self.assertEqual(copied.count(f'ROW_{i:02d}'), 1, (i, copied))

    async def test_chrome_cannot_be_highlighted_or_copied(self):
        app = HorizonApp(FakeClient(), local_directory='/Users/demo/project')
        with patch('tui.app.copy_text', new_callable=AsyncMock) as copy:
            async with app.run_test(size=(100, 30)) as pilot:
                for selector in ('#app-title', '#composer-hint', '#workspace-hint'):
                    widget = app.query_one(selector)
                    await pilot.mouse_down(widget, offset=(1, 0 if selector == '#app-title' else 1))
                    await pilot.hover(widget, offset=(8, 0 if selector == '#app-title' else 1))
                    await pilot.mouse_up(widget, offset=(8, 0 if selector == '#app-title' else 1))
                    await pilot.pause(.05)
                    self.assertFalse(app.screen.selections, selector)
                copy.assert_not_awaited()

    async def test_release_position_keeps_last_line_even_without_final_mouse_move(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock) as copy:
            async with app.run_test(size=(80, 30)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text',
                    'text': 'Start line\n\nLast line complete'}]}])
                await pilot.pause(.05)
                first, last = list(app.query('#messages MarkdownParagraph'))
                await pilot.mouse_down(first, offset=(0, 0))
                await pilot.hover(last, offset=(4, 0))
                await pilot.mouse_up(last, offset=(20, 0))
                await pilot.pause(.08)
                self.assertEqual(copy.await_args.args[0], 'Start line\nLast line complete')

    async def test_reverse_selection_includes_mixed_markdown_once(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock) as copy:
            async with app.run_test(size=(100, 40)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text',
                    'text': 'Start\n\n- **Item A**\n- Item B\n\n```python\nprint("ONE")\nprint("TWO")\n```\n\nFinish'}]}])
                await pilot.pause(.05)
                paragraphs = list(app.query('#messages MarkdownParagraph'))
                await pilot.mouse_down(paragraphs[-1], offset=(8, 0))
                await pilot.hover(paragraphs[0], offset=(0, 0))
                await pilot.mouse_up(paragraphs[0], offset=(0, 0))
                await pilot.pause(.08)
                copied = copy.await_args.args[0]
                for word in ('Start', 'Item A', 'Item B', 'print("ONE")', 'print("TWO")', 'Finish'):
                    self.assertEqual(copied.count(word), 1, copied)
                self.assertNotIn('Horizon harness', copied)

    async def test_hidden_intermediate_text_is_not_copied(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock) as copy:
            async with app.run_test(size=(80, 30)) as pilot:
                app._show_chat()
                await app._render_context([{'type': 'assistant', 'content': [{'type': 'text',
                    'text': 'Visible start\n\nHidden detail\n\nVisible end'}]}])
                await pilot.pause(.05)
                first, hidden, last = list(app.query('#messages MarkdownParagraph'))
                hidden.display = False; await pilot.pause(.05)
                await pilot.mouse_down(first, offset=(0, 0))
                await pilot.hover(last, offset=(20, 0))
                await pilot.mouse_up(last, offset=(20, 0))
                await pilot.pause(.08)
                self.assertEqual(copy.await_args.args[0], 'Visible start\nVisible end')

    async def test_composer_selection_does_not_auto_copy(self):
        app = HorizonApp(FakeClient())
        with patch('tui.app.copy_text', new_callable=AsyncMock, create=True) as copy:
            async with app.run_test(size=(90, 28)) as pilot:
                app._prompt_widget.load_text('Unsent private draft')
                await pilot.pause(.05)
                await pilot.mouse_down(app._prompt_widget, offset=(0, 0))
                await pilot.hover(app._prompt_widget, offset=(8, 0))
                await pilot.mouse_up(app._prompt_widget, offset=(8, 0))
                await pilot.pause(.05)
                copy.assert_not_awaited()


class ClipboardAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_macos_writes_utf8_stdin_without_shell_then_syncs_terminal(self):
        from tui.clipboard import copy_text
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(None, None))
        process.wait = AsyncMock()
        app = Mock()
        with patch('tui.clipboard.sys.platform', 'darwin'), patch('tui.clipboard.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            spawn.return_value = process
            await copy_text('苹果 **code**\n$HOME', app)
            self.assertEqual(spawn.await_args.args, ('/usr/bin/pbcopy',))
            process.communicate.assert_awaited_once_with('苹果 **code**\n$HOME'.encode('utf-8'))
            app.copy_to_clipboard.assert_called_once_with('苹果 **code**\n$HOME')
            process.kill.assert_not_called()

    async def test_macos_failure_and_timeout_never_report_success(self):
        from tui.clipboard import copy_text
        for code in (1, None):
            with self.subTest(returncode=code):
                process = Mock(returncode=code)
                process.communicate = AsyncMock(side_effect=asyncio.TimeoutError() if code is None else None)
                process.wait = AsyncMock()
                app = Mock()
                with patch('tui.clipboard.sys.platform', 'darwin'), patch('tui.clipboard.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
                    spawn.return_value = process
                    with self.assertRaises((OSError, asyncio.TimeoutError)):
                        await copy_text('Selected text', app)
                    app.copy_to_clipboard.assert_not_called()
                    if code is None:
                        process.kill.assert_called_once()
                        process.wait.assert_awaited_once()

    async def test_windows_clipboard_reads_utf8_stdin_without_interpolating_text(self):
        from tui.clipboard import copy_text
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(None,None))
        app = Mock()
        text = '中文 $HOME; Remove-Item example'
        with patch('tui.clipboard.sys.platform','win32'), patch('tui.clipboard.asyncio.create_subprocess_exec',new_callable=AsyncMock) as spawn:
            spawn.return_value = process
            await copy_text(text,app)
            spawn.assert_awaited_once()
            self.assertNotIn(text,spawn.await_args.args)
            self.assertIn('Set-Clipboard',spawn.await_args.args[-1])
            process.communicate.assert_awaited_once_with(text.encode('utf-8'))
            app.copy_to_clipboard.assert_called_once_with(text)

    async def test_other_terminal_path_uses_osc52_and_no_host_process(self):
        from tui.clipboard import copy_text
        app = Mock()
        with patch('tui.clipboard.sys.platform', 'linux'), patch('tui.clipboard.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            await copy_text('text\0only', app)
            app.copy_to_clipboard.assert_called_once_with('textonly')
            spawn.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
