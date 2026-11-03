"""User working directory, independent of application source and private stores."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

_directory: ContextVar[Path | None] = ContextVar('horizon_working_directory', default=None)


def get_working_directory() -> Path:
    return _directory.get() or Path.cwd().resolve()


def resolve_working_directory(value: str | Path | None = None, *, base: Path | None = None) -> Path:
    path = Path(value).expanduser() if value is not None else (base or Path.cwd())
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError(f'Not a directory: {path}')
    return path


def resolve_working_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else get_working_directory() / path).resolve()


@contextmanager
def working_directory_context(value: str | Path) -> Iterator[Path]:
    path = resolve_working_directory(value)
    token = _directory.set(path)
    try:
        yield path
    finally:
        _directory.reset(token)
