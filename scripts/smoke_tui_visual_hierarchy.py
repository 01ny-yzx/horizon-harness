"""Rendered emphasis and semantic colors with offline light/dark UI states."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.geometry import Region
from scripts.smoke_tui_session_tabs import TabClient
from tui.app import HorizonApp
from tui.terminal_colors import TerminalBackground


def segments(widget):
    return [s for line in widget.render_lines(Region(0, 0, widget.region.width, widget.region.height)) for s in line]


class VisualHierarchyContracts(unittest.IsolatedAsyncioTestCase):
    async def test_answer_footer_tab_and_activity_have_distinct_emphasis(self):
        from tui.presentation import semantic_color
        with patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            app = HorizonApp(TabClient())
        async with app.run_test(size=(120, 30)) as pilot:
            await app._refresh_tabs()
            await app._open('ses_a')
            await app._render_context([{'type': 'assistant', 'content': [
                {'type': 'text', 'text': 'Normal text with **important conclusion**.'}]}])
            for rgb, dark in [((255, 255, 255), False), ((24, 24, 24), True)]:
                app.post_message(TerminalBackground(rgb))
                app._set_model_hint('test-model')
                await pilot.pause(.1)
                paragraph = app.query_one('MarkdownParagraph')
                self.assertFalse(paragraph.rich_style.bold)
                self.assertTrue(any('important conclusion' in s.text and s.style.bold for s in segments(paragraph)))
                hint = segments(app.query_one('#composer-hint'))
                self.assertTrue(any('test-model' in s.text and not s.style.dim for s in hint))
                self.assertTrue(any('/ commands' in s.text and s.style.dim for s in hint))
                active = app.query_one('.active-tab .tab-label')
                self.assertTrue(any(s.style and s.style.bold and
                                    (s.style.color is None or s.style.color.is_default)
                                    for s in segments(active)))
                for phase, busy, role in [('正在思考', True, 'info'), ('正在压缩上下文', True, 'warning'),
                                           ('已完成', False, 'success'), ('执行失败', False, 'error')]:
                    app._set_phase(phase, busy=busy)
                    await pilot.pause()
                    self.assertEqual(app._activity_widget.styles.color.hex.lower(), semantic_color(role, dark))

    async def test_tool_and_durable_failures_are_marked_without_coloring_body(self):
        from tui.presentation import ToolRow, semantic_color
        with patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            app = HorizonApp(TabClient())
        async with app.run_test(size=(100, 30)) as pilot:
            app._show_chat()
            await app._render_context([
                {'type': 'assistant', 'content': [
                    {'type': 'tool', 'name': 'read_file', 'state': {'status': 'completed'}},
                    {'type': 'tool', 'name': 'web_search', 'state': {'status': 'error'}}]},
                {'type': 'run_status', 'status': 'failed', 'error': {'message': 'Request failed'}}])
            await pilot.pause()
            rows = list(app.query(ToolRow))
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertTrue(row.collapsed)
            for rgb, dark in [((24, 24, 24), True), ((255, 255, 255), False)]:
                app.post_message(TerminalBackground(rgb))
                await pilot.pause()
                for row, symbol, role in zip(rows, ['✓', '×'], ['success', 'error']):
                    title = row.query_one('CollapsibleTitle')
                    self.assertTrue(any(symbol in s.text and s.style.color.name == semantic_color(role, dark)
                                        for s in segments(title)))
            self.assertEqual(app.query_one('.run-outcome').styles.color.hex.lower(), semantic_color('error', False))
            self.assertTrue(app.screen.styles.background.ansi == -1)


if __name__ == '__main__':
    unittest.main()
