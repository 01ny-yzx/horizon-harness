"""Literal terminal hints for an explicitly supplied service/session directory."""
from __future__ import annotations

from pathlib import Path
import ntpath
import re
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
        home_path = str(home).rstrip('/\\')
        windows = bool(re.match(r'^[A-Za-z]:', home_path) or '\\' in home_path or home_path.startswith('//'))
        compared_value = ntpath.normcase(value) if windows else value
        compared_home = ntpath.normcase(home_path) if windows else home_path
        separator = '\\' if windows else '/'
        if home_path and compared_value == compared_home:
            value = '~'
        elif home_path and compared_value.startswith(compared_home + separator):
            suffix = value[len(home_path) + 1:]
            value = '~/' + (suffix.replace('\\', '/') if windows else suffix)
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
