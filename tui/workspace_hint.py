"""Literal terminal hints for an explicitly supplied service/session directory."""
from __future__ import annotations

from pathlib import Path
import unicodedata

from rich.cells import cell_len
from rich.text import Text


def workspace_hint(directory: str | Path | None, *, home: str | Path | None = None,
                   max_cells: int = 48) -> Text:
    """Format without filesystem access, cwd guesses, or Rich markup parsing.

    Supply ``home`` only when the directory belongs to this local machine.
    Remote paths must retain their server-provided spelling.
    """
    if directory is None or max_cells <= 0:
        return Text('')
    value = str(directory)
    if not value.strip():
        return Text('')
    if home is not None:
        home_path = str(home).rstrip('/')
        if home_path and value == home_path:
            value = '~'
        elif home_path and value.startswith(home_path + '/'):
            value = '~' + value[len(home_path):]
    # A filename may contain terminal escapes, line breaks, or bidi overrides.
    # Text(...) keeps markup literal; filtering controls keeps it on one line.
    value = ''.join(char for char in value if unicodedata.category(char)[0] != 'C')
    if cell_len(value) <= max_cells:
        return Text(value)
    if max_cells == 1:
        return Text('…')
    prefix_budget = (max_cells - 1) // 4
    prefix = Text(value)
    prefix.truncate(prefix_budget, overflow='crop')
    suffix_budget = max_cells - 1 - prefix.cell_len
    suffix = ''
    for char in reversed(value):
        if cell_len(char + suffix) > suffix_budget:
            break
        suffix = char + suffix
    return Text(prefix.plain + '…' + suffix)
