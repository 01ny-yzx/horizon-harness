"""Horizon terminal presentation. All runtime work goes through the HTTP client."""
from __future__ import annotations

import asyncio
import json
import uuid
import time
from pathlib import Path

from rich.text import Text
from rich.style import Style
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.widgets.text_area import TextAreaTheme

from tui.terminal_colors import TerminalBackground, color_driver, message_background
from tui.pickers import ConnectionForm, SelectionPicker, SearchConfigForm
from tui.session_history import SessionHistoryPicker
from tui.options import KeyboardOptionList
from tui.model_menu import PROVIDER_NAMES, PROVIDER_ORDER, visible_provider_ids
from tui.welcome import WelcomeScreen, render_home_logo
from tui.session_tabs import SessionTabBar, SessionTabState
from tui.tab_layout import TabLayoutStore
from tui.home_scan import HomeScan
from tui.presentation import ToolRow, semantic_color, phase_role
from tui.activity import activity_frame
from tui.transcript import LiveMarkdown, Transcript
from tui.text_selection import TranscriptSelectionScreen, selected_body_text
from tui.clipboard import copy_text
from tui.keybindings import KeyBindings
from tui.shortcut_settings import ShortcutSettings, KeyCapture
from tui.queue_panel import PendingQueue, QueueConfirm
from tui.workspace_hint import workspace_hint
from tui.version import __version__
from tui.transcript_outcomes import presentation_messages
from tui.i18n import LANGUAGES, Preferences, help_text, translate


HOME_TITLE_ART = "\n".join((
    r" __  __       _           _ _     _",
    r"|  \/  | __ _| | _____   (_) |_  | |__   __ _ _ __  _ __   ___ _ __",
    r"| |\/| |/ _` | |/ / _ \  | | __| | '_ \ / _` | '_ \| '_ \ / _ \ '_ \ ",
    r"| |  | | (_| |   <  __/  | | |_  | | | | (_| | |_) | |_) |  __/ | | |",
    r"|_|  |_|\__,_|_|\_\___|  |_|\__| |_| |_|\__,_| .__/| .__/ \___|_| |_|___",
    r"                                            |_|   |_|",
))


class VersionHint(Static):
    async def on_click(self):
        await self.app.action_update()


class DirectoryHint(Static):
    def on_resize(self, event: events.Resize):
        self.app._render_workspace_hint(event.size.width)


class PromptInput(TextArea):
    def set_terminal_dark(self, dark: bool) -> None:
        name = 'horizon-cursor-dark' if dark else 'horizon-cursor-light'
        if name not in self.available_themes:
            self.register_theme(TextAreaTheme(
                name=name,
                cursor_style=Style(color='#000000' if dark else '#ffffff',
                                   bgcolor='#ffffff' if dark else '#000000', bold=True),
            ))
        self.theme = name

    class Resized(Message):
        """The editor has rewrapped after a terminal or layout resize."""

    def _watch_has_focus(self, focus: bool) -> None:
        super()._watch_has_focus(focus)
        self.call_after_refresh(self.sync_terminal_cursor)

    def render_line(self, y: int):
        # TextArea's placeholder branch returns a short strip. Always repaint
        # the whole editor row so cleared text/preedit cannot leave stale cells.
        return super().render_line(y).adjust_cell_length(self.content_size.width, self.rich_style)

    def _on_resize(self) -> None:
        super()._on_resize()
        self.call_after_refresh(self.sync_terminal_cursor)
        self.post_message(self.Resized())

    def sync_terminal_cursor(self) -> None:
        """Keep the real terminal caret (the native IME anchor) in this editor."""
        if self.is_mounted and self.has_focus:
            position = self.cursor_screen_offset
            if self.app.cursor_position != position:
                self.app.cursor_position = position
                self.refresh()

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        if event.key == self.app.shortcuts.key('toggle_access'):
            event.stop()
            event.prevent_default()
            self.app.action_toggle_access()
            return
        if self.app.handle_queue_key(event):
            return
        if self.app.handle_command_key(event):
            return
        if event.key == self.app.shortcuts.key("send"):
            event.stop()
            event.prevent_default()
            if self.text.strip():
                self.post_message(self.Submitted(self.text.strip()))
        elif event.key in {"shift+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            self.insert("\n")
        else:
            await super()._on_key(event)


class HelpScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "返回"), ("f1", "dismiss", "返回")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-panel"):
            with VerticalScroll(id="help-content"):
                yield Static(help_text(self.app.language, self.app.shortcuts), markup=False)
            yield Button(self.app.tr("快捷键设置"), id="open-shortcuts")
            yield Button(self.app.tr("返回"), id="close-help")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == 'open-shortcuts':
            self.app.push_screen(ShortcutSettings(), lambda _: self.update_help())
        else:
            self.dismiss()

    def update_help(self):
        self.query_one('#help-content Static', Static).update(help_text(self.app.language, self.app.shortcuts))


class HorizonApp(App):
    TITLE = "Horizon Harness"
    CSS_PATH = "horizon.tcss"
    BINDINGS = [Binding("ctrl+q", "quit", "退出", priority=True),
                Binding("escape", "interrupt", "中断", priority=True, id="interrupt"),
                Binding("ctrl+n", "new_session", "新会话", priority=True, id="new_session"),
                Binding("ctrl+p", "history", "历史会话", priority=True, id="history"),
                Binding("ctrl+pageup", "previous_tab", "", priority=True, id="previous_tab"),
                Binding("ctrl+pagedown", "next_tab", "", priority=True, id="next_tab"),
                Binding("ctrl+w", "close_tab", "", priority=True, id="close_tab"),
                Binding("f1", "help", "帮助", priority=True)]

    def apply_shortcuts(self):
        self.set_keymap(self.shortcuts.values)

    def check_action(self, action, parameters):
        if self.screen_stack and isinstance(self.screen, KeyCapture):
            # Capture must see the chord before any app shortcut can act on it.
            return False
        return super().check_action(action, parameters)

    def get_default_screen(self):
        return TranscriptSelectionScreen(id='_default')

    def get_driver_class(self):
        return color_driver(super().get_driver_class())

    def __init__(self, client, *, welcome: bool = False, preferences: Preferences | None = None,
                 tab_scope: str = 'local', local_directory: str | Path | None = None, updater=None) -> None:
        super().__init__(ansi_color=True)
        self.client = client
        self.updater = updater
        self.update_requested = None
        self._local_directory = local_directory
        self._session_directories = {}
        self._queue = PendingQueue()
        self._queue_session_id = None
        self._queue_sending = False
        self._queue_controlling = False
        self._queue_refreshing = False
        self._queue_retry = None
        self._saved_drafts = {}
        self.preferences = preferences
        self.shortcuts = KeyBindings(preferences.path.with_name('keybindings.json') if preferences else None)
        self._access_mode = (preferences.access_mode if preferences else None) or 'read_only'
        self._access_selected = bool(preferences and preferences.access_mode)
        self.language = preferences.language if preferences else 'en'
        self.client.language = self.language
        self._welcome_screen = WelcomeScreen() if welcome else None
        self._user_message_background: str | None = None
        self.session_id: str | None = None
        self._tab_bar = SessionTabBar()
        self._session_tabs: dict[str, SessionTabState] = {}
        self._closed_tab_views: dict[str, SessionTabState] = {}
        self._active_tab_sessions: set[str] = set()
        self._tabs_lock = asyncio.Lock()
        self._tabs_refreshing = False
        self._tabs_initialized = False
        identity = getattr(client, 'identity', {})
        scope = tab_scope + ('|' + json.dumps(identity, sort_keys=True) if identity else '')
        self._tab_layout_store = TabLayoutStore(preferences.path.with_name('tabs.json'), scope=scope) if preferences else None
        self._restore_tab_id: str | None = None
        self._last_saved_tabs = None
        self._tab_save_error = False
        self._deleting_session = False
        self._deleted_session_ids: set[str] = set()
        self._admitting = False
        self._navigating = False
        self._home_draft = ''
        self.busy = False
        self.phase = ""
        self._phase_role = 'info'
        self._frame = 0
        self._activity_started = time.monotonic()
        self._terminal_rgb: tuple[int, int, int] | None = None
        self._watcher: asyncio.Task | None = None
        self._live: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._cursor = 0
        self._quitting = False
        self._context_lock = asyncio.Lock()
        self._clipboard_lock = asyncio.Lock()
        self._copy_version = 0
        self._generation = 0
        self._prompt_rows = 1
        self._pending_user_message: dict | None = None
        self._last_transcript_status: str | None = None
        self._command_rows: list[dict] = []
        self._command_dismissed_text: str | None = None
        self._command_menu = KeyboardOptionList(id='command-suggestions')
        self._command_menu.can_focus = False
        self._selected_model = ''
        self._notice: tuple[str, str] | None = None
        self._notice_timer = None
        self._activity_widget = Static("", id="activity", markup=False)
        self._home_logo_dark = True
        self._home_logo_elapsed = 0.0
        self._home_logo_tick = time.monotonic()
        self._logo_widget = Static('', id="logo", markup=False)
        self._home_scan = HomeScan(HOME_TITLE_ART)
        self._prompt_widget = PromptInput(id="prompt", placeholder=self.tr("Ask anything, or start a task…"),
                                          highlight_cursor_line=False)
        self.register_theme(Theme(name="horizon", primary="ansi_default", secondary="ansi_default",
                                  accent="ansi_default", foreground="ansi_default", background="ansi_default",
                                  surface="ansi_default", panel="ansi_default", boost="ansi_default",
                                  dark=True))
        self.theme = "horizon"

    def tr(self, source: str, **values) -> str:
        return translate(source, self.language, **values)

    def notify(self, message, *, severity='information', timeout=None, **kwargs):
        # Reuse the reserved footer row: no overlay, layout jump, or focus change.
        if self._notice_timer is not None:
            self._notice_timer.stop()
        self._notice = (str(message), severity)
        self._render_composer_hint()
        duration = timeout if timeout is not None else (6 if severity in {'error', 'warning'} else 3)
        self._notice_timer = self.set_timer(max(.01, duration), self._clear_notice)

    def _clear_notice(self):
        self._notice = None
        self._notice_timer = None
        self._render_composer_hint()

    def _render_composer_hint(self):
        if self._quitting or not self.screen_stack or not self._activity_widget.is_mounted:
            return
        if self._notice is not None:
            message, severity = self._notice
            text = ('! ' if severity in {'error', 'warning'} else '✓ ') + self.tr(message)
            text = ' '.join(text.split())
        hint = Text(no_wrap=True, overflow='ellipsis')
        hint.append('◆', style='bold not dim red' if self._access_mode == 'full_access' else 'dim')
        hint.append('  ')
        if self._notice is not None:
            role = {'error': 'error', 'warning': 'warning'}.get(severity, 'success')
            hint.append(text[:2], style=Style(color=semantic_color(role, self._home_logo_dark), bold=True))
            hint.append(text[2:])
        else:
            hint.append(self._selected_model or self.tr('未选择模型'), style='bold not dim')
        self.query_one('#composer-hint', Static).update(hint)
        self._render_workspace_hint()
        self._render_version_hint()

    def action_toggle_access(self):
        mode = 'full_access' if self._access_mode == 'read_only' else 'read_only'
        try:
            if self.preferences is not None:
                self.preferences.save_access_mode(mode)
        except OSError:
            self.notify('无法保存权限设置', severity='error')
            return
        self._access_mode = mode
        self._access_selected = True
        self._render_composer_hint()

    async def action_language(self):
        if self._deleting_session:
            return
        if isinstance(self.screen, ModalScreen):
            return
        rows = [{'id': code, 'current': code == self.language,
                 'label': ('✓ ' if code == self.language else '  ') + name}
                for code, name in LANGUAGES]
        self.push_screen(SelectionPicker(self.tr('选择语言'), rows),
                         lambda row: self._spawn(self.set_language(row['id'])) if row else None)

    async def set_language(self, language: str):
        if language not in dict(LANGUAGES):
            return
        try:
            if self.preferences is not None:
                self.preferences.save(language)
        except OSError:
            self.notify('无法保存语言设置', severity='error')
            return
        self.language = language
        self.client.language = language
        if not self._activity_widget.is_mounted:
            return
        self.query_one('#history', Button).label = self.tr('会话')
        self.query_one('#new', Button).label = '+'
        self.query_one('#welcome', Static).update(self.tr('把想法留在这里。一起让它发生。'))
        self._prompt_widget.placeholder = '' if self.has_class('chat') else self.tr('Ask anything, or start a task…')
        self._set_model_hint(self._selected_model)
        self._update_command_suggestions()
        await self._refresh_pending()
        self._tick_activity()
        try:
            await self._refresh_context()
        except Exception:
            # A local presentation preference must work while the service is offline.
            pass
        self.notify('语言已更新')
        self._prompt_widget.focus()

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar-row"):
            with Horizontal(id="topbar"):
                yield Static("Horizon harness", id="app-title", markup=False)
                yield self._tab_bar
                yield Button(self.tr("会话"), id="history")
                yield Button('+', id="new")
        with Vertical(id="workspace"):
            with Vertical(id="stage"):
                with Horizontal(id="home"):
                    with Vertical(id="home-intro"):
                        yield Static("Make it happen_", id="home-title", markup=False)
                        yield self._home_scan
                        yield Static(self.tr("把想法留在这里。一起让它发生。"), id="welcome", markup=False)
                    with Vertical(id="home-logo"):
                        yield self._logo_widget
                with Transcript(id="transcript"):
                    yield Vertical(id="messages")
                    yield LiveMarkdown()
                    yield self._activity_widget
                    # Fill only unused space below short conversations. Textual's
                    # sticky anchor otherwise computes a negative scroll offset.
                    yield Static('', id='transcript-tail-space')
                yield self._queue
                yield self._command_menu
                with Horizontal(id="compose-row"):
                    with Vertical(id="composer"):
                        with Horizontal(id="prompt-line"):
                            yield Static(">", id="prompt-prefix", markup=False)
                            yield self._prompt_widget
                with Horizontal(id="composer-footer"):
                    yield Static(self.tr("模型加载中"), id="composer-hint", markup=False)
                    yield DirectoryHint("", id="workspace-hint", markup=False)
                    yield VersionHint(f"v{__version__}", id="version-hint", markup=False)

    def on_mount(self) -> None:
        if self.updater is not None:
            self._spawn(self._check_update())
        # Custom themes otherwise enable Textual's ANSI-to-RGB conversion,
        # which would replace the user's terminal background with a fixed shade.
        self.ansi_color = True
        self.apply_shortcuts()
        self.screen.text_selection_started_signal.subscribe(self, self._selection_started)
        self.add_class("home")
        self.query_one("#transcript").display = False
        self.query_one("#live-output").display = False
        self.query_one("#activity").display = False
        self.query_one("#new").display = True
        self.query_one(PromptInput).focus()
        self.set_interval(.08, self._tick_activity)
        self.set_interval(.1, self._tick_home_scan)
        self._query_terminal_background()
        self._update_home_size()
        self._spawn(self._load_model())
        self._spawn(self._refresh_tabs())
        self.set_interval(3, lambda: self._spawn(self._refresh_tabs()))
        self.set_interval(1, lambda: self._spawn(self._refresh_pending()))
        if self._welcome_screen is not None:
            self.push_screen(self._welcome_screen,
                             lambda _: self._spawn(self._welcome_finished()))

    async def _welcome_finished(self) -> None:
        await self._restore_selected_tab()
        if self._prompt_widget.is_mounted:
            self._prompt_widget.focus()

    async def _load_model(self) -> None:
        session_id, generation = self.session_id, self._generation
        selected = None
        try:
            if session_id:
                if session_id not in self._session_directories and hasattr(self.client, 'session'):
                    try:
                        detail = await self.client.session(session_id)
                        self._session_directories[session_id] = detail.get('directory')
                    except Exception:
                        pass  # Optional metadata must not hide a working model.
                selected = await self.client.session_model(session_id)
                model = selected['model']
            else:
                try:
                    status = await self.client.status()
                except Exception:
                    status = {}  # UI-configured models need no environment default.
                model = status.get('llm', {}).get('model', '')
                if not self._access_selected and status.get('access_mode') in {'read_only', 'full_access'}:
                    self._access_mode = status['access_mode']
                try:
                    catalog = await self.client.models()
                    current = catalog.get('current', {})
                    selected = current if current.get('connection_id') and current.get('model') else None
                    if selected:
                        model = selected['model']
                except Exception:
                    pass
            if self.session_id == session_id and self._generation == generation:
                self._set_model_hint(model)
        except Exception:
            if self.session_id == session_id and self._generation == generation:
                self._set_model_hint('')

    def _set_model_hint(self, model: str):
        self._selected_model = model
        self._render_composer_hint()

    def on_resize(self, event: events.Resize) -> None:
        self._update_home_size(event.size)
        self.call_after_refresh(self._render_workspace_hint)

    def _update_home_size(self, size=None) -> None:
        size = self.size if size is None else size
        self.set_class(size.width < 76, "compact-home")
        self.set_class(size.height < 24, "short-home")
        self.set_class(size.width >= 112 and size.height >= 26, "large-home")
        expanded = size.width >= 76 and size.height >= 24
        self.set_class(expanded, "expanded-logo")
        self._draw_home_logo()

    def _draw_home_logo(self) -> None:
        self._logo_widget.update(render_home_logo(
            width=27,
            height=10 if self.has_class('short-home') else 13,
            dark=self._home_logo_dark,
            elapsed=self._home_logo_elapsed if self._home_scan.enabled else None,
        ))

    def _query_terminal_background(self) -> None:
        if self._driver is not None and getattr(self._driver, 'reports_background', False):
            self._driver.write('\x1b]11;?\x1b\\')
            self._driver.flush()
        elif self._driver is not None:
            rgb = getattr(self._driver, 'background_rgb', None)
            if rgb is not None:
                self.post_message(TerminalBackground(rgb))

    def on_app_focus(self) -> None:
        self._query_terminal_background()

    def on_terminal_background(self, event: TerminalBackground) -> None:
        self._terminal_rgb = event.rgb
        self._user_message_background = message_background(event.rgb)
        self._home_scan.set_background(event.rgb)
        brightness = sum(c * weight for c, weight in zip(event.rgb, (.2126, .7152, .0722)))
        self._home_logo_dark = brightness < 128
        self.set_class(brightness >= 128, 'terminal-light')
        self._draw_home_logo()
        self._prompt_widget.set_terminal_dark(brightness < 128)
        if self._welcome_screen is not None:
            self._welcome_screen.set_dark(brightness < 128)
        for message in self.query('.user-message'):
            message.styles.background = self._user_message_background
        for role in ('info', 'success', 'warning', 'error'):
            for widget in self.query('.semantic-' + role):
                widget.styles.color = semantic_color(role, self._home_logo_dark)
        for tool in self.query(ToolRow):
            tool.set_dark(self._home_logo_dark)
        self._render_composer_hint()
        self._tick_activity()
        self._spawn(self._sync_tabs())
        self._spawn(self._refresh_pending())

    def _tick_home_scan(self) -> None:
        if not self.screen_stack:
            return
        visible = not self._quitting and self.has_class('home') and not isinstance(self.screen, ModalScreen)
        self._home_scan.tick(visible and self.has_class('large-home'))
        now = time.monotonic()
        delta = min(now - self._home_logo_tick, .25)
        self._home_logo_tick = now
        if visible and self._home_scan.enabled:
            self._home_logo_elapsed = (self._home_logo_elapsed + delta) % 3.6
            self._draw_home_logo()

    def _remember_tab_view(self) -> None:
        if self.session_id is None:
            self._home_draft = self._prompt_widget.text
        elif self.session_id in self._session_tabs:
            state = self._session_tabs[self.session_id]
            state.draft = self._prompt_widget.text
            state.cursor = self._prompt_widget.cursor_location
            state.scroll_y = self.query_one('#transcript', VerticalScroll).scroll_y
            state.event_cursor = self._cursor
            state.live = dict(self._live)

    def _ensure_session_tab(self, session: dict) -> None:
        sid = session['id']
        if session.get('directory'):
            self._session_directories[sid] = session['directory']
        if sid not in self._session_tabs:
            self._session_tabs[sid] = self._closed_tab_views.pop(sid, SessionTabState(self.tr('空白会话')))
        title = session.get('title')
        if title:
            self._session_tabs[sid].title = self.tr('空白会话') if title.startswith('New session - ') else title

    async def _sync_tabs(self) -> None:
        if self._tab_bar.is_mounted:
            async with self._tabs_lock:
                await self._tab_bar.show_tabs(self._session_tabs, self.session_id,
                                             self._active_tab_sessions, dark=self._home_logo_dark)
            self._persist_tabs()

    def _persist_tabs(self) -> None:
        if self._tab_layout_store is None or not self._tabs_initialized:
            return
        active = self._restore_tab_id or self.session_id
        layout = (tuple(self._session_tabs), active)
        if layout == self._last_saved_tabs:
            return
        try:
            self._tab_layout_store.save(list(layout[0]), active)
            self._last_saved_tabs = layout
            self._tab_save_error = False
        except (OSError, ValueError):
            if not self._tab_save_error and not self._quitting:
                self.notify('无法保存标签布局', severity='warning')
            self._tab_save_error = True

    async def _restore_selected_tab(self) -> None:
        if self._restore_tab_id is None or isinstance(self.screen, ModalScreen):
            return
        sid = self._restore_tab_id
        self._restore_tab_id = None
        if not self._navigation_blocked():
            await self._open(sid)

    async def _refresh_tabs(self) -> None:
        if self._tabs_refreshing or self._quitting:
            return
        self._tabs_refreshing = True
        try:
            sessions, active = await asyncio.gather(self.client.sessions(), self.client.active())
            sessions = [row for row in sessions if row['id'] not in self._deleted_session_ids]
            self._active_tab_sessions = set(active) - self._deleted_session_ids
            if not self._tabs_initialized:
                layout = None
                if self._tab_layout_store is not None:
                    try:
                        layout = self._tab_layout_store.load()
                    except (OSError, ValueError):
                        self.notify('无法读取标签布局', severity='warning')
                untouched = not self._session_tabs and not self._closed_tab_views and self._generation == 0
                if untouched:
                    if layout is None:
                        for session in sorted(sessions, key=lambda row: row.get('time_updated', 0), reverse=True)[:5]:
                            self._ensure_session_tab(session)
                    else:
                        owned = {row['id']: row for row in sessions}
                        for sid in layout['sessions']:
                            if sid in owned:
                                self._ensure_session_tab(owned[sid])
                        selected = layout['active']
                        if selected is not None and not self._prompt_widget.text:
                            self._restore_tab_id = selected if selected in self._session_tabs else next(iter(self._session_tabs), None)
                self._tabs_initialized = True
            for session in sessions:
                if session['id'] in self._session_tabs:
                    self._ensure_session_tab(session)
            await self._sync_tabs()
            await self._restore_selected_tab()
        except Exception:
            pass  # Foreground SSE reports connection failures; keep open tabs intact.
        finally:
            self._tabs_refreshing = False

    def _navigation_blocked(self) -> bool:
        return self._deleting_session or self._admitting or self._navigating or (
            self.busy and self.session_id not in self._active_tab_sessions)

    async def on_session_tab_bar_selected(self, event: SessionTabBar.Selected) -> None:
        if event.close:
            await self._close_session_tab(event.session_id)
        else:
            await self._open(event.session_id)

    async def _close_session_tab(self, session_id: str) -> None:
        if self._navigation_blocked() or isinstance(self.screen, ModalScreen):
            return
        if session_id not in self._session_tabs:
            return
        self._remember_tab_view()
        keys = list(self._session_tabs)
        index = keys.index(session_id)
        if self.session_id == session_id:
            others = [sid for sid in keys if sid != session_id]
            if others:
                await self._open(others[min(index, len(others) - 1)])
            else:
                await self.action_new_session()
        self._closed_tab_views[session_id] = self._session_tabs.pop(session_id)
        await self._sync_tabs()

    async def action_close_tab(self) -> None:
        if self.session_id:
            await self._close_session_tab(self.session_id)

    async def _cycle_tab(self, offset: int) -> None:
        keys = list(self._session_tabs)
        if keys:
            index = keys.index(self.session_id) if self.session_id in keys else -1
            await self._open(keys[(index + offset) % len(keys)])

    async def action_previous_tab(self) -> None:
        await self._cycle_tab(-1)

    async def action_next_tab(self) -> None:
        await self._cycle_tab(1)

    def _spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _show_chat(self) -> None:
        self._prompt_widget.placeholder = ''
        self.remove_class("home")
        self.add_class("chat")
        self.query_one("#home").display = False
        self.query_one("#transcript").display = True
        self.query_one("#new").display = True

    def _set_phase(self, text: str, *, busy: bool | None = None, role: str | None = None) -> None:
        if busy is True and not self.busy:
            self._activity_started = time.monotonic()
        self.phase = text
        self._phase_role = role or phase_role(text)
        if busy is not None:
            self.busy = busy
            if self.session_id:
                if busy and text not in {'正在打开会话', '正在切换模型', '正在连接'}:
                    self._active_tab_sessions.add(self.session_id)
                elif not busy:
                    self._active_tab_sessions.discard(self.session_id)
                if self._tab_bar.is_mounted:
                    self._spawn(self._sync_tabs())
        if not self._activity_widget.is_mounted:
            return
        self._activity_widget.display = bool(text) and (self.busy or text not in {"已完成", "已同步会话"})
        self._tick_activity()

    def _tick_activity(self) -> None:
        if self._quitting or not self.screen_stack or not self._activity_widget.is_mounted:
            return
        # Position-only changes (such as a status line appearing) do not always
        # dispatch a Resize event to TextArea, so also reconcile after layout.
        self._prompt_widget.sync_terminal_cursor()
        focused = self.screen.focused
        if isinstance(focused, Input) and focused.has_focus:
            position = focused.cursor_screen_offset
            if self.cursor_position != position:
                self.cursor_position = position
        self._frame = (self._frame + 1) % 10
        self._tab_bar.animate_running(self._frame)
        self._activity_widget.styles.color = semantic_color(self._phase_role, self._home_logo_dark)
        self._activity_widget.update(activity_frame(
            self.tr(self.phase), time.monotonic() - self._activity_started,
            busy=self.busy, dark=self._home_logo_dark, rgb=self._terminal_rgb,
            role=self._phase_role, animate=self._home_scan.enabled,
        ))

    def _render_workspace_hint(self, width=None):
        if not self._activity_widget.is_mounted:
            return
        directory = self._session_directories.get(self.session_id) if self.session_id else self._local_directory
        field = self.query_one('#workspace-hint', Static)
        available = field.content_size.width if width is None else width
        path = workspace_hint(directory, home=Path.home() if self._local_directory else None,
                              max_cells=max(0, available - 3))
        field.update(Text(' · ', style='dim') + path if path.plain else Text(''))
        field.tooltip = workspace_hint(directory, max_cells=4096) if directory else None

    async def _refresh_pending(self):
        if self._queue_refreshing or not self._queue.is_mounted:
            return
        sid, generation = self.session_id, self._generation
        self._queue_refreshing = True
        try:
            result = await self.client.pending(sid) if sid and hasattr(self.client, 'pending') else {'pending': []}
            if (sid, generation) == (self.session_id, self._generation):
                self._queue_session_id = sid
                self._queue.background = self._user_message_background
                await self._queue.show(result.get('pending', []), self.tr)
        except Exception:
            pass  # A polling outage must not erase acknowledged queue rows.
        finally:
            self._queue_refreshing = False

    def handle_queue_key(self, event):
        if isinstance(self.screen, ModalScreen):
            return False
        key = event.key
        if self._queue_session_id != self.session_id:
            return False
        empty = not self._prompt_widget.text.strip()
        if key == self.shortcuts.key('restore') and self._saved_drafts.get(self.session_id):
            drafts = self._saved_drafts[self.session_id]
            current = (self._prompt_widget.text, self._access_mode)
            text, mode = drafts.pop()
            self._prompt_widget.load_text(text)
            self._access_mode = mode
            self._render_composer_hint()
            if current[0]:
                drafts.insert(0, current)
        elif key == self.shortcuts.key('recall') and self._queue.selected:
            self._spawn(self._control_pending(self.session_id, self._queue.selected, 'recall'))
        elif empty and self._queue.rows and key in {'up', 'down'}:
            self._queue.move(-1 if key == 'up' else 1)
            self._spawn(self._queue.show(self._queue.rows, self.tr))
        elif empty and self._queue.rows and key == self.shortcuts.key('send'):
            self.action_queue_guide()
        else:
            return False
        event.stop()
        event.prevent_default()
        return True

    async def _enqueue(self, text, mode):
        sid, generation = self.session_id, self._generation
        signature = (sid, text, mode)
        if not self._queue_retry or self._queue_retry[0] != signature:
            self._queue_retry = (signature, 'msg_' + uuid.uuid4().hex)
        try:
            await self.client.prompt(sid, text, message_id=self._queue_retry[1], access_mode=mode, delivery='queue')
            self._queue_retry = None
            if (sid, generation) == (self.session_id, self._generation):
                if self._prompt_widget.text.strip() == text:
                    self._prompt_widget.clear()
                await self._refresh_pending()
        except Exception:
            self.notify('排队失败，消息仍在输入框中', severity='error')
        finally:
            self._queue_sending = False

    def action_queue_guide(self, message_id=None):
        if self._deleting_session:
            return
        if isinstance(self.screen, ModalScreen) or self._queue_controlling or self._queue_session_id != self.session_id:
            return
        sid = self.session_id
        mid = message_id or self._queue.selected
        row = next((r for r in self._queue.rows if r['message_id'] == mid), None)
        if row:
            self.push_screen(QueueConfirm(row['prompt']['text'], self.tr),
                             lambda confirmed: self._spawn(self._control_pending(sid, mid, 'steer')) if confirmed else None)

    async def _control_pending(self, sid, mid, action):
        if self._queue_controlling or sid != self.session_id:
            return
        self._queue_controlling = True
        try:
            if action == 'steer':
                await self.client.steer_pending(sid, mid)
            else:
                result = await self.client.recall_pending(sid, mid)
                if self._queue_retry and self._queue_retry[1] == mid:
                    self._queue_retry = None
                if action == 'recall' and self.session_id == sid:
                    draft = self._prompt_widget.text
                    if draft:
                        # Keep every displaced draft; repeated recalls form a reversible stack.
                        self._saved_drafts.setdefault(sid, []).append((draft, self._access_mode))
                    self._prompt_widget.load_text(result['prompt']['text'])
                    if result.get('access_mode') in {'read_only', 'full_access'}:
                        self._access_mode = result['access_mode']
                        self._render_composer_hint()
            if self.session_id == sid:
                await self._refresh_pending()
                if action == 'steer':
                    await self._refresh_context()
        except Exception:
            self.notify('消息状态已变化，请重试', severity='warning')
            await self._refresh_pending()
        finally:
            self._queue_controlling = False
            self._prompt_widget.focus()

    async def on_pending_queue_action(self, event: PendingQueue.Action):
        if event.action == 'steer':
            self.action_queue_guide(event.message_id)
        else:
            await self._control_pending(self.session_id, event.message_id, event.action)

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        if event.text.strip().casefold() == 'exit':
            self.exit()
            return
        if self._deleting_session or self._prompt_widget.disabled:
            return
        commands = {'/models': 'models', '/session': 'session', '/sessions': 'session', '/new': 'new', '/help': 'help', '/language': 'language', '/search': 'search', '/update': 'update'}
        if event.text in commands:
            navigates = event.text in {'/session', '/sessions', '/new'} and not self._navigation_blocked()
            if self.busy and event.text not in {'/help', '/language'} and not navigates:
                self.notify('请先按 Esc 中断当前任务', severity='warning')
                return
            self._prompt_widget.clear()
            await self._run_command(commands[event.text])
            return
        if self.busy:
            if not self._queue_sending and not self._admitting and self.session_id:
                self._queue_sending = True
                self._spawn(self._enqueue(event.text, self._access_mode))
            return
        if not self.busy:
            self._spawn(self._send(event.text, access_mode=self._access_mode))
            # Set synchronously to prevent two Enter events admitting two prompts.
            self._set_phase("正在连接", busy=True)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new":
            await self.action_new_session()
        elif event.button.id == "history":
            await self.action_history()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self._prompt_widget:
            self._resize_prompt()
            self._update_command_suggestions()

    def _update_command_suggestions(self):
        text = self._prompt_widget.text
        if text != self._command_dismissed_text:
            self._command_dismissed_text = None
        rows = []
        if text.startswith('/') and '\n' not in text and text != self._command_dismissed_text:
            for name, description in (('models', '切换模型'), ('session', '历史会话'),
                                      ('new', '新建会话'), ('help', '命令与快捷键'), ('language', '界面语言'), ('search', '联网搜索'), ('update', '版本更新')):
                if name.startswith(text[1:].casefold()):
                    rows.append({'id': name, 'label': f'/{name:<10} {self.tr(description)}'})
        menu = self._command_menu
        self._command_rows = rows
        menu.clear_options()
        menu.add_options([Option(Text(row['label'])) for row in rows])
        menu.highlighted = 0 if rows else None
        menu.display = bool(rows)

    def handle_command_key(self, event: events.Key) -> bool:
        menu = self._command_menu
        if not menu.display or not self._command_rows or event.key not in {'up', 'down', 'enter'}:
            return False
        event.stop()
        event.prevent_default()
        if event.key in {'up', 'down'}:
            menu.highlighted = ((menu.highlighted or 0) + (1 if event.key == 'down' else -1)) % len(self._command_rows)
        else:
            self._command_picked(self._command_rows[menu.highlighted or 0])
        return True

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_list is self._command_menu and event.option_index < len(self._command_rows):
            self._command_picked(self._command_rows[event.option_index])

    def _command_picked(self, row: dict | None):
        self._command_menu.display = False
        if row is not None:
            self._prompt_widget.clear()
            self._spawn(self._run_command(row['id']))

    async def _run_command(self, name: str):
        if name == 'models':
            await self.action_models()
        elif name == 'session':
            await self.action_history()
        elif name == 'new':
            await self.action_new_session()
        elif name == 'help':
            self.action_help()
        elif name == 'language':
            await self.action_language()
        elif name == 'search':
            await self.action_search()
        elif name == 'update':
            await self.action_update()

    async def _check_update(self):
        await self.updater.check()
        if not self._quitting:
            self._render_version_hint()

    def _render_version_hint(self):
        if not self._activity_widget.is_mounted:
            return
        hint = Text(f'v{__version__}', style='dim', no_wrap=True, overflow='ellipsis')
        if self.updater and self.updater.latest and self.updater.should_notify(self.updater.latest):
            hint.append(' · ' + self.tr('有更新') + ' ↑', style=Style(color=semantic_color('warning', self._home_logo_dark), dim=False))
        self.query_one('#version-hint', Static).update(hint)

    async def action_update(self):
        if self._deleting_session:
            return
        if isinstance(self.screen, ModalScreen):
            return
        if self.busy or self._admitting or self._navigating or self._active_tab_sessions:
            self.notify('请先按 Esc 中断当前任务', severity='warning')
            return
        if self.updater is None:
            self.notify('当前从源码运行，请通过 Git 更新源码')
            return
        from tui.update_dialog import UpdateDialog
        self.push_screen(UpdateDialog(self.updater), self._update_picked)

    def _update_picked(self, version):
        self._render_version_hint()
        if version:
            if self.busy or self._admitting or self._active_tab_sessions:
                self.notify('请先按 Esc 中断当前任务', severity='warning')
                return
            self.update_requested = version
            self.exit()

    async def action_search(self):
        if self._deleting_session:
            return
        if self.busy or isinstance(self.screen,ModalScreen):
            self.notify('请先按 Esc 中断当前任务',severity='warning')
            return
        context=(self.session_id,self._generation)
        try:
            status=await self.client.search_config()
            if self.busy or isinstance(self.screen,ModalScreen) or context!=(self.session_id,self._generation):
                return
            def saved(result):
                if result is not None:
                    self.notify('搜索配置已保存' if result.get('search_available') else '已保存，请检查搜索组件是否可用',
                                severity='information' if result.get('search_available') else 'warning')
            self.push_screen(SearchConfigForm(self.client,status),saved)
        except Exception:
            self.notify('无法读取搜索配置',severity='error')

    async def action_models(self):
        if self._deleting_session:
            return
        if self.busy or isinstance(self.screen, ModalScreen):
            self.notify('请先按 Esc 中断当前任务', severity='warning')
            return
        context = (self.session_id, self._generation)
        try:
            catalog = await self.client.models()
            selected = catalog['current']
            if self.busy or context != (self.session_id, self._generation) or isinstance(self.screen, ModalScreen):
                return
            self._show_saved_models(catalog, selected, context)
        except Exception:
            self.notify('无法读取模型列表，请检查本地服务', severity='error')

    def _show_saved_models(self, catalog: dict, selected: dict, context: tuple):
        if self.busy or context != (self.session_id, self._generation):
            return
        recent = [row for row in catalog.get('recent', []) if row.get('configured')]
        back = lambda: self._show_saved_models(catalog, selected, context)
        if not recent:
            self._show_model_providers(catalog['models'], selected, context)
            return
        rows = []
        for row in recent:
            current = row['connection_id'] == selected['connection_id'] and row['model'] == selected['model']
            brand = PROVIDER_NAMES.get(row['connection_id'], row['provider'])
            rows.append({**row, 'current': current,
                         'label': ('✓ ' if current else '  ') + brand + ' · ' + row['model']})
        rows.sort(key=lambda row: (PROVIDER_ORDER.get(row['connection_id'], len(PROVIDER_ORDER)), not row['current']))
        rows.append({'id': '__add_model__', 'label': self.tr('＋ 添加模型')})
        def picked(row):
            if row is None or self.busy or context != (self.session_id, self._generation):
                return
            if row['id'] == '__add_model__':
                self._show_model_providers(catalog['models'], selected, context, back=back)
            else:
                self._spawn(self._model_picked(row))
        self.push_screen(SelectionPicker(self.tr('已保存的模型'), rows, models=True), picked)

    def _show_model_providers(self, models: list[dict], selected: dict, context: tuple, *, back=None):
        if self.busy or context != (self.session_id, self._generation):
            return
        groups: dict[str, list[dict]] = {}
        for row in models:
            groups.setdefault(row['connection_id'], []).append(row)
        rows = []
        visible = visible_provider_ids(groups, selected['connection_id'])
        for key, group in groups.items():
            if key not in visible:
                continue
            name = PROVIDER_NAMES.get(key, group[0]['provider'])
            group = [{**row, 'provider': name} for row in group]
            group.sort(key=lambda row: (row['model'] != selected['model'], row['model'].casefold()))
            current = key == selected['connection_id']
            rows.append({**group[0], '_models': group, 'current': current,
                         'label': f"{'✓' if current else ' '} {group[0]['provider']}" +
                                  ('  · ' + self.tr('已配置') if group[0]['configured'] else '')})
        rows.sort(key=lambda row: (PROVIDER_ORDER.get(row['connection_id'], len(PROVIDER_ORDER)),
                                   row['connection_id'] != selected['connection_id'], not row['configured'],
                                   row['provider'].casefold()))
        rows.append({'id': '__custom__', 'label': self.tr('＋ 自定义服务'), 'configured': False})
        def picked(row):
            if self.busy or context != (self.session_id, self._generation):
                return
            if row is None:
                if back is not None:
                    back()
                return
            if row['id'] == '__custom__' or row.get('_configure'):
                self._spawn(self._model_picked(row))
                return
            choices = [{**item, 'label': ('✓ ' if item['connection_id'] == selected['connection_id'] and
                                        item['model'] == selected['model'] else '  ') + item['model']}
                       for item in row['_models']]
            def model_picked(choice):
                if self.busy or context != (self.session_id, self._generation):
                    return
                if choice is None:
                    self._show_model_providers(models, selected, context, back=back)
                else:
                    self._spawn(self._model_picked(choice))
            self.push_screen(SelectionPicker(row['provider'] + ' · ' + self.tr('选择模型'), choices, models=True), model_picked)
        self.push_screen(SelectionPicker(self.tr('选择模型服务'), rows, models=True), picked)

    async def _model_picked(self, row: dict):
        if self.busy:
            self.notify('请先按 Esc 中断当前任务', severity='warning')
            return
        context = (self.session_id, self._generation)
        try:
            if not row.get('configured') or row.get('_configure'):
                connection = await self.client.connection(row['connection_id']) if row['id'] != '__custom__' else {}
                if self.busy or context != (self.session_id, self._generation) or isinstance(self.screen, ModalScreen):
                    return
                self.push_screen(ConnectionForm(self.client, row, connection),
                                 lambda selected: self._spawn(self._apply_model(selected, expected=context)) if selected else None)
            else:
                await self._apply_model(row, expected=context)
        except Exception as exc:
            self.notify(str(exc) if isinstance(exc, RuntimeError) else '无法打开模型配置', severity='error')

    async def _apply_model(self, row: dict, *, expected: tuple | None = None):
        if expected is not None and expected != (self.session_id, self._generation):
            return
        if self.busy:
            self.notify('请先按 Esc 中断当前任务', severity='warning')
            return
        self._set_phase('正在切换模型', busy=True)
        try:
            selected = await self.client.set_default_model(row['connection_id'], row['model'])
            if self.session_id is None:
                self._set_model_hint(selected['model'])
            self.notify('默认模型已更新，新会话生效')
            self._prompt_widget.focus()
            await self._sync_tabs()
        except Exception as exc:
            self.notify(str(exc) if isinstance(exc, RuntimeError) else '切换模型失败，请检查本地服务', severity='error')
        finally:
            if self.phase == '正在切换模型':
                self._set_phase('', busy=False)

    def on_prompt_input_resized(self) -> None:
        self._resize_prompt()

    def _resize_prompt(self) -> None:
        field = self._prompt_widget
        if not field.is_mounted:
            return
        rows = min(max(1, field.wrapped_document.height), 6, max(1, self.size.height // 3))
        if rows != self._prompt_rows:
            self._prompt_rows = rows
            field.styles.height = rows
            field.call_after_refresh(field.scroll_cursor_visible)
            field.call_after_refresh(field.sync_terminal_cursor)

    async def _send(self, text: str, *, access_mode: str | None = None) -> None:
        if self._deleting_session or self._prompt_widget.disabled:
            return
        self._restore_tab_id = None
        self._admitting = True
        mode = access_mode or self._access_mode
        self._generation += 1
        generation = self._generation
        pending = {"id": f"msg_{uuid.uuid4().hex}", "type": "user", "text": text}
        self._pending_user_message = pending
        admitted = False
        try:
            self._show_chat()
            self._live.clear()
            self._update_live_output("")
            async with self._context_lock:
                await self.query_one("#messages", Vertical).mount(self._user_message_widget(pending, pending=True))
            self._prompt_widget.clear()
            self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)
            if self.session_id is None:
                session = await self.client.create()
                if generation != self._generation:
                    return
                self.session_id = session["id"]
                self._ensure_session_tab(session)
                await self._load_model()
                self._home_draft = ''
                self._cursor = 0
            self._ensure_session_tab({'id': self.session_id})
            state = self._session_tabs[self.session_id]
            if state.title == self.tr('空白会话'):
                state.title = ' '.join(text.split())[:50]
            state.draft = ''
            await self._sync_tabs()
            if self._watcher is None or self._watcher.done():
                self._watcher = self._spawn(self._watch(self.session_id))
            await self.client.prompt(self.session_id, text, message_id=pending["id"], access_mode=mode)
            admitted = True
            if generation != self._generation:
                return
            await self._refresh_context()
            # run.started may already have arrived while admission was in flight.
            if self.busy and self.phase == "正在连接":
                self._set_phase("正在思考")
        except Exception as exc:
            if generation != self._generation:
                return
            if admitted:
                self._set_phase("消息已发送，正在同步会话")
                return
            async with self._context_lock:
                self._pending_user_message = None
                await self.query(".pending-user").remove()
            if not self._prompt_widget.text:
                self._prompt_widget.load_text(text)
            self._set_phase(str(exc) if isinstance(exc, RuntimeError) else "发送失败，请检查本地服务", busy=False, role='error')
        finally:
            self._admitting = False

    async def _watch(self, session_id: str) -> None:
        failures = 0
        while self.session_id == session_id and not self._quitting:
            try:
                async for event in self.client.events(session_id, after=self._cursor):
                    failures = 0
                    if self.session_id != session_id or self._quitting or not self._activity_widget.is_mounted:
                        return
                    await self.handle_event(event)
                    # Acknowledge only after any canonical context refresh has
                    # succeeded, so a transient HTTP failure can replay the event.
                    if "id" in event:
                        self._cursor = event["id"]
                raise ConnectionError("event stream closed")
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._quitting or not self._activity_widget.is_mounted:
                    return
                failures += 1
                # A lost connection does not prove a task stopped. Preserve the
                # admission guard and keep reconnecting without resending it.
                self._set_phase("连接已断开，正在重试" if failures >= 4 else "正在重新连接")
                await asyncio.sleep(min(failures * .5, 2))

    async def handle_event(self, event: dict) -> None:
        kind, data = event["type"], event.get("data", {})
        if kind in {"run.started", "run.ended", "session.snapshot", "session.next.input.controlled", "session.next.prompted", "session.next.prompt.admitted"}:
            await self._refresh_pending()
        if kind in {'session.next.input.controlled', 'session.next.prompted'}:
            await self._refresh_context()
        elif kind == 'session.next.compaction.started':
            self._set_phase('正在压缩上下文', busy=True)
        elif kind in {'session.next.compaction.ended', 'session.compaction.failed'}:
            self._set_phase('正在思考', busy=True)
        elif kind in {"run.started", "llm.started"}:
            self._set_phase("正在思考", busy=True)
        elif kind == "llm.delta":
            key = data.get("call_id", "response")
            self._live[key] = self._live.get(key, "") + data.get("text", "")
            self._update_live_output("\n\n".join(self._live.values()))
            self._set_phase("正在回答", busy=True)
            self._scroll_if_at_end()
        elif kind == "session.next.tool.called":
            self._set_phase("正在执行 · " + data.get("name", self.tr("工具")), busy=True)
            await self._refresh_context()
        elif kind in {"session.next.tool.success", "session.next.tool.failed"}:
            self._set_phase("正在处理结果")
            await self._refresh_context()
        elif kind in {"session.next.text.ended", "session.next.synthetic"}:
            await self._refresh_context()
            self._live.clear()
            self._update_live_output("")
        elif kind == "run.ended":
            status = data.get("status", "failed")
            self._set_phase({"completed": "已完成", "interrupted": "已中断"}.get(status, '执行失败'), busy=False,
                            role={'completed': 'success', 'interrupted': 'warning'}.get(status, 'error'))
            await self._refresh_context()
            if self._last_transcript_status == status:
                self._set_phase("", busy=False)
            if status == "completed":
                self._live.clear()
                self._update_live_output("")
        elif kind == "session.snapshot":
            self._live.clear()
            self._update_live_output("")
            self._set_phase("正在执行" if data.get("active") else "已同步会话", busy=bool(data.get("active")))
            await self._render_context(data.get("messages", []))

    def on_text_selected(self, event: events.TextSelected) -> None:
        # TextSelected is emitted after mouse release, once selection is settled.
        # Inputs and credential forms have separate editing/copy behavior.
        if isinstance(self.screen, ModalScreen):
            return
        screen = self.screen
        text = selected_body_text(screen)
        if not text or not text.strip():
            return
        self._copy_version += 1
        self._spawn(self._copy_selected_text(text, self._copy_version,
                                           (self.session_id, self._generation, screen),
                                           dict(screen.selections)))

    def _selection_started(self, screen):
        # A slow earlier copy must never clear a new drag or announce its result.
        self._copy_version += 1

    async def _copy_selected_text(self, text, version, context, selection):
        async with self._clipboard_lock:
            if version != self._copy_version:
                return
            try:
                await copy_text(text, self)
            except (OSError, ValueError, asyncio.TimeoutError):
                if version == self._copy_version and context == (self.session_id, self._generation, self.screen):
                    self.notify('复制失败，请重试', severity='warning')
            else:
                if version == self._copy_version and context == (self.session_id, self._generation, self.screen):
                    self.notify('已复制', timeout=2)
                    context[2].call_after_refresh(self._clear_copied_selection, version, context, selection)

    def _clear_copied_selection(self, version, context, selection):
        if (version == self._copy_version
                and context == (self.session_id, self._generation, self.screen)
                and self.screen.selections == selection):
            self.screen.clear_selection()

    def _scroll_if_at_end(self) -> None:
        view = self.query_one("#transcript", VerticalScroll)
        if view.is_vertical_scroll_end:
            view.scroll_end(animate=False)

    def _update_live_output(self, text: str) -> None:
        if not self._activity_widget.is_mounted:
            return
        self.query_one("#live-output", LiveMarkdown).set_text(text)

    async def _refresh_context(self) -> None:
        async with self._context_lock:
            session_id = self.session_id
            if session_id:
                messages = await self.client.context(session_id)
                if self.session_id == session_id and self._activity_widget.is_mounted:
                    await self._replace_context(messages)

    async def _render_context(self, messages: list[dict]) -> None:
        session_id = self.session_id
        async with self._context_lock:
            if self.session_id == session_id:
                await self._replace_context(messages)

    async def _replace_context(self, messages: list[dict]) -> None:
        view = self.query_one("#transcript", Transcript)
        follow = view.is_vertical_scroll_end
        old_scroll = view.scroll_y
        revision = view.scroll_revision
        session_id, generation = self.session_id, self._generation
        # Avoid publishing an empty intermediate transcript, which clamps scroll
        # offsets and can re-enable the bottom anchor while reading history.
        with self.batch_update():
            await self._populate_context(messages)
        if self._navigating:
            # Tab navigation restores its own viewport after layout, rather than
            # replaying the previous tab's scrolling intent over that position.
            return
        def restore_scroll() -> None:
            # Rebuilding can yield to wheel/key events or a tab switch. An old
            # snapshot must never override a newer reading/navigation intent.
            if (not view.is_mounted or view.scroll_revision != revision
                    or self.session_id != session_id or self._generation != generation):
                return
            if follow:
                view.scroll_end(animate=False, immediate=True)
            else:
                view.scroll_to(y=old_scroll, animate=False, force=True, immediate=True)

        view.call_after_refresh(restore_scroll)

    async def _populate_context(self, messages: list[dict]) -> None:
        host = self.query_one("#messages", Vertical)
        if self._pending_user_message is not None:
            pending_id = self._pending_user_message["id"]
            if any(message.get("id") == pending_id for message in messages):
                self._pending_user_message = None
            else:
                messages = [*messages, self._pending_user_message]
        messages = [m for m in messages if not (m.get('type') == 'user' and m.get('delivery_state') == 'pending' and m.get('delivery') == 'queue')]
        messages = presentation_messages(messages)
        await host.remove_children()
        self._last_transcript_status = messages[-1].get('status') if messages and messages[-1].get('type') == 'run_status' else None
        turn_started = None
        last_user_index = max((i for i, item in enumerate(messages) if item.get("type") == "user"), default=-1)
        for index, message in enumerate(messages):
            kind, data = message.get("type"), message
            if kind == "user":
                turn_started = data.get("time", {}).get("created", data.get("time_created"))
                await host.mount(self._user_message_widget(data, pending=data is self._pending_user_message))
            elif kind == 'run_status':
                label = self.tr('已中断' if data.get('status') == 'interrupted' else '执行失败')
                role = 'warning' if data.get('status') == 'interrupted' else 'error'
                outcome = Static(label, classes='run-outcome semantic-' + role, markup=False)
                outcome.styles.color = semantic_color(role, self._home_logo_dark)
                await host.mount(outcome)
            elif kind in {"assistant", "synthetic"}:
                parts = data.get("content", []) if kind == "assistant" else [{"type": "text", "text": data.get("text", "")}]
                if not parts:
                    continue
                for part in parts:
                    if part.get("type") == "text" and part.get("text"):
                        await host.mount(Markdown(part["text"], classes="answer"))
                    elif part.get("type") == "tool":
                        state = part.get("state", {})
                        detail = json.dumps(state, ensure_ascii=False, indent=2)
                        if len(detail) > 6000:
                            detail = detail[:6000] + "\n" + self.tr("…（完整结果保存在会话记录中）")
                        await host.mount(ToolRow(part.get('name', self.tr('工具')), state.get('status', ''),
                                                 detail, dark=self._home_logo_dark))
                if not data.get('error') and data.get("finish") in {"stop", "length"} and (not self.busy or index < last_user_index):
                    model = data.get("model")
                    completed = data.get("time", {}).get("completed")
                    has_text = any(part.get("type") == "text" and part.get("text") for part in parts)
                    if isinstance(model, str) and model and has_text:
                        detail = model
                        if isinstance(turn_started, (int, float)) and isinstance(completed, (int, float)):
                            detail += f" · {max(0, completed - turn_started) / 1000:.1f}s"
                        await host.mount(Static(Text(detail), classes="answer-meta"))
    def _user_message_widget(self, message: dict, *, pending: bool = False) -> Static:
        widget = Static(Text("› " + message.get("text", "")),
                        classes="user-message pending-user" if pending else "user-message")
        if self._user_message_background:
            widget.styles.background = self._user_message_background
        return widget

    async def action_interrupt(self) -> None:
        if self._command_menu.display and not isinstance(self.screen, ModalScreen):
            self._command_dismissed_text = self._prompt_widget.text
            self._command_menu.display = False
            return
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)
            return
        if self.busy and self.session_id:
            self._set_phase("正在中断")
            self._spawn(self._interrupt(self.session_id, self._generation))

    async def _interrupt(self, session_id: str, generation: int) -> None:
        try:
            await self.client.interrupt(session_id)
            if self.session_id == session_id and self._generation == generation:
                self._set_phase("已中断", busy=False)
        except Exception:
            if self.session_id == session_id and self._generation == generation:
                self._set_phase("中断请求未完成，任务可能仍在执行")

    async def action_new_session(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if self._navigation_blocked():
            self.notify("请先按 Esc 中断当前任务", severity='warning')
            return
        self._navigating = True
        self._restore_tab_id = None
        try:
            self._remember_tab_view()
            if self._watcher:
                self._watcher.cancel()
            self.session_id = None
            self._queue.display = False
            await self._refresh_pending()
            self._generation += 1
            self._cursor = 0
            self._pending_user_message = None
            self._live.clear()
            self._update_live_output("")
            await self._render_context([])
            self.query_one("#home").display = True
            self.query_one("#transcript").display = False
            self.query_one("#new").display = True
            self.add_class("home")
            self.remove_class("chat")
            self._prompt_widget.placeholder = self.tr("Ask anything, or start a task…")
            self._set_phase("", busy=False)
            await self._load_model()
            self.query_one(PromptInput).clear()
            self._prompt_widget.load_text(self._home_draft)
            self.query_one(PromptInput).focus()
            await self._sync_tabs()
        finally:
            self._navigating = False

    async def action_history(self) -> None:
        if self._navigation_blocked() or isinstance(self.screen, ModalScreen):
            return
        try:
            sessions = await self.client.sessions()
            if self._navigation_blocked() or isinstance(self.screen, ModalScreen):
                return
            rows = []
            for session in sorted(sessions, key=lambda s: s.get('time_updated', 0), reverse=True):
                age = max(0, time.time() - session.get('time_updated', time.time() * 1000) / 1000)
                when = self.tr('刚刚') if age < 60 else self.tr('{n} 分钟前', n=int(age // 60)) if age < 3600 else self.tr('{n} 小时前', n=int(age // 3600)) if age < 86400 else self.tr('{n} 天前', n=int(age // 86400))
                title = session.get('title') or ''
                if not title or title.startswith('New session - '):
                    title = self.tr('空白会话')
                label = ('✓ ' if session['id'] == self.session_id else '  ') + title
                rows.append({'id': session['id'], 'title': title, 'label': label + '  · ' + when})
            def picked(row):
                if row:
                    self._spawn(self._delete_session(row['id']) if row.get('_delete') else self._open(row['id']))
            self.push_screen(SessionHistoryPicker(self.tr('历史会话'), rows), picked)
        except Exception:
            self.notify("无法读取历史会话", severity='error')

    async def _delete_session(self, session_id: str) -> None:
        if self._deleting_session or self._admitting or self._navigating:
            return
        self._deleting_session = True
        was_disabled = self._prompt_widget.disabled
        self._prompt_widget.disabled = True
        deleted = False
        try:
            await self.client.delete_session(session_id)
            deleted = True
            self._deleted_session_ids.add(session_id)
            self._session_tabs.pop(session_id, None)
            self._closed_tab_views.pop(session_id, None)
            self._session_directories.pop(session_id, None)
            self._saved_drafts.pop(session_id, None)
            if self._queue_retry and self._queue_retry[0][0] == session_id:
                self._queue_retry = None
            self._active_tab_sessions.discard(session_id)
            if self._restore_tab_id == session_id:
                self._restore_tab_id = None
        except Exception as exc:
            self.notify(str(exc) if isinstance(exc, RuntimeError) else '无法删除会话，请重试', severity='error')
        except BaseException:
            self._prompt_widget.disabled = was_disabled
            raise
        finally:
            self._deleting_session = False
        try:
            if deleted:
                if self.session_id == session_id:
                    self._set_phase('', busy=False)
                    await self.action_new_session()
                await self._sync_tabs()
                self.notify('会话已删除')
        finally:
            self._prompt_widget.disabled = was_disabled
            if not was_disabled:
                self._prompt_widget.focus()
        if not self._quitting:
            await self.action_history()

    async def _open(self, session_id: str) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if self.session_id == session_id and self.has_class('chat'):
            return
        if self._navigation_blocked():
            self.notify('请先按 Esc 中断当前任务', severity='warning')
            return
        self._restore_tab_id = None
        self._remember_tab_view()
        self._navigating = True
        self._set_phase('正在打开会话', busy=True)
        try:
            if self._watcher:
                self._watcher.cancel()
            self.session_id = session_id
            self._queue.display = False
            self._ensure_session_tab({'id': session_id})
            state = self._session_tabs[session_id]
            self._cursor = state.event_cursor
            self._pending_user_message = None
            self._generation += 1
            self._live.clear()
            self._live.update(state.live)
            self._update_live_output("\n\n".join(self._live.values()))
            self._show_chat()
            await self._load_model()
            await self._refresh_pending()
            await self._refresh_context()
            self._set_phase("正在执行" if session_id in self._active_tab_sessions else "",
                            busy=session_id in self._active_tab_sessions)
            state = self._session_tabs[session_id]
            self._prompt_widget.load_text(state.draft)
            self._prompt_widget.move_cursor(state.cursor)
            view = self.query_one('#transcript', Transcript)
            saved_y = state.scroll_y
            generation, revision = self._generation, view.scroll_revision
            def restore_tab_scroll():
                if (self.session_id != session_id or self._generation != generation
                        or view.scroll_revision != revision or not view.is_mounted):
                    return
                if saved_y is None or saved_y >= view.max_scroll_y:
                    view.anchor()
                    view.scroll_end(animate=False, immediate=True)
                else:
                    view.scroll_to(y=saved_y, animate=False, force=True, immediate=True)
            view.call_after_refresh(restore_tab_scroll)
            self._watcher = self._spawn(self._watch(session_id))
            self.query_one(PromptInput).focus()
            await self._sync_tabs()
        except Exception:
            self._set_phase("无法打开会话", busy=False)
        finally:
            self._navigating = False

    def action_help(self) -> None:
        if self._deleting_session:
            return
        if not isinstance(self.screen, ModalScreen):
            self.push_screen(HelpScreen())

    async def on_unmount(self) -> None:
        self._quitting = True
        if self._notice_timer is not None:
            self._notice_timer.stop()
            self._notice_timer = None
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._persist_tabs()
