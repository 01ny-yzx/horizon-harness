"""One transcript-only selection boundary for highlighting and copied text."""
from textual.screen import Screen
from textual.selection import SELECT_ALL


def selectable_body(widget):
    return (widget.allow_select and widget.display and widget.visible
            and all(parent.display and parent.visible for parent in widget.ancestors) and any(
        parent.id in {'messages', 'live-output'} for parent in widget.ancestors
    ) and not widget.has_class('answer-meta') and not widget.has_class('run-outcome'))


def selected_body_text(screen):
    """Expand the two logical endpoints, including offscreen middle blocks.

    Screen.selections contains visible covered widgets, not a full document
    range. Endpoints use Textual's original-content offsets (including CJK and
    soft wrapping), so use get_selection rather than cutting terminal cells.
    """
    selected = [(w, s) for w, s in screen.selections.items() if selectable_body(w)]
    if not selected:
        return ''
    first, first_range = selected[0]
    last, last_range = selected[-1]
    nodes = [w for w in screen.query('*') if selectable_body(w)]
    if first not in nodes or last not in nodes:
        return ''
    start, end = nodes.index(first), nodes.index(last)
    if start > end:
        start, end = end, start
        first, last = last, first
        first_range, last_range = last_range, first_range
    pieces = []
    for widget in nodes[start:end + 1]:
        selection = first_range if widget is first else last_range if widget is last else SELECT_ALL
        result = widget.get_selection(selection)
        if result is not None:
            pieces.extend(result)
    return ''.join(pieces).rstrip('\n')


class TranscriptSelectionScreen(Screen):
    def get_widget_and_offset_at(self, x, y):
        widget, offset = super().get_widget_and_offset_at(x, y)
        if widget is None or not selectable_body(widget):
            return None, None
        return widget, offset

    async def _watch_selections(self, old_selections, selections):
        # Textual's cross-block coverage includes layout ancestors and chrome.
        # Apply the same boundary to highlighting as to copied content.
        filtered = {w: s for w, s in selections.items() if selectable_body(w)}
        if filtered != selections:
            self.selections = filtered
        await super()._watch_selections(old_selections, filtered)
