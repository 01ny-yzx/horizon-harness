"""Selected transcript text stays readable and copyable on light/dark terminals."""
import os
from unittest.mock import patch, AsyncMock
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.geometry import Offset, Region
from textual.selection import Selection
from textual.widgets import Markdown
from tui.app import HorizonApp
from tui.terminal_colors import TerminalBackground
from scripts.smoke_tui_ui import FakeClient


class TextSelectionContracts(unittest.IsolatedAsyncioTestCase):
    async def test_input_cursor_adapts_when_terminal_background_changes(self):
        with patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            app = HorizonApp(FakeClient())
        async with app.run_test(size=(80, 24)) as pilot:
            app._show_chat()
            prompt = app._prompt_widget
            # This checks cursor colors, independently of its timed blink phase.
            prompt.cursor_blink = False
            for rgb, cursor, ink in [((24, 24, 24), (255, 255, 255), (0, 0, 0)),
                                     ((255, 255, 255), (0, 0, 0), (255, 255, 255)),
                                     ((0, 0, 0), (255, 255, 255), (0, 0, 0))]:
                app.post_message(TerminalBackground(rgb))
                await pilot.pause()
                for text in ('', 'hello', '你好'):
                    prompt.load_text(text)
                    prompt.move_cursor((0, 0))
                    prompt._cursor_visible = True
                    prompt.refresh()
                    await pilot.pause()
                    style = prompt._theme.cursor_style
                    self.assertEqual(style.bgcolor.get_truecolor(), cursor)
                    self.assertEqual(style.color.get_truecolor(), ink)
                    segments = list(prompt.render_line(0))
                    self.assertTrue(any(s.style and s.style.bgcolor == style.bgcolor
                                        and s.style.color == style.color for s in segments))

    @patch('tui.app.copy_text', new_callable=AsyncMock)
    async def test_selected_chinese_text_has_explicit_contrast_and_copies(self, _copy):
        for rgb in ((255, 255, 255), (24, 24, 24)):
            with self.subTest(background=rgb):
                with patch.dict(os.environ):
                    os.environ.pop('NO_COLOR', None)
                    app = HorizonApp(FakeClient())
                async with app.run_test(size=(100, 30)) as pilot:
                    app._show_chat()
                    app.post_message(TerminalBackground(rgb))
                    await app._render_context([{'type': 'assistant', 'content': [
                        {'type': 'text', 'text': '也就是说，苹果没有开放可脚本调用的保修查询接口。'}]}])
                    await pilot.pause()
                    paragraph = app.query_one('#messages Markdown', Markdown).query_one('MarkdownParagraph')
                    style = app.screen.get_component_rich_style('screen--selection')
                    self.assertEqual(style.color.triplet, (255, 255, 255))
                    self.assertEqual(style.bgcolor.triplet, (74, 74, 74))
                    app.screen.selections = {paragraph: Selection(Offset(5, 0), Offset(8, 0))}
                    paragraph.refresh()
                    await pilot.pause()
                    self.assertEqual(app.screen.get_selected_text(), '苹果没')
                    strips = paragraph.render_lines(Region(0, 0, paragraph.size.width, paragraph.size.height))
                    selected = [segment for strip in strips for segment in strip
                                if segment.style and segment.style.bgcolor == style.bgcolor]
                    self.assertTrue(selected)
                    self.assertTrue(all(segment.style.color == style.color for segment in selected))
                    self.assertIn('苹果没', ''.join(segment.text for segment in selected))
                    app.screen.clear_selection()
                    await pilot.pause()
                    self.assertIsNone(app.screen.get_selected_text())
                    cleared = paragraph.render_lines(Region(0, 0, paragraph.size.width, paragraph.size.height))
                    self.assertFalse(any(segment.style and segment.style.bgcolor == style.bgcolor
                                         for strip in cleared for segment in strip))
                    await pilot.mouse_down(paragraph, offset=(10, 0))
                    await pilot.hover(paragraph, offset=(16, 0))
                    await pilot.mouse_up(paragraph, offset=(16, 0))
                    await pilot.pause()
                    self.assertIsNone(app.screen.get_selected_text())
                    self.assertEqual(_copy.await_args.args[0], '苹果没')


if __name__ == '__main__':
    unittest.main()
